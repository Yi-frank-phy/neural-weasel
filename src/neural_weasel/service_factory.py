from __future__ import annotations

from typing import Any

from .backends import FullLogitsSnapshotBackend, SparseProjectionBackend
from .bilingual_engine import BilingualImeEngine
from .conditional_backend import ConditionalFullLogitsBackend
from .index import PinyinIndex
from .mixed_pinyin import MixedPinyinConstraint
from .neural_latin import NeuralLatinPrefixConstraint
from .runtime_identity import validated_runtime_index_identity


def build_bilingual_engine(
    *,
    runtime: Any,
    index: PinyinIndex,
    backend_kind: str,
) -> BilingualImeEngine:
    identity = validated_runtime_index_identity(runtime, index)

    if getattr(runtime, "format", None) == "gguf":
        if backend_kind != "full":
            raise ValueError(
                "GGUF production runtime supports only the snapshot full-logits backend"
            )
        # Page-0 candidate handling reads immutable snapshots only. Any later
        # continuation work is deadline bounded and may fall back to the
        # permanent empty-context baseline.
        backend = FullLogitsSnapshotBackend(runtime)
    elif backend_kind == "full":
        backend = ConditionalFullLogitsBackend(runtime)
    elif backend_kind == "sparse":
        backend = SparseProjectionBackend(runtime)
    else:
        raise ValueError(f"unsupported model backend: {backend_kind}")

    engine = BilingualImeEngine(
        backend=backend,
        pinyin_constraint=MixedPinyinConstraint(index),
        latin_prefix_constraint=NeuralLatinPrefixConstraint.from_tokenizer(runtime.tokenizer),
        diagnostic_identity=identity,
    )
    # Service construction is the readiness boundary: the permanent
    # context-free neural scores and all single-letter page-0 entries exist
    # before a named pipe or HTTP listener can accept keypress queries.
    engine.initialize_neural_baseline()
    return engine
