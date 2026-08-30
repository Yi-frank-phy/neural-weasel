from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WEASEL_CONFIG = ROOT / "assets" / "rime" / "weasel.yaml"
BUNDLE_BUILDER = ROOT / "scripts" / "build-windows-bundle.ps1"


def _config_text() -> str:
    return WEASEL_CONFIG.read_text(encoding="utf-8")


def _config() -> dict[str, object]:
    loaded = yaml.safe_load(_config_text())
    assert isinstance(loaded, dict)
    return loaded


def _selected_scheme() -> dict[str, object]:
    config = _config()
    style = config["style"]
    presets = config["preset_color_schemes"]
    assert isinstance(style, dict)
    assert isinstance(presets, dict)

    scheme_name = style["color_scheme"]
    assert isinstance(scheme_name, str)
    scheme = presets[scheme_name]
    assert isinstance(scheme, dict)
    return scheme


def _weasel_alpha_for_abgr_scalar(value: object) -> int:
    assert isinstance(value, int)
    assert 0 <= value <= 0xFFFFFFFF
    # Pinned Weasel's _RimeGetColor() makes <=6-digit RGB/BGR scalars opaque.
    if value <= 0xFFFFFF:
        return 0xFF
    return (value >> 24) & 0xFF


def test_base_weasel_config_selects_an_existing_color_scheme() -> None:
    config = _config()

    # Keep the pre-existing config version unchanged so this A/B changes only
    # paint inputs, not any Rime deployment/version behavior.
    assert config["config_version"] == "0.1"
    style = config["style"]
    presets = config["preset_color_schemes"]
    assert isinstance(style, dict)
    assert isinstance(presets, dict)

    scheme_name = style["color_scheme"]
    assert scheme_name == "neural_weasel_default"
    assert scheme_name in presets


def test_base_candidate_style_has_opaque_paint_inputs() -> None:
    scheme = _selected_scheme()

    # These are the fields that make the ordinary and highlighted candidate
    # paths visible. Under pinned Weasel 0.17.4, a missing color scheme leaves
    # the corresponding UIStyle fields at 0x00000000 instead.
    critical_fields = (
        "back_color",
        "text_color",
        "candidate_text_color",
        "candidate_back_color",
        "border_color",
        "hilited_text_color",
        "hilited_back_color",
        "hilited_candidate_text_color",
        "hilited_candidate_back_color",
        "label_color",
        "comment_text_color",
    )
    for field in critical_fields:
        assert field in scheme
        assert _weasel_alpha_for_abgr_scalar(scheme[field]) == 0xFF


def test_visibility_fix_does_not_change_geometry_or_font_defaults() -> None:
    config = _config_text()

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
