from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from importlib.metadata import version
from pathlib import Path

import pytest

from neural_weasel.index import SCHEMA_VERSION, PinyinIndex, PinyinIndexBuilder


@pytest.fixture
def make_index(tmp_path: Path):
    def factory(
        rows: Iterable[
            tuple[int | None, str, str, int, int] | tuple[int | None, str, str, str, int, int]
        ],
        *,
        tokenizer_hash: str = "test-tokenizer",
        model_id: str = "test/base-model",
        revision: str = "main",
        pypinyin_version: str | None = None,
    ) -> PinyinIndex:
        path = tmp_path / "pinyin.sqlite3"
        connection = sqlite3.connect(path)
        try:
            PinyinIndexBuilder._create_schema(connection)
            normalized_rows = []
            for row in rows:
                if len(row) == 5:
                    token_id, text, pinyin, syllables, coverage = row
                    syllable_path = pinyin
                else:
                    token_id, text, pinyin, syllable_path, syllables, coverage = row
                normalized_rows.append((token_id, text, pinyin, syllable_path, syllables, coverage))
            connection.executemany(
                """
                INSERT INTO pronunciations
                (token_id, text, pinyin, syllable_path, syllables, coverage)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                normalized_rows,
            )
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "model_id": model_id,
                "revision": revision,
                "tokenizer_hash": tokenizer_hash,
                "pypinyin_version": pypinyin_version or version("pypinyin"),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()),
            )
            connection.commit()
        finally:
            connection.close()
        return PinyinIndex(path)

    return factory

