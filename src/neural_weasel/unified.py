from __future__ import annotations

import math
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

    def __init__(self) -> None:
        self.stable_script: Script | None = None

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
        if context_kind == "english":
            return script not in {Script.HAN, Script.MIXED}
        return True

    def language_prior(
        self,
        context_kind: str,
        script: Script,
        raw_keys: str,
        *,
        model_margin: float | None = None,
    ) -> float:
        if context_kind == "english":
            return 0.0 if script == Script.LATIN else -math.inf
        if context_kind == "chinese":
            if script != Script.LATIN:
                return 0.0
            if self._explicit_latin_shape(raw_keys) or (
                model_margin is not None and model_margin >= 1.5
            ):
                return 0.0
            return -0.35

        preferred = self.stable_script or Script.HAN
        return 0.15 if script == preferred else 0.0

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
        for group in self.index.query_plan(parsed).groups:
            if group.token_ids is None:
                ranked: Sequence[tuple[Any, float | None]] = tuple(
                    (entry, None) for entry in group.entries
                )
            else:
                scores = backend.score_allowed_tokens(state, group.token_ids)
                positions = np.argsort(-scores, kind="stable")
                ranked = tuple(
                    (group.entries[int(position)], float(scores[int(position)]))
                    for position in positions
                )
            structural_cost = -float(group.priority[0] * 20)
            for entry, model_score in ranked:
                if after_text.startswith(entry.text):
                    continue
                consumed_letters = min(len(raw), len(entry.pinyin))
                token_path = (entry.token_id,) if entry.token_id is not None else ()
                candidates.append(
                    Candidate(
                        text=entry.text,
                        pinyin=entry.display_pinyin,
                        consumed_keys=parsed.raw_characters_for_letters(consumed_letters),
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
                    )
                )
        return candidates


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
        constraint_cost=-1_000_000.0,
        token_path=(),
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

    ranked.sort(
        key=lambda candidate: (
            -candidate.total_score,
            candidate.constraint_kind == "literal",
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
        if _LATIN_PREFIX.fullmatch(raw_keys):
            candidates.append(_literal_candidate(raw_keys, state.epoch))
        return rank_unified_candidates(
            candidates,
            context_kind=self.script_policy.classify(before),
            raw_keys=raw_keys,
            policy=self.script_policy,
            limit=limit,
        )
