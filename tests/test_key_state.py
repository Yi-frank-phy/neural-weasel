from __future__ import annotations

from neural_weasel.key_state import (
    CompositionMode,
    CompositionState,
    KeyAction,
    reduce_key,
)


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

