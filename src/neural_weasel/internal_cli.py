from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gguf_artifact import PRODUCTION_GGUF, resolve_quantization_artifact
from .gpu import discover_target_gpu, verify_expected_nvidia_binding
from .paths import configure_hf_cache

QUANT_SELECTOR_CHOICES = ("Q4_K_M", "Q8_0")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="neural-weasel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("gpu-info", help="verify strict RTX 4060 CUDA isolation")
    subparsers.add_parser("acquire-model", help="download and hash the pinned 4B Q8_0 GGUF")
    subparsers.add_parser("gguf-smoke", help="prove Q8_0 llama.cpp CUDA full offload locally")

    build = subparsers.add_parser("build-index", help="build the production GGUF-vocabulary index")
    build.add_argument("--output", type=Path)

    predict = subparsers.add_parser("predict", help="run one constrained 4B Q8_0 query")
    predict.add_argument("--index", type=Path)
    predict.add_argument("--before", required=True)
    predict.add_argument("--after", default="")
    predict.add_argument("--pinyin", required=True)
    predict.add_argument("--limit", type=int, default=5)

    serve = subparsers.add_parser("serve", help="start the production Windows named-pipe server")
    serve.add_argument("--index", type=Path)
    serve.add_argument("--quantization", choices=QUANT_SELECTOR_CHOICES, default="Q8_0")
    serve.add_argument("--gguf-path", type=Path)

    serve_http = subparsers.add_parser(
        "serve-http",
        help="start the loopback Wisdom Weasel HTTP compatibility server",
    )
    serve_http.add_argument("--index", type=Path)
    serve_http.add_argument("--quantization", choices=QUANT_SELECTOR_CHOICES, default="Q8_0")
    serve_http.add_argument("--gguf-path", type=Path)
    serve_http.add_argument("--host", default="127.0.0.1")
    serve_http.add_argument("--port", type=int, default=8000)

    simulate = subparsers.add_parser(
        "simulate",
        help="interactive candidate simulator using the production GGUF runtime",
    )
    simulate.add_argument("--index", type=Path)
    simulate.add_argument("--before", required=True)
    simulate.add_argument("--after", default="")

    benchmark = subparsers.add_parser(
        "benchmark",
        help="measure cached pinyin-query latency after one GGUF model forward",
    )
    benchmark.add_argument("--index", type=Path)
    benchmark.add_argument("--before", required=True)
    benchmark.add_argument("--after", default="")
    benchmark.add_argument("--pinyin", required=True)
    benchmark.add_argument("--iterations", type=int, default=1000)

    coverage = subparsers.add_parser(
        "coverage-check",
        help="verify all 3,755 GB2312 level-1 characters are inputtable",
    )
    coverage.add_argument("--index", type=Path)

    replay = subparsers.add_parser(
        "replay",
        help="run the minimal bilingual replay fixture on the production GGUF runtime",
    )
    replay.add_argument("--index", type=Path)
    replay.add_argument("--fixture", type=Path, required=True)

    # Retain the old Torch comparison only as an explicit development tool.
    compare = subparsers.add_parser(
        "benchmark-backends",
        help="experimental legacy Torch full-logits vs sparse projection comparison",
    )
    compare.add_argument("--model", default="Qwen/Qwen3.5-0.8B-Base")
    compare.add_argument("--precision", choices=("bf16", "int8", "nf4"), default="bf16")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", default="")
    compare.add_argument("--allowed-counts", type=int, nargs="+", default=[32, 128, 512])
    compare.add_argument("--iterations", type=int, default=20)

    return parser


