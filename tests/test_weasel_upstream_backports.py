from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"
TRACE_HEADER = ROOT / "native" / "tsf" / "candidate_ui_diagnostics.h"
STYLE_TRACE_HEADER = ROOT / "native" / "tsf" / "candidate_style_diagnostics.h"
DIAGNOSE = ROOT / "scripts" / "diagnose.ps1"
COMPOSITION_DIAGNOSTICS = ROOT / "scripts" / "apply-composition-diagnostics.ps1"
STYLE_DIAGNOSTICS = ROOT / "scripts" / "apply-candidate-style-diagnostics.ps1"


def _overlay() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def test_candidate_ui_visibility_uses_real_hwnd_state() -> None:
    overlay = _overlay()

    assert "WeaselUI/WeaselUI.cpp" in overlay
    assert "panel.IsWindowVisible()" in overlay
    assert "bool IsShown() const { return shown; }" in overlay


def test_candidate_ui_create_reports_real_hwnd_creation() -> None:
    overlay = _overlay()

    assert "const HWND created = pimpl_->panel.Create(" in overlay
    assert "return created != NULL && pimpl_->panel.IsWindow();" in overlay
    assert "bool CCandidateList::_MakeUIWindow()" in overlay
    assert "_uiCreateSuccess = _MakeUIWindow();" in overlay


def test_candidate_ui_trace_is_connected_to_real_native_hwnd() -> None:
    overlay = _overlay()

    assert "HWND NativeWindowForDiagnostics() const;" in overlay
    assert "HWND UI::NativeWindowForDiagnostics() const" in overlay
    assert "pimpl_->panel.m_hWnd" in overlay
    assert "_ui->NativeWindowForDiagnostics()" in overlay


def test_candidate_ui_element_updates_are_not_gated_by_pbshow() -> None:
    overlay = _overlay()

    assert "WeaselTSF/CandidateList.cpp" in overlay
    assert "if (_pbShow == FALSE)" in overlay
    assert "  _UpdateUIElement();" in overlay


def test_candidate_ui_lifecycle_is_idempotent_and_checks_begin_result() -> None:
    overlay = _overlay()

    assert "WeaselTSF/CandidateList.h" in overlay
    assert "bool _uiStarted = false;" in overlay
    assert "if (_uiStarted)" in overlay
    assert "_beginUiHr = pUIElementMgr->BeginUIElement" in overlay
    assert "if (FAILED(_beginUiHr))" in overlay
    assert "_uiStarted = true;" in overlay
    assert "_uiStarted = false;" in overlay


def test_candidate_ui_trace_covers_negotiation_create_show_and_stale_start() -> None:
    overlay = _overlay()
    trace = TRACE_HEADER.read_text(encoding="utf-8")
    diagnose = DIAGNOSE.read_text(encoding="utf-8")

    for marker in (
        "begin-ui-failed",
        "start-ui",
        "update-ui-visibility-change",
        "destroy",
        "destroy-all",
        "start-suppressed-already-started",
        "end-ui",
    ):
        assert marker in overlay

    for field in (
        "begin_hr=",
        "pb_show=",
        "ui_started=",
        "create_attempted=",
        "create_success=",
        "shown=",
        "hwnd=",
        "is_window=",
        "hwnd_pid=",
        "hwnd_tid=",
        "has_rect=",
        "rect=",
        "style=",
        "ex_style=",
        "owner=",
        "root_owner=",
        "z_prev=",
        "dpi=",
        "layered_known=",
        "layered_alpha=",
        "dwm_cloaked_known=",
        "dwm_cloaked=",
        "monitor=",
        "monitor_known=",
        "monitor_rect=",
        "work_rect=",
    ):
        assert field in trace

    assert "candidate-ui-events.log" in trace
    assert "candidate_ui_trace_tail" in diagnose


def test_runtime_candidate_style_alpha_trace_is_applied_and_text_free() -> None:
    composition = COMPOSITION_DIAGNOSTICS.read_text(encoding="utf-8")
    style_patch = STYLE_DIAGNOSTICS.read_text(encoding="utf-8")
    trace = STYLE_TRACE_HEADER.read_text(encoding="utf-8")

    assert "apply-candidate-style-diagnostics.ps1" in composition
    assert "candidate_style_diagnostics.h" in style_patch
    assert "WriteCandidateStyleDiagnostic" in style_patch
    for marker in (
        '"start-ui-source-style"',
        '"start-ui-native-style"',
        '"update-ui-source-style"',
        '"update-ui-native-style"',
    ):
        assert marker in style_patch
    for field in (
        "text_alpha=",
        "back_alpha=",
        "candidate_text_alpha=",
        "candidate_back_alpha=",
        "border_alpha=",
        "hilited_candidate_text_alpha=",
        "hilited_candidate_back_alpha=",
    ):
        assert field in trace

    for forbidden_api in (
        "GetWindowText",
        "GetClipboardData",
        "ITfRange",
        "GetSelection",
        "GetText",
    ):
        assert forbidden_api not in trace


def test_composition_lifecycle_trace_is_applied_and_text_free() -> None:
    overlay = _overlay()
    trace = TRACE_HEADER.read_text(encoding="utf-8")
    composition = COMPOSITION_DIAGNOSTICS.read_text(encoding="utf-8")

    assert "apply-composition-diagnostics.ps1" in overlay
    assert "WriteCompositionDiagnostic" in trace
    for marker in (
        '"start-request"',
        '"terminated"',
        '"abort"',
        '"finalize"',
        '"set"',
    ):
        assert marker in composition
    for field in (
        "tsf_composition_active=",
        "rime_composing=",
        "current_composition=",
        "callback_composition=",
        "same_composition=",
    ):
        assert field in trace


def test_candidate_ui_trace_does_not_read_editor_or_window_text() -> None:
    trace = TRACE_HEADER.read_text(encoding="utf-8")

    for forbidden_api in (
        "GetWindowText",
        "GetClipboardData",
        "ITfRange",
        "GetSelection",
        "GetText",
    ):
        assert forbidden_api not in trace


def test_diagnose_reports_deployed_weasel_config_precedence() -> None:
    diagnose = DIAGNOSE.read_text(encoding="utf-8")

    for marker in (
        "RimeUser\\weasel.yaml",
        "RimeUser\\build\\weasel.yaml",
        "data\\weasel.yaml",
        "rime_runtime_weasel_config_present",
        "rime_deployed_weasel_config_present",
        "rime_shared_weasel_config_present",
        "rime_deployed_weasel_precedes_shared",
    ):
        assert marker in diagnose
