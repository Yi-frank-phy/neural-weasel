from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_experimental_candidate_window_has_a_visible_self_contained_theme() -> None:
    config = yaml.safe_load(
        (ROOT / "assets/rime/weasel.yaml").read_text(encoding="utf-8")
    )

    style = config["style"]
    scheme_name = style["color_scheme"]
    scheme = config["preset_color_schemes"][scheme_name]

    assert style["font_point"] > 0
    assert style["label_font_point"] > 0
    assert style["comment_font_point"] > 0
    assert style["layout"]["min_width"] > 0
    assert scheme["back_color"] != 0
    assert scheme["hilited_candidate_back_color"] != 0
    for key in (
        "text_color",
        "candidate_text_color",
        "hilited_candidate_text_color",
    ):
        assert key in scheme
