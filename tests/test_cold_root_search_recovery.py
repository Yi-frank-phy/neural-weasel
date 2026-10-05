import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import MAX_ACTIVE_SEARCH_SESSIONS, CandidatePageTimeout
from neural_weasel.response_workers import after_response, start_worker
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint


def test_cold_scan_that_fits_request_budget_publishes_without_retry(make_index, monkeypatch):
    now = [0.0]
    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index(
            [(i, text, "ni", "ni", 1, 0) for i, text in enumerate("你泥拟逆妮倪霓", 1)]
        ),
        latin_constraint=LatinPrefixConstraint(()),
        clock=lambda: now[0],
    )
    manager.install_baseline_scores(np.arange(8, dtype=np.float32))
    original = manager.matcher.iter_neural_matches
    workers = []

    def resumable(*args, **kwargs):
        now[0] += 0.010
        yield None
        yield from original(*args, **kwargs)

    monkeypatch.setattr(manager.matcher, "iter_neural_matches", resumable)
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", workers.append)
    page = _query(manager)
    assert len(page.candidates) == 7
    assert all(candidate.completes_input for candidate in page.candidates)
    assert not manager._root_plan_worker_active and not manager._root_plan_preparations
    assert now[0] < 0.035


def test_cold_scan_resumes_once_and_scores_the_retry_snapshot(make_index, monkeypatch):
    rows = [(i, text, "ni", "ni", 1, 0) for i, text in enumerate("你泥拟逆妮倪霓", 1)]
    now = [0.0]
    calls = []

    class Backend:
        def score_allowed_tokens(self, state, ids):
            assert state.valid, "background work must not score an expired context snapshot"
            calls.append(state)
            return state.payload[np.asarray(ids)]

    manager = NeuralCandidatePageManager(
        backend=Backend(),
        pinyin_index=make_index(rows),
        latin_constraint=LatinPrefixConstraint(()),
        clock=lambda: now[0],
    )
    manager.install_baseline_scores(np.arange(8, dtype=np.float32))
    original = manager.matcher.iter_neural_matches
    expected_matches = manager.matcher.neural_matches("ni")
    manager.matcher._neural_cache.clear()
    scans = []
    entered = threading.Event()
    release = threading.Event()

    def resumable(*args, **kwargs):
        scans.append(args[0])
        now[0] += 0.040
        yield None
        entered.set()
        assert release.wait(2)
        yield from original(*args, **kwargs)

    def old_blocking(*args, **kwargs):
        now[0] += 0.040
        return expected_matches

    monkeypatch.setattr(manager.matcher, "iter_neural_matches", resumable)
    monkeypatch.setattr(manager.matcher, "neural_matches", old_blocking)
    initial = SimpleNamespace(
        payload=np.arange(8, dtype=np.float32), valid=True, log_normalizer=None
    )
    retry = SimpleNamespace(
        payload=-np.arange(8, dtype=np.float32), valid=True, log_normalizer=None
    )
    request = dict(
        client_session_id="public-cold",
        composition_revision=1,
        context_epoch=7,
        context_session="public-context",
        source_revision=1,
        mode="chinese_first",
        raw_keys="ni",
        page_index=0,
        candidate_set_id=None,
        deadline_ms=35,
    )
    try:
        with pytest.raises(CandidatePageTimeout):
            manager.query_page(**request, state=initial)
        assert entered.wait(1), "cold root work was discarded instead of resumed"
        initial.valid = False
        with pytest.raises(CandidatePageTimeout):
            manager.query_page(**request, state=retry)
        assert scans == ["ni"], "same identity restarted the scan"
        assert not calls, "root preparation must retain only static phonetic work"
        release.set()
        until = time.monotonic() + 2
        while time.monotonic() < until:
            with manager._state_lock:
                if "ni" in manager._root_han_plans:
                    break
            time.sleep(0.001)
        page = manager.query_page(**request, state=retry)
        assert page.score_source == "context"
        assert page.candidates[0].text == "你"
        assert all(candidate.context_epoch == 7 for candidate in page.candidates)
        assert calls and all(state is retry for state in calls)
        assert scans == ["ni"]
    finally:
        release.set()
        manager.clear_sessions()


def _queued_manager(make_index, monkeypatch, *, latin_constraint=None):
    now = [0.0]
    workers = []
    manager = NeuralCandidatePageManager(
        backend=SimpleNamespace(),
        pinyin_index=make_index([(1, "你", "ni", "ni", 1, 0)]),
        latin_constraint=latin_constraint or LatinPrefixConstraint(()),
        clock=lambda: now[0],
    )
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", workers.append)

    def cursor(raw_keys):
        now[0] += 0.040
        yield None
        return ()

    monkeypatch.setattr(manager, "_iter_root_han_plan", cursor)
    return manager, now, workers


def _query(manager, *, client="public-queued", revision=1, raw="ni", mode="chinese_first"):
    return manager.query_page(
        client_session_id=client,
        composition_revision=revision,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=mode,
        raw_keys=raw,
        page_index=0,
        candidate_set_id=None,
        state=None,
        deadline_ms=35,
    )


