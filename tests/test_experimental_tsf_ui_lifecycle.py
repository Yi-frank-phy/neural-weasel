from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"
TRACE_HEADER = ROOT / "native" / "tsf" / "ui_lifecycle_trace.h"


def _overlay_text() -> str:
    return OVERLAY.read_text(encoding="utf-8-sig")


def test_experimental_tsf_never_launches_or_waits_for_server() -> None:
    overlay = _overlay_text()

    assert "ShellExecuteW" not in overlay
    assert "std::this_thread::sleep_for" not in overlay
    assert "std::thread th" not in overlay
    assert "GetTickCount64" in overlay
    assert "_nextReconnectTick" in overlay


def test_experimental_tsf_preserves_upstream_candidate_ui_contract() -> None:
    overlay = _overlay_text()

    assert "$CustomCandidateUi" not in overlay
    assert "CandidateList.cpp custom candidate UI" not in overlay


def test_candidate_window_creation_retries_without_an_invalid_owner() -> None:
    overlay = _overlay_text()

    assert "$WeaselUiSource" in overlay
    assert "HWND created = pimpl_->panel.Create(" in overlay
    assert "if (created == nullptr && parent != nullptr)" in overlay
    assert "created = pimpl_->panel.Create(" in overlay
    assert "return created != nullptr;" in overlay


def test_overlay_rewrites_reconnect_function_once() -> None:
    overlay = _overlay_text()

    assert "WeaselTSF.cpp bounded reconnect" in overlay
    assert "WeaselTSF.cpp bounded reconnect rewrite count" in overlay


def test_overlay_adds_metadata_only_candidate_ui_lifecycle_trace() -> None:
    overlay = _overlay_text()
    trace_header = TRACE_HEADER.read_text(encoding="utf-8")

    assert "native/tsf/ui_lifecycle_trace.h" in overlay
    assert "WeaselTSF/UiLifecycleTrace.h" in overlay
    assert "event=candidate-ui-start result=registered" in overlay
    assert "event=candidate-ui-update candidates=" in overlay
    assert "event=candidate-window-create result=" in overlay
    assert "event=candidate-window-visibility requested=" in overlay
    assert "Never pass" in trace_header
    assert "candidate text" in trace_header
    assert "surrounding text" in trace_header
    assert "window titles" in trace_header
