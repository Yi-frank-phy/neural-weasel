from __future__ import annotations

import copy
from typing import Any

_LINEAR_DICTS = (
    "conv_states",
    "recurrent_states",
    "is_conv_states_initialized",
    "is_recurrent_states_initialized",
    "has_previous_state",
    "conv_kernel_size",
)


def fork_transformers_cache(cache: Any) -> Any | None:
    layers = getattr(cache, "layers", None)
    if not isinstance(layers, list) or not callable(getattr(cache, "reorder_cache", None)):
        return None
    fork = copy.copy(cache)
    copied = []
    for layer in layers:
        target = copy.copy(layer)
        for name, value in getattr(layer, "__dict__", {}).items():
            if isinstance(value, dict):
                setattr(target, name, dict(value))
            elif isinstance(value, list):
                setattr(target, name, list(value))
        for name in _LINEAR_DICTS:
            value = getattr(layer, name, None)
            if isinstance(value, dict):
                setattr(target, name, dict(value))
        copied.append(target)
    fork.layers = copied
    return fork
