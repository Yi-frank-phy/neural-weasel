from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSLATOR = ROOT / "native" / "rime" / "ai_translator.cc"
PROCESSOR = ROOT / "native" / "rime" / "bilingual_key_processor.cc"
REFRESH_HEADER = ROOT / "native" / "rime" / "neural_refresh_key.h"
OVERLAY = ROOT / "scripts" / "prepare-weasel-overlay.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_presentation_refresh_does_not_create_an_input_revision() -> None:
    translator = _read(TRANSLATOR)
    refresh_header = _read(REFRESH_HEADER)

    assert "kNeuralPresentationRefreshProperty" in refresh_header
    assert "kNeuralCandidatePendingProperty" in refresh_header
    assert "kNeuralForceRefreshProperty" not in refresh_header

    start = translator.index('if (context->get_property(kNeuralPresentationRefreshProperty) == "1")')
    end = translator.index("const std::string language_mode", start)
    refresh_block = translator[start:end]
    assert "frozen_pages_.clear();" in refresh_block
    assert "candidate_set_id_.clear();" in refresh_block
    assert "force_new_revision_ = true" not in refresh_block
    assert "++composition_revision_" not in refresh_block


def test_background_readiness_drives_only_bounded_owner_thread_pulls() -> None:
    overlay = _read(OVERLAY)

    assert "constexpr UINT kNeuralRefreshDelayMs = 850;" in overlay
    assert "constexpr UINT kNeuralRefreshRetryDelayMs = 250;" in overlay
    assert "constexpr unsigned int kNeuralRefreshMaxAttempts = 16;" in overlay
    assert "const bool presentation_ready = m_client.ProcessKeyEvent(refresh);" in overlay
    assert "if (presentation_ready ||" in overlay
    assert "kNeuralRefreshRetryDelayMs" in overlay
    assert "GetCurrentThreadId() != _neuralRefreshOwnerThreadId" in overlay
    assert "std::this_thread::sleep_for" not in overlay


def test_backspace_and_apostrophe_schedule_the_same_identity_bound_refresh() -> None:
    overlay = _read(OVERLAY)

    assert "wParam == VK_BACK" in overlay
    assert "wParam == VK_OEM_7" in overlay
    assert "(wParam >= 'A' && wParam <= 'Z')" in overlay


def test_refresh_never_reorders_an_explicit_user_selection() -> None:
    processor = _read(PROCESSOR)

    assert "if (SelectedIndex(context) != 0)" in processor
    assert "kNeuralPresentationRefreshProperty" in processor
    assert "kNeuralCandidatePendingProperty" in processor
    assert "? ::rime::kRejected" in processor
