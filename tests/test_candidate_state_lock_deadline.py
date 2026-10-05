import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel import neural_candidate_pages as pages
from neural_weasel import neural_candidate_pages_scored as scored
from neural_weasel.neural_candidates import CandidatePageTimeout
from neural_weasel.production_pipe import ProductionNamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint


@pytest.mark.parametrize(
    "manager_type", [pages.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
@pytest.mark.parametrize("page_index", [0, 1])
@pytest.mark.parametrize("mode", ["chinese_first", "latin_first"])
def test_candidate_request_times_out_before_contended_state_lock_is_released(
    manager_type, page_index, mode
):
    manager = manager_type(
        backend=SimpleNamespace(), pinyin_index=None, latin_constraint=LatinPrefixConstraint(())
    )
    manager.install_baseline_scores(np.zeros(8, dtype=np.float32))
    locked = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []

    def hold_background_state():
        with manager._state_lock:
            locked.set()
            release.wait(2)

    def request():
        try:
            manager.query_page(
                client_session_id="public-contention",
                composition_revision=1,
                context_epoch=0,
                context_session=None,
                source_revision=None,
                mode=mode,
                raw_keys="ni",
                page_index=page_index,
                candidate_set_id=None if page_index == 0 else "public-set",
                state=None,
                deadline_ms=35,
                deadline_started=time.perf_counter(),
            )
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    holder = threading.Thread(target=hold_background_state)
    waiter = threading.Thread(target=request)
    holder.start()
    assert locked.wait(1)
    try:
        waiter.start()
        assert finished.wait(0.25), "request deadline must not wait for background unlock"
        assert len(errors) == 1 and isinstance(errors[0], CandidatePageTimeout)
        assert not manager._sessions
    finally:
        release.set()
        holder.join(2)
        waiter.join(2)
    assert not holder.is_alive() and not waiter.is_alive()


@pytest.mark.parametrize("raise_inside", [False, True])
def test_state_deadline_lock_releases_reentrant_acquisitions_on_every_exit(raise_inside):
    manager = pages.NeuralCandidatePageManager(
        backend=SimpleNamespace(), pinyin_index=None, latin_constraint=LatinPrefixConstraint(())
    )
    acquired_elsewhere = []
    try:
        with (
            manager._state_lock_until(time.perf_counter() + 1),
            manager._state_lock_until(time.perf_counter() + 1),
        ):
            if raise_inside:
                raise ValueError("public synthetic failure")
    except ValueError:
        assert raise_inside

    def acquire_from_another_thread():
        acquired = manager._state_lock.acquire(timeout=0.1)
        acquired_elsewhere.append(acquired)
        if acquired:
            manager._state_lock.release()

    thread = threading.Thread(target=acquire_from_another_thread)
    thread.start()
    thread.join(1)
    assert acquired_elsewhere == [True]


def test_response_readiness_lock_wait_uses_the_same_request_deadline():
    manager = pages.NeuralCandidatePageManager(
        backend=SimpleNamespace(), pinyin_index=None, latin_constraint=LatinPrefixConstraint(())
    )
    locked, release, finished = threading.Event(), threading.Event(), threading.Event()
    replies = []

    def hold_background_state():
        with manager._state_lock:
            locked.set()
            release.wait(2)

    holder = threading.Thread(target=hold_background_state)

    def query(**kwargs):
        holder.start()
        assert locked.wait(1)
        return SimpleNamespace(candidate_ids=(), candidates=(), candidate_set_id="public-set")

    server = ProductionNamedPipeServer(
        SimpleNamespace(candidate_pages=manager, query_candidate_page=query),
        pipe_name=r"\\.\pipe\unused-public-lock-test",
    )

    def request():
        try:
            replies.append(
                server.handle_message(
                    {
                        "type": "query_candidate_page",
                        "session_id": "public-lock",
                        "composition_revision": 1,
                        "context_epoch": 0,
                        "language_mode": "chinese_first",
                        "raw_keys": "ni",
                        "page_index": 0,
                    }
                )
            )
        finally:
            finished.set()

    waiter = threading.Thread(target=request)
    waiter.start()
    try:
        assert finished.wait(0.25), "response metadata must not wait for background unlock"
        assert replies[0]["error"]["code"] == "candidate_page_timeout"
        assert replies[0]["error"]["retryable"] is True
    finally:
        release.set()
        holder.join(2)
        waiter.join(2)
    assert not holder.is_alive() and not waiter.is_alive()
