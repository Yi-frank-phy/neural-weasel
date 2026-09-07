from __future__ import annotations

import threading
from collections import OrderedDict
from types import SimpleNamespace

from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.pipe_server import ContextBinding
from neural_weasel.production_pipe import ProductionNamedPipeServer

_CONTEXT_SESSION = "0123456789abcdef0123456789abcdef"
_SENTINEL = "PRIVATE_CONTEXT_CANDIDATE_MUST_NOT_ESCAPE"


class BlockingPageEngine:
    context_epoch = 1

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.private_reset = threading.Event()
        self.history_cleared = threading.Event()
        self.sessions_invalidated = threading.Event()

    def query_candidate_page(self, **kwargs):
        del kwargs
        self.started.set()
        assert self.release.wait(2.0)
        return SimpleNamespace(
            candidate_ids=("candidate-secret",),
            candidates=({"text": _SENTINEL},),
            candidate_set_id="set-secret",
            page_index=0,
            page_size=9,
            has_more=False,
            score_source="context",
        )

    def reset_private_context(self) -> None:
        self.private_reset.set()

    def clear_history(self) -> None:
        self.history_cleared.set()

    def invalidate_candidate_sessions(self) -> None:
        self.sessions_invalidated.set()


class BlockingCoordinator:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def invalidate_private_state(self) -> None:
        self.started.set()
        assert self.release.wait(2.0)


class CandidatePagesStub:
    def __init__(self) -> None:
        self.sessions = {"secret": object()}

    def clear_sessions(self) -> None:
        self.sessions.clear()


def test_secure_focus_invalidates_context_query_that_was_already_in_flight() -> None:
    engine = BlockingPageEngine()
    server = ProductionNamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-secure-race-test")
    with server._state_lock:
        server._context_bindings[1] = ContextBinding(_CONTEXT_SESSION, 1, "normal")

    query = {
        "type": "query_candidate_page",
        "request_id": "query-1",
        "session_id": "client",
        "composition_revision": 1,
        "context_epoch": 1,
        "context_session": _CONTEXT_SESSION,
        "source_revision": 1,
        "language_mode": "chinese_first",
        "raw_keys": "ni",
        "page_index": 0,
    }
    result: dict[str, object] = {}

    def run_query() -> None:
        result["response"] = server.handle_message(query)

    thread = threading.Thread(target=run_query, daemon=True)
    thread.start()
    assert engine.started.wait(1.0)

    focus = server.handle_message(
        {
            "type": "focus",
            "session_id": "client",
            "focused": True,
            "secure": True,
        }
    )
    assert focus["ok"] is True
    assert engine.private_reset.is_set()
    assert engine.history_cleared.is_set()
    assert engine.sessions_invalidated.is_set()

    engine.release.set()
    thread.join(2.0)
    assert not thread.is_alive()

    response = result["response"]
    assert response["ok"] is False
    assert response["error"]["code"] == "context_session_mismatch"
    assert _SENTINEL not in repr(response)


def test_private_reset_clears_query_visible_state_before_runtime_wipe_waits() -> None:
    engine = object.__new__(BilingualImeEngine)
    engine._contexts_lock = threading.Lock()
    engine._contexts = {1: ("private-before", "private-after")}
    engine._query_cache_lock = threading.Lock()
    engine._query_cache = OrderedDict({(1, "ni", 5, None): ()})
    engine.candidate_pages = CandidatePagesStub()
    coordinator = BlockingCoordinator()
    engine.coordinator = coordinator

    reset_done = threading.Event()

    def reset() -> None:
        engine.reset_private_context()
        reset_done.set()

    thread = threading.Thread(target=reset, daemon=True)
    thread.start()
    assert coordinator.started.wait(1.0)
    assert reset_done.is_set() is False

    # The physical runtime wipe can still be waiting for an in-flight GPU call,
    # but nothing query-visible from the old editor context may remain by then.
    assert engine.candidate_pages.sessions == {}
    assert engine._contexts == {}
    assert engine._query_cache == OrderedDict()

    coordinator.release.set()
    thread.join(2.0)
    assert reset_done.is_set() is True
