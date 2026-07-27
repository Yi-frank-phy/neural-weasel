from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.pipe_server import NamedPipeServer
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint, contains_han


@dataclass
class FakeRuntime:
    logits: np.ndarray

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(self.logits, before, after, 1.0)

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


class FakeTokenizer:
    pieces = {
        0: "<special>",
        1: " asymmetric",
        2: " asymmetry",
        3: " Qwen",
        4: " Qwen3.5",
        5: "  two words",
        6: "纠缠",
    }
    all_special_ids = [0]

    def __len__(self) -> int:
        return len(self.pieces)

    def decode(
        self,
        token_ids,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert not skip_special_tokens
        assert not clean_up_tokenization_spaces
        return "".join(self.pieces[token_id] for token_id in token_ids)


def make_engine(make_index) -> BilingualImeEngine:
    logits = np.full(16, -10.0, dtype=np.float32)
    logits[1:5] = [9.0, 8.0, 7.0, 6.0]
    logits[6] = 10.0
    backend = FullLogitsSnapshotBackend(FakeRuntime(logits))
    index = make_index([(6, "纠缠", "jiuchan", "jiu'chan", 2, 0)])
    return BilingualImeEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint.from_tokenizer(FakeTokenizer()),
    )


def test_tokenizer_catalog_exposes_model_tokens_without_name_whitelist(make_index) -> None:
    """AT-EN-01/CN-02: production Latin source comes from tokenizer pieces."""
    engine = make_engine(make_index)
    state = engine.update_context("The receiver-centred placement is operationally")

    candidates = engine.query("asy", 5, context_epoch=state.epoch)

    assert {"asymmetric", "asymmetry"} <= {candidate.text for candidate in candidates}
    assert not any(contains_han(candidate.text) for candidate in candidates)


def test_bilingual_engine_keeps_old_epoch_queryable(make_index) -> None:
    """AT-RT-02/03: protocol can address a retained immutable epoch."""
    engine = make_engine(make_index)
    first = engine.update_context("The receiver-centred placement is operationally")
    second = engine.update_context("该协议所消耗的")

    old = engine.query("asy", 5, context_epoch=first.epoch)
    new = engine.query("jiuchan", 5, context_epoch=second.epoch)

    assert old[0].context_epoch == first.epoch
    assert new[0].text == "纠缠"
    assert engine.has_snapshot(first.epoch)
    assert engine.has_snapshot(second.epoch)


def test_query_candidates_protocol_uses_unified_engine(make_index) -> None:
    """AT-UC-01/RT-06: service publishes unified candidates with exact epoch."""
    engine = make_engine(make_index)
    state = engine.update_context("The receiver-centred placement is operationally")
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\NeuralWeasel-test")

    response = server.handle_message(
        {
            "type": "query_candidates",
            "session_id": "session",
            "revision": 4,
            "context_epoch": state.epoch,
            "raw_keys": "asy",
            "candidate_count": 5,
        }
    )

    assert response["ok"] is True
    assert response["context_epoch"] == state.epoch
    assert response["revision"] == 4
    assert any(item["text"] == "asymmetric" for item in response["candidates"])
    assert all("constraint_kind" in item for item in response["candidates"])
    assert all(not contains_han(item["text"]) for item in response["candidates"])


def test_query_candidates_without_snapshot_returns_literal_not_error(make_index) -> None:
    """AT-EN-03/RT-05: cold service preserves literal typing."""
    engine = make_engine(make_index)
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\NeuralWeasel-test")

    response = server.handle_message(
        {
            "type": "query_candidates",
            "session_id": "session",
            "revision": 1,
            "context_epoch": 0,
            "raw_keys": "non",
            "candidate_count": 5,
        }
    )

    assert response["ok"] is True
    assert response["context_epoch"] == 0
    assert response["candidates"][0]["text"] == "non"
    assert response["candidates"][0]["constraint_kind"] == "literal"
