from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKPORT = ROOT / "scripts" / "apply-weasel-pr1462-composition.ps1"
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"


def _backport() -> str:
    return BACKPORT.read_text(encoding="utf-8")


def test_overlay_stacks_pr1462_composition_backport() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")

    assert "apply-weasel-pr1462-composition.ps1" in overlay
    assert "Pr1462Overlay" in overlay


def test_non_inline_placeholder_text_is_removed() -> None:
    backport = _backport()

    # This is the old CUAS workaround implicated by upstream #1579/#1907.
    assert 'pRangeComposition->SetText(ec, TF_ST_CORRECTION, L" ", 1);' in backport
    assert "pRangeComposition->Collapse(ec, TF_ANCHOR_END);" in backport
    assert "_pTextService->_UpdateCompositionWindow(_pContext);" in backport


def test_composition_termination_only_affects_current_composition() -> None:
    backport = _backport()

    assert "_IsCurrentComposition(pComposition)" in backport
    assert "if (_status.composing)" in backport
    assert "_FinalizeComposition();" in backport
    assert "BOOL _IsCurrentComposition(ITfComposition* pComposition);" in backport


def test_replacement_composition_can_keep_candidate_ui_alive() -> None:
    backport = _backport()

    assert "BOOL endUI" in backport
    assert "if (endUI)" in backport
    assert "_EndComposition(_pEditSessionContext, false, !_status.composing);" in backport
    assert "compositionEnded = true;" in backport
    assert "_status.composing && (compositionEnded || !_IsComposing())" in backport


def test_old_position_update_is_removed_from_update_composition() -> None:
    backport = _backport()

    assert "_async_edit = !!(hr == TF_S_ASYNC);" in backport
    # The replacement removes the immediate _UpdateCompositionWindow call;
    # positioning moves to the edit session after the new composition exists.
    assert "Position only after this new composition exists" in backport
