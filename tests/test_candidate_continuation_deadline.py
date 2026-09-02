from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend


@dataclass
class BlockingContinuationProvider:
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    calls: int = 0
    received_deadlines_ms: list[float] = field(default_factory=list)

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del root, token_paths
        self.calls += 1
        self.received_deadlines_ms.append(deadline_ms)
        self.started.set()
        try:
            assert self.release.wait(2.0)
            return [
                np.asarray([float(token_id) for token_id in allowed], dtype=np.float32)
                for allowed in allowed_token_sets
            ]
        finally:
            self.finished.set()


def test_uninterruptible_provider_cannot_hold_request_past_deadline_or_queue_retries() -> None:
    runtime = BlockingContinuationProvider()
    backend = FullLogitsSnapshotBackend(runtime)
    continuation = backend.continue_from_root
    assert continuation is not None

    started = time.perf_counter()
    first = continuation(
        object(),
        [(1,)],
        [(2, 3)],
        deadline_ms=20.0,
    )
    first_elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert runtime.started.wait(0.5)
    assert first is None
    assert first_elapsed_ms < 100.0
    assert runtime.calls == 1

    # The first worker is still inside the uninterruptible provider. A retry
    # must fail fast instead of joining a worker queue or waiting on model work.
    started = time.perf_counter()
    second = continuation(
        object(),
        [(1,)],
        [(2,)],
        deadline_ms=120.0,
    )
    second_elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert second is None
    assert second_elapsed_ms < 50.0
    assert runtime.calls == 1

    runtime.release.set()
    assert runtime.finished.wait(0.5)
    for _ in range(100):
        with backend._continuation_gate:
            if not backend._continuation_active:
                break
        time.sleep(0.001)
    else:
        raise AssertionError("continuation worker did not leave single-flight state")

    third = continuation(
        object(),
        [(1,)],
        [(2, 3)],
        deadline_ms=120.0,
    )

    assert third is not None
    assert np.array_equal(third[0], np.array([2.0, 3.0], dtype=np.float32))
    assert runtime.calls == 2
    assert all(0.0 < value <= 120.0 for value in runtime.received_deadlines_ms)
