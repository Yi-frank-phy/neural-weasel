"""Pinned modern character readings; no frequencies or editor data."""

from pathlib import Path
from types import MappingProxyType


def _load():
    readings = {}
    for line in (
        Path(__file__)
        .with_name("data")
        .joinpath("modern_han.tsv")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        char, syllable = line.split("\t")
        if len(char) != 1 or not syllable.isascii() or not syllable.isalpha():
            raise ValueError("Invalid modern pronunciation data")
        readings.setdefault(char, []).append(syllable)
    return MappingProxyType(
        {char: tuple(dict.fromkeys(values)) for char, values in readings.items()}
    )


MODERN_READINGS = _load()


def is_modern_han(text: str) -> bool:
    return bool(text) and all(char in MODERN_READINGS for char in text)
