from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.production_pipe import ProductionNamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class Runtime:
    logits: np.ndarray
    calls: int = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(self.logits, before, after, 0.1)

    def diagnostics(self) -> dict[str, object]:
        return {}

    def performance_diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


class BlockingRuntime(Runtime):
    def __post_init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        if self.calls == 2:
            self.started.set()
            assert self.release.wait(2.0)
        return RuntimeSnapshot(self.logits, before, after, 0.1)


class Tokenizer:
    pieces = {0: "<special>", 10: " neural", 11: " network"}
    all_special_ids = [0]

    def __len__(self) -> int:
        return 12

    def decode(
        self,
        token_ids,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        return "".join(self.pieces.get(token_id, "<x>") for token_id in token_ids)


def _make(make_index, runtime_cls=Runtime):
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "泥", "ni", "ni", 1, 0),
            (3, "你好", "nihao", "ni'hao", 2, 0),
            (4, "你好吗", "nihaoma", "ni'hao'ma", 3, 0),
        ]
    )
    logits = np.full(16, -10.0, dtype=np.float32)
    logits[1:5] = [5.0, 4.0, 9.0, 12.0]
    logits[10:12] = [20.0, 19.0]
    runtime = runtime_cls(logits)
    if isinstance(runtime, BlockingRuntime):
        runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint.from_tokenizer(Tokenizer()),
    )
    engine.initialize_neural_baseline()
    server = ProductionNamedPipeServer(engine, pipe_name=r"\.\pipe\NeuralWeasel-page-test")
    return engine, runtime, server


def _request(**overrides):
    request = {
        "type": "query_candidate_page",
        "session_id": "rime-session",
        "composition_revision": 7,
        "context_epoch": 0,
        "language_mode": "chinese_first",
        "raw_keys": "n",
        "page_index": 0,
    }
    request.update(overrides)
    return request


def test_page_zero_protocol_returns_stable_set_and_neural_ids(make_index) -> None:
    _, runtime, server = _make(make_index)

    response = server.handle_message(_request())

    assert response["ok"] is True
    assert response["type"] == "candidate_page"
    assert response["page_index"] == 0
    assert response["page_size"] == 9
    assert response["score_source"] == "baseline"
    assert response["candidates"][0]["script"] == "han"
    assert any(item["script"] == "latin" for item in response["candidates"])
    assert all(item["candidate_id"] for item in response["candidates"])
    assert runtime.calls == 1


def test_nonzero_context_requires_bound_identity(make_index) -> None:
    _, _, server = _make(make_index)

    response = server.handle_message(_request(context_epoch=1))

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_inflight_bound_context_uses_baseline_instead_of_context_not_ready(make_index) -> None:
    engine, runtime, server = _make(make_index, BlockingRuntime)
    context_session = "a" * 32
    accepted = server.handle_message(
        {
            "type": "context_update",
            "context_epoch": 1,
            "context_session": context_session,
            "source_revision": 1,
            "security_label": "normal",
            "before": "editor context",
            "after": "",
        }
    )
    assert accepted["ok"] is True
    assigned = accepted["context_epoch"]
    assert runtime.started.wait(1.0)

    response = server.handle_message(
        _request(
            context_epoch=assigned,
            context_session=context_session,
            source_revision=1,
        )
    )

    assert response["ok"] is True
    assert response["context_epoch"] == assigned
    assert response["score_source"] == "baseline"
    assert response["candidates"][0]["script"] == "han"
    assert runtime.calls == 2

    runtime.release.set()
    assert engine.wait_for_epoch(assigned, 1.0)


def test_page_response_from_wrong_source_revision_is_discarded(make_index) -> None:
    engine, runtime, server = _make(make_index)
    context_session = "b" * 32
    accepted = server.handle_message(
        {
            "type": "context_update",
            "context_epoch": 1,
            "context_session": context_session,
            "source_revision": 4,
            "security_label": "normal",
            "before": "context",
            "after": "",
        }
    )
    assigned = accepted["context_epoch"]
    assert engine.wait_for_epoch(assigned, 1.0)

    response = server.handle_message(
        _request(
            context_epoch=assigned,
            context_session=context_session,
            source_revision=3,
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "context_session_mismatch"
    assert runtime.calls == 2


def test_new_revision_cannot_continue_old_candidate_set(make_index) -> None:
    _, _, server = _make(make_index)
    first = server.handle_message(_request())

    response = server.handle_message(
        _request(
            composition_revision=8,
            page_index=1,
            candidate_set_id=first["candidate_set_id"],
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "candidate_set_invalid"


def test_diagnostics_expose_only_numeric_page_metadata(make_index) -> None:
    _, _, server = _make(make_index)
    server.handle_message(_request())

    diagnostics = server.handle_message({"type": "diagnostics"})

    assert diagnostics["ok"] is True
    assert diagnostics["last_candidate_page_index"] == 0
    assert diagnostics["last_candidate_count"] >= 1
    assert diagnostics["last_candidate_search_depth"] >= 1
    assert diagnostics["last_candidate_search_elapsed_ms"] >= 0
    assert "raw_keys" not in diagnostics
    assert "candidates" not in diagnostics
