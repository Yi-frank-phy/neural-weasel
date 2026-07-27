from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is required on Windows")
    root = Path(local) / "NeuralWeasel"
    root.mkdir(parents=True, exist_ok=True)
    return root


def configure_hf_cache() -> Path:
    cache = data_root() / "huggingface"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache))
    os.environ.setdefault("HF_HUB_CACHE", str(cache / "hub"))
    return cache


def indexes_root() -> Path:
    path = data_root() / "indexes"
    path.mkdir(parents=True, exist_ok=True)
    return path
