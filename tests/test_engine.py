from __future__ import annotations

import threading
import time

import pytest

from neural_weasel.engine import NeuralPinyinEngine
from neural_weasel.index import tokenizer_fingerprint
from neural_weasel.model import LogitsSnapshot


class FakeTokenizer:
    all_special_ids: list[int] = []

    def __len__(self) -> int:
        return 2

    def get_vocab(self) -> dict[str, int]:
        return {"<unused>": 0, "你": 1}


class FakeBackend:
    def __init__(self) -> None:
        self.tokenizer = FakeTokenizer()
        self.model_id = "test/base-model"
        self.calls: list[tuple[str, str]] = []
        self.cache_invalidations = 0

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        self.calls.append((before, after))
        return LogitsSnapshot(
            epoch=len(self.calls),
            before_hash=f"before-{len(self.calls)}",
            after_hash=f"after-{len(self.calls)}",
            logits=(0.0, 5.0),
            created_monotonic=time.monotonic(),
            latency_ms=1.0,
            after_text=after,
        )

    def invalidate_context_cache(self) -> None:
        self.cache_invalidations += 1


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        if not self.calls:
            self.first_started.set()
            assert self.release_first.wait(timeout=2.0)
        return super().create_snapshot(before, after)


class WorkerExitGate:
    """Pause a context worker just after its final request-lock release."""

    def __init__(self, engine: NeuralPinyinEngine) -> None:
        self.engine = engine
        self.lock = threading.Lock()
        self.worker_released_empty_lock = threading.Event()
        self.allow_worker_exit = threading.Event()
        self._gated = False
        self._worker_exits = 0

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        worker_thread = threading.current_thread().name == "neural-weasel-context"
        if worker_thread:
            self._worker_exits += 1
        should_gate = not self._gated and worker_thread and self._worker_exits == 3
        if should_gate:
            self._gated = True
        self.lock.release()
        if should_gate:
            self.worker_released_empty_lock.set()
            assert self.allow_worker_exit.wait(timeout=2.0)


def test_engine_requires_matching_tokenizer_index(make_index) -> None:
    backend = FakeBackend()
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash="wrong")
    with pytest.raises(RuntimeError, match="tokenizer/index mismatch"):
        NeuralPinyinEngine(backend, index)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"model_id": "other/base-model"}, "model/index mismatch"),
        ({"revision": "other-commit"}, "revision/index mismatch"),
        ({"pypinyin_version": "0.0.invalid"}, "pypinyin/index mismatch"),
    ],
)
def test_engine_rejects_stale_index_identity(
    make_index,
    metadata: dict[str, str],
    message: str,
) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index(
        [(1, "你", "ni", 1, 0)],
        tokenizer_hash=fingerprint,
        **metadata,
    )
    with pytest.raises(RuntimeError, match=message):
        NeuralPinyinEngine(backend, index)


def test_engine_queries_only_immutable_snapshot_and_never_calls_backend(make_index) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)

    assert engine.query("ni") == []
    assert backend.calls == []
    snapshot = engine.update_context("你好", "世界")
    candidates = engine.query("ni")
    candidates_again = engine.query("n")

    assert snapshot.epoch == 1
    assert backend.calls == [("你好", "世界")]
    assert engine.context_epoch == 1
    assert [candidate.text for candidate in candidates] == ["你"]
    assert candidates[0].context_epoch == 1
    assert [candidate.text for candidate in candidates_again] == ["你"]


def test_engine_keeps_recent_snapshot_addressable_by_composition_epoch(make_index) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)
    first = engine.update_context("first")
    second = engine.update_context("second")

    assert first.epoch == 1
    assert second.epoch == 2
    assert engine.has_snapshot(1)
    assert engine.has_snapshot(2)
    assert not engine.has_snapshot(3)
    assert engine.query("ni", context_epoch=1)[0].context_epoch == 1
    assert engine.query("ni", context_epoch=2)[0].context_epoch == 2


def test_async_context_update_never_publishes_superseded_snapshot(make_index) -> None:
    backend = BlockingBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)

    first_epoch = engine.request_context_update("stale")
    assert backend.first_started.wait(timeout=1.0)
    second_epoch = engine.request_context_update("fresh")
    backend.release_first.set()

    assert first_epoch == 1
    assert second_epoch == 2
    assert engine.wait_for_epoch(second_epoch, timeout_seconds=2.0)
    assert engine.context_epoch == second_epoch
    assert backend.calls == [("stale", ""), ("fresh", "")]
    assert engine.query("ni", context_epoch=first_epoch) == []


def test_context_request_arriving_during_worker_exit_is_not_stranded(make_index) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)
    gate = WorkerExitGate(engine)
    engine._request_lock = gate

    try:
        first_epoch = engine.request_context_update("first")
        assert engine.wait_for_epoch(first_epoch, timeout_seconds=1.0)
        assert gate.worker_released_empty_lock.wait(timeout=1.0)

        second_epoch = engine.request_context_update("second")
        gate.allow_worker_exit.set()

        assert engine.wait_for_epoch(second_epoch, timeout_seconds=1.0)
        assert backend.calls == [("first", ""), ("second", "")]
    finally:
        gate.allow_worker_exit.set()


def test_secure_reset_clears_snapshots_and_discards_inflight_context(make_index) -> None:
    backend = BlockingBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)

    requested_epoch = engine.request_context_update("private-before", "PRIVATE-AFTER")
    assert backend.first_started.wait(timeout=1.0)
    engine.reset_private_context()
    backend.release_first.set()

    deadline = time.monotonic() + 1.0
    while engine._context_worker is not None and time.monotonic() < deadline:
        time.sleep(0.005)

    assert requested_epoch == 1
    assert engine.context_epoch == 0
    assert engine.query("ni", context_epoch=requested_epoch) == []
    assert engine._snapshot is None
    assert not engine._snapshots
    assert backend.cache_invalidations == 1

