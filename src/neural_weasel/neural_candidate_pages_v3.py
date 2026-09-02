from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .backends import BackendState
from .candidate import Candidate
from .neural_candidate_pages_v2 import NeuralCandidatePageManager as _V2CandidatePageManager
from .neural_candidates import (
    MAX_ACTIVE_SEARCH_SESSIONS,
    MAX_FRONTIER_PER_BUCKET,
    MAX_HAN_CHARACTERS,
    MAX_MODEL_TOKENS,
    NeuralLanguageMode,
    _candidate_key,
    _latin_key,
    _literal_candidate,
    _SearchIdentity,
    _SearchSession,
)
from .pinyin import is_all_han, parse_raw_pinyin


@dataclass(frozen=True, slots=True)
class _HanSearchPath:
    text: str
    pinyin_path: tuple[str, ...]
    token_path: tuple[int, ...]
    score: float
    predicted_syllables: int
    matched_letters: int
    script: str = "han"


@dataclass(frozen=True, slots=True)
class _HanEdge:
    entry: Any
    matched_letters: int
    predicted_syllables: int


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

    @staticmethod
    def _path_key(path: Any) -> tuple[object, ...]:
        return (
            path.script,
            tuple(path.token_path),
            int(getattr(path, "matched_letters", -1)),
            tuple(path.pinyin_path),
        )

    def _han_frontier_from_candidates(
        self,
        raw_keys: str,
        candidates: list[Candidate],
    ) -> list[_HanSearchPath]:
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

    def _root_candidates(
        self,
        *,
        raw_keys: str,
        mode: NeuralLanguageMode,
        state: BackendState | None,
        response_epoch: int,
        allow_prewarm_cache: bool = True,
    ) -> tuple[list[Candidate], list[Any], str]:
        cached = (
            self._baseline_single_letter.get((raw_keys, mode))
            if state is None and allow_prewarm_cache
            else None
        )
        if cached is not None:
            candidates = [replace(candidate, context_epoch=response_epoch) for candidate in cached]
            han = [candidate for candidate in candidates if candidate.script == "han"]
            _, latin_frontier = self._root_latin_candidates_and_frontier(
                raw_keys,
                state,
                response_epoch,
            )
            return (
                candidates,
                [*self._han_frontier_from_candidates(raw_keys, han), *latin_frontier],
                "baseline",
            )

        han = self._root_han_candidates(raw_keys, state, response_epoch)
        latin, latin_frontier = self._root_latin_candidates_and_frontier(
            raw_keys,
            state,
            response_epoch,
        )
        score_source = "context" if state is not None else "baseline"

        if mode is NeuralLanguageMode.LATIN_FIRST:
            return sorted(latin, key=_latin_key), latin_frontier, score_source

        ordered_han = sorted(han, key=_candidate_key)
        ordered_latin = sorted(latin, key=_latin_key)
        ordered = self._merge_chinese_first(ordered_han, ordered_latin)
        frontier = [
            *self._han_frontier_from_candidates(raw_keys, ordered_han),
            *latin_frontier,
        ]
        if not ordered and not frontier and self._baseline_scores is not None:
            ordered = [_literal_candidate(raw_keys, response_epoch)]
        return ordered, frontier, score_source

    def _prune_frontier(self, session: _SearchSession) -> None:
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
        counts: dict[tuple[str, int], int] = {}
        for path in session.frontier:
            if self._path_key(path) in session.expanded_paths:
                continue
            bucket = (
                path.script,
                path.predicted_syllables if path.script == "han" else 0,
            )
            count = counts.get(bucket, 0)
            if count >= MAX_FRONTIER_PER_BUCKET:
                continue
            counts[bucket] = count + 1
            retained.append(path)
        session.frontier = retained

    def _han_edges_for(
        self,
        session: _SearchSession,
        parent: _HanSearchPath,
    ) -> dict[int, tuple[_HanEdge, ...]]:
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
            ):
                entry = match.entry
                if (
                    match.next_position <= parent.matched_letters
                    or entry.token_id is None
                    or int(entry.token_id) not in self._continuation_entries_by_token
                    or entry.coverage
                    or not is_all_han(entry.text)
                    or len(entry.text) > MAX_HAN_CHARACTERS
                ):
                    continue
                predicted = parent.predicted_syllables
                if match.next_position == len(parsed.compact):
                    predicted += match.completion_syllables
                grouped.setdefault(int(entry.token_id), []).append(
                    _HanEdge(
                        entry=entry,
                        matched_letters=match.next_position,
                        predicted_syllables=predicted,
                    )
                )
        else:
            for token_id, entries in self._continuation_entries_by_token.items():
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

    def _expand_han_constrained(
        self,
        session: _SearchSession,
        parent: _HanSearchPath,
        token_ids: tuple[int, ...],
        values: np.ndarray,
        edges_by_token: dict[int, tuple[_HanEdge, ...]],
        absolute_deadline: float,
    ) -> int:
        parsed = parse_raw_pinyin(session.identity.raw_keys)
        order = np.argsort(-values, kind="stable")
        progressed = 0
        bucket_counts: dict[int, int] = {}
        for position in order:
            token_id = token_ids[int(position)]
            token_score = float(values[int(position)])
            if not math.isfinite(token_score):
                continue
            for edge in edges_by_token[token_id]:
                entry = edge.entry
                text = parent.text + entry.text
                token_path = (*parent.token_path, token_id)
                if len(text) > MAX_HAN_CHARACTERS or len(token_path) > MAX_MODEL_TOKENS:
                    continue
                predicted = edge.predicted_syllables
                bucket_counts[predicted] = bucket_counts.get(predicted, 0) + 1
                if bucket_counts[predicted] > MAX_FRONTIER_PER_BUCKET:
                    continue
                pinyin_path = (*parent.pinyin_path, *entry.syllable_path)
                score = parent.score + token_score
                consumed_keys = parsed.raw_characters_for_letters(edge.matched_letters)
                completes_input = edge.matched_letters == len(parsed.compact)
                key = (unicodedata.normalize("NFKC", text), consumed_keys)
                if key not in session.seen_candidates:
                    session.seen_candidates.add(key)
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
                    progressed += 1
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
                    progressed += 1
            if progressed >= MAX_FRONTIER_PER_BUCKET:
                break
            if self.clock() >= absolute_deadline:
                break
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
        if not callable(continuation) or session.continuation_root is None:
            session.exhausted = True
            return 0

        self._prune_frontier(session)
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

    def _new_session(
        self,
        identity: _SearchIdentity,
        state: BackendState | None,
    ) -> _SearchSession:
        session = super()._new_session(identity, state)
        while len(self._sessions) > MAX_ACTIVE_SEARCH_SESSIONS:
            self._sessions.popitem(last=False)
        return session


__all__ = ["NeuralCandidatePageManager"]
