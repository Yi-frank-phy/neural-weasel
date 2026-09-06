from __future__ import annotations

import threading
import time

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.production_pipe import ProductionNamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


class BlockingPageRuntime:
    def __init__(self, logits: np.ndarray) -> None:
        self.logits = logits
        self.started = threading.Event()
        self.release = threading.Event()
        self.continuation_calls = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        assert root[0] == "root"
        assert deadline_ms > 0
        self.continuation_calls += 1
        if self.continuation_calls == 1:
            self.started.set()
            assert self.release.wait(2.0)
        outputs = []
        for path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            assert tuple(path)
            allowed = tuple(int(token_id) for token_id in allowed)
            values = np.full(len(allowed), -20.0, dtype=np.float32)
            for token_id in range(1, 11):
                if token_id in allowed:
                    values[allowed.index(token_id)] = 20.0 - token_id * 0.1
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine(make_index):
    han = ("你", "泥", "拟", "逆", "妮", "倪", "霓", "腻", "匿", "溺")
    index = make_index(
        [(token_id, text, "ni", "ni", 1, 0) for token_id, text in enumerate(han, start=1)]
    )
    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 11):
        logits[token_id] = 20.0 - token_id * 0.1
    runtime = BlockingPageRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    server = ProductionNamedPipeServer(
        engine,
        pipe_name=r"\.\pipe\NeuralWeasel-background-pages-test",
    )
    return engine, runtime, server


def _request(**overrides):
    request = {
        "type": "query_candidate_page",
        "session_id": "page-client",
        "composition_revision": 1,
        "context_epoch": 0,
        "language_mode": "chinese_first",
        "raw_keys": "ni",
        "page_index": 0,
    }
    request.update(overrides)
    return request


def test_page_down_never_starts_model_work_and_reads_prepared_page(make_index) -> None:
    engine, runtime, server = _engine(make_index)
    first = server.handle_message(_request())
    assert first["ok"] is True
    assert first["has_more"] is True
    assert runtime.started.wait(0.5)

    calls_while_background_blocked = runtime.continuation_calls
    not_ready = server.handle_message(
        _request(
            page_index=1,
            candidate_set_id=first["candidate_set_id"],
        )
    )
    assert not_ready["ok"] is False
    assert not_ready["error"]["code"] == "candidate_page_timeout"
    assert runtime.continuation_calls == calls_while_background_blocked

    runtime.release.set()
    deadline = time.monotonic() + 2.0
    ready = False
    while time.monotonic() < deadline:
        with engine.candidate_pages._state_lock:
            session = engine.candidate_pages._sessions.get(first["candidate_set_id"])
            ready = session is not None and 1 in session.frozen_pages
        if ready:
            break
        time.sleep(0.01)
    assert ready is True

    calls_after_background_prepare = runtime.continuation_calls
    page1 = server.handle_message(
        _request(
            page_index=1,
            candidate_set_id=first["candidate_set_id"],
        )
    )
    assert page1["ok"] is True
    assert page1["page_index"] == 1
    assert page1["candidates"]
    assert runtime.continuation_calls == calls_after_background_prepare

    replay = server.handle_message(
        _request(
            page_index=1,
            candidate_set_id=first["candidate_set_id"],
        )
    )
    assert replay["ok"] is True
    assert tuple(item["candidate_id"] for item in replay["candidates"]) == tuple(
        item["candidate_id"] for item in page1["candidates"]
    )
    assert runtime.continuation_calls == calls_after_background_prepare

    engine.invalidate_candidate_sessions()
