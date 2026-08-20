from __future__ import annotations

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .pinyin import parse_raw_pinyin
from .pinyin_beam import exact_token_path_exists, run_beam
from .pinyin_partial import PartialPinyinMatcher
from .pinyin_single import single_partial
from .unified import PinyinConstraint, _pinyin_query_variants, detect_script

# The native AiTranslator gives the pipe query a 6 ms absolute deadline
# (native/rime/ai_translator.h). The fallback below is CPU-only and
# fail-closed; the same budget bounds its snapshot scoring.
QUERY_FALLBACK_BUDGET_MS = 6.0


class MixedPinyinConstraint(PinyinConstraint):
    def __init__(
        self,
        index,
        *,
        beam_width: int = 4,
        max_tokens: int = 4,
        budget_ms: float = QUERY_FALLBACK_BUDGET_MS,
    ) -> None:
        super().__init__(index)
        if beam_width < 1 or not 2 <= max_tokens <= 4 or budget_ms <= 0:
            raise ValueError("invalid mixed-pinyin search budget")
        self.matcher = PartialPinyinMatcher(index)
        self.beam_width = beam_width
        self.max_tokens = max_tokens
        self.budget_ms = float(budget_ms)

    def candidates(
        self,
        raw_keys: str,
        *,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        ordinary = super().candidates(raw_keys, backend=backend, state=state, after_text=after_text)
        if ordinary and any(candidate.fuzzy_cost == 0 for candidate in ordinary):
            return ordinary
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return []
        raw = parsed.compact
        if not raw or parsed.explicit_boundaries:
            return ordinary
        if exact_token_path_exists(raw, self.matcher, self.max_tokens, min_tokens=2):
            exact = self._beam_candidates(raw_keys, raw, backend, state, after_text)
            relaxed = self._relaxed_beam_candidates(
                parsed,
                raw_keys,
                backend,
                state,
                after_text,
            )
            return [*exact[:2], *relaxed] if relaxed else exact
        if ordinary:
            return ordinary
        single = single_partial(raw_keys, raw, self.matcher, backend, state, after_text)
        if single:
            return single
        return self._beam_candidates(raw_keys, raw, backend, state, after_text)

    def _beam_candidates(
        self,
        raw_keys: str,
        raw: str,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
        *,
        base_fuzzy_cost: int = 0,
    ) -> list[Candidate]:
        beams = run_beam(
            raw,
            self.matcher,
            backend,
            state,
            self.beam_width,
            self.max_tokens,
            self.budget_ms,
        )
        return [
            Candidate(
                text=beam.text,
                pinyin="'".join(beam.syllables),
                consumed_keys=len(raw_keys),
                score=beam.candidate_score,
                context_epoch=state.epoch,
                coverage=False,
                completes_input=True,
                syllables=len(beam.syllables),
                token_id=beam.path[0],
                constraint_kind=self.constraint_kind,
                script=detect_script(beam.text),
                model_score=beam.model_score,
                constraint_cost=beam.cost - 0.08 * base_fuzzy_cost,
                token_path=beam.path,
                fuzzy_cost=base_fuzzy_cost + (0 if beam.cost == 0 else 1),
            )
            for beam in beams
            if not after_text.startswith(beam.text)
        ]

    def _relaxed_beam_candidates(
        self,
        parsed,
        raw_keys: str,
        backend: ModelBackend,
        state: BackendState,
        after_text: str,
    ) -> list[Candidate]:
        target_cost: int | None = None
        searched = 0
        candidates: list[Candidate] = []
        for variant, fuzzy_cost in _pinyin_query_variants(parsed, self.fuzzy_aliases):
            if fuzzy_cost == 0:
                continue
            if target_cost is not None and fuzzy_cost > target_cost:
                break
            raw = variant.compact
            if not exact_token_path_exists(raw, self.matcher, self.max_tokens, min_tokens=2):
                continue
            target_cost = fuzzy_cost
            candidates.extend(
                self._beam_candidates(
                    raw_keys,
                    raw,
                    backend,
                    state,
                    after_text,
                    base_fuzzy_cost=fuzzy_cost,
                )
            )
            searched += 1
            if searched >= 4:
                break
        return candidates
