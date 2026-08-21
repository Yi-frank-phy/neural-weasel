from __future__ import annotations

from neural_weasel.ranker import rank_candidates


def test_phrase_candidates_are_not_hidden_by_single_character_noise(make_index):
    """Regression guard for pinyin ranking quality."""
    index = make_index(
        [
            (1, "时", "shi", 1, 0),
            (2, "是", "shi", 1, 0),
            (3, "事", "shi", 1, 0),
            (4, "市", "shi", 1, 0),
            (5, "使", "shi", 1, 0),
            (6, "式", "shi", 1, 0),
            (7, "时候", "shihou", 2, 0),
        ]
    )

    candidates = rank_candidates(
        index=index,
        raw_pinyin="shi",
        logits=[0.0] * 8,
        context_epoch=0,
        limit=5,
    )

    phrase = next(candidate for candidate in candidates if candidate.text == "时候")
    assert phrase.score == 0.0
