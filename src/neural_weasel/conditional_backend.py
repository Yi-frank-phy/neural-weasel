from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from .backends import BackendState, FullLogitsSnapshotBackend
from .qwen_continuation import QwenContinuationSession

DEFAULT_CONDITIONAL_BUDGET_MS = 250.0


class ConditionalFullLogitsBackend(FullLogitsSnapshotBackend):
    """Full-logits backend with a fallback-only conditional continuation seam."""

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime)
        self._continuation_gate = threading.Lock()
        self._startup_forward_latency_ms: float | None = None
        self._latest_context_forward_latency_ms: float | None = None
        self._conditional_forward_latency_ms: float | None = None

    def update_context(self, before: str, after: str = "") -> BackendState:
        with self._continuation_gate:
            state = super().update_context(before, after)
            if self._startup_forward_latency_ms is None:
                self._startup_forward_latency_ms = state.publication_latency_ms
            self._latest_context_forward_latency_ms = state.publication_latency_ms
            return state

    def conditional_continuation_within_budget(
        self,
        state: BackendState,
        budget_ms: float,
    ) -> bool:
        self._validate_state(state)
        latency_ms = self._estimated_conditional_latency_ms()
        return latency_ms is not None and latency_ms < budget_ms

    def record_conditional_continuation_latency(
        self,
        state: BackendState,
        latency_ms: float,
    ) -> None:
        self._validate_state(state)
        with self._continuation_gate:
            previous = self._conditional_forward_latency_ms
            if previous is None or latency_ms > previous:
                self._conditional_forward_latency_ms = float(latency_ms)

    def score_allowed_sequence_start(
        self, state: BackendState, allowed_token_ids: Sequence[int]
    ) -> np.ndarray:
        self._validate_state(state)
        logits = state.payload
        token_ids = self._validated_token_ids(allowed_token_ids, logits.size)
        if token_ids.size == 0:
            return np.empty(0, dtype=np.float32)
        values = np.asarray(logits, dtype=np.float64)
        maximum = float(values.max())
        log_normalizer = maximum + float(np.log(np.exp(values - maximum).sum()))
        return np.asarray(values[token_ids] - log_normalizer, dtype=np.float32)

    def start_conditional_continuation(self, state: BackendState):
        self._validate_state(state)
        with self._continuation_gate:
            with self._lock:
                if self._state is not state:
                    return None
            return QwenContinuationSession.from_runtime(self.runtime)

    def diagnostics(self) -> dict[str, object]:
        diagnostics = super().diagnostics()
        latency_ms = self._estimated_conditional_latency_ms()
        diagnostics.update(
            {
                "conditional_startup_forward_latency_ms": self._startup_forward_latency_ms,
                "conditional_latest_context_latency_ms": self._latest_context_forward_latency_ms,
                "conditional_observed_forward_latency_ms": self._conditional_forward_latency_ms,
                "conditional_estimated_forward_latency_ms": latency_ms,
                "conditional_continuation_enabled": (
                    latency_ms is not None and latency_ms < DEFAULT_CONDITIONAL_BUDGET_MS
                ),
            }
        )
        return diagnostics

    def _estimated_conditional_latency_ms(self) -> float | None:
        if self._conditional_forward_latency_ms is not None:
            return self._conditional_forward_latency_ms
        return self._latest_context_forward_latency_ms
