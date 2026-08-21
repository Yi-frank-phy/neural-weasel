from __future__ import annotations

from pathlib import Path

from neural_weasel.profile_manifest import (
    EXPERIMENTAL_CLSID,
    EXPERIMENTAL_PROFILE_GUID,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_bundle_exposes_double_click_launchers_and_pinned_uv() -> None:
    bundle = _read("scripts/build-windows-bundle.ps1")

    assert "launch-neural-weasel.ps1" in bundle
    assert "Start-Neural-Weasel.cmd" in bundle
    assert "启动神经小狼毫.cmd" in bundle
    assert "tools/uv.exe" in bundle
    assert "uv 0.8.22" in bundle
    assert "NeuralWeaselSessionActivator.exe" in bundle


def test_launcher_installs_idempotently_starts_services_and_activates_session() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")

    assert "install-dev-profile.ps1" in launcher
    assert "start-model-service.ps1" in launcher
    assert "NeuralWeaselServer.exe" in launcher
    assert "NeuralWeaselSessionActivator.exe" in launcher
    assert "Wait-ModelPipe" in launcher
    assert "Start-Process" in launcher
    assert " activate " in launcher
    assert "--clsid $ExperimentalClsid" in launcher
    assert "--profile-guid $ExperimentalProfileGuid" in launcher
    assert EXPERIMENTAL_CLSID in launcher
    assert EXPERIMENTAL_PROFILE_GUID in launcher
    assert "SetDefault" not in launcher
    assert "SetDefaultInputMethod" not in launcher
    assert "regsvr32" not in launcher.lower()


def test_session_activator_is_current_session_only_and_never_enables_profile() -> None:
    activator = _read("native/session_activator/session_activator.cpp")

    assert "ActivateProfile(" in activator
    assert "TF_IPPMF_FORSESSION" in activator
    assert "TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE" in activator
    assert "TF_IPPMF_ENABLEPROFILE" not in activator
    assert "RegisterProfile" not in activator
    assert "RegSetValue" not in activator
    assert "SetDefault" not in activator
    assert "IsExpectedIdentity" in activator


def test_safe_tsf_shell_contains_only_crash_contained_context_capture() -> None:
    overlay = _read("scripts/prepare-weasel-overlay.ps1")
    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]

    required = (
        "native/tsf/input_scope_policy.cc",
        "native/tsf/surrounding_text_edit_session.cc",
        "native/tsf/context_capture_client.cc",
        "native/tsf/weasel_context_adapter.cc",
        "CaptureWeaselContext",
        "ClearWeaselContext",
        "$TextEditSource",
        "$WeaselTsfSource",
    )
    for marker in required:
        assert marker in tsf_block

    forbidden = (
        "native/pipe/named_pipe_client.cc",
        "native/context/context_update_bridge.cc",
        "native/rime/editor_context_epoch.cc",
        "native/rime/ai_translator.cc",
        "StartWeaselContext",
        "StopWeaselContext",
    )
    for marker in forbidden:
        assert marker not in tsf_block

    rime_start = overlay.index("$RimeXmake")
    rime_block = overlay[rime_start:]
    assert "native/pipe/named_pipe_client.cc" in rime_block
    assert "native/rime/ai_translator.cc" in rime_block


def test_bundle_verifier_rejects_neural_runtime_inside_tsf() -> None:
    verifier = _read("scripts/verify-windows-bundle.py")

    assert "TSF_FORBIDDEN_RUNTIME_LITERALS" in verifier
    assert "NeuralWeasel-v1-" in verifier
    assert '"context_update"' in verifier
    assert "in-process TSF contains neural runtime" in verifier


def test_safe_release_locks_runtime_to_4b_base_q8_gguf() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")
    service = _read("scripts/start-model-service.ps1")

    for script in (launcher, service):
        assert "Qwen/Qwen3.5-4B-Base" in script
        assert "Qwen/Qwen3.5-0.8B-Base" not in script
    assert "Q8_0" in service
    assert "gguf" in service.lower()


def test_model_service_prefers_bundled_uv_before_path_lookup() -> None:
    service = _read("scripts/start-model-service.ps1")

    assert "tools\\uv.exe" in service
    assert "$UvCommand" in service
    assert "Get-Command uv" in service
    assert "& $UvCommand sync" in service
    assert "& $UvCommand @Arguments" in service


def test_uv_pin_accepts_official_build_metadata_but_not_other_versions() -> None:
    service = _read("scripts/start-model-service.ps1")
    bundle = _read("scripts/build-windows-bundle.ps1")

    for script in (service, bundle):
        assert "$UvVersionOutput" in script
        assert "^uv 0\\.8\\.22(?:\\s|$)" in script
        assert "$UvVersion = 'uv 0.8.22'" in script
        assert "-ne 'uv 0.8.22'" not in script


def test_double_click_runtime_supports_windows_powershell_51() -> None:
    service = _read("scripts/start-model-service.ps1")
    launchers = _read("scripts/Start-Neural-Weasel.cmd") + _read("scripts/启动神经小狼毫.cmd")

    assert "powershell.exe" in launchers
    assert "ConvertToHexString" not in service
    assert "utf8NoBOM" not in service
    assert "UTF8Encoding" in service


def test_identical_install_skips_registration_when_identity_is_healthy() -> None:
    installer = _read("scripts/install-dev-profile.ps1")

    assert " status " in installer
    assert "--json" in installer
    assert "$ProfileStatus.registered" in installer
    assert "already installed and registered; registration was not repeated" in installer


def test_ci_dry_runs_the_downloaded_launcher_before_upload() -> None:
    workflow = _read(".github/workflows/ci.yml")
    focused_workflow = _read(".github/workflows/one-click-launcher.yml")

    assert "Dry-run one-click launcher" in workflow
    assert "launch-neural-weasel.ps1" in workflow
    assert "-DryRun" in workflow
    assert "Parse user scripts with Windows PowerShell 5.1" in focused_workflow
    assert "powershell.exe" in focused_workflow


def test_safe_launch_path_requires_q8_gguf_cuda_instead_of_bitsandbytes_precision() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")
    service = _read("scripts/start-model-service.ps1")
    wisdom = _read("scripts/start-wisdom-service.vbs")

    for text in (launcher, service, wisdom):
        assert "Qwen/Qwen3.5-4B-Base" in text
        assert "Q8_0" in text
    assert "--precision" not in service
    assert "[ValidateSet('int8')]" not in service
    assert "-Precision int8" not in wisdom
    assert "CUDA" in service


def test_model_service_does_not_use_model_name_only_index_cache_key() -> None:
    service = _read("scripts/start-model-service.ps1")

    assert "$ModelHash.sqlite3" not in service
    assert "$ModelHash" not in service
