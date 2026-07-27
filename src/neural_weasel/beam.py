from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .index import IndexedPronunciation
from .pinyin import ParsedPinyinInput, is_all_han, parse_raw_pinyin

BEAM_WIDTH = 4
MAX_MODEL_TOKENS = 4
MAX_HAN_CHARACTERS = 12


@dataclass(frozen=True, slots=True)
class BeamStep[StateT]:
    """An opaque backend state and the next-token log probabilities at that state."""

    state: StateT
    log_probs: Sequence[float]


class BranchingBackend[StateT](Protocol):
    """Minimal model interface required by constrained beam search.

    ``advance`` must leave ``parent_state`` reusable. A backend may implement that
    contract with persistent cache handles, supported cache batch operations, or
    serial replay. The search never copies or mutates a model cache itself.
    """

    def root(self) -> BeamStep[StateT]: ...

    def advance(self, parent_state: StateT, token_id: int) -> BeamStep[StateT]: ...


class ConstraintIndex(Protocol):
    def compatible(self, parsed: ParsedPinyinInput) -> list[IndexedPronunciation]: ...


class CanonicalTokenizer(Protocol):
    def encode(
        self,
        text: str,
        *,
        add_special_tokens: bool = False,
    ) -> Sequence[int]: ...


@dataclass(frozen=True, slots=True)
class BeamCandidate:
    text: str
    pinyin: str
    consumed_keys: int
    score: float
    token_ids: tuple[int, ...]
    syllables: int
    exact_pinyin: bool


@dataclass(frozen=True, slots=True)
class ReplayState:
    """A cache-safe state for the deliberately slow serial replay fallback."""

    token_ids: tuple[int, ...]


class SerialReplayBackend(BranchingBackend[ReplayState]):
    """Adapt a path evaluator to the branching protocol without copying cache objects.

    The evaluator receives the complete generated token path and returns normalized
    next-token log probabilities. Calls are serialized and memoized by path. A real
    HF implementation can therefore rebuild a path from the root or manage its own
    supported cache operations without exposing cache internals to the beam search.
    """

    def __init__(
        self,
        evaluator: Callable[[tuple[int, ...]], Sequence[float]],
    ) -> None:
        self._evaluator = evaluator
        self._cache: dict[tuple[int, ...], Sequence[float]] = {}
        self._lock = threading.Lock()

    def root(self) -> BeamStep[ReplayState]:
        return self._step(())

    def advance(self, parent_state: ReplayState, token_id: int) -> BeamStep[ReplayState]:
        return self._step((*parent_state.token_ids, token_id))

    def _step(self, token_ids: tuple[int, ...]) -> BeamStep[ReplayState]:
        with self._lock:
            log_probs = self._cache.get(token_ids)
            if log_probs is None:
                log_probs = self._evaluator(token_ids)
                self._cache[token_ids] = log_probs
        return BeamStep(state=ReplayState(token_ids), log_probs=log_probs)


@dataclass(frozen=True, slots=True)
class _Path[StateT]:
    token_ids: tuple[int, ...]
    text: str
    compact_pinyin: str
    syllable_path: tuple[str, ...]
    score: float
    step: BeamStep[StateT]


@dataclass(frozen=True, slots=True)
class _ScoredExtension[StateT]:
    parent: _Path[StateT]
    entry: IndexedPronunciation
    token_id: int
    token_ids: tuple[int, ...]
    text: str
    compact_pinyin: str
    syllable_path: tuple[str, ...]
    score: float


def _remaining_input(parsed: ParsedPinyinInput, consumed_letters: int) -> ParsedPinyinInput:
    raw_offset = parsed.raw_characters_for_letters(consumed_letters)
    return parse_raw_pinyin(parsed.raw[raw_offset:])


def _log_probability(log_probs: Sequence[float], token_id: int) -> float:
    if token_id < 0 or token_id >= len(log_probs):
        raise IndexError("token id is outside backend log-probability vector")
    value = float(log_probs[token_id])
    if math.isnan(value) or value > 1e-6:
        raise ValueError("backend must return normalized next-token log probabilities")
    return value


def _is_canonical(
    tokenizer: CanonicalTokenizer,
    text: str,
    token_ids: tuple[int, ...],
) -> bool:
    encoded = tokenizer.encode(text, add_special_tokens=False)
    return tuple(int(token_id) for token_id in encoded) == token_ids


