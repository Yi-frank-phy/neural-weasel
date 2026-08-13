from __future__ import annotations

import copy
from typing import Any


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
        for name in ("conv_states", "recurrent_states"):
            value = getattr(layer, name, None)
            if isinstance(value, dict):
                setattr(target, name, dict(value))
        copied.append(target)
    fork.layers = copied
    return fork
