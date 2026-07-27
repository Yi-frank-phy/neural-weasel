from __future__ import annotations

from dataclasses import dataclass

import torch

from neural_weasel.backend_benchmark import benchmark_backend_pair
from neural_weasel.backends import (
    FullLogitsSnapshotBackend,
    RuntimeSnapshot,
    SparseProjectionBackend,
)


@dataclass
class FakeRuntime:
    hidden: torch.Tensor
    weight: torch.Tensor

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            torch.mv(self.weight, self.hidden).numpy(),
            before,
            after,
            20.0,
        )

    def continuation_hidden(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(self.hidden, before, after, 12.0)

    def output_weight(self):
        return self.weight

    def diagnostics(self) -> dict[str, object]:
        return {"memory": {"peak_allocated_bytes": 4096}}

    def invalidate_private_state(self) -> None:
        pass


def test_backend_benchmark_records_consistency_latency_and_memory() -> None:
    """AT-MB-04/05: one harness compares both paths on identical token sets."""
    torch.manual_seed(4)
    runtime = FakeRuntime(
        hidden=torch.randn(8, dtype=torch.float32),
        weight=torch.randn(32, 8, dtype=torch.float32),
    )
    report = benchmark_backend_pair(
        FullLogitsSnapshotBackend(runtime),
        SparseProjectionBackend(runtime),
        before="The protocol is",
        after="",
        allowed_token_sets=[tuple(range(4)), tuple(range(16)), tuple(range(32))],
        iterations=5,
    ).to_dict()

    assert report["full_publication_latency_ms"] >= 20.0
    assert report["sparse_publication_latency_ms"] >= 12.0
    assert report["memory"]["peak_allocated_bytes"] == 4096
    assert [row["allowed_token_count"] for row in report["queries"]] == [4, 16, 32]
    for row in report["queries"]:
        assert row["top_1_equal"] is True
        assert row["top_k_set_equal"] is True
        assert row["max_abs_score_error"] <= 1e-4
        assert row["full_query_ms_p50"] > 0
        assert row["sparse_query_ms_p50"] > 0
