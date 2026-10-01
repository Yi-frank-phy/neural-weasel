from __future__ import annotations

import ctypes
import hashlib
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np

from .acquire_model import AcquiredGguf
from .backends import RuntimeSnapshot
from .context_prompt import ContextPromptConfig, ContextPromptEncoder
from .gpu import (
    GpuBindingError,
    NvidiaGpu,
    discover_target_gpu,
    require_full_gguf_offload,
    verify_expected_nvidia_binding,
)
from .llama_vocab import LlamaVocabAdapter

DEFAULT_MAX_BEFORE_TOKENS = 23552
DEFAULT_N_CTX = 24576
DEFAULT_N_BATCH = 512
DEFAULT_PARALLEL_SEQUENCES = 1
CANDIDATE_WARMUP_MAX_PATH_TOKENS = 4
# The production runtime fully offloads model layers and K/Q/V to CUDA.  Keep
# llama.cpp's host-side workers bounded so the same process always retains CPU
# scheduling headroom for the latency-critical named-pipe candidate thread.
# llama-cpp-python otherwise defaults batch work to every logical CPU.
DEFAULT_LLAMA_CPU_THREADS = 4


@dataclass(frozen=True, slots=True)
class LlamaContinuationRoot:
    """Opaque in-memory sequence state captured at one root-logits decode point.

    The serialized llama.cpp sequence state contains KV/model state but no raw
    editor string. It is never written to disk or exposed through diagnostics.
    """

    state_bytes: bytes = field(repr=False)
    n_tokens: int
    replay_token_ids: tuple[int, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class GgufLogitsSnapshot:
    epoch: int
    before_hash: str
    after_hash: str
    logits: np.ndarray = field(repr=False)
    created_monotonic: float
    latency_ms: float
    continuation_root: LlamaContinuationRoot | None = field(default=None, repr=False)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _copy_sequence_state_bytes(buffer: Any, size: int) -> bytes:
    """Copy one llama.cpp state buffer without materializing Python integers."""

    return ctypes.string_at(buffer, size)


def _default_llama_factory(model_path: str, **kwargs: object):
    from llama_cpp import Llama, llama_cpp

    parallel_sequences = int(kwargs.pop("_parallel_sequences", 1))
    original_default_params = llama_cpp.llama_context_default_params

    def parallel_default_params():
        params = original_default_params()
        params.n_seq_max = parallel_sequences
        return params

    # llama-cpp-python 0.3.23 only exposes n_seq_max through the low-level
    # context params. Construction is serialized by process startup, and the
    # module hook is restored immediately after Llama has copied the params.
    llama_cpp.llama_context_default_params = parallel_default_params
    try:
        return Llama(model_path=model_path, **kwargs)
    finally:
        llama_cpp.llama_context_default_params = original_default_params


def _default_cuda_backend_probe() -> bool:
    try:
        from llama_cpp import llama_cpp

        info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
    except Exception:
        return False
    return "cuda" in info.lower()


def _llama_cpp_version() -> str:
    try:
        return version("llama-cpp-python")
    except PackageNotFoundError:
        return "unknown"


class LlamaCppBackend:
    """Qwen3.5-4B-Base GGUF runtime with fail-closed CUDA full offload."""

    def __init__(
        self,
        acquired: AcquiredGguf,
        *,
        max_before_tokens: int = DEFAULT_MAX_BEFORE_TOKENS,
        n_ctx: int = DEFAULT_N_CTX,
        n_batch: int = DEFAULT_N_BATCH,
        prompt_config: ContextPromptConfig | None = None,
        llama_factory: Callable[..., Any] | None = None,
        cuda_backend_probe: Callable[[], bool] | None = None,
        gpu_before_probe: Callable[[], NvidiaGpu] | None = None,
        gpu_after_probe: Callable[[], NvidiaGpu] | None = None,
    ) -> None:
        if max_before_tokens < 1:
            raise ValueError("max_before_tokens must be positive")
        if n_ctx < 1:
            raise ValueError("n_ctx must be positive")
        if n_batch < 1:
            raise ValueError("n_batch must be positive")
        if max_before_tokens > n_ctx:
            raise ValueError("max_before_tokens must not exceed n_ctx")
        self.prompt_config = prompt_config or ContextPromptConfig()
        self.prompt_config.validate_window(n_ctx)

        artifact = acquired.artifact
        self.model_id = artifact.model_id
        self.format = artifact.format
        self.quantization = artifact.quantization
        self.runtime_name = "llama.cpp"
        self.model_path = acquired.path
        self.gguf_sha256 = acquired.sha256
        self.model_revision = artifact.revision
        self.max_before_tokens = int(max_before_tokens)
        self.n_ctx = int(n_ctx)
        self.n_batch = int(n_batch)
        self._lock = threading.Lock()
        self._context_waiters_lock = threading.Lock()
        self._context_waiters = 0
        self._epoch = 0
        self._cached_token_ids: tuple[int, ...] | None = None
        self._cached_logits: np.ndarray | None = None
        self._continuation_state_token_ids: tuple[int, ...] | None = None
        self._continuation_state_buffer: Any | None = None
        self._continuation_state_size = 0
        # Replaced atomically after each operation. These immutable tuples contain
        # only counts, booleans, enum-like reasons, and elapsed time; never editor
        # text, candidate text, token paths, or hashes.
        self._last_refresh_diagnostics: tuple[int, int, float, float, float] | None = None
        self._last_continuation_diagnostics: (
            tuple[float, float, int, int, int, bool, str] | None
        ) = None

        cuda_backend_probe = cuda_backend_probe or _default_cuda_backend_probe
        if not cuda_backend_probe():
            raise GpuBindingError(
                "llama.cpp CUDA backend is unavailable; CPU fallback is forbidden"
            )

        gpu_before_probe = gpu_before_probe or verify_expected_nvidia_binding
        self._gpu_probe = gpu_after_probe or discover_target_gpu
        before_gpu = gpu_before_probe()

        factory = llama_factory or _default_llama_factory
        self.llama = factory(
            model_path=str(acquired.path),
            n_gpu_layers=-1,
            main_gpu=0,
            # The editor prefix is shared by every candidate. Keep one KV
            # sequence instead of reserving a full context per beam branch;
            # continuation restores the shared root around each short suffix.
            n_ctx=self.n_ctx,
            n_batch=self.n_batch,
            n_threads=DEFAULT_LLAMA_CPU_THREADS,
            n_threads_batch=DEFAULT_LLAMA_CPU_THREADS,
            logits_all=False,
            offload_kqv=True,
            use_mmap=True,
            verbose=False,
            _parallel_sequences=DEFAULT_PARALLEL_SEQUENCES,
        )

        after_gpu = self._gpu_probe()
        self.gpu_vram_load_delta_mib = require_full_gguf_offload(before_gpu, after_gpu)
        self.target_gpu = after_gpu
        self.tokenizer = LlamaVocabAdapter(self.llama)
        self.vocab_fingerprint = self.tokenizer.fingerprint
        self._prompt_encoder = ContextPromptEncoder(self.llama, self.prompt_config)
        self._smoke_forward()

    def load(self) -> None:
        """Construction performs the one-time validated model load."""

    def _fallback_token(self) -> int:
        for name in ("token_bos", "token_eos"):
            getter = getattr(self.llama, name, None)
            if callable(getter):
                token_id = int(getter())
                if 0 <= token_id < len(self.tokenizer):
                    return token_id
        raise RuntimeError("GGUF vocabulary has no BOS/EOS token usable for empty context")

    def _tokenize_context(self, before: str) -> tuple[int, ...]:
        token_ids = tuple(self.tokenizer.encode(before, add_special_tokens=False))
        if not token_ids:
            token_ids = (self._fallback_token(),)
        return token_ids[-self.max_before_tokens :]

    def _copy_last_logits(self, *, require_finite: bool = True) -> np.ndarray:
        context = getattr(self.llama, "_ctx", None)
        get_logits = getattr(context, "get_logits", None)
        if not callable(get_logits):
            raise RuntimeError("llama-cpp-python does not expose current decode logits")
        raw = get_logits()
        if isinstance(raw, np.ndarray):
            logits = np.asarray(raw, dtype=np.float32).reshape(-1).copy()
        else:
            logits = np.ctypeslib.as_array(raw, shape=(len(self.tokenizer),)).astype(
                np.float32, copy=True
            )
        if logits.size != len(self.tokenizer):
            raise RuntimeError(
                f"llama.cpp logits size {logits.size} does not match vocabulary "
                f"size {len(self.tokenizer)}"
            )
        if require_finite and not np.isfinite(logits).all():
            raise RuntimeError("llama.cpp returned non-finite logits")
        logits.flags.writeable = False
        return logits

    def _capture_continuation_root(self, token_ids: Sequence[int]) -> LlamaContinuationRoot | None:
        """Capture sequence 0 without copying llama-cpp-python's score matrix."""

        replay_token_ids = tuple(int(token_id) for token_id in token_ids)
        n_tokens = len(replay_token_ids)
        context = getattr(self.llama, "_ctx", None)
        if context is None:
            return None
        raw_context = getattr(context, "ctx", None)
        if raw_context is not None:
            # Production continuation replays the editor prefix once and then
            # rewinds only short candidate suffixes. It never restores a copied
            # sequence state.
            # Copying that state on every context refresh can take longer than
            # the native page-0 deadline even when no token needs evaluation.
            return LlamaContinuationRoot(b"", n_tokens, replay_token_ids)
        test_capture = getattr(context, "capture_sequence_state", None)
        if callable(test_capture):
            payload = bytes(test_capture(0))
            return LlamaContinuationRoot(payload, n_tokens, replay_token_ids)

        # Lightweight unit-test fakes intentionally omit the C context.
        return None

    def _restore_continuation_root(self, root: LlamaContinuationRoot) -> None:
        context = getattr(self.llama, "_ctx", None)
        if context is None:
            raise RuntimeError("llama.cpp context is unavailable")
        clear = getattr(context, "kv_cache_clear", None)
        if callable(clear):
            clear()
        test_restore = getattr(context, "restore_sequence_state", None)
        if callable(test_restore):
            restored = test_restore(root.state_bytes, 0)
            if restored is False:
                raise RuntimeError("test continuation state restore failed")
        else:
            raw_context = getattr(context, "ctx", None)
            if raw_context is None:
                raise RuntimeError("llama.cpp raw context is unavailable for continuation restore")
            from llama_cpp import llama_cpp

            buffer = (ctypes.c_uint8 * len(root.state_bytes)).from_buffer_copy(root.state_bytes)
            restored = int(
                llama_cpp.llama_state_seq_set_data(
                    raw_context,
                    buffer,
                    len(root.state_bytes),
                    0,
                )
            )
            if restored <= 0:
                raise RuntimeError("failed to restore llama.cpp candidate continuation state")
        # High-level Llama.eval positions the next batch from n_tokens. Sequence
        # state restore owns the KV/positions; no raw context token ids are needed.
        self.llama.n_tokens = root.n_tokens

    def _clear_live_sequence(self) -> None:
        context = getattr(self.llama, "_ctx", None)
        clear = getattr(context, "kv_cache_clear", None)
        if callable(clear):
            clear()
        self.llama.reset()

    def _smoke_forward(self) -> None:
        try:
            self.llama.reset()
            self.llama.eval([self._fallback_token()])
            self._copy_last_logits()
        except Exception as exc:
            raise GpuBindingError(f"CUDA GGUF smoke forward failed: {exc}") from exc
        finally:
            self._clear_live_sequence()
        self._cached_token_ids = None
        self._cached_logits = None

    def create_snapshot(self, before: str, after: str = "") -> GgufLogitsSnapshot:
        token_ids = (
            self._prompt_encoder.encode(
                before, after, max_before_tokens=self.max_before_tokens, n_ctx=self.n_ctx
            )
            if self.prompt_config.mode == "fim"
            else self._tokenize_context(before)
        )
        started = time.perf_counter()
        with self._context_waiters_lock:
            self._context_waiters += 1
        try:
            self._lock.acquire()
        finally:
            with self._context_waiters_lock:
                self._context_waiters -= 1
        try:
            acquired = time.perf_counter()
            queue_wait_ms = max(0.0, (acquired - started) * 1000.0)
            evaluated_tokens = 0
            if token_ids != self._cached_token_ids:
                self._clear_cached_continuation_state()
            if token_ids == self._cached_token_ids and self._cached_logits is not None:
                logits = self._cached_logits
            else:
                can_append = (
                    self._cached_token_ids is not None
                    and len(token_ids) > len(self._cached_token_ids)
                    and token_ids[: len(self._cached_token_ids)] == self._cached_token_ids
                )
                try:
                    if can_append:
                        suffix = token_ids[len(self._cached_token_ids) :]
                        evaluated_tokens = len(suffix)
                        self.llama.eval(list(suffix))
                    else:
                        evaluated_tokens = len(token_ids)
                        self._clear_live_sequence()
                        self.llama.eval(list(token_ids))
                    logits = self._copy_last_logits()
                except Exception:
                    self._clear_live_sequence()
                    self._cached_token_ids = None
                    self._cached_logits = None
                    raise
                self._cached_token_ids = token_ids
                self._cached_logits = logits

            continuation_root = self._capture_continuation_root(token_ids)
            finished = time.perf_counter()
            compute_ms = max(0.0, (finished - acquired) * 1000.0)
            latency_ms = queue_wait_ms + compute_ms
            self._last_refresh_diagnostics = (
                len(token_ids),
                evaluated_tokens,
                latency_ms,
                queue_wait_ms,
                compute_ms,
            )
            self._epoch += 1
            return GgufLogitsSnapshot(
                epoch=self._epoch,
                before_hash=_text_hash(before),
                after_hash=_text_hash(after),
                logits=logits,
                created_monotonic=time.monotonic(),
                latency_ms=latency_ms,
                continuation_root=continuation_root,
            )
        finally:
            self._lock.release()

    def _context_refresh_is_waiting(self) -> bool:
        with self._context_waiters_lock:
            return self._context_waiters > 0

    def _clear_cached_continuation_state(self) -> None:
        self._continuation_state_token_ids = None
        self._continuation_state_buffer = None
        self._continuation_state_size = 0

    def full_logits(self, before: str, after: str = "") -> RuntimeSnapshot:
        snapshot = self.create_snapshot(before, after)
        return RuntimeSnapshot(
            payload=snapshot.logits,
            before_hash=snapshot.before_hash,
            after_hash=snapshot.after_hash,
            latency_ms=snapshot.latency_ms,
            continuation_root=snapshot.continuation_root,
        )

    def continue_from_root(
        self,
        root: LlamaContinuationRoot,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> list[np.ndarray] | None:
        return self._continue_values_from_root(
            root,
            token_paths,
            allowed_token_sets,
            deadline_ms=deadline_ms,
            normalize_log_probs=False,
        )

    def continue_log_probs_from_root(
        self,
        root: LlamaContinuationRoot,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> list[np.ndarray] | None:
        """Return legal-token log probabilities normalized over the full vocabulary."""

        return self._continue_values_from_root(
            root,
            token_paths,
            allowed_token_sets,
            deadline_ms=deadline_ms,
            normalize_log_probs=True,
        )

    @staticmethod
    def _select_continuation_values(
        logits: Sequence[float],
        allowed: np.ndarray,
        *,
        normalize_log_probs: bool,
    ) -> np.ndarray:
        values = np.asarray(logits, dtype=np.float64).reshape(-1)
        if not normalize_log_probs:
            return np.asarray(values[allowed], dtype=np.float32).copy()
        if values.size == 0:
            return np.full(allowed.size, -math.inf, dtype=np.float32)
        if np.isnan(values).any():
            raise ValueError("model logits must not contain NaN")
        positive_infinity = np.isposinf(values)
        if positive_infinity.any():
            output = np.full(allowed.size, -math.inf, dtype=np.float32)
            output[positive_infinity[allowed]] = np.float32(-math.log(int(positive_infinity.sum())))
            return output
        finite = np.isfinite(values)
        if not finite.any():
            return np.full(allowed.size, -math.inf, dtype=np.float32)
        maximum = float(values[finite].max())
        log_normalizer = maximum + math.log(float(np.exp(values[finite] - maximum).sum()))
        return np.asarray(values[allowed] - log_normalizer, dtype=np.float32)

    def _restore_live_continuation_root(self, root: LlamaContinuationRoot) -> bool:
        context = getattr(self.llama, "_ctx", None)
        raw_context = getattr(context, "ctx", None)
        if raw_context is None:
            self._restore_continuation_root(root)
            return True
        root_tokens = tuple(int(token_id) for token_id in root.replay_token_ids)
        root_state = self._continuation_state_buffer
        state_size = self._continuation_state_size
        if self._continuation_state_token_ids != root_tokens or root_state is None:
            return False
        from llama_cpp import llama_cpp

        self._clear_live_sequence()
        restored = int(
            llama_cpp.llama_state_seq_set_data(
                raw_context,
                root_state,
                state_size,
                0,
            )
        )
        if restored != state_size:
            return False
        self.llama.n_tokens = len(root_tokens)
        return True

    def _continue_values_from_root(
        self,
        root: LlamaContinuationRoot,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
        normalize_log_probs: bool,
    ) -> list[np.ndarray] | None:
        """Score branches from one exact root snapshot without context mixing.

        Lock acquisition is deadline bounded. Each branch restores the same
        sequence-0 KV root and evaluates only its candidate token path, so the
        root token score and every continuation score share one model context.
        """

        requested_branches = len(token_paths)
        operation_started = time.perf_counter()
        if deadline_ms <= 0:
            self._last_continuation_diagnostics = (
                0.0,
                0.0,
                requested_branches,
                0,
                0,
                False,
                "deadline",
            )
            return None
        if not isinstance(root, LlamaContinuationRoot):
            raise TypeError("candidate continuation root has an unexpected runtime type")
        if len(token_paths) != len(allowed_token_sets):
            raise ValueError("token_paths and allowed_token_sets must have equal length")
        deadline = time.monotonic() + deadline_ms / 1000.0
        remaining = max(0.0, deadline - time.monotonic())
        if not self._lock.acquire(timeout=remaining):
            elapsed_ms = max(0.0, (time.perf_counter() - operation_started) * 1000.0)
            self._last_continuation_diagnostics = (
                elapsed_ms,
                elapsed_ms,
                requested_branches,
                0,
                0,
                False,
                "lock_timeout",
            )
            return None
        queue_wait_ms = max(0.0, (time.perf_counter() - operation_started) * 1000.0)

        cache_matches_root = (
            tuple(root.replay_token_ids) == self._cached_token_ids
            and self._cached_logits is not None
        )
        cached_logits = self._cached_logits if cache_matches_root else None
        sequence_mutated = False
        completed_branches = [0]
        returned_tokens = 0
        outcome = "error"
        cache_preserved = False
        try:
            outputs: list[np.ndarray] = []
            paths: list[tuple[int, ...]] = []
            allowed_sets: list[np.ndarray] = []
            converted_sets: dict[int, np.ndarray] = {}
            vocabulary_size = len(self.tokenizer)
            for raw_path, raw_allowed in zip(token_paths, allowed_token_sets, strict=True):
                path = tuple(int(token_id) for token_id in raw_path)
                if not path:
                    raise ValueError("continuation token path must be non-empty")
                if root.n_tokens + len(path) > self.n_ctx:
                    raise ValueError("continuation token path exceeds n_ctx")
                if min(path) < 0 or max(path) >= vocabulary_size:
                    raise IndexError("continuation token path is outside the model vocabulary")
                allowed = converted_sets.get(id(raw_allowed))
                if allowed is None:
                    # Background beams share the same full-vocabulary tuple.
                    # Materialize it once, avoiding repeated GIL-bound copies.
                    allowed = np.asarray(tuple(raw_allowed), dtype=np.int64)
                    converted_sets[id(raw_allowed)] = allowed
                if allowed.ndim != 1:
                    raise ValueError("allowed continuation token ids must be one-dimensional")
                if allowed.size and (
                    int(allowed.min()) < 0 or int(allowed.max()) >= vocabulary_size
                ):
                    raise IndexError(
                        "allowed continuation token id is outside the model vocabulary"
                    )
                paths.append(path)
                allowed_sets.append(allowed)

            context = getattr(self.llama, "_ctx", None)
            raw_context = getattr(context, "ctx", None)
            if raw_context is not None and root.replay_token_ids:
                sequence_mutated = True
                result = self._continue_single_sequence_restore(
                    root.replay_token_ids,
                    paths,
                    allowed_sets,
                    deadline=deadline,
                    normalize_log_probs=normalize_log_probs,
                    completed_counter=completed_branches,
                )
                if result is None:
                    outcome = "preempted" if self._context_refresh_is_waiting() else "deadline"
                    return None
                returned_tokens = sum(int(values.size) for values in result)
                outcome = "completed"
                return result

            for path, allowed in zip(paths, allowed_sets, strict=True):
                if time.monotonic() >= deadline:
                    outcome = "deadline"
                    return None
                sequence_mutated = True
                self._restore_continuation_root(root)
                self.llama.eval(list(path))
                logits = self._copy_last_logits(require_finite=not normalize_log_probs)
                outputs.append(
                    self._select_continuation_values(
                        logits,
                        allowed,
                        normalize_log_probs=normalize_log_probs,
                    )
                )
                completed_branches[0] = len(outputs)
                if time.monotonic() >= deadline:
                    outcome = "deadline"
                    return None
                if len(outputs) < len(paths) and self._context_refresh_is_waiting():
                    outcome = "preempted"
                    return None
            returned_tokens = sum(int(values.size) for values in outputs)
            outcome = "completed"
            return outputs
        finally:
            try:
                restored = False
                if cache_matches_root:
                    try:
                        restored = not sequence_mutated or self._restore_live_continuation_root(
                            root
                        )
                    except Exception:
                        restored = False
                if restored:
                    self._cached_token_ids = tuple(root.replay_token_ids)
                    self._cached_logits = cached_logits
                    cache_preserved = True
                else:
                    self._clear_live_sequence()
                    self._cached_token_ids = None
                    self._cached_logits = None
            finally:
                self._lock.release()
                if returned_tokens == 0 and completed_branches[0] > 0:
                    returned_tokens = sum(
                        int(values.size) for values in allowed_sets[: completed_branches[0]]
                    )
                elapsed_ms = max(
                    queue_wait_ms,
                    (time.perf_counter() - operation_started) * 1000.0,
                )
                self._last_continuation_diagnostics = (
                    queue_wait_ms,
                    elapsed_ms,
                    requested_branches,
                    completed_branches[0],
                    returned_tokens,
                    cache_preserved,
                    outcome,
                )

    def warm_candidate_continuation(
        self,
        root: LlamaContinuationRoot,
        *,
        deadline_ms: float,
    ) -> bool:
        """Exercise representative single-sequence paths before the pipe is exposed."""

        fallback = self._fallback_token()
        allowed = (fallback,)
        paths = tuple(
            allowed * path_length
            for path_length in range(1, CANDIDATE_WARMUP_MAX_PATH_TOKENS + 1)
            for _ in range(DEFAULT_PARALLEL_SEQUENCES)
        )
        allowed_sets = (allowed,) * len(paths)
        results = self.continue_from_root(
            root,
            paths,
            allowed_sets,
            deadline_ms=deadline_ms,
        )
        return results is not None and len(results) == len(paths)

    def _continue_single_sequence_restore(
        self,
        replay_token_ids: Sequence[int],
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[np.ndarray],
        *,
        deadline: float,
        normalize_log_probs: bool = False,
        completed_counter: list[int] | None = None,
    ) -> list[np.ndarray] | None:
        """Replay one root once, then restore its single-sequence state per branch."""

        from llama_cpp import llama_cpp

        context = getattr(self.llama, "_ctx", None)
        raw_context = getattr(context, "ctx", None)
        if raw_context is None:
            raise RuntimeError("llama.cpp raw context is unavailable for sequence restore")
        root_tokens = tuple(int(token_id) for token_id in replay_token_ids)
        root_state = self._continuation_state_buffer
        state_size = self._continuation_state_size
        if self._continuation_state_token_ids != root_tokens or root_state is None:
            self._clear_live_sequence()
            self.llama.eval(list(root_tokens))
            state_size = int(llama_cpp.llama_state_seq_get_size(raw_context, 0))
            if state_size <= 0:
                raise RuntimeError("llama.cpp sequence state is unavailable for branch restore")
            root_state = (ctypes.c_uint8 * state_size)()
            copied = int(
                llama_cpp.llama_state_seq_get_data(
                    raw_context,
                    root_state,
                    state_size,
                    0,
                )
            )
            if copied != state_size:
                raise RuntimeError("llama.cpp did not capture the complete root sequence state")
            self._continuation_state_token_ids = root_tokens
            self._continuation_state_buffer = root_state
            self._continuation_state_size = state_size
        else:
            self._clear_live_sequence()
            restored = int(
                llama_cpp.llama_state_seq_set_data(
                    raw_context,
                    root_state,
                    state_size,
                    0,
                )
            )
            if restored != state_size:
                raise RuntimeError("llama.cpp could not restore the cached root sequence state")
            self.llama.n_tokens = len(root_tokens)

        outputs: list[np.ndarray] = []
        for path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            if time.monotonic() >= deadline:
                return None
            self.llama.eval(list(path))
            logits = self._copy_last_logits(require_finite=not normalize_log_probs)
            selected = self._select_continuation_values(
                logits,
                allowed,
                normalize_log_probs=normalize_log_probs,
            )
            if normalize_log_probs:
                if np.isnan(selected).any():
                    raise RuntimeError("llama.cpp returned NaN continuation log probabilities")
            elif not np.isfinite(selected).all():
                raise RuntimeError("llama.cpp returned non-finite continuation logits")
            outputs.append(selected)
            if completed_counter is not None:
                completed_counter[0] = len(outputs)
            if len(outputs) == len(token_paths):
                break
            if time.monotonic() >= deadline or self._context_refresh_is_waiting():
                return None
            restored = int(
                llama_cpp.llama_state_seq_set_data(
                    raw_context,
                    root_state,
                    state_size,
                    0,
                )
            )
            if restored != state_size:
                raise RuntimeError("llama.cpp could not restore the root sequence state")
            self.llama.n_tokens = len(root_tokens)
        return outputs

    def continue_from_empty(
        self,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> list[np.ndarray] | None:
        """Legacy BOS replay retained for compatibility tests/tools only."""

        if deadline_ms <= 0:
            return None
        if len(token_paths) != len(allowed_token_sets):
            raise ValueError("token_paths and allowed_token_sets must have equal length")
        deadline = time.monotonic() + deadline_ms / 1000.0
        remaining = max(0.0, deadline - time.monotonic())
        if not self._lock.acquire(timeout=remaining):
            return None

        try:
            outputs: list[np.ndarray] = []
            vocabulary_size = len(self.tokenizer)
            fallback = self._fallback_token()
            for raw_path, raw_allowed in zip(token_paths, allowed_token_sets, strict=True):
                if time.monotonic() >= deadline:
                    return None
                path = tuple(int(token_id) for token_id in raw_path)
                if not path:
                    raise ValueError("continuation token path must be non-empty")
                if len(path) + 1 > self.n_ctx:
                    raise ValueError("continuation token path exceeds n_ctx")
                if min(path) < 0 or max(path) >= vocabulary_size:
                    raise IndexError("continuation token path is outside the model vocabulary")
                allowed = np.asarray(tuple(raw_allowed), dtype=np.int64)
                if allowed.ndim != 1:
                    raise ValueError("allowed continuation token ids must be one-dimensional")
                if allowed.size and (
                    int(allowed.min()) < 0 or int(allowed.max()) >= vocabulary_size
                ):
                    raise IndexError(
                        "allowed continuation token id is outside the model vocabulary"
                    )

                self._clear_live_sequence()
                self.llama.eval([fallback, *path])
                logits = self._copy_last_logits()
                outputs.append(np.asarray(logits[allowed], dtype=np.float32).copy())
                if time.monotonic() >= deadline:
                    return None
            return outputs
        finally:
            try:
                self._clear_live_sequence()
                self._cached_token_ids = None
                self._cached_logits = None
            finally:
                self._lock.release()

    def invalidate_private_state(self) -> None:
        with self._lock:
            self._clear_live_sequence()
            self._cached_token_ids = None
            self._cached_logits = None
            self._clear_cached_continuation_state()
            self._last_refresh_diagnostics = None
            self._last_continuation_diagnostics = None

    def performance_diagnostics(self) -> dict[str, object]:
        """Return cached timing/count metadata without probing GPU or model state."""

        refresh = self._last_refresh_diagnostics
        continuation = self._last_continuation_diagnostics
        return {
            "context_mode": self.prompt_config.mode,
            "max_after_tokens": self.prompt_config.max_after_tokens,
            "continuation_reserve_tokens": self.prompt_config.continuation_reserve_tokens,
            "max_before_tokens": self.max_before_tokens,
            "n_ctx": self.n_ctx,
            "n_batch": self.n_batch,
            "last_refresh_context_tokens": None if refresh is None else refresh[0],
            "last_refresh_evaluated_tokens": None if refresh is None else refresh[1],
            "last_refresh_latency_ms": None if refresh is None else refresh[2],
            "last_refresh_queue_wait_ms": None if refresh is None else refresh[3],
            "last_refresh_compute_ms": None if refresh is None else refresh[4],
            "last_continuation_queue_wait_ms": (None if continuation is None else continuation[0]),
            "last_continuation_elapsed_ms": (None if continuation is None else continuation[1]),
            "last_continuation_requested_branches": (
                None if continuation is None else continuation[2]
            ),
            "last_continuation_completed_branches": (
                None if continuation is None else continuation[3]
            ),
            "last_continuation_returned_tokens": (
                None if continuation is None else continuation[4]
            ),
            "last_continuation_cache_preserved": (
                None if continuation is None else continuation[5]
            ),
            "last_continuation_outcome": (None if continuation is None else continuation[6]),
        }

    def diagnostics(self) -> dict[str, object]:
        current_gpu = self._gpu_probe()
        if current_gpu.uuid != self.target_gpu.uuid or current_gpu.name != self.target_gpu.name:
            raise GpuBindingError("target GPU identity changed after GGUF startup")
        diagnostics = {
            "model": self.model_id,
            "format": self.format,
            "quantization": self.quantization,
            "runtime": self.runtime_name,
            "llama_cpp_python_version": _llama_cpp_version(),
            "backend": "CUDA",
            "gpu_layers": "all",
            "gpu_name": self.target_gpu.name,
            "gpu_uuid": self.target_gpu.uuid,
            "gpu_vram_load_delta_mib": self.gpu_vram_load_delta_mib,
            "gpu_free_mib": current_gpu.memory_free_mib,
            "gguf_sha256": self.gguf_sha256,
            "gguf_revision": self.model_revision,
            "vocab_fingerprint": self.vocab_fingerprint,
        }
        diagnostics.update(self.performance_diagnostics())
        return diagnostics
