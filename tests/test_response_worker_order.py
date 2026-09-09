import threading
from types import SimpleNamespace

from test_candidate_page_concurrency import _engine, _page

from neural_weasel import pipe_server
from neural_weasel.response_workers import after_response, start_worker


def test_continuation_waits_for_response_without_marking_page_replaceable(make_index):
    engine, runtime = _engine(make_index)
    try:
        with after_response():
            page = _page(engine, client="test", revision=1, raw="nihao")
            replay = _page(engine, client="test", revision=1, raw="nihao")
            assert replay.candidate_set_id == page.candidate_set_id
            assert replay.candidates == page.candidates
            assert replay.candidate_ids == page.candidate_ids
            assert not engine.candidate_pages.presentation_update_pending(page.candidate_set_id)
            assert not runtime.started.is_set()
        assert runtime.started.wait(1)
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
