from __future__ import annotations

import math
import unicodedata
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from .backends import BackendState
from .candidate import Candidate
from .neural_candidate_pages_v3 import (
    NeuralCandidatePageManager as _V3CandidatePageManager,
)
from .neural_candidates import (
    MAX_FROZEN_CANDIDATES,
    NeuralLanguageMode,
    _candidate_key,
    _latin_key,
    _SearchSession,
)

_MAX_BASELINE_HAN_CACHE = 512


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
        output[positive_infinity[ids]] = np.float32(
            -math.log(int(positive_infinity.sum()))
        )
        return output

    finite = np.isfinite(values)
    if not finite.any():
        return np.full(ids.size, -math.inf, dtype=np.float32)
    maximum = float(values[finite].max())
    log_normalizer = maximum + math.log(
        float(np.exp(values[finite] - maximum).sum())
    )
    return np.asarray(values[ids] - log_normalizer, dtype=np.float32)


class NeuralCandidatePageManager(_V3CandidatePageManager):
    """Complete the PR36 model-score and baseline-supplement contracts.

    The constrained beam already defines a path score as the sum of normalized
    next-token log probabilities, with no token-count division. Root logits and
    every continuation step here use that same definition. Strict Han length
    buckets remain the primary ordering key; model probability only orders paths
    inside a proven bucket.

    Multi-token Han paths discovered from the permanent empty-context root are
    retained only for the exact raw pinyin that proved them legal. They can then
    supplement a later page 0 without another model forward. Editor-context paths
    are revision-local and are never copied into this cache.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_model_token_ids: tuple[int, ...] = ()
        self._baseline_han_cache: OrderedDict[
            tuple[str, str, tuple[int, ...]], Candidate
        ] = OrderedDict()

    def install_baseline_scores(
        self,
        scores: Sequence[float],
        *,
        continuation_root: Any | None = None,
    ) -> None:
        values = np.asarray(scores).reshape(-1)
        self._all_model_token_ids = tuple(range(int(values.size)))
        self._baseline_han_cache.clear()
        super().install_baseline_scores(scores, continuation_root=continuation_root)

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
        if len(normalized_raw) == 1:
            for mode in NeuralLanguageMode:
                self._baseline_single_letter.pop((normalized_raw, mode), None)
        while len(self._baseline_han_cache) > _MAX_BASELINE_HAN_CACHE:
            self._baseline_han_cache.popitem(last=False)

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
        if state is not None or mode is NeuralLanguageMode.LATIN_FIRST:
            return candidates, frontier, score_source

        cached_han = self._cached_han_for_raw(raw_keys, response_epoch)
        if not cached_han:
            return candidates, frontier, score_source

        han = [candidate for candidate in candidates if candidate.script == "han"]
        latin = [candidate for candidate in candidates if candidate.script == "latin"]
        other = [
            candidate
            for candidate in candidates
            if candidate.script not in {"han", "latin"}
            and candidate.constraint_kind != "literal"
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
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent_key)
            return 0

        # Ask the exact-root runtime for the complete next-token distribution.
        # Normalizing only over pinyin/Latin-legal tokens would score a different
        # constrained model rather than the Base model required by PR36.
        scored = continuation(
            session.continuation_root,
            [parent.token_path],
            [self._all_model_token_ids],
            deadline_ms=remaining_ms,
        )
        if scored is None:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent_key)
            return 0
        if len(scored) != 1:
            raise RuntimeError("continuation scorer returned an invalid batch")
        full_logits = np.asarray(scored[0], dtype=np.float32)
        if full_logits.size != len(self._all_model_token_ids):
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

        session.search_depth = max(session.search_depth, len(parent.token_path) + 1)
        self._prune_frontier(session)
        if not session.frontier:
            session.exhausted = True
        return progressed


__all__ = ["NeuralCandidatePageManager", "_selected_log_probs"]
