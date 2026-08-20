from __future__ import annotations

from pathlib import Path

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


def test_bundle_contains_buildable_python_project_metadata() -> None:
    bundle = _read("scripts/build-windows-bundle.ps1")
    verifier = _read("scripts/verify-windows-bundle.py")
    workflow = _read(".github/workflows/ci.yml")

    assert "README.md" in bundle
    assert '"python-service/README.md"' in verifier
    assert "Build bundled Python wheel" in workflow
    assert "uv build" in workflow


def test_launcher_installs_backend_and_uses_the_official_weasel_shell() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")

    assert "install-wisdom-integration.ps1" in launcher
    assert "start-neural-weasel-integration.ps1" in launcher
    assert "start-wisdom-service.vbs" in launcher
    assert "Start-Process" in launcher
    assert "official Weasel TSF and candidate UI" in launcher
    assert "install-dev-profile.ps1" not in launcher
    assert "NeuralWeaselServer.exe" not in launcher
    assert "NeuralWeaselSessionActivator.exe" not in launcher
    assert "ExperimentalClsid" not in launcher
    assert "--clsid" not in launcher
    assert "--profile-guid" not in launcher
    assert "& $Activator activate" not in launcher
    assert "SetDefault" not in launcher
    assert "SetDefaultInputMethod" not in launcher
    assert "regsvr32" not in launcher.lower()


def test_official_shell_launcher_is_windows_powershell_51_path_safe() -> None:
    launcher = _read("scripts/start-neural-weasel-integration.ps1")

    assert "C:\\输入法" not in launcher
    assert "0x8F93, 0x5165, 0x6CD5" in launcher
    assert "NEURAL_WEASEL_COMPATIBILITY_ROOT" in launcher
    assert "start-wisdom-service.vbs" in launcher
    assert "http://127.0.0.1:8000/health" in launcher
    assert "Start-Process" in launcher
    assert "-WindowStyle Hidden" in launcher


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


def test_safe_tsf_shell_contains_no_neural_runtime_or_context_capture() -> None:
    overlay = _read("scripts/prepare-weasel-overlay.ps1")
    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]

    forbidden = (
        "native/pipe/named_pipe_client.cc",
        "native/context/context_update_bridge.cc",
        "native/rime/editor_context_epoch.cc",
        "native/tsf/surrounding_text_edit_session.cc",
        "native/tsf/weasel_context_adapter.cc",
        "CaptureWeaselContext",
        "ClearWeaselContext",
        "StartWeaselContext",
        "StopWeaselContext",
        "$TextEditSink",
        "$WeaselTsfSource",
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


def test_safe_release_locks_the_runtime_to_the_08b_model() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")
    service = _read("scripts/start-model-service.ps1")

    for script in (launcher, service):
        assert "[ValidateSet('Qwen/Qwen3.5-0.8B-Base')]" in script
        assert "Qwen/Qwen3.5-4B-Base" not in script


def test_model_service_prefers_bundled_uv_before_path_lookup() -> None:
    service = _read("scripts/start-model-service.ps1")

    assert "tools\\uv.exe" in service
    assert "$UvCommand" in service
    assert "Get-Command uv" in service
    assert "& $UvCommand run" in service


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
    assert "BitConverter" in service
    assert "UTF8Encoding" in service


def test_identical_install_skips_registration_when_identity_is_healthy() -> None:
    installer = _read("scripts/install-dev-profile.ps1")

    assert " status " in installer
    assert "--json" in installer
    assert "$ProfileStatus.registered" in installer
    assert "already installed and registered; registration was not repeated" in installer


def test_background_runtime_processes_are_hidden() -> None:
    launcher = _read("scripts/launch-neural-weasel.ps1")

    assert "-WindowStyle Hidden" in launcher
    assert "-WindowStyle Minimized" not in launcher


def test_wisdom_service_defaults_to_native_fp8_full_logits() -> None:
    service = _read("scripts/start-model-service.ps1")
    background = _read("scripts/start-wisdom-service.vbs")

    assert "[string]$Precision = 'fp8'" in service
    assert "'--precision', $Precision" in service
    assert "-Backend full -Precision fp8" in background


def test_wisdom_install_skips_an_identical_runtime_swap() -> None:
    installer = _read("scripts/install-wisdom-integration.ps1")

    assert "$InstallMatches" in installer
    assert "if (-not $InstallMatches)" in installer
    assert "already current" in installer
    assert "$ExistingConfig -ne $DesiredConfig" in installer


def test_ci_dry_runs_the_downloaded_launcher_before_upload() -> None:
    workflow = _read(".github/workflows/ci.yml")
    focused_workflow = _read(".github/workflows/one-click-launcher.yml")

    assert "Dry-run one-click launcher" in workflow
    assert "launch-neural-weasel.ps1" in workflow
    assert "-DryRun" in workflow
    assert "Parse user scripts with Windows PowerShell 5.1" in focused_workflow
    assert "powershell.exe" in focused_workflow
