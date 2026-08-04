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


def test_model_service_prefers_bundled_uv_before_path_lookup() -> None:
    service = _read("scripts/start-model-service.ps1")

    assert "tools\\uv.exe" in service
    assert "$UvCommand" in service
    assert "Get-Command uv" in service
    assert "& $UvCommand run" in service


def test_double_click_runtime_supports_windows_powershell_51() -> None:
    service = _read("scripts/start-model-service.ps1")
    launchers = (
        _read("scripts/Start-Neural-Weasel.cmd")
        + _read("scripts/启动神经小狼毫.cmd")
    )

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


def test_ci_dry_runs_the_downloaded_launcher_before_upload() -> None:
    workflow = _read(".github/workflows/ci.yml")
    focused_workflow = _read(".github/workflows/one-click-launcher.yml")

    assert "Dry-run one-click launcher" in workflow
    assert "powershell.exe" in workflow
    assert "launch-neural-weasel.ps1" in workflow
    assert "-DryRun" in workflow
    assert "Parse user scripts with Windows PowerShell 5.1" in focused_workflow
    assert "powershell.exe" in focused_workflow
