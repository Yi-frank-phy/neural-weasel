from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Generator, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from typing import Any

from .backends import BackendState
from .neural_candidate_pages_scored import NeuralCandidatePageManager as _ScoredPageManager
from .neural_candidate_pages_v3 import _RootHanPlanEntry
from .neural_candidates import (
    CHINESE_CANDIDATE_COUNT,
    CHINESE_PAGE_SIZE,
    LATIN_PAGE_SIZE,
    MAX_ACTIVE_SEARCH_SESSIONS,
    MAX_FROZEN_CANDIDATES,
    CandidatePage,
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _page_candidate_id,
    _SearchIdentity,
    _SearchSession,
)
from .response_workers import start_worker

_BACKGROUND_PAGE_DEADLINE_MS = 2500.0
_PAGE_ZERO_LEXICAL_BACKGROUND_SLICE_MS = 100.0
_PAGE_PREPARATION_GRACE_SECONDS = 0.05
_PAGE_PREPARATION_RETRY_BACKOFF_SECONDS = 0.05
_BACKGROUND_PAGE_STATE_LOCK_SLICE_MS = 5.0


class _SearchCancelEvent(threading.Event):
    """A stage can stop independently; supersession permanently stops all stages."""

    def __init__(self, superseded: threading.Event) -> None:
        super().__init__()
        self.superseded = superseded

    def is_set(self) -> bool:
        return super().is_set() or self.superseded.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self.is_set()
            super().wait(0.005 if remaining is None else min(0.005, remaining))
        return True


@dataclass(eq=False)
class _RootPlanPreparation:
    cursor: Generator[None, None, tuple[_RootHanPlanEntry, ...]]
    cancel: threading.Event
    deadline: float


class _RootPlanWorker(threading.Thread):
    def __init__(self, manager: NeuralCandidatePageManager) -> None:
        super().__init__(
            target=manager._run_root_plan_preparations,
            name="neural-cold-root-plan",
            daemon=True,
        )
        self._manager = manager

    def start(self) -> None:
        try:
            super().start()
        except Exception:
            # Pipe dispatch starts this thread after the response. Roll back
            # the running flag there as well as at registration, so a retry
            # can resume the preserved static cursor after a failed start.
            with self._manager._state_lock:
                self._manager._root_plan_worker_active = False
            raise


