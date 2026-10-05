from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.backends import ContinuationAttempt, FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import (
    CandidatePageTimeout,
    NeuralLanguageMode,
    _SearchIdentity,
)
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


def test_ready_first_page_does_not_wait_for_eighth_lexical_candidate(make_index, monkeypatch):
    """Slow later-page discovery must not withhold an already complete first page."""
    rows = [
        *[(i, text, "ni", "ni", 1, 0) for i, text in enumerate("你泥拟逆妮倪", 1)],
        *[(i, text, "hao", "hao", 1, 0) for i, text in enumerate("好号浩豪毫郝皓", 11)],
    ]
    scores = np.arange(32, dtype=np.float32) * -0.01
    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-baseline",)
        ),
        continue_from_root=lambda *args, **kwargs: [],
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        "public-page-zero", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "nihao"
    )
    tail_started = threading.Event()
    release_tail = threading.Event()
    original = manager._lexical_completion_fallback

    def deferred_foreground(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            return []
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_lexical_completion_fallback", deferred_foreground)
    monkeypatch.setattr(manager, "_start_background_continuation", lambda session: None)
    with manager._state_lock:
        session = manager._new_session(identity, None)
        cursor = manager._iter_lexical_completion_candidates(session)

        def stalled_later_page():
            count = 0
            for candidate in cursor:
                if candidate is not None:
                    count += 1
                    if count == 8:
                        tail_started.set()
                        assert release_tail.wait(3.0)
                yield candidate

        manager._lexical_completion_cursors[session.candidate_set_id] = stalled_later_page()
        with pytest.raises(CandidatePageTimeout):
            manager._freeze_next_page(session, 0, 7, manager.clock() + 0.035)
    try:
        assert tail_started.wait(2.0), "fixture did not reach later-page discovery"
        with manager._state_lock:
            first = session.frozen_pages.get(0)
        assert first is not None, "seven ready candidates were withheld by later-page work"
        assert len(first.candidates) == 7
        assert all(candidate.completes_input for candidate in first.candidates)
    finally:
        release_tail.set()
        manager.clear_sessions()


def test_same_identity_recovers_after_one_empty_first_page_continuation(make_index, monkeypatch):
    """A temporary scorer miss is not proof that valid shorthand paths are exhausted."""
    scores = np.arange(32, dtype=np.float32) * -0.01
    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-baseline",)
        ),
        continue_from_root=lambda root, paths, allowed, **kwargs: [
            scores[np.asarray(tokens)] for tokens in allowed
        ],
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(
            make_index([(1, "你", "ni", "ni", 1, 0), (2, "好", "hao", "hao", 1, 0)])
        ),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    backend = manager.backend
    original_attempt = backend._continue_from_root_attempt
    missed = threading.Event()
    calls = 0

    def miss_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            missed.set()
            return ContinuationAttempt()
        return original_attempt(*args, **kwargs)

    monkeypatch.setattr(backend, "_continue_from_root_attempt", miss_once)

    def query():
        return engine.query_candidate_page(
            client_session_id="public-retry",
            composition_revision=1,
            context_epoch=0,
            context_session=None,
            source_revision=None,
            language_mode="chinese_first",
            raw_keys="nh",
            page_index=0,
        )

    try:
        with pytest.raises(CandidatePageTimeout):
            query()
        assert missed.wait(1.0)
        retirement_deadline = time.monotonic() + 1.0
        while time.monotonic() < retirement_deadline:
            with manager._state_lock:
                if not manager._background_searches:
                    break
            time.sleep(0.005)
        deadline = time.monotonic() + 1.0
        first = None
        while time.monotonic() < deadline:
            try:
                first = query()
                break
            except CandidatePageTimeout:
                time.sleep(0.005)
        assert first is not None
        assert any(c.script == "han" and c.completes_input for c in first.candidates), (
            "a single empty attempt permanently froze the literal fallback"
        )
        assert calls >= 2
    finally:
        manager.clear_sessions()


