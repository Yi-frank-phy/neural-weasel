from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import CandidatePageError, CandidatePageTimeout
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class BlockingContinuationRuntime:
    logits: np.ndarray
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
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
        self.started.set()
        assert self.release.wait(2.0)
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


def _page(
    engine: BilingualImeEngine,
    *,
    client: str,
    revision: int,
    raw: str,
    page_index: int = 0,
    candidate_set_id: str | None = None,
    deadline_ms: float | None = None,
):
    return engine.query_candidate_page(
        client_session_id=client,
        composition_revision=revision,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys=raw,
        page_index=page_index,
        candidate_set_id=candidate_set_id,
        deadline_ms=deadline_ms,
    )


def _run_in_thread(target):
    done = threading.Event()
    result: dict[str, object] = {}

    def run() -> None:
        try:
            result["value"] = target()
        except Exception as error:  # noqa: BLE001 - test captures the exact public error below.
            result["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, result


def test_blocked_later_page_does_not_queue_page_zero_or_focus_invalidation(make_index) -> None:
    engine, runtime = _engine(make_index)
    first = _page(engine, client="active", revision=1, raw="ni")
    assert first.has_more is True

    primary_thread, primary_done, primary = _run_in_thread(
        lambda: _page(
            engine,
            client="active",
            revision=1,
            raw="ni",
            page_index=1,
            candidate_set_id=first.candidate_set_id,
            deadline_ms=1_000.0,
        )
    )
    assert runtime.started.wait(0.5)
    assert primary_done.is_set() is False

    # A duplicate unfinished page request for the same candidate set must not
    # queue behind the uninterruptible decode. It is retryable immediately.
    duplicate_thread, duplicate_done, duplicate = _run_in_thread(
        lambda: _page(
            engine,
            client="active",
            revision=1,
            raw="ni",
            page_index=1,
            candidate_set_id=first.candidate_set_id,
            deadline_ms=120.0,
        )
    )
    if not duplicate_done.wait(0.5):
        runtime.release.set()
        primary_thread.join(1.0)
        duplicate_thread.join(1.0)
        pytest.fail("duplicate later-page request queued behind continuation")
    assert isinstance(duplicate.get("error"), CandidatePageTimeout)

    # A different composition's page 0 is baseline-only and must remain
    # responsive even while the first candidate set is still inside CUDA work.
    page0_thread, page0_done, page0_result = _run_in_thread(
        lambda: _page(engine, client="other", revision=1, raw="n")
    )
    if not page0_done.wait(0.5):
        runtime.release.set()
        primary_thread.join(1.0)
        page0_thread.join(1.0)
        pytest.fail("page zero queued behind continuation")
    assert "error" not in page0_result
    other_page = page0_result["value"]
    assert other_page.page_index == 0
    assert other_page.score_source == "baseline"

    # Focus/session invalidation is also state-only. It must not wait for the
    # model call, and the in-flight result must be discarded when it returns.
    invalidate_thread, invalidate_done, invalidate = _run_in_thread(
        engine.invalidate_candidate_sessions
    )
    if not invalidate_done.wait(0.5):
        runtime.release.set()
        primary_thread.join(1.0)
        invalidate_thread.join(1.0)
        pytest.fail("candidate session invalidation queued behind continuation")
    assert "error" not in invalidate

    runtime.release.set()
    primary_thread.join(1.0)
    assert primary_done.is_set() is True
    assert isinstance(primary.get("error"), CandidatePageError)
    assert runtime.continuation_calls == 1