class NeuralCandidatePageManager(_ScoredPageManager):
    _bound_latin_root_displays = True

    """Publish immutable snapshots and prepare all later pages off the request path."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._admission_lock = threading.Lock()
        self._latest_searches: OrderedDict[str, tuple[_SearchIdentity, threading.Event]] = (
            OrderedDict()
        )
        self._session_cancel_tokens: dict[str, threading.Event] = {}
        self._search_cancellation = threading.local()
        self._root_plan_preparations: OrderedDict[_SearchIdentity, _RootPlanPreparation] = (
            OrderedDict()
        )
        self._root_plan_worker_active = False
        self._page_preparations: set[str] = set()
        self._page_preparation_events: dict[str, threading.Event] = {}
        self._page_preparation_cancel_events: dict[str, threading.Event] = {}
        self._page_preparation_wake_events: dict[str, threading.Event] = {}
        self._full_page_zero_targets: set[str] = set()
        self._page_zero_search_completed: set[str] = set()
        self._page_zero_lexical_preparations: dict[str, threading.Event] = {}
        self._page_zero_lexical_completed: set[str] = set()
        self._page_zero_lexical_attempt_count = 0
        self._last_page_zero_lexical_result_count = 0
        self._last_page_zero_lexical_freezable_count = 0
        self._last_page_zero_lexical_elapsed_ms = 0.0
        self._last_page_zero_lexical_budget_ms = 0.0
        self._published_page_indices: dict[str, set[int]] = {}
        self._last_candidate_stage_metrics: dict[str, int | float | bool | None] = {
            "last_candidate_target_count": None,
            "last_candidate_generated_count": None,
            "last_candidate_pending_count": None,
            "last_candidate_freezable_count": None,
            "last_candidate_frontier_count": None,
            "last_candidate_frozen_count": None,
            "last_candidate_published_count": None,
            "last_candidate_background_elapsed_ms": None,
            "last_candidate_background_timed_out": None,
        }

    def _admit_search(self, identity: _SearchIdentity) -> threading.Event:
        # Never wait for the search state lock to tell obsolete CPU/GPU work to
        # stop. Equal-identity retries share one generation; A -> B -> A does not.
        with self._admission_lock:
            previous = self._latest_searches.get(identity.client_session_id)
            if previous is not None and previous[0] == identity and not previous[1].is_set():
                self._latest_searches.move_to_end(identity.client_session_id)
                return previous[1]
            if previous is not None:
                previous[1].set()
            token = threading.Event()
            self._latest_searches[identity.client_session_id] = (identity, token)
            self._latest_searches.move_to_end(identity.client_session_id)
            while len(self._latest_searches) > MAX_ACTIVE_SEARCH_SESSIONS:
                _, (_, evicted) = self._latest_searches.popitem(last=False)
                evicted.set()
            return token

    @contextmanager
    def _search_cancellation_scope(self, token: threading.Event) -> Iterator[None]:
        previous = getattr(self._search_cancellation, "token", None)
        self._search_cancellation.token = token
        try:
            yield
        finally:
            self._search_cancellation.token = previous

    def _touch_admitted_session(self, session: _SearchSession) -> None:
        # Navigation refreshes bounded LRU ownership without admitting an old
        # generation or clearing its cancellation signal.
        with self._admission_lock:
            latest = self._latest_searches.get(session.identity.client_session_id)
            if latest is not None and latest[1] is self._session_cancel_tokens.get(
                session.candidate_set_id
            ):
                self._latest_searches.move_to_end(session.identity.client_session_id)

    def _session_cancel_token(self, session: _SearchSession) -> threading.Event:
        token = self._session_cancel_tokens.get(session.candidate_set_id)
        if token is None:
            token = threading.Event()
            token.set()
        return token

    def _new_search_cancel_event(self, session: _SearchSession) -> threading.Event:
        return _SearchCancelEvent(self._session_cancel_token(session))

    def _raise_if_query_expired(self, absolute_deadline: float | None = None) -> None:
        token = getattr(self._search_cancellation, "token", None)
        if token is not None and token.is_set():
            raise CandidatePageTimeout("candidate search was superseded")
        super()._raise_if_query_expired(absolute_deadline)

    def _background_publication_ready(self, session: _SearchSession) -> bool:
        return session.identity.mode is NeuralLanguageMode.CHINESE_FIRST and (
            0 in session.frozen_pages
            or len(self._freezable_candidates(session)) >= CHINESE_PAGE_SIZE
        )

    def _run_background_continuation(
        self,
        session: _SearchSession,
        identity_key: tuple[int, str | None, int | None, str, str],
        cancel_event: threading.Event,
    ) -> None:
        with self._search_cancellation_scope(self._session_cancel_token(session)):
            self._run_background_continuation_scoped(session, identity_key, cancel_event)

    def diagnostics(self) -> dict[str, int | float | bool | None]:
        with self._state_lock:
            result = super().diagnostics()
            result.update(
                {
                    "page_zero_lexical_attempt_count": self._page_zero_lexical_attempt_count,
                    "last_page_zero_lexical_result_count": (
                        self._last_page_zero_lexical_result_count
                    ),
                    "last_page_zero_lexical_freezable_count": (
                        self._last_page_zero_lexical_freezable_count
                    ),
                    "last_page_zero_lexical_elapsed_ms": (self._last_page_zero_lexical_elapsed_ms),
                    "last_page_zero_lexical_budget_ms": (self._last_page_zero_lexical_budget_ms),
                }
            )
            result.update(self._last_candidate_stage_metrics)
            return result

    def _snapshot_candidate_stages_locked(self, session: _SearchSession) -> None:
        frozen_count = sum(len(page.candidates) for page in session.frozen_pages.values())
        published_pages = self._published_page_indices.get(session.candidate_set_id, set())
        published_count = sum(
            len(page.candidates)
            for page_index, page in session.frozen_pages.items()
            if page_index in published_pages
        )
        self._last_candidate_stage_metrics.update(
            {
                "last_candidate_target_count": (
                    CHINESE_CANDIDATE_COUNT
                    if session.identity.mode is NeuralLanguageMode.CHINESE_FIRST
                    else LATIN_PAGE_SIZE
                ),
                "last_candidate_generated_count": len(session.seen_candidates),
                "last_candidate_pending_count": len(session.pending),
                "last_candidate_freezable_count": len(self._freezable_candidates(session)),
                "last_candidate_frontier_count": len(session.frontier),
                "last_candidate_frozen_count": frozen_count,
                "last_candidate_published_count": published_count,
            }
        )

    def _note_page_published_locked(self, session: _SearchSession, page: CandidatePage) -> None:
        self._published_page_indices.setdefault(session.candidate_set_id, set()).add(
            page.page_index
        )
        self._snapshot_candidate_stages_locked(session)

    def clear_sessions(self) -> None:
        with self._admission_lock:
            for _, token in self._latest_searches.values():
                token.set()
            self._latest_searches.clear()
        with self._state_lock:
            for token in self._session_cancel_tokens.values():
                token.set()
            self._session_cancel_tokens.clear()
            for preparation in self._root_plan_preparations.values():
                preparation.cancel.set()
            for cancel in self._page_zero_lexical_preparations.values():
                cancel.set()
            self._page_zero_lexical_preparations.clear()
            self._page_zero_lexical_completed.clear()
            for candidate_set_id in tuple(self._page_preparations):
                self._cancel_page_preparation_locked(candidate_set_id)
            self._page_preparations.clear()
            self._page_preparation_events.clear()
            self._page_preparation_cancel_events.clear()
            self._page_preparation_wake_events.clear()
            self._full_page_zero_targets.clear()
            self._page_zero_search_completed.clear()
            self._published_page_indices.clear()
            super().clear_sessions()

    def _prepare_cold_root_plan(self, identity: _SearchIdentity, deadline: float) -> None:
        """Resume arbitrary cold phonetic work without retaining an editor snapshot."""
        with self._state_lock_until(deadline):
            self._raise_if_query_expired(deadline)
            if (
                identity.mode is not NeuralLanguageMode.CHINESE_FIRST
                or self.matcher is None
                or identity.raw_keys in self._root_han_plans
                or identity.raw_keys in self._startup_letter_root_plans
                or any(session.identity == identity for session in self._sessions.values())
            ):
                return
            preparation = self._root_plan_preparations.get(identity)
            if preparation is None:
                # Include running cancelled work in the bound. A single worker
                # drains it; new requests must not create an unbounded thread pool.
                if len(self._root_plan_preparations) >= MAX_ACTIVE_SEARCH_SESSIONS:
                    raise CandidatePageTimeout("cold root search queue is busy")
                preparation = _RootPlanPreparation(
                    self._iter_root_han_plan(identity.raw_keys),
                    _SearchCancelEvent(
                        getattr(self._search_cancellation, "token", None) or threading.Event()
                    ),
                    self.clock() + _BACKGROUND_PAGE_DEADLINE_MS / 1000.0,
                )
                self._root_plan_preparations[identity] = preparation
                try:
                    # Use the request's existing absolute budget. An extra
                    # five-millisecond cutoff forced otherwise timely cold
                    # scans to wait for another client request. Preserve the
                    # same cursor for background recovery if this budget ends.
                    while self.clock() < deadline:
                        self._raise_if_query_expired(deadline)
                        next(preparation.cursor)
                except StopIteration as completed:
                    self._store_root_plan_locked(identity.raw_keys, completed.value)
                    del self._root_plan_preparations[identity]
                    return
                except Exception:
                    del self._root_plan_preparations[identity]
                    with suppress(Exception):
                        preparation.cursor.close()
                    raise
            if not self._root_plan_worker_active:
                self._root_plan_worker_active = True
                try:
                    start_worker(_RootPlanWorker(self))
                except Exception:
                    self._root_plan_worker_active = False
                    del self._root_plan_preparations[identity]
                    with suppress(Exception):
                        preparation.cursor.close()
                    raise
            raise CandidatePageTimeout("cold root search is still being prepared")

    def _store_root_plan_locked(self, raw_keys: str, plan: tuple[_RootHanPlanEntry, ...]) -> None:
        self._remember_root_han_plan(self._root_han_plans, raw_keys, plan)

    def _run_root_plan_preparations(self) -> None:
        while True:
            with self._state_lock:
                if not self._root_plan_preparations:
                    self._root_plan_worker_active = False
                    return
                identity, preparation = next(iter(self._root_plan_preparations.items()))
            last_yield = self.clock()
            try:
                while not preparation.cancel.is_set() and self.clock() < preparation.deadline:
                    try:
                        next(preparation.cursor)
                    except StopIteration as completed:
                        with self._state_lock:
                            if (
                                self._root_plan_preparations.get(identity) is preparation
                                and not preparation.cancel.is_set()
                                and self.clock() < preparation.deadline
                            ):
                                self._store_root_plan_locked(identity.raw_keys, completed.value)
                        break
                    if (self.clock() - last_yield) * 1000.0 >= _BACKGROUND_PAGE_STATE_LOCK_SLICE_MS:
                        preparation.cancel.wait(0.001)
                        last_yield = self.clock()
            except Exception:
                # Discard this static cursor and continue draining the bounded
                # queue. An unexpected iterator error must not wedge all input.
                pass
            finally:
                try:
                    with suppress(Exception):
                        preparation.cursor.close()
                finally:
                    with self._state_lock:
                        if self._root_plan_preparations.get(identity) is preparation:
                            del self._root_plan_preparations[identity]

    def _cancel_superseded_searches_locked(self, identity: _SearchIdentity) -> None:
        self._raise_if_query_expired()
        for old_identity, preparation in self._root_plan_preparations.items():
            if (
                old_identity.client_session_id == identity.client_session_id
                and old_identity != identity
            ):
                preparation.cancel.set()
        for candidate_set_id, old in self._sessions.items():
            if (
                old.identity.client_session_id != identity.client_session_id
                or old.identity == identity
            ):
                continue
            token = self._session_cancel_tokens.get(candidate_set_id)
            if token is not None:
                token.set()
            for events in (self._background_cancel_events, self._page_zero_lexical_preparations):
                cancel = events.get(candidate_set_id)
                if cancel is not None:
                    cancel.set()
            self._cancel_page_preparation_locked(candidate_set_id)

    def _expire_sessions(self) -> None:
        before = set(self._sessions)
        for candidate_set_id, token in tuple(self._session_cancel_tokens.items()):
            if token.is_set():
                self._sessions.pop(candidate_set_id, None)
        super()._expire_sessions()
        for candidate_set_id in before.difference(self._sessions):
            token = self._session_cancel_tokens.pop(candidate_set_id, None)
            if token is not None:
                token.set()
            cancel = self._page_zero_lexical_preparations.pop(candidate_set_id, None)
            if cancel is not None:
                cancel.set()
            self._page_zero_lexical_completed.discard(candidate_set_id)
            self._cancel_page_preparation_locked(candidate_set_id)
            self._full_page_zero_targets.discard(candidate_set_id)
            self._page_zero_search_completed.discard(candidate_set_id)
            self._published_page_indices.pop(candidate_set_id, None)

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
                cancel = self._page_zero_lexical_preparations.get(candidate_set_id)
                if cancel is not None:
                    cancel.set()
                self._cancel_page_preparation_locked(candidate_set_id)
        session = super()._new_session(identity, state)
        self._session_cancel_tokens[session.candidate_set_id] = (
            getattr(self._search_cancellation, "token", None) or threading.Event()
        )
        try:
            self._raise_if_query_expired()
        except CandidatePageTimeout:
            self._expire_sessions()
            raise
        for candidate_set_id in tuple(self._session_cancel_tokens):
            if candidate_set_id not in self._sessions:
                self._session_cancel_tokens.pop(candidate_set_id).set()
        if self._has_current_async_han(session):
            # The prior revision already completed the continuation work for
            # this exact context/input identity. Reuse that immutable cache
            # without forcing a redundant first-publication retry.
            self._page_zero_search_completed.add(session.candidate_set_id)
        for candidate_set_id in tuple(self._page_preparations):
            if candidate_set_id not in self._sessions:
                self._cancel_page_preparation_locked(candidate_set_id)
        return session

    def presentation_update_pending(self, candidate_set_id: str) -> bool:
        """Return whether page zero still needs its first publication.

        Once a nonempty page zero has been returned it is the immutable
        presentation for this input identity. Background work may prepare later
        pages, but it must never ask the UI to replace an already visible page.
        """
        with self._state_lock:
            session = self._sessions.get(candidate_set_id)
            return session is not None and 0 not in session.frozen_pages

    def presentation_update_pending_until(
        self, candidate_set_id: str, absolute_deadline: float
    ) -> bool:
        """Keep response readiness reads inside the original query budget."""
        with self._state_lock_until(absolute_deadline):
            return self.presentation_update_pending(candidate_set_id)

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
        deadline_started: float | None = None,
        presentation_refresh: bool = False,
    ) -> CandidatePage:
        identity = _SearchIdentity(
            client_session_id,
            composition_revision,
            context_epoch,
            context_session,
            source_revision,
            NeuralLanguageMode(mode),
            raw_keys,
        )
        if page_index == 0:
            deadline = self._candidate_deadline_at(
                page_index=page_index,
                deadline_ms=deadline_ms,
                deadline_started=deadline_started,
            )
            self._raise_if_query_expired(deadline)
            token = self._admit_search(identity)
        else:
            token = self._session_cancel_tokens.get(candidate_set_id or "", threading.Event())
        with self._search_cancellation_scope(token):
            return self._query_page(
                client_session_id=client_session_id,
                composition_revision=composition_revision,
                context_epoch=context_epoch,
                context_session=context_session,
                source_revision=source_revision,
                mode=mode,
                raw_keys=raw_keys,
                page_index=page_index,
                candidate_set_id=candidate_set_id,
                state=state,
                deadline_ms=deadline_ms,
                deadline_started=deadline_started,
                presentation_refresh=presentation_refresh,
            )

    def _query_page(
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
        deadline_started: float | None = None,
        presentation_refresh: bool = False,
    ) -> CandidatePage:
        normalized_mode = NeuralLanguageMode(mode)
        absolute_deadline = self._candidate_deadline_at(
            page_index=page_index,
            deadline_ms=deadline_ms,
            deadline_started=deadline_started,
        )
        self._raise_if_query_expired(absolute_deadline)
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
            with self._state_lock_until(absolute_deadline):
                self._raise_if_query_expired(absolute_deadline)
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
                    self._touch_admitted_session(session)
                    self._record_metrics(frozen)
                    self._note_page_published_locked(session, frozen)
                    self._raise_if_query_expired(absolute_deadline)
                    return frozen
                expected = max(session.frozen_pages, default=-1) + 1
                if page_index != expected:
                    raise CandidatePageError(
                        "new candidate pages must be requested in increasing order"
                    )
                current = session.frozen_pages.get(expected - 1)
                if current is not None and not current.has_more:
                    raise CandidatePageError("candidate search is exhausted")
                # A valid retry for the next page is active use even while the
                # background preparer has not frozen that page yet. Keep this
                # session at the hot end of the bounded LRU; otherwise several
                # concurrent compositions can evict the set while native code
                # is polling it, turning a retryable pending state into a hard
                # ``candidate_set_invalid`` error.
                session.last_used = self.clock()
                self._sessions.move_to_end(candidate_set_id)
                self._touch_admitted_session(session)
                self._record_retryable_timeout(session)
                raise CandidatePageTimeout("candidate page is not ready")

        cached_page: CandidatePage | None = None
        cached_session: _SearchSession | None = None
        unpublished_session: _SearchSession | None = None
        with self._state_lock_until(absolute_deadline):
            self._raise_if_query_expired(absolute_deadline)
            self._cancel_superseded_searches_locked(identity)
            self._expire_sessions()
            # One input identity owns one immutable page zero. Explicit refresh
            # requests are legacy first-publication retries and may only replay
            # an already published snapshot.
            for existing_set_id, session in reversed(tuple(self._sessions.items())):
                if session.identity != identity:
                    continue
                if candidate_set_id is not None and candidate_set_id != existing_set_id:
                    raise CandidatePageError(
                        "candidate_set_id does not match the current composition"
                    )
                frozen = session.frozen_pages.get(0)
                if frozen is None:
                    unpublished_session = session
                    continue
                session.last_used = self.clock()
                self._sessions.move_to_end(existing_set_id)
                self._record_metrics(frozen)
                cached_page = frozen
                cached_session = session
                break

        if cached_page is not None and cached_session is not None:
            with self._state_lock_until(absolute_deadline):
                self._raise_if_query_expired(absolute_deadline)
                if self._sessions.get(cached_session.candidate_set_id) is cached_session:
                    self._note_page_published_locked(cached_session, cached_page)
            with self._state_lock_until(absolute_deadline):
                self._raise_if_query_expired(absolute_deadline)
                self._maybe_start_page_preparation(cached_session)
            self._raise_if_query_expired(absolute_deadline)
            return cached_page

        # The first request for a multi-token composition may have created a
        # session and launched its continuation search without publishing an
        # incomplete prefix page. Reuse that exact session on native retries;
        # creating a replacement session here would duplicate CUDA work and
        # lose the completed paths accumulated by the original worker.
        if unpublished_session is not None:
            started = self.clock() if deadline_started is None else float(deadline_started)
            page_size = (
                CHINESE_PAGE_SIZE
                if normalized_mode is NeuralLanguageMode.CHINESE_FIRST
                else LATIN_PAGE_SIZE
            )
            with self._state_lock_until(absolute_deadline):
                self._raise_if_query_expired(absolute_deadline)
                if (
                    self._sessions.get(unpublished_session.candidate_set_id)
                    is not unpublished_session
                ):
                    raise CandidatePageTimeout("candidate page presentation changed during retry")
                page = self._freeze_next_page(
                    unpublished_session,
                    0,
                    page_size,
                    absolute_deadline,
                )
                elapsed_ms = max(0.0, (self.clock() - started) * 1000.0)
                if page.elapsed_ms != elapsed_ms:
                    page = replace(page, elapsed_ms=elapsed_ms)
                    unpublished_session.frozen_pages[0] = page
                unpublished_session.last_used = self.clock()
                self._sessions.move_to_end(unpublished_session.candidate_set_id)
                self._record_metrics(page)
                self._note_page_published_locked(unpublished_session, page)
            with self._state_lock_until(absolute_deadline):
                self._maybe_start_page_preparation(unpublished_session)
            return page

        self._prepare_cold_root_plan(identity, absolute_deadline)
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
            deadline_started=deadline_started,
        )
        with self._state_lock_until(absolute_deadline):
            session = self._sessions.get(page.candidate_set_id)
            if session is not None:
                self._note_page_published_locked(session, page)
        if session is not None:
            with self._state_lock_until(absolute_deadline):
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
            if session.identity.mode is NeuralLanguageMode.CHINESE_FIRST:
                freezable = self._freezable_candidates(session)
                han_count = sum(candidate.script == "han" for candidate in freezable)
                has_incomplete_frontier = any(
                    path.script == "han"
                    and (
                        bool(getattr(path, "pending_options", ()))
                        or int(getattr(path, "matched_letters", len(session.identity.raw_keys)))
                        < len(session.identity.raw_keys.replace("'", ""))
                    )
                    and path.token_path not in session.expanded_paths
                    for path in session.frontier
                )
                has_pending_token_frontier = any(
                    path.script == "han"
                    and bool(getattr(path, "pending_options", ()))
                    and self._path_key(path) not in session.expanded_paths
                    for path in session.frontier
                )
                if len(freezable) < page_size:
                    candidate_set_id = session.candidate_set_id
                    search_completed = candidate_set_id in self._page_zero_search_completed
                    if candidate_set_id in self._page_zero_lexical_preparations:
                        self._record_retryable_timeout(session)
                        raise CandidatePageTimeout(
                            "complete candidate page is still being prepared"
                        )
                    # Legal-path discovery is an in-memory trie walk and does
                    # not need to wait for the much slower serial conditional
                    # scorer to produce its first whole-input path. Existing
                    # model-scored candidates retain their higher ranking tier;
                    # this only supplies the missing lower-tier tail.
                    supplement = getattr(self, "_lexical_completion_fallback", None)
                    lexical_eligible = callable(supplement) and self._lexical_completion_eligible(
                        session.identity.raw_keys
                    )
                    lexical_may_fill = lexical_eligible and self._lexical_tail_may_fill_page(
                        session.identity.raw_keys, page_size
                    )
                    if (
                        lexical_eligible
                        and candidate_set_id not in self._page_zero_lexical_completed
                    ):
                        # Resume unfinished lexical work in the background;
                        # never extend the absolute presentation deadline.
                        lexical_deadline = absolute_deadline
                        lexical_started = self.clock()
                        self._page_zero_lexical_attempt_count += 1
                        self._last_page_zero_lexical_budget_ms = max(
                            0.0,
                            (lexical_deadline - lexical_started) * 1000.0,
                        )
                        candidates = supplement(
                            session,
                            # Fill this immutable page first. The page preparer
                            # resumes the same cursor for the fixed 35-candidate set.
                            limit=max(0, page_size - len(freezable)),
                            absolute_deadline=lexical_deadline,
                        )
                        self._last_page_zero_lexical_elapsed_ms = max(
                            0.0,
                            (self.clock() - lexical_started) * 1000.0,
                        )
                        self._last_page_zero_lexical_result_count = len(candidates)
                        if lexical_may_fill or len(freezable) + len(candidates) >= page_size:
                            for candidate in candidates:
                                key = (candidate.text, candidate.consumed_keys)
                                if key in session.seen_candidates:
                                    continue
                                session.seen_candidates.add(key)
                                session.pending.append(candidate)
                            self._sort_pending(session)
                            freezable = self._freezable_candidates(session)
                            han_count = sum(candidate.script == "han" for candidate in freezable)
                        self._last_page_zero_lexical_freezable_count = len(freezable)
                    # Only wait for a full page when the legal-path count can
                    # reach one. A narrow but genuinely small index keeps its
                    # immediate partial-page behavior.
                    if len(freezable) < page_size and lexical_may_fill:
                        self._start_page_zero_lexical_preparation(session)
                    should_search = (
                        has_incomplete_frontier
                        and (
                            han_count == 0
                            or (has_pending_token_frontier and len(freezable) < page_size)
                        )
                        and not session.exhausted
                        and not search_completed
                    )
                    if should_search:
                        self._start_background_continuation(session)
                    # The continuation worker temporarily removes its selected
                    # parents from ``session.frontier`` while scoring outside
                    # the manager lock. A retry in that window must remain
                    # pending rather than freezing an incomplete first page.
                    # Once at least one whole-input Han candidate is ready,
                    # publish it immediately. A Latin fallback alone must not
                    # freeze Chinese page zero while productive Han work is in
                    # flight. Waiting for a background search to fill all seven
                    # slots can take seconds and outlive the native presentation
                    # refresh budget, which appears to users as an empty
                    # candidate window. Incomplete Han prefixes remain filtered
                    # by ``_freezable_candidates`` below.
                    if candidate_set_id in self._page_zero_lexical_preparations or (
                        han_count == 0
                        and (should_search or candidate_set_id in self._background_searches)
                    ):
                        self._record_retryable_timeout(session)
                        raise CandidatePageTimeout(
                            "complete candidate page is still being prepared"
                        )
            page = super()._freeze_next_page(
                session,
                page_index,
                page_size,
                absolute_deadline,
            )
            if self._uses_fixed_chinese_capacity(session):
                page = replace(page, has_more=True)
                session.frozen_pages[page_index] = page
                # The lexical supplement may already contain the complete
                # fixed-capacity set. Publish its remaining immutable pages
                # synchronously: this is list slicing only, avoids a race with
                # an in-flight neural batch, and makes PageDown immediately
                # reachable without adding model work to the request path.
                # A full model bucket can consist mostly of longer readings.
                # Publish page zero promptly, but leave later pages mutable
                # until the background lexical walk can find exact spellings.
                while (
                    not self._may_have_exact_spelling_tail(session)
                    and sum(len(frozen.candidates) for frozen in session.frozen_pages.values())
                    < CHINESE_CANDIDATE_COUNT
                ):
                    next_page_index = max(session.frozen_pages) + 1
                    remaining = CHINESE_CANDIDATE_COUNT - sum(
                        len(frozen.candidates) for frozen in session.frozen_pages.values()
                    )
                    target = min(page_size, remaining)
                    if len(self._freezable_candidates(session)) < target:
                        break
                    self._freeze_next_page(
                        session,
                        next_page_index,
                        page_size,
                        absolute_deadline,
                    )
            # Once page zero has a coherent immutable snapshot, the broad
            # first-publication search no longer owns the continuation lane.
            # Cancel it so the dedicated later-page preparer can take over
            # immediately instead of waiting for the five-second background
            # deadline before PageDown becomes usable.
            cancel = self._background_cancel_events.get(session.candidate_set_id)
            if cancel is not None:
                cancel.set()
            return page

        # Publication capacity is a hard endpoint, not another search state.
        # Check it before any model work or pending-candidate mutation.
        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        fixed_chinese_capacity = self._uses_fixed_chinese_capacity(session)
        capacity = CHINESE_CANDIDATE_COUNT if fixed_chinese_capacity else MAX_FROZEN_CANDIDATES
        remaining_capacity = capacity - total_frozen
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
        has_more = remaining_after > 0 and (
            (fixed_chinese_capacity and len(selected) == target_count)
            or bool(session.pending)
            or not session.exhausted
        )
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

    def _note_background_frontier_capacity(
        self,
        session: _SearchSession,
        selected: Any,
    ) -> None:
        """Record off-thread evidence that this search can fill page zero.

        A tiny/synthetic vocabulary may expose fewer than seven distinct legal
        candidates. Requiring seven there would restart publication search
        forever. The background expander already computed each selected root's
        legal next-token edges, so it can establish capacity without moving
        pinyin-trie work back onto the latency-sensitive request thread.
        """

        candidate_set_id = session.candidate_set_id
        if candidate_set_id in self._full_page_zero_targets:
            return

        possible = sum(
            candidate.script == "han" and candidate.completes_input for candidate in session.pending
        )
        possible += int(any(candidate.script == "latin" for candidate in session.pending))
        possible += sum(len(item[3]) for item in selected)
        if possible >= CHINESE_PAGE_SIZE:
            self._full_page_zero_targets.add(candidate_set_id)

    def _freezable_candidates(self, session: _SearchSession) -> list[Any]:
        candidates = super()._freezable_candidates(session)
        if session.identity.mode is not NeuralLanguageMode.CHINESE_FIRST:
            return candidates
        freezable_ids = {id(candidate) for candidate in candidates}
        # Prefix paths are search state, not commit-ready candidates. Publishing
        # them for a longer composition produced pages such as z* roots for
        # ``zuixiaohua``; once frozen, the correct continuation could never
        # replace that misleading page.
        return [
            candidate
            for candidate in session.pending
            if (
                id(candidate) in freezable_ids
                # Zero is the best possible prediction bucket. An unfinished
                # token fragment can still produce another zero-bucket path,
                # but cannot produce a shorter bucket. Freeze the first ranked
                # snapshot of completed zero-bucket paths instead of withholding
                # every ready candidate until all such fragments are explored.
                or (
                    0 not in session.frozen_pages
                    and candidate.script == "han"
                    and candidate.completes_input
                    and candidate.predicted_syllables == 0
                )
                # The deterministic lexical walk has already enumerated and
                # ranked complete whole-input paths. A still-pending neural
                # frontier must not hide that bounded tail, otherwise page
                # zero freezes at one candidate and a 35-item set spills onto
                # a sixth page while PageDown waits on CUDA work.
                or (
                    candidate.constraint_kind == "pinyin_lexical_fallback"
                    and candidate.completes_input
                )
            )
            and (candidate.script != "han" or candidate.completes_input)
        ]

    def _start_page_zero_lexical_preparation(self, session: _SearchSession) -> None:
        candidate_set_id = session.candidate_set_id
        if (
            self._sessions.get(candidate_set_id) is not session
            or candidate_set_id in self._page_zero_lexical_preparations
            or candidate_set_id in self._page_zero_lexical_completed
        ):
            return
        # The model continuation may update the live frontier while this CPU
        # search runs. Only immutable, revision-scoped search inputs cross the
        # lock boundary; late results are checked against the same session.
        snapshot = replace(
            session,
            pending=list(session.pending),
            frontier=list(session.frontier),
            frozen_pages=dict(session.frozen_pages),
            seen_candidates=set(session.seen_candidates),
            expanded_paths=set(session.expanded_paths),
        )
        # This worker owns first publication only. Do not withhold seven ready
        # candidates while enumerating the remaining 28 for later pages.
        limit = max(0, CHINESE_PAGE_SIZE - len(self._freezable_candidates(session)))
        cancel = self._new_search_cancel_event(session)
        self._page_zero_lexical_preparations[candidate_set_id] = cancel
        worker = threading.Thread(
            target=self._run_page_zero_lexical_preparation,
            args=(session, snapshot, limit, cancel),
            name=f"neural-page-zero-{candidate_set_id[:8]}",
            daemon=True,
        )
        try:
            start_worker(worker)
        except Exception:
            self._page_zero_lexical_preparations.pop(candidate_set_id, None)
            cancel.set()
            raise

    def _run_page_zero_lexical_preparation(
        self,
        session: _SearchSession,
        snapshot: _SearchSession,
        limit: int,
        cancel: threading.Event,
    ) -> None:
        with (
            self._search_cancellation_scope(self._session_cancel_token(session)),
            suppress(CandidatePageTimeout),
        ):
            self._run_page_zero_lexical_preparation_scoped(session, snapshot, limit, cancel)

    def _run_page_zero_lexical_preparation_scoped(
        self,
        session: _SearchSession,
        snapshot: _SearchSession,
        limit: int,
        cancel: threading.Event,
    ) -> None:
        candidate_set_id = session.candidate_set_id
        started = self.clock()
        should_prepare_later_pages = False
        try:
            supplement = getattr(self, "_lexical_completion_fallback", None)
            while callable(supplement) and limit > 0 and not cancel.is_set():
                candidates = supplement(
                    snapshot,
                    limit=limit,
                    absolute_deadline=(
                        self.clock() + _PAGE_ZERO_LEXICAL_BACKGROUND_SLICE_MS / 1000.0
                    ),
                )
                with self._state_lock:
                    if cancel.is_set() or self._sessions.get(candidate_set_id) is not session:
                        return
                    for candidate in candidates:
                        key = (candidate.text, candidate.consumed_keys)
                        if key in session.seen_candidates:
                            continue
                        session.seen_candidates.add(key)
                        session.pending.append(candidate)
                    if candidates:
                        self._sort_pending(session)
                    freezable_count = len(self._freezable_candidates(session))
                    # Model work may have added duplicates while this lexical
                    # batch ran. Resume only the still-missing first-page slots.
                    limit = max(0, CHINESE_PAGE_SIZE - freezable_count)
                    self._last_page_zero_lexical_elapsed_ms = max(
                        0.0, (self.clock() - started) * 1000.0
                    )
                    self._last_page_zero_lexical_result_count = len(candidates)
                    self._last_page_zero_lexical_freezable_count = freezable_count
                    if 0 not in session.frozen_pages and freezable_count >= CHINESE_PAGE_SIZE:
                        self._freeze_next_page(
                            session,
                            0,
                            CHINESE_PAGE_SIZE,
                            self.clock() + _BACKGROUND_PAGE_DEADLINE_MS / 1000.0,
                        )
                    should_prepare_later_pages = 0 in session.frozen_pages
                    exhausted = candidate_set_id in self._lexical_completion_exhausted
                if should_prepare_later_pages or exhausted:
                    break
                # A short pause lets an obsolete revision cancel between CPU batches.
                cancel.wait(0.001)
        finally:
            with self._state_lock:
                if self._page_zero_lexical_preparations.get(candidate_set_id) is cancel:
                    self._page_zero_lexical_preparations.pop(candidate_set_id, None)
                    if (
                        self._sessions.get(candidate_set_id) is session
                        and not cancel.is_set()
                        and (
                            0 in session.frozen_pages
                            or candidate_set_id in self._lexical_completion_exhausted
                        )
                    ):
                        self._page_zero_lexical_completed.add(candidate_set_id)
                        if 0 not in session.frozen_pages:
                            with suppress(CandidatePageTimeout):
                                self._freeze_next_page(
                                    session,
                                    0,
                                    CHINESE_PAGE_SIZE,
                                    self.clock() + _BACKGROUND_PAGE_DEADLINE_MS / 1000.0,
                                )
                        should_prepare_later_pages = 0 in session.frozen_pages
            if should_prepare_later_pages:
                self._maybe_start_page_preparation(session)

    def _run_background_continuation_scoped(
        self,
        session: _SearchSession,
        identity_key: tuple[int, str | None, int | None, str, str],
        cancel_event: threading.Event,
    ) -> None:
        try:
            super()._run_background_continuation(session, identity_key, cancel_event)
        finally:
            with self._state_lock:
                if self._sessions.get(session.candidate_set_id) is session and (
                    session.exhausted or 0 in session.frozen_pages
                ):
                    # Worker retirement includes deadline misses, empty attempts
                    # and cancellation. Only terminal search/publication permits
                    # a later request to freeze a fallback instead of retrying.
                    self._page_zero_search_completed.add(session.candidate_set_id)
                # Another client can publish page zero for this same input
                # while this scorer is active. Its preparer is deferred to
                # avoid competing continuation work, so retiring the scorer
                # must wake that client too; page navigation never does.
                waiting_sessions = tuple(
                    current
                    for current in self._sessions.values()
                    if self._async_identity_key(current.identity) == identity_key
                )
            for current in waiting_sessions:
                self._maybe_start_page_preparation(current)

    def _cancel_page_preparation_locked(self, candidate_set_id: str) -> None:
        cancel = self._page_preparation_cancel_events.get(candidate_set_id)
        if cancel is not None:
            cancel.set()
        wake = self._page_preparation_wake_events.get(candidate_set_id)
        if wake is not None:
            wake.set()

    def _page_preparation_allowed_locked(self, session: _SearchSession) -> bool:
        candidate_set_id = session.candidate_set_id
        if self._session_cancel_token(session).is_set():
            return False
        if self._sessions.get(candidate_set_id) is not session:
            return False
        page0 = session.frozen_pages.get(0)
        if page0 is None or not page0.has_more:
            return False
        if candidate_set_id in self._page_preparations:
            return False
        identity_key = self._async_identity_key(session.identity)
        if any(
            self._async_identity_key(self._sessions[active].identity) == identity_key
            and (
                (cancel := self._background_cancel_events.get(active)) is None
                or not cancel.is_set()
            )
            for active in self._background_searches
            if active in self._sessions
        ):
            return False
        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        capacity = (
            CHINESE_CANDIDATE_COUNT
            if self._uses_fixed_chinese_capacity(session)
            else MAX_FROZEN_CANDIDATES
        )
        return total_frozen < capacity

    @staticmethod
    def _uses_fixed_chinese_capacity(session: _SearchSession) -> bool:
        if session.identity.mode is not NeuralLanguageMode.CHINESE_FIRST:
            return False
        return 0 in session.frozen_pages

    def _may_have_exact_spelling_tail(self, session: _SearchSession) -> bool:
        if self.matcher is None:
            return False
        compact = session.identity.raw_keys.replace("'", "").replace("-", "")
        if not self.matcher.is_complete_syllable_sequence(compact):
            return False
        return any(
            candidate.script == "han"
            and candidate.completes_input
            and candidate.pinyin.replace("'", "") != compact
            for candidate in session.pending
        )

    def _start_background_continuation(self, session: _SearchSession) -> None:
        # The scored layer asks again *after* page-zero publication. The
        # page-zero freeze cancelled any first-publication scorer, but this
        # later call would start a new one and keep the later-page preparer
        # waiting for it. Once page zero is immutable, that preparer owns the
        # remaining search; a scorer is still allowed before publication.
        if self._session_cancel_token(session).is_set() or self._uses_fixed_chinese_capacity(
            session
        ):
            return
        super()._start_background_continuation(session)

    def _maybe_start_page_preparation(self, session: _SearchSession) -> None:
        with self._state_lock:
            if not self._page_preparation_allowed_locked(session):
                return
            candidate_set_id = session.candidate_set_id
            done = threading.Event()
            cancel = self._new_search_cancel_event(session)
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
            start_worker(worker)
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
        retry_generation: int,
    ) -> bool:
        register = getattr(self.backend, "register_continuation_idle_wait", None)
        unregister = getattr(self.backend, "cancel_continuation_idle_wait", None)
        if not callable(register) or cancel.is_set():
            return False
        wake.clear()
        if cancel.is_set():
            return False
        # Register against the generation observed by the failed page search.
        # The backend sets the event immediately when that exact generation has
        # already completed, closing the timeout-to-registration lost wakeup.
        register(retry_generation, wake)
        try:
            while not cancel.is_set():
                if wake.wait(0.005):
                    break
        finally:
            if callable(unregister):
                unregister(wake)
        return not cancel.is_set()

    def _ensure_freezable(
        self,
        session: _SearchSession,
        page_size: int,
        absolute_deadline: float,
    ) -> None:
        # The later-page query owns the state lock once. CPU-only resumptions
        # never reach the provider's unlock, so yield between complete steps as
        # well. This is a cooperative slice, not a bound on a single step.
        candidate_set_id = session.candidate_set_id
        cancel = self._page_preparation_cancel_events.get(candidate_set_id)
        last_yield = self.clock()
        while not session.exhausted:
            if self._sessions.get(candidate_set_id) is not session or (
                cancel is not None and cancel.is_set()
            ):
                raise CandidatePageError("candidate set was invalidated during search")
            if len(self._freezable_candidates(session)) >= page_size:
                return
            if self.clock() >= absolute_deadline:
                return
            progressed = self._expand_one_frontier(session, absolute_deadline)
            self._sort_pending(session)
            if progressed == 0:
                return
            if (
                cancel is not None
                and 0 in session.frozen_pages
                and (self.clock() - last_yield) * 1000 >= _BACKGROUND_PAGE_STATE_LOCK_SLICE_MS
            ):
                self._state_lock.release()
                try:
                    cancel.wait(0.001)
                finally:
                    self._state_lock.acquire()
                last_yield = self.clock()
                # Recheck even when the last step exhausted the frontier: its
                # caller must not freeze an old session after a focus change.
                if self._sessions.get(candidate_set_id) is not session or cancel.is_set():
                    raise CandidatePageError("candidate set was invalidated during search")

    def _run_page_preparation(
        self,
        session: _SearchSession,
        done: threading.Event,
        cancel: threading.Event,
        wake: threading.Event,
    ) -> None:
        with (
            self._search_cancellation_scope(self._session_cancel_token(session)),
            suppress(CandidatePageError, CandidatePageTimeout),
        ):
            self._run_page_preparation_scoped(session, done, cancel, wake)

    def _run_page_preparation_scoped(
        self,
        session: _SearchSession,
        done: threading.Event,
        cancel: threading.Event,
        wake: threading.Event,
    ) -> None:
        candidate_set_id = session.candidate_set_id
        preparation_started = self.clock()
        timed_out = False
        try:
            # Rapid typing should cancel the old snapshot before its later-page
            # work can compete with the next foreground request.
            if cancel.wait(_PAGE_PREPARATION_GRACE_SECONDS):
                return

            # Page zero may have consumed most of its 35 ms budget before the
            # lexical walk started.  If it froze a coherent first page but not
            # the full fixed set, finish the same bounded, model-free trie walk
            # here before asking the serial conditional scorer for later pages.
            # This keeps PageDown responsive without extending the foreground
            # deadline or changing the immutable page-zero snapshot.
            if session.identity.mode is NeuralLanguageMode.CHINESE_FIRST:
                with self._state_lock:
                    if self._sessions.get(candidate_set_id) is not session:
                        return
                    total_frozen = sum(
                        len(page.candidates) for page in session.frozen_pages.values()
                    )
                    freezable_count = len(self._freezable_candidates(session))
                    lexical_limit = max(
                        0,
                        CHINESE_CANDIDATE_COUNT - total_frozen - freezable_count,
                    )
                    if self._may_have_exact_spelling_tail(session):
                        lexical_limit = max(
                            lexical_limit,
                            min(CHINESE_PAGE_SIZE, CHINESE_CANDIDATE_COUNT - total_frozen),
                        )
                supplement = getattr(self, "_lexical_completion_fallback", None)
                if lexical_limit and callable(supplement) and not cancel.is_set():
                    candidates = supplement(
                        session,
                        limit=lexical_limit,
                        absolute_deadline=(self.clock() + _BACKGROUND_PAGE_DEADLINE_MS / 1000.0),
                    )
                    with self._state_lock:
                        if cancel.is_set() or self._sessions.get(candidate_set_id) is not session:
                            return
                        for candidate in candidates:
                            key = (candidate.text, candidate.consumed_keys)
                            if key in session.seen_candidates:
                                continue
                            session.seen_candidates.add(key)
                            session.pending.append(candidate)
                        self._sort_pending(session)

            with self._state_lock:
                if self._sessions.get(candidate_set_id) is not session:
                    return
                total_ready = sum(
                    len(page.candidates) for page in session.frozen_pages.values()
                ) + len(self._freezable_candidates(session))
                lexical_capacity_ready = (
                    self._uses_fixed_chinese_capacity(session)
                    and total_ready >= CHINESE_CANDIDATE_COUNT
                )
            if not lexical_capacity_ready:
                prepare = getattr(self, "_prepare_page_search", None)
                if callable(prepare) and not prepare(session, cancel):
                    return
            while not cancel.is_set():
                retiring_search_event: threading.Event | None = None
                freeze_ready_without_scorer = False
                with self._state_lock:
                    if self._sessions.get(candidate_set_id) is not session:
                        return
                    if candidate_set_id in self._background_searches:
                        background_cancel = self._background_cancel_events.get(candidate_set_id)
                        if background_cancel is None or not background_cancel.is_set():
                            return
                        if not lexical_capacity_ready:
                            # Page zero cancelled this scorer, but its finally
                            # block may run while this preparer is still
                            # registered. Returning here can lose both restart
                            # attempts and strand page one permanently.
                            retiring_search_event = self._background_search_events.get(
                                candidate_set_id
                            )
                            if retiring_search_event is None:
                                return
                        else:
                            # The cancelled scorer is no longer needed: all 35
                            # candidates are already freezable. The scored-layer
                            # query would wait for its retirement event for up to
                            # 2500 ms before freezing these model-free pages.
                            freeze_ready_without_scorer = True
                    latest_page_index = max(session.frozen_pages, default=-1)
                    latest_page = session.frozen_pages.get(latest_page_index)
                    if latest_page is None or not latest_page.has_more:
                        return
                    next_page_index = latest_page_index + 1
                    before = self._page_search_marker(session)

                if retiring_search_event is not None:
                    while not cancel.is_set() and not retiring_search_event.wait(0.05):
                        pass
                    continue

                try:
                    if freeze_ready_without_scorer:
                        with self._state_lock:
                            if self._sessions.get(candidate_set_id) is not session:
                                return
                            page = self._freeze_next_page(
                                session,
                                next_page_index,
                                CHINESE_PAGE_SIZE,
                                self.clock() + _BACKGROUND_PAGE_DEADLINE_MS / 1000.0,
                            )
                            self._record_metrics(page)
                    else:
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
                    timed_out = True
                    with self._state_lock:
                        if self._sessions.get(candidate_set_id) is not session:
                            return
                        progressed = self._page_search_marker(session) != before
                        retry_generation = self._background_retry_generations.pop(
                            candidate_set_id, None
                        )
                    if progressed:
                        continue
                    if retry_generation is None:
                        # A provider may finish its own bounded attempt with no
                        # result and no competing continuation generation. The
                        # next batch can still succeed (notably during cold
                        # GGUF startup), so keep the sole preparer alive with a
                        # cancellable backoff instead of stranding pagination.
                        if cancel.wait(_PAGE_PREPARATION_RETRY_BACKOFF_SECONDS):
                            return
                        continue
                    if not self._wait_for_continuation_idle(cancel, wake, retry_generation):
                        return
                    continue
                except CandidatePageError:
                    return

                if not page.has_more:
                    return
        finally:
            with self._state_lock:
                self._last_candidate_stage_metrics["last_candidate_background_elapsed_ms"] = max(
                    0.0, (self.clock() - preparation_started) * 1000.0
                )
                self._last_candidate_stage_metrics["last_candidate_background_timed_out"] = (
                    timed_out
                )
                if self._sessions.get(candidate_set_id) is session:
                    self._snapshot_candidate_stages_locked(session)
                self._page_preparations.discard(candidate_set_id)
                current_cancel = self._page_preparation_cancel_events.get(candidate_set_id)
                if current_cancel is cancel:
                    self._page_preparation_cancel_events.pop(candidate_set_id, None)
                current_wake = self._page_preparation_wake_events.get(candidate_set_id)
                if current_wake is wake:
                    self._page_preparation_wake_events.pop(candidate_set_id, None)
                done.set()


__all__ = ["NeuralCandidatePageManager"]
