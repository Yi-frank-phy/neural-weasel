from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import neural_weasel.production as production

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


def test_production_runtime_defaults_are_explicit_and_testable() -> None:
    config = production.ProductionRuntimeConfig()

    assert config.max_before_tokens == 3072
    assert config.n_ctx == 4096
    assert config.n_batch == 512
    assert config == production.DEFAULT_PRODUCTION_RUNTIME_CONFIG


def test_build_production_runtime_forwards_explicit_context_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    acquired = object()
    runtime = object()
    index = SimpleNamespace(path=tmp_path / "index.sqlite3")
    captured: dict[str, int] = {}

    def fake_acquire(artifact: object, gguf_path: object) -> object:
        del artifact, gguf_path
        return acquired

    def fake_backend(received: object, **kwargs: int) -> object:
        assert received is acquired
        captured.update(kwargs)
        return runtime

    def fake_index(received: object, explicit: Path | None) -> SimpleNamespace:
        assert received is runtime
        assert explicit is None
        return index

    monkeypatch.setattr(production, "ensure_production_gguf", fake_acquire)
    monkeypatch.setattr(production, "LlamaCppBackend", fake_backend)
    monkeypatch.setattr(production, "ensure_production_index", fake_index)

    config = production.ProductionRuntimeConfig(
        max_before_tokens=1536,
        n_ctx=2048,
        n_batch=256,
    )
    result = production.build_production_runtime(runtime_config=config)

    assert captured == {
        "max_before_tokens": 1536,
        "n_ctx": 2048,
        "n_batch": 256,
    }
    assert result.runtime is runtime
    assert result.index is index
