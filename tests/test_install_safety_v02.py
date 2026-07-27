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

# Regression fixture only: neither value may ever become an allowed mutation
# target. The Microsoft value is deliberately represented as a non-experimental
# profile identifier rather than used by runtime fallback selection.
OFFICIAL_WEASEL_FIXTURE_CLSID = "{3D02CAB6-2B8E-4781-BA20-1C9267529467}"
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
    assert "--no-default" in install
    assert OFFICIAL_WEASEL_FIXTURE_CLSID not in combined
    assert MICROSOFT_PINYIN_FIXTURE_PROFILE not in combined
    assert "Refusing" in uninstall


def test_uninstall_has_no_identifier_override_parameters() -> None:
    """AT-WIN-04/05: callers cannot redirect uninstall at another profile."""
    uninstall = (ROOT / "scripts" / "uninstall-dev-profile.ps1").read_text(encoding="utf-8")

    assert "[string]$Clsid" not in uninstall
    assert "[string]$ProfileGuid" not in uninstall
    assert "--clsid $ExperimentalClsid" in uninstall
    assert "--profile-guid $ExperimentalProfileGuid" in uninstall
