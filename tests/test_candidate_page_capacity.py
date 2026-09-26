from __future__ import annotations

from types import SimpleNamespace

import pytest

from neural_weasel.candidate import Candidate
from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import (
    CHINESE_CANDIDATE_COUNT,
    CHINESE_PAGE_SIZE,
    CandidatePageError,
    NeuralLanguageMode,
)


def _candidate(index: int) -> Candidate:
    return Candidate(
        text=f"candidate-{index}",
        pinyin="",
        consumed_keys=1,
        score=-float(index),
        context_epoch=0,
        coverage=False,
        completes_input=True,
        syllables=0,
        token_id=index + 1,
        constraint_kind="latin_prefix",
        script="latin",
        model_score=-float(index),
        total_score=-float(index),
        token_path=(index + 1,),
    )


def _manager():
    return SimpleNamespace(
        _ensure_freezable=lambda session, page_size, deadline: None,
        _freezable_candidates=lambda session: list(session.pending),
        _uses_fixed_chinese_capacity=lambda session: (
            session.identity.mode is NeuralLanguageMode.CHINESE_FIRST
            and 0 in session.frozen_pages
        ),
    )


def _chinese_session(*, pending, frozen_pages, exhausted=False):
    return SimpleNamespace(
        candidate_set_id="fixed-chinese-capacity",
        identity=SimpleNamespace(mode=NeuralLanguageMode.CHINESE_FIRST),
        pending=list(pending),
        exhausted=exhausted,
        frozen_pages=dict(frozen_pages),
        score_source="baseline",
        search_depth=1,
        timeout_count=0,
    )


def test_chinese_capacity_rejects_sixth_page_before_search_or_mutation() -> None:
    pending = [_candidate(index) for index in range(9)]
    session = _chinese_session(
        pending=pending,
        frozen_pages={
            page_index: SimpleNamespace(candidates=tuple(range(CHINESE_PAGE_SIZE)))
            for page_index in range(CHINESE_CANDIDATE_COUNT // CHINESE_PAGE_SIZE)
        },
    )

    with pytest.raises(CandidatePageError, match="frozen-candidate safety limit"):
        NeuralCandidatePageManager._freeze_next_page(
            _manager(),
            session,
            page_index=5,
            page_size=CHINESE_PAGE_SIZE,
            absolute_deadline=10.0,
        )

    assert session.pending == pending
    assert 5 not in session.frozen_pages


def test_chinese_fifth_page_is_full_and_ends_at_exactly_35_candidates() -> None:
    pending = [_candidate(index) for index in range(20)]
    session = _chinese_session(
        pending=pending,
        frozen_pages={
            page_index: SimpleNamespace(candidates=tuple(range(CHINESE_PAGE_SIZE)))
            for page_index in range(4)
        },
    )

    page = NeuralCandidatePageManager._freeze_next_page(
        _manager(),
        session,
        page_index=4,
        page_size=CHINESE_PAGE_SIZE,
        absolute_deadline=10.0,
    )

    assert page.candidates == tuple(pending[:CHINESE_PAGE_SIZE])
    assert page.has_more is False
    assert session.pending == pending[CHINESE_PAGE_SIZE:]
    assert len(page.candidate_ids) == CHINESE_PAGE_SIZE
    assert sum(len(item.candidates) for item in session.frozen_pages.values()) == 35


def test_chinese_intermediate_page_advertises_fixed_next_page_when_exhausted() -> None:
    pending = [_candidate(index) for index in range(CHINESE_PAGE_SIZE)]
    session = _chinese_session(
        pending=pending,
        frozen_pages={
            page_index: SimpleNamespace(candidates=tuple(range(CHINESE_PAGE_SIZE)))
            for page_index in range(3)
        },
        exhausted=True,
    )

    page = NeuralCandidatePageManager._freeze_next_page(
        _manager(),
        session,
        page_index=3,
        page_size=CHINESE_PAGE_SIZE,
        absolute_deadline=10.0,
    )

    assert len(page.candidates) == CHINESE_PAGE_SIZE
    assert page.has_more is True