def test_latin_first_does_not_enter_resumable_han_preparation(make_index, monkeypatch):
    manager, _, workers = _queued_manager(
        make_index,
        monkeypatch,
        latin_constraint=LatinPrefixConstraint((LatinCompletion("debug", (1,)),)),
    )
    manager.install_baseline_scores(np.arange(8, dtype=np.float32))
    monkeypatch.setattr(manager, "_root_han_candidates_and_frontier", lambda *args: ([], []))
    page = _query(manager, raw="de", mode="latin_first")
    assert page.candidates[0].text == "debug"
    assert not workers and not manager._root_plan_preparations


def test_deferred_thread_start_failure_allows_next_response_to_restart(make_index, monkeypatch):
    manager, _, _ = _queued_manager(make_index, monkeypatch)
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", start_worker)
    starts = []

    def fail_once(worker):
        starts.append(worker)
        if len(starts) == 1:
            raise RuntimeError("synthetic deferred thread start failure")
        worker.run()

    monkeypatch.setattr(threading.Thread, "start", fail_once)
    with (
        pytest.raises(RuntimeError, match="synthetic deferred thread start failure"),
        after_response(),
        pytest.raises(CandidatePageTimeout),
    ):
        _query(manager)
    assert not manager._root_plan_worker_active, "failed dispatch left search marked running"
    assert len(manager._root_plan_preparations) == 1
    with after_response(), pytest.raises(CandidatePageTimeout):
        _query(manager)
    assert len(starts) == 2
    assert "ni" in manager._root_han_plans
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active


def test_new_identity_cancels_cold_cursor_before_cache_publication(make_index, monkeypatch):
    manager, _, workers = _queued_manager(make_index, monkeypatch)
    with pytest.raises(CandidatePageTimeout):
        _query(manager)
    old = next(iter(manager._root_plan_preparations.values()))
    with pytest.raises(CandidatePageTimeout):
        _query(manager, revision=2, raw="hao")
    assert old.cancel.is_set()
    assert len(workers) == 1, "pending identities created multiple workers"
    manager._run_root_plan_preparations()
    assert "ni" not in manager._root_han_plans
    assert manager._root_han_plans["hao"] == ()
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active


def test_clear_and_expiry_discard_static_work(make_index, monkeypatch):
    manager, now, _ = _queued_manager(make_index, monkeypatch)
    with pytest.raises(CandidatePageTimeout):
        _query(manager)
    manager.clear_sessions()
    manager._run_root_plan_preparations()
    assert "ni" not in manager._root_han_plans
    with pytest.raises(CandidatePageTimeout):
        _query(manager, revision=2)
    now[0] += 2.501
    manager._run_root_plan_preparations()
    assert "ni" not in manager._root_han_plans
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active


@pytest.mark.parametrize("during_close", [False, True])
def test_broken_cursor_does_not_wedge_following_input(make_index, monkeypatch, during_close):
    manager, now, workers = _queued_manager(make_index, monkeypatch)
    original = manager._iter_root_han_plan

    def broken(raw_keys):
        if raw_keys != "ni":
            return (yield from original(raw_keys))
        try:
            now[0] += 0.040
            yield None
            if not during_close:
                raise LookupError("public synthetic iterator failure")
            now[0] += 3.0
            yield None
        finally:
            if during_close:
                raise OSError("public synthetic cleanup failure")

    monkeypatch.setattr(manager, "_iter_root_han_plan", broken)
    with pytest.raises(CandidatePageTimeout):
        _query(manager, client="public-broken")
    with pytest.raises(CandidatePageTimeout):
        _query(manager, client="public-next", raw="hao")
    manager._run_root_plan_preparations()
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active
    assert "ni" not in manager._root_han_plans
    # A cursor that expires also advances the shared clock past the queued
    # job's deadline. A new identity must still be able to start another worker.
    with pytest.raises(CandidatePageTimeout):
        _query(manager, revision=3, raw="ma")
    assert len(workers) == 2
    manager._run_root_plan_preparations()
    assert manager._root_han_plans["ma"] == ()


def test_queue_bounds_include_running_and_cancelled_jobs(make_index, monkeypatch):
    manager, _, workers = _queued_manager(make_index, monkeypatch)
    for revision in range(MAX_ACTIVE_SEARCH_SESSIONS):
        with pytest.raises(CandidatePageTimeout):
            _query(manager, revision=revision)
    with pytest.raises(CandidatePageTimeout, match="queue is busy"):
        _query(manager, revision=MAX_ACTIVE_SEARCH_SESSIONS)
    assert len(manager._root_plan_preparations) == MAX_ACTIVE_SEARCH_SESSIONS
    assert len(workers) == 1
    manager.clear_sessions()
    manager._run_root_plan_preparations()
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active


def test_worker_registration_failure_can_retry(make_index, monkeypatch):
    manager, _, workers = _queued_manager(make_index, monkeypatch)

    def failed_start(worker):
        raise RuntimeError("public synthetic worker registration failure")

    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", failed_start)
    with pytest.raises(RuntimeError, match="registration failure"):
        _query(manager)
    assert not manager._root_plan_preparations and not manager._root_plan_worker_active
    monkeypatch.setattr("neural_weasel.neural_candidate_pages.start_worker", workers.append)
    with pytest.raises(CandidatePageTimeout):
        _query(manager)
    manager._run_root_plan_preparations()
    assert manager._root_han_plans["ni"] == ()
