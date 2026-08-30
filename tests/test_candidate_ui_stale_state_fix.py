from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "scripts" / "apply-candidate-ui-stale-state-fix.ps1"
COMPOSITION_DIAGNOSTICS = ROOT / "scripts" / "apply-composition-diagnostics.ps1"


def test_destroy_paths_end_started_ui_session_before_disposing_hwnd() -> None:
    fix = FIX.read_text(encoding="utf-8")

    assert "void CCandidateList::Destroy()" in fix
    assert "void CCandidateList::DestroyAll()" in fix
    assert fix.count("if (_uiStarted) {") >= 2
    assert fix.count("EndUI();") >= 2
    assert "_uiStarted = false;" in fix


def test_end_ui_cleanup_cannot_return_before_local_state_reset() -> None:
    fix = FIX.read_text(encoding="utf-8")

    assert "if (SUCCEEDED(hr) && emgr != NULL)" in fix
    assert '"end-ui-element-manager-unavailable"' in fix
    assert "if (FAILED(hr))\n      return;" not in fix
    assert fix.index("if (SUCCEEDED(hr) && emgr != NULL)") < fix.index(
        "_uiStarted = false;"
    )


def test_stale_ui_fix_is_in_the_applied_overlay_chain() -> None:
    composition = COMPOSITION_DIAGNOSTICS.read_text(encoding="utf-8")

    assert "apply-candidate-ui-stale-state-fix.ps1" in composition
    assert "& $StaleUiFix -WeaselRoot $ResolvedWeaselRoot" in composition


@dataclass
class CandidateUiState:
    ui_started: bool = False
    hwnd_alive: bool = False
    composition_active: bool = False


def _start_ui(state: CandidateUiState) -> bool:
    if state.ui_started:
        return False
    state.ui_started = True
    state.hwnd_alive = True
    return True


def _end_ui(state: CandidateUiState) -> None:
    state.ui_started = False
    state.hwnd_alive = False


def _destroy_fixed(state: CandidateUiState) -> None:
    if state.ui_started:
        _end_ui(state)
    else:
        state.hwnd_alive = False


def test_async_start_focus_loss_destroy_allows_next_start() -> None:
    """Model the exact reachable race the partial backport previously broke."""
    state = CandidateUiState()

    assert _start_ui(state)
    assert state.ui_started
    assert state.hwnd_alive

    # StartComposition was requested with TF_ES_ASYNCDONTCARE but has not run.
    assert not state.composition_active

    # Focus loss calls _AbortComposition(). With no TSF composition it skips
    # _EndComposition(), so Destroy() is the only chance to repair UI state.
    _destroy_fixed(state)
    assert not state.ui_started
    assert not state.hwnd_alive

    # A later composition must be able to negotiate and create its HWND again.
    assert _start_ui(state)
    assert state.ui_started
    assert state.hwnd_alive
