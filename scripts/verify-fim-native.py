"""Isolated CPU/Q8 correctness checks; never load or change the Windows IME.

Run with colab-job highram and --source-commit <immutable commit>.
Only public synthetic text is used. GPU construction, latency and TSF UI are
outside this test's scope. Production GPU admission checks remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def verify(model_path: str) -> dict:
    import llama_cpp
    import numpy as np

    from neural_weasel.backends import FullLogitsSnapshotBackend
    from neural_weasel.context_prompt import ContextPromptConfig, ContextPromptEncoder
    from neural_weasel.llama_runtime import LlamaCppBackend
    from neural_weasel.llama_vocab import LlamaVocabAdapter

    model = llama_cpp.Llama(
        model_path=model_path,
        n_ctx=256,
        n_batch=64,
        n_gpu_layers=0,
        n_threads=2,
        n_threads_batch=2,
        logits_all=False,
        verbose=False,
    )
    original_eval = model.eval
    evaluations = [0]

    def counted_eval(tokens):
        evaluations[0] += 1
        return original_eval(tokens)

    model.eval = counted_eval
    rows = []
    fixtures = (
        ("zh", "这是需要", "的问题。", ("注意的", "研究的方法")),
        (
            "en",
            "Please ",
            " the archive before sending it.",
            ("decompress_archive", "recompress_archive"),
        ),
        ("code", "result = ", "(source, options)", ("compile_document", "compress_archive")),
    )
    for mode in ("continuation", "fim"):
        # Attach the real CPU context solely for testing production runtime
        # methods. Do not fake GPU probes or exercise the GPU-only constructor.
        runtime = LlamaCppBackend.__new__(LlamaCppBackend)
        runtime.llama = model
        runtime.tokenizer = LlamaVocabAdapter(model)
        runtime.prompt_config = ContextPromptConfig(mode=mode, max_after_tokens=96)
        runtime.max_before_tokens = 96
        runtime.n_ctx = 256
        runtime.n_batch = 64
        runtime._prompt_encoder = ContextPromptEncoder(model, runtime.prompt_config)
        runtime._lock = threading.Lock()
        runtime._context_waiters_lock = threading.Lock()
        runtime._context_waiters = 0
        runtime._epoch = 0
        runtime._cached_token_ids = None
        runtime._cached_logits = None
        runtime._continuation_state_token_ids = None
        runtime._continuation_state_buffer = None
        runtime._continuation_state_size = 0
        runtime._last_refresh_diagnostics = None
        runtime._last_continuation_diagnostics = None
        backend = FullLogitsSnapshotBackend(runtime)
        backend.invalidate_private_state()

        for label, before, after, candidates in fixtures:
            state = backend.update_context(before, after)
            root = state.continuation_root
            assert root is not None and root.n_tokens == len(root.replay_token_ids)
            initial_evals = evaluations[0]
            cached = backend.update_context(before, after)
            assert evaluations[0] == initial_evals
            np.testing.assert_array_equal(cached.payload, state.payload)
            sequences = [runtime.tokenizer.encode(c, add_special_tokens=False) for c in candidates]
            assert all(2 <= len(s) <= 16 for s in sequences), sequences
            paths, allowed = [], []
            for sequence in sequences:
                for index in range(1, len(sequence)):
                    paths.append(tuple(sequence[:index]))
                    allowed.append((sequence[index],))
            baseline_evals = evaluations[0]
            for _ in range(100):
                scores = backend.score_allowed_tokens(cached, tuple(s[0] for s in sequences))
                assert np.isfinite(scores).all()
            assert evaluations[0] == baseline_evals
            assert not cached.payload.flags.writeable

            result = runtime.continue_log_probs_from_root(
                root,
                paths,
                allowed,
                deadline_ms=120000,
            )
            assert result is not None and len(result) == len(paths)
            assert runtime._last_continuation_diagnostics[5] is True
            assert model.n_tokens == root.n_tokens
            cached_evals = evaluations[0]
            backend.update_context(before, after)
            assert evaluations[0] == cached_evals

            # Independent cold-forward oracle: no runtime restore/cache helper.
            # Compare the same root/path batching and separately measure the
            # numerical difference from a single combined prefill batch.
            max_error = 0.0
            max_batching_delta = 0.0
            for path, permitted, actual in zip(paths, allowed, result, strict=True):
                model._ctx.kv_cache_clear()
                model.reset()
                model.eval([*root.replay_token_ids, *path])
                full = np.ctypeslib.as_array(
                    model._ctx.get_logits(),
                    shape=(model.n_vocab(),),
                ).astype(np.float64, copy=True)
                maximum = full.max()
                normalizer = maximum + np.log(np.exp(full - maximum).sum())
                combined = full[list(permitted)] - normalizer
                model._ctx.kv_cache_clear()
                model.reset()
                model.eval(list(root.replay_token_ids))
                model.eval(list(path))
                segmented_full = np.ctypeslib.as_array(
                    model._ctx.get_logits(),
                    shape=(model.n_vocab(),),
                ).astype(np.float64, copy=True)
                segmented_maximum = segmented_full.max()
                segmented_normalizer = segmented_maximum + np.log(
                    np.exp(segmented_full - segmented_maximum).sum()
                )
                expected = segmented_full[list(permitted)] - segmented_normalizer
                max_batching_delta = max(
                    max_batching_delta,
                    float(np.max(np.abs(combined - expected))),
                )
                error = float(np.max(np.abs(actual - expected)))
                max_error = max(max_error, error)
                np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-4)

            backend.invalidate_private_state()
            old_state = backend.update_context(before, after)
            old_root = old_state.continuation_root
            changed = backend.update_context(before, after + " Extra.")
            if mode == "fim":
                assert changed.continuation_root.replay_token_ids != old_root.replay_token_ids
                assert not np.array_equal(changed.payload, old_state.payload)
                # An older root may compute, but must not leave a newer cache
                # associated with the restored older model sequence.
                assert (
                    runtime.continue_log_probs_from_root(
                        old_root,
                        paths,
                        allowed,
                        deadline_ms=120000,
                    )
                    is not None
                )
                assert runtime._cached_token_ids is None
                assert runtime._cached_logits is None
            else:
                assert changed.continuation_root.replay_token_ids == old_root.replay_token_ids

            backend.invalidate_private_state()
            assert backend.latest_state() is None
            assert runtime._cached_token_ids is None and runtime._cached_logits is None
            assert runtime._continuation_state_token_ids is None
            assert runtime._continuation_state_buffer is None
            assert runtime._continuation_state_size == 0 and model.n_tokens == 0
            try:
                backend.score_allowed_tokens(changed, (sequences[0][0],))
            except RuntimeError:
                pass
            else:
                raise AssertionError("private invalidation did not reject old state")
            fresh = backend.update_context(before, after)
            assert np.isfinite(fresh.payload).all()
            backend.invalidate_private_state()
            rows.append(
                {
                    "mode": mode,
                    "fixture": label,
                    "candidate_token_counts": [len(s) for s in sequences],
                    "continuation_branches": len(paths),
                    "max_logprob_error": max_error,
                    "max_combined_vs_segmented_logprob_delta": max_batching_delta,
                    "cache_hit": True,
                    "keypress_100_calls_zero_eval": True,
                    "root_restore_cache_preserved": True,
                    "suffix_update": True,
                    "private_cleanup_and_stale_rejection": True,
                }
            )
    model.close()
    with open(model_path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "status": "passed",
        "llama_cpp_version": llama_cpp.__version__,
        "model_sha256": digest,
        "cpu_threads": 2,
        "n_ctx": 256,
        "native_evaluations": evaluations[0],
        "rows": rows,
        "scope": "CPU native runtime correctness; not GPU admission, quality, latency or TSF",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--gguf")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.worker:
        if not args.gguf or not args.output:
            parser.error("worker requires --gguf and --output")
        Path(args.output).write_text(json.dumps(verify(args.gguf), indent=2), encoding="utf-8")
        return
    commit = args.source_commit or ""
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        parser.error("source-commit must be the immutable 40-character commit")
    root = Path(tempfile.mkdtemp(prefix="neural-weasel-native-test-"))
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(["git", "init", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "fetch",
            "--depth=1",
            "https://github.com/Yi-frank-phy/neural-weasel.git",
            commit,
        ],
        check=True,
        timeout=120,
    )
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual_commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert actual_commit == commit
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "llama-cpp-python==0.3.23",
            "numpy",
            "huggingface_hub",
            "--extra-index-url",
            "https://abetlen.github.io/llama-cpp-python/whl/cpu",
        ],
        check=True,
        timeout=480,
    )
    sys.path.insert(0, str(root / "src"))
    from huggingface_hub import hf_hub_download

    from neural_weasel.gguf_artifact import PRODUCTION_GGUF

    model = hf_hub_download(
        repo_id=PRODUCTION_GGUF.repo_id,
        filename=PRODUCTION_GGUF.filename,
        revision=PRODUCTION_GGUF.revision,
        token=False,
    )
    output = root / "native-test.json"
    # colab-job executes a notebook cell, without __file__.
    # Materialize the function for the isolated child process.
    worker_source = (
        "import hashlib, json, threading, sys\nfrom pathlib import Path\n"
        + inspect.getsource(verify)
        + "\nPath(sys.argv[2]).write_text(json.dumps(verify(sys.argv[1]), indent=2), "
        + "encoding='utf-8')\n"
    )
    worker_script = root / "verify-native-worker.py"
    worker_script.write_text(worker_source, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(worker_script),
            model,
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
    )
    if completed.returncode:
        print(completed.stdout, flush=True)
        print(completed.stderr, flush=True)
        completed.check_returncode()
    result = json.loads(output.read_text(encoding="utf-8"))
    result["source_commit"] = actual_commit
    result["model_hub_revision"] = PRODUCTION_GGUF.revision
    result["verification_worker_sha256"] = hashlib.sha256(worker_script.read_bytes()).hexdigest()
    print("FIM_NATIVE_TEST_RESULT=" + json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
