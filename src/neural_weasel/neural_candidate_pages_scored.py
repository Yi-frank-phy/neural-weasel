from __future__ import annotations

import math
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from .backends import BackendState
from .candidate import Candidate
from .neural_candidate_pages_v2 import _MAX_BASELINE_LATIN_CACHE
from .neural_candidate_pages_v3 import (
    NeuralCandidatePageManager as _V3CandidatePageManager,
)
from .neural_candidates import (
    MAX_FROZEN_CANDIDATES,
    NEXT_PAGE_DEADLINE_MS,
    CandidatePage,
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _candidate_key,
    _latin_key,
    _SearchIdentity,
    _SearchSession,
)

_MAX_BASELINE_HAN_CACHE = 512
_MAX_ASYNC_HAN_CACHE = 128
# A production Q4 continuation over 32 roots takes about 1.1--1.2 s on the
# target 4060.  Keep the total work bounded, but split it into short calls so
# a new composition never waits behind one monolithic CUDA replay.  Three
# eight-root batches reach the observed rank-20 ``mxbd`` root while each model
# lock hold remains in the measured ~0.3 s range.
# A production Q4 continuation over 32 roots takes about 1.1--1.2 s on the
# target 4060.  Allow two and a half seconds for progressive eight-root calls:
# this covers the measured rank-20 ``mxbd`` root even when the first replay
# pays the model's warm-up cost.  This is a daemon-only budget; the foreground
# native query remains guarded by its independent 50 ms deadline.
_BACKGROUND_CONTINUATION_DEADLINE_MS = 2500.0
_BACKGROUND_ROOT_BATCH_SIZE = 8


def _selected_log_probs(logits: Sequence[float], token_ids: Sequence[int]) -> np.ndarray:
    """Normalize over the complete model vocabulary, then select token ids."""

    values = np.asarray(logits, dtype=np.float64).reshape(-1)
    ids = np.asarray(tuple(token_ids), dtype=np.int64)
    if ids.ndim != 1:
        raise ValueError("token ids must be one-dimensional")
    if ids.size and (int(ids.min()) < 0 or int(ids.max()) >= values.size):
        raise IndexError("token id is outside the model vocabulary")
    if values.size == 0:
        return np.full(ids.size, -math.inf, dtype=np.float32)
    if np.isnan(values).any():
        raise ValueError("model logits must not contain NaN")

    positive_infinity = np.isposinf(values)
    if positive_infinity.any():
        output = np.full(ids.size, -math.inf, dtype=np.float32)
        output[positive_infinity[ids]] = np.float32(-math.log(int(positive_infinity.sum())))
        return output

    finite = np.isfinite(values)
    if not finite.any():
        return np.full(ids.size, -math.inf, dtype=np.float32)
    maximum = float(values[finite].max())
    log_normalizer = maximum + math.log(float(np.exp(values[finite] - maximum).sum()))
    return np.asarray(values[ids] - log_normalizer, dtype=np.float32)


