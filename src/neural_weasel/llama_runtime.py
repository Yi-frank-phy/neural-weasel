from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np

from .acquire_model import AcquiredGguf
from .backends import RuntimeSnapshot
from .gpu import (
    GpuBindingError,
    NvidiaGpu,
    discover_target_gpu,
    require_full_gguf_offload,
    verify_expected_nvidia_binding,
)
from .llama_vocab import LlamaVocabAdapter

DEFAULT_MAX_BEFORE_TOKENS = 3072
DEFAULT_N_CTX = 4096
DEFAULT_N_BATCH = 512


@dataclass(frozen=True, slots=True)
class GgufLogitsSnapshot:
    epoch: int
    before_hash: str
    after_hash: str
    logits: np.ndarray = field(repr=False)
    created_monotonic: float
    latency_ms: float


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_llama_factory(model_path: str, **kwargs: object):
    from llama_cpp import Llama

    return Llama(model_path=model_path, **kwargs)


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
        self._epoch = 0
        self._cached_token_ids: tuple[int, ...] | None = None
        self._cached_logits: np.ndarray | None = None
        # Replaced atomically after a successful refresh. The immutable tuple
        # contains only counts and elapsed time, never editor text or hashes.
        self._last_refresh_diagnostics: tuple[int, int, float] | None = None

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
            n_ctx=self.n_ctx,
            n_batch=self.n_batch,
            logits_all=False,
            offload_kqv=True,
            use_mmap=True,
            verbose=False,
        )

        after_gpu = self._gpu_probe()
        self.gpu_vram_load_delta_mib = require_full_gguf_offload(before_gpu, after_gpu)
        self.target_gpu = after_gpu
        self.tokenizer = LlamaVocabAdapter(self.llama)
        self.vocab_fingerprint = self.tokenizer.fingerprint
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

    def _copy_last_logits(self) -> np.ndarray:
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
        if not np.isfinite(logits).all():
            raise RuntimeError("llama.cpp returned non-finite logits")
        logits.flags.writeable = False
        return logits

    def _smoke_forward(self) -> None:
        try:
            self.llama.reset()
            self.llama.eval([self._fallback_token()])
            self._copy_last_logits()
        except Exception as exc:
            raise GpuBindingError(f"CUDA GGUF smoke forward failed: {exc}") from exc
        finally:
            self.llama.reset()
        self._cached_token_ids = None
        self._cached_logits = None

    def create_snapshot(self, before: str, after: str = "") -> GgufLogitsSnapshot:
        token_ids = self._tokenize_context(before)
        with self._lock:
            started = time.perf_counter()
            evaluated_tokens = 0
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
                        self.llama.reset()
                        self.llama.eval(list(token_ids))
                    logits = self._copy_last_logits()
                except Exception:
                    self.llama.reset()
                    self._cached_token_ids = None
                    self._cached_logits = None
                    raise
                self._cached_token_ids = token_ids
                self._cached_logits = logits

            latency_ms = (time.perf_counter() - started) * 1000
            self._last_refresh_diagnostics = (
                len(token_ids),
                evaluated_tokens,
                latency_ms,
            )
            self._epoch += 1
            return GgufLogitsSnapshot(
                epoch=self._epoch,
                before_hash=_text_hash(before),
                after_hash=_text_hash(after),
                logits=logits,
                created_monotonic=time.monotonic(),
                latency_ms=latency_ms,
            )

    def full_logits(self, before: str, after: str = "") -> RuntimeSnapshot:
        snapshot = self.create_snapshot(before, after)
        return RuntimeSnapshot(
            payload=snapshot.logits,
            before_hash=snapshot.before_hash,
            after_hash=snapshot.after_hash,
            latency_ms=snapshot.latency_ms,
        )

    def continue_from_empty(
        self,
        token_paths: Sequence[Sequence[int]],
        allowed_token_sets: Sequence[Sequence[int]],
        *,
        deadline_ms: float,
    ) -> list[np.ndarray] | None:
        """Score short context-free continuations without waiting for model work.

        The caller owns the outer 120 ms request deadline. This method never
        queues behind a context refresh past the remaining lock-acquisition
        budget. It deliberately replays only the short candidate token path from
        the permanent empty-context root, so editor text is not copied into a
        candidate search session or continuation cache.
        """

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
            for raw_path, raw_allowed in zip(
                token_paths,
                allowed_token_sets,
                strict=True,
            ):
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

                self.llama.reset()
                self.llama.eval([fallback, *path])
                logits = self._copy_last_logits()
                outputs.append(np.asarray(logits[allowed], dtype=np.float32).copy())
                if time.monotonic() >= deadline:
                    return None
            return outputs
        finally:
            try:
                self.llama.reset()
                # Candidate replay deliberately disturbs the live llama context.
                # Forget the incremental editor cache so the next refresh cannot
                # accidentally append to a candidate continuation sequence.
                self._cached_token_ids = None
                self._cached_logits = None
            finally:
                self._lock.release()

    def invalidate_private_state(self) -> None:
        with self._lock:
            self.llama.reset()
            self._cached_token_ids = None
            self._cached_logits = None
            self._last_refresh_diagnostics = None

    def performance_diagnostics(self) -> dict[str, object]:
        """Return cached timing/count metadata without probing GPU or model state."""

        refresh = self._last_refresh_diagnostics
        return {
            "max_before_tokens": self.max_before_tokens,
            "n_ctx": self.n_ctx,
            "n_batch": self.n_batch,
            "last_refresh_context_tokens": None if refresh is None else refresh[0],
            "last_refresh_evaluated_tokens": None if refresh is None else refresh[1],
            "last_refresh_latency_ms": None if refresh is None else refresh[2],
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
