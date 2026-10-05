from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.backends import ContinuationAttempt, FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.candidate import Candidate
from neural_weasel.neural_candidates import (
    MAX_HAN_CHARACTERS,
    CandidatePageTimeout,
    NeuralLanguageMode,
    _SearchIdentity,
    _SearchPath,
)
from neural_weasel.simplified_chinese import is_simplified_han
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint, PinyinConstraint


@dataclass
class BlockingContinuationRuntime:
    logits: np.ndarray
    first_started: threading.Event = field(default_factory=threading.Event)
    release_first: threading.Event = field(default_factory=threading.Event)
    second_started: threading.Event = field(default_factory=threading.Event)
    continuation_calls: int = 0

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
        call = self.continuation_calls
        if call == 1:
            self.first_started.set()
            assert self.release_first.wait(2.0)
        else:
            self.second_started.set()

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


@dataclass
class TransientEmptyContinuationRuntime:
    logits: np.ndarray
    continuation_calls: int = 0
    second_started: threading.Event = field(default_factory=threading.Event)
    release_second: threading.Event = field(default_factory=threading.Event)
    retry_started: threading.Event = field(default_factory=threading.Event)

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
        if self.continuation_calls == 2:
            self.second_started.set()
            assert self.release_second.wait(2.0)
        outputs = []
        for path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            assert tuple(path)
            allowed = tuple(int(token_id) for token_id in allowed)
            outputs.append(
                np.asarray(
                    [20.0 - token_id * 0.01 for token_id in allowed],
                    dtype=np.float32,
                )
            )
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


@dataclass
class StagedPageZeroRuntime:
    logits: np.ndarray
    continuation_calls: int = 0
    second_started: threading.Event = field(default_factory=threading.Event)
    release_second: threading.Event = field(default_factory=threading.Event)

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
        if self.continuation_calls == 2:
            self.second_started.set()
            assert self.release_second.wait(2.0)
        outputs = []
        for allowed in allowed_token_sets:
            allowed = tuple(int(token_id) for token_id in allowed)
            values = np.full(len(allowed), -np.inf, dtype=np.float32)
            if self.continuation_calls == 1:
                values[0] = 20.0
            else:
                for index, token_id in enumerate(allowed):
                    values[index] = 20.0 - token_id * 0.01
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _productive_two_syllable_index(make_index):
    rows = []
    for token_id, text in enumerate("你泥拟逆妮倪", start=1):
        rows.append((token_id, text, "ni", "ni", 1, 0))
    for token_id, text in enumerate("好号浩豪毫郝皓", start=11):
        rows.append((token_id, text, "hao", "hao", 1, 0))
    return make_index(rows)


def _engine(make_index) -> tuple[BilingualImeEngine, BlockingContinuationRuntime]:
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
    return engine, runtime


def _page(
    engine: BilingualImeEngine,
    *,
    revision: int,
    presentation_refresh: bool = False,
):
    return engine.query_candidate_page(
        client_session_id="same-client",
        composition_revision=revision,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="nihao",
        page_index=0,
        presentation_refresh=presentation_refresh,
    )


def test_latest_revision_retries_after_old_provider_releases(make_index, monkeypatch) -> None:
    """The latest input must not be stranded after it loses the single-flight race."""

    engine, runtime = _engine(make_index)
    backend = engine.candidate_pages.backend
    busy_seen = threading.Event()
    original_attempt = getattr(backend, "_continue_from_root_attempt", None)

    if callable(original_attempt):

        def observed_attempt(root, token_paths, allowed_token_sets, *, deadline_ms: float):
            with backend._continuation_gate:
                if backend._continuation_active:
                    busy_seen.set()
            return original_attempt(
                root,
                token_paths,
                allowed_token_sets,
                deadline_ms=deadline_ms,
            )

        monkeypatch.setattr(backend, "_continue_from_root_attempt", observed_attempt)
    else:
        original_continue = backend._continue_from_root_bounded

        def observed_continue(root, token_paths, allowed_token_sets, *, deadline_ms: float):
            with backend._continuation_gate:
                if backend._continuation_active:
                    busy_seen.set()
            return original_continue(
                root,
                token_paths,
                allowed_token_sets,
                deadline_ms=deadline_ms,
            )

        monkeypatch.setattr(backend, "_continue_from_root_bounded", observed_continue)

    with pytest.raises(CandidatePageTimeout):
        _page(engine, revision=1)
    assert runtime.first_started.wait(0.5)

    with pytest.raises(CandidatePageTimeout):
        _page(engine, revision=2)
    assert busy_seen.wait(0.5), "revision 2 never attempted continuation while revision 1 was busy"
    assert runtime.continuation_calls == 1
    revision_two_id = next(iter(engine.candidate_pages._sessions))
    completion = engine.candidate_pages._background_search_events.get(revision_two_id)
    assert completion is not None

    runtime.release_first.set()

    assert runtime.second_started.wait(1.0), (
        "latest revision did not resume automatically after the old provider released"
    )
    assert completion.wait(1.0), "resumed revision did not publish its completed candidates"

    replay = _page(engine, revision=2)
    assert replay.candidate_set_id == revision_two_id
    assert "你好" in {candidate.text for candidate in replay.candidates}

    refreshed = _page(engine, revision=2, presentation_refresh=True)
    assert refreshed.candidate_set_id == revision_two_id
    assert refreshed.candidates == replay.candidates
    assert all(
        candidate.completes_input for candidate in replay.candidates if candidate.script == "han"
    )


