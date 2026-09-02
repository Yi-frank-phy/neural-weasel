from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from neural_weasel.acquire_model import AcquiredGguf, ensure_production_gguf
from neural_weasel.gguf_artifact import (
    PRODUCTION_GGUF,
    GgufArtifactError,
    ProductionGgufArtifact,
    resolve_quantization_artifact,
)
from neural_weasel.gguf_index import GgufPinyinIndexBuilder, default_gguf_index_path
from neural_weasel.index import PinyinIndex
from neural_weasel.runtime_identity import validated_runtime_index_identity

# Trusted local digest of the target machine's Q4_K_M GGUF bytes. Until a Hub
# commit is pinned for Q4, this digest is the artifact's only trust anchor.
Q4_PINNED_SHA256 = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"


class _AcquisitionStopped(Exception):
    """Raised by the stubbed acquisition to prove forwarding without loading."""


class FakeGgufVocab:
    all_special_ids = frozenset()
    fingerprint = "f" * 64

    def __len__(self) -> int:
        return 2

    def decode(self, token_ids: list[int], **_: object) -> str:
        return {0: "你", 1: "hello"}[token_ids[0]]


def _write_payload(directory: Path, payload: bytes = b"GGUF-q4-payload") -> tuple[Path, str]:
    path = directory / "Qwen3.5-4B-Q4_K_M.gguf"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_q4_k_m_is_a_first_class_locally_anchored_artifact() -> None:
    artifact = resolve_quantization_artifact("Q4_K_M")

    assert artifact.model_id == "Qwen/Qwen3.5-4B-Base"
    assert artifact.format == "gguf"
    assert artifact.quantization == "Q4_K_M"
    assert artifact.filename == "Qwen3.5-4B-Q4_K_M.gguf"
    assert artifact.expected_sha256 == Q4_PINNED_SHA256


def test_q8_0_remains_the_default_hub_anchored_production_artifact() -> None:
    assert resolve_quantization_artifact("Q8_0") is PRODUCTION_GGUF
    assert PRODUCTION_GGUF.quantization == "Q8_0"
    assert PRODUCTION_GGUF.revision == "d1238424e1efe0c4389935d7bd03853378d5c9e1"
    assert PRODUCTION_GGUF.expected_sha256 is None


def test_resolver_normalizes_case_and_rejects_unknown_selectors() -> None:
    assert resolve_quantization_artifact("q4_k_m").quantization == "Q4_K_M"
    with pytest.raises(GgufArtifactError, match="unsupported quantization selector"):
        resolve_quantization_artifact("Q6_K")


def test_locally_verified_gguf_acquisition_binds_real_bytes(tmp_path: Path) -> None:
    path, digest = _write_payload(tmp_path)
    artifact = replace(resolve_quantization_artifact("Q4_K_M"), expected_sha256=digest)

    acquired = ensure_production_gguf(artifact=artifact, gguf_path=path)

    assert isinstance(acquired, AcquiredGguf)
    assert acquired.source == "local"
    assert acquired.sha256 == digest
    assert acquired.artifact.quantization == "Q4_K_M"
    identity = json.loads((path.parent / "artifact-identity.json").read_text(encoding="utf-8"))
    assert identity["source"] == "local"
    assert identity["quantization"] == "Q4_K_M"
    assert identity["sha256"] == digest


def test_local_gguf_mismatching_the_pinned_sha_fails_closed(tmp_path: Path) -> None:
    path, _ = _write_payload(tmp_path)
    artifact = replace(resolve_quantization_artifact("Q4_K_M"), expected_sha256="b" * 64)

    with pytest.raises(GgufArtifactError, match="pinned SHA-256"):
        ensure_production_gguf(artifact=artifact, gguf_path=path)


def test_missing_local_gguf_fails_closed(tmp_path: Path) -> None:
    artifact = resolve_quantization_artifact("Q4_K_M")
    with pytest.raises(GgufArtifactError, match="does not exist"):
        ensure_production_gguf(artifact=artifact, gguf_path=tmp_path / "missing.gguf")


