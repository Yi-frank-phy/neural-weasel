import threading
from dataclasses import replace

import pytest
from test_candidate_page_concurrency import _engine, _page


@pytest.mark.parametrize("batch_count", [2, 6])
def test_completed_batches_publish_before_search_finishes(make_index, monkeypatch, batch_count):
    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    gates = [threading.Event() for _ in range(batch_count)]
    started = [threading.Event() for _ in range(batch_count)]
    calls = 0

    def batch(session, deadline, *, max_parents):
        nonlocal calls
        calls += 1
        if calls > 1:
            slot = calls - 2
            started[slot].set()
            manager._state_lock.release()
            try:
                assert gates[slot].wait(3)
            finally:
                manager._state_lock.acquire()
        if calls == batch_count + 1:
            return 0
        template = session.frozen_pages[0].candidates[0]
        session.pending.append(
            replace(
                template,
                text="你好" + "啊" * calls,
                script="han",
                token_path=(calls, 2),
                completes_input=True,
                consumed_keys=5,
            )
        )
        return 1

    monkeypatch.setattr(manager, "_expand_background_frontier_batch", batch)
    monkeypatch.setattr(manager, "_maybe_start_page_preparation", lambda session: None)
    first = _page(engine, client="progressive", revision=1, raw="nihao")
    kwargs = dict(
        client_session_id="progressive",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode="chinese_first",
        raw_keys="nihao",
        page_index=0,
        candidate_set_id=None,
        state=None,
        presentation_refresh=True,
    )
    try:
        previous = first
        for slot in range(batch_count):
            assert started[slot].wait(1)
            current = manager.query_page(**kwargs)
            assert current.candidate_set_id != previous.candidate_set_id
            text = "你好" + "啊" * (slot + 1)
            assert text in [c.text for c in current.candidates]
            assert text not in [c.text for c in previous.candidates]
            assert first.candidate_set_id in manager._sessions
            assert manager.presentation_update_pending(current.candidate_set_id)
            assert len(manager._sessions) <= 4
            assert calls == slot + 2
            previous = current
            gates[slot].set()
    finally:
        for gate in gates:
            gate.set()
        manager.clear_sessions()
