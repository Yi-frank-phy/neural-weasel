import threading
from dataclasses import replace

import pytest
from test_candidate_page_concurrency import _engine, _page

from neural_weasel.neural_candidates import CandidatePageTimeout


def test_published_page_zero_cancels_progressive_background_search(
    make_index, monkeypatch
):
    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    gate = threading.Event()
    started = threading.Event()
    calls = 0

    def batch(session, deadline, *, max_parents):
        nonlocal calls
        calls += 1
        if calls > 1:
            started.set()
            manager._state_lock.release()
            try:
                assert gate.wait(3)
            finally:
                manager._state_lock.acquire()
        template = session.pending[0]
        session.pending.append(
            replace(
                template,
                text="你好" + "啊" * calls,
                script="han",
                token_path=(calls, 2),
                completes_input=True,
                consumed_keys=5,
                model_score=float(calls),
            )
        )
        return 1

    monkeypatch.setattr(manager, "_expand_background_frontier_batch", batch)
    # Later-page ownership is covered separately. Keep this regression focused
    # on the handoff boundary: publication must cancel the broad search and no
    # batch already in flight may replace the immutable first page.
    monkeypatch.setattr(manager, "_maybe_start_page_preparation", lambda session: None)
    with pytest.raises(
        CandidatePageTimeout, match="complete candidate page is still being prepared"
    ):
        _page(engine, client="progressive", revision=1, raw="nihao")
    assert started.wait(3)

    first = _page(engine, client="progressive", revision=1, raw="nihao")
    assert all(
        candidate.completes_input
        for candidate in first.candidates
        if candidate.script == "han"
    )
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
        with manager._state_lock:
            cancel = manager._background_cancel_events[first.candidate_set_id]
            assert cancel.is_set()
        current = manager.query_page(**kwargs)
        assert current.candidate_set_id == first.candidate_set_id
        assert current.candidates == first.candidates
        assert current.candidate_ids == first.candidate_ids
        assert current.has_more is True
        assert not manager.presentation_update_pending(current.candidate_set_id)
        assert len(manager._sessions) == 1
        assert calls == 2
        gate.set()
        assert completion.wait(1.0)
        final = manager.query_page(**kwargs)
        assert final.candidates == first.candidates
        with manager._state_lock:
            pending_text = {
                candidate.text
                for candidate in manager._sessions[first.candidate_set_id].pending
            }
        assert "你好啊啊" in pending_text
        assert calls == 2
    finally:
        gate.set()
        manager.clear_sessions()
