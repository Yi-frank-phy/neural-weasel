from __future__ import annotations

from typing import Any

from .backends import SparseProjectionBackend
from .bilingual_engine import BilingualImeEngine
from .conditional_backend import ConditionalFullLogitsBackend
from .index import PinyinIndex
from .mixed_pinyin import MixedPinyinConstraint
from .runtime_identity import validated_runtime_index_identity
from .unified import LatinPrefixConstraint


def build_bilingual_engine(
    *,
    runtime: Any,
    index: PinyinIndex,
    backend_kind: str,
) -> BilingualImeEngine:
    identity = validated_runtime_index_identity(runtime, index)

    if backend_kind == "full":
        backend = ConditionalFullLogitsBackend(runtime)
    elif backend_kind == "sparse":
        backend = SparseProjectionBackend(runtime)
    else:
        raise ValueError(f"unsupported model backend: {backend_kind}")

    return BilingualImeEngine(
        backend=backend,
        pinyin_constraint=MixedPinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint.from_tokenizer(runtime.tokenizer),
        diagnostic_identity=identity,
    )
