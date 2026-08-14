from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Beam:
    text: str
    syllables: tuple[str, ...]
    pos: int
    path: tuple[int, ...]
    score: float
    cost: float

    @property
    def model_score(self) -> float:
        return self.score / len(self.path) ** 0.7

    @property
    def rank(self) -> float:
        return self.model_score + self.cost


def run_beam(
    raw: str,
    matcher: Any,
    backend: Any,
    state: Any,
    width: int,
    depth: int,
    ms: float,
):
    roots = [
        match
        for match in matcher.partial_matches(raw, 0)
        if 0 < match.next_position < len(raw)
        and match.entry.token_id is not None
        and not match.entry.coverage
    ]
    if not roots:
        return []
    ids = [int(match.entry.token_id) for match in roots]
    scorer = getattr(backend, "score_allowed_sequence_start", None)
    scores = scorer(state, ids) if callable(scorer) else backend.score_allowed_tokens(state, ids)
    active = [
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
    active = sorted(active, key=_key)[:width]
    started = time.perf_counter()
    open_session = getattr(backend, "start_conditional_continuation", None)
    session = open_session(state) if callable(open_session) else None
    if session is None:
        return []
    session.advance([0] * len(active), [beam.path[-1] for beam in active])
    if _late(started, ms):
        return []

    done = []
    for level in range(2, depth + 1):
        groups = [
            [
                match
                for match in matcher.partial_matches(raw, beam.pos)
                if match.entry.token_id is not None and not match.entry.coverage
            ]
            for beam in active
        ]
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
            break
        session.advance(
            [parent for parent, _ in selected],
            [beam.path[-1] for _, beam in selected],
        )
        active = [beam for _, beam in selected]
        if _late(started, ms):
            break

    best = {}
    for beam in done:
        if beam.text not in best or beam.rank > best[beam.text].rank:
            best[beam.text] = beam
    return sorted(best.values(), key=_key)


def _key(beam: Beam):
    return (-beam.rank, beam.text, beam.path)


def _late(start: float, ms: float) -> bool:
    return (time.perf_counter() - start) * 1000 >= ms
