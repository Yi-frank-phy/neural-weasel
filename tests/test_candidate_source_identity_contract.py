from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from neural_weasel.pipe_server import ContextBinding
from neural_weasel.production_pipe import ProductionNamedPipeServer

ROOT = Path(__file__).resolve().parents[1]
_CONTEXT_SESSION = "0123456789abcdef0123456789abcdef"


class PageEngine:
    context_epoch = 1

    def query_candidate_page(self, **kwargs):
        del kwargs
        return SimpleNamespace(
            candidate_ids=("candidate-1",),
            candidates=(
                {
                    "text": "你",
                    "consumed_keys": 2,
                    "constraint_kind": "pinyin",
                    "script": "han",
                },
            ),
            candidate_set_id="set-1",
            page_index=0,
            page_size=9,
            has_more=False,
            score_source="context",
        )


def _server() -> ProductionNamedPipeServer:
    server = ProductionNamedPipeServer(PageEngine(), pipe_name=r"\\.\pipe\unused-source-id-test")
    with server._state_lock:
        server._context_bindings[1] = ContextBinding(_CONTEXT_SESSION, 7, "normal")
    return server


def test_nonzero_context_page_echoes_exact_editor_source_identity() -> None:
    response = _server().handle_message(
        {
            "type": "query_candidate_page",
            "session_id": "client",
            "composition_revision": 3,
            "context_epoch": 1,
            "context_session": _CONTEXT_SESSION,
            "source_revision": 7,
            "language_mode": "chinese_first",
            "raw_keys": "ni",
            "page_index": 0,
        }
    )

    assert response["ok"] is True
    assert response["context_session"] == _CONTEXT_SESSION
    assert response["source_revision"] == 7


def test_epoch_zero_page_does_not_invent_editor_source_identity() -> None:
    server = ProductionNamedPipeServer(PageEngine(), pipe_name=r"\\.\pipe\unused-source-id-zero")
    response = server.handle_message(
        {
            "type": "query_candidate_page",
            "session_id": "client",
            "composition_revision": 1,
            "context_epoch": 0,
            "language_mode": "chinese_first",
            "raw_keys": "n",
            "page_index": 0,
        }
    )

    assert response["ok"] is True
    assert "context_session" not in response
    assert "source_revision" not in response


def test_native_translator_rejects_wrong_source_identity_before_freezing_page() -> None:
    source = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")

    assert "const bool source_identity_matches" in source
    assert 'response.value("context_session", std::string{})' in source
    assert 'response.value("source_revision", std::uint64_t{0})' in source
    assert "!source_identity_matches" in source
    assert source.index("!source_identity_matches") < source.index("frozen_pages_[requested_page]")