def test_unpinned_hub_commit_refuses_network_download_route() -> None:
    artifact = resolve_quantization_artifact("Q4_K_M")
    assert artifact.repo_id == "" and artifact.revision == ""
    with pytest.raises(GgufArtifactError, match="no pinned Hugging Face commit"):
        ensure_production_gguf(artifact=artifact)


def test_hub_download_verifies_a_pinned_expected_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = tmp_path / "cached.gguf"
    _, digest = _write_payload(tmp_path, b"GGUF-hub-payload")
    cached.write_bytes(b"GGUF-hub-payload")
    artifact = replace(PRODUCTION_GGUF, expected_sha256=digest)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda **_: cached)

    acquired = ensure_production_gguf(artifact=artifact)

    assert acquired.source == "huggingface"
    assert acquired.sha256 == digest


def test_hub_download_mismatching_a_pinned_expected_sha256_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = tmp_path / "cached.gguf"
    cached.write_bytes(b"GGUF-hub-payload")
    artifact = replace(PRODUCTION_GGUF, expected_sha256="b" * 64)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda **_: cached)

    with pytest.raises(GgufArtifactError, match="pinned SHA-256"):
        ensure_production_gguf(artifact=artifact)


def test_build_production_runtime_forwards_selector_and_local_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import neural_weasel.production as production_module

    captured: dict[str, object] = {}
    sentinel_artifact = object()

    def fake_ensure(
        artifact: ProductionGgufArtifact, gguf_path: Path | None = None
    ) -> AcquiredGguf:
        captured["artifact"] = artifact
        captured["gguf_path"] = gguf_path
        raise _AcquisitionStopped

    monkeypatch.setattr(production_module, "ensure_production_gguf", fake_ensure)
    gguf_path = Path("models/q4.gguf")

    with pytest.raises(_AcquisitionStopped):
        production_module.build_production_runtime(
            None, artifact=sentinel_artifact, gguf_path=gguf_path
        )  # type: ignore[arg-type]

    assert captured == {"artifact": sentinel_artifact, "gguf_path": gguf_path}


def test_serve_commands_accept_quantization_selector_and_gguf_path() -> None:
    from neural_weasel.internal_cli import _parser

    parser = _parser()
    serve_args = parser.parse_args(["serve", "--quantization", "Q4_K_M"])
    assert serve_args.quantization == "Q4_K_M"
    assert serve_args.gguf_path is None

    http_args = parser.parse_args(["serve-http", "--gguf-path", "models/q4.gguf"])
    assert http_args.gguf_path == Path("models/q4.gguf")
    assert http_args.quantization == "Q8_0"


def test_serve_rejects_unsupported_quantization_selector() -> None:
    from neural_weasel.internal_cli import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(["serve", "--quantization", "Q6_K"])


def test_each_quantization_binds_its_own_default_index_path() -> None:
    q4_path = default_gguf_index_path(
        "Qwen/Qwen3.5-4B-Base",
        Q4_PINNED_SHA256,
        "f" * 64,
        "0.55.0",
    )
    q8_path = default_gguf_index_path("Qwen/Qwen3.5-4B-Base", "a" * 64, "f" * 64, "0.55.0")

    assert q4_path != q8_path


def test_index_identity_fails_closed_across_quantizations(tmp_path: Path) -> None:
    path = tmp_path / "q4.sqlite3"
    GgufPinyinIndexBuilder(
        FakeGgufVocab(),
        model_id="Qwen/Qwen3.5-4B-Base",
        gguf_sha256=Q4_PINNED_SHA256,
    ).build(path)
    index = PinyinIndex(path)
    q8_runtime = SimpleNamespace(
        format="gguf",
        model_id="Qwen/Qwen3.5-4B-Base",
        gguf_sha256="a" * 64,
        vocab_fingerprint="f" * 64,
    )

    with pytest.raises(RuntimeError, match="GGUF SHA-256"):
        validated_runtime_index_identity(q8_runtime, index)
