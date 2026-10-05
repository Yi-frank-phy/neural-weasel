from __future__ import annotations

from pathlib import Path

from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.internal_cli import _parser
from neural_weasel.production_pipe import ProductionNamedPipeServer

ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    context_epoch = 4

    def runtime_performance_diagnostics(self) -> dict[str, object]:
        return {
            "max_before_tokens": 3072,
            "n_ctx": 4096,
            "n_batch": 512,
            "last_refresh_context_tokens": 640,
            "last_refresh_evaluated_tokens": 12,
            "last_refresh_latency_ms": 18.75,
            "last_refresh_queue_wait_ms": 3.25,
            "last_refresh_compute_ms": 15.5,
            "last_continuation_queue_wait_ms": 4.0,
            "last_continuation_elapsed_ms": 52.0,
            "last_continuation_requested_branches": 32,
            "last_continuation_completed_branches": 28,
            "last_continuation_returned_tokens": 112,
            "last_continuation_cache_preserved": True,
            "last_continuation_outcome": "preempted",
            "last_candidate_page_index": 2,
            "last_candidate_count": 9,
            "last_candidate_search_depth": 4,
            "last_candidate_length_bucket": 1,
            "last_candidate_search_elapsed_ms": 37.5,
            "candidate_page_timeout_count": 3,
            "page_zero_lexical_attempt_count": 2,
            "last_page_zero_lexical_result_count": 34,
            "last_page_zero_lexical_freezable_count": 35,
            "last_page_zero_lexical_elapsed_ms": 2.25,
            "last_page_zero_lexical_budget_ms": 12.0,
            "last_candidate_target_count": 35,
            "last_candidate_generated_count": 35,
            "last_candidate_pending_count": 7,
            "last_candidate_freezable_count": 7,
            "last_candidate_frontier_count": 16,
            "last_candidate_frozen_count": 28,
            "last_candidate_published_count": 14,
            "last_candidate_background_elapsed_ms": 2460.0,
            "last_candidate_background_timed_out": True,
            "service_gc_gen2_count": 4,
            "service_gc_gen2_start_ns": 1_000,
            "service_gc_gen2_end_ns": 2_000,
            "service_gc_gen2_elapsed_ms": 0.001,
            "service_gc_gen2_max_ms": 0.002,
            "secret": "PRIVATE-CONTEXT-MUST-NOT-LEAK",
        }


class FakeRuntime:
    def performance_diagnostics(self) -> dict[str, object]:
        return {"last_refresh_evaluated_tokens": 7}


class FakeBackend:
    runtime = FakeRuntime()

    def load(self) -> None:
        pass

    def update_context(self, before: str, after: str = ""):
        raise AssertionError("diagnostic delegation must not refresh the model")

    def latest_state(self):
        return None

    def score_allowed_tokens(self, state, token_ids):
        raise AssertionError("diagnostic delegation must not score tokens")

    def diagnostics(self) -> dict[str, object]:
        raise AssertionError("lightweight diagnostics must not call full diagnostics")

    def invalidate_private_state(self) -> None:
        pass


def test_engine_delegates_only_to_cached_runtime_performance_diagnostics() -> None:
    engine = BilingualImeEngine(backend=FakeBackend())

    diagnostics = engine.runtime_performance_diagnostics()

    assert diagnostics["last_refresh_evaluated_tokens"] == 7
    assert diagnostics["last_candidate_page_index"] is None
    assert diagnostics["last_candidate_count"] is None
    assert diagnostics["last_candidate_search_depth"] is None
    assert diagnostics["last_candidate_length_bucket"] is None
    assert diagnostics["last_candidate_search_elapsed_ms"] is None
    assert diagnostics["candidate_page_timeout_count"] == 0


