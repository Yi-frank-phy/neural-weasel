from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from neural_weasel.acquire_model import AcquiredGguf
from neural_weasel.gguf_artifact import PRODUCTION_GGUF
from neural_weasel.gpu import GpuBindingError, NvidiaGpu
from neural_weasel.llama_runtime import LlamaCppBackend


class FakeContext:
    def __init__(self, owner: FakeLlama) -> None:
        self.owner = owner

    def get_logits(self):
        return self.owner.last_logits


class FakeLlama:
    created_kwargs: dict[str, object] | None = None

    def __init__(self, model_path: str, **kwargs: object) -> None:
        type(self).created_kwargs = {"model_path": model_path, **kwargs}
        self._pieces = [b"<bos>", "你".encode(), b"hello", b"<eos>"]
        self._ctx = FakeContext(self)
        self.last_logits = np.array([0.1, 2.0, 0.3, -1.0], dtype=np.float32)
        self.eval_calls: list[list[int]] = []
        self.reset_calls = 0

    def n_vocab(self) -> int:
        return len(self._pieces)

    def token_bos(self) -> int:
        return 0

    def token_eos(self) -> int:
        return 3

    def detokenize(self, tokens: list[int], special: bool = False) -> bytes:
        del special
        return b"".join(self._pieces[token] for token in tokens)

    def tokenize(self, text: bytes, add_bos: bool = False, special: bool = False) -> list[int]:
        del add_bos, special
        if text == b"":
            return []
        if text == "你".encode():
            return [1]
        if text == "你好".encode():
            return [1, 2]
        return [2]

    def reset(self) -> None:
        self.reset_calls += 1

    def eval(self, tokens: list[int]) -> None:
        self.eval_calls.append(list(tokens))


@dataclass
class FakeProbe:
    before_free: int = 7600
    after_free: int = 3300

    def before(self) -> NvidiaGpu:
        return NvidiaGpu(
            0,
            "NVIDIA GeForce RTX 4060 Laptop GPU",
            "GPU-test",
            8192,
            self.before_free,
        )

    def after(self) -> NvidiaGpu:
        return NvidiaGpu(
            0,
            "NVIDIA GeForce RTX 4060 Laptop GPU",
            "GPU-test",
            8192,
            self.after_free,
        )


def _acquired(tmp_path: Path) -> AcquiredGguf:
    model = tmp_path / PRODUCTION_GGUF.filename
    model.write_bytes(b"GGUF")
    return AcquiredGguf(model, "a" * 64)


def test_runtime_requests_full_cuda_offload_and_exposes_full_logits(tmp_path: Path) -> None:
    probe = FakeProbe()
    backend = LlamaCppBackend(
        _acquired(tmp_path),
        llama_factory=FakeLlama,
        cuda_backend_probe=lambda: True,
        gpu_before_probe=probe.before,
        gpu_after_probe=probe.after,
    )

    assert FakeLlama.created_kwargs is not None
    assert FakeLlama.created_kwargs["n_gpu_layers"] == -1
    assert FakeLlama.created_kwargs["main_gpu"] == 0
    assert FakeLlama.created_kwargs["logits_all"] is False
    assert FakeLlama.created_kwargs["offload_kqv"] is True

    snapshot = backend.create_snapshot("你")
    assert np.array_equal(snapshot.logits, np.array([0.1, 2.0, 0.3, -1.0], dtype=np.float32))
    assert snapshot.logits.flags.writeable is False
    assert backend.diagnostics()["backend"] == "CUDA"
    assert backend.diagnostics()["gpu_layers"] == "all"


def test_runtime_reuses_prefix_without_replaying_full_context(tmp_path: Path) -> None:
    probe = FakeProbe()
    backend = LlamaCppBackend(
        _acquired(tmp_path),
        llama_factory=FakeLlama,
        cuda_backend_probe=lambda: True,
        gpu_before_probe=probe.before,
        gpu_after_probe=probe.after,
    )

    backend.create_snapshot("你")
    backend.create_snapshot("你好")

    assert backend.llama.eval_calls == [[0], [1], [2]]


