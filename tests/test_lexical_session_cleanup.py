from __future__ import annotations

import threading

import numpy as np
import pytest

from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import NeuralLanguageMode, _SearchIdentity
from neural_weasel.unified import LatinPrefixConstraint


def _manager_and_session(make_index):
    manager = NeuralCandidatePageManager(
        pinyin_index=make_index([(1, "你", "ni", 1, 0), (2, "好", "hao", 1, 0)]),
        latin_constraint=LatinPrefixConstraint(()),
        backend=None,
    )
    manager.install_baseline_scores(np.zeros(3, dtype=np.float32))
    identity = _SearchIdentity(
        client_session_id="cleanup",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    return manager, manager._new_session(identity, None)


def test_public_clear_sessions_releases_lexical_state(make_index):
    manager, session = _manager_and_session(make_index)
    manager._lexical_completion_cursors[session.candidate_set_id] = iter([None])
    manager._lexical_completion_exhausted.add("previously-exhausted")

    manager.clear_sessions()

    assert not manager._sessions
    assert not manager._lexical_completion_cursors
    assert not manager._lexical_completion_exhausted


@pytest.mark.parametrize("pause_at", ["create", "exhaust"])
def test_late_lexical_worker_cannot_repopulate_cleared_state(make_index, monkeypatch, pause_at):
    manager, session = _manager_and_session(make_index)
    paused, release = threading.Event(), threading.Event()

    def exhaust():
        paused.set()
        assert release.wait(2)
        yield from ()

    def create(_snapshot):
        if pause_at == "create":
            paused.set()
            assert release.wait(2)
            return iter(())
        return exhaust()

    monkeypatch.setattr(manager, "_iter_lexical_completion_candidates", create)
    failures = []

    def run():
        try:
            manager._lexical_completion_fallback(
                session,
                limit=1,
                absolute_deadline=manager.clock() + 2,
            )
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert paused.wait(1)
        manager.clear_sessions()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not failures
    assert not manager._lexical_completion_cursors
    assert not manager._lexical_completion_exhausted
