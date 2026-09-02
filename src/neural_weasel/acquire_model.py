from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .gguf_artifact import (
    PRODUCTION_GGUF,
    GgufArtifactError,
    ProductionGgufArtifact,
    verified_local_sha256,
)
from .paths import models_root


@dataclass(frozen=True, slots=True)
class AcquiredGguf:
    path: Path
    sha256: str
    artifact: ProductionGgufArtifact = PRODUCTION_GGUF
    source: str = "huggingface"


def production_model_dir(artifact: ProductionGgufArtifact = PRODUCTION_GGUF) -> Path:
    safe_repo = artifact.repo_id.replace("/", "--")
    return models_root() / safe_repo / artifact.revision


def _pinned_digest_matches(artifact: ProductionGgufArtifact, sha256: str) -> None:
    expected = artifact.expected_sha256
    if expected is not None and sha256.lower() != expected.lower():
        raise GgufArtifactError(
            f"local GGUF does not match the pinned SHA-256 for "
            f"{artifact.quantization}: expected {expected}, got {sha256}"
        )


def _verify_local_gguf(artifact: ProductionGgufArtifact, gguf_path: Path) -> AcquiredGguf:
    path = Path(gguf_path)
    sha256 = verified_local_sha256(path)
    _pinned_digest_matches(artifact, sha256)
    return AcquiredGguf(path=path, sha256=sha256, artifact=artifact, source="local")


def _download_gguf(artifact: ProductionGgufArtifact) -> AcquiredGguf:
    if not artifact.repo_id or not artifact.revision:
        raise GgufArtifactError(
            f"{artifact.quantization} has no pinned Hugging Face commit; "
            "supply the locally verified GGUF with --gguf-path instead"
        )

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
    _pinned_digest_matches(artifact, sha256)
    return AcquiredGguf(
        path=downloaded,
        sha256=sha256,
        artifact=artifact,
        source="huggingface",
    )


def _write_artifact_identity(acquired: AcquiredGguf) -> None:
    artifact = acquired.artifact
    identity_path = acquired.path.parent / "artifact-identity.json"
    identity_path.write_text(
        json.dumps(
            {
                "model_id": artifact.model_id,
                "repo_id": artifact.repo_id,
                "filename": artifact.filename,
                "revision": artifact.revision,
                "format": artifact.format,
                "quantization": artifact.quantization,
                "source": acquired.source,
                "sha256": acquired.sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def ensure_production_gguf(
    artifact: ProductionGgufArtifact = PRODUCTION_GGUF,
    gguf_path: Path | str | None = None,
) -> AcquiredGguf:
    """Bind a trusted local GGUF to its real SHA-256.

    Two supported acquisition routes:

    - Hub anchored (default ``Q8_0``): download through the immutable Hugging
      Face commit, reusing the local HF cache.
    - Locally anchored (``Q4_K_M``): verify an existing file against the
      pinned ``expected_sha256`` and refuse any network substitution.
    """

    if gguf_path is not None:
        acquired = _verify_local_gguf(artifact, Path(gguf_path))
    else:
        acquired = _download_gguf(artifact)
    _write_artifact_identity(acquired)
    return acquired
