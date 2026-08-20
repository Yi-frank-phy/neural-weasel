from __future__ import annotations

from neural_weasel.pipe_server import CaptureDiagnostics


def test_capture_diagnostics_buckets_deny_reasons() -> None:
    diagnostics = CaptureDiagnostics()

    diagnostics.record_allowed(partial=True)
    diagnostics.record_allowed(partial=False)
    diagnostics.record_denied(reason="sensitive_input_scope")
    diagnostics.record_denied(reason="policy_unavailable")
    diagnostics.record_denied(reason="policy_unavailable")
    diagnostics.record_denied(reason="capture_failed")

    snapshot = diagnostics.snapshot()
    assert snapshot == {
        "capture_allowed": 2,
        "capture_sensitive": 1,
        "capture_unavailable": 2,
        "capture_error": 1,
        "last_deny_reason": "capture_failed",
        "last_partial": False,
    }


def test_capture_diagnostics_defaults_are_empty() -> None:
    snapshot = CaptureDiagnostics().snapshot()

    assert snapshot == {
        "capture_allowed": 0,
        "capture_sensitive": 0,
        "capture_unavailable": 0,
        "capture_error": 0,
        "last_deny_reason": None,
        "last_partial": None,
    }
