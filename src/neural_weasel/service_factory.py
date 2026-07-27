from __future__ import annotations

from typing import Any

from .backends import FullLogitsSnapshotBackend, SparseProjectionBackend
from .bilingual_engine import BilingualImeEngine
from .index import PinyinIndex
from .unified import LatinPrefixConstraint, PinyinConstraint


def build_bilingual_engine(
    *,
    runtime: Any,
    index: PinyinIndex,
    backend_kind: str,
) -> BilingualImeEngine:
    if backend_kind == "full":
        backend = FullLogitsSnapshotBackend(runtime)
    elif backend_kind == "sparse":
        backend = SparseProjectionBackend(runtime)
    else:
        raise ValueError(f"unsupported model backend: {backend_kind}")

    return BilingualImeEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint.from_tokenizer(runtime.tokenizer),
    )
