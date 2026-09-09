from __future__ import annotations

import hashlib
import heapq
import math
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

import numpy as np

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .pinyin import parse_raw_pinyin
from .pinyin_partial import PartialPinyinMatcher
from .unified import LatinPrefixConstraint, detect_script

CHINESE_PAGE_SIZE = 7
LATIN_PAGE_SIZE = 5
MAX_ACTIVE_SEARCH_SESSIONS = 4
SEARCH_SESSION_IDLE_SECONDS = 15.0
MAX_FROZEN_CANDIDATES = 180
MAX_MODEL_TOKENS = 16
MAX_HAN_CHARACTERS = 64
MAX_FRONTIER_PER_BUCKET = 32
PAGE0_DEADLINE_MS = 35.0
NEXT_PAGE_DEADLINE_MS = 120.0
_MAX_ROOT_HAN_PLAN_CACHE = 256


class NeuralLanguageMode(StrEnum):
    CHINESE_FIRST = "chinese_first"
    LATIN_FIRST = "latin_first"


class CandidatePageError(ValueError):
    pass


class CandidatePageTimeout(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class CandidatePage:
    candidate_set_id: str
    page_index: int
    page_size: int
    has_more: bool
    candidates: tuple[Candidate, ...]
    candidate_ids: tuple[str, ...]
    score_source: str
    search_depth: int
    length_bucket: int | None
    elapsed_ms: float
    timeout_count: int


@dataclass(frozen=True, slots=True)
class _SearchIdentity:
    client_session_id: str
    composition_revision: int
    context_epoch: int
    context_session: str | None
    source_revision: int | None
    mode: NeuralLanguageMode
    raw_keys: str


@dataclass(frozen=True, slots=True)
class _SearchPath:
    text: str
    pinyin_path: tuple[str, ...]
    token_path: tuple[int, ...]
    score: float
    predicted_syllables: int
    script: str = "han"


@dataclass(frozen=True, slots=True)
class _RootHanPlanEntry:
    text: str
    pinyin: str
    normalized_text: str
    consumed_keys: int
    completes_input: bool
    syllables: int
    token_id: int
    predicted_syllables: int


@dataclass(slots=True)
class _SearchSession:
    candidate_set_id: str
    identity: _SearchIdentity
    score_source: str
    continuation_root: Any | None
    pending: list[Candidate]
    frontier: list[_SearchPath]
    frozen_pages: dict[int, CandidatePage]
    seen_candidates: set[tuple[str, int]]
    expanded_paths: set[tuple[int, ...]]
    last_used: float
    search_depth: int = 1
    timeout_count: int = 0
    exhausted: bool = False


def _candidate_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        not candidate.completes_input,
        candidate.predicted_syllables,
        -(candidate.model_score if candidate.model_score is not None else -math.inf),
        -candidate.consumed_keys,
        unicodedata.normalize("NFKC", candidate.text),
        candidate.token_path,
        candidate.pinyin,
    )


def _latin_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        -(candidate.model_score if candidate.model_score is not None else -math.inf),
        unicodedata.normalize("NFKC", candidate.text).casefold(),
        candidate.token_path,
    )


def _page_candidate_id(
    candidate_set_id: str,
    page_index: int,
    offset: int,
    candidate: Candidate,
) -> str:
    digest = hashlib.sha256()
    digest.update(candidate_set_id.encode("ascii"))
    digest.update(page_index.to_bytes(4, "little", signed=False))
    digest.update(offset.to_bytes(2, "little", signed=False))
    digest.update(str(candidate.consumed_keys).encode("ascii"))
    digest.update(b":")
    digest.update(",".join(str(token_id) for token_id in candidate.token_path).encode("ascii"))
    digest.update(b":")
    digest.update(candidate.text.encode("utf-8"))
    return digest.hexdigest()[:24]