def test_background_cancellation_is_not_swallowed_by_retry_wait(make_index) -> None:
    """Cancelling a busy-generation waiter must retire it without another retry."""

    engine, runtime = _engine(make_index)
    manager = engine.candidate_pages
    backend = manager.backend

    with pytest.raises(CandidatePageTimeout):
        _page(engine, revision=1)
    assert runtime.first_started.wait(0.5)

    with pytest.raises(CandidatePageTimeout):
        _page(engine, revision=2)
    candidate_set_id = next(iter(manager._sessions))
    completion = manager._background_search_events.get(candidate_set_id)
    cancel = manager._background_cancel_events.get(candidate_set_id)
    assert completion is not None
    assert cancel is not None

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        with backend._continuation_gate:
            if backend._continuation_idle_waiters:
                break
        time.sleep(0.005)
    else:
        pytest.fail("the replacement continuation never entered its retry wait")

    cancel.set()
    try:
        assert completion.wait(0.25), "retry wake swallowed permanent cancellation"
        assert runtime.continuation_calls == 1
    finally:
        runtime.release_first.set()


def test_later_page_retries_after_provider_returns_no_result(make_index, monkeypatch) -> None:
    """A self-completed deadline miss must not strand the only page preparer."""

    index = _productive_two_syllable_index(make_index)
    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 7):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = TransientEmptyContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    monkeypatch.setattr(
        engine.candidate_pages,
        "_lexical_completion_fallback",
        lambda *args, **kwargs: [],
    )
    backend = engine.candidate_pages.backend
    original_attempt = backend._continue_from_root_attempt
    injected_empty = False

    def transient_empty_attempt(root, token_paths, allowed_token_sets, *, deadline_ms: float):
        nonlocal injected_empty
        with engine.candidate_pages._state_lock:
            page_zero_is_frozen = any(
                0 in session.frozen_pages for session in engine.candidate_pages._sessions.values()
            )
        if page_zero_is_frozen and not injected_empty:
            injected_empty = True
            return ContinuationAttempt()
        if injected_empty:
            runtime.retry_started.set()
        return original_attempt(
            root,
            token_paths,
            allowed_token_sets,
            deadline_ms=deadline_ms,
        )

    monkeypatch.setattr(backend, "_continue_from_root_attempt", transient_empty_attempt)

    deadline = time.monotonic() + 2.0
    first = None
    while time.monotonic() < deadline:
        try:
            first = engine.query_candidate_page(
                client_session_id="transient-empty-client",
                composition_revision=1,
                context_epoch=0,
                context_session=None,
                source_revision=None,
                language_mode="chinese_first",
                raw_keys="nihao",
                page_index=0,
            )
            break
        except CandidatePageTimeout:
            time.sleep(0.01)

    assert first is not None
    assert len(first.candidates) == 7
    assert runtime.second_started.wait(1.0)
    runtime.release_second.set()
    injection_deadline = time.monotonic() + 1.0
    while not injected_empty and time.monotonic() < injection_deadline:
        time.sleep(0.01)
    assert injected_empty is True
    assert runtime.retry_started.wait(1.0), (
        "page preparation exited after a provider returned no result"
    )

    completion = engine.candidate_pages._page_preparation_events.get(first.candidate_set_id)
    assert completion is not None
    assert completion.wait(2.0)
    second = engine.query_candidate_page(
        client_session_id="transient-empty-client",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="nihao",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )
    assert len(second.candidates) == 7


def test_page_zero_uses_lexical_paths_to_fill_35_without_neural_continuation(
    make_index,
) -> None:
    """Fast legal enumeration fills the fixed set without mutating page zero."""

    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 7):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    rows = [
        (31, "你好", "nihao", "ni'hao", 2, 0),
        *[
            (token_id, text, "ni", "ni", 1, 0)
            for token_id, text in enumerate("你泥拟逆妮倪", start=1)
        ],
        *[
            (token_id, text, "hao", "hao", 1, 0)
            for token_id, text in enumerate("好号浩豪毫郝皓", start=11)
        ],
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, revision=1)

    assert len(first.candidates) == 7
    assert all(candidate.completes_input for candidate in first.candidates)
    replay = _page(engine, revision=1)
    assert replay.candidates == first.candidates
    assert replay.candidate_ids == first.candidate_ids
    pages = _wait_for_fixed_pages(engine, first, client_session_id="same-client", raw_keys="nihao")
    assert [len(page.candidates) for page in pages] == [7, 7, 7, 7, 7]
    assert pages[-1].has_more is False
    assert len({candidate.text for page in pages for candidate in page.candidates}) == 35
    assert _page(engine, revision=1).candidate_ids == first.candidate_ids
    time.sleep(0.05)
    assert runtime.continuation_calls == 0


def _wait_for_fixed_pages(engine, first, *, client_session_id: str, raw_keys: str):
    """Exercise bounded API retries while later pages are prepared asynchronously."""
    pages = [first]
    deadline = time.monotonic() + 0.5
    for page_index in range(1, 5):
        while True:
            try:
                page = engine.query_candidate_page(
                    client_session_id=client_session_id,
                    composition_revision=1,
                    context_epoch=0,
                    context_session=None,
                    source_revision=None,
                    language_mode="chinese_first",
                    raw_keys=raw_keys,
                    page_index=page_index,
                    candidate_set_id=first.candidate_set_id,
                )
                pages.append(page)
                break
            except CandidatePageTimeout:
                assert time.monotonic() < deadline, "later lexical pages never became ready"
                time.sleep(0.002)
    return pages


