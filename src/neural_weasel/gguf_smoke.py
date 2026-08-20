from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .acquire_model import ensure_production_gguf
from .gguf_artifact import PRODUCTION_GGUF
from .llama_runtime import LlamaCppBackend


def run_gguf_smoke(
    *,
    acquire: Callable[[], Any] = ensure_production_gguf,
    runtime_factory: Callable[[Any], Any] = LlamaCppBackend,
) -> dict[str, object]:
    started = time.perf_counter()
    acquired = acquire()
    runtime = runtime_factory(acquired)
    snapshot = runtime.create_snapshot("Neural Weasel CUDA smoke")
    diagnostics = dict(runtime.diagnostics())

    expected = {
        "model": PRODUCTION_GGUF.model_id,
        "format": "gguf",
        "quantization": "Q8_0",
        "runtime": "llama.cpp",
        "backend": "CUDA",
        "gpu_layers": "all",
    }
    for key, value in expected.items():
        if diagnostics.get(key) != value:
            raise RuntimeError(
                f"GGUF smoke identity mismatch for {key}: "
                f"expected {value!r}, got {diagnostics.get(key)!r}"
            )
    if diagnostics.get("gguf_sha256") != acquired.sha256:
        raise RuntimeError("GGUF smoke SHA-256 does not match acquired artifact")
    if diagnostics.get("vocab_fingerprint") != runtime.vocab_fingerprint:
        raise RuntimeError("GGUF smoke vocabulary fingerprint mismatch")

    return {
        "status": "ok",
        "artifact_path": str(acquired.path),
        "artifact_revision": acquired.artifact.revision,
        "gguf_sha256": acquired.sha256,
        "smoke_forward_latency_ms": round(snapshot.latency_ms, 3),
        "total_startup_smoke_ms": round((time.perf_counter() - started) * 1000, 3),
        "diagnostics": diagnostics,
    }
