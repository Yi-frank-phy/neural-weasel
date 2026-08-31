from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_does_not_ship_or_poll_an_editor_context_disk_bridge() -> None:
    http_server = (ROOT / "src/neural_weasel/http_server.py").read_text(
        encoding="utf-8"
    )

    assert "NeuralWeasel\" / \"Bridge" not in http_server
    assert "*.request" not in http_server
    assert "_serve_file_bridge" not in http_server
    assert "BRIDGE_POLL_SECONDS" not in http_server
    assert not (ROOT / "assets/rime/lua/neural_translator.lua").exists()
    assert not (ROOT / "assets/rime/rime_ice.custom.yaml").exists()
