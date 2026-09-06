from __future__ import annotations

import re
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


def test_background_readiness_uses_bounded_owner_thread_pulls() -> None:
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


def test_refresh_pending_signal_uses_a_non_printable_private_key() -> None:
    processor = _read(PROCESSOR)
    refresh_header = _read(REFRESH_HEADER)

    key_match = re.search(r"kNeuralRefreshKeycode\s*=\s*(0x[0-9A-Fa-f]+)", refresh_header)
    assert key_match is not None
    assert int(key_match.group(1), 16) > 0x7E

    refresh_start = processor.index("kNeuralRefreshKeycode")
    refresh_end = processor.index("if (IsShiftKey", refresh_start)
    refresh_block = processor[refresh_start:refresh_end]
    assert "if (SelectedIndex(context) != 0)" in refresh_block
    assert "kNeuralPresentationRefreshProperty" in refresh_block
    assert "kNeuralCandidatePendingProperty" in refresh_block
    assert "RefreshNonConfirmedComposition()" in refresh_block
    assert "kRejected" in refresh_block
    assert "kAccepted" in refresh_block
    assert "PushInput" not in refresh_block
