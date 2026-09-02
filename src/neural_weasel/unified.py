from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .index import PinyinIndex
from .pinyin import parse_raw_pinyin

_LATIN_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9.'-]*$")
_FUZZY_INITIALS = (("zh", "z"), ("ch", "c"), ("sh", "s"), ("n", "l"), ("f", "h"))
_FUZZY_FINALS = (("an", "ang"), ("en", "eng"), ("in", "ing"))
_PINYIN_INITIALS = (
    "zh",
    "ch",
    "sh",
    "b",
    "p",
    "m",
    "f",
    "d",
    "t",
    "n",
    "l",
    "g",
    "k",
    "h",
    "j",
    "q",
    "x",
    "r",
    "z",
    "c",
    "s",
    "y",
    "w",
)
_PARTIAL_BEAM_WIDTH = 1
_PARTIAL_MAX_MODEL_TOKENS = 1
_PARTIAL_MAX_HAN_CHARACTERS = 16


class Script(StrEnum):
    HAN = "han"
    LATIN = "latin"
    MIXED = "mixed"
    OTHER = "other"


def is_han(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x3134F
    )


def contains_han(text: str) -> bool:
    return any(is_han(character) for character in text)


def detect_script(text: str) -> Script:
    han = contains_han(text)
    latin = any(character.isascii() and character.isalpha() for character in text)
    if han and latin:
        return Script.MIXED
    if han:
        return Script.HAN
    if latin:
        return Script.LATIN
    return Script.OTHER


class ContextScriptPolicy:
    """Explainable asymmetric policy; no independent language classifier."""

    def __init__(
        self,
        *,
        cross_script_penalty: float = -0.15,
        ambiguous_script_penalty: float = -0.05,
    ) -> None:
        if cross_script_penalty > 0 or ambiguous_script_penalty > 0:
            raise ValueError("script penalties must not be positive")
        self.stable_script: Script | None = None
        self.cross_script_penalty = float(cross_script_penalty)
        self.ambiguous_script_penalty = float(ambiguous_script_penalty)

    def classify(self, before: str) -> str:
        bounded = before[-128:]
        han_count = sum(is_han(character) for character in bounded)
        latin_count = sum(character.isascii() and character.isalpha() for character in bounded)
        counted = han_count + latin_count
        english = latin_count >= 8 and latin_count / max(1, counted) >= 0.70
        chinese = han_count >= 2 and han_count / max(1, counted) >= 0.30
        if english and chinese:
            nearest = self._nearest_decisive_run(bounded)
            if nearest is not None:
                return nearest
        if english:
            return "english"
        if chinese:
            return "chinese"
        return "ambiguous"

    @staticmethod
    def _nearest_decisive_run(text: str) -> str | None:
        current: str | None = None
        length = 0
        for character in reversed(text):
            kind = (
                "chinese"
                if is_han(character)
                else "english"
                if character.isascii() and character.isalpha()
                else None
            )
            if kind is None:
                if length >= 2:
                    return current
                current = None
                length = 0
            elif kind == current:
                length += 1
            else:
                if length >= 2:
                    return current
                current = kind
                length = 1
        return current if length >= 2 else None

    @staticmethod
    def allows(context_kind: str, script: Script) -> bool:
        # Context changes ranking but never deletes the other writing system.
        return True

    def language_prior(
        self,
        context_kind: str,
        script: Script,
        raw_keys: str,
    ) -> float:
        if context_kind == "english":
            return 0.0 if script in {Script.LATIN, Script.OTHER} else self.cross_script_penalty
        if context_kind == "chinese":
            if script != Script.LATIN:
                return 0.0
            if self._explicit_latin_shape(raw_keys):
                return 0.0
            return self.cross_script_penalty

        preferred = self.stable_script or Script.HAN
        if script in {Script.OTHER, Script.MIXED} or script == preferred:
            return 0.0
        return self.ambiguous_script_penalty

    @staticmethod
    def _explicit_latin_shape(raw_keys: str) -> bool:
        return (
            any(character.isupper() for character in raw_keys)
            or any(character.isdigit() or character in ".'-" for character in raw_keys)
            or (len(raw_keys) >= 2 and raw_keys.isupper())
        )

    def record_commit(self, text: str) -> None:
        script = detect_script(text)
        if script in {Script.HAN, Script.LATIN}:
            self.stable_script = script


