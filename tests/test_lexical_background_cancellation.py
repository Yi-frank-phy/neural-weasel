import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.unified import LatinPrefixConstraint


@pytest.mark.parametrize(
    "event_map", ["_page_zero_lexical_preparations", "_page_preparation_cancel_events"]
)
def test_superseded_lexical_cursor_stops_at_next_checkpoint(make_index, monkeypatch, event_map):
    now = [0.0]
    pages = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index(
            [(i, text, "ni", "ni", 1, 0) for i, text in enumerate("你泥拟逆妮倪霓", 1)]
        ),
        latin_constraint=LatinPrefixConstraint(()),
        clock=lambda: now[0],
    )
    pages.install_baseline_scores(np.arange(8, dtype=np.float32))
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", lambda worker: None)
    page = pages.query_page(
        client_session_id="public-lexical-cancel",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode="chinese_first",
        raw_keys="ni",
        page_index=0,
        candidate_set_id=None,
        state=None,
        deadline_ms=35,
    )
    session = pages._sessions[page.candidate_set_id]
    cancellation = threading.Event()
    getattr(pages, event_map)[page.candidate_set_id] = cancellation
    pages._lexical_completion_cursors.pop(page.candidate_set_id, None)
    pages._lexical_completion_exhausted.discard(page.candidate_set_id)
    advances = []

    def cursor(snapshot):
        while True:
            advances.append(True)
            now[0] += 0.001
            with pages._state_lock:
                pages._cancel_superseded_searches_locked(
                    replace(session.identity, composition_revision=2)
                )
            yield None

    monkeypatch.setattr(pages, "_iter_lexical_completion_candidates", cursor)
    assert not pages._lexical_completion_fallback(session, limit=35, absolute_deadline=0.035)
    assert cancellation.is_set()
    assert pages._sessions[page.candidate_set_id] is session
    assert len(advances) == 1
