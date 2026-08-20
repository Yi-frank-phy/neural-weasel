from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gpu import discover_target_gpu, verify_torch_binding
from .index import (
    PinyinIndex,
    PinyinIndexBuilder,
    default_index_path,
    resolved_tokenizer_revision,
    tokenizer_fingerprint,
)
from .paths import configure_hf_cache


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="neural-weasel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("gpu-info", help="verify strict RTX 4060 CUDA isolation")

    build = subparsers.add_parser("build-index", help="build a model-token pinyin index")
    build.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    build.add_argument("--revision", default="main")
    build.add_argument("--output", type=Path)

    predict = subparsers.add_parser("predict", help="run one constrained Base-model query")
    predict.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    predict.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    predict.add_argument("--index", type=Path)
    predict.add_argument("--before", required=True)
    predict.add_argument("--after", default="")
    predict.add_argument("--pinyin", required=True)
    predict.add_argument("--limit", type=int, default=5)

    serve = subparsers.add_parser("serve", help="start the per-user Windows named-pipe server")
    serve.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    serve.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    serve.add_argument("--index", type=Path)
    serve.add_argument("--backend", choices=("full", "sparse"), default="full")

    serve_http = subparsers.add_parser(
        "serve-http",
        help="start the loopback Wisdom Weasel HTTP compatibility server",
    )
    serve_http.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    serve_http.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    serve_http.add_argument("--index", type=Path)
    serve_http.add_argument("--backend", choices=("full", "sparse"), default="full")
    serve_http.add_argument("--host", default="127.0.0.1")
    serve_http.add_argument("--port", type=int, default=8000)

    simulate = subparsers.add_parser(
        "simulate",
        help="interactive candidate simulator; forward once, then query every edited pinyin",
    )
    simulate.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    simulate.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    simulate.add_argument("--index", type=Path)
    simulate.add_argument("--before", required=True)
    simulate.add_argument("--after", default="")

    benchmark = subparsers.add_parser(
        "benchmark",
        help="measure cached pinyin-query latency after one model forward",
    )
    benchmark.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    benchmark.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    benchmark.add_argument("--index", type=Path)
    benchmark.add_argument("--before", required=True)
    benchmark.add_argument("--after", default="")
    benchmark.add_argument("--pinyin", required=True)
    benchmark.add_argument("--iterations", type=int, default=1000)

    coverage = subparsers.add_parser(
        "coverage-check",
        help="verify all 3,755 GB2312 level-1 characters are inputtable",
    )
    coverage.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    coverage.add_argument("--index", type=Path)

    compare = subparsers.add_parser(
        "benchmark-backends",
        help="compare full-logits and sparse projection on identical legal tokens",
    )
    compare.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    compare.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", default="")
    compare.add_argument("--allowed-counts", type=int, nargs="+", default=[32, 128, 512])
    compare.add_argument("--iterations", type=int, default=20)

    replay = subparsers.add_parser(
        "replay",
        help="run the minimal bilingual replay fixture on the local Base model",
    )
    replay.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    replay.add_argument("--precision", choices=("bf16", "fp8", "int8", "nf4"), default="bf16")
    replay.add_argument("--index", type=Path)
    replay.add_argument("--fixture", type=Path, required=True)
    replay.add_argument("--backend", choices=("full", "sparse"), default="full")

    return parser


def _load_tokenizer(model_id: str, revision: str = "main"):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_id, revision=revision, use_fast=True)


