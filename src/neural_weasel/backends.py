from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One runtime result before backend-specific immutable publication."""

    payload: Any = field(repr=False)
    before_hash: str
    after_hash: str
    latency_ms: float


@dataclass(frozen=True, slots=True)
class BackendState:
    epoch: int
    backend_kind: str
    before_hash: str
    after_hash: str
    created_monotonic: float
    publication_latency_ms: float
    payload: Any = field(repr=False)
    _backend_identity: int = field(repr=False)
    _generation: int = field(repr=False)


@runtime_checkable
class ModelBackend(Protocol):
    def load(self) -> None: ...

    def update_context(self, before: str, after: str = "") -> BackendState: ...

    def latest_state(self) -> BackendState | None: ...

    def score_allowed_tokens(
        self,
        state: BackendState,
        allowed_token_ids: Sequence[int],
    ) -> np.ndarray: ...

    def diagnostics(self) -> dict[str, object]: ...

    def invalidate_private_state(self) -> None: ...


class _SnapshotBackend:
    backend_kind: str

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._lock = threading.Lock()
        self._state: BackendState | None = None
        self._epoch = 0
        self._generation = 0
        self._identity = id(self)

    def load(self) -> None:
        self.runtime.load()

    def latest_state(self) -> BackendState | None:
        with self._lock:
            return self._state

    def _publish(self, runtime_snapshot: RuntimeSnapshot, payload: Any) -> BackendState:
        publication_started = time.perf_counter()
        with self._lock:
            self._epoch += 1
            state = BackendState(
                epoch=self._epoch,
                backend_kind=self.backend_kind,
                before_hash=runtime_snapshot.before_hash,
                after_hash=runtime_snapshot.after_hash,
                created_monotonic=time.monotonic(),
                publication_latency_ms=runtime_snapshot.latency_ms
                + (time.perf_counter() - publication_started) * 1000,
                payload=payload,
                _backend_identity=self._identity,
                _generation=self._generation,
            )
            self._state = state
            return state

    def _validate_state(self, state: BackendState) -> None:
        with self._lock:
            if (
                state._backend_identity != self._identity
                or state._generation != self._generation
                or state.backend_kind != self.backend_kind
            ):
                raise RuntimeError("stale state or state from another backend")

    @staticmethod
    def _validated_token_ids(
        allowed_token_ids: Sequence[int],
        vocabulary_size: int,
    ) -> np.ndarray:
        token_ids = np.asarray(tuple(allowed_token_ids), dtype=np.int64)
        if token_ids.ndim != 1:
            raise ValueError("allowed_token_ids must be one-dimensional")
        if token_ids.size and (int(token_ids.min()) < 0 or int(token_ids.max()) >= vocabulary_size):
            raise IndexError("allowed token id is outside the model vocabulary")
        return token_ids

    def invalidate_private_state(self) -> None:
        with self._lock:
            self._generation += 1
            self._state = None
        self.runtime.invalidate_private_state()

    def diagnostics(self) -> dict[str, object]:
        diagnostics = dict(self.runtime.diagnostics())
        with self._lock:
            state = self._state
        diagnostics.update(
            {
                "backend_kind": self.backend_kind,
                "state_epoch": state.epoch if state is not None else 0,
                "snapshot_age_ms": (
                    max(0.0, (time.monotonic() - state.created_monotonic) * 1000)
                    if state is not None
                    else None
                ),
                "snapshot_publication_latency_ms": (
                    state.publication_latency_ms if state is not None else None
                ),
            }
        )
        return diagnostics


class FullLogitsSnapshotBackend(_SnapshotBackend):
    """Correctness baseline: immutable CPU full-vocabulary logits."""

    backend_kind = "full_logits"

    def update_context(self, before: str, after: str = "") -> BackendState:
        result = self.runtime.full_logits(before, after)
        logits = np.asarray(result.payload, dtype=np.float32).copy()
        if logits.ndim != 1:
            raise ValueError("full logits snapshot must be one-dimensional")
        logits.flags.writeable = False
        return self._publish(result, logits)

    def score_allowed_tokens(
        self,
        state: BackendState,
        allowed_token_ids: Sequence[int],
    ) -> np.ndarray:
        self._validate_state(state)
        logits = state.payload
        token_ids = self._validated_token_ids(allowed_token_ids, logits.size)
        return np.asarray(logits[token_ids], dtype=np.float32)


class SparseProjectionBackend(_SnapshotBackend):
    """Project an immutable continuation hidden state onto selected lm-head rows."""

    backend_kind = "sparse_projection"

    def update_context(self, before: str, after: str = "") -> BackendState:
        result = self.runtime.continuation_hidden(before, after)
        hidden = result.payload.detach()
        if hidden.ndim == 2 and hidden.shape[0] == 1:
            hidden = hidden[0]
        if hidden.ndim != 1:
            raise ValueError("continuation hidden state must be one-dimensional")
        return self._publish(result, hidden)

    def score_allowed_tokens(
        self,
        state: BackendState,
        allowed_token_ids: Sequence[int],
    ) -> np.ndarray:
        self._validate_state(state)
        weight = self.runtime.output_weight()
        token_ids = self._validated_token_ids(allowed_token_ids, int(weight.shape[0]))
        if token_ids.size == 0:
            return np.empty(0, dtype=np.float32)

        torch = __import__("torch")
        index = torch.as_tensor(token_ids, dtype=torch.long, device=weight.device)
        selected_weight = weight.index_select(0, index)
        hidden = state.payload.to(device=weight.device, dtype=selected_weight.dtype)
        with torch.inference_mode():
            scores = torch.mv(selected_weight, hidden)
            output_bias = getattr(self.runtime, "output_bias", None)
            if callable(output_bias):
                bias = output_bias()
                if bias is not None:
                    scores = scores + bias.index_select(0, index)
        return np.asarray(scores.float().cpu().numpy(), dtype=np.float32)
