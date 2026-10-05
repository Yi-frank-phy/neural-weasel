import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.candidate import Candidate
from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import (
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _SearchIdentity,
    _SearchSession,
)
from neural_weasel.unified import LatinPrefixConstraint


def _manager(**kwargs):
    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=None,
        latin_constraint=LatinPrefixConstraint(()),
        **kwargs,
    )
    manager.install_baseline_scores(np.zeros(16, dtype=np.float32))
    return manager


def _identity(revision=1):
    return _SearchIdentity(
        "public-bounded-search", revision, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "nihao"
    )


def _candidate(i):
    return Candidate(
        chr(0x4E00 + i), "ni'hao", 5, -float(i), 0, False, True, 2, token_path=(1, i + 2)
    )


def _session(manager, pending=()):
    session = _SearchSession(
        "public-set",
        _identity(),
        "baseline",
        None,
        list(pending),
        [],
        {},
        set(),
        set(),
        manager.clock(),
    )
    manager._sessions[session.candidate_set_id] = session
    manager._session_cancel_tokens[session.candidate_set_id] = manager._admit_search(
        session.identity
    )
    return session


@pytest.mark.parametrize("initial_count", [6, 7])
def test_first_publication_worker_stops_when_seven_candidates_are_freezable(
    monkeypatch,
    initial_count,
):
    now = [0.0]
    manager = _manager(clock=lambda: now[0])
    session = _session(manager, [_candidate(i) for i in range(initial_count)])
    batches = []

    def expand(session, deadline, *, max_parents):
        batches.append(True)
        now[0] += 0.1
        session.pending.append(_candidate(len(session.pending)))
        return 1

    monkeypatch.setattr(manager, "_expand_background_frontier_batch", expand)
    manager._run_background_continuation(
        session,
        manager._async_identity_key(session.identity),
        threading.Event(),
    )
    assert len(batches) == 7 - initial_count, "ready first page must stop broad expansion"
    assert not manager._session_cancel_token(session).is_set()
    if initial_count == 6:
        assert len(manager._async_han_cache[manager._async_identity_key(session.identity)]) == 1


