import threading
from dataclasses import replace

import pytest
from test_candidate_page_concurrency import _engine, _page


@pytest.mark.parametrize("batch_count", [2, 6])
def test_completed_batches_do_not_replace_published_page(make_index, monkeypatch, batch_count):
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
            session.exhausted = True
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
    first = _page(engine, client="progressive", revision=1, raw="nihao")
    assert first.has_more is True
    completion = manager._background_search_events[first.candidate_set_id]
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
        for slot in range(batch_count):
            assert started[slot].wait(1)
            current = manager.query_page(**kwargs)
            assert current.candidate_set_id == first.candidate_set_id
            assert current.candidates == first.candidates
            assert current.candidate_ids == first.candidate_ids
            assert current.has_more is True
            text = "你好" + "啊" * (slot + 1)
            with manager._state_lock:
                session = manager._sessions[first.candidate_set_id]
                assert text in [candidate.text for candidate in session.pending]
            assert not manager.presentation_update_pending(current.candidate_set_id)
            assert len(manager._sessions) == 1
            assert calls == slot + 2
            gates[slot].set()
        assert completion.wait(1.0)
        preparation = manager._page_preparation_events[first.candidate_set_id]
        assert preparation.wait(1.0)
        with manager._state_lock:
            later_text = {
                candidate.text
                for page_index, page in manager._sessions[
                    first.candidate_set_id
                ].frozen_pages.items()
                if page_index > 0
                for candidate in page.candidates
            }
        assert {"你好" + "啊" * index for index in range(1, batch_count + 1)}.issubset(later_text)
    finally:
        for gate in gates:
            gate.set()
        manager.clear_sessions()
