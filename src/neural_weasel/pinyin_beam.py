from __future__ import annotations

import time
from dataclasses import dataclass, replace
from functools import cache
from typing import Any

_INLINE_INTERJECTION_SYLLABLES = frozenset({"m", "n", "ng", "hm", "hng"})


@dataclass(frozen=True, slots=True)
class Beam:
    text: str
    syllables: tuple[str, ...]
    pos: int
    path: tuple[int, ...]
    score: float
    cost: float
    selection_score: float | None = None

    @property
    def model_score(self) -> float:
        return self.score / len(self.path) ** 0.7

    @property
    def rank(self) -> float:
        return self.model_score + self.cost

    @property
    def candidate_score(self) -> float:
        return self.model_score if self.selection_score is None else self.selection_score


def run_beam(
    raw: str,
    matcher: Any,
    backend: Any,
    state: Any,
    width: int,
    depth: int,
    ms: float,
):
    can_finish_exact = _exact_finish_checker(raw, matcher)
    all_roots = [
        match
        for match in matcher.partial_matches(raw, 0)
        if 0 < match.next_position < len(raw)
        and match.entry.token_id is not None
        and not match.entry.coverage
    ]
    exact_roots = [
        match
        for match in all_roots
        if match.cost == 0 and can_finish_exact(match.next_position, depth - 1)
    ]
    exact_mode = bool(exact_roots)
    roots = exact_roots if exact_mode else all_roots
    if not roots:
        return []
    ids = [int(match.entry.token_id) for match in roots]
    scorer = getattr(backend, "score_allowed_sequence_start", None)
    scores = scorer(state, ids) if callable(scorer) else backend.score_allowed_tokens(state, ids)
    roots_beams = [
        Beam(
            match.entry.text,
            match.entry.syllable_path,
            match.next_position,
            (int(match.entry.token_id),),
            float(score),
            match.cost,
        )
        for match, score in zip(roots, scores, strict=True)
    ]
    active = sorted(roots_beams, key=_key)[:width]
    snapshot_roots = tuple(
        sorted(roots_beams, key=lambda beam: _snapshot_frontier_key(beam, matcher))[:width]
    )
    started = time.perf_counter()
    budget_check = getattr(backend, "conditional_continuation_within_budget", None)
    if callable(budget_check) and not budget_check(state, ms):
        return _snapshot_beam(
            raw,
            matcher,
            backend,
            state,
            snapshot_roots,
            width,
            depth,
            ms,
            exact_mode=exact_mode,
            can_finish_exact=can_finish_exact,
        )
    open_session = getattr(backend, "start_conditional_continuation", None)
    session = open_session(state) if callable(open_session) else None
    if session is None:
        return _snapshot_beam(
            raw,
            matcher,
            backend,
            state,
            snapshot_roots,
            width,
            depth,
            ms,
            exact_mode=exact_mode,
            can_finish_exact=can_finish_exact,
        )
    advance_ms = session.advance([0] * len(active), [beam.path[-1] for beam in active])
    _record_conditional_latency(backend, state, advance_ms)
    if advance_ms >= ms or _late(started, ms):
        return _snapshot_beam(
            raw,
            matcher,
            backend,
            state,
            snapshot_roots,
            width,
            depth,
            ms,
            exact_mode=exact_mode,
            can_finish_exact=can_finish_exact,
        )

    done = []
    for level in range(2, depth + 1):
        groups = []
        for beam in active:
            group = [
                match
                for match in matcher.partial_matches(raw, beam.pos)
                if match.entry.token_id is not None and not match.entry.coverage
            ]
            if exact_mode:
                group = [
                    match
                    for match in group
                    if _is_exact_token_match(match, beam.pos)
                    and can_finish_exact(match.next_position, depth - level)
                ]
            groups.append(group)
        allowed = [[int(match.entry.token_id) for match in group] for group in groups]
        if not any(allowed):
            break
        scored = session.score_allowed(allowed)
        pending = []
        for parent, (beam, group, values) in enumerate(zip(active, groups, scored, strict=True)):
            for match, value in zip(group, values, strict=True):
                entry = match.entry
                child = Beam(
                    beam.text + entry.text,
                    beam.syllables + entry.syllable_path,
                    match.next_position,
                    beam.path + (int(entry.token_id),),
                    beam.score + float(value),
                    beam.cost + match.cost,
                )
                if child.pos == len(raw):
                    done.append(child)
                elif child.pos < len(raw):
                    pending.append((parent, child))
        if level == depth or not pending:
            break
        selected = sorted(pending, key=lambda item: _key(item[1]))[:width]
        if _late(started, ms):
            _record_conditional_latency(backend, state, _elapsed_ms(started))
            return _snapshot_beam(
                raw,
                matcher,
                backend,
                state,
                snapshot_roots,
                width,
                depth,
                ms,
                exact_mode=exact_mode,
                can_finish_exact=can_finish_exact,
            )
        advance_ms = session.advance(
            [parent for parent, _ in selected],
            [beam.path[-1] for _, beam in selected],
        )
        _record_conditional_latency(backend, state, advance_ms)
        active = [beam for _, beam in selected]
        if advance_ms >= ms or _late(started, ms):
            return _snapshot_beam(
                raw,
                matcher,
                backend,
                state,
                snapshot_roots,
                width,
                depth,
                ms,
                exact_mode=exact_mode,
                can_finish_exact=can_finish_exact,
            )

    if _late(started, ms):
        _record_conditional_latency(backend, state, _elapsed_ms(started))
        return _snapshot_beam(raw, matcher, backend, state, snapshot_roots, width, depth, ms)

    best = {}
    for beam in done:
        if beam.text not in best or beam.rank > best[beam.text].rank:
            best[beam.text] = beam
    return sorted(best.values(), key=_key)


