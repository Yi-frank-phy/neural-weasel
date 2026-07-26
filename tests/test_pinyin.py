from __future__ import annotations

import pytest

from neural_weasel.pinyin import (
    PinyinInputError,
    concatenate_path,
    is_all_han,
    is_han_character,
    normalize_raw_pinyin,
    parse_raw_pinyin,
    pronunciation_paths,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nihao", "nihao"),
        (" NI'HAO ", "nihao"),
        ("ＮＩＨＡＯ", "nihao"),
        ("lüe", "lve"),
        ("", ""),
    ],
)
def test_normalize_raw_pinyin(raw: str, expected: str) -> None:
    assert normalize_raw_pinyin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["'ni", "ni'", "ni''hao", "ni hao", "ni3", "nǐ", "nu:e", "你"],
)
def test_normalize_raw_pinyin_rejects_unsupported_input(raw: str) -> None:
    with pytest.raises(PinyinInputError):
        normalize_raw_pinyin(raw)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("你", True),
        ("㐀", True),
        ("龍", True),
        ("A", False),
        ("你好", False),
        ("", False),
    ],
)
def test_is_han_character(value: str, expected: bool) -> None:
    assert is_han_character(value) is expected


def test_is_all_han_requires_nonempty_all_han_text() -> None:
    assert is_all_han("你好")
    assert not is_all_han("")
    assert not is_all_han("你好!")


def test_pronunciation_paths_contains_multiple_readings_for_polyphone() -> None:
    paths = pronunciation_paths("行")
    assert ("xing",) in paths
    assert ("hang",) in paths
    assert len(paths) == len(set(paths))


def test_pronunciation_paths_forms_cartesian_product_and_honors_limit() -> None:
    paths = pronunciation_paths("行行", max_paths=3)
    assert len(paths) == 3
    assert all(len(path) == 2 for path in paths)


def test_pronunciation_paths_rejects_non_han_text() -> None:
    assert pronunciation_paths("你A") == ()


def test_concatenate_path() -> None:
    assert concatenate_path(("xi", "an")) == "xian"


def test_parse_raw_pinyin_preserves_explicit_boundaries_and_raw_key_positions() -> None:
    parsed = parse_raw_pinyin("XI'AN")
    assert parsed.raw == "xi'an"
    assert parsed.compact == "xian"
    assert parsed.explicit_boundaries == frozenset({2})
    assert parsed.raw_characters_for_letters(2) == 3
    assert parsed.raw_characters_for_letters(4) == 5
