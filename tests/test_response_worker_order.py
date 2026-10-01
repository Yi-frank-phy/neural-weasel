import threading
from types import SimpleNamespace

import pytest
from test_candidate_page_concurrency import _engine, _page

from neural_weasel import pipe_server
from neural_weasel.neural_candidates import CandidatePageTimeout
from neural_weasel.response_workers import after_response, start_worker


def test_continuation_waits_for_response_before_building_page_zero(make_index):
    engine, runtime = _engine(make_index)
    try:
        with after_response():
            with pytest.raises(CandidatePageTimeout):
                _page(engine, client="test", revision=1, raw="nihao")
            with engine.candidate_pages._state_lock:
                session = next(iter(engine.candidate_pages._sessions.values()))
                assert 0 not in session.frozen_pages
            assert not runtime.started.is_set()
        assert runtime.started.wait(1)
        runtime.release.set()
        completion = engine.candidate_pages._background_search_events[session.candidate_set_id]
        assert completion.wait(1)
        page = _page(engine, client="test", revision=1, raw="nihao")
        assert "你好" in {candidate.text for candidate in page.candidates}
    finally:
        runtime.release.set()
        engine.candidate_pages.clear_sessions()


def test_failed_response_still_dispatches_registered_worker():
    started = threading.Event()
    try:
        with after_response():
            start_worker(threading.Thread(target=started.set))
            assert not started.is_set()
            raise OSError("peer disconnected")
    except OSError:
        pass
    assert started.wait(1)


def test_pipe_writes_before_starting_registered_worker(monkeypatch):
    order = []
    server = pipe_server.NamedPipeServer(object())

    def read(handle):
        if order:
            raise EOFError
        return {}

    def handle(message):
        # A deterministic stand-in for Thread.start observes exact ordering.
        start_worker(SimpleNamespace(start=lambda: order.append("worker")))
        return {}

    monkeypatch.setattr(server, "handle_message", handle)
    monkeypatch.setattr(pipe_server, "_read_message", read)
    monkeypatch.setattr(pipe_server, "_write_message", lambda *a: order.append("write"))
    monkeypatch.setattr(
        pipe_server,
        "_win32_modules",
        lambda: (
            SimpleNamespace(error=OSError),
            None,
            None,
            SimpleNamespace(CloseHandle=lambda h: None),
            SimpleNamespace(DisconnectNamedPipe=lambda h: None),
        ),
    )
    server._serve_connection(object())
    assert order == ["write", "worker"]
