from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import numpy as np

from .cache_fork import fork_transformers_cache


class QwenContinuationSession:
    def __init__(self, runtime: Any, cache: Any, total_length: int) -> None:
        self.runtime = runtime
        self.cache = cache
        self.total_length = total_length
        self.log_probs = None

    @classmethod
    def from_runtime(cls, runtime: Any):
        needed = ("_lock", "_cache_state_lock", "_context_cache", "torch", "model")
        if any(not hasattr(runtime, name) for name in needed):
            return None
        if not runtime._lock.acquire(blocking=False):
            return None
        try:
            with runtime._cache_state_lock:
                state = runtime._context_cache
                if state is None or state.past_key_values is None:
                    return None
                token_ids = state.token_ids
                cache = fork_transformers_cache(state.past_key_values)
            cache_ok = runtime._cache_has_length(
                state.past_key_values,
                len(token_ids),
            )
            if cache is None or not cache_ok:
                return None
            root = runtime.torch.as_tensor(
                [0],
                dtype=runtime.torch.long,
                device="cuda:0",
            )
            cache.reorder_cache(root)
            return cls(runtime, cache, len(token_ids))
        finally:
            runtime._lock.release()

    def advance(self, parent_indices: Sequence[int], token_ids: Sequence[int]) -> float:
        parents = tuple(int(value) for value in parent_indices)
        tokens = tuple(int(value) for value in token_ids)
        if not parents or len(parents) != len(tokens):
            raise ValueError("continuation batches must be equally sized and non-empty")
        torch = self.runtime.torch
        with self.runtime._lock, torch.inference_mode():
            beam_index = torch.as_tensor(parents, dtype=torch.long, device="cuda:0")
            self.cache.reorder_cache(beam_index)
            input_ids = torch.as_tensor(
                tokens,
                dtype=torch.long,
                device="cuda:0",
            ).reshape(-1, 1)
            next_length = self.total_length + 1
            attention_mask = torch.ones(
                (len(tokens), next_length),
                dtype=torch.long,
                device="cuda:0",
            )
            torch.cuda.synchronize(0)
            started = time.perf_counter()
            outputs = self.runtime.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                past_key_values=self.cache,
                use_cache=True,
                logits_to_keep=1,
                return_dict=True,
            )
            torch.cuda.synchronize(0)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.cache = outputs.past_key_values
            self.total_length = next_length
            self.log_probs = torch.log_softmax(
                outputs.logits[:, -1, :].float(),
                dim=-1,
            ).detach()
            return elapsed_ms

    def score_allowed(
        self,
        allowed_by_beam: Sequence[Sequence[int]],
    ) -> tuple[np.ndarray, ...]:
        if self.log_probs is None:
            raise RuntimeError("advance must run before child scoring")
        if len(allowed_by_beam) != int(self.log_probs.shape[0]):
            raise ValueError("allowed-token batches must match beam count")
        torch = self.runtime.torch
        result = []
        for row, allowed in enumerate(allowed_by_beam):
            ids = tuple(int(value) for value in allowed)
            if not ids:
                result.append(np.empty(0, dtype=np.float32))
                continue
            index = torch.as_tensor(
                ids,
                dtype=torch.long,
                device=self.log_probs.device,
            )
            selected = self.log_probs[row].index_select(0, index)
            result.append(np.asarray(selected.float().cpu().numpy(), dtype=np.float32))
        return tuple(result)
