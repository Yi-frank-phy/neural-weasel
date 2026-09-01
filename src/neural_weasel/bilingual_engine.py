from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .neural_candidates import CandidatePage, NeuralCandidatePageManager, NeuralLanguageMode
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
        diagnostic_identity: dict[str, object] | None = None,
    ) -> None:
        self.script_policy = ContextScriptPolicy()
        self.constraint_engine = UnifiedConstraintEngine(
            backend=backend,
            pinyin_constraint=pinyin_constraint,
            latin_prefix_constraint=latin_prefix_constraint,
            script_policy=self.script_policy,
        )
        self._contexts: dict[int, tuple[str, str]] = {}
        self._contexts_lock = threading.Lock()
        self._retained_contexts = retained_contexts
        self._diagnostic_identity = dict(diagnostic_identity or {})
        self._query_cache: OrderedDict[tuple[int, str, int, str | None], tuple[Candidate, ...]] = (
            OrderedDict()
        )
        self._query_cache_lock = threading.Lock()
        self.coordinator = SnapshotCoordinator(
            backend=backend,
            engine=self.constraint_engine,
            retained_states=retained_contexts,
            candidate_query=self._query_state,
        )
        self.candidate_pages = NeuralCandidatePageManager(
            backend=backend,
            pinyin_index=getattr(pinyin_constraint, "index", None),
            latin_constraint=self.constraint_engine.latin_prefix_constraint,
        )

    def initialize_neural_baseline(self) -> None:
        """Create the permanent empty-context neural score vector before serving.

        The copied score vector contains no editor context and intentionally lives
        outside the mutable editor-snapshot lifecycle. Secure/private invalidation
        may therefore destroy all contextual model state without deleting this
        context-free fallback.
        """

        state = self.coordinator.backend.update_context("", "")
        scores = np.asarray(state.payload, dtype=np.float32)
        if scores.ndim != 1:
            raise RuntimeError("empty-context baseline requires a full-vocabulary score vector")
        self.candidate_pages.install_baseline_scores(scores)
        self.candidate_pages.prewarm_single_letter_pages()

    def _remember_context(self, epoch: int, before: str, after: str) -> None:
        with self._contexts_lock:
            self._contexts[epoch] = (before, after)
            expired_epochs = sorted(self._contexts)[: -self._retained_contexts]
            for old_epoch in expired_epochs:
                del self._contexts[old_epoch]
        if expired_epochs:
            with self._query_cache_lock:
                for key in tuple(self._query_cache):
                    if key[0] in expired_epochs:
                        del self._query_cache[key]

    def _query_state(
        self,
        before: str,
        raw_keys: str,
        *,
        state: BackendState,
        after_text: str,
        limit: int,
    ) -> list[Candidate]:
        stable_script = self.script_policy.stable_script
        cache_key = (
            state.epoch,
            raw_keys,
            limit,
            stable_script.value if stable_script is not None else None,
        )
        with self._query_cache_lock:
            cached = self._query_cache.get(cache_key)
            if cached is not None:
                self._query_cache.move_to_end(cache_key)
                return list(cached)

        candidates = self.constraint_engine.query(
            before,
            raw_keys,
            state=state,
            after_text=after_text,
            limit=limit,
        )
        with self._query_cache_lock:
            self._query_cache[cache_key] = tuple(candidates)
            self._query_cache.move_to_end(cache_key)
            while len(self._query_cache) > 256:
                self._query_cache.popitem(last=False)
        return candidates

    def _clear_query_cache(self) -> None:
        with self._query_cache_lock:
            self._query_cache.clear()

    def update_context(self, before: str, after: str = "") -> BackendState:
        # The production launcher historically issued an empty update only to
        # force the startup forward. Once the permanent baseline exists, keep
        # that call outside the editor-context epoch lifecycle.
        if (
            not before
            and not after
            and self.candidate_pages.baseline_ready
            and self.coordinator.context_epoch == 0
        ):
            state = self.coordinator.backend.latest_state()
            if state is not None:
                return state
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
        if context_epoch is None or context_epoch == 0:
            state = self.coordinator.latest_state
        else:
            state = self.coordinator.state_for_epoch(context_epoch)
            if state is None:
                return []
        if state is None:
            return self.constraint_engine.query("", raw_keys, state=None, limit=limit)
        with self._contexts_lock:
            before, after = self._contexts.get(state.epoch, ("", ""))
        return self._query_state(
            before,
            raw_keys,
            state=state,
            after_text=after,
            limit=limit,
        )

    def query_candidate_page(
        self,
        *,
        client_session_id: str,
        composition_revision: int,
        context_epoch: int,
        context_session: str | None,
        source_revision: int | None,
        language_mode: NeuralLanguageMode | str,
        raw_keys: str,
        page_index: int,
        candidate_set_id: str | None = None,
        deadline_ms: float | None = None,
    ) -> CandidatePage:
        """Return one immutable revision-scoped page without waiting for context.

        `context_epoch == 0` deliberately means the context-free baseline, never
        "whatever editor snapshot happened to be latest". If a nonzero requested
        snapshot is not ready or has expired, the same baseline is used instead.
        """

        state = self.coordinator.state_for_epoch(context_epoch) if context_epoch > 0 else None
        return self.candidate_pages.query_page(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=language_mode,
            raw_keys=raw_keys,
            page_index=page_index,
            candidate_set_id=candidate_set_id,
            state=state,
            deadline_ms=deadline_ms,
        )

    def query_pinyin(
        self,
        raw_keys: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[Candidate]:
        """Rank Han-only pinyin candidates from one retained logits snapshot."""

        if context_epoch is None or context_epoch == 0:
            state = self.coordinator.latest_state
        else:
            state = self.coordinator.state_for_epoch(context_epoch)
            if state is None:
                return []
        if state is None:
            return []
        with self._contexts_lock:
            before, after = self._contexts.get(state.epoch, ("", ""))
        return self.constraint_engine.query_pinyin(
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
        self._clear_query_cache()
        self.candidate_pages.clear_sessions()

    def invalidate_candidate_sessions(self) -> None:
        self.candidate_pages.clear_sessions()

    def reset_private_context(self) -> None:
        self.coordinator.invalidate_private_state()
        with self._contexts_lock:
            self._contexts.clear()
        self._clear_query_cache()
        self.candidate_pages.clear_sessions()

    def clear_history(self) -> None:
        self.script_policy.stable_script = None
        self._clear_query_cache()
        self.candidate_pages.clear_sessions()

    def runtime_performance_diagnostics(self) -> dict[str, object]:
        """Expose only cached timing/count metadata for live diagnosis."""

        runtime = getattr(self.coordinator.backend, "runtime", None)
        provider = getattr(runtime, "performance_diagnostics", None)
        diagnostics = {}
        if callable(provider):
            raw = provider()
            if isinstance(raw, dict):
                diagnostics.update(raw)
        diagnostics.update(self.candidate_pages.diagnostics())
        return diagnostics

    def diagnostics(self) -> dict[str, object]:
        diagnostics = self.coordinator.diagnostics()
        diagnostics.update(self._diagnostic_identity)
        return diagnostics
