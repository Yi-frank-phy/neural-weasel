from __future__ import annotations

from importlib.metadata import version
from types import SimpleNamespace

import pytest

from neural_weasel.index import SCHEMA_VERSION
from neural_weasel.runtime_identity import validated_runtime_index_identity


class Runtime:
    model_id = "Qwen/Qwen3.5-0.8B-Base"
    tokenizer_revision = "revision-a"
    tokenizer_fingerprint = "fingerprint-a"
    tokenizer = object()


class Index:
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "model_id": Runtime.model_id,
        "revision": Runtime.tokenizer_revision,
        "tokenizer_hash": Runtime.tokenizer_fingerprint,
        "pypinyin_version": version("pypinyin"),
    }


def test_validated_identity_exposes_matching_runtime_and_index_fields() -> None:
    identity = validated_runtime_index_identity(Runtime(), Index())

    assert identity["tokenizer_fingerprint"] == "fingerprint-a"
    assert identity["index_tokenizer_fingerprint"] == "fingerprint-a"
    assert identity["index_schema_version"] == SCHEMA_VERSION


def test_validated_identity_rejects_stale_tokenizer_index() -> None:
    stale = SimpleNamespace(metadata=dict(Index.metadata, tokenizer_hash="fingerprint-old"))

    with pytest.raises(RuntimeError, match="tokenizer fingerprint"):
        validated_runtime_index_identity(Runtime(), stale)
