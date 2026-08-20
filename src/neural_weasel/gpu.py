from __future__ import annotations

import csv
import io
import os
import subprocess
from dataclasses import dataclass
from typing import Any

EXPECTED_GPU_NAME = "NVIDIA GeForce RTX 4060 Laptop GPU"
MIN_RUNTIME_FREE_MIB = 2048
MIN_FULL_GGUF_OFFLOAD_DELTA_MIB = 3000


class GpuBindingError(RuntimeError):
    """Raised when the process cannot prove it is isolated to the target GPU."""


@dataclass(frozen=True, slots=True)
class NvidiaGpu:
    index: int
    name: str
    uuid: str
    memory_total_mib: int
    memory_free_mib: int


def _run_nvidia_smi(fields: str) -> list[list[str]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            f"--query-gpu={fields}",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [
        [column.strip() for column in row] for row in csv.reader(io.StringIO(result.stdout)) if row
    ]


def discover_target_gpu() -> NvidiaGpu:
    rows = _run_nvidia_smi("index,name,uuid,memory.total,memory.free")
    matches = [row for row in rows if row[1] == EXPECTED_GPU_NAME]
    if len(matches) != 1:
        names = ", ".join(row[1] for row in rows) or "<none>"
        raise GpuBindingError(
            f"expected exactly one {EXPECTED_GPU_NAME!r}; found {len(matches)} among: {names}"
        )
    row = matches[0]
    return NvidiaGpu(
        index=int(row[0]),
        name=row[1],
        uuid=row[2],
        memory_total_mib=int(row[3]),
        memory_free_mib=int(row[4]),
    )


def child_environment(gpu: NvidiaGpu) -> dict[str, str]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = gpu.uuid
    env["NEURAL_WEASEL_EXPECTED_GPU_UUID"] = gpu.uuid
    env["NEURAL_WEASEL_EXPECTED_GPU_NAME"] = gpu.name
    env["PYTHONUTF8"] = "1"
    return env


def _normalize_uuid(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="strict")
    text = str(value).strip().lower()
    if text.startswith("gpu-"):
        text = text[4:]
    return text.replace("-", "")


def verify_expected_nvidia_binding() -> NvidiaGpu:
    """Prove the launcher isolated the expected physical RTX 4060."""

    expected_uuid = os.environ.get("NEURAL_WEASEL_EXPECTED_GPU_UUID")
    expected_name = os.environ.get("NEURAL_WEASEL_EXPECTED_GPU_NAME")
    if not expected_uuid or not expected_name:
        raise GpuBindingError("GPU guard environment is missing; use the neural-weasel launcher")
    if expected_name != EXPECTED_GPU_NAME:
        raise GpuBindingError(
            f"launcher GPU name mismatch: expected {EXPECTED_GPU_NAME!r}, got {expected_name!r}"
        )
    target = discover_target_gpu()
    if target.name != expected_name or _normalize_uuid(target.uuid) != _normalize_uuid(
        expected_uuid
    ):
        raise GpuBindingError("target GPU changed after process launch")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if not visible or _normalize_uuid(visible) != _normalize_uuid(expected_uuid):
        raise GpuBindingError("CUDA_VISIBLE_DEVICES does not isolate the expected RTX 4060")
    return target


def require_full_gguf_offload(before: NvidiaGpu, after: NvidiaGpu) -> int:
    if before.uuid != after.uuid or before.name != after.name:
        raise GpuBindingError("target GPU changed while loading GGUF runtime")
    delta = before.memory_free_mib - after.memory_free_mib
    if delta < MIN_FULL_GGUF_OFFLOAD_DELTA_MIB:
        raise GpuBindingError(
            "GGUF load did not consume enough target-GPU VRAM to prove full model offload: "
            f"observed {delta} MiB, require at least {MIN_FULL_GGUF_OFFLOAD_DELTA_MIB} MiB"
        )
    if after.memory_free_mib < MIN_RUNTIME_FREE_MIB:
        raise GpuBindingError(
            f"VRAM headroom {after.memory_free_mib} MiB is below required "
            f"{MIN_RUNTIME_FREE_MIB} MiB after GGUF load"
        )
    return delta


def verify_torch_binding(torch_module: Any) -> NvidiaGpu:
    expected_uuid = os.environ.get("NEURAL_WEASEL_EXPECTED_GPU_UUID")
    expected_name = os.environ.get("NEURAL_WEASEL_EXPECTED_GPU_NAME")
    if not expected_uuid or not expected_name:
        raise GpuBindingError("GPU guard environment is missing; use the neural-weasel launcher")
    if not torch_module.cuda.is_available():
        raise GpuBindingError("CUDA is unavailable; CPU fallback is forbidden")
    if torch_module.cuda.device_count() != 1:
        visible_count = torch_module.cuda.device_count()
        raise GpuBindingError(
            f"CUDA isolation failed: expected 1 visible device, got {visible_count}"
        )
    actual_name = torch_module.cuda.get_device_name(0)
    if actual_name != expected_name or actual_name != EXPECTED_GPU_NAME:
        raise GpuBindingError(
            f"wrong CUDA device: expected {EXPECTED_GPU_NAME!r}, got {actual_name!r}"
        )

    properties = torch_module.cuda.get_device_properties(0)
    actual_uuid = getattr(properties, "uuid", None)
    if actual_uuid is not None and _normalize_uuid(actual_uuid) != _normalize_uuid(expected_uuid):
        raise GpuBindingError(f"CUDA UUID mismatch: expected {expected_uuid}, got {actual_uuid}")

    target = discover_target_gpu()
    if _normalize_uuid(target.uuid) != _normalize_uuid(expected_uuid):
        raise GpuBindingError("target GPU changed after process launch")
    return target


def verify_model_device_map(model: Any) -> None:
    device_map = getattr(model, "hf_device_map", None)
    if not device_map:
        devices = {str(parameter.device) for parameter in model.parameters()}
        if devices != {"cuda:0"}:
            raise GpuBindingError(f"model parameters are not exclusively on cuda:0: {devices}")
        return

    allowed = {0, "0", "cuda", "cuda:0"}
    invalid = {
        str(module): device for module, device in device_map.items() if device not in allowed
    }
    if invalid:
        raise GpuBindingError(f"model offload or wrong device detected: {invalid}")


def memory_snapshot(torch_module: Any) -> dict[str, int]:
    free_bytes, total_bytes = torch_module.cuda.mem_get_info(0)
    return {
        "allocated_mib": round(torch_module.cuda.memory_allocated(0) / 2**20),
        "reserved_mib": round(torch_module.cuda.memory_reserved(0) / 2**20),
        "peak_allocated_mib": round(torch_module.cuda.max_memory_allocated(0) / 2**20),
        "peak_reserved_mib": round(torch_module.cuda.max_memory_reserved(0) / 2**20),
        "free_mib": round(free_bytes / 2**20),
        "total_mib": round(total_bytes / 2**20),
    }


def require_runtime_headroom(
    torch_module: Any,
    minimum_free_mib: int = MIN_RUNTIME_FREE_MIB,
) -> None:
    snapshot = memory_snapshot(torch_module)
    if snapshot["free_mib"] < minimum_free_mib:
        raise GpuBindingError(
            f"VRAM headroom {snapshot['free_mib']} MiB is below required {minimum_free_mib} MiB"
        )