def _snapshot_beam(
    raw,
    matcher,
    backend,
    state,
    active,
    width,
    depth,
    ms,
    *,
    exact_mode=False,
    can_finish_exact=None,
):
    """Complete a bounded path using only the immutable context snapshot.

    This is the latency-safe fallback when target hardware cannot finish one
    conditional model step inside the key-path budget. It preserves pinyin
    legality, then uses tokenizer vocabulary ranks as a bounded lexical prior
    without starting another GPU forward.
    """

    done = []
    scorer = getattr(backend, "score_allowed_sequence_start", None)
    score = scorer if callable(scorer) else backend.score_allowed_tokens
    started = time.perf_counter()
    start_level = min(len(beam.path) for beam in active) + 1
    match_cache = {}
    for level in range(start_level, depth + 1):
        groups = []
        for beam in active:
            if beam.pos not in match_cache:
                match_cache[beam.pos] = _bounded_snapshot_matches(
                    raw,
                    matcher,
                    beam.pos,
                    max(16, width * 8),
                    exact_mode=exact_mode,
                    can_finish_exact=can_finish_exact,
                    remaining_tokens=depth - level,
                )
            groups.append(match_cache[beam.pos])
        flattened = [int(match.entry.token_id) for group in groups for match in group]
        if not flattened:
            break
        values = score(state, flattened)
        pending = []
        offset = 0
        for parent, (beam, group) in enumerate(zip(active, groups, strict=True)):
            group_values = values[offset : offset + len(group)]
            offset += len(group)
            for match, value in zip(group, group_values, strict=True):
                entry = match.entry
                child = Beam(
                    beam.text + entry.text,
                    beam.syllables + entry.syllable_path,
                    match.next_position,
                    beam.path + (int(entry.token_id),),
                    beam.score + float(value),
                    beam.cost + match.cost,
                )
                if child.pos == len(raw):
                    done.append(child)
                elif child.pos < len(raw):
                    pending.append((parent, child))
        if level == depth or not pending:
            break
        active = [beam for _, beam in _select_snapshot_frontier(pending, width, matcher)]
        if _late(started, ms):
            break

    best = {}
    for beam in done:
        if beam.text not in best or _snapshot_key(beam, matcher) < _snapshot_key(
            best[beam.text], matcher
        ):
            best[beam.text] = beam
    ordered = sorted(best.values(), key=lambda beam: _snapshot_key(beam, matcher))
    # Candidate.score is the pipeline's selection score; model_score remains
    # available separately for diagnostics. Cancel the structural cost here so
    # the unified ranker preserves this already-ordered fallback list exactly.
    return [
        replace(beam, selection_score=-float(index) - beam.cost)
        for index, beam in enumerate(ordered)
    ]