def _resolve_index(model_id: str, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    tokenizer = _load_tokenizer(model_id)
    return default_index_path(
        model_id,
        tokenizer_fingerprint(tokenizer),
        resolved_tokenizer_revision(tokenizer),
    )


def main() -> int:
    configure_hf_cache()
    args = _parser().parse_args()
    if args.command == "gpu-info":
        import torch

        isolated = verify_torch_binding(torch)
        physical = discover_target_gpu()
        print(
            json.dumps(
                {
                    "name": isolated.name,
                    "uuid": isolated.uuid,
                    "physical_index": physical.index,
                    "memory_total_mib": physical.memory_total_mib,
                    "memory_free_mib": physical.memory_free_mib,
                    "torch_cuda": torch.version.cuda,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "build-index":
        tokenizer = _load_tokenizer(args.model, args.revision)
        builder = PinyinIndexBuilder(tokenizer, args.model, args.revision)
        path = builder.build(args.output)
        index = PinyinIndex(path)
        print(json.dumps({"path": str(path), "stats": index.stats()}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "coverage-check":
        from .common_chars import gb2312_level1_characters

        index = PinyinIndex(_resolve_index(args.model, args.index))
        expected = frozenset(gb2312_level1_characters())
        covered = index.covered_characters()
        missing = sorted(expected - covered)
        print(
            json.dumps(
                {
                    "expected": len(expected),
                    "covered": len(expected & covered),
                    "missing_count": len(missing),
                    "missing": missing[:100],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not missing else 1

    if args.command == "benchmark-backends":
        from .backend_benchmark import benchmark_backend_pair
        from .backends import FullLogitsSnapshotBackend, SparseProjectionBackend
        from .model import QwenBaseBackend
        from .unified import LatinPrefixConstraint

        runtime = QwenBaseBackend(args.model, precision=args.precision)
        latin = LatinPrefixConstraint.from_tokenizer(runtime.tokenizer)
        legal_token_ids = tuple(
            dict.fromkeys(
                completion.token_path[0]
                for completion in latin.completions
                if completion.token_path
            )
        )
        if not legal_token_ids:
            print("tokenizer exposes no legal Latin token candidates", file=sys.stderr)
            return 2
        allowed_sets = [
            legal_token_ids[: min(count, len(legal_token_ids))]
            for count in args.allowed_counts
            if count > 0
        ]
        if not allowed_sets:
            print("--allowed-counts must contain a positive value", file=sys.stderr)
            return 2
        report = benchmark_backend_pair(
            FullLogitsSnapshotBackend(runtime),
            SparseProjectionBackend(runtime),
            before=args.before,
            after=args.after,
            allowed_token_sets=allowed_sets,
            iterations=args.iterations,
        )
        print(
            json.dumps(
                {
                    "model": args.model,
                    "legal_latin_token_count": len(legal_token_ids),
                    "diagnostics": runtime.diagnostics(),
                    "comparison": report.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "replay":
        import time
        from dataclasses import replace

        from .model import QwenBaseBackend
        from .replay import ReplayObservation, load_replay_cases, run_replay
        from .service_factory import build_bilingual_engine

        index_path = _resolve_index(args.model, args.index)
        if not index_path.exists():
            print(
                f"index not found: {index_path}\n"
                f"run: neural-weasel build-index --model {args.model}",
                file=sys.stderr,
            )
            return 2
        runtime = QwenBaseBackend(args.model, precision=args.precision)
        engine = build_bilingual_engine(
            runtime=runtime,
            index=PinyinIndex(index_path),
            backend_kind=args.backend,
        )
        loaded_cases = load_replay_cases(args.fixture)
        cases = [
            replace(case, requested_epoch=index) for index, case in enumerate(loaded_cases, start=1)
        ]
        refresh_measurements = []

        def query(case):
            refresh_started = time.perf_counter()
            state = engine.update_context(case.context)
            refresh_measurements.append((time.perf_counter() - refresh_started) * 1000)
            query_started = time.perf_counter()
            candidates = engine.query(case.input, 5, context_epoch=state.epoch)
            query_latency = (time.perf_counter() - query_started) * 1000
            return ReplayObservation(
                candidates=candidates,
                snapshot_age_ms=max(
                    0.0,
                    (time.monotonic() - state.created_monotonic) * 1000,
                ),
                used_epoch=state.epoch,
                query_latency_ms=query_latency,
            )

        # The callback appends one measured refresh before run_replay validates
        # and summarizes the list, so seed only the contract check here.
        if not cases:
            print("replay fixture contains no cases", file=sys.stderr)
            return 2
        observations = []
        for case in cases:
            observations.append((case, query(case)))
        observation_by_id = {case.id: observation for case, observation in observations}
        report = run_replay(
            cases,
            lambda case: observation_by_id[case.id],
            model_refresh_measurements_ms=refresh_measurements,
        )
        print(
            json.dumps(
                {
                    "model": args.model,
                    "backend": args.backend,
                    "diagnostics": engine.diagnostics(),
                    "replay": report.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command in {"predict", "serve", "serve-http", "simulate", "benchmark"}:
        from .engine import NeuralPinyinEngine
        from .model import QwenBaseBackend

        index_path = _resolve_index(args.model, args.index)
        if not index_path.exists():
            print(
                f"index not found: {index_path}\n"
                f"run: neural-weasel build-index --model {args.model}",
                file=sys.stderr,
            )
            return 2
        runtime = QwenBaseBackend(args.model, precision=args.precision)

        if args.command in {"serve", "serve-http"}:
            from .service_factory import build_bilingual_engine

            engine = build_bilingual_engine(
                runtime=runtime,
                index=PinyinIndex(index_path),
                backend_kind=args.backend,
            )
            # Publish a context-free startup snapshot before accepting pipe
            # clients. Later context updates replace it asynchronously; a
            # backend initialization failure remains explicit and terminates
            # startup instead of silently changing backend.
            engine.update_context("", "")
            if args.command == "serve":
                from .pipe_server import NamedPipeServer

                NamedPipeServer(engine).serve_forever()
            else:
                from .http_server import serve_wisdom_http

                if not 1 <= args.port <= 65535:
                    print("--port must be between 1 and 65535", file=sys.stderr)
                    return 2
                serve_wisdom_http(engine, host=args.host, port=args.port)
            return 0

        backend = runtime
        engine = NeuralPinyinEngine(runtime, PinyinIndex(index_path))

        if args.command == "predict":
            snapshot = engine.update_context(args.before, args.after)
            candidates = engine.query(args.pinyin, args.limit)
            print(
                json.dumps(
                    {
                        "diagnostics": backend.diagnostics(),
                        "context_epoch": snapshot.epoch,
                        "context_latency_ms": round(snapshot.latency_ms, 3),
                        "candidates": [candidate.to_dict() for candidate in candidates],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "simulate":
            snapshot = engine.update_context(args.before, args.after)
            print(
                f"context epoch {snapshot.epoch}, forward {snapshot.latency_ms:.1f} ms; "
                "type full pinyin, blank line exits"
            )
            while True:
                try:
                    raw = input("pinyin> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not raw:
                    break
                candidates = engine.query(raw, 5)
                for number, candidate in enumerate(candidates, start=1):
                    score = "-" if candidate.score is None else f"{candidate.score:.3f}"
                    print(
                        f"{number}. {candidate.text} [{candidate.pinyin}] "
                        f"consume={candidate.consumed_keys} score={score}"
                    )
            return 0

        if args.command == "benchmark":
            from .benchmark import benchmark_queries

            snapshot = engine.update_context(args.before, args.after)
            summary = benchmark_queries(
                engine,
                args.pinyin,
                iterations=args.iterations,
            )
            print(
                json.dumps(
                    {
                        "diagnostics": backend.diagnostics(),
                        "context_latency_ms": round(snapshot.latency_ms, 3),
                        "query_latency": summary.to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
