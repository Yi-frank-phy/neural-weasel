from __future__ import annotations

import heapq
import math
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .backends import BackendState
from .candidate import Candidate
from .neural_candidate_pages_v2 import NeuralCandidatePageManager as _V2CandidatePageManager
from .neural_candidates import (
    _MAX_ROOT_HAN_PLAN_CACHE,
    CHINESE_PAGE_SIZE,
    MAX_ACTIVE_SEARCH_SESSIONS,
    MAX_FRONTIER_PER_BUCKET,
    MAX_HAN_CHARACTERS,
    MAX_MODEL_TOKENS,
    NeuralLanguageMode,
    _candidate_key,
    _latin_key,
    _literal_candidate,
    _RootHanPlanEntry,
    _SearchIdentity,
    _SearchSession,
    _single_initial_static_ranks,
)
from .pinyin import parse_raw_pinyin

_MAX_BASELINE_ROOT_PAGE_CACHE = 128


def _stable_bounded_score_positions(
    values: Sequence[float],
    *,
    initial_limit: int = MAX_FRONTIER_PER_BUCKET,
):
    """Yield finite score positions in stable descending order by bounded prefixes."""

    scores = np.asarray(values, dtype=np.float64).reshape(-1)
    finite_positions = np.flatnonzero(np.isfinite(scores))
    total = int(finite_positions.size)
    if total == 0:
        return
    target = min(total, max(1, int(initial_limit)))
    yielded: set[int] = set()
    while True:
        if target >= total:
            selected = finite_positions
        else:
            finite_scores = scores[finite_positions]
            partition = np.argpartition(finite_scores, total - target)[total - target :]
            cutoff = float(finite_scores[partition].min())
            selected = finite_positions[finite_scores >= cutoff]
        ordered = sorted(
            (int(position) for position in selected),
            key=lambda position: (-float(scores[position]), position),
        )
        for position in ordered:
            if position not in yielded:
                yielded.add(position)
                yield position
        if len(yielded) >= total:
            return
        target = min(total, max(target * 2, len(yielded) + 1))


@dataclass(frozen=True, slots=True)
class _HanSearchPath:
    text: str
    pinyin_path: tuple[str, ...]
    token_path: tuple[int, ...]
    score: float
    predicted_syllables: int
    matched_letters: int
    script: str = "han"
    pending_options: tuple[_HanEdge, ...] = ()
    pending_start: int = 0


@dataclass(frozen=True, slots=True)
class _HanEdge:
    entry: Any
    matched_letters: int
    predicted_syllables: int


@dataclass(frozen=True, slots=True)
class _DeferredHanFrontier:
    """Immutable root scores whose bounded search seeds are built off page 0."""

    raw_keys: str
    plan: tuple[_RootHanPlanEntry, ...]
    scores: np.ndarray
    script: str = "han"
    matched_letters: int = 0
    predicted_syllables: int = 0
    token_path: tuple[int, ...] = ()
    pinyin_path: tuple[str, ...] = ()
    text: str = ""
    score: float = -math.inf


@dataclass(frozen=True, slots=True)
class _ScoredHanExpansion:
    """Unvisited legal edges after a bounded CPU slice of an already scored parent."""

    parent: _HanSearchPath
    token_ids: tuple[int, ...]
    values: np.ndarray
    edges_by_token: dict[int, tuple[_HanEdge, ...]]
    positions: tuple[int, ...]
    cursor: int
    script: str = "han_resume"
    scored_han_resume: bool = True

    @property
    def predicted_syllables(self) -> int:
        return self.parent.predicted_syllables

    @property
    def pending_options(self) -> tuple[_HanEdge, ...]:
        return self.parent.pending_options

    @property
    def token_path(self) -> tuple[int, ...]:
        return self.parent.token_path

    @property
    def pinyin_path(self) -> tuple[str, ...]:
        return self.parent.pinyin_path

    @property
    def matched_letters(self) -> int:
        return self.parent.matched_letters

    @property
    def text(self) -> str:
        return self.parent.text

    @property
    def score(self) -> float:
        return self.parent.score


@dataclass(frozen=True, slots=True)
class _DeferredRootSeeds:
    records: tuple[tuple[object, ...], ...]
    cursor: int
    script: str = "han_root_resume"
    root_seed_resume: bool = True
    predicted_syllables: int = 0
    token_path: tuple[int, ...] = ()
    pinyin_path: tuple[str, ...] = ()
    matched_letters: int = 0
    text: str = ""
    score: float = -math.inf


@dataclass(frozen=True, slots=True)
class _RootHanSelectionOrder:
    ranking_tier: np.ndarray
    incomplete: np.ndarray
    predicted_syllables: np.ndarray
    static_rank: np.ndarray
    static_tie_rank: np.ndarray


