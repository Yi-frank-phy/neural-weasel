"""Dispatch newly registered search workers after the current pipe response."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Thread

_pending: ContextVar[list[Thread] | None] = ContextVar("response_workers", default=None)


def start_worker(worker: Thread) -> None:
    pending = _pending.get()
    if pending is None:
        worker.start()
    else:
        pending.append(worker)


@contextmanager
def after_response() -> Iterator[None]:
    if _pending.get() is not None:
        yield
        return
    workers: list[Thread] = []
    token = _pending.set(workers)
    try:
        yield
    finally:
        _pending.reset(token)
        # Registration already advertises pending work. Dispatch even if the
        # peer disconnected so existing cancellation/cleanup still runs.
        for worker in workers:
            worker.start()
