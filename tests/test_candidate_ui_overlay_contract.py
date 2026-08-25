from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"


def _overlay() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def test_candidate_ui_overlay_does_not_patch_panel_visibility_semantics() -> None:
    overlay = _overlay()

    assert "WeaselUI/WeaselUI.cpp" not in overlay
    assert "panel.IsWindowVisible()" not in overlay


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
    assert "FAILED(pUIElementMgr->BeginUIElement" in overlay
    assert "_uiStarted = true;" in overlay
    assert "_uiStarted = false;" in overlay