class NeuralCandidatePageManager(_V2CandidatePageManager):
    """PR36 pager with pinyin-position-aware multi-token Han continuation.

    A Han frontier remembers how much of the typed compact pinyin has already
    been consumed. Until that position reaches the end of the typed input, the
    next Base-model token is restricted to pronunciation edges returned by the
    pinyin matcher at that exact position. Only after the typed input is fully
    covered may the same search session enter free predictive Han continuation.

    This fixes the important case where an exact candidate exists only as more
    than one model token, for example ``nihao`` -> ``你`` + ``好`` when no
    one-token ``你好`` entry exists in the model vocabulary.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._baseline_root_pages: OrderedDict[
            tuple[str, NeuralLanguageMode], tuple[tuple[Candidate, ...], tuple[Any, ...]]
        ] = OrderedDict()
        self._root_han_selection_orders: OrderedDict[str, _RootHanSelectionOrder] = OrderedDict()
        self._lexical_completion_cursors: dict[str, Iterator[Candidate | None]] = {}
        self._lexical_completion_exhausted: set[str] = set()
        grouped: dict[int, list[Any]] = {}
        if self.matcher is not None:
            for entry in self.matcher.entries:
                if 1 < len(entry.token_path) <= MAX_MODEL_TOKENS:
                    grouped.setdefault(entry.token_path[0], []).append(entry)
        self._multitoken_entries_by_first = {
            token_id: tuple(entries) for token_id, entries in grouped.items()
        }

    def _sort_pending(self, session: _SearchSession) -> None:
        if session.identity.mode is not NeuralLanguageMode.CHINESE_FIRST:
            super()._sort_pending(session)
            return
        try:
            compact = parse_raw_pinyin(session.identity.raw_keys).compact
        except ValueError:
            super()._sort_pending(session)
            return
        # A complete typed spelling is structurally stronger than a longer
        # pronunciation that merely shares its prefix. Preserve model-first
        # ranking within each spelling bucket; this only prevents a flood of
        # model-scored extensions from exhausting the fixed 35 slots first.
        han = sorted(
            (candidate for candidate in session.pending if candidate.script == "han"),
            key=lambda candidate: (
                candidate.pinyin.replace("'", "") != compact,
                _candidate_key(candidate),
            ),
        )
        latin = sorted(
            (candidate for candidate in session.pending if candidate.script == "latin"),
            key=_latin_key,
        )
        other = [
            candidate
            for candidate in session.pending
            if candidate.script not in {"han", "latin"}
        ]
        session.pending = [*self._merge_chinese_first(han, latin), *other]

    def install_baseline_scores(
        self,
        scores: Sequence[float],
        *,
        continuation_root: Any | None = None,
    ) -> None:
        self._baseline_root_pages.clear()
        super().install_baseline_scores(scores, continuation_root=continuation_root)

    def prewarm_single_letter_pages(self) -> None:
        super().prewarm_single_letter_pages()

    @staticmethod
    def _path_key(path: Any) -> tuple[object, ...]:
        if isinstance(path, _ScoredHanExpansion):
            return ("han_resume", tuple(path.token_path), path.cursor)
        if isinstance(path, _DeferredRootSeeds):
            return ("han_root_resume", id(path.records), path.cursor)
        return (
            path.script,
            tuple(path.token_path),
            int(getattr(path, "matched_letters", -1)),
            tuple(path.pinyin_path),
            tuple(
                (tuple(edge.entry.token_path), edge.matched_letters, edge.predicted_syllables)
                for edge in getattr(path, "pending_options", ())
            ),
        )

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

    def _root_han_candidates_and_frontier(
        self,
        raw_keys: str,
        state: BackendState | None,
        response_epoch: int,
    ) -> tuple[list[Candidate], list[Any]]:
        """Select visible roots now and defer all continuation bookkeeping."""

        self._raise_if_query_expired()
        plan = self._root_han_plan(raw_keys)
        self._raise_if_query_expired()
        if not plan:
            return [], []
        scores = self._score_root(state, [entry.token_id for entry in plan])
        self._raise_if_query_expired()
        candidates = self._root_han_candidates_top_k_from_scores(
            raw_keys,
            plan,
            response_epoch=response_epoch,
            scores=scores,
            limit=CHINESE_PAGE_SIZE,
        )
        self._raise_if_query_expired()
        frozen_scores = np.asarray(scores, dtype=np.float32).reshape(-1).copy()
        frozen_scores.flags.writeable = False
        return candidates, [self._deferred_han_frontier(raw_keys, plan, frozen_scores)]

    @staticmethod
    def _deferred_han_frontier(
        raw_keys: str,
        plan: Sequence[_RootHanPlanEntry],
        scores: np.ndarray,
    ) -> _DeferredHanFrontier:
        parsed = parse_raw_pinyin(raw_keys)
        viable = [
            entry for entry, score in zip(plan, scores, strict=True) if math.isfinite(float(score))
        ]
        predicted_syllables = min(
            (entry.predicted_syllables for entry in viable),
            default=0,
        )
        matched_letters = min(
            (
                sum(
                    character != "'"
                    for character in parsed.raw[: min(max(entry.consumed_keys, 0), len(parsed.raw))]
                )
                for entry in viable
            ),
            default=0,
        )
        return _DeferredHanFrontier(
            raw_keys=raw_keys,
            plan=tuple(plan),
            scores=scores,
            matched_letters=matched_letters,
            predicted_syllables=predicted_syllables,
        )

    def _root_han_candidates_top_k_from_scores(
        self,
        raw_keys: str,
        plan: Sequence[_RootHanPlanEntry],
        scores: Sequence[float],
        *,
        response_epoch: int,
        limit: int,
    ) -> list[Candidate]:
        """Materialize only the exact visible prefix from one logits view."""

        if limit <= 0 or not plan:
            return []
        values = np.asarray(scores, dtype=np.float32).reshape(-1)
        if values.size != len(plan):
            raise ValueError("root Han score count does not match the plan")
        order = self._root_han_selection_orders.pop(raw_keys, None)
        if order is None or order.incomplete.size != len(plan):
            static_order = sorted(
                range(len(plan)),
                key=lambda index: (
                    -plan[index].consumed_keys,
                    plan[index].normalized_text,
                    plan[index].token_id,
                    plan[index].pinyin,
                ),
            )
            static_tie_rank = np.empty(len(plan), dtype=np.int32)
            static_tie_rank[np.asarray(static_order, dtype=np.intp)] = np.arange(
                len(plan), dtype=np.int32
            )
            order = _RootHanSelectionOrder(
                ranking_tier=np.fromiter(
                    (entry.ranking_tier for entry in plan),
                    dtype=np.int32,
                    count=len(plan),
                ),
                incomplete=np.fromiter(
                    (not entry.completes_input for entry in plan),
                    dtype=np.bool_,
                    count=len(plan),
                ),
                predicted_syllables=np.fromiter(
                    (entry.predicted_syllables for entry in plan),
                    dtype=np.int32,
                    count=len(plan),
                ),
                static_rank=np.fromiter(
                    (entry.static_rank for entry in plan),
                    dtype=np.int32,
                    count=len(plan),
                ),
                static_tie_rank=static_tie_rank,
            )
        self._root_han_selection_orders[raw_keys] = order
        while len(self._root_han_selection_orders) > _MAX_ROOT_HAN_PLAN_CACHE:
            self._root_han_selection_orders.popitem(last=False)

        best: dict[tuple[str, int], tuple[tuple[object, ...], int]] = {}
        for index, entry in enumerate(plan):
            if index % 64 == 0:
                self._raise_if_query_expired()
            if len(entry.token_path or (entry.token_id,)) != 1:
                continue
            score = float(values[index])
            if not math.isfinite(score):
                continue
            identity = (entry.normalized_text, entry.consumed_keys)
            rank_key = (
                int(order.ranking_tier[index]),
                bool(order.incomplete[index]),
                int(order.predicted_syllables[index]),
                int(order.static_rank[index]),
                -score,
                int(order.static_tie_rank[index]),
            )
            previous = best.get(identity)
            if previous is None or rank_key < previous[0]:
                best[identity] = (rank_key, index)

        selected_records = heapq.nsmallest(
            limit,
            best.values(),
            key=lambda record: record[0],
        )
        selected: list[Candidate] = []
        for rank_key, index in selected_records:
            entry = plan[index]
            score = -float(rank_key[4])
            selected.append(
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
                    ranking_tier=entry.ranking_tier,
                    static_rank=entry.static_rank,
                    predicted_syllables=entry.predicted_syllables,
                )
            )
        return selected

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
            for match in self.matcher.neural_matches(
                raw,
                0,
                parsed.explicit_boundaries,
            )
            if match.next_position > 0
            and match.entry.token_path
            and len(match.entry.token_path) <= MAX_MODEL_TOKENS
            and len(match.entry.text) <= MAX_HAN_CHARACTERS
        ]
        self._raise_if_query_expired()
        static_ranks = _single_initial_static_ranks(matches, raw)
        plan = tuple(
            _RootHanPlanEntry(
                text=match.entry.text,
                pinyin=match.entry.display_pinyin,
                normalized_text=match.entry.normalized_text,
                consumed_keys=parsed.raw_characters_for_letters(match.next_position),
                completes_input=match.next_position == len(raw),
                syllables=match.entry.syllables,
                token_id=int(match.entry.token_path[0]),
                ranking_tier=0 if match.entry.pinyin == raw else 1,
                static_rank=static_ranks.get(int(match.entry.token_path[0]), 0),
                predicted_syllables=(
                    match.completion_syllables if match.next_position == len(raw) else 0
                ),
                token_path=match.entry.token_path,
            )
            for match in matches
        )
        self._root_han_plans[raw_keys] = plan
        while len(self._root_han_plans) > _MAX_ROOT_HAN_PLAN_CACHE:
            self._root_han_plans.popitem(last=False)
        return plan

    def _han_frontier_from_candidates(
        self,
        raw_keys: str,
        candidates: list[Candidate],
    ) -> list[Any]:
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return []

        frontier: list[_HanSearchPath] = []
        seen: set[tuple[object, ...]] = set()
        for candidate in sorted(candidates, key=_candidate_key):
            if (
                candidate.script != "han"
                or not candidate.token_path
                or len(candidate.token_path) >= MAX_MODEL_TOKENS
                or len(candidate.text) >= MAX_HAN_CHARACTERS
            ):
                continue
            consumed_raw = min(max(candidate.consumed_keys, 0), len(parsed.raw))
            matched_letters = sum(character != "'" for character in parsed.raw[:consumed_raw])
            path = _HanSearchPath(
                text=candidate.text,
                pinyin_path=tuple(part for part in candidate.pinyin.split("'") if part),
                token_path=candidate.token_path,
                score=float(candidate.model_score or 0.0),
                predicted_syllables=candidate.predicted_syllables,
                matched_letters=min(matched_letters, len(parsed.compact)),
            )
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            frontier.append(path)
        return frontier

    def _root_han_search_frontier(
        self,
        raw_keys: str,
        state: BackendState | None,
    ) -> list[_HanSearchPath]:
        """Retain bounded search seeds independently of the visible root cap."""

        self._raise_if_query_expired()
        plan = self._root_han_plan(raw_keys)
        self._raise_if_query_expired()
        if not plan:
            return []
        scores = self._score_root(state, [entry.token_id for entry in plan])
        self._raise_if_query_expired()
        return self._root_han_search_frontier_from_scores(raw_keys, plan, scores)

    def _root_han_search_frontier_from_scores(
        self,
        raw_keys: str,
        plan: Sequence[_RootHanPlanEntry],
        scores: Sequence[float],
    ) -> list[_HanSearchPath]:
        """Retain every viable seed for conditional continuation scoring."""

        self._raise_if_query_expired()
        if not plan:
            return []
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return []
        # De-duplicate equivalent roots before allocating paths. A low-scored
        # root can win after conditional continuation, so it cannot be dropped.
        best: dict[
            tuple[object, ...],
            tuple[tuple[object, ...], int, _RootHanPlanEntry, float, int, tuple[str, ...]],
        ] = {}
        pending_groups: dict[int, list[_HanEdge]] = {}
        pending_scores: dict[int, float] = {}
        for index, (entry, score) in enumerate(zip(plan, scores, strict=True)):
            if index % 64 == 0:
                self._raise_if_query_expired()
            value = float(score)
            if not math.isfinite(value):
                continue
            if len(entry.token_path or (entry.token_id,)) > 1:
                matched_letters = min(
                    sum(
                        character != "'"
                        for character in parsed.raw[
                            : min(max(entry.consumed_keys, 0), len(parsed.raw))
                        ]
                    ),
                    len(parsed.compact),
                )
                pending_groups.setdefault(entry.token_id, []).append(
                    _HanEdge(entry, matched_letters, entry.predicted_syllables)
                )
                pending_scores[entry.token_id] = value
                continue
            consumed_raw = min(max(entry.consumed_keys, 0), len(parsed.raw))
            matched_letters = min(
                sum(character != "'" for character in parsed.raw[:consumed_raw]),
                len(parsed.compact),
            )
            pinyin_path = tuple(part for part in entry.pinyin.split("'") if part)
            path_key = ("han", (entry.token_id,), matched_letters, pinyin_path)
            rank_key = (
                not entry.completes_input,
                entry.predicted_syllables,
                -value,
                -entry.consumed_keys,
                entry.normalized_text,
                (entry.token_id,),
                entry.pinyin,
            )
            record = (rank_key, index, entry, value, matched_letters, pinyin_path)
            previous = best.get(path_key)
            if previous is None or rank_key < previous[0]:
                best[path_key] = record

        selected = sorted(best.values(), key=lambda record: record[0])
        self._raise_if_query_expired()
        records: tuple[tuple[object, ...], ...] = (
            *(('ordinary', entry, value, matched_letters, pinyin_path)
              for _, _, entry, value, matched_letters, pinyin_path in selected),
            *(('pending', token_id, pending_scores[token_id], tuple(options))
              for token_id, options in pending_groups.items()),
        )
        frontier = self._materialize_root_seed_records(records[:MAX_FRONTIER_PER_BUCKET])
        if len(records) > MAX_FRONTIER_PER_BUCKET:
            frontier.append(_DeferredRootSeeds(records, MAX_FRONTIER_PER_BUCKET))
        return frontier

    @staticmethod
    def _materialize_root_seed_records(
        records: Sequence[tuple[object, ...]],
    ) -> list[_HanSearchPath]:
        frontier: list[_HanSearchPath] = []
        for record in records:
            if record[0] == "ordinary":
                _, entry, value, matched_letters, pinyin_path = record
                frontier.append(
                    _HanSearchPath(
                        text=entry.text,
                        pinyin_path=pinyin_path,
                        token_path=(entry.token_id,),
                        score=value,
                        predicted_syllables=entry.predicted_syllables,
                        matched_letters=matched_letters,
                    )
                )
            else:
                _, token_id, value, options = record
                frontier.append(
                    _HanSearchPath(
                        text="",
                        pinyin_path=(),
                        token_path=(token_id,),
                        score=value,
                        predicted_syllables=0,
                        matched_letters=0,
                        pending_options=options,
                    )
                )
        return frontier

    def _resume_root_han_frontier(
        self,
        session: _SearchSession,
        work: _DeferredRootSeeds,
    ) -> int:
        end = min(len(work.records), work.cursor + MAX_FRONTIER_PER_BUCKET)
        materialized = self._materialize_root_seed_records(work.records[work.cursor:end])
        session.frontier.extend(materialized)
        if end < len(work.records):
            session.frontier.append(_DeferredRootSeeds(work.records, end))
        return len(materialized)

    def _root_candidates(
        self,
        *,
        raw_keys: str,
        mode: NeuralLanguageMode,
        state: BackendState | None,
        response_epoch: int,
        allow_prewarm_cache: bool = True,
    ) -> tuple[list[Candidate], list[Any], str]:
        self._raise_if_query_expired()
        cached = (
            self._baseline_single_letter.get((raw_keys, mode))
            if state is None and allow_prewarm_cache
            else None
        )
        if cached is not None:
            candidates = [replace(candidate, context_epoch=response_epoch) for candidate in cached]
            self._raise_if_query_expired()
            plan = self._root_han_plan(raw_keys)
            scores = self._score_root(state, [entry.token_id for entry in plan])
            frozen_scores = np.asarray(scores, dtype=np.float32).reshape(-1).copy()
            frozen_scores.flags.writeable = False
            han_frontier: list[Any] = (
                [self._deferred_han_frontier(raw_keys, plan, frozen_scores)]
                if plan and mode is NeuralLanguageMode.CHINESE_FIRST
                else []
            )
            _, latin_frontier = self._root_latin_candidates_and_frontier(
                raw_keys,
                state,
                response_epoch,
            )
            self._raise_if_query_expired()
            return (
                candidates,
                [*han_frontier, *latin_frontier],
                "baseline",
            )

        cacheable = state is None and allow_prewarm_cache
        cache_key = (raw_keys, mode)
        cached_root = self._baseline_root_pages.pop(cache_key, None) if cacheable else None
        if cached_root is not None:
            self._baseline_root_pages[cache_key] = cached_root
            candidates, frontier = cached_root
            return (
                [
                    candidate
                    if response_epoch == 0
                    else replace(candidate, context_epoch=response_epoch)
                    for candidate in candidates
                ],
                list(frontier),
                "baseline",
            )

        han, han_frontier = self._root_han_candidates_and_frontier(
            raw_keys,
            state,
            response_epoch,
        )
        self._raise_if_query_expired()
        latin, latin_frontier = self._root_latin_candidates_and_frontier(
            raw_keys,
            state,
            response_epoch,
        )
        self._raise_if_query_expired()
        score_source = "context" if state is not None else "baseline"

        if mode is NeuralLanguageMode.LATIN_FIRST:
            ordered_latin = sorted(latin, key=_latin_key)
            if not ordered_latin:
                ordered_latin = [_literal_candidate(raw_keys, response_epoch)]
            result = (ordered_latin, latin_frontier, score_source)
            if cacheable:
                self._remember_baseline_root_page(cache_key, ordered_latin, latin_frontier)
            return result

        ordered_han = sorted(han, key=_candidate_key)
        ordered_latin = sorted(latin, key=_latin_key)
        ordered = self._merge_chinese_first(ordered_han, ordered_latin)
        frontier = [*han_frontier, *latin_frontier]
        self._raise_if_query_expired()
        if not ordered and not frontier and self._baseline_scores is not None:
            ordered = [_literal_candidate(raw_keys, response_epoch)]
        if cacheable:
            self._remember_baseline_root_page(cache_key, ordered, frontier)
        return ordered, frontier, score_source

    def _remember_baseline_root_page(
        self,
        key: tuple[str, NeuralLanguageMode],
        candidates: list[Candidate],
        frontier: list[Any],
    ) -> None:
        self._baseline_root_pages[key] = (
            tuple(
                candidate if candidate.context_epoch == 0 else replace(candidate, context_epoch=0)
                for candidate in candidates
            ),
            tuple(frontier),
        )
        self._baseline_root_pages.move_to_end(key)
        while len(self._baseline_root_pages) > _MAX_BASELINE_ROOT_PAGE_CACHE:
            self._baseline_root_pages.popitem(last=False)

    def _prune_frontier(self, session: _SearchSession) -> None:
        deferred = [path for path in session.frontier if isinstance(path, _DeferredHanFrontier)]
        if deferred:
            immediate = [
                path for path in session.frontier if not isinstance(path, _DeferredHanFrontier)
            ]
            for seed in deferred:
                roots = self._materialize_root_han_candidates(
                    seed.plan,
                    state=None,
                    response_epoch=session.identity.context_epoch,
                    scores=seed.scores,
                )
                for candidate in roots:
                    key = (
                        unicodedata.normalize("NFKC", candidate.text),
                        candidate.consumed_keys,
                    )
                    if key in session.seen_candidates:
                        continue
                    session.seen_candidates.add(key)
                    session.pending.append(candidate)
                immediate.extend(
                    self._root_han_search_frontier_from_scores(
                        seed.raw_keys,
                        seed.plan,
                        seed.scores,
                    )
                )
            session.frontier = immediate
            self._sort_pending(session)
        preferred_script = (
            "latin" if session.identity.mode is NeuralLanguageMode.LATIN_FIRST else "han"
        )
        session.frontier.sort(
            key=lambda path: (
                path.script != preferred_script,
                path.predicted_syllables if path.script == "han" else 0,
                -path.score,
                len(path.token_path),
                path.text,
                path.token_path,
            )
        )
        retained: list[Any] = []
        seen_paths: set[tuple[object, ...]] = set()
        for path in session.frontier:
            key = self._path_key(path)
            if key in session.expanded_paths or key in seen_paths:
                continue
            seen_paths.add(key)
            retained.append(path)
        session.frontier = retained

    def _minimum_future_bucket(self, session: _SearchSession) -> int | None:
        # A deferred seed can still yield a lower prediction bucket when its
        # continuation snapshot is available. Without one it cannot expand.
        buckets = [
            path.predicted_syllables + (0 if getattr(path, "pending_options", ()) else 1)
            for path in session.frontier
            if path.script in {"han", "han_resume", "han_root_resume"}
            and (
                not isinstance(path, _DeferredHanFrontier)
                or session.continuation_root is not None
            )
            and self._path_key(path) not in session.expanded_paths
        ]
        return min(buckets, default=None)

    def _prepare_page_search(
        self,
        session: _SearchSession,
        cancel: threading.Event,
    ) -> bool:
        """Realize deferred roots off-lock before later pages are frozen."""

        with self._state_lock:
            if self._sessions.get(session.candidate_set_id) is not session or cancel.is_set():
                return False
            seeds = tuple(
                path for path in session.frontier if isinstance(path, _DeferredHanFrontier)
            )
        if not seeds:
            return True

        prepared: list[tuple[list[Candidate], list[_HanSearchPath]]] = []
        for seed in seeds:
            roots = self._materialize_root_han_candidates(
                seed.plan,
                state=None,
                response_epoch=session.identity.context_epoch,
                scores=seed.scores,
            )
            frontier = self._root_han_search_frontier_from_scores(
                seed.raw_keys,
                seed.plan,
                seed.scores,
            )
            if cancel.is_set():
                return False
            prepared.append((roots, frontier))

        with self._state_lock:
            if self._sessions.get(session.candidate_set_id) is not session or cancel.is_set():
                return False
            seed_ids = {id(seed) for seed in seeds}
            session.frontier = [
                path
                for path in session.frontier
                if not (isinstance(path, _DeferredHanFrontier) and id(path) in seed_ids)
            ]
            for roots, frontier in prepared:
                for candidate in roots:
                    key = (
                        unicodedata.normalize("NFKC", candidate.text),
                        candidate.consumed_keys,
                    )
                    if key in session.seen_candidates:
                        continue
                    session.seen_candidates.add(key)
                    session.pending.append(candidate)
                session.frontier.extend(frontier)
            self._sort_pending(session)
            self._prune_frontier(session)
            continuation = getattr(self.backend, "continue_from_root", None)
            if session.continuation_root is None or not callable(continuation):
                session.exhausted = True
            return True

    def _han_edges_for(
        self,
        session: _SearchSession,
        parent: _HanSearchPath,
    ) -> dict[int, tuple[_HanEdge, ...]]:
        if parent.pending_options:
            next_index = len(parent.token_path) - parent.pending_start
            pending_grouped: dict[int, list[_HanEdge]] = {}
            for edge in parent.pending_options:
                path = edge.entry.token_path
                if next_index < len(path):
                    pending_grouped.setdefault(path[next_index], []).append(edge)
            return {token_id: tuple(edges) for token_id, edges in pending_grouped.items()}
        if self.matcher is None:
            return {}
        try:
            parsed = parse_raw_pinyin(session.identity.raw_keys)
        except ValueError:
            return {}

        grouped: dict[int, list[_HanEdge]] = {}
        if parent.matched_letters < len(parsed.compact):
            for match in self.matcher.neural_matches(
                parsed.compact,
                parent.matched_letters,
                parsed.explicit_boundaries,
            ):
                entry = match.entry
                if (
                    match.next_position <= parent.matched_letters
                    or not entry.token_path
                    or len(parent.token_path) + len(entry.token_path) > MAX_MODEL_TOKENS
                    or len(entry.text) > MAX_HAN_CHARACTERS
                ):
                    continue
                predicted = parent.predicted_syllables
                if match.next_position == len(parsed.compact):
                    predicted += match.completion_syllables
                grouped.setdefault(int(entry.token_path[0]), []).append(
                    _HanEdge(
                        entry=entry,
                        matched_letters=match.next_position,
                        predicted_syllables=predicted,
                    )
                )
        else:
            all_groups = (
                *self._continuation_entries_by_token.items(),
                *self._multitoken_entries_by_first.items(),
            )
            for token_id, entries in all_groups:
                for entry in entries:
                    grouped.setdefault(token_id, []).append(
                        _HanEdge(
                            entry=entry,
                            matched_letters=len(parsed.compact),
                            predicted_syllables=(
                                parent.predicted_syllables + len(entry.syllable_path)
                            ),
                        )
                    )
        return {token_id: tuple(edges) for token_id, edges in grouped.items()}

    def _lexical_completion_eligible(self, raw_keys: str) -> bool:
        """Whether a complete-syllable path can supply a lexical tail."""
        if self.matcher is None or self._baseline_scores is None:
            return False
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return False
        if not parsed.compact:
            return False
        groups = parsed.raw.split("'")
        if not all(self.matcher.is_complete_syllable_sequence(group) for group in groups[:-1]):
            return False

        def complete_syllable_count(value: str) -> int:
            counts = [-1] * (len(value) + 1)
            counts[0] = 0
            for position in range(len(value)):
                if counts[position] < 0:
                    continue
                for syllable in self.matcher.by_initial.get(value[position], ()):
                    if value.startswith(syllable, position):
                        end = position + len(syllable)
                        counts[end] = max(counts[end], counts[position] + 1)
            return counts[-1]

        final_group = groups[-1]
        if self.matcher.is_complete_syllable_sequence(final_group):
            return True
        prior_syllables = sum(complete_syllable_count(group) for group in groups[:-1])
        return any(
            prior_syllables + complete_syllable_count(final_group[:split]) >= 2
            and any(
                syllable.startswith(final_group[split:])
                and len(syllable) > len(final_group[split:])
                for syllable in self.matcher.by_initial.get(final_group[split], ())
            )
            for split in range(len(final_group))
        )

    def _lexical_tail_may_fill_page(self, raw_keys: str, page_size: int) -> bool:
        """Return false only after proving fewer than one page of legal paths.

        A large root plan is enough to warrant background preparation. For a
        small plan, count completion paths without logits, capped at one page.
        If the bounded check is inconclusive, preserve the pending route.
        """
        if len(self._root_han_plan(raw_keys)) >= page_size:
            return True
        if self.matcher is None:
            return False
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return False
        compact = parsed.compact
        if not compact:
            return False
        deadline = self.clock() + 0.002
        memo: dict[int, int] = {}

        def count_from(position: int) -> int:
            if self.clock() >= deadline:
                raise TimeoutError
            if position == len(compact):
                return 1
            if position in memo:
                return memo[position]
            count = 0
            for match in self.matcher.neural_matches(
                compact, position, parsed.explicit_boundaries
            ):
                if match.next_position <= position or not match.entry.token_path:
                    continue
                count += count_from(match.next_position)
                if count >= page_size:
                    break
            memo[position] = min(count, page_size)
            return memo[position]

        try:
            return count_from(0) >= page_size
        except TimeoutError:
            return True

    def _lexical_completion_fallback(
        self,
        session: _SearchSession,
        *,
        limit: int,
        absolute_deadline: float,
    ) -> list[Candidate]:
        """Advance a revision-scoped lexical cursor only within this budget."""
        if limit <= 0 or not self._lexical_completion_eligible(session.identity.raw_keys):
            return []
        candidate_set_id = session.candidate_set_id
        if candidate_set_id in self._lexical_completion_exhausted:
            return []
        cursor = self._lexical_completion_cursors.get(candidate_set_id)
        if cursor is None:
            snapshot = replace(
                session,
                continuation_root=None,
                # Immutable model candidates can identify a promising shorter
                # lexical root by text and pinyin, even when the model used a
                # different single-token path for the completed phrase.
                pending=list(session.pending),
                frontier=list(session.frontier),
                frozen_pages={},
                seen_candidates=set(session.seen_candidates),
                expanded_paths=set(),
            )
            cursor = self._iter_lexical_completion_candidates(snapshot)
            self._lexical_completion_cursors[candidate_set_id] = cursor
        candidates: list[Candidate] = []
        while len(candidates) < limit and self.clock() < absolute_deadline:
            try:
                candidate = next(cursor)
            except StopIteration:
                self._lexical_completion_cursors.pop(candidate_set_id, None)
                self._lexical_completion_exhausted.add(candidate_set_id)
                break
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _iter_lexical_completion_candidates(
        self, session: _SearchSession
    ) -> Iterator[Candidate | None]:
        """Enumerate legal Han paths without losing work at a batch boundary.

        Conditional continuation scores remain authoritative for every candidate
        already discovered by the model.  This bounded best-first walk uses the
        permanent empty-context logits only to order additional legal pinyin
        paths; those supplements have a lower ranking tier and can never displace
        a model-scored candidate.  No model call or mutable editor context is
        touched here.
        """

        try:
            parsed = parse_raw_pinyin(session.identity.raw_keys)
        except ValueError:
            return
        compact = parsed.compact
        if not compact:
            return
        exact_spelling_possible = self.matcher.is_complete_syllable_sequence(compact)

        baseline = np.asarray(self._baseline_scores, dtype=np.float32).reshape(-1)
        # Preserve the current editor-context score for the first token when
        # the deferred root plan is still resident. Only deeper tokens fall
        # back to the immutable empty-context baseline.
        contextual_root_scores: dict[int, float] = {}
        for frontier_item in session.frontier:
            if not isinstance(frontier_item, _DeferredHanFrontier):
                continue
            if frontier_item.raw_keys != session.identity.raw_keys:
                continue
            for entry, value in zip(
                frontier_item.plan,
                frontier_item.scores,
                strict=True,
            ):
                contextual_root_scores[int(entry.token_id)] = float(value)
        serial = 0
        frontier: list[tuple[tuple[object, ...], int, _HanSearchPath, int]] = []

        def structural_rank(path: _HanSearchPath) -> int:
            spelling = "".join(path.pinyin_path)
            completion_letters = max(0, len(spelling) - path.matched_letters)
            # Complete spellings are a hard tier above prefix extensions
            # (``hua`` before ``huan``). Within that tier, prefer the parse
            # with fewer syllables (``ming'xian'dui`` before
            # ``ming'xia+n+d-ui``). Baseline logits only break ties after
            # these pinyin-structure guarantees.
            return completion_letters * (MAX_HAN_CHARACTERS + 1) + len(path.pinyin_path)

        def path_priority(path: _HanSearchPath, static_rank: int = 0) -> tuple[object, ...]:
            average_score = path.score / max(1, len(path.token_path))
            spelling = "".join(path.pinyin_path)
            return (
                # Keep exact-spelling prefixes ahead of longer readings only
                # when the typed input can actually spell a complete reading.
                # With an unfinished final syllable (e.g. ``zhuyid``), every
                # legal completed path extends the input, so this tier would
                # otherwise exhaust all unfinished prefixes first.
                exact_spelling_possible and spelling != compact[:path.matched_letters],
                # Drive the immutable trie walk toward whole-input paths
                # before widening across thousands of shallow roots.  The
                # previous syllable-first ordering could spend the entire UI
                # deadline enumerating one-token ``ming*`` roots before ever
                # reaching ``ming'xian'dui``.
                -path.matched_letters,
                path.predicted_syllables,
                structural_rank(path),
                len(path.token_path),
                -average_score,
                static_rank,
                sum(path.token_path),
                unicodedata.normalize("NFKC", path.text),
                path.token_path,
            )

        roots: list[tuple[_HanSearchPath, int]] = []
        for entry in self._root_han_plan(session.identity.raw_keys):
            if entry.token_id < 0 or entry.token_id >= baseline.size:
                continue
            score = contextual_root_scores.get(
                entry.token_id,
                float(baseline[entry.token_id]),
            )
            if not math.isfinite(score):
                continue
            consumed_raw = min(max(entry.consumed_keys, 0), len(parsed.raw))
            matched_letters = min(
                sum(character != "'" for character in parsed.raw[:consumed_raw]),
                len(compact),
            )
            path = _HanSearchPath(
                text=entry.text,
                pinyin_path=tuple(part for part in entry.pinyin.split("'") if part),
                token_path=(entry.token_id,),
                score=score,
                predicted_syllables=entry.predicted_syllables,
                matched_letters=matched_letters,
            )
            roots.append((path, entry.static_rank))
            if len(roots) % 32 == 0:
                yield None

        # A scored phrase is stronger evidence for its longest legal text and
        # pinyin prefix than empty-context logits are for an unrelated root.
        # The phrase may itself be one token, so token-id prefix matching is
        # insufficient. Reuse only immutable candidates from this revision.
        hinted_roots: dict[int, int] = {}
        if not exact_spelling_possible:
            roots_by_text: dict[str, list[_HanSearchPath]] = {}
            max_root_text_length = 0
            for root_index, (path, _) in enumerate(roots, start=1):
                roots_by_text.setdefault(path.text, []).append(path)
                max_root_text_length = max(max_root_text_length, len(path.text))
                if root_index % 128 == 0 and root_index < len(roots):
                    yield None
            prefix_checks = 0
            for candidate in sorted(session.pending, key=_candidate_key):
                if (
                    candidate.script != "han"
                    or candidate.constraint_kind != "pinyin"
                    or not candidate.completes_input
                ):
                    continue
                matching = []
                for prefix_length in range(
                    1, min(max_root_text_length, len(candidate.text) - 1) + 1
                ):
                    prefix_checks += 1
                    if prefix_checks % 64 == 0:
                        yield None
                    for path in roots_by_text.get(candidate.text[:prefix_length], ()):
                        prefix_checks += 1
                        if prefix_checks % 64 == 0:
                            yield None
                        if candidate.pinyin.startswith("'".join(path.pinyin_path) + "'"):
                            matching.append(path)
                if matching:
                    longest = max((path.matched_letters, len(path.text)) for path in matching)
                    for path in matching:
                        if (path.matched_letters, len(path.text)) == longest:
                            hinted_roots.setdefault(path.token_path[0], len(hinted_roots))

        # Without a model-backed prefix, give each nearby group one page
        # before returning to an earlier group. Deferred paths stay in the
        # heap; no root is discarded.
        root_groups: dict[int, int] = {}
        if not exact_spelling_possible and not hinted_roots:
            for path, _ in sorted(roots, key=lambda seed: path_priority(*seed)):
                root_id = path.token_path[0]
                if root_id not in root_groups:
                    root_groups[root_id] = len(root_groups) // CHINESE_PAGE_SIZE
        emitted_by_root: dict[int, int] = {}
        emitted_by_group: dict[int, int] = {}

        def queue_priority(path: _HanSearchPath, static_rank: int = 0) -> tuple[object, ...]:
            priority = path_priority(path, static_rank)
            if exact_spelling_possible:
                return priority
            root_id = path.token_path[0]
            if hinted_roots:
                return (hinted_roots.get(root_id, len(hinted_roots)), *priority)
            group = root_groups[root_id]
            return (
                emitted_by_group.get(group, 0) // CHINESE_PAGE_SIZE,
                group,
                emitted_by_root.get(root_id, 0) // CHINESE_PAGE_SIZE,
                *priority,
            )

        for path, static_rank in roots:
            heapq.heappush(
                frontier,
                (queue_priority(path, static_rank), serial, path, static_rank),
            )
            serial += 1
            if serial % 128 == 0 and serial < len(roots):
                yield None

        existing = set(session.seen_candidates)
        visited: set[tuple[tuple[int, ...], int, int]] = set()
        states = 0
        stale_steps = 0
        while frontier:
            priority, _, path, static_rank = heapq.heappop(frontier)
            root_id = path.token_path[0]
            if (
                not exact_spelling_possible
                and not hinted_roots
                and (
                    priority[0]
                    != emitted_by_group.get(root_groups[root_id], 0) // CHINESE_PAGE_SIZE
                    or priority[2]
                    != emitted_by_root.get(root_id, 0) // CHINESE_PAGE_SIZE
                )
            ):
                stale_steps += 1
                if stale_steps % 32 == 0:
                    yield None
                heapq.heappush(
                    frontier,
                    (queue_priority(path, static_rank), serial, path, static_rank),
                )
                serial += 1
                continue
            state_key = (path.token_path, path.matched_letters, path.predicted_syllables)
            if state_key in visited:
                continue
            visited.add(state_key)
            states += 1
            if states % 32 == 0:
                yield None
            if path.matched_letters == len(compact):
                key = (unicodedata.normalize("NFKC", path.text), len(parsed.raw))
                if key not in existing:
                    existing.add(key)
                    if not exact_spelling_possible and not hinted_roots:
                        emitted_by_root[root_id] = emitted_by_root.get(root_id, 0) + 1
                        group = root_groups[root_id]
                        emitted_by_group[group] = emitted_by_group.get(group, 0) + 1
                    yield Candidate(
                        text=path.text,
                        pinyin="'".join(path.pinyin_path),
                        consumed_keys=len(parsed.raw),
                        score=path.score,
                        context_epoch=session.identity.context_epoch,
                        coverage=False,
                        completes_input=True,
                        syllables=len(path.pinyin_path),
                        token_id=path.token_path[0],
                        constraint_kind="pinyin_lexical_fallback",
                        script="han",
                        model_score=path.score,
                        total_score=path.score,
                        token_path=path.token_path,
                        ranking_tier=50,
                        static_rank=structural_rank(path),
                        predicted_syllables=path.predicted_syllables,
                    )
                continue

            edges_by_token: dict[int, list[_HanEdge]] = {}
            if path.matched_letters < len(compact):
                match_count = 0
                for match in self.matcher.neural_matches(
                    compact,
                    path.matched_letters,
                    parsed.explicit_boundaries,
                ):
                    match_count += 1
                    if match_count % 64 == 0:
                        yield None
                    entry = match.entry
                    if (
                        match.next_position <= path.matched_letters
                        or entry.token_id is None
                        or int(entry.token_id) not in self._continuation_entries_by_token
                        or entry.coverage
                        or len(entry.text) > MAX_HAN_CHARACTERS
                    ):
                        continue
                    predicted = path.predicted_syllables
                    if match.next_position == len(compact):
                        predicted += match.completion_syllables
                    edges_by_token.setdefault(int(entry.token_id), []).append(
                        _HanEdge(
                            entry=entry,
                            matched_letters=match.next_position,
                            predicted_syllables=predicted,
                        )
                    )
            else:
                entry_count = 0
                for token_id, entries in self._continuation_entries_by_token.items():
                    for entry in entries:
                        entry_count += 1
                        if entry_count % 64 == 0:
                            yield None
                        if entry.coverage or len(entry.text) > MAX_HAN_CHARACTERS:
                            continue
                        edges_by_token.setdefault(token_id, []).append(
                            _HanEdge(
                                entry=entry,
                                matched_letters=path.matched_letters,
                                predicted_syllables=(
                                    path.predicted_syllables + len(entry.syllable_path)
                                ),
                            )
                        )

            edge_count = 0
            for token_id, edges in edges_by_token.items():
                if token_id < 0 or token_id >= baseline.size:
                    continue
                token_score = float(baseline[token_id])
                if not math.isfinite(token_score):
                    continue
                for edge in edges:
                    edge_count += 1
                    if edge_count % 64 == 0:
                        yield None
                    if (
                        edge.matched_letters < path.matched_letters
                        or (
                            edge.matched_letters == path.matched_letters
                            and edge.predicted_syllables <= path.predicted_syllables
                        )
                    ):
                        continue
                    token_path = (*path.token_path, token_id)
                    text = path.text + edge.entry.text
                    if len(token_path) > MAX_MODEL_TOKENS or len(text) > MAX_HAN_CHARACTERS:
                        continue
                    child = _HanSearchPath(
                        text=text,
                        pinyin_path=(*path.pinyin_path, *edge.entry.syllable_path),
                        token_path=token_path,
                        score=path.score + token_score,
                        predicted_syllables=edge.predicted_syllables,
                        matched_letters=edge.matched_letters,
                    )
                    heapq.heappush(frontier, (queue_priority(child), serial, child, 0))
                    serial += 1

    def _expand_han_constrained(
        self,
        session: _SearchSession,
        parent: _HanSearchPath,
        token_ids: tuple[int, ...],
        values: np.ndarray,
        edges_by_token: dict[int, tuple[_HanEdge, ...]],
        absolute_deadline: float,
        *,
        positions: tuple[int, ...] | None = None,
        cursor: int = 0,
    ) -> int:
        parsed = parse_raw_pinyin(session.identity.raw_keys)
        emitted_candidates = 0
        enqueued_frontier = 0
        processed_positions = 0
        if positions is None:
            positions = tuple(_stable_bounded_score_positions(values))
        next_cursor = cursor
        for position in positions[cursor:]:
            token_id = token_ids[position]
            token_score = float(values[position])
            incomplete: list[_HanEdge] = []
            for edge in edges_by_token[token_id]:
                entry = edge.entry
                token_path = (*parent.token_path, token_id)
                if len(token_path) > MAX_MODEL_TOKENS:
                    continue
                consumed_in_entry = (
                    len(parent.token_path) - parent.pending_start
                    if parent.pending_options
                    else 0
                )
                if consumed_in_entry + 1 < len(entry.token_path):
                    incomplete.append(edge)
                    continue
                text = parent.text + entry.text
                if len(text) > MAX_HAN_CHARACTERS:
                    continue
                predicted = edge.predicted_syllables
                entry_syllables = getattr(entry, "syllable_path", None)
                if entry_syllables is None:
                    entry_syllables = tuple(part for part in entry.pinyin.split("'") if part)
                pinyin_path = (*parent.pinyin_path, *entry_syllables)
                score = parent.score + token_score
                consumed_keys = parsed.raw_characters_for_letters(edge.matched_letters)
                completes_input = edge.matched_letters == len(parsed.compact)
                key = (unicodedata.normalize("NFKC", text), consumed_keys)
                replaceable_index = next(
                    (
                        index
                        for index, previous in enumerate(session.pending)
                        if previous.constraint_kind == "pinyin_lexical_fallback"
                        and (
                            unicodedata.normalize("NFKC", previous.text),
                            previous.consumed_keys,
                        )
                        == key
                    ),
                    None,
                )
                if key not in session.seen_candidates or replaceable_index is not None:
                    session.seen_candidates.add(key)
                    if replaceable_index is not None:
                        session.pending.pop(replaceable_index)
                    session.pending.append(
                        Candidate(
                            text=text,
                            pinyin="'".join(pinyin_path),
                            consumed_keys=consumed_keys,
                            score=score,
                            context_epoch=session.identity.context_epoch,
                            coverage=False,
                            completes_input=completes_input,
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
                    emitted_candidates += 1
                if len(token_path) < MAX_MODEL_TOKENS and len(text) < MAX_HAN_CHARACTERS:
                    session.frontier.append(
                        _HanSearchPath(
                            text=text,
                            pinyin_path=pinyin_path,
                            token_path=token_path,
                            score=score,
                            predicted_syllables=predicted,
                            matched_letters=edge.matched_letters,
                        )
                    )
                    enqueued_frontier += 1
            if incomplete:
                # A byte/token fragment has no visible text or pinyin consumption.
                # Options sharing this model prefix are scored once on the next step.
                session.frontier.append(
                    _HanSearchPath(
                        text=parent.text,
                        pinyin_path=parent.pinyin_path,
                        token_path=(*parent.token_path, token_id),
                        score=parent.score + token_score,
                        predicted_syllables=parent.predicted_syllables,
                        matched_letters=parent.matched_letters,
                        pending_options=tuple(incomplete),
                        pending_start=(
                            parent.pending_start
                            if parent.pending_options
                            else len(parent.token_path)
                        ),
                    )
                )
                enqueued_frontier += 1
            next_cursor += 1
            processed_positions += 1
            if (
                processed_positions >= MAX_FRONTIER_PER_BUCKET
                or self.clock() >= absolute_deadline
            ):
                break
        if next_cursor < len(positions):
            session.frontier.append(
                _ScoredHanExpansion(
                    parent=parent,
                    token_ids=token_ids,
                    values=values,
                    edges_by_token=edges_by_token,
                    positions=positions,
                    cursor=next_cursor,
                )
            )
        work_done = max(emitted_candidates + enqueued_frontier, processed_positions)
        return work_done

    def _resume_han_expansion(
        self,
        session: _SearchSession,
        work: _ScoredHanExpansion,
        absolute_deadline: float,
    ) -> int:
        return self._expand_han_constrained(
            session,
            work.parent,
            work.token_ids,
            work.values,
            work.edges_by_token,
            absolute_deadline,
            positions=work.positions,
            cursor=work.cursor,
        )

    def _expand_one_frontier(
        self,
        session: _SearchSession,
        absolute_deadline: float,
    ) -> int:
        # A deferred seed represents root work that is valid without a model
        # continuation root. Realize it before deciding whether deep search is
        # available so root-only backends can still prepare later pages.
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
            return 0
        continuation = getattr(self.backend, "continue_from_root", None)
        if not callable(continuation) or session.continuation_root is None:
            session.exhausted = True
            return 0

        parent: Any | None = None
        token_ids: tuple[int, ...] = ()
        han_edges: dict[int, tuple[_HanEdge, ...]] = {}
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
            token_ids = allowed
            break
        if parent is None or parent_key is None:
            session.exhausted = True
            return 0

        remaining_ms = max(0.0, (absolute_deadline - self.clock()) * 1000.0)
        if remaining_ms <= 0:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent_key)
            return 0
        scored = continuation(
            session.continuation_root,
            [parent.token_path],
            [token_ids],
            deadline_ms=remaining_ms,
        )
        if scored is None:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent_key)
            return 0
        if len(scored) != 1:
            raise RuntimeError("continuation scorer returned an invalid batch")
        values = np.asarray(scored[0], dtype=np.float32)
        if values.size != len(token_ids):
            raise RuntimeError("continuation scorer returned an invalid score vector")

        if parent.script == "han":
            progressed = self._expand_han_constrained(
                session,
                parent,
                token_ids,
                values,
                han_edges,
                absolute_deadline,
            )
        else:
            progressed = self._expand_latin(
                session,
                parent,
                token_ids,
                values,
                absolute_deadline,
            )

        session.search_depth = max(session.search_depth, len(parent.token_path) + 1)
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
        return progressed

    def clear_sessions(self) -> None:
        super().clear_sessions()
        self._lexical_completion_cursors.clear()
        self._lexical_completion_exhausted.clear()

    def _new_session(
        self,
        identity: _SearchIdentity,
        state: BackendState | None,
    ) -> _SearchSession:
        session = super()._new_session(identity, state)
        if any(isinstance(path, _DeferredHanFrontier) for path in session.frontier):
            session.exhausted = False
        while len(self._sessions) > MAX_ACTIVE_SEARCH_SESSIONS:
            self._sessions.popitem(last=False)
        for candidate_set_id in tuple(self._lexical_completion_cursors):
            if candidate_set_id not in self._sessions:
                self._lexical_completion_cursors.pop(candidate_set_id, None)
                self._lexical_completion_exhausted.discard(candidate_set_id)
        return session


__all__ = ["NeuralCandidatePageManager"]
