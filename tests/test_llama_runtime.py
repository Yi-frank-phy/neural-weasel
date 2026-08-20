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
    def __init__(self, owner: "FakeLlama") -> None:
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
        return NvidiaGpu(0, "NVIDIA GeForce RTX 4060 Laptop GPU", "GPU-test", 8192, self.before_free)

    def after(self) -> NvidiaGpu:
        return NvidiaGpu(0, "NVIDIA GeForce RTX 4060 Laptop GPU", "GPU-test", 8192, self.after_free)


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
