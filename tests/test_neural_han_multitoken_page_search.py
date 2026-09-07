from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class MultiTokenRuntime:
    logits: np.ndarray
    calls: int = 0
    continuation_calls: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(default_factory=list)

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", self.calls, before),
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
        outputs = []
        for raw_path, raw_allowed in zip(
            token_paths,
            allowed_token_sets,
            strict=True,
        ):
            path = tuple(int(token_id) for token_id in raw_path)
            allowed = tuple(int(token_id) for token_id in raw_allowed)
            self.continuation_calls.append((path, allowed))
            outputs.append(
                np.asarray(
                    [1000.0 if token_id == 7 else 20.0 - float(token_id) for token_id in allowed],
                    dtype=np.float32,
                )
            )
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine(make_index):
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
            (3, "吗", "ma", "ma", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1:4] = [10.0, 9.0, 8.0]
    logits[7] = 1000.0
    runtime = MultiTokenRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine, raw: str, **overrides):
    values = {
        "client_session_id": "han-multitoken-session",
        "composition_revision": 1,
        "context_epoch": 0,
        "context_session": None,
        "source_revision": None,
        "language_mode": "chinese_first",
        "raw_keys": raw,
        "page_index": 0,
    }
    values.update(overrides)
    return engine.query_candidate_page(**values)


def _wait_for_async_han(engine: BilingualImeEngine, candidate_set_id: str) -> None:
    manager = engine.candidate_pages
    with manager._state_lock:
        session = manager._sessions[candidate_set_id]
        identity_key = manager._async_identity_key(session.identity)
        if identity_key in manager._async_han_cache:
            return
        event = manager._background_search_events.get(candidate_set_id)
    assert event is not None
    assert event.wait(1.0)
    with manager._state_lock:
        assert identity_key in manager._async_han_cache


@pytest.mark.parametrize(
    ("raw", "expected_text", "expected_path"),
    [
        ("nihao", "你好", (1, 2)),
        ("nh", "你好", (1, 2)),
        ("nihaoma", "你好吗", (1, 2, 3)),
    ],
)
def test_exact_han_cover_can_span_multiple_base_tokens(
    make_index,
    raw: str,
    expected_text: str,
    expected_path: tuple[int, ...],
) -> None:
    engine, runtime = _engine(make_index)

    first = _page(engine, raw)
    assert expected_text not in {candidate.text for candidate in first.candidates}
    assert first.has_more is True

    _wait_for_async_han(engine, first.candidate_set_id)
    refreshed = _page(engine, raw, presentation_refresh=True)
    assert refreshed.candidate_set_id != first.candidate_set_id

    exact = next(candidate for candidate in refreshed.candidates if candidate.text == expected_text)
    assert exact.token_path == expected_path
    assert exact.completes_input is True
    assert exact.consumed_keys == len(raw)
    assert exact.predicted_syllables == 0
    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[0][1] == tuple(range(runtime.logits.size))
    assert all(7 not in candidate.token_path for candidate in refreshed.candidates)


def test_han_continuation_scores_full_vocab_but_generates_only_legal_edges(
    make_index,
) -> None:
    engine, runtime = _engine(make_index)

    first = _page(engine, "nihaoma")
    _wait_for_async_han(engine, first.candidate_set_id)
    refreshed = _page(engine, "nihaoma", presentation_refresh=True)

    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[1][0] == (1, 2)
    assert all(
        allowed == tuple(range(runtime.logits.size))
        for _, allowed in runtime.continuation_calls[:2]
    )
    assert all(7 not in candidate.token_path for candidate in refreshed.candidates)
    exact = next(candidate for candidate in refreshed.candidates if candidate.text == "你好吗")
    assert exact.token_path == (1, 2, 3)


def test_late_multitoken_cache_publishes_new_snapshot_without_mutating_old_page_zero(
    make_index,
) -> None:
    engine, _ = _engine(make_index)

    first = _page(engine, "nihao")
    first_candidates = first.candidates
    first_ids = first.candidate_ids
    assert "你好" not in {candidate.text for candidate in first_candidates}

    _wait_for_async_han(engine, first.candidate_set_id)
    refreshed = _page(engine, "nihao", presentation_refresh=True)
    assert refreshed.candidate_set_id != first.candidate_set_id
    assert any(candidate.text == "你好" for candidate in refreshed.candidates)

    # Publishing a new presentation does not mutate the already-returned page.
    assert first.candidates == first_candidates
    assert first.candidate_ids == first_ids
    assert "你好" not in {candidate.text for candidate in first.candidates}

    repeated_page_zero = _page(engine, "nihao")
    assert repeated_page_zero.candidate_set_id == refreshed.candidate_set_id
    assert repeated_page_zero.candidates == refreshed.candidates
    assert repeated_page_zero.candidate_ids == refreshed.candidate_ids

    # The newly learned baseline path is also available to a later input revision.
    next_revision = _page(engine, "nihao", composition_revision=2)
    assert next_revision.candidate_set_id != refreshed.candidate_set_id
    assert any(candidate.text == "你好" for candidate in next_revision.candidates)
