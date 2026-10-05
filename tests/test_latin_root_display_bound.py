from __future__ import annotations

import numpy as np
import pytest

from neural_weasel import neural_candidate_pages_scored as scored
from neural_weasel import neural_candidate_pages_v2 as v2
from neural_weasel import neural_candidate_pages_v3 as v3
from neural_weasel.candidate import Candidate
from neural_weasel.neural_candidates import (
    MAX_FROZEN_CANDIDATES,
    NeuralLanguageMode,
    _latin_key,
    _SearchIdentity,
)
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint


@pytest.mark.parametrize("contextual", [False, True])
def test_latin_display_bound_preserves_visible_winners_and_every_root(monkeypatch, contextual):
    """Discarded display allocations must not remove their continuation paths."""
    scores = np.asarray([-(i % 7) * 0.1 for i in range(402)], dtype=np.float32)
    scores[400] = np.nan
    rows = [(f"de{i:03}", i) for i in range(400)]
    rows += [("DE000", 401), ("debad", 400), ("d", 0)]

    class Backend:
        def score_allowed_tokens(self, state, token_ids):
            return scores[np.asarray(token_ids)]

    manager = v2.NeuralCandidatePageManager(
        backend=Backend(),
        pinyin_index=None,
        latin_constraint=LatinPrefixConstraint(
            [LatinCompletion(text, (token,)) for text, token in rows]
        ),
    )
    manager._baseline_scores = scores
    manager._bound_latin_root_displays = True

    def candidate(text, token_path, score):
        return Candidate(
            text=text,
            pinyin="",
            consumed_keys=2,
            score=score,
            context_epoch=7,
            coverage=False,
            completes_input=False,
            syllables=0,
            token_id=token_path[0],
            constraint_kind="latin_prefix",
            script="latin",
            model_score=score,
            total_score=score,
            token_path=token_path,
            predicted_syllables=0,
        )

    # A cached continuation can promote a root outside the allocation bound.
    cached = candidate("de398", (398, 399), 1.0)
    manager._baseline_latin_cache[("de398", (398, 399))] = cached
    expected = [candidate(text, (token,), float(scores[token])) for text, token in rows[:400]]
    if not contextual:
        expected[398] = cached
    expected = sorted(expected, key=_latin_key)[:MAX_FROZEN_CANDIDATES]
    allocations = []

    def allocate(**kwargs):
        result = Candidate(**kwargs)
        allocations.append(result)
        return result

    monkeypatch.setattr(v2, "Candidate", allocate)
    actual, frontier = manager._root_latin_candidates_and_frontier(
        "de", object() if contextual else None, 7
    )
    frontier = [path for path in frontier if not isinstance(path, v2._LatinRootDisplayIdentities)]
    assert len(allocations) == MAX_FROZEN_CANDIDATES
    assert sorted(actual, key=_latin_key)[:MAX_FROZEN_CANDIDATES] == expected
    assert [(path.text, path.token_path) for path in frontier] == [
        *[(text, (token,)) for text, token in rows[:401]],
        *([] if contextual else [("de398", (398, 399))]),
    ]
    assert all(item.context_epoch == 7 for item in actual)


def _many_root_manager(manager_type=v2.NeuralCandidatePageManager):
    scores = np.full(184, -0.1, dtype=np.float32)
    scores[180] = -10.0
    rows = [(f"de{i:03}", i) for i in range(180)] + [("debug", 180), ("de", 181)]

    class Backend:
        def score_allowed_tokens(self, state, token_ids):
            return scores[np.asarray(token_ids)]

    manager = manager_type(
        backend=Backend(),
        pinyin_index=None,
        latin_constraint=LatinPrefixConstraint(
            [LatinCompletion(text, (token,)) for text, token in rows]
        ),
    )
    manager._bound_latin_root_displays = True
    manager._baseline_scores = scores
    manager._baseline_log_probs = scores
    manager._root_han_candidates = lambda *args: []
    manager._root_han_candidates_and_frontier = lambda *args: ([], [])
    manager._root_han_plan = lambda *args: ()
    manager._latin_fragments_by_token = {182: "bug"}
    return manager


def _new_root_session(manager, mode=NeuralLanguageMode.CHINESE_FIRST):
    return manager._new_session(
        _SearchIdentity("public-display", 1, 7, None, None, mode, "de"), None
    )


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, v3.NeuralCandidatePageManager]
)
def test_omitted_root_cannot_reenter_via_higher_scoring_continuation(manager_type):
    manager = _many_root_manager(manager_type)
    # Repeat to exercise the immutable baseline root-page cache too.
    for _ in range(2):
        session = _new_root_session(manager)
        assert ("debug", 2) in session.seen_candidates
        assert all(isinstance(path, v2._SearchPath) for path in session.frontier)
        parent = next(path for path in session.frontier if path.text == "de")
        manager._expand_latin(session, parent, (182,), np.asarray([-0.1]), float("inf"))
        assert all(candidate.text != "debug" for candidate in session.pending)
        assert not manager._baseline_latin_cache


@pytest.mark.parametrize("cached_score", [-20.0, -10.0, 1.0])
def test_omitted_root_seen_uses_final_case_sensitive_cached_winner(cached_score):
    manager = _many_root_manager()
    cached = Candidate(
        text="DEBUG",
        pinyin="",
        consumed_keys=2,
        score=cached_score,
        context_epoch=0,
        coverage=False,
        completes_input=False,
        syllables=0,
        token_id=183,
        constraint_kind="latin_prefix",
        script="latin",
        model_score=cached_score,
        total_score=cached_score,
        token_path=(183,),
    )
    manager._baseline_latin_cache[("debug", (183,))] = cached
    session = _new_root_session(manager)
    expected = "DEBUG" if cached_score > -10.0 else "debug"
    assert (expected, 2) in session.seen_candidates
    assert (("debug" if expected == "DEBUG" else "DEBUG"), 2) not in session.seen_candidates


def test_latin_first_keeps_original_five_seen_identities():
    session = _new_root_session(_many_root_manager(), NeuralLanguageMode.LATIN_FIRST)
    assert len(session.pending) == len(session.seen_candidates) == 5
    assert ("debug", 2) not in session.seen_candidates
    assert all(isinstance(path, v2._SearchPath) for path in session.frontier)


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, v3.NeuralCandidatePageManager]
)
def test_single_letter_prewarm_does_not_add_hidden_seen(manager_type):
    manager = _many_root_manager(manager_type)
    roots, _ = manager._root_latin_candidates_and_frontier("de", None, 7)
    retained = sorted(roots, key=_latin_key)[:5]
    manager._baseline_single_letter[("de", NeuralLanguageMode.CHINESE_FIRST)] = tuple(retained)
    session = _new_root_session(manager)
    assert len(session.seen_candidates) == 5
    assert ("debug", 2) not in session.seen_candidates


def test_cached_han_branch_keeps_original_display_cutoff_seen():
    manager = _many_root_manager(scored.NeuralCandidatePageManager)
    manager._baseline_han_cache[("de", "的", (182, 183))] = Candidate(
        text="的",
        pinyin="de",
        consumed_keys=2,
        score=0.0,
        context_epoch=0,
        coverage=False,
        completes_input=True,
        syllables=1,
        token_path=(182, 183),
    )
    manager._han_frontier_from_candidates = lambda *args: []
    session = _new_root_session(manager)
    assert len(session.seen_candidates) == MAX_FROZEN_CANDIDATES
    assert ("debug", 2) not in session.seen_candidates
    assert all(isinstance(path, v2._SearchPath) for path in session.frontier)
