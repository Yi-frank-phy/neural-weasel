from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEASEL_CONFIG = ROOT / "assets" / "rime" / "weasel.yaml"
LAUNCHER = ROOT / "scripts" / "launch-neural-weasel.ps1"


def _config() -> str:
    return WEASEL_CONFIG.read_text(encoding="utf-8")


def _launcher() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def _repair_block(launcher: str) -> str:
    start = launcher.index("function Repair-KnownTransparentWeaselStyle")
    end = launcher.index("if (-not [Environment]::Is64BitOperatingSystem)")
    return launcher[start:end]


def test_base_weasel_config_selects_an_explicit_color_scheme() -> None:
    config = _config()

    assert 'config_version: "0.2"' in config
    assert "color_scheme: neural_weasel_default" in config
    assert "preset_color_schemes:" in config
    assert "neural_weasel_default:" in config


def test_base_candidate_style_has_opaque_paint_inputs() -> None:
    config = _config()

    # Pinned Weasel 0.17.4 leaves all UIStyle colors at 0x00000000 unless
    # style/color_scheme exists and _UpdateUIStyleColor() runs. Keep explicit
    # visible text/background/highlight values in the managed base config.
    for marker in (
        "back_color: 0xffffff",
        "text_color: 0x000000",
        "candidate_text_color: 0x000000",
        "candidate_back_color: 0xffffff",
        "border_color: 0xd0d0d0",
        "hilited_candidate_text_color: 0xffffff",
        "hilited_candidate_back_color: 0x3a6ea5",
    ):
        assert marker in config

    assert "back_color: 0x00000000" not in config
    assert "candidate_text_color: 0x00000000" not in config
    assert "hilited_candidate_back_color: 0x00000000" not in config


def test_visibility_fix_does_not_change_geometry_or_font_defaults() -> None:
    config = _config()

    for unrelated_setting in (
        "font_point:",
        "label_font_point:",
        "comment_font_point:",
        "min_width:",
        "margin_x:",
        "margin_y:",
        "candidate_spacing:",
        "hilite_padding:",
    ):
        assert unrelated_setting not in config


def test_launcher_repairs_only_the_exact_legacy_managed_config() -> None:
    launcher = _launcher()
    repair = _repair_block(launcher)

    assert "function Repair-KnownTransparentWeaselStyle" in repair
    assert 'config_version: "0.1"' in repair
    assert 'label_format: "%s."' in repair
    assert "Normalize-ManagedYamlText" in launcher
    assert "-ne" in repair
    assert (
        "Copy-Item -LiteralPath $ManagedSource -Destination $RuntimeWeasel -Force"
        in repair
    )

    # Do not turn the migration into a generic overwrite of RimeUser YAML.
    assert "Get-ChildItem" not in repair


def test_style_migration_invalidates_only_deployed_weasel_config() -> None:
    repair = _repair_block(_launcher())

    assert "build\\weasel.yaml" in repair
    assert "Remove-Item -LiteralPath $DeployedWeasel -Force" in repair
    assert "Remove-Item -LiteralPath $RimeUserRoot -Recurse" not in repair
    assert "Remove-Item -LiteralPath $RuntimeRoot -Recurse" not in repair


def test_style_repair_happens_before_experimental_server_start() -> None:
    launcher = _launcher()

    repair_call = launcher.index(
        "$StyleWasMigrated = Repair-KnownTransparentWeaselStyle"
    )
    server_start = launcher.index("Start-Process -FilePath $Server")
    assert repair_call < server_start
    assert "Stop-Process -Force" in launcher[repair_call:server_start]


def test_bundle_preflight_requires_both_weasel_config_copies() -> None:
    launcher = _launcher()

    assert "'data\\weasel.yaml'" in launcher
    assert "'rime-user\\weasel.yaml'" in launcher
