from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .acquire_model import AcquiredGguf, ensure_production_gguf
from .gguf_artifact import PRODUCTION_GGUF, ProductionGgufArtifact
from .gguf_index import GgufPinyinIndexBuilder, default_gguf_index_path
from .index import PinyinIndex
from .llama_runtime import (
    DEFAULT_MAX_BEFORE_TOKENS,
    DEFAULT_N_BATCH,
    DEFAULT_N_CTX,
    LlamaCppBackend,
)


@dataclass(frozen=True, slots=True)
class ProductionRuntimeConfig:
    max_before_tokens: int = DEFAULT_MAX_BEFORE_TOKENS
    n_ctx: int = DEFAULT_N_CTX
    n_batch: int = DEFAULT_N_BATCH

    def __post_init__(self) -> None:
        if self.max_before_tokens < 1:
            raise ValueError("max_before_tokens must be positive")
        if self.n_ctx < 1:
            raise ValueError("n_ctx must be positive")
        if self.n_batch < 1:
            raise ValueError("n_batch must be positive")
        if self.max_before_tokens > self.n_ctx:
            raise ValueError("max_before_tokens must not exceed n_ctx")


DEFAULT_PRODUCTION_RUNTIME_CONFIG = ProductionRuntimeConfig()


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
    runtime_config: ProductionRuntimeConfig | None = None,
) -> ProductionRuntime:
    config = runtime_config or DEFAULT_PRODUCTION_RUNTIME_CONFIG
    acquired = ensure_production_gguf(artifact or PRODUCTION_GGUF, gguf_path)
    runtime = LlamaCppBackend(
        acquired,
        max_before_tokens=config.max_before_tokens,
        n_ctx=config.n_ctx,
        n_batch=config.n_batch,
    )
    index = ensure_production_index(runtime, index_path)
    return ProductionRuntime(
        acquired=acquired,
        runtime=runtime,
        index_path=index.path,
        index=index,
    )
