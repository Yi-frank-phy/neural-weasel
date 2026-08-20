from __future__ import annotations

from pathlib import Path

from neural_weasel.gguf_index import GgufPinyinIndexBuilder
from neural_weasel.index import PinyinIndex, SCHEMA_VERSION


class FakeGgufVocab:
    all_special_ids = frozenset()
    fingerprint = "f" * 64

    def __len__(self) -> int:
        return 2

    def decode(
        self,
        token_ids: list[int],
        *,
        skip_special_tokens: bool = False,
        clean_up_tokenization_spaces: bool = False,
    ) -> str:
        del skip_special_tokens, clean_up_tokenization_spaces
        return {0: "你", 1: "hello"}[token_ids[0]]


def test_gguf_index_records_artifact_and_vocab_identity(tmp_path: Path) -> None:
    path = tmp_path / "gguf.sqlite3"
    builder = GgufPinyinIndexBuilder(
        FakeGgufVocab(),
        model_id="Qwen/Qwen3.5-4B-Base",
        gguf_sha256="a" * 64,
    )

    builder.build(path)
    index = PinyinIndex(path)

    assert index.metadata["schema_version"] == SCHEMA_VERSION
    assert index.metadata["identity_kind"] == "gguf-v1"
    assert index.metadata["model_id"] == "Qwen/Qwen3.5-4B-Base"
    assert index.metadata["gguf_sha256"] == "a" * 64
    assert index.metadata["vocab_fingerprint"] == "f" * 64
    assert index.compatible(__import__("neural_weasel.pinyin", fromlist=["parse_raw_pinyin"]).parse_raw_pinyin("ni"))
