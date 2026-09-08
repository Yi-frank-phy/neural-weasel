from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.modern_han import MODERN_READINGS
from neural_weasel.neural_candidates import CandidatePageError, CandidatePageTimeout
from neural_weasel.simplified_chinese import is_simplified_han
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint

MODERN_TEST_CHARACTERS = sorted(char for char in MODERN_READINGS if is_simplified_han(char))


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


class ToggleContinuationRuntime(FakeRuntime):
    blocked = True
    continuation_calls = 0

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", self.calls, before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del token_paths, deadline_ms
        assert root[0] == "root"
        self.continuation_calls += 1
        if self.blocked:
            return None
        return [
            np.asarray([-float(token_id) for token_id in allowed], dtype=np.float32)
            for allowed in allowed_token_sets
        ]


class BlockingContinuationRuntime(FakeRuntime):
    def __post_init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", self.calls, before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del token_paths, deadline_ms
        assert root[0] == "root"
        self.started.set()
        assert self.release.wait(2.0)
        outputs = []
        for allowed in allowed_token_sets:
            allowed = tuple(int(token_id) for token_id in allowed)
            values = np.full(len(allowed), -20.0, dtype=np.float32)
            if 2 in allowed:
                values[allowed.index(2)] = 20.0
            outputs.append(values)
        return outputs


class ProductionTopologyContinuationRuntime(FakeRuntime):
    def __post_init__(self) -> None:
        self.continuation_batches: list[tuple[tuple[int, ...], ...]] = []

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", self.calls, before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del deadline_ms
        assert root[0] == "root"
        paths = tuple(tuple(int(token_id) for token_id in path) for path in token_paths)
        self.continuation_batches.append(paths)
        outputs = []
        for path, allowed in zip(paths, allowed_token_sets, strict=True):
            values = np.full(len(tuple(allowed)), -20.0, dtype=np.float32)
            if path == (3,):
                values[4] = 20.0
            outputs.append(values)
        return outputs


