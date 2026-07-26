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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

