from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEASEL_CONFIG = ROOT / "assets" / "rime" / "weasel.yaml"


def test_vscode_only_inline_preedit_probe_is_scoped() -> None:
    config = WEASEL_CONFIG.read_text(encoding="utf-8")

    assert "app_options:" in config
    assert "  code.exe:" in config
    assert "    inline_preedit: true" in config
    # Keep the global baseline non-inline so this remains a VS Code-only A/B.
    assert "style:\n  display_tray_icon: true\n  inline_preedit: false" in config
