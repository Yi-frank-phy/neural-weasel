from __future__ import annotations

from neural_weasel.ranker import rank_candidates


def test_phrase_candidates_are_not_hidden_by_single_character_noise(make_index):
    """Regression guard for pinyin ranking quality."""
    index = make_index(
        {
            "时": ("shi",),
            "是": ("shi",),
            "时候": ("shi hou",),
        }
    )

    candidates = rank_candidates(
        index=index,
        raw_pinyin="shi",
        logits=[0.0] * 10,
        context_epoch=0,
        limit=5,
    )

    assert any(candidate.text == "时候" for candidate in candidates)
