from __future__ import annotations

from pathlib import Path

import pytest

from neural_weasel.profile_manifest import (
    EXPERIMENTAL_CLSID,
    EXPERIMENTAL_DISPLAY_NAME,
    EXPERIMENTAL_PROFILE_GUID,
    ProfileInstallManifest,
    validate_experimental_manifest,
)

ROOT = Path(__file__).resolve().parents[1]

# Regression fixtures only: these values may never become mutation targets.
OFFICIAL_WEASEL_FIXTURE_CLSID = "{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}"
OFFICIAL_WEASEL_FIXTURE_PROFILE = "{3D02CAB6-2B8E-4781-BA20-1C9267529467}"
MICROSOFT_PINYIN_FIXTURE_PROFILE = "{F3BA9077-6C7E-11D4-97FA-0080C882687E}"


def test_manifest_accepts_only_reserved_experimental_identity(tmp_path: Path) -> None:
    """AT-WIN-01/02/04: install and uninstall target one reserved pair."""
    manifest = ProfileInstallManifest(
        clsid=EXPERIMENTAL_CLSID,
        profile_guid=EXPERIMENTAL_PROFILE_GUID,
        display_name=EXPERIMENTAL_DISPLAY_NAME,
        install_directory=tmp_path / "experimental-profile",
        set_default=False,
    )

    validate_experimental_manifest(manifest)
    assert manifest.clsid != OFFICIAL_WEASEL_FIXTURE_CLSID
    assert manifest.profile_guid != MICROSOFT_PINYIN_FIXTURE_PROFILE

    with pytest.raises(ValueError, match="experimental"):
        validate_experimental_manifest(
            ProfileInstallManifest(
                clsid=OFFICIAL_WEASEL_FIXTURE_CLSID,
                profile_guid=EXPERIMENTAL_PROFILE_GUID,
                display_name=EXPERIMENTAL_DISPLAY_NAME,
                install_directory=manifest.install_directory,
                set_default=False,
            )
        )


def test_manifest_forbids_default_activation_and_unisolated_directory(tmp_path: Path) -> None:
    """AT-WIN-03: development installation is isolated and never default."""
    with pytest.raises(ValueError, match="default"):
        validate_experimental_manifest(
            ProfileInstallManifest(
                clsid=EXPERIMENTAL_CLSID,
                profile_guid=EXPERIMENTAL_PROFILE_GUID,
                display_name=EXPERIMENTAL_DISPLAY_NAME,
                install_directory=tmp_path / "experimental-profile",
                set_default=True,
            )
        )

    with pytest.raises(ValueError, match="experimental-profile"):
        validate_experimental_manifest(
            ProfileInstallManifest(
                clsid=EXPERIMENTAL_CLSID,
                profile_guid=EXPERIMENTAL_PROFILE_GUID,
                display_name=EXPERIMENTAL_DISPLAY_NAME,
                install_directory=tmp_path / "weasel",
                set_default=False,
            )
        )


def test_required_powershell_scripts_are_fail_closed() -> None:
    """AT-WIN-05: user entry points carry explicit safety markers."""
    required = {
        "install-dev-profile.ps1",
        "uninstall-dev-profile.ps1",
        "start-model-service.ps1",
        "diagnose.ps1",
    }
    script_paths = {path.name: path for path in (ROOT / "scripts").glob("*.ps1")}
    assert required <= script_paths.keys()

    install = script_paths["install-dev-profile.ps1"].read_text(encoding="utf-8")
    uninstall = script_paths["uninstall-dev-profile.ps1"].read_text(encoding="utf-8")
    combined = install + uninstall
    assert EXPERIMENTAL_CLSID in combined
    assert EXPERIMENTAL_PROFILE_GUID in combined
    assert "experimental-profile" in combined
    assert "SetDefault" not in install
    assert "SetDefaultInputMethod" not in combined
    assert "ActivateProfile" not in combined
    assert "--dry-run" in combined
    assert "build-manifest.json" in install
    assert "Get-FileHash" in install
    assert OFFICIAL_WEASEL_FIXTURE_CLSID not in combined
    assert OFFICIAL_WEASEL_FIXTURE_PROFILE not in combined
    assert MICROSOFT_PINYIN_FIXTURE_PROFILE not in combined
    assert "Refusing" in uninstall


def test_uninstall_has_no_identifier_override_parameters() -> None:
    """AT-WIN-04/05: callers cannot redirect uninstall at another profile."""
    uninstall = (ROOT / "scripts" / "uninstall-dev-profile.ps1").read_text(encoding="utf-8")

    assert "[string]$Clsid" not in uninstall
    assert "[string]$ProfileGuid" not in uninstall
    assert "--clsid $ExperimentalClsid" in uninstall
    assert "--profile-guid $ExperimentalProfileGuid" in uninstall


def test_experimental_identity_is_consistent_across_all_mutation_boundaries() -> None:
    header = (ROOT / "native/tsf/experimental_profile_ids.h").read_text(encoding="utf-8")
    profile_tool = (ROOT / "native/profile_tool/profile_tool.cpp").read_text(encoding="utf-8")
    bundle = (ROOT / "scripts/build-windows-bundle.ps1").read_text(encoding="utf-8")
    for path in (
        ROOT / "scripts/install-dev-profile.ps1",
        ROOT / "scripts/uninstall-dev-profile.ps1",
        ROOT / "scripts/diagnose.ps1",
    ):
        text = path.read_text(encoding="utf-8")
        assert EXPERIMENTAL_CLSID in text
        assert EXPERIMENTAL_PROFILE_GUID in text

    assert EXPERIMENTAL_CLSID in header
    assert EXPERIMENTAL_PROFILE_GUID in header
    assert "IsExpectedIdentity" in profile_tool
    assert "Refusing non-experimental identifier" in profile_tool
    assert EXPERIMENTAL_CLSID in bundle
    assert EXPERIMENTAL_PROFILE_GUID in bundle


def test_pinned_overlay_rewrites_all_official_runtime_identities() -> None:
    overlay = (ROOT / "scripts/prepare-weasel-overlay.ps1").read_text(encoding="utf-8")

    assert "9cc96e20dc71b80876b12f689bb5863c76c2a7ed" in overlay
    assert OFFICIAL_WEASEL_FIXTURE_CLSID.strip("{}") in overlay
    assert OFFICIAL_WEASEL_FIXTURE_PROFILE.strip("{}") in overlay
    assert "NeuralWeaselExperimentalIPC" in overlay
    assert "NeuralWeaselServer.exe" in overlay
    assert "Software\\\\NeuralWeasel\\\\Experimental" in overlay
    assert "NeuralWeaselExperimentalTSF.dll" in overlay
    assert "CaptureWeaselContext" not in overlay
    assert "crash-contained" in overlay
    assert "rime_require_module_ai_translator" in overlay


def test_ci_runs_disposable_install_safety_suite_without_global_registration() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    safety = (ROOT / "scripts/test-install-safety.ps1").read_text(encoding="utf-8")

    assert "test-install-safety.ps1" in workflow
    assert "-DryRun" in safety
    assert "missing TSF DLL" in safety
    assert "identifier conflict" in safety
    assert "non-experimental GUID" in safety
    assert "SetDefault" not in safety
