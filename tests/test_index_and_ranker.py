from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import neural_weasel.index as index_module
from neural_weasel.index import PinyinIndexBuilder, default_index_path, tokenizer_fingerprint
from neural_weasel.ranker import rank_candidates


class FakeTokenizer:
    all_special_ids = [0]

    def __init__(self) -> None:
        self._tokens = ["<special>", "你", "你好", "A", "行"]

    def __len__(self) -> int:
        return len(self._tokens)

    def get_vocab(self) -> dict[str, int]:
        return {token: token_id for token_id, token in enumerate(self._tokens)}

    def decode(
        self,
        token_ids: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        del skip_special_tokens, clean_up_tokenization_spaces
        return self._tokens[token_ids[0]]


def test_tokenizer_fingerprint_is_stable_and_sensitive_to_vocabulary() -> None:
    tokenizer = FakeTokenizer()
    first = tokenizer_fingerprint(tokenizer)
    second = tokenizer_fingerprint(tokenizer)
    assert first == second
    tokenizer._tokens[2] = "您好"
    assert tokenizer_fingerprint(tokenizer) != first


def test_default_index_path_changes_with_revision_and_pypinyin_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(index_module, "indexes_root", lambda: tmp_path)
    base = default_index_path("org/model", "fingerprint", "commit-a", "0.55")
    new_revision = default_index_path("org/model", "fingerprint", "commit-b", "0.55")
    new_pinyin = default_index_path("org/model", "fingerprint", "commit-a", "0.56")

    assert base != new_revision
    assert base != new_pinyin
    assert "commit-a" in base.name
    assert "pypinyin-0.55" in base.name


def test_builder_persists_tokens_polyphones_coverage_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokenizer = FakeTokenizer()
    pronunciations = {
        "你": (("ni",),),
        "你好": (("ni", "hao"),),
        "行": (("xing",), ("hang",)),
        "妳": (("ni",),),
    }
    monkeypatch.setattr(
        index_module,
        "pronunciation_paths",
        lambda text: pronunciations.get(text, ()),
    )
    import pypinyin.constants

    monkeypatch.setattr(pypinyin.constants, "PINYIN_DICT", {ord("妳"): "ni3"})

    path = tmp_path / "built.sqlite3"
    index = PinyinIndexBuilder(tokenizer, "test/base-model", revision="abc123").build(path)
    loaded = index_module.PinyinIndex(index)

    assert loaded.metadata["model_id"] == "test/base-model"
    assert loaded.metadata["revision"] == "abc123"
    assert loaded.metadata["tokenizer_hash"] == tokenizer_fingerprint(tokenizer)
    assert loaded.stats() == {"model": 4, "coverage": 1}

    ni_entries = loaded.compatible(index_module.ParsedPinyinInput("ni", "ni", frozenset()))
    assert {(entry.text, entry.coverage) for entry in ni_entries} >= {
        ("你", False),
        ("你好", False),
        ("妳", True),
    }
    parsed_hang = index_module.ParsedPinyinInput("hang", "hang", frozenset())
    assert {entry.pinyin for entry in loaded.compatible(parsed_hang)} == {"hang"}


def test_builder_replaces_existing_index_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokenizer = FakeTokenizer()
    monkeypatch.setattr(index_module, "pronunciation_paths", lambda text: ())
    import pypinyin.constants

    monkeypatch.setattr(pypinyin.constants, "PINYIN_DICT", {})
    path = tmp_path / "replace.sqlite3"
    path.write_bytes(b"old index")

    built = PinyinIndexBuilder(tokenizer, "test/base-model").build(path)

    assert built == path
    assert path.read_bytes() != b"old index"
    assert not path.with_suffix(".sqlite3.building").exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM metadata").fetchone()[0] >= 1


def test_index_returns_completed_prefixes_and_longer_prefix_matches(make_index) -> None:
    index = make_index(
        [
            (1, "你", "ni", 1, 0),
            (2, "你好", "nihao", 2, 0),
            (3, "年", "nian", 1, 0),
        ]
    )

    parsed_nih = index_module.ParsedPinyinInput("nih", "nih", frozenset())
    assert {(entry.text, entry.pinyin) for entry in index.compatible(parsed_nih)} == {
        ("你", "ni"),
        ("你好", "nihao"),
    }
    parsed_nihao = index_module.ParsedPinyinInput("nihao", "nihao", frozenset())
    assert {(entry.text, entry.pinyin) for entry in index.compatible(parsed_nihao)} == {
        ("你", "ni"),
        ("你好", "nihao"),
    }
    parsed_z = index_module.ParsedPinyinInput("z", "z", frozenset())
    assert index.compatible(parsed_z) == []


def test_ranker_prefers_exact_then_more_syllables_then_model_score(make_index) -> None:
    index = make_index(
        [
            (1, "你好", "nihao", 2, 0),
            (2, "拟好", "nihao", 2, 0),
            (3, "你", "ni", 1, 0),
            (None, "妳", "ni", 1, 1),
            (4, "年", "nian", 1, 0),
        ]
    )
    logits = [0.0, 1.0, 8.0, 20.0, 100.0]

    candidates = rank_candidates(
        index=index,
        raw_pinyin="nihao",
        logits=logits,
        context_epoch=7,
        limit=5,
    )

    assert [candidate.text for candidate in candidates] == ["拟好", "你好", "你", "妳"]
    assert candidates[0].completes_input
    assert candidates[0].consumed_keys == 5
    assert candidates[0].score == 8.0
    assert candidates[0].context_epoch == 7
    assert candidates[-1].coverage
    assert candidates[-1].score is None
    assert all(candidate.text != "年" for candidate in candidates)


def test_ranker_handles_incomplete_final_syllable_and_partial_consumption(make_index) -> None:
    index = make_index(
        [
            (1, "纠", "jiu", 1, 0),
            (2, "纠缠", "jiuchan", 2, 0),
            (3, "就餐", "jiucan", 2, 0),
        ]
    )

    candidates = rank_candidates(
        index=index,
        raw_pinyin="jiuc",
        logits=[0.0, 9.0, 5.0, 4.0],
        context_epoch=2,
    )

    assert [candidate.text for candidate in candidates] == ["纠缠", "就餐", "纠"]
    assert [candidate.consumed_keys for candidate in candidates] == [4, 4, 3]
    assert not any(candidate.completes_input for candidate in candidates)


def test_ranker_deduplicates_same_text_and_consumption_by_best_score(make_index) -> None:
    index = make_index(
        [
            (1, "行", "xing", 1, 0),
            (2, "行", "xing", 1, 0),
        ]
    )

    candidates = rank_candidates(
        index=index,
        raw_pinyin="xing",
        logits=[0.0, 1.0, 9.0],
        context_epoch=1,
    )

    assert len(candidates) == 1
    assert candidates[0].token_id == 2
    assert candidates[0].score == 9.0


def test_ranker_filters_candidate_that_would_duplicate_after_text(make_index) -> None:
    index = make_index(
        [
            (1, "你好", "nihao", "ni'hao", 2, 0),
            (2, "拟好", "nihao", "ni'hao", 2, 0),
        ]
    )

    candidates = rank_candidates(
        index=index,
        raw_pinyin="nihao",
        logits=[0.0, 10.0, 1.0],
        context_epoch=1,
        after_text="你好世界",
    )

    assert [candidate.text for candidate in candidates] == ["拟好"]


def test_ranker_rejects_token_id_outside_logit_vector(make_index) -> None:
    index = make_index([(9, "你", "ni", 1, 0)])
    with pytest.raises(IndexError, match="outside logits length"):
        rank_candidates(
            index=index,
            raw_pinyin="ni",
            logits=[0.0],
            context_epoch=1,
        )


def test_apostrophe_forces_syllable_boundary(make_index) -> None:
    index = make_index(
        [
            (1, "先", "xian", "xian", 1, 0),
            (2, "西安", "xian", "xi'an", 2, 0),
        ]
    )
    candidates = rank_candidates(
        index=index,
        raw_pinyin="xi'an",
        logits=[0.0, 10.0, 1.0],
        context_epoch=1,
    )
    assert [candidate.text for candidate in candidates] == ["西安"]


def test_consumed_keys_counts_raw_apostrophe_keys(make_index) -> None:
    index = make_index(
        [
            (1, "西安", "xian", "xi'an", 2, 0),
            (2, "西", "xi", "xi", 1, 0),
        ]
    )
    candidate = rank_candidates(
        index=index,
        raw_pinyin="xi'an",
        logits=[0.0, 1.0, 2.0],
        context_epoch=1,
    )
    by_text = {item.text: item for item in candidate}
    assert by_text["西安"].consumed_keys == 5
    assert by_text["西"].consumed_keys == 3


def test_wide_prefix_does_not_drop_highest_logit_after_arbitrary_cap(make_index) -> None:
    rows = [
        (token_id, chr(0x4E00 + token_id), "a", "a", 1, 0)
        for token_id in range(1, 5002)
    ]
    index = make_index(rows)
    logits = [0.0] * 5002
    logits[5001] = 100.0

    candidates = rank_candidates(
        index=index,
        raw_pinyin="a",
        logits=logits,
        context_epoch=1,
        limit=1,
    )

    assert candidates[0].token_id == 5001
