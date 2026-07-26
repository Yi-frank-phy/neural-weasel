from __future__ import annotations

from dataclasses import dataclass

import pytest

import neural_weasel.gpu as gpu_module
from neural_weasel.gpu import (
    EXPECTED_GPU_NAME,
    GpuBindingError,
    NvidiaGpu,
    child_environment,
    discover_target_gpu,
    verify_model_device_map,
    verify_torch_binding,
)
from neural_weasel.model import ModelPolicyError, QwenBaseBackend


def target_gpu() -> NvidiaGpu:
    return NvidiaGpu(
        index=1,
        name=EXPECTED_GPU_NAME,
        uuid="GPU-1234-ABCD",
        memory_total_mib=8188,
        memory_free_mib=7000,
    )


def test_discover_target_gpu_requires_one_exact_name(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        ["0", "AMD Radeon 780M", "GPU-amd", "512", "400"],
        ["1", EXPECTED_GPU_NAME, "GPU-1234-ABCD", "8188", "7000"],
    ]
    monkeypatch.setattr(gpu_module, "_run_nvidia_smi", lambda fields: rows)

    assert discover_target_gpu() == target_gpu()


@pytest.mark.parametrize(
    "rows",
    [
        [["0", "NVIDIA GeForce RTX 4090", "GPU-other", "24000", "22000"]],
        [
            ["0", EXPECTED_GPU_NAME, "GPU-first", "8188", "7000"],
            ["1", EXPECTED_GPU_NAME, "GPU-second", "8188", "7000"],
        ],
    ],
)
def test_discover_target_gpu_rejects_missing_or_ambiguous_match(
    rows: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_module, "_run_nvidia_smi", lambda fields: rows)
    with pytest.raises(GpuBindingError, match="expected exactly one"):
        discover_target_gpu()


def test_child_environment_binds_uuid_before_child_imports_torch() -> None:
    env = child_environment(target_gpu())
    assert env["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert env["CUDA_VISIBLE_DEVICES"] == "GPU-1234-ABCD"
    assert env["NEURAL_WEASEL_EXPECTED_GPU_UUID"] == "GPU-1234-ABCD"
    assert env["NEURAL_WEASEL_EXPECTED_GPU_NAME"] == EXPECTED_GPU_NAME


@dataclass
class FakeProperties:
    uuid: str


class FakeCuda:
    def is_available(self) -> bool:
        return True

    def device_count(self) -> int:
        return 1

    def get_device_name(self, device: int) -> str:
        assert device == 0
        return EXPECTED_GPU_NAME

    def get_device_properties(self, device: int) -> FakeProperties:
        assert device == 0
        return FakeProperties(uuid="1234abcd")


class FakeTorch:
    cuda = FakeCuda()


def test_verify_torch_binding_checks_name_uuid_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEURAL_WEASEL_EXPECTED_GPU_UUID", "GPU-1234-ABCD")
    monkeypatch.setenv("NEURAL_WEASEL_EXPECTED_GPU_NAME", EXPECTED_GPU_NAME)
    monkeypatch.setattr(gpu_module, "discover_target_gpu", target_gpu)

    assert verify_torch_binding(FakeTorch()) == target_gpu()


def test_verify_torch_binding_fails_closed_without_launcher_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEURAL_WEASEL_EXPECTED_GPU_UUID", raising=False)
    monkeypatch.delenv("NEURAL_WEASEL_EXPECTED_GPU_NAME", raising=False)
    with pytest.raises(GpuBindingError, match="launcher"):
        verify_torch_binding(FakeTorch())


class FakeModelWithMap:
    def __init__(self, device_map: dict[str, object]) -> None:
        self.hf_device_map = device_map


def test_model_device_map_accepts_only_explicit_cuda_zero() -> None:
    verify_model_device_map(FakeModelWithMap({"": 0, "layers.0": "cuda:0"}))
    with pytest.raises(GpuBindingError, match="offload"):
        verify_model_device_map(FakeModelWithMap({"": 0, "lm_head": "cpu"}))
    with pytest.raises(GpuBindingError, match="wrong device"):
        verify_model_device_map(FakeModelWithMap({"": "cuda:1"}))


def test_model_policy_rejects_instruct_checkpoint_before_loading_ml_stack() -> None:
    with pytest.raises(ModelPolicyError, match="Base-only"):
        QwenBaseBackend("Qwen/Qwen3.5-0.8B-Instruct")
