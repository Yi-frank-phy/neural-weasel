from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .unified import UnifiedConstraintEngine


class SnapshotCoordinator:
    """Publish only the newest requested backend state.

    Candidate queries capture the current immutable state and never wait for
    the context worker.
    """

    def __init__(
        self,
        *,
        backend: ModelBackend,
        engine: UnifiedConstraintEngine,
        retained_states: int = 4,
    ) -> None:
        if retained_states < 1:
            raise ValueError("retained_states must be positive")
        self.backend = backend
        self.engine = engine
        self.retained_states = retained_states
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._requested_epoch = 0
        self._pending: tuple[int, str, str] | None = None
        self._worker: threading.Thread | None = None
        self._state: BackendState | None = None
        self._states: dict[int, BackendState] = {}
        self._last_refresh_error: str | None = None

    def _next_request_epoch(self) -> int:
        self._requested_epoch += 1
        return self._requested_epoch

    def update_context(self, before: str, after: str = "") -> BackendState:
        with self._request_lock:
            requested_epoch = self._next_request_epoch()
        backend_state = self.backend.update_context(before, after)
        state = (
            backend_state
            if backend_state.epoch == requested_epoch
            else replace(backend_state, epoch=requested_epoch)
        )
        with self._request_lock:
            if requested_epoch == self._requested_epoch:
                self._publish(state)
        return state

    def request_context_update(self, before: str, after: str = "") -> int:
        with self._request_lock:
            requested_epoch = self._next_request_epoch()
            self._pending = (requested_epoch, before, after)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop,
                    name="neural-weasel-v02-context",
                    daemon=True,
                )
                self._worker.start()
            return requested_epoch

    def _worker_loop(self) -> None:
        while True:
            with self._request_lock:
                pending = self._pending
                self._pending = None
                if pending is None:
                    self._worker = None
                    return
            requested_epoch, before, after = pending
            try:
                backend_state = self.backend.update_context(before, after)
                state = (
                    backend_state
                    if backend_state.epoch == requested_epoch
                    else replace(backend_state, epoch=requested_epoch)
                )
            except Exception as error:
                with self._request_lock:
                    if requested_epoch == self._requested_epoch:
                        self._last_refresh_error = type(error).__name__
                continue
            with self._request_lock:
                if requested_epoch == self._requested_epoch:
                    self._last_refresh_error = None
                    self._publish(state)

    def _publish(self, state: BackendState) -> None:
        with self._state_lock:
            self._state = state
            self._states[state.epoch] = state
            for epoch in sorted(self._states)[: -self.retained_states]:
                del self._states[epoch]

    @property
    def latest_state(self) -> BackendState | None:
        with self._state_lock:
            return self._state

    def state_for_epoch(self, epoch: int) -> BackendState | None:
        with self._state_lock:
            return self._states.get(epoch)

    @property
    def context_epoch(self) -> int:
        state = self.latest_state
        return state.epoch if state is not None else 0

    def query(
        self,
        before: str,
        raw_keys: str,
        *,
        after_text: str = "",
        limit: int = 5,
    ) -> list[Candidate]:
        state = self.latest_state
        return self.engine.query(
            before,
            raw_keys,
            state=state,
            after_text=after_text,
            limit=limit,
        )

    def wait_for_epoch(self, epoch: int, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.context_epoch >= epoch:
                return True
            time.sleep(0.002)
        return self.context_epoch >= epoch

    def invalidate_private_state(self) -> None:
        with self._request_lock:
            self._next_request_epoch()
            self._pending = None
            self.backend.invalidate_private_state()
        with self._state_lock:
            self._state = None
            self._states.clear()

    def diagnostics(self) -> dict[str, object]:
        state = self.latest_state
        with self._request_lock:
            requested_epoch = self._requested_epoch
            last_error = self._last_refresh_error
        diagnostics = self.backend.diagnostics()
        diagnostics.update(
            {
                "context_epoch": state.epoch if state is not None else 0,
                "requested_context_epoch": requested_epoch,
                "snapshot_age_ms": (
                    max(0.0, (time.monotonic() - state.created_monotonic) * 1000)
                    if state is not None
                    else None
                ),
                "last_refresh_error": last_error,
            }
        )
        return diagnostics


def safe_service_query(
    query: Callable[[float], list[Any]],
    *,
    deadline_ms: float,
) -> list[Any]:
    """Translate expected service failures into a no-replacement response.

    The supplied native/pipe query owns enforcement of its absolute deadline.
    This boundary forwards that deadline and never retries or starts recovery.
    """

    if deadline_ms <= 0:
        return []
    try:
        return query(deadline_ms)
    except (OSError, TimeoutError, ValueError):
        return []
