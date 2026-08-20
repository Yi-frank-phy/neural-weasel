from __future__ import annotations

from importlib.metadata import version
from typing import Any

from .gguf_index import GGUF_IDENTITY_KIND
from .index import SCHEMA_VERSION, resolved_tokenizer_revision, tokenizer_fingerprint


def _validate(
    metadata: dict[str, object],
    expected: dict[str, object],
    labels: dict[str, str],
) -> None:
    for key, expected_value in expected.items():
        actual_value = metadata.get(key)
        if actual_value != expected_value:
            raise RuntimeError(
                f"pinyin index {labels[key]} does not match runtime: "
                f"expected {expected_value!r}, got {actual_value!r}; rebuild the index"
            )


def _validated_gguf_identity(runtime: Any, metadata: dict[str, object]) -> dict[str, object]:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "identity_kind": GGUF_IDENTITY_KIND,
        "model_id": str(runtime.model_id),
        "gguf_sha256": str(runtime.gguf_sha256),
        "vocab_fingerprint": str(runtime.vocab_fingerprint),
        "pypinyin_version": version("pypinyin"),
    }
    labels = {
        "schema_version": "schema version",
        "identity_kind": "identity kind",
        "model_id": "model id",
        "gguf_sha256": "GGUF SHA-256",
        "vocab_fingerprint": "GGUF vocabulary fingerprint",
        "pypinyin_version": "pypinyin version",
    }
    _validate(metadata, expected, labels)
    return {
        "index_model_id": metadata["model_id"],
        "index_identity_kind": metadata["identity_kind"],
        "index_gguf_sha256": metadata["gguf_sha256"],
        "index_vocab_fingerprint": metadata["vocab_fingerprint"],
        "index_pypinyin_version": metadata["pypinyin_version"],
        "index_schema_version": metadata["schema_version"],
    }


def _validated_legacy_identity(runtime: Any, metadata: dict[str, object]) -> dict[str, object]:
    model_id = str(runtime.model_id)
    revision = str(
        getattr(runtime, "tokenizer_revision", None)
        or resolved_tokenizer_revision(runtime.tokenizer)
    )
    fingerprint = str(
        getattr(runtime, "tokenizer_fingerprint", None) or tokenizer_fingerprint(runtime.tokenizer)
    )
    expected = {
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "revision": revision,
        "tokenizer_hash": fingerprint,
        "pypinyin_version": version("pypinyin"),
    }
    labels = {
        "schema_version": "schema version",
        "model_id": "model id",
        "revision": "tokenizer revision",
        "tokenizer_hash": "tokenizer fingerprint",
        "pypinyin_version": "pypinyin version",
    }
    _validate(metadata, expected, labels)
    return {
        "tokenizer_revision": revision,
        "tokenizer_fingerprint": fingerprint,
        "index_model_id": metadata["model_id"],
        "index_revision": metadata["revision"],
        "index_tokenizer_fingerprint": metadata["tokenizer_hash"],
        "index_pypinyin_version": metadata["pypinyin_version"],
        "index_schema_version": metadata["schema_version"],
    }


def validated_runtime_index_identity(runtime: Any, index: Any) -> dict[str, object]:
    """Fail closed when candidate token ids do not belong to the active runtime."""

    metadata = dict(index.metadata)
    if getattr(runtime, "format", None) == "gguf":
        return _validated_gguf_identity(runtime, metadata)
    return _validated_legacy_identity(runtime, metadata)
