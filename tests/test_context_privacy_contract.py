from __future__ import annotations

from pathlib import Path

from neural_weasel.context import EditorContext
from neural_weasel.pipe_server import NamedPipeServer

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "NW_SENTINEL_SECRET_6d1f48f1"


class NoopEngine:
    context_epoch = 0

    def request_context_update(self, before: str, after: str = "") -> int:
        self.context_epoch += 1
        return self.context_epoch


def test_context_metadata_has_no_text_or_content_fingerprint() -> None:
    context = EditorContext(
        before=SENTINEL,
        after="private research tail",
        app_id="editor.exe",
        partial=False,
        complete_region=True,
        secure=False,
    )
    metadata = context.metadata()
    representation = repr(metadata)

    assert SENTINEL not in representation
    assert "private research tail" not in representation
    for key in metadata:
        normalized = str(key).casefold()
        assert "sha" not in normalized
        assert "hash" not in normalized
        assert "digest" not in normalized
        assert "fingerprint" not in normalized


def test_unknown_context_oracle_operations_do_not_echo_secret() -> None:
    server = NamedPipeServer(NoopEngine(), pipe_name=r"\\.\pipe\unused-privacy-test")
    for operation in ("get_context", "dump_context", "list_contexts"):
        response = server.handle_message({"type": operation, "payload": SENTINEL})
        assert response["ok"] is False
        assert response["error"]["code"] == "unknown_message_type"
        assert SENTINEL not in repr(response)


def test_restored_tsf_path_does_not_use_wisdom_or_persistence_bridges() -> None:
    overlay = (ROOT / "scripts/prepare-weasel-overlay-pinned.ps1").read_text(
        encoding="utf-8"
    )
    client = (ROOT / "native/tsf/context_capture_client.cc").read_text(encoding="utf-8")
    broker = (ROOT / "native/context/context_capture_broker.cc").read_text(encoding="utf-8")

    context_path = "\n".join((client, broker))
    for marker in ("Wisdom", "sqlite", "telemetry", "ofstream", "fopen("):
        assert marker.casefold() not in context_path.casefold()

    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]
    assert "wisdom" not in tsf_block.casefold()
