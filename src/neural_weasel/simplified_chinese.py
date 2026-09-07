from __future__ import annotations

import warnings

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
    from zhconv import convert

from .modern_han import is_modern_han
from .pinyin import is_all_han


def is_simplified_han(text: str) -> bool:
    """Return whether text is all Han and already Mainland-Simplified."""

    return is_all_han(text) and is_modern_han(text) and convert(text, "zh-cn") == text
