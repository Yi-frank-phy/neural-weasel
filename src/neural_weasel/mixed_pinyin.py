from __future__ import annotations

from .backends import BackendState, ModelBackend
from .candidate import Candidate
from .pinyin import parse_raw_pinyin
from .pinyin_beam import run_beam
from .pinyin_partial import PartialPinyinMatcher
from .pinyin_single import single_partial
from .unified import PinyinConstraint, detect_script


class MixedPinyinConstraint(PinyinConstraint):
    def __init__(
        self,
        index,
        *,
        beam_width: int = 4,
        max_tokens: int = 4,
        budget_ms: float = 80.0,
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
        if ordinary:
            return ordinary
        try:
            parsed = parse_raw_pinyin(raw_keys)
        except ValueError:
            return []
        raw = parsed.compact
        if not raw or parsed.explicit_boundaries:
            return []
        single = single_partial(raw_keys, raw, self.matcher, backend, state, after_text)
        if single:
            return single
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
                score=beam.model_score,
                context_epoch=state.epoch,
                coverage=False,
                completes_input=True,
                syllables=len(beam.syllables),
                token_id=beam.path[0],
                constraint_kind=self.constraint_kind,
                script=detect_script(beam.text),
                model_score=beam.model_score,
                constraint_cost=beam.cost,
                token_path=beam.path,
            )
            for beam in beams
            if not after_text.startswith(beam.text)
        ]
