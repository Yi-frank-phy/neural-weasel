from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="backend tensor tests require Torch")

from neural_weasel.backends import (
    FullLogitsSnapshotBackend,
    ModelBackend,
    RuntimeSnapshot,
    SparseProjectionBackend,
)


@dataclass
class FakeRuntime:
    logits: np.ndarray
    hidden: torch.Tensor
    lm_head_weight: torch.Tensor
    loaded: bool = False
    full_calls: int = 0
    hidden_calls: int = 0
    invalidations: int = 0

    def load(self) -> None:
        self.loaded = True

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.full_calls += 1
        return RuntimeSnapshot(
            payload=self.logits,
            before_hash=f"before:{before}",
            after_hash=f"after:{after}",
            latency_ms=12.5,
        )

    def continuation_hidden(self, before: str, after: str) -> RuntimeSnapshot:
        self.hidden_calls += 1
        return RuntimeSnapshot(
            payload=self.hidden,
            before_hash=f"before:{before}",
            after_hash=f"after:{after}",
            latency_ms=7.0,
        )

    def output_weight(self) -> torch.Tensor:
        return self.lm_head_weight

    def diagnostics(self) -> dict[str, object]:
        return {"runtime": "fake"}

    def invalidate_private_state(self) -> None:
        self.invalidations += 1


@pytest.fixture
def runtime() -> FakeRuntime:
    hidden = torch.tensor([0.25, -0.5, 1.0], dtype=torch.float32)
    weight = torch.tensor(
        [
            [0.2, 0.1, 0.0],
            [1.0, -0.5, 0.25],
            [-0.25, 0.4, 0.8],
            [0.75, 0.25, -0.5],
            [0.1, -0.1, 0.3],
        ],
        dtype=torch.float32,
    )
    logits = torch.mv(weight, hidden).numpy()
    return FakeRuntime(logits=logits, hidden=hidden, lm_head_weight=weight)


def test_backends_satisfy_minimal_model_backend_protocol(runtime: FakeRuntime) -> None:
    """AT-MB-01/06: both paths expose the same minimal backend contract."""
    full: ModelBackend = FullLogitsSnapshotBackend(runtime)
    sparse: ModelBackend = SparseProjectionBackend(runtime)

    for backend in (full, sparse):
        backend.load()
        state = backend.update_context("context", "")
        assert backend.latest_state() is state
        assert backend.diagnostics()["backend_kind"] in {"full_logits", "sparse_projection"}


def test_full_logits_indexes_only_allowed_tokens(runtime: FakeRuntime) -> None:
    """AT-MB-02/03: cached scoring indexes immutable logits and never refreshes."""
    backend = FullLogitsSnapshotBackend(runtime)
    state = backend.update_context("context", "")
    refresh_calls = runtime.full_calls

    scores = backend.score_allowed_tokens(state, [3, 1])

    np.testing.assert_allclose(scores, runtime.logits[[3, 1]], rtol=0, atol=0)
    assert runtime.full_calls == refresh_calls
    assert runtime.hidden_calls == 0
    assert state.payload.flags.writeable is False


def test_sparse_projection_matches_full_logits(runtime: FakeRuntime) -> None:
    """AT-MB-04: selected lm-head rows match the full projection."""
    full = FullLogitsSnapshotBackend(runtime)
    sparse = SparseProjectionBackend(runtime)
    full_state = full.update_context("same", "")
    sparse_state = sparse.update_context("same", "")
    allowed = [4, 1, 3, 2]

    full_scores = full.score_allowed_tokens(full_state, allowed)
    sparse_scores = sparse.score_allowed_tokens(sparse_state, allowed)

    np.testing.assert_allclose(sparse_scores, full_scores, rtol=0, atol=1e-4)
    assert allowed[int(np.argmax(sparse_scores))] == allowed[int(np.argmax(full_scores))]
    assert {allowed[index] for index in np.argsort(-sparse_scores)[:3]} == {
        allowed[index] for index in np.argsort(-full_scores)[:3]
    }


def test_state_from_another_backend_or_generation_is_rejected(runtime: FakeRuntime) -> None:
    """AT-MB-07: invalidation makes older private state unqueryable."""
    backend = SparseProjectionBackend(runtime)
    state = backend.update_context("private", "")

    backend.invalidate_private_state()

    with pytest.raises(RuntimeError, match="stale|backend"):
        backend.score_allowed_tokens(state, [1, 2])
    assert backend.latest_state() is None
    assert runtime.invalidations == 1


def test_snapshot_age_is_diagnostic_not_a_rejection(runtime: FakeRuntime) -> None:
    """AT-RT-04: an old immutable snapshot remains queryable."""
    backend = FullLogitsSnapshotBackend(runtime)
    state = backend.update_context("old", "")
    object.__setattr__(state, "created_monotonic", state.created_monotonic - 10.0)

    scores = backend.score_allowed_tokens(state, [0])

    assert scores.shape == (1,)
    assert backend.diagnostics()["snapshot_age_ms"] >= 10_000