def test_deferred_root_preparation_materializes_only_the_display_capacity(make_index, monkeypatch):
    from neural_weasel.neural_candidate_pages_v3 import _DeferredHanFrontier

    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index([(i, chr(0x4E00 + i), "ni", "ni", 1, 0) for i in range(1, 201)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    manager.install_baseline_scores(np.arange(256, dtype=np.float32))
    identity = replace(_identity(), raw_keys="ni")
    with manager._state_lock:
        session = manager._new_session(identity, None)
    assert any(isinstance(path, _DeferredHanFrontier) for path in session.frontier)
    manager._prepare_page_search(session, threading.Event())
    assert len(session.pending) <= 35, "display allocation must not materialize every legal root"
    assert len(session.frontier) <= 33, "continuation roots must retain a resumable tail"


def test_new_request_cancels_old_work_before_waiting_for_the_state_lock(make_index, monkeypatch):
    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index([(1, "你", "ni", "ni", 1, 0)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    manager.install_baseline_scores(np.zeros(8, dtype=np.float32))
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", lambda worker: None)
    kwargs = dict(
        client_session_id="public-bounded-search",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode="chinese_first",
        raw_keys="ni",
        page_index=0,
        candidate_set_id=None,
        state=None,
    )
    page = manager.query_page(**kwargs)
    session = manager._sessions[page.candidate_set_id]
    # Register a real later-page cancellation event before the new request.
    cancel = manager._page_preparation_cancel_events[session.candidate_set_id]
    locked, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def hold():
        with manager._state_lock:
            locked.set()
            release.wait(2)

    def query():
        try:
            manager.query_page(**{**kwargs, "composition_revision": 2}, deadline_ms=10)
        except CandidatePageTimeout:
            errors.append(True)
        finally:
            finished.set()

    holder, waiter = threading.Thread(target=hold), threading.Thread(target=query)
    holder.start()
    assert locked.wait(1)
    try:
        waiter.start()
        assert finished.wait(0.3)
        assert errors
        assert cancel.is_set(), "new input must cancel old work despite a busy state lock"
    finally:
        release.set()
        holder.join(2)
        waiter.join(2)


def _admitted_session(manager, identity):
    token = manager._admit_search(identity)
    with manager._search_cancellation_scope(token), manager._state_lock:
        session = manager._new_session(identity, None)
    return session, token


def test_delayed_old_worker_does_not_join_the_new_aba_generation(monkeypatch):
    manager = _manager()
    a = _identity()
    old, old_token = _admitted_session(manager, a)
    _admitted_session(manager, _identity(2))
    new, new_token = _admitted_session(manager, a)
    calls = []
    monkeypatch.setattr(
        manager,
        "_expand_background_frontier_batch",
        lambda *args, **kwargs: calls.append(True) or 0,
    )
    delayed_cancel = manager._new_search_cancel_event(old)
    manager._run_background_continuation(old, manager._async_identity_key(a), delayed_cancel)
    assert old_token.is_set() and delayed_cancel.is_set()
    assert manager._session_cancel_token(new) is new_token
    assert not new_token.is_set()
    assert not calls


def test_superseded_query_cannot_cancel_or_register_against_the_new_owner():
    manager = _manager()
    _, old_token = _admitted_session(manager, _identity())
    new, new_token = _admitted_session(manager, _identity(2))
    with (
        manager._search_cancellation_scope(old_token),
        manager._state_lock,
        pytest.raises(CandidatePageTimeout, match="superseded"),
    ):
        manager._cancel_superseded_searches_locked(_identity())
    assert not new_token.is_set()
    assert manager._session_cancel_token(new) is new_token


def test_old_navigation_cannot_admit_itself_again():
    manager = _manager()
    old, _ = _admitted_session(manager, _identity())
    _, new_token = _admitted_session(manager, _identity(2))
    with pytest.raises((CandidatePageError, CandidatePageTimeout)):
        manager.query_page(
            client_session_id=old.identity.client_session_id,
            composition_revision=1,
            context_epoch=0,
            context_session=None,
            source_revision=None,
            mode="chinese_first",
            raw_keys="nihao",
            page_index=1,
            candidate_set_id=old.candidate_set_id,
            state=None,
        )
    assert manager._latest_searches[old.identity.client_session_id][1] is new_token
    assert not new_token.is_set()


def test_clear_and_eviction_cancel_captured_tokens():
    manager = _manager()
    session, token = _admitted_session(manager, _identity())
    stage = manager._new_search_cancel_event(session)
    stage.set()
    stage.clear()
    assert not stage.is_set()
    manager.clear_sessions()
    stage.clear()
    assert token.is_set() and stage.is_set()
    assert not manager._session_cancel_tokens
    assert not manager._latest_searches
    session, token = _admitted_session(manager, _identity())
    stage = manager._new_search_cancel_event(session)
    for i in range(4):
        manager._admit_search(replace(_identity(), client_session_id=f"public-client-{i}"))
    assert token.is_set() and stage.is_set()
    with manager._state_lock:
        manager._expire_sessions()
    assert session.candidate_set_id not in manager._sessions
    assert session.candidate_set_id not in manager._session_cancel_tokens


@pytest.mark.parametrize("prepare", [True, False])
def test_deferred_continuation_keeps_roots_beyond_the_visible_top_35(make_index, prepare):
    from neural_weasel.neural_candidate_pages_v3 import _DeferredRootSeeds

    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index([(i, chr(0x4E00 + i), "ni", "ni", 1, 0) for i in range(1, 201)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    manager.install_baseline_scores(np.arange(256, dtype=np.float32))
    session, _ = _admitted_session(manager, replace(_identity(), raw_keys="ni"))
    if prepare:
        assert manager._prepare_page_search(session, manager._new_search_cancel_event(session))
    else:
        manager._prune_frontier(session)
    visible = {c.token_path for c in session.pending}
    all_roots = set()
    while session.frontier:
        work = session.frontier.pop(0)
        if isinstance(work, _DeferredRootSeeds):
            manager._resume_root_han_frontier(session, work)
        else:
            all_roots.add(work.token_path)
    assert len(visible) <= 35 < len(all_roots)
    assert visible <= all_roots
    assert all_roots - visible, "low-ranked roots must remain eligible for continuation"


def test_idle_wait_observes_supersession_without_the_state_lock():
    manager = _manager()
    session, token = _admitted_session(manager, _identity())
    registered, finished = threading.Event(), threading.Event()
    unregistered, result = [], []
    manager.backend.register_continuation_idle_wait = lambda generation, wake: registered.set()
    manager.backend.cancel_continuation_idle_wait = unregistered.append
    cancel = manager._new_search_cancel_event(session)

    def wait():
        result.append(manager._wait_for_continuation_idle(cancel, threading.Event(), 1))
        finished.set()

    worker = threading.Thread(target=wait, daemon=True)
    worker.start()
    assert registered.wait(1)
    with manager._state_lock:
        token.set()
        assert finished.wait(0.3), "supersession must interrupt a backend idle wait"
    worker.join(1)
    assert result == [False]
    assert len(unregistered) == 1


@pytest.mark.parametrize("cancel_at", ["provider", "scratch"])
def test_obsolete_batch_never_merges_a_late_result(monkeypatch, cancel_at):
    from neural_weasel.neural_candidate_pages_v3 import _HanSearchPath

    manager = _manager()
    session, token = _admitted_session(manager, _identity())
    session.continuation_root = "public-root"
    session.pending.clear()
    session.frontier = [_HanSearchPath("你", ("ni",), (1,), -1.0, 0, 2)]
    manager._all_model_token_ids = (1, 2, 3)
    monkeypatch.setattr(manager, "_resume_scored_han_frontier", lambda *args: None)
    monkeypatch.setattr(manager, "_prune_frontier", lambda *args: None)
    monkeypatch.setattr(manager, "_han_edges_for", lambda *args: {2: ()})

    def provider(*args, **kwargs):
        if cancel_at == "provider":
            manager._admit_search(_identity(2))
        return [np.zeros(3, dtype=np.float32)]

    def expand(scratch, *args):
        scratch.pending.append(_candidate(0))
        manager._admit_search(_identity(2))
        return 1

    manager.backend.continue_from_root = provider
    monkeypatch.setattr(manager, "_expand_han_constrained", expand)
    with (
        manager._search_cancellation_scope(token),
        manager._state_lock,
        pytest.raises(CandidatePageTimeout, match="superseded"),
    ):
        manager._expand_background_frontier_batch(session, manager.clock() + 1, max_parents=1)
    assert not session.pending
    assert not manager._async_han_cache