class ProgressiveContinuationRuntime(FakeRuntime):
    """Expose a target root only after the first bounded background batch."""

    def __post_init__(self) -> None:
        self.continuation_batches: list[tuple[tuple[int, ...], ...]] = []

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(
            self.logits,
            before,
            after,
            0.1,
            continuation_root=("root", self.calls, before),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del deadline_ms
        assert root[0] == "root"
        paths = tuple(tuple(int(token_id) for token_id in path) for path in token_paths)
        self.continuation_batches.append(paths)
        child_token = 21
        target_root = (20,)
        outputs = []
        for path in paths:
            values = np.full(self.logits.size, -np.inf, dtype=np.float32)
            if child_token < self.logits.size:
                values[child_token] = 30.0 if path == target_root else 0.0
            outputs.append(values)
        assert len(outputs) == len(tuple(allowed_token_sets))
        return outputs


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


def _continuation_engine(make_index):
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "你好世界", "nihaoshijie", "ni'hao'shi'jie", 4, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 5.0
    logits[2] = 100.0
    runtime = ToggleContinuationRuntime(logits)
    runtime.blocked = True
    runtime.continuation_calls = 0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
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


def _wait_for_page_preparation(
    engine: BilingualImeEngine,
    candidate_set_id: str,
    timeout: float = 1.0,
) -> None:
    manager = engine.candidate_pages
    with manager._state_lock:
        event = manager._page_preparation_events.get(candidate_set_id)
    assert event is not None
    assert event.wait(timeout)


def test_empty_context_baseline_is_ready_before_editor_context(make_index) -> None:
    engine, runtime = _engine(make_index)

    page = _page(engine, "n")

    assert runtime.calls == 1
    assert page.score_source == "baseline"
    assert page.candidates
    assert page.candidates[0].script == "han"
    assert any(candidate.script == "latin" for candidate in page.candidates)
    assert all(candidate.constraint_kind != "literal" for candidate in page.candidates)


def test_chinese_page_zero_reserves_eight_han_slots_and_one_latin_slot(make_index) -> None:
    engine, _ = _engine(make_index)

    page = _page(engine, "n")

    assert [candidate.script for candidate in page.candidates] == ["han"] * 8 + ["latin"]


def test_ready_chinese_context_reorders_page_zero_ahead_of_baseline_prewarm(make_index) -> None:
    engine, runtime = _engine(make_index)
    baseline = _page(engine, "n")
    assert baseline.candidates[0].text != "逆"

    contextual_logits = runtime.logits.copy()
    contextual_logits[9] = 1_000.0
    runtime.logits = contextual_logits
    context_state = engine.update_context("这是一个中文上下文")

    contextual = _page(
        engine,
        "n",
        composition_revision=2,
        context_epoch=context_state.epoch,
        context_session="source-context",
        source_revision=1,
    )

    assert contextual.score_source == "context"
    assert contextual.candidates[0].text == "逆"
    assert [candidate.script for candidate in contextual.candidates] == ["han"] * 8 + ["latin"]


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


def test_wide_han_root_materializes_only_protocol_reachable_candidates(make_index) -> None:
    rows = [
        (token_id, MODERN_TEST_CHARACTERS[token_id], "zi", "zi", 1, 0) for token_id in range(1, 251)
    ]
    index = make_index(rows)
    logits = np.full(300, -100.0, dtype=np.float32)
    logits[1:251] = np.arange(250, 0, -1, dtype=np.float32)
    runtime = FakeRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    candidates = engine.candidate_pages._root_han_candidates("z", None, 0)
    eligible_token_ids = sorted(
        int(entry.token_id)
        for entry in engine.candidate_pages.matcher.entries
        if entry.token_id is not None
    )

    assert len(candidates) == 180
    assert {candidate.token_id for candidate in candidates} == set(eligible_token_ids[:180])


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


def test_initial_shorthand_survives_a_competing_exact_short_syllable(make_index) -> None:
    index = make_index(
        [
            (1, "嗯", "n", "n", 1, 0),
            (2, "你好", "nihao", "ni'hao", 2, 0),
        ]
    )
    logits = np.full(4, -20.0, dtype=np.float32)
    logits[1] = 20.0
    logits[2] = 10.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(FakeRuntime(logits)),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    page = _page(engine, "nh")

    by_text = {candidate.text: candidate for candidate in page.candidates}
    assert "你好" in by_text
    assert by_text["你好"].completes_input
    assert by_text["你好"].token_path == (2,)


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


def test_latin_first_is_latin_only_single_page_and_bounded_to_five(make_index) -> None:
    engine, _ = _engine(make_index)

    page = _page(engine, "n", "latin_first")

    assert len(page.candidates) <= 5
    assert page.candidates
    assert all(candidate.script == "latin" for candidate in page.candidates)
    assert page.has_more is False


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
        (
            token_id,
            MODERN_TEST_CHARACTERS[token_id],
            f"n{'a' * token_id}",
            f"n{'a' * token_id}",
            1,
            0,
        )
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


def test_root_only_search_freezes_five_pages_without_restarting(make_index) -> None:
    simplified = (
        "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能看"
    )
    rows = [(token_id, text, "ni", "ni", 1, 0) for token_id, text in enumerate(simplified, start=1)]
    index = make_index(rows)
    logits = np.arange(64, dtype=np.float32)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(FakeRuntime(logits)),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    pages = [_page(engine, "ni")]
    for page_index in range(1, 5):
        pages.append(
            _page(
                engine,
                "ni",
                page_index=page_index,
                candidate_set_id=pages[0].candidate_set_id,
            )
        )

    assert [len(page.candidates) for page in pages] == [9, 9, 9, 9, 9]
    assert pages[-1].has_more is False
    ids = [candidate_id for page in pages for candidate_id in page.candidate_ids]
    assert len(ids) == len(set(ids)) == 45

    replay = _page(
        engine,
        "ni",
        page_index=2,
        candidate_set_id=pages[0].candidate_set_id,
    )
    assert replay.candidates == pages[2].candidates
    assert replay.candidate_ids == pages[2].candidate_ids


def test_page_zero_never_waits_for_continuation_and_long_root_stays_unfrozen(make_index) -> None:
    engine, runtime = _continuation_engine(make_index)

    first = _page(engine, "n")

    assert runtime.continuation_calls == 0
    assert [candidate.text for candidate in first.candidates] == ["你"]
    assert first.has_more is True


def test_search_frontier_retains_partial_root_beyond_visible_180(make_index) -> None:
    safe = "的一是在不了有和人这中大为上个国我以要"
    exact = [
        (token_id, safe[left] + safe[right], "mingxian", "ming'xian", 2, 0)
        for token_id, (left, right) in enumerate(
            ((left, right) for left in range(len(safe)) for right in range(len(safe))),
            start=1,
        )
    ][:250]
    parent_token = 251
    index = make_index([*exact, (parent_token, "明", "ming", "ming", 1, 0)])
    logits = np.full(300, -20.0, dtype=np.float32)
    logits[1:251] = np.arange(250, dtype=np.float32)
    logits[parent_token] = 1_000.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(FakeRuntime(logits)),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, "mingxian")
    session = engine.candidate_pages._sessions[first.candidate_set_id]

    assert all(candidate.text != "明" for candidate in first.candidates)
    assert any(path.token_path == (parent_token,) for path in session.frontier)