def _key(beam: Beam):
    return (-beam.rank, beam.text, beam.path)


def _snapshot_frontier_key(beam: Beam, matcher: Any):
    snapshot_key = _snapshot_key(beam, matcher)
    return (snapshot_key[0], snapshot_key[1], -beam.pos, *snapshot_key[2:])


def _bounded_snapshot_matches(
    raw: str,
    matcher: Any,
    pos: int,
    limit: int,
    *,
    exact_mode: bool = False,
    can_finish_exact=None,
    remaining_tokens: int = 0,
):
    matches = [
        match
        for match in matcher.partial_matches(raw, pos)
        if match.entry.token_id is not None and not match.entry.coverage
    ]
    if exact_mode:
        matches = [
            match
            for match in matches
            if _is_exact_token_match(match, pos)
            and can_finish_exact is not None
            and can_finish_exact(match.next_position, remaining_tokens)
        ]

    def key(match):
        character_rank = getattr(matcher, "character_token_rank", lambda text: None)(
            match.entry.text
        )
        lexical_rank = character_rank if character_rank is not None else int(match.entry.token_id)
        return (
            -match.next_position,
            -match.cost,
            lexical_rank,
            int(match.entry.token_id),
            match.entry.text,
        )

    return sorted(matches, key=key)[:limit]


def exact_token_path_exists(
    raw: str,
    matcher: Any,
    max_tokens: int,
    *,
    min_tokens: int = 1,
) -> bool:
    """Return whether model tokens can consume all keys without relaxation."""

    if not raw or max_tokens < min_tokens or min_tokens < 1:
        return False
    positions = {0}
    for token_count in range(1, max_tokens + 1):
        next_positions: set[int] = set()
        for position in positions:
            for match in _exact_token_matches(raw, matcher, position):
                if match.next_position == len(raw) and token_count >= min_tokens:
                    return True
                if match.next_position < len(raw):
                    next_positions.add(match.next_position)
        positions = next_positions
        if not positions:
            break
    return False


def _exact_finish_checker(raw: str, matcher: Any):
    @cache
    def can_finish(position: int, remaining_tokens: int) -> bool:
        if position == len(raw):
            return True
        if remaining_tokens <= 0:
            return False
        return any(
            can_finish(match.next_position, remaining_tokens - 1)
            for match in _exact_token_matches(raw, matcher, position)
        )

    return can_finish


def _exact_token_matches(raw: str, matcher: Any, position: int):
    return (
        match
        for match in matcher.partial_matches(raw, position)
        if _is_exact_token_match(match, position)
    )


def _is_exact_token_match(match: Any, position: int) -> bool:
    return bool(
        match.cost == 0
        and match.next_position > position
        and match.entry.token_id is not None
        and not match.entry.coverage
        and not (
            position > 0
            and match.entry.syllable_path
            and match.entry.syllable_path[0] in _INLINE_INTERJECTION_SYLLABLES
        )
    )


def _select_snapshot_frontier(pending, width: int, matcher: Any):
    ordered = sorted(pending, key=lambda item: _snapshot_frontier_key(item[1], matcher))
    selected_indices = []
    roots = set()
    for index, (_, beam) in enumerate(ordered):
        root = beam.path[0]
        if root in roots:
            continue
        selected_indices.append(index)
        roots.add(root)
        if len(selected_indices) == width:
            break
    if len(selected_indices) < width:
        chosen = set(selected_indices)
        selected_indices.extend(index for index in range(len(ordered)) if index not in chosen)
    return [ordered[index] for index in selected_indices[:width]]


def _snapshot_key(beam: Beam, matcher: Any):
    path_rank = sum(beam.path) / len(beam.path)
    lexical_rank = path_rank
    if beam.cost < 0:
        character_rank = getattr(matcher, "character_token_rank", lambda text: None)(beam.text)
        if character_rank is not None:
            lexical_rank = character_rank
    return (-beam.cost, len(beam.path), lexical_rank, beam.path, -beam.model_score, beam.text)


def _late(start: float, ms: float) -> bool:
    return _elapsed_ms(start) >= ms


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _record_conditional_latency(backend: Any, state: Any, latency_ms: float) -> None:
    record = getattr(backend, "record_conditional_continuation_latency", None)
    if callable(record):
        record(state, latency_ms)