def test_metadata_only_diagnostics_filters_engine_output() -> None:
    server = ProductionNamedPipeServer(FakeEngine(), r"\\.\pipe\NeuralWeasel-test-diagnostics")
    server._requested_context_epoch = 5

    response = server.handle_message({"type": "diagnostics", "request_id": "d1"})

    assert response == {
        "type": "diagnostics",
        "ok": True,
        "context_epoch": 4,
        "requested_context_epoch": 5,
        "context_updating": True,
        "max_after_tokens": None,
        "continuation_reserve_tokens": None,
        "context_mode": None,
        "max_before_tokens": 3072,
        "n_ctx": 4096,
        "n_batch": 512,
        "last_refresh_context_tokens": 640,
        "last_refresh_evaluated_tokens": 12,
        "last_candidate_page_index": 2,
        "last_candidate_count": 9,
        "last_candidate_search_depth": 4,
        "last_candidate_length_bucket": 1,
        "candidate_page_timeout_count": 3,
        "page_zero_lexical_attempt_count": 2,
        "last_page_zero_lexical_result_count": 34,
        "last_page_zero_lexical_freezable_count": 35,
        "last_continuation_requested_branches": 32,
        "last_continuation_completed_branches": 28,
        "last_continuation_returned_tokens": 112,
        "last_candidate_target_count": 35,
        "last_candidate_generated_count": 35,
        "last_candidate_pending_count": 7,
        "last_candidate_freezable_count": 7,
        "last_candidate_frontier_count": 16,
        "last_candidate_frozen_count": 28,
        "last_candidate_published_count": 14,
        **{
            f"service_gc_gen{generation}_{metric}": None
            for generation in (0, 1)
            for metric in ("count", "start_ns", "end_ns", "elapsed_ms", "max_ms")
        },
        "service_gc_gen2_count": 4,
        "service_gc_gen2_start_ns": 1_000,
        "service_gc_gen2_end_ns": 2_000,
        "service_gc_gen2_elapsed_ms": 0.001,
        "service_gc_gen2_max_ms": 0.002,
        "last_refresh_latency_ms": 18.75,
        "last_refresh_queue_wait_ms": 3.25,
        "last_refresh_compute_ms": 15.5,
        "last_continuation_queue_wait_ms": 4.0,
        "last_continuation_elapsed_ms": 52.0,
        "last_candidate_search_elapsed_ms": 37.5,
        "last_candidate_background_elapsed_ms": 2460.0,
        "last_page_zero_lexical_elapsed_ms": 2.25,
        "last_page_zero_lexical_budget_ms": 12.0,
        "last_continuation_cache_preserved": True,
        "last_candidate_background_timed_out": True,
        "last_continuation_outcome": "preempted",
        "request_id": "d1",
    }
    assert "PRIVATE-CONTEXT-MUST-NOT-LEAK" not in repr(response)


def test_diagnostics_rejects_non_numeric_allowed_metrics_without_echoing_them() -> None:
    private_text = "PRIVATE-METRIC-MUST-NOT-LEAK"

    class InvalidMetricEngine(FakeEngine):
        def runtime_performance_diagnostics(self) -> dict[str, object]:
            diagnostics = super().runtime_performance_diagnostics()
            diagnostics["last_refresh_context_tokens"] = private_text
            return diagnostics

    server = ProductionNamedPipeServer(
        InvalidMetricEngine(),
        r"\\.\pipe\NeuralWeasel-test-invalid-diagnostics",
    )
    response = server.handle_message({"type": "diagnostics"})

    assert response["ok"] is False
    assert response["error"]["code"] == "internal_error"
    assert private_text not in repr(response)


def test_runtime_diagnostics_request_accepts_no_context_fields() -> None:
    server = ProductionNamedPipeServer(FakeEngine(), r"\\.\pipe\NeuralWeasel-test-fields")

    response = server.handle_message(
        {
            "type": "diagnostics",
            "before": "PRIVATE-CONTEXT-MUST-NOT-LEAK",
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"
    assert "PRIVATE-CONTEXT-MUST-NOT-LEAK" not in repr(response)


def test_runtime_diagnostics_cli_has_a_bounded_pipe_timeout() -> None:
    args = _parser().parse_args(["runtime-diagnostics", "--timeout-ms", "250"])

    assert args.command == "runtime-diagnostics"
    assert args.timeout_ms == 250


def test_production_serve_uses_the_diagnostic_pipe_server() -> None:
    cli = (ROOT / "src/neural_weasel/internal_cli.py").read_text(encoding="utf-8")

    assert "from .production_pipe import ProductionNamedPipeServer" in cli
    assert "ProductionNamedPipeServer(engine).serve_forever()" in cli