class Constraint(Protocol):
    def candidates(
        self,
        raw_keys: str,
        *,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]: ...


class PinyinConstraint:
    constraint_kind = "pinyin"

    def __init__(self, index: PinyinIndex) -> None:
        self.index = index
        self.fuzzy_aliases = _build_fuzzy_aliases(index.syllables)

    def candidates(
        self,
        raw_keys: str,
        *,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return []
        raw = parsed.compact
        if not raw:
            return []

        candidates: list[Candidate] = []
        for query_parsed, fuzzy_cost in _pinyin_query_variants(parsed, self.fuzzy_aliases):
            for group in self.index.query_plan(query_parsed).groups:
                candidates.extend(
                    self._group_candidates(
                        parsed, query_parsed, group, fuzzy_cost, backend, state, after_text
                    )
                )
        if not candidates:
            candidates.extend(self._partial_candidates(parsed, backend, state, after_text))
        return candidates

    def _partial_candidates(
        self,
        parsed: Any,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        """Bounded fallback for mixed full-pinyin and syllable-initial input."""

        raw = parsed.compact
        frontier = [_PartialPath(0, "", (), (), 0.0, 0)]
        finished: dict[str, _PartialPath] = {}
        for _depth in range(_PARTIAL_MAX_MODEL_TOKENS):
            active: dict[tuple[int, str], _PartialPath] = {}
            for path in frontier:
                remaining = parse_raw_pinyin(raw[path.consumed_letters :])
                matches = [
                    match
                    for match in self.index.partial_matches(remaining, max_results=4096)
                    if not match.entry.coverage
                    and match.entry.token_id is not None
                    and contains_han(match.entry.text)
                    and len(path.text) + len(match.entry.text) <= _PARTIAL_MAX_HAN_CHARACTERS
                ]
                if not matches:
                    continue
                token_ids = [match.entry.token_id for match in matches]
                scores = backend.score_allowed_tokens(state, token_ids)
                for match, token_score in zip(matches, scores, strict=True):
                    entry = match.entry
                    consumed = path.consumed_letters + match.consumed_letters
                    token_path = (*path.token_path, entry.token_id)
                    text = path.text + entry.text
                    pinyin_path = (*path.pinyin_path, *entry.syllable_path)
                    score_sum = path.score_sum + float(token_score)
                    abbreviation_cost = path.abbreviation_cost + match.abbreviation_cost
                    extension = _PartialPath(
                        consumed,
                        text,
                        pinyin_path,
                        token_path,
                        score_sum,
                        abbreviation_cost,
                    )
                    if match.covers_input:
                        previous = finished.get(text)
                        if previous is None or _partial_path_key(extension) < _partial_path_key(
                            previous
                        ):
                            finished[text] = extension
                    elif consumed < len(raw):
                        key = (consumed, text)
                        previous = active.get(key)
                        if previous is None or _partial_path_key(extension) < _partial_path_key(
                            previous
                        ):
                            active[key] = extension
            frontier = sorted(active.values(), key=_partial_path_key)[:_PARTIAL_BEAM_WIDTH]
            if not frontier:
                break

        candidates = []
        for path in sorted(
            finished.values(),
            key=lambda item: (
                item.abbreviation_cost,
                len(item.token_path),
                *_partial_path_key(item),
            ),
        )[:64]:
            if after_text.startswith(path.text):
                continue
            model_score = path.score_sum / len(path.token_path) ** 0.7
            candidates.append(
                Candidate(
                    text=path.text,
                    pinyin="'".join(path.pinyin_path),
                    consumed_keys=len(parsed.raw),
                    score=model_score,
                    context_epoch=state.epoch,
                    coverage=False,
                    completes_input=True,
                    syllables=len(path.pinyin_path),
                    token_id=path.token_path[0],
                    constraint_kind="partial_pinyin",
                    script=detect_script(path.text),
                    model_score=model_score,
                    constraint_cost=-0.12 * path.abbreviation_cost,
                    token_path=path.token_path,
                    ranking_tier=1,
                )
            )
        return candidates

    def _group_candidates(
        self,
        user_parsed: Any,
        query_parsed: Any,
        group: Any,
        fuzzy_cost: int,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        raw = query_parsed.compact
        if not raw:
            return candidates
        legal_entries = tuple(
            entry for entry in group.entries if _covers_current_pinyin(query_parsed, entry)
        )
        if not legal_entries:
            return candidates
        if group.token_ids is None:
            ranked: Sequence[tuple[Any, float | None]] = tuple(
                (entry, None) for entry in legal_entries
            )
        else:
            token_ids = [entry.token_id for entry in legal_entries]
            scores = backend.score_allowed_tokens(state, token_ids)
            positions = np.argsort(-scores, kind="stable")
            ranked = tuple(
                (legal_entries[int(position)], float(scores[int(position)]))
                for position in positions
            )
        # Pinyin establishes a hard structural tier. Model logits rank only
        # within the same tier, so a fuzzy or extended reading cannot outrank
        # an exact reading merely because its next-token logit is larger.
        structural_cost = -0.08 * fuzzy_cost
        for entry, model_score in ranked:
            if after_text.startswith(entry.text):
                continue
            if model_score is None:
                continue
            token_path = (entry.token_id,) if entry.token_id is not None else ()
            candidates.append(
                Candidate(
                    text=entry.text,
                    pinyin=entry.display_pinyin,
                    consumed_keys=len(user_parsed.raw),
                    score=model_score,
                    context_epoch=state.epoch,
                    coverage=entry.coverage,
                    completes_input=entry.pinyin == raw,
                    syllables=entry.matched_syllables(len(raw)),
                    token_id=entry.token_id,
                    constraint_kind=self.constraint_kind,
                    script=detect_script(entry.text),
                    model_score=model_score,
                    constraint_cost=structural_cost,
                    token_path=token_path,
                    ranking_tier=(
                        2 + fuzzy_cost if fuzzy_cost else 0 if entry.pinyin == raw else 1
                    ),
                )
            )
        return candidates


def _covers_current_pinyin(parsed: Any, entry: Any) -> bool:
    """Reject under-coverage while allowing a Neural candidate to add characters."""

    raw = parsed.compact
    return entry.pinyin.startswith(raw)


@dataclass(frozen=True, slots=True)
class _PartialPath:
    consumed_letters: int
    text: str
    pinyin_path: tuple[str, ...]
    token_path: tuple[int, ...]
    score_sum: float
    abbreviation_cost: int


def _partial_path_key(path: _PartialPath) -> tuple[object, ...]:
    normalized_score = path.score_sum / max(1, len(path.token_path)) ** 0.7
    return (
        -(normalized_score - 0.12 * path.abbreviation_cost),
        path.abbreviation_cost,
        len(path.token_path),
        path.text,
        path.token_path,
    )


def _split_initial(syllable: str) -> tuple[str, str]:
    for initial in _PINYIN_INITIALS:
        if syllable.startswith(initial):
            return initial, syllable[len(initial) :]
    return "", syllable


def _fuzzy_alternatives(
    value: str, pairs: Sequence[tuple[str, str]]
) -> tuple[tuple[str, int], ...]:
    alternatives = [(value, 0)]
    for left, right in pairs:
        if value == left:
            alternatives.append((right, 1))
        elif value == right:
            alternatives.append((left, 1))
    return tuple(alternatives)


def _build_fuzzy_aliases(syllables: Sequence[str]) -> dict[str, tuple[tuple[str, int], ...]]:
    aliases: dict[str, dict[str, int]] = {}
    for canonical in syllables:
        initial, final = _split_initial(canonical)
        for alias_initial, initial_cost in _fuzzy_alternatives(initial, _FUZZY_INITIALS):
            for alias_final, final_cost in _fuzzy_alternatives(final, _FUZZY_FINALS):
                alias = alias_initial + alias_final
                cost = initial_cost + final_cost
                previous = aliases.setdefault(alias, {}).get(canonical)
                if previous is None or cost < previous:
                    aliases[alias][canonical] = cost
    return {
        alias: tuple(sorted(values.items(), key=lambda item: (item[1], item[0])))
        for alias, values in aliases.items()
    }


def _pinyin_query_variants(
    parsed: Any,
    aliases: dict[str, tuple[tuple[str, int], ...]],
    limit: int = 24,
) -> tuple[tuple[Any, int], ...]:
    variants: dict[str, int] = {parsed.raw: 0}
    frontier: list[tuple[int, str, int]] = [(0, "", 0)]
    raw = parsed.compact
    while frontier:
        position, canonical, cost = frontier.pop()
        if position == len(raw):
            previous = variants.get(canonical)
            if previous is None or cost < previous:
                variants[canonical] = cost
            continue
        for alias, canonical_values in aliases.items():
            if raw.startswith(alias, position):
                for canonical_syllable, syllable_cost in canonical_values:
                    frontier.append(
                        (
                            position + len(alias),
                            canonical + canonical_syllable,
                            cost + syllable_cost,
                        )
                    )
        if len(frontier) > limit * 8:
            frontier.sort(key=lambda item: (item[2], -item[0], item[1]))
            del frontier[limit * 8 :]
    return tuple(
        (parse_raw_pinyin(raw_variant), cost)
        for raw_variant, cost in sorted(variants.items(), key=lambda item: (item[1], item[0]))[
            :limit
        ]
    )


@dataclass(frozen=True, slots=True)
class LatinCompletion:
    text: str
    token_path: tuple[int, ...]
    constraint_cost: float = 0.0


class LatinPrefixConstraint:
    constraint_kind = "latin_prefix"

    def __init__(self, completions: Sequence[LatinCompletion]) -> None:
        self.completions = tuple(completions)

    @classmethod
    def from_tokenizer(cls, tokenizer: Any) -> LatinPrefixConstraint:
        """Build the bounded one-token baseline directly from model vocabulary."""

        special_ids = set(getattr(tokenizer, "all_special_ids", ()))
        completions: list[LatinCompletion] = []
        seen: set[tuple[str, int]] = set()
        for token_id in range(len(tokenizer)):
            if token_id in special_ids:
                continue
            text = tokenizer.decode(
                [token_id],
                skip_special_tokens=False,
                clean_up_tokenization_spaces=False,
            ).lstrip()
            if (
                not text
                or len(text) > 48
                or _LATIN_PREFIX.fullmatch(text) is None
                or contains_han(text)
            ):
                continue
            key = (text, token_id)
            if key in seen:
                continue
            seen.add(key)
            completions.append(LatinCompletion(text=text, token_path=(token_id,)))
        return cls(completions)

    def candidates(
        self,
        raw_keys: str,
        *,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        if _LATIN_PREFIX.fullmatch(raw_keys) is None:
            return []
        compatible = [
            completion
            for completion in self.completions
            if completion.text.casefold().startswith(raw_keys.casefold())
            and not contains_han(completion.text)
            and len(completion.token_path) <= 4
            and len(completion.text) <= 48
        ]
        first_token_ids = [completion.token_path[0] for completion in compatible]
        first_scores = (
            backend.score_allowed_tokens(state, first_token_ids)
            if first_token_ids
            else np.empty(0, dtype=np.float32)
        )
        candidates = []
        for completion, first_score in zip(compatible, first_scores, strict=True):
            # v0.2's backend seam supplies selected next-token scores. A
            # continuation worker may replace this with full per-path log
            # probabilities without changing candidate ranking semantics.
            model_score = float(first_score) / len(completion.token_path) ** 0.7
            candidates.append(
                Candidate(
                    text=completion.text,
                    pinyin="",
                    consumed_keys=len(raw_keys),
                    score=model_score,
                    context_epoch=state.epoch,
                    coverage=False,
                    completes_input=completion.text.casefold() == raw_keys.casefold(),
                    syllables=0,
                    token_id=completion.token_path[0],
                    constraint_kind=self.constraint_kind,
                    script=detect_script(completion.text),
                    model_score=model_score,
                    constraint_cost=completion.constraint_cost,
                    token_path=completion.token_path,
                    ranking_tier=0,
                )
            )
        return candidates


def _literal_candidate(raw_keys: str, epoch: int) -> Candidate:
    return Candidate(
        text=raw_keys,
        pinyin="",
        consumed_keys=len(raw_keys),
        score=None,
        context_epoch=epoch,
        coverage=True,
        completes_input=True,
        syllables=0,
        constraint_kind="literal",
        script=detect_script(raw_keys),
        model_score=None,
        # This is only a final commit escape hatch for arbitrary identifiers.
        # It is deliberately ranked after every model-scored candidate.
        constraint_cost=-1_000_000.0,
        token_path=(),
        ranking_tier=1_000_000,
    )


def rank_unified_candidates(
    candidates: Sequence[Candidate],
    *,
    context_kind: str,
    raw_keys: str,
    policy: ContextScriptPolicy,
    limit: int,
) -> list[Candidate]:
    ranked: list[Candidate] = []
    for candidate in candidates:
        script = Script(candidate.script)
        if not policy.allows(context_kind, script):
            continue
        prior = policy.language_prior(context_kind, script, raw_keys)
        model_score = candidate.model_score if candidate.model_score is not None else 0.0
        ranked.append(
            replace(
                candidate,
                language_prior=prior,
                total_score=model_score + candidate.constraint_cost + prior,
            )
        )

    def script_tier(candidate: Candidate) -> int:
        script = Script(candidate.script)
        if context_kind == "english":
            return 0 if script == Script.LATIN else 1
        return 0 if script == Script.HAN else 1

    ranked.sort(
        key=lambda candidate: (
            candidate.constraint_kind == "literal",
            script_tier(candidate),
            candidate.ranking_tier,
            -candidate.total_score,
            -candidate.consumed_keys,
            candidate.text,
            candidate.token_path,
        )
    )
    deduplicated: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for candidate in ranked:
        key = (unicodedata.normalize("NFKC", candidate.text), candidate.consumed_keys)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(candidate)
        if len(deduplicated) >= limit:
            break
    return deduplicated


class UnifiedConstraintEngine:
    def __init__(
        self,
        *,
        backend: ModelBackend,
        pinyin_constraint: Constraint | None = None,
        latin_prefix_constraint: Constraint | None = None,
        script_policy: ContextScriptPolicy | None = None,
    ) -> None:
        self.backend = backend
        self.pinyin_constraint = pinyin_constraint
        self.latin_prefix_constraint = latin_prefix_constraint or LatinPrefixConstraint(())
        self.script_policy = script_policy or ContextScriptPolicy()

    def query(
        self,
        before: str,
        raw_keys: str,
        *,
        state: BackendState | None = None,
        after_text: str = "",
        limit: int = 5,
    ) -> list[Candidate]:
        state = state or self.backend.latest_state()
        if not raw_keys:
            return []
        if state is None:
            return [_literal_candidate(raw_keys, 0)] if _LATIN_PREFIX.fullmatch(raw_keys) else []

        candidates: list[Candidate] = []
        if self.pinyin_constraint is not None:
            candidates.extend(
                self.pinyin_constraint.candidates(
                    raw_keys,
                    backend=self.backend,
                    state=state,
                    after_text=after_text,
                )
            )
        candidates.extend(
            self.latin_prefix_constraint.candidates(
                raw_keys,
                backend=self.backend,
                state=state,
                after_text=after_text,
            )
        )
        candidates = [candidate for candidate in candidates if candidate.model_score is not None]
        literal = (
            _literal_candidate(raw_keys, state.epoch) if _LATIN_PREFIX.fullmatch(raw_keys) else None
        )
        normalized_raw = unicodedata.normalize("NFKC", raw_keys).casefold()
        model_has_exact_literal = any(
            candidate.model_score is not None
            and unicodedata.normalize("NFKC", candidate.text).casefold() == normalized_raw
            and candidate.consumed_keys == len(raw_keys)
            for candidate in candidates
        )
        reserve_literal = literal is not None and not model_has_exact_literal and limit > 0
        ranked = rank_unified_candidates(
            candidates,
            context_kind=self.script_policy.classify(before),
            raw_keys=raw_keys,
            policy=self.script_policy,
            limit=max(0, limit - 1) if reserve_literal else limit,
        )
        if reserve_literal:
            ranked.extend(
                rank_unified_candidates(
                    [literal],
                    context_kind=self.script_policy.classify(before),
                    raw_keys=raw_keys,
                    policy=self.script_policy,
                    limit=1,
                )
            )
        return ranked[:limit]

    def query_pinyin(
        self,
        before: str,
        raw_keys: str,
        *,
        state: BackendState | None = None,
        after_text: str = "",
        limit: int = 5,
    ) -> list[Candidate]:
        """Return only Han candidates for an explicitly pinyin-constrained query."""

        state = state or self.backend.latest_state()
        if not raw_keys or state is None or self.pinyin_constraint is None:
            return []
        candidates = self.pinyin_constraint.candidates(
            raw_keys,
            backend=self.backend,
            state=state,
            after_text=after_text,
        )
        han_candidates = [
            candidate for candidate in candidates if Script(candidate.script) == Script.HAN
        ]
        return rank_unified_candidates(
            han_candidates,
            context_kind=self.script_policy.classify(before),
            raw_keys=raw_keys,
            policy=self.script_policy,
            limit=limit,
        )