def test_runtime_exposes_only_refresh_metadata_and_explicit_limits(tmp_path: Path) -> None:
    probe = FakeProbe()
    backend = LlamaCppBackend(
        _acquired(tmp_path),
        max_before_tokens=2,
        n_ctx=8,
        n_batch=4,
        llama_factory=FakeLlama,
        cuda_backend_probe=lambda: True,
        gpu_before_probe=probe.before,
        gpu_after_probe=probe.after,
    )

    assert FakeLlama.created_kwargs is not None
    assert FakeLlama.created_kwargs["n_ctx"] == 8
    assert FakeLlama.created_kwargs["n_batch"] == 4

    diagnostics = backend.diagnostics()
    assert diagnostics["max_before_tokens"] == 2
    assert diagnostics["n_ctx"] == 8
    assert diagnostics["n_batch"] == 4
    assert diagnostics["last_refresh_context_tokens"] is None
    assert diagnostics["last_refresh_evaluated_tokens"] is None
    assert diagnostics["last_refresh_latency_ms"] is None

    backend.create_snapshot("你")
    diagnostics = backend.diagnostics()
    assert diagnostics["last_refresh_context_tokens"] == 1
    assert diagnostics["last_refresh_evaluated_tokens"] == 1
    assert isinstance(diagnostics["last_refresh_latency_ms"], float)
    assert diagnostics["last_refresh_latency_ms"] >= 0.0

    backend.create_snapshot("你好")
    diagnostics = backend.diagnostics()
    assert diagnostics["last_refresh_context_tokens"] == 2
    assert diagnostics["last_refresh_evaluated_tokens"] == 1

    backend.create_snapshot("你好")
    diagnostics = backend.diagnostics()
    assert diagnostics["last_refresh_context_tokens"] == 2
    assert diagnostics["last_refresh_evaluated_tokens"] == 0

    backend.invalidate_private_state()
    diagnostics = backend.diagnostics()
    assert diagnostics["last_refresh_context_tokens"] is None
    assert diagnostics["last_refresh_evaluated_tokens"] is None
    assert diagnostics["last_refresh_latency_ms"] is None

    expected = {
        "max_before_tokens": 2,
        "n_ctx": 8,
        "n_batch": 4,
        "last_refresh_context_tokens": None,
        "last_refresh_evaluated_tokens": None,
        "last_refresh_latency_ms": None,
    }
    assert backend.performance_diagnostics() == expected

    def fail_gpu_probe() -> NvidiaGpu:
        raise AssertionError("performance diagnostics must not probe the GPU")

    backend._gpu_probe = fail_gpu_probe
    assert backend.performance_diagnostics() == expected


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_before_tokens": 0}, "max_before_tokens must be positive"),
        ({"n_ctx": 0}, "n_ctx must be positive"),
        ({"n_batch": 0}, "n_batch must be positive"),
        (
            {"max_before_tokens": 9, "n_ctx": 8},
            "max_before_tokens must not exceed n_ctx",
        ),
    ],
)
def test_runtime_rejects_invalid_context_limits(
    tmp_path: Path,
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LlamaCppBackend(_acquired(tmp_path), **kwargs)


def test_runtime_rejects_cpu_only_llama_cpp(tmp_path: Path) -> None:
    probe = FakeProbe()
    with pytest.raises(GpuBindingError, match="CUDA backend"):
        LlamaCppBackend(
            _acquired(tmp_path),
            llama_factory=FakeLlama,
            cuda_backend_probe=lambda: False,
            gpu_before_probe=probe.before,
            gpu_after_probe=probe.after,
        )


def test_runtime_rejects_insufficient_vram_delta_for_full_offload(tmp_path: Path) -> None:
    probe = FakeProbe(before_free=7600, after_free=6900)
    with pytest.raises(GpuBindingError, match="full model offload"):
        LlamaCppBackend(
            _acquired(tmp_path),
            llama_factory=FakeLlama,
            cuda_backend_probe=lambda: True,
            gpu_before_probe=probe.before,
            gpu_after_probe=probe.after,
        )
