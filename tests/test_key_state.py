from __future__ import annotations

import csv
from pathlib import Path

import pytest

from neural_weasel.key_state import (
    CompositionMode,
    CompositionState,
    KeyAction,
    reduce_key,
)

FIXTURE = Path(__file__).parent / "fixtures" / "key_state_vectors.tsv"


def latin_state() -> CompositionState:
    return CompositionState(
        mode=CompositionMode.LATIN_FIRST,
        literal="asy",
        candidates=("asymmetric", "asymmetry"),
        selected_index=0,
        completion_visible=True,
    )


def chinese_state() -> CompositionState:
    return CompositionState(
        mode=CompositionMode.CHINESE_FIRST,
        literal="jiuchan",
        candidates=("纠缠", "就产"),
        selected_index=0,
        completion_visible=True,
    )


def test_latin_space_commits_literal_plus_space() -> None:
    """AT-KS-01: Space never silently accepts the model completion."""
    transition = reduce_key(latin_state(), KeyAction.SPACE)

    assert transition.committed_text == "asy "
    assert transition.committed_text != "asymmetric "
    assert transition.state.is_idle


def test_latin_tab_accepts_selected_completion() -> None:
    """AT-KS-02: Tab is the explicit default completion acceptance key."""
    transition = reduce_key(latin_state(), KeyAction.TAB)

    assert transition.committed_text == "asymmetric"
    assert transition.state.is_idle


def test_escape_cancels_composition_in_both_modes() -> None:
    """AT-KS-03: Escape cancels the current composition without committing text."""
    for state in (latin_state(), chinese_state()):
        transition = reduce_key(state, KeyAction.ESCAPE)
        assert transition.committed_text is None
        assert transition.state.is_idle
        assert transition.state.candidates == ()
        assert not transition.state.completion_visible


def test_chinese_space_and_number_commit_candidates() -> None:
    """AT-KS-04: Chinese-first mode keeps conventional candidate acceptance."""
    space = reduce_key(chinese_state(), KeyAction.SPACE)
    number = reduce_key(chinese_state(), KeyAction.SELECT_2)

    assert space.committed_text == "纠缠"
    assert number.committed_text == "就产"


def test_latin_number_key_never_commits_completion() -> None:
    transition = reduce_key(latin_state(), KeyAction.SELECT_1)

    assert transition.committed_text is None
    assert transition.state.literal == "asy"


def test_enter_commits_literal_in_both_modes_without_forwarding_enter() -> None:
    """AT-KS-05: Enter deterministically commits the raw composition."""
    latin = reduce_key(latin_state(), KeyAction.ENTER)
    chinese = reduce_key(chinese_state(), KeyAction.ENTER)

    assert latin.committed_text == "asy"
    assert not latin.forward_enter
    assert latin.state.is_idle
    assert chinese.committed_text == "jiuchan"
    assert not chinese.forward_enter
    assert chinese.state.is_idle


def test_backspace_to_empty_returns_to_idle() -> None:
    """AT-KS-06: deletion/retyping state contains no hidden prefix."""
    state = CompositionState(
        mode=CompositionMode.LATIN_FIRST,
        literal="a",
        candidates=("asymmetric",),
        selected_index=0,
        completion_visible=True,
    )

    transition = reduce_key(state, KeyAction.BACKSPACE)

    assert transition.state.is_idle
    assert transition.state.literal == ""
    assert transition.state.candidates == ()


def test_page_intents_do_not_move_in_page_selection() -> None:
    """Lazy paging is owned by the page protocol, not this key-state reducer."""
    state = chinese_state()
    for action in (KeyAction.NEXT, KeyAction.PREVIOUS):
        transition = reduce_key(state, action)
        assert transition.state == state
        assert transition.committed_text is None


def _shared_vectors() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


@pytest.mark.parametrize("vector", _shared_vectors(), ids=lambda row: row["# case"])
def test_shared_python_cpp_key_state_vectors(vector: dict[str, str]) -> None:
    """Python and the compiled Rime boundary consume the same safety vectors."""
    mode = CompositionMode(vector["mode"])
    literal = "asy" if mode == CompositionMode.LATIN_FIRST else "jiuchan"
    has_usable_candidate = (
        vector["has_candidate"] == "1"
        and vector["candidate_fresh"] == "1"
        and vector["service_available"] == "1"
    )
    candidate = "asymmetric" if mode == CompositionMode.LATIN_FIRST else "纠缠"
    state = CompositionState(
        mode=mode,
        literal=literal,
        candidates=(candidate,) if has_usable_candidate else (),
        completion_visible=has_usable_candidate,
    )
    action = {
        "space": KeyAction.SPACE,
        "tab": KeyAction.TAB,
        "escape": KeyAction.ESCAPE,
        "enter": KeyAction.ENTER,
        "backspace": KeyAction.BACKSPACE,
        "numbered_selection": KeyAction.SELECT_1,
        "page_next": KeyAction.NEXT,
        "page_previous": KeyAction.PREVIOUS,
    }[vector["intent"]]

    transition = reduce_key(state, action)
    expected = vector["expected"]
    if expected == "commit_literal_space":
        assert transition.committed_text == literal + " "
    elif expected == "accept_completion":
        assert transition.committed_text == candidate
    elif expected == "commit_literal":
        assert transition.committed_text == literal
        assert not transition.forward_enter
        assert transition.state.is_idle
    elif expected in {"commit_selected", "commit_numbered"}:
        assert transition.committed_text == candidate
    elif expected == "cancel":
        assert transition.committed_text is None
        assert transition.state.is_idle
    elif expected == "update_literal":
        assert transition.state.literal == literal[:-1]
    elif expected == "keep_literal":
        assert transition.committed_text is None
        assert transition.state.literal == literal
    elif expected in {"page_next", "page_previous"}:
        assert transition.committed_text is None
        assert transition.state == state
    else:
        raise AssertionError(f"unhandled shared vector outcome: {expected}")
