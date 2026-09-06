from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class BlockingContinuationRuntime:
    logits: np.ndarray
    first_started: threading.Event = field(default_factory=threading.Event)
    release_first: threading.Event = field(default_factory=threading.Event)
    second_started: threading.Event = field(default_factory=threading.Event)
    continuation_calls: int = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        assert root[0] == "root"
        assert deadline_ms > 0
        self.continuation_calls += 1
        call = self.continuation_calls
        if call == 1:
            self.first_started.set()
            assert self.release_first.wait(2.0)
        else:
            self.second_started.set()

        outputs = []
        for path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            assert tuple(path)
            allowed = tuple(int(token_id) for token_id in allowed)
            values = np.full(len(allowed), -20.0, dtype=np.float32)
            if 2 in allowed:
                values[allowed.index(2)] = 20.0
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine(make_index) -> tuple[BilingualImeEngine, BlockingContinuationRuntime]:
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 9.0
    runtime = BlockingContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine: BilingualImeEngine, *, revision: int):
    return engine.query_candidate_page(
        client_session_id="same-client",
        composition_revision=revision,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="nihao",
        page_index=0,
    )


def test_latest_revision_retries_after_old_provider_releases(make_index, monkeypatch) -> None:
    """The latest input must not be stranded after it loses the single-flight race."""

    engine, runtime = _engine(make_index)
    backend = engine.candidate_pages.backend
    busy_seen = threading.Event()
    original_attempt = getattr(backend, "_continue_from_root_attempt", None)

    if callable(original_attempt):

        def observed_attempt(root, token_paths, allowed_token_sets, *, deadline_ms: float):
            with backend._continuation_gate:
                if backend._continuation_active:
                    busy_seen.set()
            return original_attempt(
                root,
                token_paths,
                allowed_token_sets,
                deadline_ms=deadline_ms,
            )

        monkeypatch.setattr(backend, "_continue_from_root_attempt", observed_attempt)
    else:
        original_continue = backend._continue_from_root_bounded

        def observed_continue(root, token_paths, allowed_token_sets, *, deadline_ms: float):
            with backend._continuation_gate:
                if backend._continuation_active:
                    busy_seen.set()
            return original_continue(
                root,
                token_paths,
                allowed_token_sets,
                deadline_ms=deadline_ms,
            )

        monkeypatch.setattr(backend, "_continue_from_root_bounded", observed_continue)

    revision_one = _page(engine, revision=1)
    assert runtime.first_started.wait(0.5)
    assert "你好" not in {candidate.text for candidate in revision_one.candidates}

    revision_two = _page(engine, revision=2)
    assert busy_seen.wait(0.5), "revision 2 never attempted continuation while revision 1 was busy"
    assert runtime.continuation_calls == 1
    assert "你好" not in {candidate.text for candidate in revision_two.candidates}
    completion = engine.candidate_pages._background_search_events.get(
        revision_two.candidate_set_id
    )
    assert completion is not None

    runtime.release_first.set()

    assert runtime.second_started.wait(1.0), (
        "latest revision did not resume automatically after the old provider released"
    )
    assert completion.wait(1.0), "resumed revision did not publish its completed candidates"

    refreshed = _page(engine, revision=2)
    assert refreshed.candidate_set_id != revision_two.candidate_set_id
    assert "你好" in {candidate.text for candidate in refreshed.candidates}
    assert all(candidate.context_epoch == 0 for candidate in refreshed.candidates)
