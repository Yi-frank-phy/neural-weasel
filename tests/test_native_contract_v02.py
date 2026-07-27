from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_translator_uses_unified_protocol_and_mode() -> None:
    """AT-UC-01/RT-06: native request and response use the v0.2 contract."""
    source = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")

    assert '"query_candidates"' in source
    assert '"query_pinyin"' not in source
    assert '"input_mode"' in source
    assert '"constraint_kind"' in source
    assert "neural_input_mode" in source


def test_native_build_includes_bilingual_processor_and_test() -> None:
    """AT-KS-01..06: compiled integration includes the tested processor."""
    cmake = (ROOT / "native/CMakeLists.txt").read_text(encoding="utf-8")

    assert "rime/bilingual_key_semantics.cc" in cmake
    assert "rime/bilingual_key_processor.cc" in cmake
    assert "neural_weasel_bilingual_key_semantics_test" in cmake


def test_processor_has_no_model_or_pipe_dependency() -> None:
    """AT-RT-01: acceptance keys are pure local state transitions."""
    source = (ROOT / "native/rime/bilingual_key_processor.cc").read_text(encoding="utf-8")

    assert "NamedPipeClient" not in source
    assert "TryQuery" not in source
    assert "model" not in source.casefold()