def _build_production(
    index_path: Path | None,
    quantization: str = "Q8_0",
    gguf_path: Path | None = None,
):
    from .production import build_production_runtime

    return build_production_runtime(
        index_path,
        artifact=resolve_quantization_artifact(quantization),
        gguf_path=gguf_path,
    )


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    configure_hf_cache()
    args = _parser().parse_args()

    if args.command == "gpu-info":
        isolated = verify_expected_nvidia_binding()
        physical = discover_target_gpu()
        _print_json(
            {
                "name": isolated.name,
                "uuid": isolated.uuid,
                "physical_index": physical.index,
                "memory_total_mib": physical.memory_total_mib,
                "memory_free_mib": physical.memory_free_mib,
                "required_runtime": "llama.cpp CUDA",
                "required_model": PRODUCTION_GGUF.model_id,
                "required_quantization": PRODUCTION_GGUF.quantization,
            }
        )
        return 0

    if args.command == "acquire-model":
        from .acquire_model import ensure_production_gguf

        acquired = ensure_production_gguf()
        _print_json(
            {
                "model": acquired.artifact.model_id,
                "repo": acquired.artifact.repo_id,
                "revision": acquired.artifact.revision,
                "filename": acquired.artifact.filename,
                "quantization": acquired.artifact.quantization,
                "path": str(acquired.path),
                "sha256": acquired.sha256,
            }
        )
        return 0

    if args.command == "gguf-smoke":
        from .gguf_smoke import run_gguf_smoke

        _print_json(run_gguf_smoke())
        return 0

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
        _print_json(
            {
                "experimental_legacy_runtime": True,
                "model": args.model,
                "legal_latin_token_count": len(legal_token_ids),
                "diagnostics": runtime.diagnostics(),
                "comparison": report.to_dict(),
            }
        )
        return 0

    if args.command == "build-index":
        bundle = _build_production(args.output)
        _print_json(
            {
                "path": str(bundle.index_path),
                "stats": bundle.index.stats(),
                "runtime": bundle.runtime.diagnostics(),
            }
        )
        return 0

    if args.command == "coverage-check":
        from .common_chars import gb2312_level1_characters

        bundle = _build_production(args.index)
        expected = frozenset(gb2312_level1_characters())
        covered = bundle.index.covered_characters()
        missing = sorted(expected - covered)
        _print_json(
            {
                "expected": len(expected),
                "covered": len(expected & covered),
                "missing_count": len(missing),
                "missing": missing[:100],
            }
        )
        return 0 if not missing else 1

    if args.command == "replay":
        import time
        from dataclasses import replace

        from .replay import ReplayObservation, load_replay_cases, run_replay
        from .service_factory import build_bilingual_engine

        bundle = _build_production(args.index)
        engine = build_bilingual_engine(
            runtime=bundle.runtime,
            index=bundle.index,
            backend_kind="full",
        )
        loaded_cases = load_replay_cases(args.fixture)
        cases = [
            replace(case, requested_epoch=index) for index, case in enumerate(loaded_cases, start=1)
        ]
        if not cases:
            print("replay fixture contains no cases", file=sys.stderr)
            return 2
        refresh_measurements: list[float] = []
        observations = []
        for case in cases:
            refresh_started = time.perf_counter()
            state = engine.update_context(case.context)
            refresh_measurements.append((time.perf_counter() - refresh_started) * 1000)
            query_started = time.perf_counter()
            candidates = engine.query(case.input, 5, context_epoch=state.epoch)
            observations.append(
                (
                    case,
                    ReplayObservation(
                        candidates=candidates,
                        snapshot_age_ms=max(
                            0.0,
                            (time.monotonic() - state.created_monotonic) * 1000,
                        ),
                        used_epoch=state.epoch,
                        query_latency_ms=(time.perf_counter() - query_started) * 1000,
                    ),
                )
            )
        observation_by_id = {case.id: observation for case, observation in observations}
        report = run_replay(
            cases,
            lambda case: observation_by_id[case.id],
            model_refresh_measurements_ms=refresh_measurements,
        )
        _print_json(
            {
                "model": PRODUCTION_GGUF.model_id,
                "backend": "full",
                "diagnostics": engine.diagnostics(),
                "replay": report.to_dict(),
            }
        )
        return 0

    if args.command in {"predict", "serve", "serve-http", "simulate", "benchmark"}:
        from .engine import NeuralPinyinEngine

        bundle = _build_production(
            args.index,
            getattr(args, "quantization", "Q8_0"),
            getattr(args, "gguf_path", None),
        )
        runtime = bundle.runtime

        if args.command in {"serve", "serve-http"}:
            from .service_factory import build_bilingual_engine

            engine = build_bilingual_engine(
                runtime=runtime,
                index=bundle.index,
                backend_kind="full",
            )
            # Startup owns the initial model forward. Keypress queries only read
            # immutable published snapshots while newer context is refreshed in
            # the background by the service engine.
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

        engine = NeuralPinyinEngine(runtime, bundle.index)

        if args.command == "predict":
            snapshot = engine.update_context(args.before, args.after)
            candidates = engine.query(args.pinyin, args.limit)
            _print_json(
                {
                    "diagnostics": runtime.diagnostics(),
                    "context_epoch": snapshot.epoch,
                    "context_latency_ms": round(snapshot.latency_ms, 3),
                    "candidates": [candidate.to_dict() for candidate in candidates],
                }
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
            _print_json(
                {
                    "diagnostics": runtime.diagnostics(),
                    "context_latency_ms": round(snapshot.latency_ms, 3),
                    "query_latency": summary.to_dict(),
                }
            )
            return 0

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
