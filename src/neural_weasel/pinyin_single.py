from __future__ import annotations

from .candidate import Candidate
from .unified import detect_script


def single_partial(raw_keys, raw, matcher, backend, state, after_text):
    matches = [
        match
        for match in matcher.partial_matches(raw, 0)
        if match.next_position == len(raw)
        and match.entry.token_id is not None
        and not match.entry.coverage
        and not after_text.startswith(match.entry.text)
    ]
    if not matches:
        return []
    ids = [int(match.entry.token_id) for match in matches]
    scores = backend.score_allowed_tokens(state, ids)
    result = []
    for match, score in zip(matches, scores, strict=True):
        entry = match.entry
        value = float(score)
        result.append(
            Candidate(
                text=entry.text,
                pinyin=entry.display_pinyin,
                consumed_keys=len(raw_keys),
                score=value,
                context_epoch=state.epoch,
                coverage=False,
                completes_input=True,
                syllables=entry.syllables,
                token_id=entry.token_id,
                constraint_kind="pinyin",
                script=detect_script(entry.text),
                model_score=value,
                constraint_cost=match.cost,
                token_path=(int(entry.token_id),),
                fuzzy_cost=(
                    match.shorthand + int(match.incomplete_final) + match.completion_syllables
                ),
            )
        )
    result.sort(
        key=lambda item: (
            -(float(item.model_score) + item.constraint_cost),
            item.text,
            item.token_path,
        )
    )
    return result
