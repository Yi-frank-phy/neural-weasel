from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from neural_weasel.backends import BackendState, FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.realtime import SnapshotCoordinator, safe_service_query
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint, UnifiedConstraintEngine


@dataclass
class BlockingRuntime:
    release_second: threading.Event
    second_started: threading.Event
    calls: int = 0
    invalidations: int = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        if self.calls == 2:
            self.second_started.set()
            assert self.release_second.wait(2.0)
        logits = np.full(16, -10.0, dtype=np.float32)
        logits[1] = float(self.calls)
        return RuntimeSnapshot(
            payload=logits,
            before_hash=before,
            after_hash=after,
            latency_ms=250.0 if self.calls == 2 else 1.0,
        )

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        self.invalidations += 1


def make_coordinator(runtime: BlockingRuntime):
    backend = FullLogitsSnapshotBackend(runtime)
    engine = UnifiedConstraintEngine(
        backend=backend,
        latin_prefix_constraint=LatinPrefixConstraint([LatinCompletion("asymmetric", (1,))]),
    )
    return backend, SnapshotCoordinator(backend=backend, engine=engine)


def test_query_never_refreshes_model_and_can_use_old_snapshot() -> None:
    """AT-RT-01/02: new keys query epoch n while n+1 is still refreshing."""
    runtime = BlockingRuntime(threading.Event(), threading.Event())
    backend, coordinator = make_coordinator(runtime)
    first = coordinator.update_context("The first context")
    coordinator.request_context_update("The newer context")
    assert runtime.second_started.wait(1.0)
    refresh_calls = runtime.calls

    candidates = coordinator.query("The first context", "asy")

    assert candidates[0].context_epoch == first.epoch
    assert runtime.calls == refresh_calls
    assert backend.latest_state() is first
    runtime.release_second.set()
    assert coordinator.wait_for_epoch(2, timeout_seconds=1.0)


def test_stale_completion_cannot_overwrite_newer_requested_epoch() -> None:
    """AT-RT-03: a completed stale request is discarded."""
    runtime = BlockingRuntime(threading.Event(), threading.Event())
    _, coordinator = make_coordinator(runtime)
    coordinator.update_context("initial")
    stale_epoch = coordinator.request_context_update("stale")
    assert runtime.second_started.wait(1.0)
    newest_epoch = coordinator.request_context_update("newest")

    runtime.release_second.set()
    assert coordinator.wait_for_epoch(newest_epoch, timeout_seconds=1.0)

    assert stale_epoch < newest_epoch
    assert coordinator.context_epoch == newest_epoch
    assert coordinator.latest_state.before_hash == "newest"


def test_background_refresh_publishes_before_legacy_candidate_prewarm() -> None:
    """The compatibility prewarm may never hold an editor epoch unpublished."""

    class RecordingEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int, str, int]] = []
            self.started = threading.Event()
            self.release = threading.Event()

        def query(
            self,
            before: str,
            raw_keys: str,
            *,
            state: BackendState,
            after_text: str,
            limit: int,
        ) -> list[object]:
            self.calls.append((before, raw_keys, state.epoch, after_text, limit))
            if len(self.calls) == 1:
                self.started.set()
                assert self.release.wait(1.0)
            return []

    runtime = BlockingRuntime(threading.Event(), threading.Event())
    backend = FullLogitsSnapshotBackend(runtime)
    engine = RecordingEngine()
    coordinator = SnapshotCoordinator(backend=backend, engine=engine)

    epoch = coordinator.request_context_update("中文上下文", "右侧文本")

    assert engine.started.wait(1.0)
    assert coordinator.context_epoch == epoch
    assert coordinator.latest_state.before_hash == "中文上下文"
    engine.release.set()
    deadline = time.monotonic() + 1.0
    while len(engine.calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert engine.calls == [
        ("中文上下文", "n", epoch, "右侧文本", 5),
        ("中文上下文", "ni", epoch, "右侧文本", 5),
    ]


def test_background_prewarm_failure_does_not_block_snapshot_publication() -> None:
    class FailingEngine:
        def query(self, *args: object, **kwargs: object) -> list[object]:
            raise RuntimeError("synthetic prewarm failure")

    runtime = BlockingRuntime(threading.Event(), threading.Event())
    backend = FullLogitsSnapshotBackend(runtime)
    coordinator = SnapshotCoordinator(backend=backend, engine=FailingEngine())

    epoch = coordinator.request_context_update("context")

    assert coordinator.wait_for_epoch(epoch, timeout_seconds=1.0)
    deadline = time.monotonic() + 1.0
    while coordinator.diagnostics()["last_prewarm_error"] is None and time.monotonic() < deadline:
        time.sleep(0.002)
    assert coordinator.diagnostics()["last_prewarm_error"] == "RuntimeError"


def test_snapshot_age_over_100ms_is_reported_but_queryable() -> None:
    """AT-RT-04: stale age is a metric, not a query rejection."""
    runtime = BlockingRuntime(threading.Event(), threading.Event())
    _, coordinator = make_coordinator(runtime)
    state = coordinator.update_context("old context")
    object.__setattr__(state, "created_monotonic", time.monotonic() - 1.0)

    candidates = coordinator.query("old context", "asy")
    diagnostics = coordinator.diagnostics()

    assert candidates
    assert diagnostics["snapshot_age_ms"] >= 1_000


def test_service_unavailable_returns_safely_within_deadline() -> None:
    """AT-RT-05: key path gets no replacement instead of an exception."""
    observed_deadline: list[float] = []

    def unavailable(deadline_ms: float) -> list[object]:
        observed_deadline.append(deadline_ms)
        raise OSError("model service is not running")

    started = time.perf_counter()
    response = safe_service_query(unavailable, deadline_ms=6.0)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert response == []
    assert observed_deadline == [6.0]
    assert elapsed_ms < 20.0


def test_private_invalidation_clears_published_state() -> None:
    """AT-MB-07/RT-08: protected focus clears backend and coordinator state."""
    runtime = BlockingRuntime(threading.Event(), threading.Event())
    backend, coordinator = make_coordinator(runtime)
    state: BackendState = coordinator.update_context("private context")

    coordinator.invalidate_private_state()

    assert coordinator.latest_state is None
    assert backend.latest_state() is None
    assert coordinator.query("", "literal")[0].text == "literal"
    assert runtime.invalidations == 1
    assert state.epoch == 1
