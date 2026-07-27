from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product

from pypinyin import Style, pinyin

_INPUT_RE = re.compile(r"^[a-z']*$")


class PinyinInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedPinyinInput:
    raw: str
    compact: str
    explicit_boundaries: frozenset[int]

    def raw_characters_for_letters(self, letters: int) -> int:
        if letters >= len(self.compact):
            return len(self.raw)
        seen = 0
        position = 0
        while position < len(self.raw) and seen < letters:
            if self.raw[position] != "'":
                seen += 1
            position += 1
        while position < len(self.raw) and self.raw[position] == "'":
            position += 1
        return position


def parse_raw_pinyin(raw: str) -> ParsedPinyinInput:
    normalized = unicodedata.normalize("NFKC", raw).strip().lower().replace("ü", "v")
    if not _INPUT_RE.fullmatch(normalized):
        raise PinyinInputError("v0.1 accepts only ASCII a-z and apostrophe")
    if normalized.startswith("'") or normalized.endswith("'") or "''" in normalized:
        raise PinyinInputError("apostrophes must separate non-empty syllable groups")
    compact_characters: list[str] = []
    boundaries: set[int] = set()
    for character in normalized:
        if character == "'":
            boundaries.add(len(compact_characters))
        else:
            compact_characters.append(character)
    return ParsedPinyinInput(
        raw=normalized,
        compact="".join(compact_characters),
        explicit_boundaries=frozenset(boundaries),
    )


def normalize_raw_pinyin(raw: str) -> str:
    return parse_raw_pinyin(raw).compact


def is_han_character(char: str) -> bool:
    if len(char) != 1:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x323AF
    )


def is_all_han(text: str) -> bool:
    return bool(text) and all(is_han_character(char) for char in text)


def _normalize_syllable(value: str) -> str:
    return value.lower().replace("ü", "v").replace("u:", "v")


def pronunciation_paths(text: str, max_paths: int = 256) -> tuple[tuple[str, ...], ...]:
    if not is_all_han(text):
        return ()
    per_character: list[tuple[str, ...]] = []
    for readings in pinyin(text, style=Style.NORMAL, heteronym=True, strict=False):
        clean = tuple(dict.fromkeys(_normalize_syllable(value) for value in readings if value))
        if not clean:
            return ()
        per_character.append(clean)

    paths: list[tuple[str, ...]] = []
    for path in product(*per_character):
        paths.append(tuple(path))
        if len(paths) >= max_paths:
            break
    return tuple(paths)


def concatenate_path(path: Iterable[str]) -> str:
    return "".join(path)

