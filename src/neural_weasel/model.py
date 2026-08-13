from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .backends import RuntimeSnapshot
from .gpu import (
    memory_snapshot,
    require_runtime_headroom,
    verify_model_device_map,
    verify_torch_binding,
)

BASE_MODELS: dict[str, dict[str, str]] = {
    "Qwen/Qwen3.5-0.8B-Base": {
        "family": "qwen3_5",
        "default_precision": "int8",
    },
    "Qwen/Qwen3.5-4B-Base": {
        "family": "qwen3_5",
        "default_precision": "nf4",
    },
    "Qwen/Qwen3-0.6B-Base": {
        "family": "qwen3",
        "default_precision": "bf16",
    },
}
SUPPORTED_PRECISIONS = frozenset({"bf16", "int8", "nf4"})


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
        precision: str | None = None,
        max_before_tokens: int = 3072,
        max_after_tokens: int = 512,
    ) -> None:
        if model_id not in BASE_MODELS:
            raise ModelPolicyError(
                f"checkpoint {model_id!r} is not in the Base-only allowlist: {sorted(BASE_MODELS)}"
            )

        import torch
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            Qwen3_5ForCausalLM,
        )

        self.torch = torch
        self.target_gpu = verify_torch_binding(torch)
        self.model_id = model_id
        policy = BASE_MODELS[model_id]
        self.model_family = policy["family"]
        self.precision = precision or policy["default_precision"]
        if self.precision not in SUPPORTED_PRECISIONS:
            raise ModelPolicyError(
                f"unsupported precision {self.precision!r}; expected one of "
                f"{sorted(SUPPORTED_PRECISIONS)}"
            )
        self.max_before_tokens = max_before_tokens
        self.max_after_tokens = max_after_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, use_fast=True)

        kwargs: dict[str, Any] = {
            "torch_dtype": torch.bfloat16,
            "device_map": {"": 0},
            "trust_remote_code": False,
        }
        if self.precision == "int8":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_enable_fp32_cpu_offload=False,
            )
        elif self.precision == "nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        torch.cuda.reset_peak_memory_stats(0)
        model_class = Qwen3_5ForCausalLM if self.model_family == "qwen3_5" else AutoModelForCausalLM
        self.model = model_class.from_pretrained(model_id, **kwargs)
        self.model.eval()
        verify_model_device_map(self.model)
        require_runtime_headroom(torch)
        self._lock = threading.Lock()
        self._cache_state_lock = threading.Lock()
        self._cache_generation = 0
        self._context_cache: _ContextCacheState | None = None
        self._epoch = 0

    def load(self) -> None:
        """The constructor performs the strict one-time Qwen load."""

    def full_logits(self, before: str, after: str = "") -> RuntimeSnapshot:
        snapshot = self.create_snapshot(before, after)
        return RuntimeSnapshot(
            payload=snapshot.logits,
            before_hash=snapshot.before_hash,
            after_hash=snapshot.after_hash,
            latency_ms=snapshot.latency_ms,
        )

    def continuation_hidden(self, before: str, after: str = "") -> RuntimeSnapshot:
        """Run the Qwen base transformer without the full-vocabulary lm head."""

        with self._lock, self.torch.inference_mode():
            before_ids = self.tokenizer.encode(before, add_special_tokens=False)[
                -self.max_before_tokens :
            ]
            if not before_ids:
                fallback_id = self.tokenizer.bos_token_id or self.tokenizer.eos_token_id
                if fallback_id is None:
                    raise ModelPolicyError("tokenizer has no token usable for empty context")
                before_ids = [fallback_id]

            input_ids = self.torch.tensor(
                [before_ids],
                dtype=self.torch.long,
                device="cuda:0",
            )
            attention_mask = self.torch.ones(
                (1, len(before_ids)),
                dtype=self.torch.long,
                device="cuda:0",
            )
            self.torch.cuda.synchronize(0)
            started = time.perf_counter()
            outputs = self.model.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                use_cache=True,
                return_dict=True,
            )
            self.torch.cuda.synchronize(0)
            elapsed_ms = (time.perf_counter() - started) * 1000
            hidden = outputs.last_hidden_state[0, -1].detach()
            require_runtime_headroom(self.torch)
            return RuntimeSnapshot(
                payload=hidden,
                before_hash=_text_hash(before),
                after_hash=_text_hash(after),
                latency_ms=elapsed_ms,
            )

    def output_weight(self):
        return self.model.get_output_embeddings().weight

    def output_bias(self):
        return getattr(self.model.get_output_embeddings(), "bias", None)

    def invalidate_private_state(self) -> None:
        self.invalidate_context_cache()

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

                snapshot_started = time.perf_counter()
                logits = np.asarray(outputs.logits[0, -1].float().cpu().numpy()).copy()
                logits.flags.writeable = False
                # The context snapshot is not queryable until the complete
                # vocabulary vector has reached immutable CPU memory.
                elapsed_ms += (time.perf_counter() - snapshot_started) * 1000
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
            "model_family": self.model_family,
            "precision": self.precision,
            "gpu_name": self.target_gpu.name,
            "gpu_uuid": self.target_gpu.uuid,
            "memory": memory_snapshot(self.torch),
        }
