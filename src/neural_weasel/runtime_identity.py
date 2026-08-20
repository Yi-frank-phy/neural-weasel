from __future__ import annotations

from importlib.metadata import version
from typing import Any

from .index import SCHEMA_VERSION, resolved_tokenizer_revision, tokenizer_fingerprint


def validated_runtime_index_identity(runtime: Any, index: Any) -> dict[str, object]:
    """Validate that the loaded pinyin index belongs to the active tokenizer.

    Token IDs are meaningful only relative to one tokenizer revision. Service
    startup therefore fails closed before candidate scoring if an explicit or
    cached index was built for a different runtime identity.
    """

    model_id = str(runtime.model_id)
    revision = str(
        getattr(runtime, "tokenizer_revision", None)
        or resolved_tokenizer_revision(runtime.tokenizer)
    )
    fingerprint = str(
        getattr(runtime, "tokenizer_fingerprint", None) or tokenizer_fingerprint(runtime.tokenizer)
    )
    metadata = dict(index.metadata)
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
    for key, expected_value in expected.items():
        actual_value = metadata.get(key)
        if actual_value != expected_value:
            raise RuntimeError(
                f"pinyin index {labels[key]} does not match runtime: "
                f"expected {expected_value!r}, got {actual_value!r}; rebuild the index"
            )

    return {
        "tokenizer_revision": revision,
        "tokenizer_fingerprint": fingerprint,
        "index_model_id": metadata["model_id"],
        "index_revision": metadata["revision"],
        "index_tokenizer_fingerprint": metadata["tokenizer_hash"],
        "index_pypinyin_version": metadata["pypinyin_version"],
        "index_schema_version": metadata["schema_version"],
    }
