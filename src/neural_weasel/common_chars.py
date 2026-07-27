from __future__ import annotations

from .pinyin import is_han_character


def gb2312_level1_characters() -> tuple[str, ...]:
    """Return the 3,755 first-level GB2312 Han characters.

    This is generated from Python's standard codec rather than copied from an
    opaque frequency list, which makes the coverage gate deterministic.
    """
    characters: list[str] = []
    for lead in range(0xB0, 0xD8):
        for trail in range(0xA1, 0xFF):
            try:
                text = bytes((lead, trail)).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if is_han_character(text):
                characters.append(text)
    if len(characters) != 3755:
        raise RuntimeError(f"unexpected GB2312 level-1 character count: {len(characters)}")
    return tuple(characters)

