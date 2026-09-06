from __future__ import annotations

from types import SimpleNamespace

import pytest

from neural_weasel.candidate import Candidate
from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import CandidatePageError


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
    )


def test_capacity_zero_rejects_before_search_or_pending_mutation() -> None:
    pending = [_candidate(index) for index in range(9)]
    session = SimpleNamespace(
        candidate_set_id="capacity-zero",
        pending=list(pending),
        exhausted=False,
        frozen_pages={
            page_index: SimpleNamespace(candidates=tuple(range(9)))
            for page_index in range(20)
        },
        score_source="baseline",
        search_depth=1,
        timeout_count=0,
    )

    with pytest.raises(CandidatePageError, match="frozen-candidate safety limit"):
        NeuralCandidatePageManager._freeze_next_page(
            _manager(),
            session,
            page_index=20,
            page_size=9,
            absolute_deadline=10.0,
        )

    assert session.pending == pending
    assert 20 not in session.frozen_pages


def test_last_capacity_slot_freezes_only_one_candidate_and_ends_paging() -> None:
    pending = [_candidate(index) for index in range(9)]
    frozen_pages = {
        page_index: SimpleNamespace(candidates=tuple(range(9)))
        for page_index in range(19)
    }
    frozen_pages[19] = SimpleNamespace(candidates=tuple(range(8)))
    session = SimpleNamespace(
        candidate_set_id="capacity-one",
        pending=list(pending),
        exhausted=False,
        frozen_pages=frozen_pages,
        score_source="baseline",
        search_depth=1,
        timeout_count=0,
    )

    page = NeuralCandidatePageManager._freeze_next_page(
        _manager(),
        session,
        page_index=20,
        page_size=9,
        absolute_deadline=10.0,
    )

    assert page.candidates == (pending[0],)
    assert page.has_more is False
    assert session.pending == pending[1:]
    assert len(page.candidate_ids) == 1
