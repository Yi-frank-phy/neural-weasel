from __future__ import annotations

import threading
from typing import Any

from .backends import BackendState
from .neural_candidate_pages_scored import NeuralCandidatePageManager as _ScoredPageManager
from .neural_candidates import (
    MAX_FROZEN_CANDIDATES,
    CandidatePage,
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _page_candidate_id,
    _SearchIdentity,
    _SearchSession,
)

_BACKGROUND_PAGE_DEADLINE_MS = 2500.0
_MAX_BACKGROUND_PAGE_RETRY_WAKES = 8


class NeuralCandidatePageManager(_ScoredPageManager):
    """Publish immutable snapshots and prepare all later pages off the request path."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._page_preparations: set[str] = set()
        self._page_preparation_events: dict[str, threading.Event] = {}
        self._page_preparation_cancel_events: dict[str, threading.Event] = {}
        self._page_preparation_wake_events: dict[str, threading.Event] = {}

    def clear_sessions(self) -> None:
        with self._state_lock:
            for candidate_set_id in tuple(self._page_preparations):
                self._cancel_page_preparation_locked(candidate_set_id)
            self._page_preparations.clear()
            self._page_preparation_events.clear()
            self._page_preparation_cancel_events.clear()
            self._page_preparation_wake_events.clear()
            super().clear_sessions()

    def _expire_sessions(self) -> None:
        before = set(self._sessions)
        super()._expire_sessions()
        for candidate_set_id in before.difference(self._sessions):
            self._cancel_page_preparation_locked(candidate_set_id)

    def _new_session(
        self,
        identity: _SearchIdentity,
        state: BackendState | None,
    ) -> _SearchSession:
        for candidate_set_id, old in tuple(self._sessions.items()):
            if (
                old.identity.client_session_id == identity.client_session_id
                and old.identity != identity
            ):
                self._cancel_page_preparation_locked(candidate_set_id)
        session = super()._new_session(identity, state)
        for candidate_set_id in tuple(self._page_preparations):
            if candidate_set_id not in self._sessions:
                self._cancel_page_preparation_locked(candidate_set_id)
        return session

    def presentation_update_pending(self, candidate_set_id: str) -> bool:
        """Report whether this immutable snapshot still has a newer presentation."""

        with self._state_lock:
            session = self._sessions.get(candidate_set_id)
            if session is None:
                return False
            if candidate_set_id in self._background_searches:
                return True
            identity_key = self._async_identity_key(session.identity)
            return (
                identity_key in self._async_han_cache
                and not self._session_includes_async_han.get(candidate_set_id, False)
            )

    def query_page(
        self,
        *,
        client_session_id: str,
        composition_revision: int,
        context_epoch: int,
        context_session: str | None,
        source_revision: int | None,
        mode: NeuralLanguageMode | str,
        raw_keys: str,
        page_index: int,
        candidate_set_id: str | None,
        state: BackendState | None,
        deadline_ms: float | None = None,
        presentation_refresh: bool = False,
    ) -> CandidatePage:
        normalized_mode = NeuralLanguageMode(mode)
        identity = _SearchIdentity(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=normalized_mode,
            raw_keys=raw_keys,
        )

        # Navigation is deliberately read-only. The request thread may replay a
        # frozen page or report not-ready/exhausted, but it never starts model
        # work. This keeps PageDown/PageUp latency independent of CUDA.
        if page_index > 0:
            if deadline_ms is not None and float(deadline_ms) <= 0:
                raise CandidatePageTimeout("candidate page deadline expired")
            if candidate_set_id is None:
                raise CandidatePageError("candidate_set_id is required after page 0")
            with self._state_lock:
                self._expire_sessions()
                session = self._sessions.get(candidate_set_id)
                if session is None:
                    raise CandidatePageError("candidate_set_id is unknown or expired")
                if session.identity != identity:
                    raise CandidatePageError(
                        "candidate_set_id does not match the current composition"
                    )
                frozen = session.frozen_pages.get(page_index)
                if frozen is not None:
                    session.last_used = self.clock()
                    self._sessions.move_to_end(candidate_set_id)
                    self._record_metrics(frozen)
                    return frozen
                expected = max(session.frozen_pages, default=-1) + 1
                if page_index != expected:
                    raise CandidatePageError(
                        "new candidate pages must be requested in increasing order"
                    )
                current = session.frozen_pages.get(expected - 1)
                if current is not None and not current.has_more:
                    raise CandidatePageError("candidate search is exhausted")
                self._record_retryable_timeout(session)
                raise CandidatePageTimeout("candidate page is not ready")

        cached_page: CandidatePage | None = None
        cached_session: _SearchSession | None = None
        with self._state_lock:
            self._expire_sessions()
            # Multiple immutable presentation snapshots may share one input
            # identity. A normal retry always replays the newest published
            # snapshot. An explicit presentation pull advances it only when the
            # completed async cache is newer than that snapshot.
            for existing_set_id, session in reversed(tuple(self._sessions.items())):
                if session.identity != identity:
                    continue
                frozen = session.frozen_pages.get(0)
                if frozen is None:
                    continue
                if presentation_refresh:
                    identity_key = self._async_identity_key(identity)
                    async_ready = identity_key in self._async_han_cache
                    includes_async = self._session_includes_async_han.get(existing_set_id, False)
                    if async_ready and not includes_async:
                        break
                session.last_used = self.clock()
                self._sessions.move_to_end(existing_set_id)
                self._record_metrics(frozen)
                cached_page = frozen
                cached_session = session
                break

        if cached_page is not None and cached_session is not None:
            self._maybe_start_page_preparation(cached_session)
            return cached_page

        page = super().query_page(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=normalized_mode,
            raw_keys=raw_keys,
            page_index=0,
            candidate_set_id=None,
            state=state,
            deadline_ms=deadline_ms,
        )
        with self._state_lock:
            session = self._sessions.get(page.candidate_set_id)
        if session is not None:
            self._maybe_start_page_preparation(session)
        return page

    def _freeze_next_page(
        self,
        session: _SearchSession,
        page_index: int,
        page_size: int,
        absolute_deadline: float,
    ) -> CandidatePage:
        if page_index == 0:
            return super()._freeze_next_page(
                session,
                page_index,
                page_size,
                absolute_deadline,
            )

        # Publication capacity is a hard endpoint, not another search state.
        # Check it before any model work or pending-candidate mutation.
        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        remaining_capacity = MAX_FROZEN_CANDIDATES - total_frozen
        if remaining_capacity <= 0:
            raise CandidatePageError("candidate set reached the frozen-candidate safety limit")
        target_count = min(page_size, remaining_capacity)

        self._ensure_freezable(session, target_count, absolute_deadline)
        freezable = self._freezable_candidates(session)
        if len(freezable) < target_count and not session.exhausted:
            self._record_retryable_timeout(session)
            raise CandidatePageTimeout("candidate page search exceeded its absolute deadline")
        selected = freezable[:target_count]
        if not selected and session.exhausted:
            raise CandidatePageError("candidate search is exhausted")

        selected_set = set(selected)
        session.pending = [
            candidate for candidate in session.pending if candidate not in selected_set
        ]
        remaining_after = remaining_capacity - len(selected)
        has_more = remaining_after > 0 and (bool(session.pending) or not session.exhausted)
        length_bucket = min(
            (candidate.predicted_syllables for candidate in selected if candidate.script == "han"),
            default=None,
        )
        candidate_ids = tuple(
            _page_candidate_id(session.candidate_set_id, page_index, offset, candidate)
            for offset, candidate in enumerate(selected)
        )
        page = CandidatePage(
            candidate_set_id=session.candidate_set_id,
            page_index=page_index,
            page_size=page_size,
            has_more=has_more,
            candidates=tuple(selected),
            candidate_ids=candidate_ids,
            score_source=session.score_source,
            search_depth=session.search_depth,
            length_bucket=length_bucket,
            elapsed_ms=0.0,
            timeout_count=session.timeout_count,
        )
        session.frozen_pages[page_index] = page
        return page

    def _run_background_continuation(
        self,
        session: _SearchSession,
        identity_key: tuple[int, str | None, int | None, str, str],
        cancel_event: threading.Event,
    ) -> None:
        try:
            super()._run_background_continuation(session, identity_key, cancel_event)
        finally:
            self._maybe_start_page_preparation(session)

    def _cancel_page_preparation_locked(self, candidate_set_id: str) -> None:
        cancel = self._page_preparation_cancel_events.get(candidate_set_id)
        if cancel is not None:
            cancel.set()
        wake = self._page_preparation_wake_events.get(candidate_set_id)
        if wake is not None:
            wake.set()

    def _page_preparation_allowed_locked(self, session: _SearchSession) -> bool:
        candidate_set_id = session.candidate_set_id
        if self._sessions.get(candidate_set_id) is not session:
            return False
        page0 = session.frozen_pages.get(0)
        if page0 is None or not page0.has_more:
            return False
        if candidate_set_id in self._page_preparations:
            return False
        if candidate_set_id in self._background_searches:
            return False
        identity_key = self._async_identity_key(session.identity)
        if identity_key in self._async_han_cache and not self._session_includes_async_han.get(
            candidate_set_id, False
        ):
            return False
        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        return total_frozen < MAX_FROZEN_CANDIDATES

    def _maybe_start_page_preparation(self, session: _SearchSession) -> None:
        with self._state_lock:
            if not self._page_preparation_allowed_locked(session):
                return
            candidate_set_id = session.candidate_set_id
            done = threading.Event()
            cancel = threading.Event()
            wake = threading.Event()
            self._page_preparations.add(candidate_set_id)
            self._page_preparation_events[candidate_set_id] = done
            self._page_preparation_cancel_events[candidate_set_id] = cancel
            self._page_preparation_wake_events[candidate_set_id] = wake
            worker = threading.Thread(
                target=self._run_page_preparation,
                args=(session, done, cancel, wake),
                name=f"neural-pages-{candidate_set_id[:8]}",
                daemon=True,
            )
        try:
            worker.start()
        except Exception:
            with self._state_lock:
                self._page_preparations.discard(candidate_set_id)
                done.set()
            raise

    @staticmethod
    def _page_search_marker(session: _SearchSession) -> tuple[int, int, int, int, bool, int]:
        return (
            len(session.pending),
            len(session.frontier),
            len(session.expanded_paths),
            session.search_depth,
            session.exhausted,
            len(session.frozen_pages),
        )

    def _wait_for_continuation_idle(
        self,
        cancel: threading.Event,
        wake: threading.Event,
    ) -> bool:
        generation_provider = getattr(self.backend, "continuation_busy_generation", None)
        register = getattr(self.backend, "register_continuation_idle_wait", None)
        unregister = getattr(self.backend, "cancel_continuation_idle_wait", None)
        if not callable(generation_provider) or not callable(register) or cancel.is_set():
            return False
        generation = generation_provider()
        if generation is None:
            return False
        wake.clear()
        if cancel.is_set():
            return False
        register(generation, wake)
        if cancel.is_set():
            wake.set()
        wake.wait()
        if callable(unregister):
            unregister(wake)
        return not cancel.is_set()

    def _run_page_preparation(
        self,
        session: _SearchSession,
        done: threading.Event,
        cancel: threading.Event,
        wake: threading.Event,
    ) -> None:
        candidate_set_id = session.candidate_set_id
        retry_wakes = 0
        try:
            while not cancel.is_set():
                with self._state_lock:
                    if self._sessions.get(candidate_set_id) is not session:
                        return
                    if candidate_set_id in self._background_searches:
                        return
                    latest_page_index = max(session.frozen_pages, default=-1)
                    latest_page = session.frozen_pages.get(latest_page_index)
                    if latest_page is None or not latest_page.has_more:
                        return
                    next_page_index = latest_page_index + 1
                    before = self._page_search_marker(session)

                try:
                    page = super().query_page(
                        client_session_id=session.identity.client_session_id,
                        composition_revision=session.identity.composition_revision,
                        context_epoch=session.identity.context_epoch,
                        context_session=session.identity.context_session,
                        source_revision=session.identity.source_revision,
                        mode=session.identity.mode,
                        raw_keys=session.identity.raw_keys,
                        page_index=next_page_index,
                        candidate_set_id=candidate_set_id,
                        state=None,
                        deadline_ms=_BACKGROUND_PAGE_DEADLINE_MS,
                    )
                except CandidatePageTimeout:
                    with self._state_lock:
                        if self._sessions.get(candidate_set_id) is not session:
                            return
                        progressed = self._page_search_marker(session) != before
                    if progressed:
                        retry_wakes = 0
                        continue
                    if retry_wakes >= _MAX_BACKGROUND_PAGE_RETRY_WAKES:
                        return
                    if not self._wait_for_continuation_idle(cancel, wake):
                        return
                    retry_wakes += 1
                    continue
                except CandidatePageError:
                    return

                retry_wakes = 0
                if not page.has_more:
                    return
        finally:
            with self._state_lock:
                self._page_preparations.discard(candidate_set_id)
                current_cancel = self._page_preparation_cancel_events.get(candidate_set_id)
                if current_cancel is cancel:
                    self._page_preparation_cancel_events.pop(candidate_set_id, None)
                current_wake = self._page_preparation_wake_events.get(candidate_set_id)
                if current_wake is wake:
                    self._page_preparation_wake_events.pop(candidate_set_id, None)
                done.set()


__all__ = ["NeuralCandidatePageManager"]