from __future__ import annotations

import threading

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .realtime import SnapshotCoordinator
from .unified import Constraint, ContextScriptPolicy, UnifiedConstraintEngine


class BilingualImeEngine:
    """Service-facing v0.2 engine with retained epoch-consistent context."""

    def __init__(
        self,
        *,
        backend: ModelBackend,
        pinyin_constraint: Constraint | None = None,
        latin_prefix_constraint: Constraint | None = None,
        retained_contexts: int = 4,
    ) -> None:
        self.script_policy = ContextScriptPolicy()
        self.constraint_engine = UnifiedConstraintEngine(
            backend=backend,
            pinyin_constraint=pinyin_constraint,
            latin_prefix_constraint=latin_prefix_constraint,
            script_policy=self.script_policy,
        )
        self.coordinator = SnapshotCoordinator(
            backend=backend,
            engine=self.constraint_engine,
            retained_states=retained_contexts,
        )
        self._contexts: dict[int, tuple[str, str]] = {}
        self._contexts_lock = threading.Lock()
        self._retained_contexts = retained_contexts

    def _remember_context(self, epoch: int, before: str, after: str) -> None:
        with self._contexts_lock:
            self._contexts[epoch] = (before, after)
            for old_epoch in sorted(self._contexts)[: -self._retained_contexts]:
                del self._contexts[old_epoch]

    def update_context(self, before: str, after: str = "") -> BackendState:
        state = self.coordinator.update_context(before, after)
        self._remember_context(state.epoch, before, after)
        return state

    def request_context_update(self, before: str, after: str = "") -> int:
        epoch = self.coordinator.request_context_update(before, after)
        self._remember_context(epoch, before, after)
        return epoch

    def query(
        self,
        raw_keys: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[Candidate]:
        if context_epoch is None:
            state = self.coordinator.latest_state
        elif context_epoch == 0:
            state = self.coordinator.latest_state
        else:
            state = self.coordinator.state_for_epoch(context_epoch)
            if state is None:
                return []
        if state is None:
            return self.constraint_engine.query("", raw_keys, state=None, limit=limit)
        with self._contexts_lock:
            before, after = self._contexts.get(state.epoch, ("", ""))
        return self.constraint_engine.query(
            before,
            raw_keys,
            state=state,
            after_text=after,
            limit=limit,
        )

    def has_snapshot(self, epoch: int) -> bool:
        return self.coordinator.state_for_epoch(epoch) is not None

    def context_kind(self, epoch: int) -> str:
        with self._contexts_lock:
            before, _ = self._contexts.get(epoch, ("", ""))
        return self.script_policy.classify(before)

    @property
    def context_epoch(self) -> int:
        return self.coordinator.context_epoch

    def wait_for_epoch(self, epoch: int, timeout_seconds: float = 5.0) -> bool:
        return self.coordinator.wait_for_epoch(epoch, timeout_seconds)

    def commit(self, text: str) -> None:
        self.script_policy.record_commit(text)

    def reset_private_context(self) -> None:
        self.coordinator.invalidate_private_state()
        with self._contexts_lock:
            self._contexts.clear()

    def clear_history(self) -> None:
        self.script_policy.stable_script = None

    def diagnostics(self) -> dict[str, object]:
        return self.coordinator.diagnostics()
