from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tsf_context_sender_is_authenticated_one_way_and_nonblocking() -> None:
    source = (ROOT / "native/tsf/context_capture_client.cc").read_text(encoding="utf-8")

    for required in (
        "NeuralWeaselContext-v1-",
        "FILE_FLAG_OVERLAPPED",
        "GetNamedPipeServerProcessId",
        "PROCESS_QUERY_LIMITED_INFORMATION",
        "NeuralWeaselServer.exe",
    ):
        assert required in source

    for forbidden in (
        "WaitNamedPipe",
        "FlushFileBuffers",
        "ReadFile(",
        "CreateThread",
        "std::thread",
    ):
        assert forbidden not in source


def test_server_owns_context_broker_and_tsf_does_not_own_backend() -> None:
    overlay = (ROOT / "scripts/prepare-weasel-overlay.ps1").read_text(encoding="utf-8")
    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]
    server_block = overlay[server_start:]

    for source in (
        "native/tsf/input_scope_policy.cc",
        "native/tsf/surrounding_text_edit_session.cc",
        "native/tsf/context_capture_client.cc",
        "native/tsf/weasel_context_adapter.cc",
    ):
        assert source in tsf_block

    for forbidden in (
        "native/context/context_update_bridge.cc",
        "native/pipe/named_pipe_client.cc",
        "native/rime/ai_translator.cc",
        "StartWeaselContext",
        "StopWeaselContext",
    ):
        assert forbidden not in tsf_block

    assert "native/context/context_capture_broker.cc" in server_block
    assert "native/context/context_update_bridge.cc" in server_block
    assert "native/pipe/named_pipe_client.cc" in server_block


def test_context_broker_has_local_identity_and_anti_squatting_contract() -> None:
    source = (ROOT / "native/context/context_capture_broker.cc").read_text(encoding="utf-8")

    for required in (
        "NeuralWeaselContext-v1-",
        "FILE_FLAG_FIRST_PIPE_INSTANCE",
        "PIPE_REJECT_REMOTE_CLIENTS",
        "GetNamedPipeClientProcessId",
        "ContextFrameReceiver",
    ):
        assert required in source


def test_rime_candidate_query_carries_coherent_editor_identity() -> None:
    epoch_header = (ROOT / "native/rime/editor_context_epoch.h").read_text(encoding="utf-8")
    translator = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")

    assert "AcceptedEditorContext" in epoch_header
    assert "source_capability" in epoch_header
    assert "source_revision" in epoch_header

    # Epoch zero is the permanent empty-context neural baseline, not a reason to
    # reject a page request. Every request carries the captured epoch; editor
    # identity is attached only when a real accepted editor epoch was captured.
    assert '{"context_epoch", context_epoch_}' in translator
    assert "if (context_epoch_ > 0)" in translator
    assert 'request["context_session"] = context_session_' in translator
    assert 'request["source_revision"] = source_revision_' in translator
    assert "model_epoch == 0" not in translator
    assert "context_identity.model_epoch == 0" not in translator

    # A composition revision freezes its context identity. Responses must match
    # that captured epoch, and a source boundary creates a fresh revision.
    assert 'response.value("context_epoch", std::uint64_t{0}) !=' in translator
    assert "context_epoch_" in translator
    assert "IsSourceBoundaryChange(context_session_, latest_context)" in translator
    assert "source_boundary" in translator


def test_context_sender_and_broker_have_no_raw_context_read_api() -> None:
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "native/tsf/context_capture_client.cc",
            "native/context/context_capture_broker.cc",
            "src/neural_weasel/pipe_server.py",
        )
    )
    for operation in ("get_context", "dump_context", "list_contexts"):
        assert operation not in sources
