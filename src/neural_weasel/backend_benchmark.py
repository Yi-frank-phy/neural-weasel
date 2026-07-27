from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from .backends import FullLogitsSnapshotBackend, SparseProjectionBackend


@dataclass(frozen=True, slots=True)
class BackendQueryBenchmark:
    allowed_token_count: int
    top_1_equal: bool
    top_k_set_equal: bool
    max_abs_score_error: float
    full_query_ms_p50: float
    full_query_ms_p95: float
    sparse_query_ms_p50: float
    sparse_query_ms_p95: float


@dataclass(frozen=True, slots=True)
class BackendPairReport:
    full_publication_latency_ms: float
    sparse_publication_latency_ms: float
    memory: dict[str, object]
    queries: tuple[BackendQueryBenchmark, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _measure(call, iterations: int) -> tuple[np.ndarray, list[float]]:
    measurements = []
    result = None
    for _ in range(iterations):
        started = time.perf_counter()
        result = call()
        measurements.append((time.perf_counter() - started) * 1000)
    assert result is not None
    return result, measurements


def _percentile(values: Sequence[float], value: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), value))


def benchmark_backend_pair(
    full: FullLogitsSnapshotBackend,
    sparse: SparseProjectionBackend,
    *,
    before: str,
    after: str,
    allowed_token_sets: Sequence[Sequence[int]],
    iterations: int = 20,
) -> BackendPairReport:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    full_state = full.update_context(before, after)
    sparse_state = sparse.update_context(before, after)

    rows = []
    for allowed in allowed_token_sets:
        token_ids = tuple(allowed)
        full_scores, full_times = _measure(
            lambda ids=token_ids: full.score_allowed_tokens(full_state, ids),
            iterations,
        )
        sparse_scores, sparse_times = _measure(
            lambda ids=token_ids: sparse.score_allowed_tokens(sparse_state, ids),
            iterations,
        )
        top_k = min(5, len(token_ids))
        full_order = np.argsort(-full_scores, kind="stable")
        sparse_order = np.argsort(-sparse_scores, kind="stable")
        rows.append(
            BackendQueryBenchmark(
                allowed_token_count=len(token_ids),
                top_1_equal=(
                    not token_ids
                    or token_ids[int(full_order[0])] == token_ids[int(sparse_order[0])]
                ),
                top_k_set_equal={token_ids[int(index)] for index in full_order[:top_k]}
                == {token_ids[int(index)] for index in sparse_order[:top_k]},
                max_abs_score_error=(
                    float(np.max(np.abs(full_scores - sparse_scores))) if token_ids else 0.0
                ),
                full_query_ms_p50=_percentile(full_times, 50),
                full_query_ms_p95=_percentile(full_times, 95),
                sparse_query_ms_p50=_percentile(sparse_times, 50),
                sparse_query_ms_p95=_percentile(sparse_times, 95),
            )
        )

    diagnostics = sparse.diagnostics()
    memory = diagnostics.get("memory")
    return BackendPairReport(
        full_publication_latency_ms=full_state.publication_latency_ms,
        sparse_publication_latency_ms=sparse_state.publication_latency_ms,
        memory=dict(memory) if isinstance(memory, dict) else {},
        queries=tuple(rows),
    )

