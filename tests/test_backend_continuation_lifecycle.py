from __future__ import annotations

import threading
from dataclasses import dataclass, field

from neural_weasel.backends import FullLogitsSnapshotBackend


@dataclass
class BlockingContinuationRuntime:
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del root, allowed_token_sets
        assert deadline_ms > 0
        self.started.set()
        assert self.release.wait(2.0)
        self.finished.set()
        return [[0.0] for _ in token_paths]


def _timed_out_attempt(
    backend: FullLogitsSnapshotBackend,
    runtime: BlockingContinuationRuntime,
):
    attempt = backend.continue_from_root_attempt(
        ("root",),
        [(1,)],
        [(1,)],
        deadline_ms=20.0,
    )
    assert runtime.started.wait(0.5)
    assert attempt.result is None
    assert attempt.retry_generation is not None
    return attempt


def test_waiter_registered_while_provider_is_busy_wakes_on_real_completion() -> None:
    runtime = BlockingContinuationRuntime()
    backend = FullLogitsSnapshotBackend(runtime)
    attempt = _timed_out_attempt(backend, runtime)
    wake = threading.Event()

    backend.register_continuation_idle_wait(attempt.retry_generation, wake)
    assert wake.is_set() is False

    runtime.release.set()
    assert wake.wait(1.0)


def test_waiter_cannot_miss_provider_completion_before_registration() -> None:
    runtime = BlockingContinuationRuntime()
    backend = FullLogitsSnapshotBackend(runtime)
    attempt = _timed_out_attempt(backend, runtime)

    runtime.release.set()
    assert runtime.finished.wait(1.0)

    wake = threading.Event()
    backend.register_continuation_idle_wait(attempt.retry_generation, wake)
    assert wake.wait(1.0), "provider completion was lost before waiter registration"
