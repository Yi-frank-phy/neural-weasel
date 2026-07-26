from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .gpu import (
    memory_snapshot,
    require_runtime_headroom,
    verify_model_device_map,
    verify_torch_binding,
)

BASE_MODELS = {
    "Qwen/Qwen3.5-0.8B-Base": "bf16",
    "Qwen/Qwen3.5-4B-Base": "nf4",
}


class ModelPolicyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LogitsSnapshot:
    epoch: int
    before_hash: str
    after_hash: str
    logits: np.ndarray
    created_monotonic: float
    latency_ms: float
    after_text: str = field(repr=False, default="")


@dataclass(slots=True)
class _ContextCacheState:
    token_ids: tuple[int, ...]
    past_key_values: Any
    logits: np.ndarray


def _text_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class QwenBaseBackend:
    def __init__(
        self,
        model_id: str,
        max_before_tokens: int = 3072,
        max_after_tokens: int = 512,
    ) -> None:
        if model_id not in BASE_MODELS:
            raise ModelPolicyError(
                f"checkpoint {model_id!r} is not in the Base-only allowlist: {sorted(BASE_MODELS)}"
            )

        import torch
        from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3_5ForCausalLM

        self.torch = torch
        self.target_gpu = verify_torch_binding(torch)
        self.model_id = model_id
        self.max_before_tokens = max_before_tokens
        self.max_after_tokens = max_after_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=True)

        mode = BASE_MODELS[model_id]
        kwargs: dict[str, Any] = {
            "torch_dtype": torch.bfloat16,
            "device_map": {"": 0},
            "trust_remote_code": False,
        }
        if mode == "nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        torch.cuda.reset_peak_memory_stats(0)
        self.model = Qwen3_5ForCausalLM.from_pretrained(model_id, **kwargs)
        self.model.eval()
        verify_model_device_map(self.model)
        require_runtime_headroom(torch)
        self._lock = threading.Lock()
        self._cache_state_lock = threading.Lock()
        self._cache_generation = 0
        self._context_cache: _ContextCacheState | None = None
        self._epoch = 0

    def invalidate_context_cache(self) -> None:
        """Invalidate cached private context without waiting for a model forward.

        A forward already in progress holds its cache through a local reference.
        The generation check in ``create_snapshot`` prevents that stale forward
        from installing the cache again after this method returns.
        """

        with self._cache_state_lock:
            self._cache_generation += 1
            self._context_cache = None

    @staticmethod
    def _cache_has_length(cache: Any, expected_length: int) -> bool:
        get_seq_length = getattr(cache, "get_seq_length", None)
        if not callable(get_seq_length):
            return False
        try:
            return int(get_seq_length()) == expected_length
        except (TypeError, ValueError, RuntimeError):
            return False

    def _forward_tokens(
        self,
        token_ids: list[int],
        *,
        total_length: int,
        past_key_values: Any | None = None,
    ) -> tuple[Any, float]:
        input_ids = self.torch.tensor(
            [token_ids],
            dtype=self.torch.long,
            device="cuda:0",
        )
        attention_mask = self.torch.ones(
            (1, total_length),
            dtype=self.torch.long,
            device="cuda:0",
        )
        kwargs: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "use_cache": True,
            "logits_to_keep": 1,
            "return_dict": True,
        }
        if past_key_values is not None:
            kwargs["past_key_values"] = past_key_values

        self.torch.cuda.synchronize(0)
        started = time.perf_counter()
        outputs = self.model(**kwargs)
        self.torch.cuda.synchronize(0)
        return outputs, (time.perf_counter() - started) * 1000

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        # Base continuation uses raw text before the caret. The after text remains
        # metadata for de-duplication and future FIM experiments.
        with self._cache_state_lock:
            call_cache_generation = self._cache_generation

        with self._lock, self.torch.inference_mode():
            before_ids = self.tokenizer.encode(before, add_special_tokens=False)[
                -self.max_before_tokens :
            ]
            after_ids = self.tokenizer.encode(after, add_special_tokens=False)[
                : self.max_after_tokens
            ]
            if not before_ids:
                fallback_id = self.tokenizer.bos_token_id or self.tokenizer.eos_token_id
                if fallback_id is None:
                    raise ModelPolicyError("tokenizer has no token usable for empty context")
                before_ids = [fallback_id]

            token_ids = tuple(before_ids)
            with self._cache_state_lock:
                if call_cache_generation == self._cache_generation:
                    cached_state = self._context_cache
                    # Incremental forwards mutate cache objects in place. Keep
                    # the shared slot empty until the complete result is ready.
                    self._context_cache = None
                else:
                    cached_state = None

            if cached_state is not None and token_ids == cached_state.token_ids:
                logits = cached_state.logits
                past_key_values = cached_state.past_key_values
                elapsed_ms = 0.0
            else:
                can_append = (
                    cached_state is not None
                    and cached_state.past_key_values is not None
                    and len(token_ids) > len(cached_state.token_ids)
                    and token_ids[: len(cached_state.token_ids)] == cached_state.token_ids
                    and self._cache_has_length(
                        cached_state.past_key_values,
                        len(cached_state.token_ids),
                    )
                )
                if can_append:
                    suffix_ids = before_ids[len(cached_state.token_ids) :]
                    try:
                        outputs, elapsed_ms = self._forward_tokens(
                            suffix_ids,
                            total_length=len(before_ids),
                            past_key_values=cached_state.past_key_values,
                        )
                    except Exception:
                        # Cache updates are in-place and may be partial on any
                        # exception. Never crop or reset this mixed
                        # Gated-DeltaNet/KV cache; rebuild from all retained IDs.
                        cached_state = None
                        outputs, elapsed_ms = self._forward_tokens(
                            before_ids,
                            total_length=len(before_ids),
                        )
                else:
                    outputs, elapsed_ms = self._forward_tokens(
                        before_ids,
                        total_length=len(before_ids),
                    )

                logits = np.asarray(outputs.logits[0, -1].float().cpu().numpy()).copy()
                logits.flags.writeable = False
                past_key_values = getattr(outputs, "past_key_values", None)

            self._epoch += 1
            require_runtime_headroom(self.torch)
            new_cache_state = _ContextCacheState(
                token_ids=token_ids,
                past_key_values=past_key_values,
                logits=logits,
            )
            with self._cache_state_lock:
                if call_cache_generation == self._cache_generation:
                    self._context_cache = new_cache_state

            return LogitsSnapshot(
                epoch=self._epoch,
                before_hash=_text_hash(before),
                after_hash=_text_hash(after),
                logits=logits,
                created_monotonic=time.monotonic(),
                latency_ms=elapsed_ms,
                after_text=self.tokenizer.decode(
                    after_ids,
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ),
            )

    def diagnostics(self) -> dict[str, object]:
        return {
            "model": self.model_id,
            "gpu_name": self.target_gpu.name,
            "gpu_uuid": self.target_gpu.uuid,
            "memory": memory_snapshot(self.torch),
        }
