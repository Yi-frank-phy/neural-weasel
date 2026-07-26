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

        self.model = Qwen3_5ForCausalLM.from_pretrained(model_id, **kwargs)
        self.model.eval()
        verify_model_device_map(self.model)
        require_runtime_headroom(torch)
        self._lock = threading.Lock()
        self._epoch = 0

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        # Base continuation uses raw text before the caret. The after text remains
        # metadata for de-duplication and future FIM experiments.
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
            input_ids = self.torch.tensor(
                [before_ids],
                dtype=self.torch.long,
                device="cuda:0",
            )
            attention_mask = self.torch.ones_like(input_ids)
            self.torch.cuda.synchronize(0)
            started = time.perf_counter()
            outputs = self.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                use_cache=True,
                logits_to_keep=1,
                return_dict=True,
            )
            logits = np.asarray(outputs.logits[0, -1].float().cpu().numpy()).copy()
            logits.flags.writeable = False
            self.torch.cuda.synchronize(0)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._epoch += 1
            require_runtime_headroom(self.torch)
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
