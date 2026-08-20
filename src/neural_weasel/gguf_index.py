from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .index import PinyinIndexBuilder, SCHEMA_VERSION
from .paths import indexes_root

GGUF_IDENTITY_KIND = "gguf-v1"


def _safe_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-._" else "-" for character in value
    )


def default_gguf_index_path(
    model_id: str,
    gguf_sha256: str,
    vocab_fingerprint: str,
    pypinyin_version: str,
) -> Path:
    safe_model = model_id.replace("/", "--")
    safe_pinyin = _safe_component(pypinyin_version)
    return indexes_root() / (
        f"{safe_model}-gguf-{gguf_sha256[:16]}-{vocab_fingerprint[:16]}-"
        f"pypinyin-{safe_pinyin}-v{SCHEMA_VERSION}.sqlite3"
    )


class GgufPinyinIndexBuilder(PinyinIndexBuilder):
    """Build the existing physical index schema from llama.cpp token ids."""

    def __init__(self, vocab: Any, *, model_id: str, gguf_sha256: str) -> None:
        if len(gguf_sha256) != 64:
            raise ValueError("GGUF SHA-256 must contain 64 hexadecimal characters")
        self.tokenizer = vocab
        self.model_id = model_id
        self.revision = "gguf"
        self.fingerprint = str(vocab.fingerprint)
        self.gguf_sha256 = gguf_sha256.lower()

    def build(self, path: Path | None = None) -> Path:
        pypinyin_version = self._pypinyin_version()
        path = path or default_gguf_index_path(
            self.model_id,
            self.gguf_sha256,
            self.fingerprint,
            pypinyin_version,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".building")
        temporary.unlink(missing_ok=True)

        connection = sqlite3.connect(temporary)
        try:
            self._create_schema(connection)
            self._insert_tokens(connection)
            self._insert_character_coverage(connection)
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "identity_kind": GGUF_IDENTITY_KIND,
                "model_id": self.model_id,
                "gguf_sha256": self.gguf_sha256,
                "vocab_fingerprint": self.fingerprint,
                "pypinyin_version": pypinyin_version,
                "built_unix": int(time.time()),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()),
            )
            connection.commit()
        finally:
            connection.close()
        temporary.replace(path)
        return path