def test_incomplete_final_syllable_fills_zhuyid_pages_from_legal_paths(make_index) -> None:
    """An unfinished last syllable must not strand a three-result first page."""

    logits = np.full(64, -20.0, dtype=np.float32)
    for token_id in (*range(1, 5), *range(10, 14), *range(20, 24), *range(40, 43)):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    runtime.release_second.set()
    rows = [
        (40, "注意的", "zhuyide", "zhu'yi'de", 3, 0),
        (41, "注意到", "zhuyidao", "zhu'yi'dao", 3, 0),
        (42, "注意点", "zhuyidian", "zhu'yi'dian", 3, 0),
        *[
            (token_id, text, "zhu", "zhu", 1, 0)
            for token_id, text in enumerate("朱主煮住", start=1)
        ],
        *[(token_id, text, "yi", "yi", 1, 0) for token_id, text in enumerate("一以已义", start=10)],
        *[(token_id, text, "de", "de", 1, 0) for token_id, text in enumerate("的地得德", start=20)],
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    deadline = time.monotonic() + 2.0
    first = None
    while time.monotonic() < deadline:
        try:
            first = engine.query_candidate_page(
                client_session_id="zhuyid-client",
                composition_revision=1,
                context_epoch=0,
                context_session=None,
                source_revision=None,
                language_mode="chinese_first",
                raw_keys="zhuyid",
                page_index=0,
            )
            break
        except CandidatePageTimeout:
            time.sleep(0.01)
    assert first is not None
    assert len(first.candidates) == 7
    assert all(candidate.completes_input for candidate in first.candidates)

    pages = _wait_for_fixed_pages(
        engine, first, client_session_id="zhuyid-client", raw_keys="zhuyid"
    )
    assert [len(page.candidates) for page in pages] == [7] * 5
    assert pages[-1].has_more is False
    assert len({candidate.text for page in pages for candidate in page.candidates}) == 35


def test_underfilled_page_zero_waits_for_late_lexical_tail(make_index, monkeypatch) -> None:
    """A retryable first page must not permanently freeze three visible candidates."""

    logits = np.full(64, -20.0, dtype=np.float32)
    for token_id in (*range(1, 5), *range(10, 14), *range(20, 24), *range(40, 43)):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    runtime.release_second.set()
    rows = [
        (40, "注意的", "zhuyide", "zhu'yi'de", 3, 0),
        (41, "注意到", "zhuyidao", "zhu'yi'dao", 3, 0),
        (42, "注意点", "zhuyidian", "zhu'yi'dian", 3, 0),
        *[
            (token_id, text, "zhu", "zhu", 1, 0)
            for token_id, text in enumerate("朱主煮住", start=1)
        ],
        *[(token_id, text, "yi", "yi", 1, 0) for token_id, text in enumerate("一以已义", start=10)],
        *[(token_id, text, "de", "de", 1, 0) for token_id, text in enumerate("的地得德", start=20)],
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    original = manager._lexical_completion_fallback
    worker_calls = 0

    def late_lexical_tail(*args, **kwargs):
        nonlocal worker_calls
        if threading.current_thread() is threading.main_thread():
            return []
        worker_calls += 1
        return original(*args, **{**kwargs, "limit": 1})

    monkeypatch.setattr(manager, "_lexical_completion_fallback", late_lexical_tail)
    monkeypatch.setattr(manager, "_start_background_continuation", lambda session: None)
    identity = _SearchIdentity(
        client_session_id="late-zhuyid-tail",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
        with pytest.raises(CandidatePageTimeout):
            manager._freeze_next_page(
                session,
                page_index=0,
                page_size=7,
                absolute_deadline=manager.clock() + 0.035,
            )
        assert 0 not in session.frozen_pages

    deadline = time.monotonic() + 2.0
    first = None
    while time.monotonic() < deadline:
        with manager._state_lock:
            first = session.frozen_pages.get(0)
        if first is not None:
            break
        time.sleep(0.01)
    assert first is not None
    assert len(first.candidates) == 7
    assert first.candidate_set_id == session.candidate_set_id
    assert worker_calls >= 4


def test_partial_foreground_lexical_tail_finishes_in_page_preparer(
    make_index,
    monkeypatch,
) -> None:
    """A short page-zero budget must not strand PageDown on neural decoding."""

    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 7):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    runtime.release_second.set()
    rows = [
        (31, "你好", "nihao", "ni'hao", 2, 0),
        *[
            (token_id, text, "ni", "ni", 1, 0)
            for token_id, text in enumerate("你泥拟逆妮倪", start=1)
        ],
        *[
            (token_id, text, "hao", "hao", 1, 0)
            for token_id, text in enumerate("好号浩豪毫郝皓", start=11)
        ],
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    manager = engine.candidate_pages
    original = manager._lexical_completion_fallback
    calls = 0

    def foreground_limited(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert kwargs["limit"] <= 7, "page zero must defer later-page lexical work"
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_lexical_completion_fallback", foreground_limited)
    first = _page(engine, revision=1)
    assert len(first.candidates) == 7

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with manager._state_lock:
            frozen_count = sum(
                len(page.candidates)
                for page in manager._sessions[first.candidate_set_id].frozen_pages.values()
            )
        if frozen_count == 35:
            break
        time.sleep(0.01)

    pages = [first]
    for page_index in range(1, 5):
        pages.append(
            engine.query_candidate_page(
                client_session_id="same-client",
                composition_revision=1,
                context_epoch=0,
                context_session=None,
                source_revision=None,
                language_mode="chinese_first",
                raw_keys="nihao",
                page_index=page_index,
                candidate_set_id=first.candidate_set_id,
            )
        )
    assert calls >= 2
    assert [len(page.candidates) for page in pages] == [7, 7, 7, 7, 7]


def test_page_zero_defers_lexical_tail_without_extending_request_deadline(make_index) -> None:
    """Near-deadline root work must not freeze a capable page zero below seven."""

    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 7):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(_productive_two_syllable_index(make_index)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    original = manager._lexical_completion_fallback

    foreground_deadline = None

    def requires_lexical_budget(*args, absolute_deadline: float, **kwargs):
        if foreground_deadline is not None:
            assert absolute_deadline <= foreground_deadline
        if absolute_deadline - manager.clock() < 0.005:
            return []
        return original(*args, absolute_deadline=absolute_deadline, **kwargs)

    manager._lexical_completion_fallback = requires_lexical_budget
    identity = _SearchIdentity(
        client_session_id="bounded-lexical-tail",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    from neural_weasel.response_workers import after_response

    with after_response(), manager._state_lock:
        session = manager._new_session(identity, None)
        foreground_deadline = manager.clock() + 0.001
        with pytest.raises(CandidatePageTimeout):
            manager._freeze_next_page(
                session,
                page_index=0,
                page_size=7,
                absolute_deadline=foreground_deadline,
            )
        foreground_deadline = None

    manager._maybe_start_page_preparation(session)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with manager._state_lock:
            if len(session.frozen_pages) == 5:
                break
        time.sleep(0.01)
    with manager._state_lock:
        assert len(session.frozen_pages) == 5
        assert len(session.frozen_pages[0].candidates) == 7
        assert sum(len(current.candidates) for current in session.frozen_pages.values()) == 35


def test_complete_lexical_tail_is_freezable_while_neural_frontier_is_pending(
    make_index,
) -> None:
    """A pending neural path must not hide the deterministic lexical tail."""

    logits = np.full(32, -20.0, dtype=np.float32)
    runtime = StagedPageZeroRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(_productive_two_syllable_index(make_index)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="lexical-tail-frontier",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
        model_candidate = Candidate(
            text="你好",
            pinyin="ni'hao",
            consumed_keys=5,
            score=20.0,
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=2,
            token_path=(31,),
            predicted_syllables=1,
        )
        lexical_candidates = [
            Candidate(
                text=f"词{index}",
                pinyin="ni'hao",
                consumed_keys=5,
                score=float(-index),
                context_epoch=0,
                coverage=False,
                completes_input=True,
                syllables=2,
                constraint_kind="pinyin_lexical_fallback",
                token_path=(index,),
                ranking_tier=50,
                predicted_syllables=3,
            )
            for index in range(1, 7)
        ]
        session.pending = [model_candidate, *lexical_candidates]
        session.frontier = [
            _SearchPath(
                text="你",
                pinyin_path=("ni",),
                token_path=(1,),
                score=19.0,
                predicted_syllables=1,
            )
        ]

        freezable = manager._freezable_candidates(session)

    assert [candidate.text for candidate in freezable] == [
        "你好",
        *[f"词{index}" for index in range(1, 7)],
    ]


def test_cancelled_continuation_does_not_block_lexical_page_preparation(
    make_index,
    monkeypatch,
) -> None:
    """A retiring CUDA worker must not hold the model-free Trie tail hostage."""

    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id in range(1, 7):
        logits[token_id] = 20.0 - token_id * 0.01
    runtime = StagedPageZeroRuntime(logits)
    runtime.release_second.set()
    rows = [
        (31, "你好", "nihao", "ni'hao", 2, 0),
        *[
            (token_id, text, "ni", "ni", 1, 0)
            for token_id, text in enumerate("你泥拟逆妮倪", start=1)
        ],
        *[
            (token_id, text, "hao", "hao", 1, 0)
            for token_id, text in enumerate("好号浩豪毫郝皓", start=11)
        ],
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    manager = engine.candidate_pages
    original_maybe_start = manager._maybe_start_page_preparation
    monkeypatch.setattr(manager, "_maybe_start_page_preparation", lambda session: None)
    original_fallback = manager._lexical_completion_fallback
    calls = 0
    preparer_launched = threading.Event()
    preparer_lexical = threading.Event()

    def foreground_limited(*args, **kwargs):
        nonlocal calls
        calls += 1
        if preparer_launched.is_set():
            preparer_lexical.set()
        candidates = original_fallback(*args, **kwargs)
        return candidates[:7] if calls == 1 else candidates

    monkeypatch.setattr(manager, "_lexical_completion_fallback", foreground_limited)
    first = _page(engine, revision=1)
    assert len(first.candidates) == 7

    retiring = threading.Event()
    retiring.set()
    retired = threading.Event()
    with manager._state_lock:
        manager._background_searches.add(first.candidate_set_id)
        manager._background_cancel_events[first.candidate_set_id] = retiring
        manager._background_search_events[first.candidate_set_id] = retired
    monkeypatch.setattr(manager, "_maybe_start_page_preparation", original_maybe_start)
    preparer_launched.set()
    original_maybe_start(manager._sessions[first.candidate_set_id])

    completion = manager._page_preparation_events.get(first.candidate_set_id)
    assert completion is not None, "the cancelled continuation blocked Trie preparation"
    try:
        assert preparer_lexical.wait(1.0), "Trie work did not run before scorer retirement"
    finally:
        with manager._state_lock:
            manager._background_searches.discard(first.candidate_set_id)
            manager._background_cancel_events.pop(first.candidate_set_id, None)
            manager._background_search_events.pop(first.candidate_set_id, None)
        retired.set()
    assert completion.wait(1.0)
    pages = [first]
    for page_index in range(1, 5):
        pages.append(
            engine.query_candidate_page(
                client_session_id="same-client",
                composition_revision=1,
                context_epoch=0,
                context_session=None,
                source_revision=None,
                language_mode="chinese_first",
                raw_keys="nihao",
                page_index=page_index,
                candidate_set_id=first.candidate_set_id,
            )
        )
    assert [len(page.candidates) for page in pages] == [7, 7, 7, 7, 7]


def test_retiring_continuation_hands_off_incomplete_page_preparation(
    make_index,
    monkeypatch,
) -> None:
    """A cancelled search must not strand the only later-page preparer."""

    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="retiring-preparer",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    candidates = [
        Candidate(
            text=f"词{index}",
            pinyin="ni'hao",
            consumed_keys=5,
            score=float(-index),
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=2,
            constraint_kind="pinyin_lexical_fallback",
            ranking_tier=50,
            static_rank=index,
        )
        for index in range(35)
    ]
    with manager._state_lock:
        session = manager._new_session(identity, None)
        session.frontier = []
        session.exhausted = True
        session.pending = candidates[:7]
        manager._freeze_next_page(session, 0, 7, manager.clock() + 0.035)
        assert list(session.frozen_pages) == [0]
        session.pending.append(candidates[7])
        retiring = threading.Event()
        retiring.set()
        retired = threading.Event()
        manager._background_searches.add(session.candidate_set_id)
        manager._background_cancel_events[session.candidate_set_id] = retiring
        manager._background_search_events[session.candidate_set_id] = retired

    monkeypatch.setattr(manager, "_lexical_completion_fallback", lambda *args, **kwargs: [])
    manager._maybe_start_page_preparation(session)
    completion = manager._page_preparation_events[session.candidate_set_id]
    try:
        assert not completion.wait(0.2), "preparer exited before retired search completed"
    finally:
        with manager._state_lock:
            session.pending.extend(candidates[8:])
            manager._background_searches.discard(session.candidate_set_id)
            manager._background_cancel_events.pop(session.candidate_set_id, None)
            manager._background_search_events.pop(session.candidate_set_id, None)
        retired.set()

    assert completion.wait(2.0)
    assert [len(page.candidates) for page in session.frozen_pages.values()] == [7] * 5


def test_ready_lexical_pages_do_not_wait_for_retiring_continuation(make_index) -> None:
    """A cancelled scorer must not delay pages already backed by 35 candidates."""

    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="ready-lexical-pages",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    candidates = [
        Candidate(
            text=f"词{index}",
            pinyin="ni'hao",
            consumed_keys=5,
            score=float(-index),
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=2,
            constraint_kind="pinyin_lexical_fallback",
            ranking_tier=50,
            static_rank=index,
        )
        for index in range(35)
    ]
    with manager._state_lock:
        session = manager._new_session(identity, None)
        session.frontier = []
        session.exhausted = True
        session.pending = candidates[:7]
        manager._freeze_next_page(session, 0, 7, manager.clock() + 0.035)
        session.pending.extend(candidates[7:])
        assert len(manager._freezable_candidates(session)) == 28
        retiring = threading.Event()
        retiring.set()
        retired = threading.Event()
        manager._background_searches.add(session.candidate_set_id)
        manager._background_cancel_events[session.candidate_set_id] = retiring
        manager._background_search_events[session.candidate_set_id] = retired

    manager._maybe_start_page_preparation(session)
    completion = manager._page_preparation_events[session.candidate_set_id]
    try:
        completed_before_retirement = completion.wait(0.5)
    finally:
        with manager._state_lock:
            manager._background_searches.discard(session.candidate_set_id)
            manager._background_cancel_events.pop(session.candidate_set_id, None)
            manager._background_search_events.pop(session.candidate_set_id, None)
        retired.set()
        assert completion.wait(2.0)

    assert completed_before_retirement, "ready pages waited for the cancelled scorer"
    assert [len(page.candidates) for page in session.frozen_pages.values()] == [7] * 5


def test_other_session_continuation_retirement_wakes_deferred_pages(
    make_index, monkeypatch
) -> None:
    """A same-input scorer must hand page preparation to a waiting client session."""

    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    first_identity = _SearchIdentity(
        client_session_id="first-client",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    second_identity = _SearchIdentity(
        client_session_id="second-client",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    candidates = [
        Candidate(
            text=f"词{index}",
            pinyin="ni'hao",
            consumed_keys=5,
            score=float(-index),
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=2,
            constraint_kind="pinyin_lexical_fallback",
            ranking_tier=50,
            static_rank=index,
        )
        for index in range(35)
    ]
    with manager._state_lock:
        first = manager._new_session(first_identity, None)
        first.frontier = []
        first.exhausted = True
        first.pending = candidates[:7]
        manager._freeze_next_page(first, 0, 7, manager.clock() + 0.035)
        first.pending.extend(candidates[7:])
        second = manager._new_session(second_identity, None)
        second.frontier = []
        second.exhausted = True
        second.pending = candidates[:7]
        manager._freeze_next_page(second, 0, 7, manager.clock() + 0.035)
        second.pending.extend(candidates[7:])
        assert second.frozen_pages[0].has_more
        assert len(second.frozen_pages) == 1
        assert len(manager._freezable_candidates(second)) == 28
        assert first.candidate_set_id != second.candidate_set_id
        assert manager._async_identity_key(first.identity) == manager._async_identity_key(
            second.identity
        )
        active = threading.Event()
        manager._background_searches.add(first.candidate_set_id)
        manager._background_cancel_events[first.candidate_set_id] = active
        manager._background_search_events[first.candidate_set_id] = threading.Event()

    manager._maybe_start_page_preparation(second)
    assert second.candidate_set_id not in manager._page_preparations
    monkeypatch.setattr(manager, "_expand_background_frontier_batch", lambda *a, **kw: 0)
    manager._run_background_continuation(first, manager._async_identity_key(first_identity), active)

    completion = manager._page_preparation_events.get(second.candidate_set_id)
    assert completion is not None, "the other scorer left page preparation unscheduled"
    assert completion.wait(1.0)
    assert [len(page.candidates) for page in second.frozen_pages.values()] == [7] * 5


def test_published_page_zero_hands_search_to_page_preparer(make_index, monkeypatch) -> None:
    """A post-publication scorer must not block the worker that fills later pages."""

    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="page-preparer-handoff",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    candidates = [
        Candidate(
            text=f"词{index}",
            pinyin="ni'hao",
            consumed_keys=5,
            score=float(-index),
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=2,
            constraint_kind="pinyin_lexical_fallback",
            ranking_tier=50,
            static_rank=index,
        )
        for index in range(7)
    ]
    started: list[str] = []
    monkeypatch.setattr(
        "neural_weasel.neural_candidate_pages_scored.start_worker",
        lambda worker: started.append(worker.name),
    )
    monkeypatch.setattr(
        "neural_weasel.neural_candidate_pages.start_worker",
        lambda worker: started.append(worker.name),
    )

    with manager._state_lock:
        session = manager._new_session(identity, None)
        session.pending = candidates
        session.frontier = []
        session.exhausted = True
        first = manager._freeze_next_page(session, 0, 7, manager.clock() + 0.035)
        assert first.has_more
        assert list(session.frozen_pages) == [0]
        # The scored layer makes this call after _freeze_next_page returns.
        # An unfinished Han path makes a new scorer eligible on the old path.
        session.frontier = [SimpleNamespace(script="han", matched_letters=0)]
        session.exhausted = False
        manager._start_background_continuation(session)

    assert session.candidate_set_id not in manager._background_searches
    manager._maybe_start_page_preparation(session)
    assert session.candidate_set_id in manager._page_preparations
    assert started == [f"neural-pages-{session.candidate_set_id[:8]}"]
    manager.clear_sessions()


def test_lexical_tail_finds_exact_path_before_abbreviation_flood(make_index) -> None:
    """An exact two-token path must survive 35 deeper shorthand completions."""

    suffixes = [
        character
        for codepoint in range(0x4E00, 0x5000)
        if is_simplified_han(character := chr(codepoint))
    ][:40]
    assert len(suffixes) == 40
    rows = [
        (1, "注意", "zhuyi", "zhu'yi", 2, 0),
        (2, "注意到", "zhuyidao", "zhu'yi'dao", 3, 0),
        (3, "的", "de", "de", 1, 0),
        *[
            (token_id, character, "e", "e", 1, 0)
            for token_id, character in enumerate(suffixes, start=4)
        ],
    ]
    logits = np.full(44, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 20.0
    logits[3] = 5.0
    logits[4:] = 15.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="exact-prefix-flood",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyide",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    found = []
    for candidate in manager._iter_lexical_completion_candidates(session):
        if candidate is not None:
            found.append(candidate)
        if len(found) == 35:
            break

    assert len(found) == 35
    assert ("注意的", (1, 3)) in {(candidate.text, candidate.token_path) for candidate in found}


def test_exact_spelling_survives_full_model_extension_bucket(make_index) -> None:
    """A complete typed spelling must not vanish behind 35 longer readings."""

    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(np.zeros(3, dtype=np.float32))),
        pinyin_constraint=PinyinConstraint(
            make_index(
                [
                    (1, "注意", "zhuyi", "zhu'yi", 2, 0),
                    (2, "的", "de", "de", 1, 0),
                ]
            )
        ),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="exact-spelling-capacity",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyide",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
        model_extensions = [
            Candidate(
                text=f"候选{index}",
                pinyin="zhu'yi'deng",
                consumed_keys=7,
                score=float(100 - index),
                context_epoch=0,
                coverage=False,
                completes_input=True,
                syllables=3,
                model_score=float(100 - index),
                token_path=(index + 3,),
                predicted_syllables=0,
            )
            for index in range(35)
        ]
        session.pending = list(model_extensions)
        session.pending.append(
            Candidate(
                text="注意的",
                pinyin="zhu'yi'de",
                consumed_keys=7,
                score=-100.0,
                context_epoch=0,
                coverage=False,
                completes_input=True,
                syllables=3,
                constraint_kind="pinyin_lexical_fallback",
                token_path=(1, 2),
                ranking_tier=50,
            )
        )
        manager._sort_pending(session)

    assert "注意的" in {candidate.text for candidate in session.pending[:35]}

    with manager._state_lock:
        discovery_identity = _SearchIdentity(
            client_session_id="exact-spelling-discovery",
            composition_revision=1,
            context_epoch=0,
            context_session=None,
            source_revision=None,
            mode=NeuralLanguageMode.CHINESE_FIRST,
            raw_keys="zhuyide",
        )
        discovery = manager._new_session(discovery_identity, None)
        discovery.pending = list(model_extensions)
        discovery.seen_candidates = {
            (candidate.text, candidate.consumed_keys) for candidate in model_extensions
        }
        discovery.frontier = []
        discovery.exhausted = True
        manager._freeze_next_page(
            discovery,
            page_index=0,
            page_size=7,
            absolute_deadline=manager.clock() + 0.035,
        )
        assert len(discovery.frozen_pages) == 1

    manager._run_page_preparation(
        discovery, threading.Event(), threading.Event(), threading.Event()
    )
    frozen = [
        candidate for page in discovery.frozen_pages.values() for candidate in page.candidates
    ]
    assert len(frozen) == 35
    assert "注意的" in {candidate.text for candidate in frozen}


def test_lexical_tail_resumes_past_dead_exact_prefix_roots(make_index) -> None:
    """Unproductive exact-prefix roots must not block cursor progress."""

    dead_roots = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:128]
    assert len(dead_roots) == 128
    rows = [
        (1, "注意", "zhuyi", "zhu'yi", 2, 0),
        (2, "的", "de", "de", 1, 0),
        *[
            (token_id, character * MAX_HAN_CHARACTERS, "zhuyi", "zhu'yi", 2, 0)
            for token_id, character in enumerate(dead_roots, start=3)
        ],
    ]
    logits = np.zeros(len(rows) + 1, dtype=np.float32)
    logits[3:] = 20.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="dead-exact-prefix-roots",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyide",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    pending_steps = 0
    for candidate in manager._iter_lexical_completion_candidates(session):
        if candidate is None:
            pending_steps += 1
            continue
        assert (candidate.text, candidate.token_path) == ("注意的", (1, 2))
        break
    else:
        pytest.fail("the lexical cursor exhausted before finding the exact path")
    assert pending_steps > 0


def test_lexical_tail_reaches_incomplete_syllable_before_prefix_flood(make_index) -> None:
    """A legal ``zhuyid`` completion must not wait behind every ``zhuyi`` root."""

    prefix_chars = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:128]
    assert len(prefix_chars) == 128
    rows = [
        (1, "注意", "zhuyi", "zhu'yi", 2, 0),
        (2, "的", "de", "de", 1, 0),
        *[
            (token_id, f"注{character}", "zhuyi", "zhu'yi", 2, 0)
            for token_id, character in enumerate(prefix_chars, start=3)
        ],
    ]
    logits = np.zeros(len(rows) + 1, dtype=np.float32)
    logits[3:] = 20.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="incomplete-prefix-flood",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
    assert not manager.matcher.is_complete_syllable_sequence("zhuyid")

    expanded_suffixes = []
    original_matches = manager.matcher.iter_neural_matches

    def counted_matches(raw, start=0, boundaries=None, **kwargs):
        expanded_suffixes.append(start)
        yield from original_matches(raw, start, boundaries, **kwargs)

    manager.matcher.iter_neural_matches = counted_matches
    pending_steps = 0
    for candidate in manager._iter_lexical_completion_candidates(session):
        if candidate is None:
            pending_steps += 1
            continue
        assert len(expanded_suffixes) <= 1, "completed reading starved behind prefix roots"
        assert candidate.completes_input
        assert len(candidate.token_path) == 2
        assert candidate.pinyin.endswith("'de")
        break
    else:
        pytest.fail("the lexical cursor exhausted before finding the completed reading")


def test_incomplete_lexical_tail_does_not_fill_all_pages_from_one_root(make_index) -> None:
    """A prolific root must not hide another legal completion past 35 slots."""

    suffix_chars = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:40]
    assert len(suffix_chars) == 40
    rows = [
        (1, "逐一", "zhuyi", "zhu'yi", 2, 0),
        (2, "注意", "zhuyi", "zhu'yi", 2, 0),
        (3, "的", "de", "de", 1, 0),
        *[
            (token_id, character, "di", "di", 1, 0)
            for token_id, character in enumerate(suffix_chars, start=4)
        ],
    ]
    logits = np.zeros(len(rows) + 1, dtype=np.float32)
    logits[1] = 20.0  # The prolific root wins the empty-context logit tie-break.
    logits[2] = 10.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="incomplete-one-root-flood",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    results = []
    for candidate in manager._iter_lexical_completion_candidates(session):
        if candidate is not None:
            results.append(candidate)
        if len(results) == 35:
            break
    assert len(results) == 35
    assert "注意的" in {candidate.text for candidate in results}
    assert len({candidate.token_path[0] for candidate in results}) > 1
    assert {candidate.token_path[0] for candidate in results[:7]} == {1}
    assert any(candidate.token_path[0] == 2 for candidate in results[7:14])


def test_incomplete_lexical_tail_reaches_eighth_root_within_capacity(make_index) -> None:
    """A later root must not be hidden by the first seven prolific roots."""

    root_texts = ("逐一", "主意", "竹艺", "煮艺", "珠艺", "猪艺", "筑艺", "注意")
    suffixes = "的地得底低第帝"
    rows = [
        (token_id, text, "zhuyi", "zhu'yi", 2, 0)
        for token_id, text in enumerate(root_texts, start=1)
    ]
    rows.extend(
        (token_id, text, "de", "de", 1, 0) for token_id, text in enumerate(suffixes, start=9)
    )
    logits = np.zeros(16, dtype=np.float32)
    for token_id in range(1, 9):
        logits[token_id] = float(100 - token_id)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="eighth-root-capacity",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    results = []
    for candidate in manager._iter_lexical_completion_candidates(session):
        if candidate is not None:
            results.append(candidate)
        if len(results) == 35:
            break
    assert len(results) == 35
    assert "注意的" in {candidate.text for candidate in results}


def test_lexical_hint_matching_avoids_pending_root_cartesian_scan(make_index) -> None:
    """Hints should look up text prefixes, not compare every root to every candidate."""

    root_chars = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:64]
    rows = [
        (token_id, f"注{character}", "zhuyi", "zhu'yi", 2, 0)
        for token_id, character in enumerate(root_chars, start=1)
    ]
    logits = np.zeros(len(rows) + 1, dtype=np.float32)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="large-hint-scan",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    prefix_checks = 0
    prefix_slices = 0

    class CountedText(str):
        def startswith(self, prefix, *args):
            nonlocal prefix_checks
            prefix_checks += 1
            return super().startswith(prefix, *args)

        def __getitem__(self, key):
            nonlocal prefix_slices
            prefix_slices += 1
            return super().__getitem__(key)

    session.pending = [
        Candidate(
            text=CountedText("注意到"),
            pinyin="zhu'yi'dao",
            consumed_keys=6,
            score=0.0,
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=3,
            token_path=(100 + index,),
            ranking_tier=1,
        )
        for index in range(64)
    ]
    cursor = manager._iter_lexical_completion_candidates(session)
    while True:
        before = prefix_slices
        try:
            next(cursor)
        except StopIteration:
            break
        assert prefix_slices - before <= 64
    assert prefix_checks <= 64
    assert prefix_slices == 64 * 2


def test_lexical_hint_matching_yields_with_duplicate_root_text(make_index) -> None:
    """A large same-text bucket must not monopolize one cursor advance."""

    rows = [(token_id, "注意", "zhuyi", "zhu'yi", 2, 0) for token_id in range(1, 129)]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(np.zeros(129))),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="duplicate-hint-roots",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    comparisons = 0

    class CountedPinyin(str):
        def startswith(self, prefix, *args):
            nonlocal comparisons
            comparisons += 1
            return super().startswith(prefix, *args)

    session.pending = [
        Candidate(
            text="注意到",
            pinyin=CountedPinyin("zhu'yi'dao"),
            consumed_keys=6,
            score=0.0,
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=3,
            ranking_tier=1,
        )
    ]
    cursor = manager._iter_lexical_completion_candidates(session)
    while True:
        before = comparisons
        try:
            next(cursor)
        except StopIteration:
            break
        assert comparisons - before <= 64
    assert comparisons == 128


def test_lexical_root_heap_build_yields_in_bounded_batches(make_index, monkeypatch) -> None:
    """Heap initialization must not occupy one uninterrupted cursor step."""

    root_chars = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:256]
    rows = [
        (token_id, f"注{character}", "zhuyi", "zhu'yi", 2, 0)
        for token_id, character in enumerate(root_chars, start=1)
    ]
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(
            StagedPageZeroRuntime(np.zeros(len(rows) + 1, dtype=np.float32))
        ),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="large-lexical-root-heap",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)

    pushes = 0
    original_push = heapq.heappush

    def counted_push(heap, item):
        nonlocal pushes
        pushes += 1
        return original_push(heap, item)

    monkeypatch.setattr(heapq, "heappush", counted_push)
    cursor = manager._iter_lexical_completion_candidates(session)
    while True:
        before = pushes
        try:
            next(cursor)
        except StopIteration:
            break
        assert pushes - before <= 128
    assert pushes == 256


def test_incomplete_lexical_tail_uses_model_text_prefix_without_token_prefix(make_index) -> None:
    """A scored phrase can identify its shorter Han root despite a separate token."""

    suffixes = [
        character
        for codepoint in range(0x4E00, 0x6000)
        if is_simplified_han(character := chr(codepoint))
    ][:12]
    rows = [
        (1, "逐一", "zhuyi", "zhu'yi", 2, 0),
        (2, "注意", "zhuyi", "zhu'yi", 2, 0),
        (3, "的", "de", "de", 1, 0),
        *[
            (token_id, character, "di", "di", 1, 0)
            for token_id, character in enumerate(suffixes, start=4)
        ],
    ]
    logits = np.zeros(len(rows) + 1, dtype=np.float32)
    logits[1] = 20.0
    logits[2] = 10.0
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(StagedPageZeroRuntime(logits)),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        client_session_id="model-phrase-root-hint",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="zhuyid",
    )
    with manager._state_lock:
        session = manager._new_session(identity, None)
    session.pending.append(
        Candidate(
            text="注意到",
            pinyin="zhu'yi'dao",
            consumed_keys=6,
            score=0.0,
            context_epoch=0,
            coverage=False,
            completes_input=True,
            syllables=3,
            constraint_kind="pinyin",
            token_id=99,
            token_path=(99,),
            ranking_tier=1,
        )
    )

    results = manager._lexical_completion_fallback(
        session,
        limit=7,
        absolute_deadline=manager.clock() + 1.0,
    )
    assert len(results) == 7
    assert results[0].text == "注意的"
    assert results[0].token_path == (2, 3)


def test_lexical_tail_prefers_exact_spelling_over_longer_readings(make_index) -> None:
    """``hua`` must outrank higher-logit ``huan``/``huang`` completions."""

    exact = "化花华划画话桦哗滑猾"
    extended = (
        ("患缓还环换幻焕欢桓寰", "huan"),
        ("黄皇荒慌晃煌蝗惶簧磺", "huang"),
        ("怀淮槐踝坏", "huai"),
    )
    rows = [(1, "最小", "zuixiao", "zui'xiao", 2, 0)]
    token_id = 2
    for text in exact:
        rows.append((token_id, text, "hua", "hua", 1, 0))
        token_id += 1
    for texts, pinyin in extended:
        for text in texts:
            rows.append((token_id, text, pinyin, pinyin, 1, 0))
            token_id += 1

    logits = np.full(token_id + 1, -20.0, dtype=np.float32)
    logits[1] = 20.0
    # Make the incorrect longer readings more attractive to the baseline.
    for current in range(2, token_id):
        logits[current] = 5.0 if current < 2 + len(exact) else 15.0
    logits[2] = 10.0  # 化 is the best exact ``hua`` candidate.
    runtime = StagedPageZeroRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    page = engine.query_candidate_page(
        client_session_id="lexical-structure",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="zuixiaohua",
        page_index=0,
    )

    assert page.candidates[0].text == "最小化"
    assert all(candidate.pinyin.replace("'", "") == "zuixiaohua" for candidate in page.candidates)


def test_lexical_tail_prefers_fewer_syllables_for_same_spelling(make_index) -> None:
    """``ming'xian'dui`` must beat the over-segmented ``ming'xia+n+d-ui`` tail."""

    rows = [
        (1, "名下", "mingxia", "ming'xia", 2, 0),
        (2, "明显", "mingxian", "ming'xian", 2, 0),
    ]
    token_id = 3
    for text, pinyin in zip(
        "牛年内你乃您浓嫩能宁",
        ("niu", "nian", "nei", "ni", "nai", "nin", "nong", "nen", "neng", "ning"),
        strict=True,
    ):
        rows.append((token_id, text, pinyin, pinyin, 1, 0))
        token_id += 1
    for text in "对堆兑队碓怼镦祋濧譵":
        rows.append((token_id, text, "dui", "dui", 1, 0))
        token_id += 1

    logits = np.full(token_id + 1, -20.0, dtype=np.float32)
    logits[1] = 20.0  # Prefer the bad ``名下`` root by baseline score.
    logits[2] = 5.0
    logits[3:13] = 15.0
    logits[13:token_id] = np.linspace(10.0, 1.0, token_id - 13, dtype=np.float32)
    runtime = StagedPageZeroRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    page = engine.query_candidate_page(
        client_session_id="lexical-structure",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="mingxiandui",
        page_index=0,
    )

    assert "明显对" in {candidate.text for candidate in page.candidates}
    assert all(candidate.syllables == 3 for candidate in page.candidates)


def test_latin_fallback_does_not_freeze_chinese_page_before_han_is_ready(
    make_index,
) -> None:
    """A freezable Latin fallback cannot masquerade as a ready Chinese page."""

    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 10.0
    logits[2] = 9.0
    logits[7] = 8.0
    runtime = BlockingContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(
            (LatinCompletion(text="nihaoword", token_path=(7,)),)
        ),
    )
    engine.initialize_neural_baseline()

    with pytest.raises(
        CandidatePageTimeout, match="complete candidate page is still being prepared"
    ):
        _page(engine, revision=1)
    assert runtime.first_started.wait(0.5)

    completion = next(iter(engine.candidate_pages._background_search_events.values()))
    runtime.release_first.set()
    assert completion.wait(1.0)

    page = _page(engine, revision=1)
    assert any(candidate.script == "han" for candidate in page.candidates)
    assert all(
        candidate.completes_input for candidate in page.candidates if candidate.script == "han"
    )