def _extension_sort_key(extension: _ScoredExtension[object]) -> tuple[object, ...]:
    return (
        -extension.score,
        len(extension.token_ids),
        extension.text,
        extension.compact_pinyin,
        extension.token_ids,
    )


def _candidate_sort_key(candidate: BeamCandidate) -> tuple[object, ...]:
    return (
        -candidate.score,
        len(candidate.token_ids),
        candidate.text,
        candidate.pinyin,
        candidate.token_ids,
    )


def constrained_beam_search[StateT](
    *,
    backend: BranchingBackend[StateT],
    index: ConstraintIndex,
    tokenizer: CanonicalTokenizer,
    raw_pinyin: str,
    limit: int = 5,
) -> list[BeamCandidate]:
    """Return model-scored multi-token candidates legal under exact full pinyin.

    The hard limits are deliberately fixed for the v0.1 background search:
    beam width 4, at most 4 model tokens, and at most 12 Han characters.
    Only hypotheses that cover all currently typed pinyin are returned. For an
    incomplete final syllable, a pronunciation may extend beyond the typed prefix.

    The index supplies the legal token set before any score-based pruning. The
    backend must supply *normalized* log probabilities so scores can be accumulated
    across different token counts without length normalization.
    """

    if limit <= 0:
        return []
    parsed = parse_raw_pinyin(raw_pinyin)
    if not parsed.compact:
        return []

    root = backend.root()
    frontier: list[_Path[StateT]] = [
        _Path(
            token_ids=(),
            text="",
            compact_pinyin="",
            syllable_path=(),
            score=0.0,
            step=root,
        )
    ]
    finished: dict[str, BeamCandidate] = {}

    for depth in range(MAX_MODEL_TOKENS):
        active_extensions: list[_ScoredExtension[StateT]] = []
        for path in frontier:
            remaining = _remaining_input(parsed, len(path.compact_pinyin))
            legal_entries = index.compatible(remaining)

            # Legality is established by the pinyin index before scores are read
            # or top-k pruning is applied.
            for entry in legal_entries:
                if entry.coverage or entry.token_id is None or not is_all_han(entry.text):
                    continue
                if len(path.text) + len(entry.text) > MAX_HAN_CHARACTERS:
                    continue

                token_id = entry.token_id
                token_ids = (*path.token_ids, token_id)
                text = path.text + entry.text
                if not _is_canonical(tokenizer, text, token_ids):
                    continue

                log_probability = _log_probability(path.step.log_probs, token_id)
                if log_probability == -math.inf:
                    continue
                compact_pinyin = path.compact_pinyin + entry.pinyin
                syllable_path = (*path.syllable_path, *entry.syllable_path)
                extension = _ScoredExtension(
                    parent=path,
                    entry=entry,
                    token_id=token_id,
                    token_ids=token_ids,
                    text=text,
                    compact_pinyin=compact_pinyin,
                    syllable_path=syllable_path,
                    score=path.score + log_probability,
                )

                if len(compact_pinyin) >= len(parsed.compact):
                    candidate = BeamCandidate(
                        text=text,
                        pinyin="'".join(syllable_path),
                        consumed_keys=len(parsed.raw),
                        score=extension.score,
                        token_ids=token_ids,
                        syllables=len(syllable_path),
                        exact_pinyin=compact_pinyin == parsed.compact,
                    )
                    previous = finished.get(text)
                    if previous is None or _candidate_sort_key(candidate) < _candidate_sort_key(
                        previous
                    ):
                        finished[text] = candidate
                elif depth + 1 < MAX_MODEL_TOKENS:
                    active_extensions.append(extension)

        if not active_extensions:
            break

        # All extensions in this list have already passed the pinyin constraint.
        active_extensions.sort(key=_extension_sort_key)
        selected = active_extensions[:BEAM_WIDTH]
        frontier = []
        for extension in selected:
            next_step = backend.advance(extension.parent.step.state, extension.token_id)
            frontier.append(
                _Path(
                    token_ids=extension.token_ids,
                    text=extension.text,
                    compact_pinyin=extension.compact_pinyin,
                    syllable_path=extension.syllable_path,
                    score=extension.score,
                    step=next_step,
                )
            )

    return sorted(finished.values(), key=_candidate_sort_key)[:limit]

