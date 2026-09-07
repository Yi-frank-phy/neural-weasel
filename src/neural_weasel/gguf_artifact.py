from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


class GgufArtifactError(RuntimeError):
    """Raised when the production GGUF artifact cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ProductionGgufArtifact:
    model_id: str
    repo_id: str
    filename: str
    revision: str
    format: str
    quantization: str
    # When set, the exact local GGUF bytes must hash to this digest. The
    # digest is the trust anchor for locally verified artifacts; Hub-anchored
    # artifacts keep relying on the immutable Hugging Face commit instead.
    expected_sha256: str | None = None


PRODUCTION_GGUF = ProductionGgufArtifact(
    model_id="Qwen/Qwen3.5-4B-Base",
    repo_id="mradermacher/Qwen3.5-4B-Base-GGUF",
    filename="Qwen3.5-4B-Base.Q8_0.gguf",
    revision="d1238424e1efe0c4389935d7bd03853378d5c9e1",
    format="gguf",
    quantization="Q8_0",
)

# The target machine's local Q4_K_M copy was verified by SHA-256, but no Hub
# commit is pinned for it yet. Empty repo_id/revision force the locally
# verified acquisition route until that commit is recorded here.
PRODUCTION_GGUF_Q4_K_M = ProductionGgufArtifact(
    model_id="Qwen/Qwen3.5-4B-Base",
    repo_id="",
    filename="Qwen3.5-4B-Q4_K_M.gguf",
    revision="",
    format="gguf",
    quantization="Q4_K_M",
    expected_sha256=("00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"),
)

# Closed set of supported quant runtime selectors. Functional contracts stay
# quantization-independent; the hardware VRAM guards in gpu.py apply to every
# entry in this mapping.
QUANTIZATION_ARTIFACTS = {
    "Q4_K_M": PRODUCTION_GGUF_Q4_K_M,
    "Q8_0": PRODUCTION_GGUF,
}


def resolve_quantization_artifact(selector: str) -> ProductionGgufArtifact:
    """Resolve a user-facing quant selector to its pinned production artifact."""

    normalized = selector.strip().upper()
    try:
        return QUANTIZATION_ARTIFACTS[normalized]
    except KeyError:
        supported = ", ".join(sorted(QUANTIZATION_ARTIFACTS))
        raise GgufArtifactError(
            f"unsupported quantization selector {selector!r}; supported: {supported}"
        ) from None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_local_sha256(path: Path, sidecar: Path | None = None) -> str:
    """Return a cached SHA-256 that is tied to the current local file identity.

    The immutable Hugging Face commit is the remote trust anchor.  We still
    compute a real SHA-256 over the downloaded GGUF so the runtime and pinyin
    index can be bound to the exact local bytes without confusing a Xet CAS id
    with a file SHA-256.
    """

    path = Path(path)
    if not path.is_file():
        raise GgufArtifactError(f"GGUF model does not exist: {path}")
    stat = path.stat()
    sidecar = sidecar or path.with_name(path.name + ".sha256.json")

    try:
        cached = json.loads(sidecar.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        cached = None
    if (
        isinstance(cached, dict)
        and cached.get("size") == stat.st_size
        and cached.get("mtime_ns") == stat.st_mtime_ns
        and isinstance(cached.get("sha256"), str)
        and len(cached["sha256"]) == 64
    ):
        return cached["sha256"]

    sha256 = _sha256_file(path)
    payload = json.dumps(
        {"sha256": sha256, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        sort_keys=True,
    )
    temporary = sidecar.with_name(f"{sidecar.name}.tmp-{os.getpid()}")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, sidecar)
    return sha256
