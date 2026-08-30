from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEASEL_CONFIG = ROOT / "assets" / "rime" / "weasel.yaml"
BUNDLE_BUILDER = ROOT / "scripts" / "build-windows-bundle.ps1"


def _config() -> str:
    return WEASEL_CONFIG.read_text(encoding="utf-8")


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


def test_bundle_places_managed_weasel_config_in_shared_data() -> None:
    builder = BUNDLE_BUILDER.read_text(encoding="utf-8")

    assert "$DataRoot = Join-Path $OutputRoot 'data'" in builder
    assert "Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'assets/rime')" in builder
    assert "$RimeUser = Join-Path $OutputRoot 'rime-user'" in builder
