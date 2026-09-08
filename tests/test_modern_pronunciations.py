import pytest

from neural_weasel.pinyin import pronunciation_paths
from neural_weasel.simplified_chinese import is_simplified_han


@pytest.mark.parametrize(
    "text,reading",
    [("王", "wang"), ("青", "qing"), ("妖", "yao"), ("方", "fang"), ("或", "huo"), ("吾", "wu")],
)
def test_modern_single_character_readings(text, reading):
    assert pronunciation_paths(text) == ((reading,),)


def test_modern_polyphones_and_phrase_readings():
    assert {("hang",), ("xing",)} <= set(pronunciation_paths("行"))
    assert {("le",), ("yue",)} <= set(pronunciation_paths("乐"))
    assert pronunciation_paths("继续") == (("ji", "xu"),)
    assert pronunciation_paths("音乐") == (("yin", "yue"),)


def test_japanese_and_nonstandard_han_are_not_chinese_candidates():
    assert pronunciation_paths("継続") == ()
    assert not is_simplified_han("継続")
    assert is_simplified_han("继续")
