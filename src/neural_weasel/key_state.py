from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CompositionMode(StrEnum):
    CHINESE_FIRST = "chinese_first"
    LATIN_FIRST = "latin_first"

    # Compatibility aliases for callers that still use the old symbolic names.
    CHINESE = "chinese_first"
    ENGLISH = "latin_first"


class KeyAction(StrEnum):
    SPACE = "space"
    TAB = "tab"
    ENTER = "enter"
    ESCAPE = "escape"
    BACKSPACE = "backspace"
    SELECT_1 = "select_1"
    SELECT_2 = "select_2"
    SELECT_3 = "select_3"
    SELECT_4 = "select_4"
    SELECT_5 = "select_5"
    SELECT_6 = "select_6"
    SELECT_7 = "select_7"
    SELECT_8 = "select_8"
    SELECT_9 = "select_9"
    NEXT = "next"
    PREVIOUS = "previous"


@dataclass(frozen=True, slots=True)
class CompositionState:
    mode: CompositionMode
    literal: str = ""
    candidates: tuple[str, ...] = ()
    selected_index: int = 0
    completion_visible: bool = False

    def __post_init__(self) -> None:
        if self.selected_index < 0:
            raise ValueError("selected_index must not be negative")

    @property
    def is_idle(self) -> bool:
        return not self.literal

    @classmethod
    def idle(cls, mode: CompositionMode) -> CompositionState:
        return cls(mode=mode)


@dataclass(frozen=True, slots=True)
class KeyTransition:
    state: CompositionState
    committed_text: str | None = None
    forward_enter: bool = False


_NUMBER_ACTIONS = {
    KeyAction.SELECT_1: 0,
    KeyAction.SELECT_2: 1,
    KeyAction.SELECT_3: 2,
    KeyAction.SELECT_4: 3,
    KeyAction.SELECT_5: 4,
    KeyAction.SELECT_6: 5,
    KeyAction.SELECT_7: 6,
    KeyAction.SELECT_8: 7,
    KeyAction.SELECT_9: 8,
}


def _selected_candidate(state: CompositionState, index: int | None = None) -> str | None:
    selected = state.selected_index if index is None else index
    if 0 <= selected < len(state.candidates):
        return state.candidates[selected]
    return None


def _commit(state: CompositionState, text: str) -> KeyTransition:
    return KeyTransition(
        state=CompositionState.idle(state.mode),
        committed_text=text,
    )


def reduce_key(state: CompositionState, action: KeyAction) -> KeyTransition:
    """Pure key reducer for the native bilingual composition contract.

    Candidate recomputation and lazy page transport are owned by the caller.
    NEXT/PREVIOUS therefore preserve this in-page state; they are page intents,
    not candidate-selection movement.
    """

    if action == KeyAction.BACKSPACE:
        literal = state.literal[:-1]
        return KeyTransition(
            CompositionState(
                mode=state.mode,
                literal=literal,
                candidates=(),
                selected_index=0,
                completion_visible=False,
            )
        )

    if action == KeyAction.ESCAPE:
        return KeyTransition(CompositionState.idle(state.mode))

    if action == KeyAction.SPACE:
        if state.mode == CompositionMode.LATIN_FIRST:
            return _commit(state, state.literal + " ")
        return _commit(state, _selected_candidate(state) or state.literal)

    if action == KeyAction.TAB:
        if state.mode != CompositionMode.LATIN_FIRST:
            return KeyTransition(state)
        completion = _selected_candidate(state)
        return _commit(state, completion) if completion is not None else KeyTransition(state)

    if action == KeyAction.ENTER:
        return _commit(state, state.literal)

    if action in _NUMBER_ACTIONS:
        if state.mode == CompositionMode.LATIN_FIRST:
            return KeyTransition(state)
        candidate = _selected_candidate(state, _NUMBER_ACTIONS[action])
        return _commit(state, candidate) if candidate is not None else KeyTransition(state)

    if action in {KeyAction.NEXT, KeyAction.PREVIOUS}:
        return KeyTransition(state)

    raise ValueError(f"unsupported key action: {action}")
