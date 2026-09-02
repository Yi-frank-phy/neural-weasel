from __future__ import annotations

import math
import re
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from .backends import BackendState
from .candidate import Candidate
from .neural_candidates import (
    MAX_ACTIVE_SEARCH_SESSIONS,
    MAX_FRONTIER_PER_BUCKET,
    MAX_FROZEN_CANDIDATES,
    MAX_HAN_CHARACTERS,
    MAX_MODEL_TOKENS,
    CandidatePage,
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _candidate_key,
    _latin_key,
    _literal_candidate,
    _page_candidate_id,
    _SearchIdentity,
    _SearchPath,
    _SearchSession,
)
from .neural_candidates import (
    NeuralCandidatePageManager as _BaseCandidatePageManager,
)

_MAX_LATIN_CHARACTERS = 64
_MAX_BASELINE_LATIN_CACHE = 512
_LATIN_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9.'-]*$")


class NeuralCandidatePageManager(_BaseCandidatePageManager):
    """PR36 candidate pager with a true multi-token Base-model Latin graph.

    The base implementation supplies the revision protocol, strict Han length
    buckets and exact-root continuation seam. This class extends that same
    resumable search session to Latin token paths while preserving tokenizer
    word boundaries. No Latin candidate is synthesized from a dictionary or
    ranked by static frequency; every cached multi-token supplement is a path
    previously scored from the permanent Base-model root.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        raw_fragments = getattr(self.latin_constraint, "continuation_fragments", {})
        self._latin_fragments_by_token: dict[int, str] = {
            int(token_id): str(fragment)
            for token_id, fragment in dict(raw_fragments).items()
            if fragment
            and len(str(fragment)) <= _MAX_LATIN_CHARACTERS
            and _LATIN_PATH.fullmatch(str(fragment)) is not None
        }
        self._latin_continuation_token_ids = tuple(sorted(self._latin_fragments_by_token))
        self._baseline_latin_cache: OrderedDict[tuple[str, tuple[int, ...]], Candidate] = (
            OrderedDict()
        )

    def install_baseline_scores(
        self,
        scores: Sequence[float],
        *,
        continuation_root: Any | None = None,
    ) -> None:
        self._baseline_latin_cache.clear()
        super().install_baseline_scores(scores, continuation_root=continuation_root)

    @staticmethod
    def _prefix_comparable(raw_keys: str, text: str) -> bool:
        raw = raw_keys.casefold()
        value = text.casefold()
        return value.startswith(raw) or raw.startswith(value)

    def _root_latin_candidates_and_frontier(
        self,
        raw_keys: str,
        state: BackendState | None,
        response_epoch: int,
    ) -> tuple[list[Candidate], list[_SearchPath]]:
        if _LATIN_PATH.fullmatch(raw_keys) is None:
            return [], []

        compatible = [
            completion
            for completion in self.latin_constraint.completions
            if completion.token_path
            and len(completion.token_path) == 1
            and self._prefix_comparable(raw_keys, completion.text)
            and len(completion.text) <= _MAX_LATIN_CHARACTERS
            and _LATIN_PATH.fullmatch(completion.text) is not None
        ]
        token_ids = [int(completion.token_path[0]) for completion in compatible]
        scores = self._score_root(state, token_ids)

        candidates: list[Candidate] = []
        frontier: list[_SearchPath] = []
        seen_frontier: set[tuple[int, ...]] = set()
        for completion, score in zip(compatible, scores, strict=True):
            value = float(score)
            if not math.isfinite(value):
                continue
            token_path = (int(completion.token_path[0]),)
            text = completion.text
            if text.casefold().startswith(raw_keys.casefold()):
                candidates.append(
                    Candidate(
                        text=text,
                        pinyin="",
                        consumed_keys=len(raw_keys),
                        score=value,
                        context_epoch=response_epoch,
                        coverage=False,
                        completes_input=text.casefold() == raw_keys.casefold(),
                        syllables=0,
                        token_id=token_path[0],
                        constraint_kind="latin_prefix",
                        script="latin",
                        model_score=value,
                        total_score=value,
                        token_path=token_path,
                        predicted_syllables=0,
                    )
                )
            if token_path not in seen_frontier and len(text) < _MAX_LATIN_CHARACTERS:
                seen_frontier.add(token_path)
                frontier.append(
                    _SearchPath(
                        text=text,
                        pinyin_path=(),
                        token_path=token_path,
                        score=value,
                        predicted_syllables=0,
                        script="latin",
                    )
                )

        # Only permanent-baseline paths can be reused across composition
        # revisions. Contextual paths remain revision-local because their score
        # origin is the accepted editor snapshot.
        if state is None:
            for cached in self._baseline_latin_cache.values():
                if not cached.text.casefold().startswith(raw_keys.casefold()):
                    continue
                candidate = replace(cached, context_epoch=response_epoch)
                candidates.append(candidate)
                if (
                    candidate.token_path
                    and len(candidate.token_path) < MAX_MODEL_TOKENS
                    and len(candidate.text) < _MAX_LATIN_CHARACTERS
                    and candidate.token_path not in seen_frontier
                ):
                    seen_frontier.add(candidate.token_path)
                    frontier.append(
                        _SearchPath(
                            text=candidate.text,
                            pinyin_path=(),
                            token_path=candidate.token_path,
                            score=float(candidate.model_score or 0.0),
                            predicted_syllables=0,
                            script="latin",
                        )
                    )

        best: dict[str, Candidate] = {}
        for candidate in candidates:
            key = unicodedata.normalize("NFKC", candidate.text).casefold()
            previous = best.get(key)
            if previous is None or _latin_key(candidate) < _latin_key(previous):
                best[key] = candidate
        return list(best.values()), frontier

    def _root_candidates(
        self,
        *,
        raw_keys: str,
        mode: NeuralLanguageMode,
        state: BackendState | None,
        response_epoch: int,
        allow_prewarm_cache: bool = True,
    ) -> tuple[list[Candidate], list[_SearchPath], str]:
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
                [*self._frontier_from_candidates(han), *latin_frontier],
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
        frontier = [*self._frontier_from_candidates(ordered_han), *latin_frontier]
        if not ordered and not frontier and self._baseline_scores is not None:
            ordered = [_literal_candidate(raw_keys, response_epoch)]
        return ordered, frontier, score_source

    def _new_session(self, identity: _SearchIdentity, state: BackendState | None) -> _SearchSession:
        for candidate_set_id, old in tuple(self._sessions.items()):
            if (
                old.identity.client_session_id == identity.client_session_id
                and old.identity != identity
            ):
                del self._sessions[candidate_set_id]

        score_state, continuation_root, score_source = self._select_score_origin(state)
        root_candidates, frontier, _ = self._root_candidates(
            raw_keys=identity.raw_keys,
            mode=identity.mode,
            state=score_state,
            response_epoch=identity.context_epoch,
        )
        if identity.mode is NeuralLanguageMode.LATIN_FIRST:
            root_candidates = root_candidates[:5]
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
            exhausted=(not frontier or not callable(continuation) or continuation_root is None),
        )
        self._sessions[candidate_set_id] = session
        self._sessions.move_to_end(candidate_set_id)
        while len(self._sessions) > MAX_ACTIVE_SEARCH_SESSIONS:
            self._sessions.popitem(last=False)
        return session

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
        retained: list[_SearchPath] = []
        counts: dict[tuple[str, int], int] = {}
        for path in session.frontier:
            if path.token_path in session.expanded_paths:
                continue
            bucket = (path.script, path.predicted_syllables if path.script == "han" else 0)
            count = counts.get(bucket, 0)
            if count >= MAX_FRONTIER_PER_BUCKET:
                continue
            counts[bucket] = count + 1
            retained.append(path)
        session.frontier = retained

    def _sort_pending(self, session: _SearchSession) -> None:
        if session.identity.mode is NeuralLanguageMode.LATIN_FIRST:
            session.pending.sort(key=_latin_key)
            return
        han = sorted(
            (candidate for candidate in session.pending if candidate.script == "han"),
            key=_candidate_key,
        )
        latin = sorted(
            (candidate for candidate in session.pending if candidate.script == "latin"),
            key=_latin_key,
        )
        other = [
            candidate for candidate in session.pending if candidate.script not in {"han", "latin"}
        ]
        session.pending = [*self._merge_chinese_first(han, latin), *other]

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
            progressed = self._expand_one_frontier(session, absolute_deadline)
            self._sort_pending(session)
            if progressed == 0:
                return

    def _allowed_tokens_for(self, path: _SearchPath) -> tuple[int, ...]:
        if path.script == "han":
            return self._continuation_token_ids
        if path.script == "latin":
            return self._latin_continuation_token_ids
        return ()

    def _remember_baseline_latin_candidate(self, candidate: Candidate) -> None:
        normalized = unicodedata.normalize("NFKC", candidate.text).casefold()
        key = (normalized, candidate.token_path)
        previous = self._baseline_latin_cache.get(key)
        changed = previous is None or _latin_key(candidate) < _latin_key(previous)
        if changed:
            self._baseline_latin_cache[key] = replace(candidate, context_epoch=0)
            self._baseline_latin_cache.move_to_end(key)
            if normalized:
                first = normalized[0]
                for mode in NeuralLanguageMode:
                    self._baseline_single_letter.pop((first, mode), None)
        while len(self._baseline_latin_cache) > _MAX_BASELINE_LATIN_CACHE:
            self._baseline_latin_cache.popitem(last=False)

    def _expand_han(
        self,
        session: _SearchSession,
        parent: _SearchPath,
        token_ids: tuple[int, ...],
        values: np.ndarray,
        absolute_deadline: float,
    ) -> int:
        order = np.argsort(-values, kind="stable")
        progressed = 0
        bucket_counts: dict[int, int] = {}
        for position in order:
            token_id = token_ids[int(position)]
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
                    progressed += 1
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
                    progressed += 1
            if progressed >= MAX_FRONTIER_PER_BUCKET:
                break
            if self.clock() >= absolute_deadline:
                break
        return progressed

    def _expand_latin(
        self,
        session: _SearchSession,
        parent: _SearchPath,
        token_ids: tuple[int, ...],
        values: np.ndarray,
        absolute_deadline: float,
    ) -> int:
        order = np.argsort(-values, kind="stable")
        raw_folded = session.identity.raw_keys.casefold()
        progressed = 0
        emitted = 0
        for position in order:
            token_id = token_ids[int(position)]
            token_score = float(values[int(position)])
            if not math.isfinite(token_score):
                continue
            fragment = self._latin_fragments_by_token[token_id]
            text = parent.text + fragment
            token_path = (*parent.token_path, token_id)
            if (
                len(text) > _MAX_LATIN_CHARACTERS
                or len(token_path) > MAX_MODEL_TOKENS
                or _LATIN_PATH.fullmatch(text) is None
                or not self._prefix_comparable(session.identity.raw_keys, text)
            ):
                continue
            score = parent.score + token_score
            text_folded = text.casefold()
            if text_folded.startswith(raw_folded):
                key = (unicodedata.normalize("NFKC", text), len(session.identity.raw_keys))
                if key not in session.seen_candidates:
                    session.seen_candidates.add(key)
                    candidate = Candidate(
                        text=text,
                        pinyin="",
                        consumed_keys=len(session.identity.raw_keys),
                        score=score,
                        context_epoch=session.identity.context_epoch,
                        coverage=False,
                        completes_input=text_folded == raw_folded,
                        syllables=0,
                        token_id=token_path[0],
                        constraint_kind="latin_prefix",
                        script="latin",
                        model_score=score,
                        total_score=score,
                        token_path=token_path,
                        predicted_syllables=0,
                    )
                    session.pending.append(candidate)
                    if session.score_source == "baseline":
                        self._remember_baseline_latin_candidate(candidate)
                    emitted += 1
                    progressed += 1
            if len(token_path) < MAX_MODEL_TOKENS and len(text) < _MAX_LATIN_CHARACTERS:
                session.frontier.append(
                    _SearchPath(
                        text=text,
                        pinyin_path=(),
                        token_path=token_path,
                        score=score,
                        predicted_syllables=0,
                        script="latin",
                    )
                )
                progressed += 1
            if emitted >= MAX_FRONTIER_PER_BUCKET:
                break
            if self.clock() >= absolute_deadline:
                break
        return progressed

    def _expand_one_frontier(self, session: _SearchSession, absolute_deadline: float) -> int:
        if not session.frontier:
            session.exhausted = True
            return 0
        continuation = getattr(self.backend, "continue_from_root", None)
        if not callable(continuation) or session.continuation_root is None:
            session.exhausted = True
            return 0

        self._prune_frontier(session)
        parent: _SearchPath | None = None
        token_ids: tuple[int, ...] = ()
        while session.frontier:
            candidate = session.frontier.pop(0)
            if candidate.token_path in session.expanded_paths:
                continue
            allowed = self._allowed_tokens_for(candidate)
            session.expanded_paths.add(candidate.token_path)
            if not allowed:
                continue
            parent = candidate
            token_ids = allowed
            break
        if parent is None:
            session.exhausted = True
            return 0

        remaining_ms = max(0.0, (absolute_deadline - self.clock()) * 1000.0)
        if remaining_ms <= 0:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent.token_path)
            return 0
        scored = continuation(
            session.continuation_root,
            [parent.token_path],
            [token_ids],
            deadline_ms=remaining_ms,
        )
        if scored is None:
            session.frontier.insert(0, parent)
            session.expanded_paths.discard(parent.token_path)
            return 0
        if len(scored) != 1:
            raise RuntimeError("continuation scorer returned an invalid batch")
        values = np.asarray(scored[0], dtype=np.float32)
        if values.size != len(token_ids):
            raise RuntimeError("continuation scorer returned an invalid score vector")

        if parent.script == "han":
            progressed = self._expand_han(
                session,
                parent,
                token_ids,
                values,
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
        # A legal multi-token frontier is not model silence. Page 0 may be empty
        # rather than fabricating a literal while the resumable path remains
        # available for a later bounded page request/cache fill.
        if not selected and session.exhausted:
            selected = [
                _literal_candidate(
                    session.identity.raw_keys,
                    session.identity.context_epoch,
                )
            ]

        total_frozen = sum(len(page.candidates) for page in session.frozen_pages.values())
        remaining_capacity = max(0, MAX_FROZEN_CANDIDATES - total_frozen)
        selected = selected[:remaining_capacity]
        if not selected and session.exhausted:
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
