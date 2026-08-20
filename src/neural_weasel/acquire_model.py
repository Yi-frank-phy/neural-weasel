from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .gguf_artifact import PRODUCTION_GGUF, ProductionGgufArtifact, verified_local_sha256
from .paths import models_root


@dataclass(frozen=True, slots=True)
class AcquiredGguf:
    path: Path
    sha256: str
    artifact: ProductionGgufArtifact = PRODUCTION_GGUF


def production_model_dir(artifact: ProductionGgufArtifact = PRODUCTION_GGUF) -> Path:
    safe_repo = artifact.repo_id.replace("/", "--")
    return models_root() / safe_repo / artifact.revision


def ensure_production_gguf(
    artifact: ProductionGgufArtifact = PRODUCTION_GGUF,
) -> AcquiredGguf:
    """Download the immutable production GGUF and bind it to a real local SHA-256."""

    from huggingface_hub import hf_hub_download

    destination = production_model_dir(artifact)
    destination.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        hf_hub_download(
            repo_id=artifact.repo_id,
            filename=artifact.filename,
            revision=artifact.revision,
            local_dir=destination,
        )
    )
    sha256 = verified_local_sha256(downloaded)
    identity_path = destination / "artifact-identity.json"
    identity_path.write_text(
        json.dumps(
            {
                "model_id": artifact.model_id,
                "repo_id": artifact.repo_id,
                "filename": artifact.filename,
                "revision": artifact.revision,
                "format": artifact.format,
                "quantization": artifact.quantization,
                "sha256": sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return AcquiredGguf(path=downloaded, sha256=sha256, artifact=artifact)
