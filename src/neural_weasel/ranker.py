from __future__ import annotations

import heapq
from collections.abc import Sequence

from .candidate import Candidate
from .index import PinyinIndex
from .pinyin import parse_raw_pinyin


def _logit_at(logits: Sequence[float], token_id: int | None) -> float | None:
    if token_id is None:
        return None
    if token_id < 0 or token_id >= len(logits):
        raise IndexError(f"token id {token_id} is outside logits length {len(logits)}")
    return float(logits[token_id])


def rank_candidates(
    *,
    index: PinyinIndex,
    raw_pinyin: str,
    logits: Sequence[float],
    context_epoch: int,
    limit: int = 5,
    after_text: str = "",
) -> list[Candidate]:
    parsed = parse_raw_pinyin(raw_pinyin)
    raw = parsed.compact
    if not raw:
        return []

    best: dict[tuple[str, int], Candidate] = {}
    for entry in index.compatible(parsed):
        if after_text.startswith(entry.text):
            continue
        consumed_letters = min(len(raw), len(entry.pinyin))
        consumed = parsed.raw_characters_for_letters(consumed_letters)
        candidate = Candidate(
            text=entry.text,
            pinyin=entry.display_pinyin,
            consumed_keys=consumed,
            score=_logit_at(logits, entry.token_id),
            context_epoch=context_epoch,
            coverage=entry.coverage,
            completes_input=entry.pinyin == raw,
            syllables=entry.syllables,
            token_id=entry.token_id,
        )
        key = (candidate.text, candidate.consumed_keys)
        previous = best.get(key)
        if previous is None or _sort_key(candidate) < _sort_key(previous):
            best[key] = candidate

    return heapq.nsmallest(limit, best.values(), key=_sort_key)


def _sort_key(candidate: Candidate) -> tuple[int, int, int, float, str]:
    return (
        0 if candidate.completes_input else 1,
        -candidate.syllables,
        1 if candidate.coverage else 0,
        -(candidate.score if candidate.score is not None else float("-inf")),
        candidate.text,
    )
