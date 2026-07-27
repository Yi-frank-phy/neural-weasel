from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class CompositionMode(StrEnum):
    CHINESE = "chinese"
    ENGLISH = "english"


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


def _commit(state: CompositionState, text: str, *, forward_enter: bool = False) -> KeyTransition:
    return KeyTransition(
        state=CompositionState.idle(state.mode),
        committed_text=text,
        forward_enter=forward_enter,
    )


def reduce_key(state: CompositionState, action: KeyAction) -> KeyTransition:
    """Pure initial v0.2 key reducer.

    Candidate recomputation after character or backspace input is owned by the
    caller. This reducer only makes acceptance and literal-preservation
    semantics explicit.
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
        if state.mode == CompositionMode.ENGLISH:
            return KeyTransition(
                replace(
                    state,
                    candidates=(),
                    selected_index=0,
                    completion_visible=False,
                )
            )
        return KeyTransition(CompositionState.idle(state.mode))

    if action == KeyAction.SPACE:
        if state.mode == CompositionMode.ENGLISH:
            return _commit(state, state.literal + " ")
        return _commit(state, _selected_candidate(state) or state.literal)

    if action == KeyAction.TAB:
        if state.mode != CompositionMode.ENGLISH:
            return KeyTransition(state)
        completion = _selected_candidate(state)
        return _commit(state, completion) if completion is not None else KeyTransition(state)

    if action == KeyAction.ENTER:
        return _commit(
            state,
            state.literal,
            forward_enter=state.mode == CompositionMode.ENGLISH,
        )

    if action in _NUMBER_ACTIONS:
        if state.mode == CompositionMode.ENGLISH:
            return KeyTransition(state)
        candidate = _selected_candidate(state, _NUMBER_ACTIONS[action])
        return _commit(state, candidate) if candidate is not None else KeyTransition(state)

    if action in {KeyAction.NEXT, KeyAction.PREVIOUS}:
        if not state.candidates:
            return KeyTransition(state)
        step = 1 if action == KeyAction.NEXT else -1
        return KeyTransition(
            replace(
                state,
                selected_index=(state.selected_index + step) % len(state.candidates),
            )
        )

    raise ValueError(f"unsupported key action: {action}")
