from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from neural_weasel.gguf_artifact import (
    PRODUCTION_GGUF,
    GgufArtifactError,
    verified_local_sha256,
)


def test_production_gguf_is_exact_4b_base_q8_revision() -> None:
    assert PRODUCTION_GGUF.model_id == "Qwen/Qwen3.5-4B-Base"
    assert PRODUCTION_GGUF.repo_id == "mradermacher/Qwen3.5-4B-Base-GGUF"
    assert PRODUCTION_GGUF.filename == "Qwen3.5-4B-Base.Q8_0.gguf"
    assert PRODUCTION_GGUF.revision == "d1238424e1efe0c4389935d7bd03853378d5c9e1"
    assert PRODUCTION_GGUF.format == "gguf"
    assert PRODUCTION_GGUF.quantization == "Q8_0"


def test_verified_sha256_records_and_reuses_matching_file_identity(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF-test-payload")
    sidecar = tmp_path / "model.gguf.sha256.json"

    expected = hashlib.sha256(model.read_bytes()).hexdigest()
    first = verified_local_sha256(model, sidecar)
    assert first == expected

    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata["sha256"] == expected
    assert metadata["size"] == model.stat().st_size
    assert metadata["mtime_ns"] == model.stat().st_mtime_ns

    # A valid sidecar is a cache of a SHA already computed over the pinned Hub
    # artifact. The immutable Hub revision is the download trust anchor.
    assert verified_local_sha256(model, sidecar) == expected


def test_verified_sha256_rehashes_changed_file(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    sidecar = tmp_path / "model.gguf.sha256.json"
    model.write_bytes(b"first")
    first = verified_local_sha256(model, sidecar)

    model.write_bytes(b"second-longer")
    second = verified_local_sha256(model, sidecar)

    assert first != second
    assert second == hashlib.sha256(b"second-longer").hexdigest()


def test_verified_sha256_rejects_missing_model(tmp_path: Path) -> None:
    with pytest.raises(GgufArtifactError, match="does not exist"):
        verified_local_sha256(tmp_path / "missing.gguf")
