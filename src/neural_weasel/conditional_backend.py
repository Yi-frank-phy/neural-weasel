from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from .backends import BackendState, FullLogitsSnapshotBackend
from .qwen_continuation import QwenContinuationSession


class ConditionalFullLogitsBackend(FullLogitsSnapshotBackend):
    """Full-logits backend with a fallback-only conditional continuation seam."""

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime)
        self._continuation_gate = threading.Lock()

    def update_context(self, before: str, after: str = "") -> BackendState:
        with self._continuation_gate:
            return super().update_context(before, after)

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
