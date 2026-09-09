from __future__ import annotations

import threading

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.production_pipe import ProductionNamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


class BlockingContinuationRuntime:
    def __init__(self, logits: np.ndarray) -> None:
        self.logits = logits
        self.started = threading.Event()
        self.release = threading.Event()

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
        self.started.set()
        assert self.release.wait(2.0)
        outputs = []
        for path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            assert tuple(path)
            allowed = tuple(int(token_id) for token_id in allowed)
            values = np.full(len(allowed), -20.0, dtype=np.float32)
            if 2 in allowed:
                values[allowed.index(2)] = 20.0
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def test_candidate_page_reports_background_readiness_for_exact_identity(make_index) -> None:
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
        pipe_name=r"\.\pipe\NeuralWeasel-background-readiness-test",
    )
    request = {
        "type": "query_candidate_page",
        "session_id": "same-client",
        "composition_revision": 1,
        "context_epoch": 0,
        "language_mode": "chinese_first",
        "raw_keys": "nihao",
        "page_index": 0,
    }

    first = server.handle_message(request)
    assert first["ok"] is True
    assert first["has_more"] is True
    assert first["background_pending"] is False
    assert first["presentation_refresh"] is False
    assert runtime.started.wait(0.5)
    completion = engine.candidate_pages._background_search_events.get(first["candidate_set_id"])
    assert completion is not None

    runtime.release.set()
    assert completion.wait(1.0)

    replay = server.handle_message(request)
    assert replay["ok"] is True
    assert replay["candidate_set_id"] == first["candidate_set_id"]
    assert replay["background_pending"] is False
    assert tuple(item["candidate_id"] for item in replay["candidates"]) == tuple(
        item["candidate_id"] for item in first["candidates"]
    )

    refreshed_request = dict(request, presentation_refresh=True)
    refreshed = server.handle_message(refreshed_request)
    assert refreshed["ok"] is True
    assert refreshed["background_pending"] is False
    assert refreshed["presentation_refresh"] is True
    assert refreshed["candidate_set_id"] == first["candidate_set_id"]
    assert refreshed["candidates"] == first["candidates"]
    assert refreshed["has_more"] is True

    manager = engine.candidate_pages
    preparation = manager._page_preparation_events.get(first["candidate_set_id"])
    assert preparation is not None
    assert preparation.wait(1.0)
    later = server.handle_message(
        dict(request, page_index=1, candidate_set_id=first["candidate_set_id"])
    )
    assert later["ok"] is True
    assert "你好" in {item["text"] for item in later["candidates"]}
