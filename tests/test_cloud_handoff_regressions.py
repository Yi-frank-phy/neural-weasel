from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-dev-profile.ps1"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig")


def test_installer_atomically_upgrades_only_the_product_owned_schema() -> None:
    installer = _installer_text()

    assert "$ManagedSchemaName = 'neural_weasel.schema.yaml'" in installer
    assert "function Sync-ManagedRimeSchema" in installer
    assert "[IO.File]::Replace(" in installer
    assert "Where-Object { $_.Name -ne $ManagedSchemaName }" in installer
    assert "rime-user\\neural_weasel.schema.yaml" in installer


def test_installer_invalidates_and_can_restore_the_generated_managed_schema() -> None:
    installer = _installer_text()

    assert "$GeneratedSchema = Join-Path $BuildRoot $ManagedSchemaName" in installer
    assert "Move-Item `\n                -LiteralPath $GeneratedSchema" in installer
    assert "$GeneratedBackup" in installer
    assert "-Destination $GeneratedSchema" in installer


def test_identical_bundle_path_also_repairs_the_runtime_schema() -> None:
    installer = _installer_text()

    assert installer.count("Sync-ManagedRimeSchema -InstalledBundleRoot $InstallRoot") == 2
