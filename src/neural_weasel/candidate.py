from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    pinyin: str
    consumed_keys: int
    score: float | None
    context_epoch: int
    coverage: bool
    completes_input: bool
    syllables: int
    token_id: int | None = None
    constraint_kind: str = "pinyin"
    script: str = "han"
    model_score: float | None = None
    constraint_cost: float = 0.0
    language_prior: float = 0.0
    total_score: float = 0.0
    token_path: tuple[int, ...] = ()
    ranking_tier: int = 0
    predicted_syllables: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
