from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import (
    CandidatePageError,
    NeuralLanguageMode,
)
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class FakeRuntime:
    logits: np.ndarray
    calls: int = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(self.logits, before, after, 0.1)

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


class BlockingRuntime(FakeRuntime):
    def __post_init__(self) -> None:
        self.refresh_started = threading.Event()
        self.release_refresh = threading.Event()

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        if self.calls == 2:
            self.refresh_started.set()
            assert self.release_refresh.wait(2.0)
        return RuntimeSnapshot(self.logits, before, after, 0.1)


class FakeTokenizer:
    pieces = {
        0: "<special>",
        10: " neural",
        11: " network",
        12: " next",
        13: " ni",
    }
    all_special_ids = [0]

    def __len__(self) -> int:
        return 14

    def decode(
        self,
        token_ids,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert not skip_special_tokens
        assert not clean_up_tokenization_spaces
        return "".join(self.pieces.get(token_id, "<x>") for token_id in token_ids)


def _engine(make_index, runtime_cls=FakeRuntime):
    rows = [
        (1, "你", "ni", "ni", 1, 0),
        (2, "呢", "ne", "ne", 1, 0),
        (3, "南", "nan", "nan", 1, 0),
        (4, "你好", "nihao", "ni'hao", 2, 0),
        (5, "你好吗", "nihaoma", "ni'hao'ma", 3, 0),
        (6, "你能不能", "ninengbuneng", "ni'neng'bu'neng", 4, 0),
        (7, "泥", "ni", "ni", 1, 0),
        (8, "拟", "ni", "ni", 1, 0),
        (9, "逆", "ni", "ni", 1, 0),
    ]
    index = make_index(rows)
    logits = np.full(32, -20.0, dtype=np.float32)
    logits[1:10] = [9.0, 8.0, 7.0, 20.0, 30.0, 40.0, 6.0, 5.0, 4.0]
    logits[10:14] = [100.0, 90.0, 80.0, 70.0]
    runtime = runtime_cls(logits)
    if isinstance(runtime, BlockingRuntime):
        runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint.from_tokenizer(FakeTokenizer()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine, raw: str, mode: str = "chinese_first", **kwargs):
    values = {
        "client_session_id": "ime-session",
        "composition_revision": 1,
        "context_epoch": 0,
        "context_session": None,
        "source_revision": None,
        "language_mode": mode,
        "raw_keys": raw,
        "page_index": 0,
    }
    values.update(kwargs)
    return engine.query_candidate_page(**values)


def test_empty_context_baseline_is_ready_before_editor_context(make_index) -> None:
    engine, runtime = _engine(make_index)

    page = _page(engine, "n")

    assert runtime.calls == 1
    assert page.score_source == "baseline"
    assert page.candidates
    assert page.candidates[0].script == "han"
    assert any(candidate.script == "latin" for candidate in page.candidates)
    assert all(candidate.constraint_kind != "literal" for candidate in page.candidates)


def test_predicted_syllables_is_hard_primary_han_bucket(make_index) -> None:
    engine, _ = _engine(make_index)

    first = _page(engine, "n")
    second = _page(
        engine,
        "n",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )
    han = [
        candidate
        for candidate in (*first.candidates, *second.candidates)
        if candidate.script == "han"
    ]

    # The longer phrases have deliberately much larger model logits. They still
    # cannot jump over a shorter predicted-syllable bucket.
    predicted = [candidate.predicted_syllables for candidate in han]
    assert predicted == sorted(predicted)
    assert han[0].text != "你能不能"
    by_text = {candidate.text: candidate for candidate in han}
    assert by_text["你"].predicted_syllables == 0
    assert by_text["你好"].predicted_syllables == 1


@pytest.mark.parametrize(
    ("raw", "expected", "predicted"),
    [
        ("ni", "你好", 1),
        ("nih", "你好", 0),
        ("nihao", "你好", 0),
        ("nh", "你好", 0),
    ],
)
def test_half_pinyin_and_initial_shorthand_stay_on_legal_model_paths(
    make_index,
    raw: str,
    expected: str,
    predicted: int,
) -> None:
    engine, _ = _engine(make_index)

    page = _page(engine, raw)
    matches = [candidate for candidate in page.candidates if candidate.text == expected]

    assert matches
    assert min(candidate.predicted_syllables for candidate in matches) == predicted
    assert all(candidate.token_path for candidate in matches)


def test_full_input_can_offer_prefix_consumption_without_beating_full_cover(make_index) -> None:
    engine, _ = _engine(make_index)

    page = _page(engine, "nihao")
    han = [candidate for candidate in page.candidates if candidate.script == "han"]
    full = next(candidate for candidate in han if candidate.text == "你好")
    prefix = next(candidate for candidate in han if candidate.text == "你")

    assert full.completes_input
    assert full.consumed_keys == len("nihao")
    assert not prefix.completes_input
    assert prefix.consumed_keys == len("ni")
    assert han.index(full) < han.index(prefix)


def test_latin_first_is_latin_only_and_bounded_to_five(make_index) -> None:
    engine, _ = _engine(make_index)

    page = _page(engine, "n", "latin_first")

    assert len(page.candidates) <= 5
    assert page.candidates
    assert all(candidate.script == "latin" for candidate in page.candidates)


def test_context_refresh_in_flight_falls_back_to_baseline_without_waiting(make_index) -> None:
    engine, runtime = _engine(make_index, BlockingRuntime)
    requested = engine.request_context_update("PRIVATE-CONTEXT-MUST-NOT-LEAK")
    assert runtime.refresh_started.wait(1.0)

    started = time.perf_counter()
    page = _page(
        engine,
        "n",
        context_epoch=requested,
        context_session=None,
        source_revision=None,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert page.score_source == "baseline"
    assert page.candidates[0].script == "han"
    assert elapsed_ms < 35
    assert runtime.calls == 2

    runtime.release_refresh.set()
    assert engine.wait_for_epoch(requested, 1.0)


def test_returned_pages_are_frozen_and_candidate_ids_stable(make_index) -> None:
    rows = [
        (token_id, chr(0x4E00 + token_id), f"n{'a' * token_id}", f"n{'a' * token_id}", 1, 0)
        for token_id in range(1, 25)
    ]
    index = make_index(rows)
    logits = np.arange(64, dtype=np.float32)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(FakeRuntime(logits)),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, "n")
    second = _page(
        engine,
        "n",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )
    repeated = _page(
        engine,
        "n",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )

    assert len(first.candidates) == 9
    assert len(second.candidates) == 9
    assert repeated.candidates == second.candidates
    assert repeated.candidate_ids == second.candidate_ids
    assert set(first.candidate_ids).isdisjoint(second.candidate_ids)


def test_candidate_set_rejects_new_composition_identity(make_index) -> None:
    engine, _ = _engine(make_index)
    first = _page(engine, "n")

    with pytest.raises(CandidatePageError):
        _page(
            engine,
            "n",
            page_index=1,
            candidate_set_id=first.candidate_set_id,
            composition_revision=2,
        )


def test_private_reset_clears_sessions_but_preserves_context_free_baseline(make_index) -> None:
    engine, runtime = _engine(make_index)
    first = _page(engine, "n")

    engine.reset_private_context()

    with pytest.raises(CandidatePageError):
        _page(
            engine,
            "n",
            page_index=1,
            candidate_set_id=first.candidate_set_id,
        )
    replacement = _page(engine, "n", composition_revision=2)
    assert replacement.score_source == "baseline"
    assert replacement.candidates[0].script == "han"
    assert runtime.calls == 1
