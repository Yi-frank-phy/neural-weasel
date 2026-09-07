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
    # Opaque, in-memory-only model continuation root captured at exactly the
    # same decode point as ``payload``. It must never contain raw editor text.
    continuation_root: Any | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ContinuationAttempt:
    """One bounded continuation attempt with an optional retry generation."""

    result: Any = field(default=None, repr=False)
    retry_generation: int | None = None


@dataclass(frozen=True, slots=True)
class BackendState:
    epoch: int
    backend_kind: str
    before_hash: str
    after_hash: str
    created_monotonic: float
    publication_latency_ms: float
    payload: Any = field(repr=False)
    continuation_root: Any | None = field(default=None, repr=False)
    _backend_identity: int = field(repr=False, default=0)
    _generation: int = field(repr=False, default=0)


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

    def _capture_generation(self) -> int:
        with self._lock:
            return self._generation

    def _publish(
        self,
        runtime_snapshot: RuntimeSnapshot,
        payload: Any,
        *,
        expected_generation: int,
    ) -> BackendState:
        publication_started = time.perf_counter()
        with self._lock:
            if expected_generation != self._generation:
                raise RuntimeError("backend state was invalidated during context update")
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
                continuation_root=runtime_snapshot.continuation_root,
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

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime)
        self._continuation_gate = threading.Lock()
        self._continuation_active = False
        self._continuation_generation = 0
        self._continuation_idle_waiters: set[threading.Event] = set()

    def update_context(self, before: str, after: str = "") -> BackendState:
        generation = self._capture_generation()
        result = self.runtime.full_logits(before, after)
        logits = np.asarray(result.payload, dtype=np.float32).copy()
        if logits.ndim != 1:
            raise ValueError("full logits snapshot must be one-dimensional")
        logits.flags.writeable = False
        return self._publish(result, logits, expected_generation=generation)

    def score_allowed_tokens(
        self,
        state: BackendState,
        allowed_token_ids: Sequence[int],
    ) -> np.ndarray:
        self._validate_state(state)
        logits = state.payload
        token_ids = self._validated_token_ids(allowed_token_ids, logits.size)
        return np.asarray(logits[token_ids], dtype=np.float32)

    def _finish_continuation_slot(self) -> None:
        with self._continuation_gate:
            self._continuation_active = False
            self._continuation_generation += 1
            waiters = tuple(self._continuation_idle_waiters)
            self._continuation_idle_waiters.clear()
        for waiter in waiters:
            waiter.set()

    def register_continuation_idle_wait(
        self,
        generation: int,
        event: threading.Event,
    ) -> None:
        """Wake ``event`` once the continuation occupying ``generation`` is gone."""

        with self._continuation_gate:
            if not self._continuation_active or generation != self._continuation_generation:
                event.set()
                return
            self._continuation_idle_waiters.add(event)

    def cancel_continuation_idle_wait(self, event: threading.Event) -> None:
        with self._continuation_gate:
            self._continuation_idle_waiters.discard(event)

    def continuation_busy_generation(self) -> int | None:
        """Return the generation currently occupying continuation, if any."""

        with self._continuation_gate:
            if not self._continuation_active:
                return None
            return self._continuation_generation

    def _continue_from_root_attempt(
        self,
        root: Any,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> ContinuationAttempt:
        """Run one continuation and identify retryable single-flight misses."""

        if deadline_ms <= 0:
            return ContinuationAttempt()
        provider = getattr(self.runtime, "continue_from_root", None)
        if not callable(provider):
            return ContinuationAttempt()

        paths = tuple(tuple(int(token_id) for token_id in path) for path in token_paths)
        # Full-vocabulary scoring shares the same immutable token set across
        # every beam. Convert it once, avoiding millions of Python operations
        # that contend with the foreground pipe threads for the GIL.
        converted_sets: dict[int, tuple[int, ...]] = {}
        allowed_sets_list = []
        for allowed in allowed_token_sets:
            key = id(allowed)
            if key not in converted_sets:
                converted_sets[key] = tuple(int(token_id) for token_id in allowed)
            allowed_sets_list.append(converted_sets[key])
        allowed_sets = tuple(allowed_sets_list)
        deadline = time.monotonic() + deadline_ms / 1000.0
        with self._continuation_gate:
            generation = self._continuation_generation
            if self._continuation_active:
                return ContinuationAttempt(retry_generation=generation)
            self._continuation_active = True

        done = threading.Event()
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                remaining_ms = min(
                    float(deadline_ms),
                    max(0.0, (deadline - time.monotonic()) * 1000.0),
                )
                if remaining_ms <= 0:
                    box["result"] = None
                else:
                    box["result"] = provider(
                        root,
                        paths,
                        allowed_sets,
                        deadline_ms=remaining_ms,
                    )
            except Exception as error:  # Re-raised only if the caller is still waiting.
                box["error"] = error
            finally:
                self._finish_continuation_slot()
                done.set()

        thread = threading.Thread(
            target=worker,
            name="neural-weasel-candidate-continuation",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            self._finish_continuation_slot()
            raise

        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0 or not done.wait(remaining):
            return ContinuationAttempt(retry_generation=generation)
        error = box.get("error")
        if isinstance(error, Exception):
            raise error
        return ContinuationAttempt(result=box.get("result"))

    def _continue_from_root_bounded(
        self,
        root: Any,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> Any:
        """Compatibility wrapper returning only the bounded attempt result."""

        return self._continue_from_root_attempt(
            root, token_paths, allowed_token_sets, deadline_ms=deadline_ms
        ).result

    @property
    def continue_from_root_attempt(self) -> Any:
        provider = getattr(self.runtime, "continue_from_root", None)
        return self._continue_from_root_attempt if callable(provider) else None

    @property
    def continue_from_root(self) -> Any:
        """Expose exact-root continuation through a deadline-bounded wrapper."""

        provider = getattr(self.runtime, "continue_from_root", None)
        return self._continue_from_root_bounded if callable(provider) else None

    @property
    def continue_from_empty(self) -> Any:
        """Legacy compatibility seam; new candidate paging does not use it."""

        provider = getattr(self.runtime, "continue_from_empty", None)
        return provider if callable(provider) else None


class SparseProjectionBackend(_SnapshotBackend):
    """Project an immutable continuation hidden state onto selected lm-head rows."""

    backend_kind = "sparse_projection"

    def update_context(self, before: str, after: str = "") -> BackendState:
        generation = self._capture_generation()
        result = self.runtime.continuation_hidden(before, after)
        hidden = result.payload.detach()
        if hidden.ndim == 2 and hidden.shape[0] == 1:
            hidden = hidden[0]
        if hidden.ndim != 1:
            raise ValueError("continuation hidden state must be one-dimensional")
        return self._publish(result, hidden, expected_generation=generation)

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