def test_impossible_shorthand_tail_terminates_without_restarting_workers(make_index):
    """A proven dead lexical edge must not be retried as a transient scorer miss."""
    scores = np.arange(8, dtype=np.float32) * -0.01
    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-baseline",)
        ),
        continue_from_root=lambda *args, **kwargs: pytest.fail("impossible tail was scored"),
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(
            make_index([(1, "你", "ni", "ni", 1, 0), (2, "好", "hao", "hao", 1, 0)])
        ),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    first = None
    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                first = engine.query_candidate_page(
                    client_session_id="public-dead-tail",
                    composition_revision=1,
                    context_epoch=0,
                    context_session=None,
                    source_revision=None,
                    language_mode="chinese_first",
                    raw_keys="nq",
                    page_index=0,
                )
                break
            except CandidatePageTimeout:
                time.sleep(0.005)
        assert first is not None, "proven dead edges kept restarting first-page workers"
        assert all(candidate.script != "han" for candidate in first.candidates)
    finally:
        engine.candidate_pages.clear_sessions()


def _public_shorthand_manager(make_index):
    scores = np.arange(8, dtype=np.float32) * -0.01
    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-baseline",)
        ),
        continue_from_root=lambda *args, **kwargs: [],
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(
            make_index([(1, "你", "ni", "ni", 1, 0), (2, "好", "hao", "hao", 1, 0)])
        ),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        "public-lock-budget", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "nh"
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
    return manager, session


def test_completed_neural_first_page_is_not_hidden_by_shallower_frontier(make_index, monkeypatch):
    """A bounded first snapshot must publish completed paths, not await a global beam proof."""
    rows = [
        (1, "你", "ni", "ni", 1, 0),
        (None, "年", "nian", "nian", 1, 1, (2, 3)),
        *[
            (i, text + "问题", "haowenti", "hao'wen'ti", 3, 0)
            for i, text in enumerate("好号浩豪毫郝皓", 11)
        ],
    ]
    scores = np.arange(32, dtype=np.float32) * -0.01
    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-baseline",)
        ),
        continue_from_root=lambda root, paths, allowed, **kwargs: [
            scores[list(ids)] for ids in allowed
        ],
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    monkeypatch.setattr(manager, "_start_background_continuation", lambda session: None)
    identity = _SearchIdentity(
        "public-neural-snapshot", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "nhwt"
    )
    try:
        with manager._state_lock:
            session = manager._new_session(identity, None)
            deadline = manager.clock() + 1.0
            while not any(c.completes_input and c.script == "han" for c in session.pending):
                assert manager.clock() < deadline, "fixture produced no complete neural path"
                assert manager._expand_background_frontier_batch(session, deadline, max_parents=1)
            assert not session.exhausted and session.frontier
            assert manager._minimum_future_bucket(session) <= min(
                c.predicted_syllables
                for c in session.pending
                if c.completes_input and c.script == "han"
            ), "fixture no longer retains a shallower competing root"
            try:
                page = manager._freeze_next_page(session, 0, 7, manager.clock() + 0.035)
            except CandidatePageTimeout:
                pytest.fail("completed neural candidates were hidden by shallower search roots")
            assert len(page.candidates) == 7
            assert all(c.script == "han" and c.completes_input for c in page.candidates)
            assert all(c.constraint_kind == "pinyin" for c in page.candidates)
            # Later search keeps its conservative bucket rule and cannot mutate
            # the already published first snapshot.
            frozen = page.candidates
            manager._expand_background_frontier_batch(session, manager.clock() + 1, max_parents=1)
            assert session.frozen_pages[0].candidates == frozen
    finally:
        manager.clear_sessions()


