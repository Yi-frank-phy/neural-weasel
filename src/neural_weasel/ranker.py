from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .candidate import Candidate
from .index import IndexedPronunciation, PinyinIndex, PinyinQueryGroup
from .pinyin import parse_raw_pinyin


def _candidate(
    *,
    entry: IndexedPronunciation,
    parsed,
    raw: str,
    score: float | None,
    context_epoch: int,
) -> Candidate:
    consumed_letters = min(len(raw), len(entry.pinyin))
    return Candidate(
        text=entry.text,
        pinyin=entry.display_pinyin,
        consumed_keys=parsed.raw_characters_for_letters(consumed_letters),
        score=score,
        context_epoch=context_epoch,
        coverage=entry.coverage,
        completes_input=entry.pinyin == raw,
        syllables=entry.matched_syllables(len(raw)),
        token_id=entry.token_id,
    )


def _rank_model_group(
    group: PinyinQueryGroup,
    logits: np.ndarray,
    limit: int,
) -> list[tuple[IndexedPronunciation, float]]:
    assert group.token_ids is not None
    if group.token_ids.size == 0:
        return []
    if int(group.token_ids.max(initial=0)) >= logits.size:
        raise IndexError("token id is outside logits length")
    scores = logits[group.token_ids]
    selection_size = min(len(group.entries), max(limit * 8, 64))
    while True:
        if selection_size == len(group.entries):
            selected = np.arange(len(group.entries))
        else:
            selected = np.argpartition(-scores, selection_size - 1)[:selection_size]
        selected = selected[np.argsort(-scores[selected], kind="stable")]
        ranked = [
            (group.entries[int(position)], float(scores[int(position)])) for position in selected
        ]
        if selection_size == len(group.entries):
            return ranked
        # This exact top subset is normally ample for text de-duplication. The
        # caller can request a larger exact subset if duplicates consume it.
        if len({entry.text for entry, _ in ranked}) >= limit:
            return ranked
        selection_size = min(len(group.entries), selection_size * 2)


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

    score_vector = np.asarray(logits, dtype=np.float32)
    results: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for group in index.query_plan(parsed).groups:
        if group.token_ids is None:
            ranked_entries = ((entry, None) for entry in group.entries)
        else:
            ranked_entries = _rank_model_group(group, score_vector, limit)
        for entry, score in ranked_entries:
            if after_text.startswith(entry.text):
                continue
            candidate = _candidate(
                entry=entry,
                parsed=parsed,
                raw=raw,
                score=score,
                context_epoch=context_epoch,
            )
            key = (candidate.text, candidate.consumed_keys)
            if key in seen:
                continue
            seen.add(key)
            results.append(candidate)
            if len(results) >= limit:
                return results
    return results

