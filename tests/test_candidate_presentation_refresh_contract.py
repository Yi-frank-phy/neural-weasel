from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSLATOR = ROOT / "native" / "rime" / "ai_translator.cc"
PROCESSOR = ROOT / "native" / "rime" / "bilingual_key_processor.cc"
REFRESH_HEADER = ROOT / "native" / "rime" / "neural_refresh_key.h"
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"
OVERLAY_CORE = ROOT / "scripts" / "prepare-weasel-overlay-core.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _overlay_text() -> str:
    return _read(OVERLAY_CORE) + "\n" + _read(OVERLAY)


def test_presentation_refresh_does_not_create_an_input_revision() -> None:
    translator = _read(TRANSLATOR)
    refresh_header = _read(REFRESH_HEADER)

    assert "kNeuralPresentationRefreshProperty" in refresh_header
    assert "kNeuralCandidatePendingProperty" in refresh_header
    assert "kNeuralForceRefreshProperty" not in refresh_header

    refresh_condition = 'if (context->get_property(kNeuralPresentationRefreshProperty) == "1")'
    start = translator.index(refresh_condition)
    end = translator.index("const std::string language_mode", start)
    refresh_block = translator[start:end]
    assert "presentation_refresh = true;" in refresh_block
    assert "frozen_pages_.clear();" not in refresh_block
    assert "candidate_set_id_.clear();" not in refresh_block
    assert "force_new_revision_ = true" not in refresh_block
    assert "++composition_revision_" not in refresh_block
    assert 'request["presentation_refresh"] = true;' in translator


def test_background_readiness_uses_status_metadata_and_bounded_owner_thread_pulls() -> None:
    overlay = _overlay_text()

    assert "constexpr UINT kNeuralRefreshDelayMs = 850;" in overlay
    assert "constexpr UINT kNeuralRefreshRetryDelayMs = 250;" in overlay
    assert "constexpr unsigned int kNeuralRefreshMaxAttempts = 16;" in overlay
    assert "if (!m_client.ProcessKeyEvent(refresh))" in overlay
    assert "const bool presentation_ready = !_status.neural_candidate_pending;" in overlay
    assert "status.neural_candidate_pending=" in overlay
    assert 'get_property(session_id, "neural_candidate_pending"' in overlay
    assert "if (presentation_ready ||" in overlay
    assert "kNeuralRefreshRetryDelayMs" in overlay
    assert "GetCurrentThreadId() != _neuralRefreshOwnerThreadId" in overlay
    assert "std::this_thread::sleep_for" not in overlay


def test_backspace_and_apostrophe_schedule_the_same_identity_bound_refresh() -> None:
    overlay = _overlay_text()

    assert "wParam == VK_BACK" in overlay
    assert "wParam == VK_OEM_7" in overlay
    assert "(wParam >= 'A' && wParam <= 'Z')" in overlay


def test_refresh_never_reorders_an_explicit_user_selection_or_enters_commit_history() -> None:
    processor = _read(PROCESSOR)

    refresh_start = processor.index("kNeuralRefreshKeycode")
    refresh_end = processor.index("if (IsShiftKey", refresh_start)
    refresh_block = processor[refresh_start:refresh_end]
    assert "if (SelectedIndex(context) != 0)" in refresh_block
    assert "kNeuralPresentationRefreshProperty" in refresh_block
    assert "RefreshNonConfirmedComposition()" in refresh_block
    assert "kRejected" not in refresh_block
    assert "return ::rime::kAccepted;" in refresh_block
    assert "PushInput" not in refresh_block