class NeuralCandidatePageManager(_V3CandidatePageManager):
    """Complete the PR36 model-score, cache and concurrency contracts.

    The constrained beam defines a path score as the sum of normalized
    next-token log probabilities, with no token-count division. Root logits and
    every continuation step here use that same definition. Strict Han length
    buckets remain the primary ordering key; model probability only orders paths
    inside a proven bucket.

    Candidate state is shared by up to four named-pipe client threads. A short
    manager lock protects sessions, frozen pages, baseline caches and metrics,
    but the lock is deliberately released around the potentially long CUDA
    continuation call. Page 0 therefore never queues behind an uninterruptible
    later-page decode. If a focus/new-revision boundary invalidates the session
    while CUDA work is in flight, that late result is discarded before it can
    mutate caches or freeze a page.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_model_token_ids: tuple[int, ...] = ()
        self._baseline_han_cache: OrderedDict[tuple[str, str, tuple[int, ...]], Candidate] = (
            OrderedDict()
        )
        self._dirty_single_letter_prewarms: set[str] = set()
        self._state_lock = threading.RLock()
        self._active_searches: set[str] = set()
        self._background_searches: set[str] = set()
        self._background_search_events: dict[str, threading.Event] = {}
        self._async_han_cache: OrderedDict[
            tuple[int, str | None, int | None, str, str], tuple[Candidate, ...]
        ] = OrderedDict()
        self._building_identity: _SearchIdentity | None = None

    def install_baseline_scores(
        self,
        scores: Sequence[float],
        *,
        continuation_root: Any | None = None,
    ) -> None:
        with self._state_lock:
            values = np.asarray(scores).reshape(-1)
            self._all_model_token_ids = tuple(range(int(values.size)))
            self._baseline_han_cache.clear()
            self._async_han_cache.clear()
            self._dirty_single_letter_prewarms.clear()
            super().install_baseline_scores(scores, continuation_root=continuation_root)

    def prewarm_single_letter_pages(self) -> None:
        with self._state_lock:
            super().prewarm_single_letter_pages()

    def clear_sessions(self) -> None:
        with self._state_lock:
            for event in self._background_search_events.values():
                event.set()
            self._sessions.clear()
            self._active_searches.clear()
            self._background_searches.clear()
            self._background_search_events.clear()
            self._async_han_cache.clear()

    def diagnostics(self) -> dict[str, int | float | None]:
        with self._state_lock:
            return super().diagnostics()

    def _record_retryable_timeout(self, session: _SearchSession) -> None:
        session.timeout_count += 1
        self._last_page_metrics["candidate_page_timeout_count"] = (
            int(self._last_page_metrics["candidate_page_timeout_count"] or 0) + 1
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
    ) -> CandidatePage:
        """Serve frozen state quickly while later-page CUDA work runs unlocked."""

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
        wait_event: threading.Event | None = None
        wait_budget_ms = NEXT_PAGE_DEADLINE_MS if deadline_ms is None else float(deadline_ms)
        wait_started = time.monotonic()
        with self._state_lock:
            if page_index == 0:
                self._expire_sessions()
                for existing_set_id, session in tuple(self._sessions.items()):
                    if session.identity != identity:
                        continue
                    frozen = session.frozen_pages.get(0)
                    if frozen is None:
                        continue
                    session.last_used = self.clock()
                    self._sessions.move_to_end(existing_set_id)
                    self._record_metrics(frozen)
                    return frozen
            elif candidate_set_id is not None and (
                candidate_set_id in self._background_searches
                or candidate_set_id in self._active_searches
            ):
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
                if candidate_set_id in self._background_searches:
                    wait_event = self._background_search_events.get(candidate_set_id)
                if wait_event is not None:
                    pass
                else:
                    self._record_retryable_timeout(session)
                    raise CandidatePageTimeout("candidate page search is already in progress")

            if wait_event is None:
                page = super().query_page(
                    client_session_id=client_session_id,
                    composition_revision=composition_revision,
                    context_epoch=context_epoch,
                    context_session=context_session,
                    source_revision=source_revision,
                    mode=normalized_mode,
                    raw_keys=raw_keys,
                    page_index=page_index,
                    candidate_set_id=candidate_set_id,
                    state=state,
                    deadline_ms=deadline_ms,
                )
                if page_index == 0:
                    session = self._sessions.get(page.candidate_set_id)
                    if session is not None:
                        self._start_background_continuation(session)
                return page

        if wait_event is None:
            raise CandidatePageTimeout("candidate page search coordination failed")
        if wait_budget_ms <= 0 or not wait_event.wait(wait_budget_ms / 1000.0):
            with self._state_lock:
                session = self._sessions.get(candidate_set_id or "")
                if session is not None:
                    self._record_retryable_timeout(session)
            raise CandidatePageTimeout("candidate page search is already in progress")
        remaining_ms = wait_budget_ms - (time.monotonic() - wait_started) * 1000.0
        if remaining_ms <= 0:
            raise CandidatePageTimeout("candidate page deadline expired")
        return self.query_page(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=normalized_mode,
            raw_keys=raw_keys,
            page_index=page_index,
            candidate_set_id=candidate_set_id,
            state=state,
            deadline_ms=remaining_ms,
        )

    @staticmethod
    def _async_identity_key(
        identity: _SearchIdentity,
    ) -> tuple[int, str | None, int | None, str, str]:
        return (
            identity.context_epoch,
            identity.context_session,
            identity.source_revision,
            identity.mode.value,
            identity.raw_keys.casefold(),
        )

    def _new_session(
        self,
        identity: _SearchIdentity,
        state: BackendState | None,
    ) -> _SearchSession:
        self._building_identity = identity
        try:
            return super()._new_session(identity, state)
        finally:
            self._building_identity = None

    def _start_background_continuation(self, session: _SearchSession) -> None:
        candidate_set_id = session.candidate_set_id
        identity_key = self._async_identity_key(session.identity)
        if (
            session.identity.mode is NeuralLanguageMode.LATIN_FIRST
            or candidate_set_id in self._background_searches
            or identity_key in self._async_han_cache
            or session.exhausted
            or not any(
                path.script == "han"
                and int(getattr(path, "matched_letters", len(session.identity.raw_keys)))
                < len(session.identity.raw_keys.replace("'", ""))
                for path in session.frontier
            )
        ):
            return
        self._background_searches.add(candidate_set_id)
        self._background_search_events[candidate_set_id] = threading.Event()
        worker = threading.Thread(
            target=self._run_background_continuation,
            args=(session, identity_key),
            name=f"neural-page-{candidate_set_id[:8]}",
            daemon=True,
        )
        worker.start()

    def _run_background_continuation(
        self,
        session: _SearchSession,
        identity_key: tuple[int, str | None, int | None, str, str],
    ) -> None:
        with self._state_lock:
            candidate_set_id = session.candidate_set_id
            try:
                if self._sessions.get(candidate_set_id) is not session:
                    return
                before = {
                    (unicodedata.normalize("NFKC", candidate.text), candidate.token_path)
                    for candidate in session.pending
                }
                deadline = self.clock() + _BACKGROUND_CONTINUATION_DEADLINE_MS / 1000.0
                # Advance in bounded batches.  The frontier is re-ranked after
                # every batch, so lower-scoring partial roots remain reachable
                # without making any one foreground context update wait for a
                # full 32-root CUDA replay.
                while self.clock() < deadline:
                    if self._sessions.get(candidate_set_id) is not session:
                        return
                    progressed = self._expand_background_frontier_batch(
                        session,
                        deadline,
                        max_parents=_BACKGROUND_ROOT_BATCH_SIZE,
                    )
                    if progressed <= 0:
                        break
                if self._sessions.get(candidate_set_id) is not session:
                    return
                completed = tuple(
                    candidate
                    for candidate in session.pending
                    if candidate.script == "han"
                    and candidate.completes_input
                    and len(candidate.token_path) > 1
                    and (
                        unicodedata.normalize("NFKC", candidate.text),
                        candidate.token_path,
                    )
                    not in before
                )
                if not completed:
                    return
                self._async_han_cache[identity_key] = completed
                self._async_han_cache.move_to_end(identity_key)
                while len(self._async_han_cache) > _MAX_ASYNC_HAN_CACHE:
                    self._async_han_cache.popitem(last=False)
            except (CandidatePageError, CandidatePageTimeout, RuntimeError, ValueError):
                return
            finally:
                self._background_searches.discard(candidate_set_id)
                event = self._background_search_events.pop(candidate_set_id, None)
                if event is not None:
                    event.set()

    def _score_root(
        self,
        state: BackendState | None,
        token_ids: Sequence[int],
    ) -> np.ndarray:
        if not token_ids:
            return np.empty(0, dtype=np.float32)
        if state is None:
            if self._baseline_scores is None:
                return np.full(len(token_ids), -math.inf, dtype=np.float32)
            try:
                return _selected_log_probs(self._baseline_scores, token_ids)
            except (ValueError, IndexError):
                return np.full(len(token_ids), -math.inf, dtype=np.float32)

        try:
            # Keep backend ownership/generation validation on the context state.
            self.backend.score_allowed_tokens(state, token_ids)
            payload = np.asarray(state.payload)
            if payload.ndim != 1:
                raise ValueError("candidate paging requires a full-vocabulary root")
            return _selected_log_probs(payload, token_ids)
        except (RuntimeError, ValueError, IndexError, TypeError):
            # Do not silently switch score origin inside a revision.
            return np.full(len(token_ids), -math.inf, dtype=np.float32)

    def _root_latin_candidates_and_frontier(
        self,
        raw_keys: str,
        state: BackendState | None,
        response_epoch: int,
    ) -> tuple[list[Candidate], list[Any]]:
        candidates, frontier = super()._root_latin_candidates_and_frontier(
            raw_keys,
            state,
            response_epoch,
        )
        raw_folded = raw_keys.casefold()
        consumed = len(raw_keys)
        return (
            [
                replace(
                    candidate,
                    consumed_keys=consumed,
                    completes_input=candidate.text.casefold() == raw_folded,
                )
                for candidate in candidates
            ],
            frontier,
        )

    def _mark_single_letter_prewarm_dirty(self, raw: str) -> None:
        folded = raw.casefold()
        if len(folded) == 1 and "a" <= folded <= "z":
            self._dirty_single_letter_prewarms.add(folded)

    def _refresh_dirty_single_letter_prewarms(self) -> None:
        if not self._dirty_single_letter_prewarms or self._baseline_scores is None:
            return
        dirty = tuple(sorted(self._dirty_single_letter_prewarms))
        self._dirty_single_letter_prewarms.clear()
        for raw in dirty:
            for mode in NeuralLanguageMode:
                candidates, _, _ = self._root_candidates(
                    raw_keys=raw,
                    mode=mode,
                    state=None,
                    response_epoch=0,
                    allow_prewarm_cache=False,
                )
                self._baseline_single_letter[(raw, mode)] = tuple(
                    candidates[:MAX_FROZEN_CANDIDATES]
                )

    def _remember_baseline_latin_candidate(self, candidate: Candidate) -> None:
        normalized = unicodedata.normalize("NFKC", candidate.text).casefold()
        key = (normalized, candidate.token_path)
        previous = self._baseline_latin_cache.get(key)
        changed = previous is None or _latin_key(candidate) < _latin_key(previous)
        if changed:
            self._baseline_latin_cache[key] = replace(candidate, context_epoch=0)
            self._baseline_latin_cache.move_to_end(key)
            if normalized:
                self._mark_single_letter_prewarm_dirty(normalized[0])
        while len(self._baseline_latin_cache) > _MAX_BASELINE_LATIN_CACHE:
            _, evicted = self._baseline_latin_cache.popitem(last=False)
            evicted_text = unicodedata.normalize("NFKC", evicted.text).casefold()
            if evicted_text:
                self._mark_single_letter_prewarm_dirty(evicted_text[0])

    @staticmethod
    def _han_cache_key(
        raw_keys: str,
        candidate: Candidate,
    ) -> tuple[str, str, tuple[int, ...]]:
        return (
            raw_keys.casefold(),
            unicodedata.normalize("NFKC", candidate.text),
            candidate.token_path,
        )

    def _remember_baseline_han_candidate(
        self,
        raw_keys: str,
        candidate: Candidate,
    ) -> None:
        if candidate.script != "han" or len(candidate.token_path) <= 1:
            return
        key = self._han_cache_key(raw_keys, candidate)
        previous = self._baseline_han_cache.get(key)
        if previous is not None and _candidate_key(previous) <= _candidate_key(candidate):
            return
        self._baseline_han_cache[key] = replace(candidate, context_epoch=0)
        self._baseline_han_cache.move_to_end(key)

        normalized_raw = raw_keys.casefold()
        self._mark_single_letter_prewarm_dirty(normalized_raw)
        while len(self._baseline_han_cache) > _MAX_BASELINE_HAN_CACHE:
            (evicted_raw, _, _), _ = self._baseline_han_cache.popitem(last=False)
            self._mark_single_letter_prewarm_dirty(evicted_raw)

    def _cached_han_for_raw(
        self,
        raw_keys: str,
        response_epoch: int,
    ) -> list[Candidate]:
        raw = raw_keys.casefold()
        return [
            replace(candidate, context_epoch=response_epoch)
            for (cached_raw, _, _), candidate in self._baseline_han_cache.items()
            if cached_raw == raw
        ]

    def _root_candidates(
        self,
        *,
        raw_keys: str,
        mode: NeuralLanguageMode,
        state: BackendState | None,
        response_epoch: int,
        allow_prewarm_cache: bool = True,
    ) -> tuple[list[Candidate], list[Any], str]:
        candidates, frontier, score_source = super()._root_candidates(
            raw_keys=raw_keys,
            mode=mode,
            state=state,
            response_epoch=response_epoch,
            allow_prewarm_cache=allow_prewarm_cache,
        )
        async_han: list[Candidate] = []
        if self._building_identity is not None:
            cached = self._async_han_cache.get(self._async_identity_key(self._building_identity))
            if cached is not None:
                async_han = [
                    replace(candidate, context_epoch=response_epoch) for candidate in cached
                ]
        if mode is NeuralLanguageMode.LATIN_FIRST:
            return candidates, frontier, score_source

        cached_han = [*async_han]
        if state is None:
            cached_han.extend(self._cached_han_for_raw(raw_keys, response_epoch))
        if not cached_han:
            return candidates, frontier, score_source

        han = [candidate for candidate in candidates if candidate.script == "han"]
        latin = [candidate for candidate in candidates if candidate.script == "latin"]
        other = [
            candidate
            for candidate in candidates
            if candidate.script not in {"han", "latin"} and candidate.constraint_kind != "literal"
        ]
        best: dict[tuple[str, int], Candidate] = {}
        for candidate in (*han, *cached_han):
            key = (
                unicodedata.normalize("NFKC", candidate.text),
                candidate.consumed_keys,
            )
            previous = best.get(key)
            if previous is None or _candidate_key(candidate) < _candidate_key(previous):
                best[key] = candidate
        ordered_han = sorted(best.values(), key=_candidate_key)
        ordered = [
            *self._merge_chinese_first(ordered_han, sorted(latin, key=_latin_key)),
            *other,
        ]

        seen_paths = {self._path_key(path) for path in frontier}
        for path in self._han_frontier_from_candidates(raw_keys, cached_han):
            key = self._path_key(path)
            if key not in seen_paths:
                frontier.append(path)
                seen_paths.add(key)
        return ordered[:MAX_FROZEN_CANDIDATES], frontier, score_source

    @staticmethod
    def _rollback_parent(
        session: _SearchSession,
        parent: Any,
        parent_key: tuple[object, ...],
    ) -> None:
        session.frontier.insert(0, parent)
        session.expanded_paths.discard(parent_key)

    def _expand_background_frontier_batch(
        self,
        session: _SearchSession,
        absolute_deadline: float,
        *,
        max_parents: int,
    ) -> int:
        """Score several high-consumption Han roots in one backend call.

        Production shorthand input can leave dozens of legal one-token roots.
        Expanding only the highest root makes a lower-scoring partial phrase
        unreachable even when its next model token is the best complete path.
        Background publication therefore explores a bounded set of roots at
        once; the backend may execute that request in smaller GPU batches.
        """

        continuation = getattr(self.backend, "continue_from_root", None)
        if (
            max_parents <= 0
            or not callable(continuation)
            or session.continuation_root is None
            or not self._all_model_token_ids
        ):
            return 0

        self._prune_frontier(session)
        compact_length = len(session.identity.raw_keys.replace("'", ""))
        eligible = sorted(
            (
                path
                for path in session.frontier
                if path.script == "han"
                and self._path_key(path) not in session.expanded_paths
                and int(getattr(path, "matched_letters", compact_length)) < compact_length
            ),
            key=lambda path: (
                -int(getattr(path, "matched_letters", 0)),
                path.predicted_syllables,
                -path.score,
                path.text,
                path.token_path,
            ),
        )

        selected: list[
            tuple[Any, tuple[object, ...], tuple[int, ...], dict[int, tuple[Any, ...]]]
        ] = []
        # ``_han_edges_for`` walks the pinyin trie for a given suffix start.
        # Every root with the same matched/predicted bucket has the same edge
        # set; computing it once avoids doing that work once per competing
        # shorthand root while the manager lock is held.
        edge_cache: dict[tuple[int, int], dict[int, tuple[Any, ...]]] = {}
        for parent in eligible:
            edge_key = (
                int(getattr(parent, "matched_letters", 0)),
                int(getattr(parent, "predicted_syllables", 0)),
            )
            if edge_key not in edge_cache:
                edge_cache[edge_key] = self._han_edges_for(session, parent)
            han_edges = edge_cache[edge_key]
            legal_token_ids = tuple(sorted(han_edges))
            if not legal_token_ids:
                continue
            selected.append((parent, self._path_key(parent), legal_token_ids, han_edges))
            if len(selected) >= max_parents:
                break
        if not selected:
            return 0

        selected_keys = {item[1] for item in selected}
        session.frontier = [
            path for path in session.frontier if self._path_key(path) not in selected_keys
        ]
        session.expanded_paths.update(selected_keys)

        def rollback() -> None:
            session.frontier = [*(item[0] for item in selected), *session.frontier]
            session.expanded_paths.difference_update(selected_keys)

        remaining_ms = max(0.0, (absolute_deadline - self.clock()) * 1000.0)
        if remaining_ms <= 0:
            rollback()
            return 0

        candidate_set_id = session.candidate_set_id
        self._active_searches.add(candidate_set_id)
        failure: Exception | None = None
        normalized: list[np.ndarray] | None = None

        self._state_lock.release()
        try:
            try:
                scored = continuation(
                    session.continuation_root,
                    [item[0].token_path for item in selected],
                    [self._all_model_token_ids] * len(selected),
                    deadline_ms=remaining_ms,
                )
                if scored is not None:
                    if len(scored) != len(selected):
                        raise RuntimeError("continuation scorer returned an invalid batch")
                    normalized = []
                    for values, item in zip(scored, selected, strict=True):
                        full_logits = np.asarray(values, dtype=np.float32)
                        if full_logits.size != len(self._all_model_token_ids):
                            raise RuntimeError(
                                "continuation scorer returned an invalid full-vocabulary vector"
                            )
                        normalized.append(_selected_log_probs(full_logits, item[2]))
            except Exception as error:
                failure = error
        finally:
            self._state_lock.acquire()
            self._active_searches.discard(candidate_set_id)

        if self._sessions.get(candidate_set_id) is not session:
            raise CandidatePageError("candidate set was invalidated during search")
        if failure is not None:
            rollback()
            raise failure
        if normalized is None:
            rollback()
            return 0

        progressed = 0
        for (parent, _, legal_token_ids, han_edges), values in zip(
            selected, normalized, strict=True
        ):
            before_pending = len(session.pending)
            progressed += self._expand_han_constrained(
                session,
                parent,
                legal_token_ids,
                values,
                han_edges,
                absolute_deadline,
            )
            if session.score_source == "baseline":
                for candidate in session.pending[before_pending:]:
                    self._remember_baseline_han_candidate(
                        session.identity.raw_keys,
                        candidate,
                    )
            session.search_depth = max(session.search_depth, len(parent.token_path) + 1)

        self._refresh_dirty_single_letter_prewarms()
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
        return progressed

    def _expand_one_frontier(
        self,
        session: _SearchSession,
        absolute_deadline: float,
    ) -> int:
        if not session.frontier:
            session.exhausted = True
            return 0
        continuation = getattr(self.backend, "continue_from_root", None)
        if (
            not callable(continuation)
            or session.continuation_root is None
            or not self._all_model_token_ids
        ):
            session.exhausted = True
            return 0

        self._prune_frontier(session)
        parent: Any | None = None
        legal_token_ids: tuple[int, ...] = ()
        han_edges: dict[int, tuple[Any, ...]] = {}
        parent_key: tuple[object, ...] | None = None
        while session.frontier:
            candidate = session.frontier.pop(0)
            key = self._path_key(candidate)
            if key in session.expanded_paths:
                continue
            if candidate.script == "han":
                han_edges = self._han_edges_for(session, candidate)
                allowed = tuple(sorted(han_edges))
            else:
                allowed = self._latin_continuation_token_ids
            session.expanded_paths.add(key)
            if not allowed:
                continue
            parent = candidate
            parent_key = key
            legal_token_ids = allowed
            break
        if parent is None or parent_key is None:
            session.exhausted = True
            return 0

        remaining_ms = max(0.0, (absolute_deadline - self.clock()) * 1000.0)
        if remaining_ms <= 0:
            self._rollback_parent(session, parent, parent_key)
            return 0

        candidate_set_id = session.candidate_set_id
        self._active_searches.add(candidate_set_id)
        failure: Exception | None = None
        scored: list[np.ndarray] | None = None

        # The state lock protects Python paging state, not model execution. Drop
        # it while CUDA runs so an unrelated page 0 or a focus boundary can be
        # served immediately on another named-pipe client thread.
        self._state_lock.release()
        try:
            try:
                scored = continuation(
                    session.continuation_root,
                    [parent.token_path],
                    [self._all_model_token_ids],
                    deadline_ms=remaining_ms,
                )
            except Exception as error:
                failure = error
        finally:
            self._state_lock.acquire()
            self._active_searches.discard(candidate_set_id)

        # A new composition/focus may have removed this object while CUDA was
        # still executing. In that case the late result is stale by definition.
        if self._sessions.get(candidate_set_id) is not session:
            raise CandidatePageError("candidate set was invalidated during search")
        if failure is not None:
            self._rollback_parent(session, parent, parent_key)
            raise failure
        if scored is None:
            self._rollback_parent(session, parent, parent_key)
            return 0
        if len(scored) != 1:
            self._rollback_parent(session, parent, parent_key)
            raise RuntimeError("continuation scorer returned an invalid batch")
        full_logits = np.asarray(scored[0], dtype=np.float32)
        if full_logits.size != len(self._all_model_token_ids):
            self._rollback_parent(session, parent, parent_key)
            raise RuntimeError("continuation scorer returned an invalid full-vocabulary vector")
        values = _selected_log_probs(full_logits, legal_token_ids)

        before_pending = len(session.pending)
        if parent.script == "han":
            progressed = self._expand_han_constrained(
                session,
                parent,
                legal_token_ids,
                values,
                han_edges,
                absolute_deadline,
            )
        else:
            progressed = self._expand_latin(
                session,
                parent,
                legal_token_ids,
                values,
                absolute_deadline,
            )

        if session.score_source == "baseline" and parent.script == "han":
            for candidate in session.pending[before_pending:]:
                self._remember_baseline_han_candidate(
                    session.identity.raw_keys,
                    candidate,
                )
        self._refresh_dirty_single_letter_prewarms()

        session.search_depth = max(session.search_depth, len(parent.token_path) + 1)
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
        return progressed


__all__ = ["NeuralCandidatePageManager", "_selected_log_probs"]
