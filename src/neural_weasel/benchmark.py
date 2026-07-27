from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from .engine import NeuralPinyinEngine


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    maximum_ms: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "p50_ms": round(self.p50_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "maximum_ms": round(self.maximum_ms, 4),
        }


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(samples)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def benchmark_queries(
    engine: NeuralPinyinEngine,
    raw_pinyin: str,
    iterations: int = 1000,
    warmup: int = 100,
) -> LatencySummary:
    if iterations <= 0 or warmup < 0:
        raise ValueError("iterations must be positive and warmup non-negative")
    for _ in range(warmup):
        engine.query(raw_pinyin)

    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        engine.query(raw_pinyin)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)

    return LatencySummary(
        count=len(samples),
        p50_ms=statistics.median(samples),
        p95_ms=_percentile(samples, 0.95),
        p99_ms=_percentile(samples, 0.99),
        maximum_ms=max(samples),
    )
