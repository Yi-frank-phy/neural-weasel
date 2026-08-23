from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .acquire_model import AcquiredGguf, ensure_production_gguf
from .gguf_artifact import PRODUCTION_GGUF, ProductionGgufArtifact
from .gguf_index import GgufPinyinIndexBuilder, default_gguf_index_path
from .index import PinyinIndex
from .llama_runtime import LlamaCppBackend


@dataclass(frozen=True, slots=True)
class ProductionRuntime:
    acquired: AcquiredGguf
    runtime: LlamaCppBackend
    index_path: Path
    index: PinyinIndex


def ensure_production_index(runtime: LlamaCppBackend, explicit: Path | None = None) -> PinyinIndex:
    from importlib.metadata import version

    path = explicit or default_gguf_index_path(
        runtime.model_id,
        runtime.gguf_sha256,
        runtime.vocab_fingerprint,
        version("pypinyin"),
    )
    if not path.exists():
        GgufPinyinIndexBuilder(
            runtime.tokenizer,
            model_id=runtime.model_id,
            gguf_sha256=runtime.gguf_sha256,
        ).build(path)
    return PinyinIndex(path)


def build_production_runtime(
    index_path: Path | None = None,
    *,
    artifact: ProductionGgufArtifact | None = None,
    gguf_path: Path | str | None = None,
) -> ProductionRuntime:
    acquired = ensure_production_gguf(artifact or PRODUCTION_GGUF, gguf_path)
    runtime = LlamaCppBackend(acquired)
    index = ensure_production_index(runtime, index_path)
    return ProductionRuntime(
        acquired=acquired,
        runtime=runtime,
        index_path=index.path,
        index=index,
    )
