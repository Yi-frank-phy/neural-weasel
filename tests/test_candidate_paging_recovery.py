from __future__ import annotations

import threading
import time

import numpy as np
from test_candidate_background_readiness import BlockingContinuationRuntime

from neural_weasel.backends import FullLogitsSnapshotBackend
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.production_pipe import ProductionNamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


def test_page_preparation_retries_when_continuation_finished_before_wait_registration(
    make_index,
) -> None:
    index = make_index([(1, "你", "ni", "ni", 1, 0)])
    logits = np.full(4, -20.0, dtype=np.float32)
    runtime = BlockingContinuationRuntime(logits)
    backend = FullLogitsSnapshotBackend(runtime)
    engine = BilingualImeEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )

    # The page search captured generation 0 while continuation was busy. It
    # completed before the preparation worker registered its waiter, so exact
    # generation registration must wake immediately rather than lose the edge.
    assert engine.candidate_pages._wait_for_continuation_idle(
        threading.Event(), threading.Event(), 0
    )


def test_chinese_page_one_prepares_without_replacing_frozen_page_zero(make_index) -> None:
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 9.0
    runtime = BlockingContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    server = ProductionNamedPipeServer(
        engine,
        pipe_name=r"\.\pipe\NeuralWeasel-paging-recovery-test",
    )
    request = {
        "type": "query_candidate_page",
        "session_id": "explicit-navigation",
        "composition_revision": 1,
        "context_epoch": 0,
        "language_mode": "chinese_first",
        "raw_keys": "nihao",
        "page_index": 0,
    }

    pending = server.handle_message(request)
    assert pending["ok"] is False
    assert pending["error"]["retryable"] is True
    assert runtime.started.wait(0.5)
    manager = engine.candidate_pages
    candidate_set_id = next(iter(manager._sessions))
    continuation = manager._background_search_events[candidate_set_id]

    runtime.release.set()
    assert continuation.wait(1.0)
    first = server.handle_message(request)
    assert first["ok"] is True
    assert "你好" in {item["text"] for item in first["candidates"]}
    replay = server.handle_message(dict(request, presentation_refresh=True))
    assert replay["candidates"] == first["candidates"]
    assert replay["candidate_set_id"] == first["candidate_set_id"]

    deadline = time.monotonic() + 1.0
    preparation = manager._page_preparation_events.get(first["candidate_set_id"])
    while preparation is None and time.monotonic() < deadline:
        time.sleep(0.01)
        preparation = manager._page_preparation_events.get(first["candidate_set_id"])
    assert preparation is not None, "frozen page zero must not starve page-one preparation"
    assert preparation.wait(1.0)

    assert all(item["completes_input"] for item in first["candidates"] if item["script"] == "han")
