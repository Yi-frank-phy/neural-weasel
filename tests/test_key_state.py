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


def english_state() -> CompositionState:
    return CompositionState(
        mode=CompositionMode.ENGLISH,
        literal="asy",
        candidates=("asymmetric", "asymmetry"),
        selected_index=0,
        completion_visible=True,
    )


def chinese_state() -> CompositionState:
    return CompositionState(
        mode=CompositionMode.CHINESE,
        literal="jiuchan",
        candidates=("纠缠", "就产"),
        selected_index=0,
        completion_visible=True,
    )


def test_english_space_commits_literal_plus_space() -> None:
    """AT-KS-01: Space never silently accepts the model completion."""
    transition = reduce_key(english_state(), KeyAction.SPACE)

    assert transition.committed_text == "asy "
    assert transition.committed_text != "asymmetric "
    assert transition.state.is_idle


def test_english_tab_accepts_selected_completion() -> None:
    """AT-KS-02: Tab is the explicit default completion acceptance key."""
    transition = reduce_key(english_state(), KeyAction.TAB)

    assert transition.committed_text == "asymmetric"
    assert transition.state.is_idle


def test_english_escape_dismisses_completion_and_preserves_literal() -> None:
    """AT-KS-03: Escape cannot replace or delete literal input."""
    transition = reduce_key(english_state(), KeyAction.ESCAPE)

    assert transition.committed_text is None
    assert transition.state.literal == "asy"
    assert transition.state.candidates == ()
    assert not transition.state.completion_visible


def test_chinese_space_and_number_commit_candidates() -> None:
    """AT-KS-04: Chinese mode keeps conventional candidate acceptance."""
    space = reduce_key(chinese_state(), KeyAction.SPACE)
    number = reduce_key(chinese_state(), KeyAction.SELECT_2)

    assert space.committed_text == "纠缠"
    assert number.committed_text == "就产"


def test_english_number_key_never_commits_completion() -> None:
    transition = reduce_key(english_state(), KeyAction.SELECT_1)

    assert transition.committed_text is None
    assert transition.state.literal == "asy"


def test_enter_commits_literal_in_both_modes() -> None:
    """AT-KS-05: Enter has deterministic literal behavior."""
    english = reduce_key(english_state(), KeyAction.ENTER)
    chinese = reduce_key(chinese_state(), KeyAction.ENTER)

    assert english.committed_text == "asy"
    assert english.forward_enter
    assert chinese.committed_text == "jiuchan"
    assert not chinese.forward_enter


def test_backspace_to_empty_returns_to_idle() -> None:
    """AT-KS-06: deletion/retyping state contains no hidden prefix."""
    state = CompositionState(
        mode=CompositionMode.ENGLISH,
        literal="a",
        candidates=("asymmetric",),
        selected_index=0,
        completion_visible=True,
    )

    transition = reduce_key(state, KeyAction.BACKSPACE)

    assert transition.state.is_idle
    assert transition.state.literal == ""
    assert transition.state.candidates == ()


def _shared_vectors() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


@pytest.mark.parametrize("vector", _shared_vectors(), ids=lambda row: row["# case"])
def test_shared_python_cpp_key_state_vectors(vector: dict[str, str]) -> None:
    """Python and the compiled Rime boundary consume the same safety vectors."""
    mode = CompositionMode(vector["mode"])
    literal = "asy" if mode == CompositionMode.ENGLISH else "jiuchan"
    has_usable_candidate = (
        vector["has_candidate"] == "1"
        and vector["candidate_fresh"] == "1"
        and vector["service_available"] == "1"
    )
    candidate = "asymmetric" if mode == CompositionMode.ENGLISH else "纠缠"
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
    }[vector["intent"]]

    transition = reduce_key(state, action)
    expected = vector["expected"]
    if expected == "commit_literal_space":
        assert transition.committed_text == literal + " "
    elif expected == "accept_completion":
        assert transition.committed_text == candidate
    elif expected == "dismiss_completion":
        assert transition.committed_text is None
        assert transition.state.literal == literal
        assert transition.state.candidates == ()
    elif expected == "commit_literal_enter":
        assert transition.committed_text == literal
        assert transition.forward_enter
    elif expected in {"commit_selected", "commit_numbered"}:
        assert transition.committed_text == candidate
    elif expected == "cancel":
        assert transition.state.is_idle
    elif expected == "update_literal":
        assert transition.state.literal == literal[:-1]
    elif expected == "keep_literal":
        assert transition.committed_text is None
        assert transition.state.literal == literal
    else:
        raise AssertionError(f"unhandled shared vector outcome: {expected}")
