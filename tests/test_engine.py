from __future__ import annotations

import threading
import time

import pytest

from neural_weasel.engine import NeuralPinyinEngine
from neural_weasel.index import tokenizer_fingerprint
from neural_weasel.model import LogitsSnapshot


class FakeTokenizer:
    all_special_ids: list[int] = []

    def __len__(self) -> int:
        return 2

    def get_vocab(self) -> dict[str, int]:
        return {"<unused>": 0, "你": 1}


class FakeBackend:
    def __init__(self) -> None:
        self.tokenizer = FakeTokenizer()
        self.model_id = "test/base-model"
        self.calls: list[tuple[str, str]] = []

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        self.calls.append((before, after))
        return LogitsSnapshot(
            epoch=len(self.calls),
            before_hash=f"before-{len(self.calls)}",
            after_hash=f"after-{len(self.calls)}",
            logits=(0.0, 5.0),
            created_monotonic=time.monotonic(),
            latency_ms=1.0,
        )


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def create_snapshot(self, before: str, after: str = "") -> LogitsSnapshot:
        if not self.calls:
            self.first_started.set()
            assert self.release_first.wait(timeout=2.0)
        return super().create_snapshot(before, after)


def test_engine_requires_matching_tokenizer_index(make_index) -> None:
    backend = FakeBackend()
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash="wrong")
    with pytest.raises(RuntimeError, match="tokenizer/index mismatch"):
        NeuralPinyinEngine(backend, index)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"model_id": "other/base-model"}, "model/index mismatch"),
        ({"revision": "other-commit"}, "revision/index mismatch"),
        ({"pypinyin_version": "0.0.invalid"}, "pypinyin/index mismatch"),
    ],
)
def test_engine_rejects_stale_index_identity(
    make_index,
    metadata: dict[str, str],
    message: str,
) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index(
        [(1, "你", "ni", 1, 0)],
        tokenizer_hash=fingerprint,
        **metadata,
    )
    with pytest.raises(RuntimeError, match=message):
        NeuralPinyinEngine(backend, index)


def test_engine_queries_only_immutable_snapshot_and_never_calls_backend(make_index) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)

    assert engine.query("ni") == []
    assert backend.calls == []
    snapshot = engine.update_context("你好", "世界")
    candidates = engine.query("ni")
    candidates_again = engine.query("n")

    assert snapshot.epoch == 1
    assert backend.calls == [("你好", "世界")]
    assert engine.context_epoch == 1
    assert [candidate.text for candidate in candidates] == ["你"]
    assert candidates[0].context_epoch == 1
    assert [candidate.text for candidate in candidates_again] == ["你"]


def test_engine_keeps_recent_snapshot_addressable_by_composition_epoch(make_index) -> None:
    backend = FakeBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)
    first = engine.update_context("first")
    second = engine.update_context("second")

    assert first.epoch == 1
    assert second.epoch == 2
    assert engine.query("ni", context_epoch=1)[0].context_epoch == 1
    assert engine.query("ni", context_epoch=2)[0].context_epoch == 2


def test_async_context_update_never_publishes_superseded_snapshot(make_index) -> None:
    backend = BlockingBackend()
    fingerprint = tokenizer_fingerprint(backend.tokenizer)
    index = make_index([(1, "你", "ni", 1, 0)], tokenizer_hash=fingerprint)
    engine = NeuralPinyinEngine(backend, index)

    first_epoch = engine.request_context_update("stale")
    assert backend.first_started.wait(timeout=1.0)
    second_epoch = engine.request_context_update("fresh")
    backend.release_first.set()

    assert first_epoch == 1
    assert second_epoch == 2
    assert engine.wait_for_epoch(second_epoch, timeout_seconds=2.0)
    assert engine.context_epoch == second_epoch
    assert backend.calls == [("stale", ""), ("fresh", "")]
    assert engine.query("ni", context_epoch=first_epoch) == []
