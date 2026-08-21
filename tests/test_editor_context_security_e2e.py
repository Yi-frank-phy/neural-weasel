from __future__ import annotations

from pathlib import Path

from neural_weasel.context import EditorContext
from neural_weasel.pipe_server import NamedPipeServer
from neural_weasel.protocol import decode_message, encode_message

ROOT = Path(__file__).resolve().parents[1]


class RecordingEngine:
    def __init__(self) -> None:
        self.context_epoch = 0
        self.requests: list[tuple[str, str]] = []

    def request_context_update(self, before: str, after: str = "") -> int:
        self.requests.append((before, after))
        self.context_epoch += 1
        return self.context_epoch


class FailingEngine:
    context_epoch = 0

    def request_context_update(self, before: str, after: str = "") -> int:
        raise RuntimeError(f"backend unavailable while processing {before}{after}")


def _push_context(
    server: NamedPipeServer,
    *,
    revision: int,
    before: str,
    after: str = "",
) -> dict[str, object]:
    wire = encode_message(
        {
            "type": "context_update",
            "context_epoch": revision,
            "before": before,
            "after": after,
        }
    )
    return server.handle_message(decode_message(wire))


def test_normal_editor_capture_metadata_ipc_server_path() -> None:
    before = "NW_E2E_NORMAL_BEFORE_71c4"
    after = "NW_E2E_NORMAL_AFTER_71c4"
    context = EditorContext(
        before=before,
        after=after,
        app_id="editor.exe",
        partial=False,
        complete_region=True,
        secure=False,
    ).clipped_fast()

    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-task10-normal")
    response = _push_context(
        server,
        revision=1,
        before=context.before,
        after=context.after,
    )

    assert response["ok"] is True
    assert response["accepted"] is True
    assert engine.requests == [(before, after)]

    metadata = context.metadata()
    serialized = repr(metadata)
    assert before not in serialized
    assert after not in serialized
    forbidden_key_fragments = ("sha", "hash", "digest", "fingerprint")
    assert not any(
        fragment in str(key).casefold()
        for key in metadata
        for fragment in forbidden_key_fragments
    )


def test_rapid_typing_latest_revision_wins_before_inference() -> None:
    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-task10-rapid")

    newest = _push_context(server, revision=8, before="newest", after="context")
    stale = _push_context(server, revision=7, before="stale", after="must-not-run")

    assert newest["accepted"] is True
    assert stale["ok"] is True
    assert stale["accepted"] is False
    assert stale["stale"] is True
    assert stale["client_context_epoch"] == 7
    assert engine.requests == [("newest", "context")]


def test_server_unavailable_is_fail_soft_and_does_not_echo_context() -> None:
    sentinel = "NW_E2E_BACKEND_SECRET_f0d9"
    server = NamedPipeServer(
        FailingEngine(),
        pipe_name=r"\\.\pipe\unused-task10-backend-failure",
    )

    response = _push_context(server, revision=1, before=sentinel)

    assert response["type"] == "error"
    assert response["ok"] is False
    assert response["error"]["code"] == "internal_error"
    assert sentinel not in repr(response)

    verifier = (ROOT / "scripts/verify-windows-bundle.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "_verify_tsf_loadable_without_backend" in verifier
    assert "LoadLibraryExW" in verifier
    assert "FreeLibrary" in verifier

    build = workflow.index("Build experimental TSF, server, and static neural module")
    verify = workflow.index("Verify binary and resource isolation")
    preverification = workflow[build:verify]
    assert "start-model-service.ps1" not in preverification
    assert "NeuralWeaselServer.exe" not in preverification
