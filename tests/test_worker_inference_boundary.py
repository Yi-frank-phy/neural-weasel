from __future__ import annotations

from pathlib import Path

from neural_weasel.pipe_server import NamedPipeServer

ROOT = Path(__file__).resolve().parents[1]


class RecordingEngine:
    def __init__(self) -> None:
        self.context_epoch = 0
        self.requests: list[tuple[str, str]] = []

    def request_context_update(self, before: str, after: str = "") -> int:
        self.requests.append((before, after))
        return len(self.requests)


def test_tsf_build_does_not_link_backend_dependency() -> None:
    """Task 8: the shipped TSF target must not acquire backend ownership."""
    overlay = (ROOT / "scripts/prepare-weasel-overlay.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts/verify-windows-bundle.py").read_text(encoding="utf-8")

    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]

    for backend_dependency in (
        "native/context/context_update_bridge.cc",
        "native/pipe/named_pipe_client.cc",
        "native/rime/ai_translator.cc",
        "native/tsf/weasel_context_adapter.cc",
    ):
        assert backend_dependency not in tsf_block

    # Binary verification is the final guard against a future transitive/static
    # link accidentally reintroducing the legacy Python/model path.
    for runtime_marker in (
        "NeuralWeasel-v1-",
        '"context_update"',
        "query_candidates",
    ):
        assert runtime_marker in verifier


def test_bundle_verifier_loads_tsf_with_backend_absent() -> None:
    """Task 8: CI must actually load/unload the TSF before any backend starts."""
    verifier = (ROOT / "scripts/verify-windows-bundle.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "_verify_tsf_loadable_without_backend" in verifier
    assert "LoadLibraryExW" in verifier
    assert "FreeLibrary" in verifier

    build = workflow.index("Build experimental TSF, server, and static neural module")
    verify = workflow.index("Verify binary and resource isolation")
    verification_window = workflow[build:verify]
    assert "start-model-service.ps1" not in verification_window
    assert "NeuralWeaselServer.exe" not in verification_window


def test_stale_context_is_dropped_before_inference() -> None:
    """Task 8: an older client revision never reaches request_context_update."""
    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-task8-test")

    newest = server.handle_message(
        {
            "type": "context_update",
            "context_epoch": 8,
            "before": "newest",
            "after": "context",
        }
    )
    stale = server.handle_message(
        {
            "type": "context_update",
            "context_epoch": 7,
            "before": "stale",
            "after": "must-not-run",
        }
    )

    assert newest["accepted"] is True
    assert stale["ok"] is True
    assert stale["accepted"] is False
    assert stale["stale"] is True
    assert stale["client_context_epoch"] == 7
    assert engine.requests == [("newest", "context")]