def test_continuation_batches_allow_waiting_foreground_between_cpu_steps(make_index, monkeypatch):
    from neural_weasel import neural_candidate_pages_scored as scored

    monkeypatch.setattr(scored, "_BACKGROUND_STATE_LOCK_SLICE_MS", 0.0)
    manager, session = _public_shorthand_manager(make_index)
    first_step = threading.Event()
    reader_waiting = threading.Event()
    reader_acquired = threading.Event()
    observed = []
    calls = 0

    def cpu_step(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_step.set()
            reader_waiting.wait(1.0)
            return 1
        observed.append(reader_acquired.wait(0.15))
        session.exhausted = True
        return 0

    def foreground():
        reader_waiting.set()
        with manager._state_lock:
            reader_acquired.set()

    monkeypatch.setattr(manager, "_expand_background_frontier_batch", cpu_step)
    cancel = threading.Event()
    worker = threading.Thread(
        target=manager._run_background_continuation,
        args=(session, manager._async_identity_key(session.identity), cancel),
    )
    worker.start()
    assert first_step.wait(1.0)
    reader = threading.Thread(target=foreground)
    reader.start()
    try:
        worker.join(1.0)
        reader.join(1.0)
        assert observed == [True], "successive CPU batches held the foreground state lock"
    finally:
        cancel.set()
        worker.join(1.0)
        reader.join(1.0)
        manager.clear_sessions()


def test_continuation_idle_wait_respects_worker_deadline(make_index, monkeypatch):
    from neural_weasel import neural_candidate_pages_scored as scored

    manager, session = _public_shorthand_manager(make_index)
    registered = threading.Event()
    unregistered = threading.Event()
    cancel = threading.Event()
    monkeypatch.setattr(scored, "_BACKGROUND_CONTINUATION_DEADLINE_MS", 20.0)

    def busy_step(*args, **kwargs):
        manager._background_retry_generations[session.candidate_set_id] = 1
        return 0

    monkeypatch.setattr(manager, "_expand_background_frontier_batch", busy_step)
    monkeypatch.setattr(
        manager.backend, "register_continuation_idle_wait", lambda *a: registered.set()
    )
    monkeypatch.setattr(
        manager.backend, "cancel_continuation_idle_wait", lambda *a: unregistered.set()
    )
    worker = threading.Thread(
        target=manager._run_background_continuation,
        args=(session, manager._async_identity_key(session.identity), cancel),
    )
    worker.start()
    try:
        assert registered.wait(1.0)
        worker.join(0.15)
        assert not worker.is_alive(), "an occupied provider lane outlived the worker deadline"
        assert unregistered.is_set()
        assert not session.exhausted
        assert session.candidate_set_id not in manager._page_zero_search_completed
    finally:
        cancel.set()
        worker.join(1.0)
        manager.clear_sessions()


@pytest.mark.parametrize("invalidate", [False, True])
def test_later_page_cpu_search_yields_to_foreground(make_index, monkeypatch, invalidate):
    """Exercise the preparer's real query route, including cancellation during a yield."""
    from neural_weasel import neural_candidate_pages as pages

    monkeypatch.setattr(pages, "_BACKGROUND_PAGE_STATE_LOCK_SLICE_MS", 0.0, raising=False)
    manager, session = _public_shorthand_manager(make_index)
    first_step = threading.Event()
    reader_waiting = threading.Event()
    reader_acquired = threading.Event()
    done, cancel, wake = threading.Event(), threading.Event(), threading.Event()
    observed = []
    calls = 0
    sid = session.candidate_set_id
    session.frozen_pages[0] = SimpleNamespace(candidates=(), has_more=True)
    manager._page_preparations.add(sid)
    manager._page_preparation_cancel_events[sid] = cancel
    monkeypatch.setattr(manager, "_freezable_candidates", lambda session: [])
    monkeypatch.setattr(manager, "_lexical_completion_fallback", lambda *a, **k: [])
    monkeypatch.setattr(manager, "_sort_pending", lambda session: None)

    def cpu_step(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_step.set()
            assert reader_waiting.wait(1.0)
            return 1
        observed.append(reader_acquired.wait(0.15))
        session.exhausted = True
        return 0

    def foreground():
        reader_waiting.set()
        with manager._state_lock:
            if invalidate:
                manager.clear_sessions()
            reader_acquired.set()

    monkeypatch.setattr(manager, "_expand_one_frontier", cpu_step)
    worker = threading.Thread(
        target=manager._run_page_preparation, args=(session, done, cancel, wake)
    )
    reader = threading.Thread(target=foreground)
    worker.start()
    try:
        assert first_step.wait(1.0)
        reader.start()
        worker.join(1.0)
        reader.join(1.0)
        assert not worker.is_alive() and done.is_set()
        if invalidate:
            assert calls == 1, "invalidated later-page search continued after yielding"
            assert sid not in manager._sessions
            assert 1 not in session.frozen_pages
        else:
            assert observed == [True], "later-page CPU steps monopolized the foreground lock"
    finally:
        cancel.set()
        worker.join(1.0)
        if reader.ident is not None:
            reader.join(1.0)
        manager.clear_sessions()
