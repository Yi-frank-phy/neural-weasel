from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_translator_uses_unified_protocol_and_mode() -> None:
    """AT-UC-01/RT-06: native request and response use the v0.2 contract."""
    source = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")
    header = (ROOT / "native/rime/epoch_semantics.h").read_text(encoding="utf-8")

    assert '"query_candidates"' in source
    assert '"query_pinyin"' not in source
    assert '"input_mode"' in source
    assert '"constraint_kind"' in source
    assert "neural_input_mode" in source
    assert "requested_epoch == 0" in header
    assert "IsResponseEpochAcceptable" in source


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


def test_ci_compiles_native_plugin_and_tests_on_windows() -> None:
    """Release evidence requires a real MSVC compile, not source inspection."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "windows-vertical-slice" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "Configure VS 2022 MSVC" in workflow
    assert 'toolset: "14.3"' not in workflow
    assert "vsversion:" not in workflow
    assert "set BJAM_TOOLSET=msvc-14.3" in workflow
    assert "set PLATFORM_TOOLSET=v143" in workflow
    assert "NEURAL_WEASEL_BUILD_RIME_PLUGIN=ON" in workflow
    assert "NEURAL_WEASEL_BUILD_NATIVE_TESTS=ON" in workflow
    assert "boost-signals2:x64-windows" in workflow
    assert "boost-unordered:x64-windows" in workflow
    assert "ctest" in workflow
    assert "NeuralWeaselExperimentalTSF.dll" in (
        ROOT / "scripts/build-windows-bundle.ps1"
    ).read_text(encoding="utf-8")
    assert 'xmake-version: "3.0.9"' in workflow
    assert "build.bat msvc" in workflow
    assert "Boost.Build bootstrap did not produce b2.exe" in workflow
    expected_failure = (
        "Pinned Boost/Weasel native dependency build did not produce "
        "verified x64 outputs"
    )
    assert expected_failure in workflow
    assert "Verify pinned native dependency outputs" in workflow
    assert "external/weasel/lib64/rime.lib" in workflow
    assert "xmake build -y WeaselTSF" in workflow
    assert "xmake build -y WeaselServer" in workflow
    assert "neural-weasel-experimental-x64" in workflow
    assert "verify-windows-bundle.py" in workflow


def test_plugin_build_generates_librime_build_config_header() -> None:
    """AT-WIN-06: source-only librime checkouts receive their generated header."""
    cmake = (ROOT / "native/CMakeLists.txt").read_text(encoding="utf-8")

    assert "src/rime/build_config.h.in" in cmake
    assert "configure_file(" in cmake
    assert "rime_generated" in cmake
    assert "${RIME_ROOT}/include" in cmake


def test_context_capture_is_hooked_into_pinned_tsf_without_model_on_edit_thread() -> None:
    overlay = (ROOT / "scripts/prepare-weasel-overlay.ps1").read_text(encoding="utf-8")
    adapter = (ROOT / "native/tsf/weasel_context_adapter.cc").read_text(encoding="utf-8")
    bridge = (ROOT / "native/context/context_update_bridge.h").read_text(encoding="utf-8")

    assert "CaptureWeaselContext(pContext, _tfClientId)" in overlay
    assert "ClearWeaselContext" in overlay
    assert 'add_includedirs("$(projectdir)/librime/src")' in overlay
    assert "TF_ES_ASYNCDONTCARE | TF_ES_READ" in adapter
    assert "IS_PASSWORD" in adapter
    assert "IS_PRIVATE" in adapter
    assert "IS_NUMERIC_PASSWORD" in adapter
    assert "IS_NUMERIC_PIN" in adapter
    assert "IS_ALPHANUMERIC_PIN" in adapter
    assert "ContextUpdateBridge" in adapter
    assert "all pipe I/O" in bridge
