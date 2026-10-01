"""Offline native model A/B on public synthetic fixtures, never editor text.

This compares model-only constrained joint probabilities, not the production
candidate search, TSF acceptance, or GPU latency. CPU-only by construction.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from neural_weasel.context_prompt import ContextPromptConfig, ContextPromptEncoder
from neural_weasel.llama_runtime import LlamaCppBackend

FIXTURES = (
    (
        "policy",
        "zh",
        "shishi",
        "我们现在需要",
        "这项新政策，而不是继续讨论。",
        ("实施", "事实", "试试"),
        "实施",
    ),
    (
        "facts",
        "zh",
        "shishi",
        "我们现在需要",
        "，而不是未经证实的猜测。",
        ("实施", "事实", "试试"),
        "事实",
    ),
    (
        "archive",
        "en",
        "comp",
        "We will ",
        " these large files into a small archive.",
        ("compress", "compare", "compile", "complete"),
        "compress",
    ),
    (
        "executable",
        "en",
        "comp",
        "We will ",
        " these source files into an executable.",
        ("compress", "compare", "compile", "complete"),
        "compile",
    ),
    ("append", "code", "app", "items.", "(new_item)\n", ("append", "apply", "approve"), "append"),
    (
        "replace",
        "code",
        "rep",
        "result = text.",
        "('old', 'new')\n",
        ("replace", "repeat", "repr"),
        "replace",
    ),
)


def _validate_fixtures() -> None:
    from pypinyin import lazy_pinyin

    for _, language, keys, _, _, candidates, expected in FIXTURES:
        assert expected in candidates
        if language == "zh":
            assert all("".join(lazy_pinyin(word)) == keys for word in candidates)
        else:
            assert all(
                word.isascii() and word.isalpha() and word.startswith(keys) for word in candidates
            )


def _logits(model) -> np.ndarray:
    return np.ctypeslib.as_array(model._ctx.get_logits(), shape=(model.n_vocab(),)).copy()


def _score_candidates(model, prompt: tuple[int, ...], candidates: tuple[str, ...]):
    from llama_cpp import llama_cpp

    model._ctx.kv_cache_clear()
    model.reset()
    model.eval(list(prompt))
    root_logits = _logits(model)
    size = int(llama_cpp.llama_state_seq_get_size(model._ctx.ctx, 0))
    if not 0 < size <= 512 * 1024 * 1024:
        raise RuntimeError("native root state is unavailable or exceeds the benchmark bound")
    state = (ctypes.c_uint8 * size)()
    if llama_cpp.llama_state_seq_get_data(model._ctx.ctx, state, size, 0) != size:
        raise RuntimeError("native root state capture was incomplete")
    results = []
    for candidate in candidates:
        tokens = model.tokenize(candidate.encode(), add_bos=False, special=False)
        if not tokens or len(tokens) > 16 or len(prompt) + len(tokens) > model.n_ctx():
            raise ValueError("synthetic candidate exceeds the constrained token budget")
        if llama_cpp.llama_state_seq_set_data(model._ctx.ctx, state, size, 0) != size:
            raise RuntimeError("native root state restore was incomplete")
        model.n_tokens = len(prompt)
        values = root_logits
        total = 0.0
        for index, token in enumerate(tokens):
            total += float(
                LlamaCppBackend._select_continuation_values(
                    values, np.asarray([token]), normalize_log_probs=True
                )[0]
            )
            if index + 1 < len(tokens):
                model.eval([token])
                values = _logits(model)
        if not math.isfinite(total):
            raise RuntimeError("synthetic candidate probability is not finite")
        results.append({"candidate": candidate, "tokens": len(tokens), "joint_logprob": total})
    return sorted(results, key=lambda row: row["joint_logprob"], reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.threads <= 4:
        parser.error("threads must be between 1 and 4")
    _validate_fixtures()
    import llama_cpp
    from llama_cpp import Llama

    with args.gguf.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    started = time.monotonic()
    model = Llama(
        model_path=str(args.gguf),
        n_gpu_layers=0,
        n_ctx=256,
        n_batch=64,
        n_threads=args.threads,
        n_threads_batch=args.threads,
        offload_kqv=False,
        op_offload=False,
        verbose=False,
    )
    try:
        encoder = ContextPromptEncoder(model, ContextPromptConfig(mode="fim", max_after_tokens=64))
        rows = []
        for case, language, keys, before, after, candidates, expected in FIXTURES:
            row = {"case": case, "language": language, "raw_keys": keys, "expected": expected}
            for mode in ("continuation", "fim"):
                prompt = (
                    tuple(model.tokenize(before.encode(), add_bos=False, special=False))
                    if mode == "continuation"
                    else encoder.encode(before, after, max_before_tokens=128, n_ctx=256)
                )
                ranked = _score_candidates(model, prompt, candidates)
                row[mode] = {
                    "root_tokens": len(prompt),
                    "ranking": ranked,
                    "correct": ranked[0]["candidate"] == expected,
                }
            rows.append(row)
            print(json.dumps({"completed_case": case}, ensure_ascii=False), flush=True)
        result = {
            "scope": "six public synthetic fixtures; model-only joint-logprob comparison",
            "model_filename": args.gguf.name,
            "model_sha256": digest,
            "llama_cpp_python": llama_cpp.__version__,
            "n_gpu_layers": 0,
            "n_ctx": 256,
            "threads": args.threads,
            "elapsed_seconds": time.monotonic() - started,
            "markers": encoder._markers,
            "bos_required": bool(encoder._bos),
            "correct": {
                mode: sum(row[mode]["correct"] for row in rows) for mode in ("continuation", "fim")
            },
            "cases": rows,
        }
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print("FIM_CONTEXT_AB_RESULT=" + json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        model.close()


if __name__ == "__main__":
    main()