def _literal_candidate(raw_keys: str, context_epoch: int) -> Candidate:
    return Candidate(
        text=raw_keys,
        pinyin="",
        consumed_keys=len(raw_keys),
        score=None,
        context_epoch=context_epoch,
        coverage=True,
        completes_input=True,
        syllables=0,
        constraint_kind="literal",
        script=detect_script(raw_keys),
        model_score=None,
        token_path=(),
        ranking_tier=1_000_000,
        predicted_syllables=0,
    )


class NeuralCandidatePageManager:
    """Revision-scoped pure-neural candidate paging over immutable root scores.

    Page 0 never performs a model forward. Every search session locks one score
    origin when it is created: either an accepted editor snapshot *and its exact
    continuation root*, or the permanent empty-context baseline and baseline
    root. A session never combines root scores from one origin with continuation
    scores from another. Once a page is returned it is never mutated.

    A Han page is frozen only from length buckets that are already proven closed:
    if an unexpanded frontier can still produce bucket ``k``, a bucket ``k`` or
    longer candidate stays pending. This preserves strict short-before-long order
    without making page 0 wait for continuation work.
    """

    def __init__(
        self,
        *,
        backend: ModelBackend,
        pinyin_index: Any | None,
        latin_constraint: LatinPrefixConstraint,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.matcher = PartialPinyinMatcher(pinyin_index) if pinyin_index is not None else None
        self.latin_constraint = latin_constraint
        self.clock = clock
        self._query_deadline = threading.local()
        self._baseline_scores: np.ndarray | None = None
        self._baseline_continuation_root: Any | None = None
        self._baseline_single_letter: dict[
            tuple[str, NeuralLanguageMode], tuple[Candidate, ...]
        ] = {}
        self._sessions: OrderedDict[str, _SearchSession] = OrderedDict()
        self._root_han_plans: OrderedDict[str, tuple[_RootHanPlanEntry, ...]] = OrderedDict()
        self._last_page_metrics: dict[str, int | float | None] = {
            "last_candidate_page_index": None,
            "last_candidate_count": None,
            "last_candidate_search_depth": None,
            "last_candidate_length_bucket": None,
            "last_candidate_search_elapsed_ms": None,
            "candidate_page_timeout_count": 0,
        }
        self._continuation_entries_by_token: dict[int, tuple[Any, ...]] = {}
        self._continuation_token_ids: tuple[int, ...] = ()
        if self.matcher is not None:
            grouped: dict[int, list[Any]] = {}
            for entry in self.matcher.entries:
                if entry.token_id is None or entry.coverage or len(entry.text) > MAX_HAN_CHARACTERS:
                    continue
                grouped.setdefault(int(entry.token_id), []).append(entry)
            self._continuation_entries_by_token = {
                token_id: tuple(entries) for token_id, entries in grouped.items()
            }
            self._continuation_token_ids = tuple(sorted(grouped))

    @property
    def baseline_ready(self) -> bool:
        return self._baseline_scores is not None

    def install_baseline_scores(
        self,
        scores: Sequence[float],
        *,
        continuation_root: Any | None = None,
    ) -> None:
        values = np.asarray(scores, dtype=np.float32).reshape(-1).copy()
        if values.size == 0 or np.isnan(values).any():
            raise ValueError("empty-context baseline scores must not contain NaN")
        values.flags.writeable = False
        self._baseline_scores = values
        self._baseline_continuation_root = continuation_root
        self._baseline_single_letter.clear()
        self.clear_sessions()

    def prewarm_single_letter_pages(self) -> None:
        if self._baseline_scores is None:
            raise RuntimeError("empty-context neural baseline is not ready")
        for raw in "abcdefghijklmnopqrstuvwxyz":
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

    def clear_sessions(self) -> None:
        self._sessions.clear()

    def diagnostics(self) -> dict[str, int | float | None]:
        return dict(self._last_page_metrics)

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
    ) -> CandidatePage:
        started = self.clock() if deadline_started is None else float(deadline_started)
        mode = NeuralLanguageMode(mode)
        page_size = (
            CHINESE_PAGE_SIZE if mode is NeuralLanguageMode.CHINESE_FIRST else LATIN_PAGE_SIZE
        )
        if page_index == 0:
            deadline_ms = (
                PAGE0_DEADLINE_MS
                if deadline_ms is None
                else min(PAGE0_DEADLINE_MS, float(deadline_ms))
            )
        else:
            deadline_ms = NEXT_PAGE_DEADLINE_MS if deadline_ms is None else float(deadline_ms)
        if deadline_ms <= 0:
            raise CandidatePageTimeout("candidate page deadline expired")
        if not raw_keys:
            raise CandidatePageError("raw_keys must be non-empty")
        if page_index < 0:
            raise CandidatePageError("page_index must be non-negative")
        if composition_revision < 0:
            raise CandidatePageError("composition_revision must be non-negative")

        absolute_deadline = started + deadline_ms / 1000.0
        self._raise_if_query_expired(absolute_deadline)
        self._expire_sessions()
        identity = _SearchIdentity(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=mode,
            raw_keys=raw_keys,
        )
        if page_index == 0:
            previous_deadline = getattr(self._query_deadline, "absolute", None)
            self._query_deadline.absolute = absolute_deadline
            try:
                session = self._new_session(identity, state)
                self._raise_if_query_expired(absolute_deadline)
            finally:
                if previous_deadline is None:
                    del self._query_deadline.absolute
                else:
                    self._query_deadline.absolute = previous_deadline
        else:
            if candidate_set_id is None:
                raise CandidatePageError("candidate_set_id is required after page 0")
            session = self._sessions.get(candidate_set_id)
            if session is None:
                raise CandidatePageError("candidate_set_id is unknown or expired")
            if session.identity != identity:
                raise CandidatePageError("candidate_set_id does not match the current composition")
            frozen = session.frozen_pages.get(page_index)
            if frozen is not None:
                session.last_used = self.clock()
                self._sessions.move_to_end(candidate_set_id)
                return frozen
            expected = max(session.frozen_pages, default=-1) + 1
            if page_index != expected:
                raise CandidatePageError(
                    "new candidate pages must be requested in increasing order"
                )

        self._raise_if_query_expired(absolute_deadline)
        page = self._freeze_next_page(session, page_index, page_size, absolute_deadline)
        elapsed_ms = max(0.0, (self.clock() - started) * 1000.0)
        if page.elapsed_ms != elapsed_ms:
            page = replace(page, elapsed_ms=elapsed_ms)
            session.frozen_pages[page_index] = page
        session.last_used = self.clock()
        self._sessions.move_to_end(session.candidate_set_id)
        self._record_metrics(page)
        return page

    def _raise_if_query_expired(self, absolute_deadline: float | None = None) -> None:
        deadline = (
            getattr(getattr(self, "_query_deadline", None), "absolute", None)
            if absolute_deadline is None
            else absolute_deadline
        )
        if deadline is not None and self.clock() >= deadline:
            raise CandidatePageTimeout("candidate page deadline expired")

    def _select_score_origin(
        self,
        state: BackendState | None,
    ) -> tuple[BackendState | None, Any | None, str]:
        continuation = getattr(self.backend, "continue_from_root", None)
        if not callable(continuation):
            # Root-only test/alternate backends can still rank one-token paths
            # from editor context because no continuation path can be produced.
            return state, None, "context" if state is not None else "baseline"
        if state is not None and state.continuation_root is not None:
            return state, state.continuation_root, "context"
        # A requested context epoch whose exact continuation root is absent must
        # fall back as a whole session. Never hybridize its root logits with the
        # permanent BOS continuation root.
        return None, self._baseline_continuation_root, "baseline"

    def _new_session(self, identity: _SearchIdentity, state: BackendState | None) -> _SearchSession:
        for candidate_set_id, old in tuple(self._sessions.items()):
            if (
                old.identity.client_session_id == identity.client_session_id
                and old.identity != identity
            ):
                del self._sessions[candidate_set_id]

        self._raise_if_query_expired()
        score_state, continuation_root, score_source = self._select_score_origin(state)
        self._raise_if_query_expired()
        root_candidates, frontier, _ = self._root_candidates(
            raw_keys=identity.raw_keys,
            mode=identity.mode,
            state=score_state,
            response_epoch=identity.context_epoch,
        )
        self._raise_if_query_expired()
        if identity.mode is NeuralLanguageMode.LATIN_FIRST:
            root_candidates = root_candidates[:LATIN_PAGE_SIZE]
            frontier = []
        candidate_set_id = uuid.uuid4().hex
        continuation = getattr(self.backend, "continue_from_root", None)
        session = _SearchSession(
            candidate_set_id=candidate_set_id,
            identity=identity,
            score_source=score_source,
            continuation_root=continuation_root,
            pending=list(root_candidates),
            frontier=frontier,
            frozen_pages={},
            seen_candidates={
                (unicodedata.normalize("NFKC", candidate.text), candidate.consumed_keys)
                for candidate in root_candidates
                if candidate.constraint_kind != "literal"
            },
            expanded_paths=set(),
            last_used=self.clock(),
            exhausted=(
                identity.mode is NeuralLanguageMode.LATIN_FIRST
                or not frontier
                or not callable(continuation)
                or continuation_root is None
            ),
        )
        self._sessions[candidate_set_id] = session
        self._sessions.move_to_end(candidate_set_id)
        while len(self._sessions) > MAX_ACTIVE_SEARCH_SESSIONS:
            self._sessions.popitem(last=False)
        return session

    def _root_candidates(
        self,
        *,
        raw_keys: str,
        mode: NeuralLanguageMode,
        state: BackendState | None,
        response_epoch: int,
        allow_prewarm_cache: bool = True,
    ) -> tuple[list[Candidate], list[_SearchPath], str]:
        self._raise_if_query_expired()
        cached = (
            self._baseline_single_letter.get((raw_keys, mode))
            if state is None and allow_prewarm_cache
            else None
        )
        if cached is not None:
            candidates = [replace(candidate, context_epoch=response_epoch) for candidate in cached]
            self._raise_if_query_expired()
            han = [candidate for candidate in candidates if candidate.script == "han"]
            return candidates, self._frontier_from_candidates(han), "baseline"

        han = self._root_han_candidates(raw_keys, state, response_epoch)
        self._raise_if_query_expired()
        latin = self._root_latin_candidates(raw_keys, state, response_epoch)
        self._raise_if_query_expired()
        score_source = "context" if state is not None else "baseline"

        if mode is NeuralLanguageMode.LATIN_FIRST:
            ordered = sorted(latin, key=_latin_key)
            return ordered, [], score_source

        ordered_han = sorted(han, key=_candidate_key)
        ordered_latin = sorted(latin, key=_latin_key)
        ordered = self._merge_chinese_first(ordered_han, ordered_latin)
        if not ordered and self._baseline_scores is not None:
            ordered = [_literal_candidate(raw_keys, response_epoch)]
        frontier = self._frontier_from_candidates(ordered_han)
        return ordered, frontier, score_source

    def _root_han_candidates(
        self,
        raw_keys: str,
        state: BackendState | None,
        response_epoch: int,
    ) -> list[Candidate]:
        self._raise_if_query_expired()
        plan = self._root_han_plan(raw_keys)
        self._raise_if_query_expired()
        if not plan:
            return []
        result = self._materialize_root_han_candidates(
            plan,
            state=state,
            response_epoch=response_epoch,
        )
        self._raise_if_query_expired()
        return result

    def _root_han_plan(self, raw_keys: str) -> tuple[_RootHanPlanEntry, ...]:
        cached = self._root_han_plans.pop(raw_keys, None)
        if cached is not None:
            self._root_han_plans[raw_keys] = cached
            return cached
        if self.matcher is None:
            return ()
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return ()
        raw = parsed.compact
        if not raw:
            return ()
        matches = [
            match
            for match in self.matcher.neural_matches(raw, 0)
            if match.next_position > 0
            and match.entry.token_id is not None
            and not match.entry.coverage
            and len(match.entry.text) <= MAX_HAN_CHARACTERS
        ]
        self._raise_if_query_expired()
        plan = tuple(
            _RootHanPlanEntry(
                text=match.entry.text,
                pinyin=match.entry.display_pinyin,
                normalized_text=match.entry.normalized_text,
                consumed_keys=parsed.raw_characters_for_letters(match.next_position),
                completes_input=match.next_position == len(raw),
                syllables=match.entry.syllables,
                token_id=int(match.entry.token_id),
                predicted_syllables=(
                    match.completion_syllables if match.next_position == len(raw) else 0
                ),
            )
            for match in matches
        )
        self._root_han_plans[raw_keys] = plan
        while len(self._root_han_plans) > _MAX_ROOT_HAN_PLAN_CACHE:
            self._root_han_plans.popitem(last=False)
        return plan

    def _materialize_root_han_candidates(
        self,
        plan: Sequence[_RootHanPlanEntry],
        *,
        state: BackendState | None,
        response_epoch: int,
        scores: Sequence[float] | None = None,
    ) -> list[Candidate]:
        if scores is None:
            token_ids = [entry.token_id for entry in plan]
            scores = self._score_root(state, token_ids)
        self._raise_if_query_expired()
        best: dict[
            tuple[str, int],
            tuple[tuple[object, ...], _RootHanPlanEntry, float],
        ] = {}
        for index, (entry, score) in enumerate(zip(plan, scores, strict=True)):
            if index % 64 == 0:
                self._raise_if_query_expired()
            if not math.isfinite(float(score)):
                continue
            value = float(score)
            sort_key = (
                not entry.completes_input,
                entry.predicted_syllables,
                -value,
                -entry.consumed_keys,
                entry.normalized_text,
                (entry.token_id,),
                entry.pinyin,
            )
            key = (entry.normalized_text, entry.consumed_keys)
            previous = best.get(key)
            if previous is None or sort_key < previous[0]:
                best[key] = (sort_key, entry, value)

        # A revision can freeze at most this many candidates across every page.
        # Retain the exact globally ordered reachable prefix, matching the
        # existing single-letter baseline cache contract, before constructing
        # heavyweight Candidate objects.
        selected = heapq.nsmallest(
            MAX_FROZEN_CANDIDATES,
            best.values(),
            key=lambda value: value[0],
        )
        self._raise_if_query_expired()
        return [
            Candidate(
                text=entry.text,
                pinyin=entry.pinyin,
                consumed_keys=entry.consumed_keys,
                score=score,
                context_epoch=response_epoch,
                coverage=False,
                completes_input=entry.completes_input,
                syllables=entry.syllables,
                token_id=entry.token_id,
                constraint_kind="pinyin",
                script="han",
                model_score=score,
                total_score=score,
                token_path=(entry.token_id,),
                predicted_syllables=entry.predicted_syllables,
            )
            for _, entry, score in selected
        ]

    def _root_latin_candidates(
        self,
        raw_keys: str,
        state: BackendState | None,
        response_epoch: int,
    ) -> list[Candidate]:
        self._raise_if_query_expired()
        compatible = [
            completion
            for completion in self.latin_constraint.completions
            if completion.token_path
            and completion.text.casefold().startswith(raw_keys.casefold())
            and len(completion.token_path) == 1
        ]
        if not compatible:
            return []
        token_ids = [int(completion.token_path[0]) for completion in compatible]
        scores = self._score_root(state, token_ids)
        self._raise_if_query_expired()
        candidates: list[Candidate] = []
        for index, (completion, score) in enumerate(zip(compatible, scores, strict=True)):
            if index % 64 == 0:
                self._raise_if_query_expired()
            if not math.isfinite(float(score)):
                continue
            value = float(score)
            candidates.append(
                Candidate(
                    text=completion.text,
                    pinyin="",
                    consumed_keys=len(raw_keys),
                    score=value,
                    context_epoch=response_epoch,
                    coverage=False,
                    completes_input=completion.text.casefold() == raw_keys.casefold(),
                    syllables=0,
                    token_id=int(completion.token_path[0]),
                    constraint_kind="latin_prefix",
                    script="latin",
                    model_score=value,
                    total_score=value,
                    token_path=(int(completion.token_path[0]),),
                    predicted_syllables=0,
                )
            )
        return candidates

    def _score_root(self, state: BackendState | None, token_ids: Sequence[int]) -> np.ndarray:
        if not token_ids:
            return np.empty(0, dtype=np.float32)
        if state is not None:
            try:
                return np.asarray(
                    self.backend.score_allowed_tokens(state, token_ids),
                    dtype=np.float32,
                )
            except (RuntimeError, ValueError, IndexError):
                # A session is created with a single score origin. If an editor
                # state becomes invalid here, do not silently fall through to a
                # different root. Make those candidates unavailable instead.
                return np.full(len(token_ids), -math.inf, dtype=np.float32)
        if self._baseline_scores is None:
            return np.full(len(token_ids), -math.inf, dtype=np.float32)
        ids = np.asarray(tuple(token_ids), dtype=np.int64)
        if ids.size and (int(ids.min()) < 0 or int(ids.max()) >= self._baseline_scores.size):
            return np.full(len(token_ids), -math.inf, dtype=np.float32)
        return np.asarray(self._baseline_scores[ids], dtype=np.float32)

    @staticmethod
    def _merge_chinese_first(han: list[Candidate], latin: list[Candidate]) -> list[Candidate]:
        if not han:
            return list(latin)
        # Preserve the strict Han short-bucket order. Latin may interleave by
        # neural score after item 1, but can never displace the first Han item.
        result = [han[0]]
        hi = 1
        li = 0
        while hi < len(han) or li < len(latin):
            if hi >= len(han):
                result.append(latin[li])
                li += 1
            elif li >= len(latin):
                result.append(han[hi])
                hi += 1
            else:
                han_score = han[hi].model_score if han[hi].model_score is not None else -math.inf
                latin_score = (
                    latin[li].model_score if latin[li].model_score is not None else -math.inf
                )
                if latin_score > han_score:
                    result.append(latin[li])
                    li += 1
                else:
                    result.append(han[hi])
                    hi += 1
        return result

    @staticmethod
    def _frontier_from_candidates(candidates: Sequence[Candidate]) -> list[_SearchPath]:
        frontier = []
        seen: set[tuple[int, ...]] = set()
        for candidate in sorted(candidates, key=_candidate_key):
            if (
                not candidate.completes_input
                or not candidate.token_path
                or candidate.token_path in seen
                or len(candidate.token_path) >= MAX_MODEL_TOKENS
                or len(candidate.text) >= MAX_HAN_CHARACTERS
            ):
                continue
            seen.add(candidate.token_path)
            frontier.append(
                _SearchPath(
                    text=candidate.text,
                    pinyin_path=tuple(part for part in candidate.pinyin.split("'") if part),
                    token_path=candidate.token_path,
                    score=float(candidate.model_score or 0.0),
                    predicted_syllables=candidate.predicted_syllables,
                    script="han",
                )
            )
        return frontier

    @staticmethod
    def _minimum_future_bucket(session: _SearchSession) -> int | None:
        buckets = [
            path.predicted_syllables + 1
            for path in session.frontier
            if path.script == "han" and path.token_path not in session.expanded_paths
        ]
        return min(buckets, default=None)

    def _freezable_candidates(self, session: _SearchSession) -> list[Candidate]:
        if session.exhausted:
            return list(session.pending)
        minimum_future = self._minimum_future_bucket(session)
        if minimum_future is None:
            return list(session.pending)
        return [
            candidate
            for candidate in session.pending
            if candidate.script != "han"
            or not candidate.completes_input
            or candidate.predicted_syllables < minimum_future
        ]

    def _freeze_next_page(
        self,
        session: _SearchSession,
        page_index: int,
        page_size: int,
        absolute_deadline: float,
    ) -> CandidatePage:
        if page_index == 0:
            selected = self._take_page0(session, page_size)
        else:
            self._ensure_freezable(session, page_size, absolute_deadline)
            freezable = self._freezable_candidates(session)
            if len(freezable) < page_size and not session.exhausted:
                session.timeout_count += 1
                self._last_page_metrics["candidate_page_timeout_count"] = (
                    int(self._last_page_metrics["candidate_page_timeout_count"] or 0) + 1
                )
                raise CandidatePageTimeout("candidate page search exceeded its absolute deadline")
            selected = freezable[:page_size]
            selected_set = set(selected)
            session.pending = [
                candidate for candidate in session.pending if candidate not in selected_set
            ]

        if not selected and page_index > 0 and session.exhausted:
            raise CandidatePageError("candidate search is exhausted")
        if not selected:
            selected = [
                _literal_candidate(
                    session.identity.raw_keys,
                    session.identity.context_epoch,
                )
            ]

        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        remaining_capacity = max(0, MAX_FROZEN_CANDIDATES - total_frozen)
        selected = selected[:remaining_capacity]
        if not selected:
            raise CandidatePageError("candidate set reached the frozen-candidate safety limit")

        has_more = bool(session.pending) or not session.exhausted
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

    def _take_page0(self, session: _SearchSession, page_size: int) -> list[Candidate]:
        freezable = self._freezable_candidates(session)
        if session.identity.mode is NeuralLanguageMode.LATIN_FIRST:
            chosen = freezable[:page_size]
            selected_set = set(chosen)
            session.pending = [
                candidate for candidate in session.pending if candidate not in selected_set
            ]
            return chosen

        han = [candidate for candidate in freezable if candidate.script == "han"]
        latin = [candidate for candidate in freezable if candidate.script == "latin"]
        literal = [candidate for candidate in freezable if candidate.constraint_kind == "literal"]
        if not han:
            combined = latin or literal
            chosen = combined[:page_size]
            selected_set = set(chosen)
            session.pending = [
                candidate for candidate in session.pending if candidate not in selected_set
            ]
            return chosen

        # Latin has its own explicit five-item mode. Keep one neural Latin
        # escape hatch on the Chinese homepage without allowing high-scoring
        # ASCII tokens to evict all but the first Han candidate.
        han_limit = page_size - 1 if latin else page_size
        chosen = [
            *han[:han_limit],
            *latin[: max(0, page_size - min(len(han), han_limit))],
        ]

        selected_set = set(chosen)
        session.pending = [
            candidate for candidate in session.pending if candidate not in selected_set
        ]
        return chosen

    def _ensure_freezable(
        self,
        session: _SearchSession,
        page_size: int,
        absolute_deadline: float,
    ) -> None:
        while not session.exhausted:
            if len(self._freezable_candidates(session)) >= page_size:
                return
            if self.clock() >= absolute_deadline:
                return
            produced = self._expand_one_frontier(session, absolute_deadline)
            session.pending.sort(key=_candidate_key)
            if produced == 0:
                return

    def _prune_frontier(self, session: _SearchSession) -> None:
        session.frontier.sort(
            key=lambda path: (
                path.predicted_syllables,
                -path.score,
                len(path.token_path),
                path.text,
                path.token_path,
            )
        )
        retained: list[_SearchPath] = []
        counts: dict[int, int] = {}
        for path in session.frontier:
            if path.token_path in session.expanded_paths:
                continue
            count = counts.get(path.predicted_syllables, 0)
            if count >= MAX_FRONTIER_PER_BUCKET:
                continue
            counts[path.predicted_syllables] = count + 1
            retained.append(path)
        session.frontier = retained

    def _expand_one_frontier(self, session: _SearchSession, absolute_deadline: float) -> int:
        if not session.frontier or not self._continuation_token_ids:
            session.exhausted = True
            return 0
        continuation = getattr(self.backend, "continue_from_root", None)
        if not callable(continuation) or session.continuation_root is None:
            session.exhausted = True
            return 0

        self._prune_frontier(session)
        parent = None
        while session.frontier:
            candidate = session.frontier.pop(0)
            if candidate.token_path not in session.expanded_paths:
                parent = candidate
                break
        if parent is None:
            session.exhausted = True
            return 0
        session.expanded_paths.add(parent.token_path)

        remaining_ms = max(0.0, (absolute_deadline - self.clock()) * 1000.0)
        if remaining_ms <= 0:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent.token_path)
            return 0
        scored = continuation(
            session.continuation_root,
            [parent.token_path],
            [self._continuation_token_ids],
            deadline_ms=remaining_ms,
        )
        if scored is None:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent.token_path)
            return 0
        if len(scored) != 1:
            raise RuntimeError("continuation scorer returned an invalid batch")
        values = np.asarray(scored[0], dtype=np.float32)
        if values.size != len(self._continuation_token_ids):
            raise RuntimeError("continuation scorer returned an invalid score vector")

        order = np.argsort(-values, kind="stable")
        produced = 0
        bucket_counts: dict[int, int] = {}
        for position in order:
            token_id = self._continuation_token_ids[int(position)]
            token_score = float(values[int(position)])
            if not math.isfinite(token_score):
                continue
            for entry in self._continuation_entries_by_token[token_id]:
                text = parent.text + entry.text
                token_path = (*parent.token_path, token_id)
                if len(text) > MAX_HAN_CHARACTERS or len(token_path) > MAX_MODEL_TOKENS:
                    continue
                predicted = parent.predicted_syllables + len(entry.syllable_path)
                bucket_counts[predicted] = bucket_counts.get(predicted, 0) + 1
                if bucket_counts[predicted] > MAX_FRONTIER_PER_BUCKET:
                    continue
                pinyin_path = (*parent.pinyin_path, *entry.syllable_path)
                score = parent.score + token_score
                key = (unicodedata.normalize("NFKC", text), len(session.identity.raw_keys))
                if key not in session.seen_candidates:
                    session.seen_candidates.add(key)
                    session.pending.append(
                        Candidate(
                            text=text,
                            pinyin="'".join(pinyin_path),
                            consumed_keys=len(session.identity.raw_keys),
                            score=score,
                            context_epoch=session.identity.context_epoch,
                            coverage=False,
                            completes_input=True,
                            syllables=len(pinyin_path),
                            token_id=token_path[0],
                            constraint_kind="pinyin",
                            script="han",
                            model_score=score,
                            total_score=score,
                            token_path=token_path,
                            predicted_syllables=predicted,
                        )
                    )
                    produced += 1
                if len(token_path) < MAX_MODEL_TOKENS and len(text) < MAX_HAN_CHARACTERS:
                    session.frontier.append(
                        _SearchPath(
                            text=text,
                            pinyin_path=pinyin_path,
                            token_path=token_path,
                            score=score,
                            predicted_syllables=predicted,
                            script="han",
                        )
                    )
            if produced >= MAX_FRONTIER_PER_BUCKET:
                break
            if self.clock() >= absolute_deadline:
                break
        session.search_depth = max(session.search_depth, len(parent.token_path) + 1)
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
        return produced

    def _expire_sessions(self) -> None:
        cutoff = self.clock() - SEARCH_SESSION_IDLE_SECONDS
        for candidate_set_id, session in tuple(self._sessions.items()):
            if session.last_used < cutoff:
                del self._sessions[candidate_set_id]

    def _record_metrics(self, page: CandidatePage) -> None:
        self._last_page_metrics.update(
            {
                "last_candidate_page_index": page.page_index,
                "last_candidate_count": len(page.candidates),
                "last_candidate_search_depth": page.search_depth,
                "last_candidate_length_bucket": page.length_bucket,
                "last_candidate_search_elapsed_ms": page.elapsed_ms,
                "candidate_page_timeout_count": int(
                    self._last_page_metrics["candidate_page_timeout_count"] or 0
                ),
            }
        )