def test_page_zero_background_continuation_publishes_only_to_next_revision(make_index) -> None:
    index = make_index(
        [
            (1, "明", "ming", "ming", 1, 0),
            (2, "显", "xian", "xian", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 9.0
    runtime = BlockingContinuationRuntime(logits)
    runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, "mingxian")
    assert [candidate.text for candidate in first.candidates] == ["明"]
    assert runtime.started.wait(0.5)
    replay = _page(engine, "mingxian")
    assert replay.candidate_set_id == first.candidate_set_id
    assert replay.candidates == first.candidates

    runtime.release.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if engine.candidate_pages._async_han_cache:
            break
        time.sleep(0.01)
    else:
        pytest.fail("background continuation did not publish its completed candidate")

    refreshed = _page(engine, "mingxian", composition_revision=2)
    assert refreshed.candidate_set_id != first.candidate_set_id
    assert refreshed.candidates[0].text == "明显"


def test_background_continuation_batches_production_shorthand_roots(
    make_index,
    monkeypatch,
) -> None:
    index = make_index(
        [
            (1, "明星", "mingxing", "ming'xing", 2, 0),
            (2, "梦想", "mengxiang", "meng'xiang", 2, 0),
            (3, "明显", "mingxian", "ming'xian", 2, 0),
            (4, "不对", "budui", "bu'dui", 2, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 12.0
    logits[2] = 11.0
    logits[3] = 10.0
    runtime = ProductionTopologyContinuationRuntime(logits)
    runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    matcher = engine.candidate_pages.matcher
    assert matcher is not None
    original_neural_matches = matcher.neural_matches
    suffix_match_starts: list[int] = []

    def counted_neural_matches(raw, start=0, boundaries=None):
        if start > 0:
            suffix_match_starts.append(start)
        return original_neural_matches(raw, start, boundaries)

    monkeypatch.setattr(matcher, "neural_matches", counted_neural_matches)

    first = _page(engine, "mxbd")
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if engine.candidate_pages._async_han_cache:
            break
        time.sleep(0.01)
    else:
        pytest.fail("background continuation did not publish the multi-token phrase")

    assert runtime.continuation_batches
    assert runtime.continuation_batches[0][0] != (3,)
    assert (3,) in runtime.continuation_batches[0]
    assert suffix_match_starts == [2]
    replay = _page(engine, "mxbd")
    assert replay.candidate_set_id == first.candidate_set_id
    assert all(candidate.text != "明显不对" for candidate in replay.candidates)

    refreshed = _page(engine, "mxbd", composition_revision=2)
    assert refreshed.candidate_set_id != first.candidate_set_id
    assert refreshed.candidates[0].text == "明显不对"


def test_background_continuation_progresses_beyond_first_root_batch(make_index) -> None:
    # Keep nineteen lower-scoring roots in the same shorthand bucket so the
    # desired twentieth root can only be reached by multiple eight-root calls.
    dummy_text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申"
    rows = [
        (token_id, text, "ming'xian", "ming'xian", 2, 0)
        for token_id, text in enumerate(dummy_text, start=1)
    ]
    rows.extend(
        [
            (20, "明显", "ming'xian", "ming'xian", 2, 0),
            (21, "不对", "bu'dui", "bu'dui", 2, 0),
        ]
    )
    index = make_index(rows)
    logits = np.full(22, -20.0, dtype=np.float32)
    logits[1:20] = np.arange(19, 0, -1, dtype=np.float32)
    logits[20] = -1.0
    runtime = ProgressiveContinuationRuntime(logits)
    runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, "mxbd")
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if engine.candidate_pages._async_han_cache:
            break
        time.sleep(0.01)
    else:
        pytest.fail("progressive background continuation did not publish a candidate")

    assert len(runtime.continuation_batches) >= 3
    assert (20,) in runtime.continuation_batches[2]
    refreshed = _page(engine, "mxbd", composition_revision=2)
    assert refreshed.candidate_set_id != first.candidate_set_id
    pages = [refreshed]
    for page_index in (1, 2):
        pages.append(
            _page(
                engine,
                "mxbd",
                composition_revision=2,
                page_index=page_index,
                candidate_set_id=refreshed.candidate_set_id,
            )
        )
    assert any(candidate.text == "明显不对" for page in pages for candidate in page.candidates)


def test_focus_invalidation_discards_background_continuation_result(make_index) -> None:
    index = make_index(
        [
            (1, "明", "ming", "ming", 1, 0),
            (2, "显", "xian", "xian", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 9.0
    runtime = BlockingContinuationRuntime(logits)
    runtime.__post_init__()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    _page(engine, "mingxian")
    assert runtime.started.wait(0.5)
    engine.invalidate_candidate_sessions()
    runtime.release.set()
    time.sleep(0.05)

    assert not engine.candidate_pages._async_han_cache


def test_next_page_timeout_keeps_same_candidate_set_retryable(make_index) -> None:
    engine, runtime = _continuation_engine(make_index)
    first = _page(engine, "n")

    # C2 prepares page 1 in the background. Let the blocked first attempt finish,
    # then verify that navigation itself does not retry the model.
    _wait_for_page_preparation(engine, first.candidate_set_id)
    assert runtime.continuation_calls == 1
    with pytest.raises(CandidatePageTimeout):
        _page(
            engine,
            "n",
            page_index=1,
            candidate_set_id=first.candidate_set_id,
            deadline_ms=120.0,
        )
    assert runtime.continuation_calls == 1

    runtime.blocked = False
    replay = _page(engine, "n")
    assert replay.candidate_set_id == first.candidate_set_id
    _wait_for_page_preparation(engine, first.candidate_set_id)
    second = _page(
        engine,
        "n",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )
    assert second.candidate_set_id == first.candidate_set_id
    assert second.candidates
    assert all(candidate.predicted_syllables >= 1 for candidate in second.candidates)


def test_context_session_never_hybridizes_with_baseline_continuation(make_index) -> None:
    engine, runtime = _continuation_engine(make_index)
    runtime.blocked = False
    context_state = engine.update_context("editor-context")

    first = _page(
        engine,
        "n",
        context_epoch=context_state.epoch,
        context_session="source-a",
        source_revision=1,
    )
    assert first.score_source == "context"

    _wait_for_page_preparation(engine, first.candidate_set_id)
    second = _page(
        engine,
        "n",
        context_epoch=context_state.epoch,
        context_session="source-a",
        source_revision=1,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )
    assert second.score_source == "context"
    assert runtime.continuation_calls > 0


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
