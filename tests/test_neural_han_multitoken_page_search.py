from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import CandidatePageTimeout
from neural_weasel.simplified_chinese import is_simplified_han
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class MultiTokenRuntime:
    logits: np.ndarray
    calls: int = 0
    continuation_calls: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(default_factory=list)

    def load(self) -> None:
        pass

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
        assert root[0] == "root"
        assert deadline_ms > 0
        outputs = []
        for raw_path, raw_allowed in zip(
            token_paths,
            allowed_token_sets,
            strict=True,
        ):
            path = tuple(int(token_id) for token_id in raw_path)
            allowed = tuple(int(token_id) for token_id in raw_allowed)
            self.continuation_calls.append((path, allowed))
            outputs.append(
                np.asarray(
                    [1000.0 if token_id == 7 else 20.0 - float(token_id) for token_id in allowed],
                    dtype=np.float32,
                )
            )
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


class LateRootRuntime(MultiTokenRuntime):
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
        outputs = []
        for raw_path, raw_allowed in zip(token_paths, allowed_token_sets, strict=True):
            path = tuple(int(token_id) for token_id in raw_path)
            allowed = tuple(int(token_id) for token_id in raw_allowed)
            self.continuation_calls.append((path, allowed))
            values = np.full(len(allowed), -120.0, dtype=np.float32)
            values[allowed.index(35)] = 0.0
            if path == (33,):
                values[allowed.index(34)] = 30.0
            outputs.append(values)
        return outputs


class LateEdgeRuntime(MultiTokenRuntime):
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
        outputs = []
        for raw_path, raw_allowed in zip(token_paths, allowed_token_sets, strict=True):
            path = tuple(int(token_id) for token_id in raw_path)
            allowed = tuple(int(token_id) for token_id in raw_allowed)
            self.continuation_calls.append((path, allowed))
            values = np.full(len(allowed), -120.0, dtype=np.float32)
            values[allowed.index(39)] = 0.0
            if path == (1,):
                for token_id in range(2, 34):
                    values[allowed.index(token_id)] = 10.0 - token_id
                values[allowed.index(34)] = -30.0
            elif path == (1, 34):
                values[allowed.index(35)] = 20.0
            outputs.append(values)
        return outputs


def _engine(make_index):
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
            (3, "吗", "ma", "ma", 1, 0),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1:4] = [10.0, 9.0, 8.0]
    logits[7] = 1000.0
    runtime = MultiTokenRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine, raw: str, **overrides):
    values = {
        "client_session_id": "han-multitoken-session",
        "composition_revision": 1,
        "context_epoch": 0,
        "context_session": None,
        "source_revision": None,
        "language_mode": "chinese_first",
        "raw_keys": raw,
        "page_index": 0,
    }
    values.update(overrides)
    return engine.query_candidate_page(**values)


def _wait_for_async_han(engine: BilingualImeEngine, candidate_set_id: str) -> None:
    manager = engine.candidate_pages
    with manager._state_lock:
        session = manager._sessions[candidate_set_id]
        identity_key = manager._async_identity_key(session.identity)
        if identity_key in manager._async_han_cache:
            return
        event = manager._background_search_events.get(candidate_set_id)
    assert event is not None
    assert event.wait(1.0)
    with manager._state_lock:
        assert identity_key in manager._async_han_cache


def _coherent_page(engine: BilingualImeEngine, raw: str, **overrides):
    try:
        return _page(engine, raw, **overrides)
    except CandidatePageTimeout:
        manager = engine.candidate_pages
        with manager._state_lock:
            candidate_set_id = next(iter(manager._sessions))
            completion = manager._background_search_events[candidate_set_id]
        assert completion.wait(1.0)
        return _page(engine, raw, **overrides)


def _later_candidates(engine: BilingualImeEngine, candidate_set_id: str):
    manager = engine.candidate_pages
    preparation = manager._page_preparation_events.get(candidate_set_id)
    assert preparation is not None
    assert preparation.wait(1.0)
    with manager._state_lock:
        session = manager._sessions[candidate_set_id]
        return tuple(
            candidate
            for page_index, page in session.frozen_pages.items()
            if page_index > 0
            for candidate in page.candidates
        )


def test_byte_fragment_han_is_hidden_until_complete_and_remains_reachable(make_index) -> None:
    index = make_index(
        [
            (1, "比", "bi", "bi", 1, 0),
            (None, "敝", "bi", "bi", 1, 1, (4, 5)),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 9.0
    logits[4] = 8.0
    runtime = MultiTokenRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _coherent_page(engine, "bi")
    assert all("\ufffd" not in candidate.text for candidate in first.candidates)
    later = _later_candidates(engine, first.candidate_set_id)
    target = next(candidate for candidate in (*first.candidates, *later) if candidate.text == "敝")
    assert target.token_path == (4, 5)
    assert target.consumed_keys == 2
    assert target.completes_input
    assert target.ranking_tier == 0
    assert target.model_score is not None and target.model_score < -900
    assert ((4,), tuple(range(8))) in runtime.continuation_calls


@pytest.mark.parametrize(
    ("expected_text", "expected_path"),
    [
        ("比敝", (1, 4, 5)),
        ("敝比", (4, 5, 1)),
        ("敝敝", (4, 5, 4, 5)),
    ],
)
def test_multitoken_han_is_selectable_inside_a_phrase(
    make_index,
    expected_text: str,
    expected_path: tuple[int, ...],
) -> None:
    index = make_index(
        [
            (1, "比", "bi", "bi", 1, 0),
            (None, "敝", "bi", "bi", 1, 1, (4, 5)),
        ]
    )
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 9.0
    logits[4] = 8.0
    runtime = MultiTokenRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _coherent_page(engine, "bibi")
    later = _later_candidates(engine, first.candidate_set_id)
    target = next(
        candidate for candidate in (*first.candidates, *later) if candidate.text == expected_text
    )
    assert target.token_path == expected_path
    assert target.consumed_keys == 4
    assert target.completes_input
    assert target.ranking_tier == 0


def test_thirty_third_root_can_recover_and_win_after_continuation(make_index) -> None:
    heads = [
        character
        for codepoint in range(0x4E00, 0x5000)
        if is_simplified_han(character := chr(codepoint))
    ][:33]
    assert len(heads) == 33
    index = make_index(
        [(token_id, head, "bi", "bi", 1, 0) for token_id, head in enumerate(heads, start=1)]
        + [(34, "好", "hao", "hao", 1, 0)]
    )
    logits = np.full(40, -100.0, dtype=np.float32)
    logits[1:33] = np.linspace(20.0, 1.0, 32, dtype=np.float32)
    logits[33] = -10.0
    runtime = LateRootRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _coherent_page(engine, "bih")
    later = _later_candidates(engine, first.candidate_set_id)
    assert any(path == (33,) for path, _allowed in runtime.continuation_calls)
    target = next(
        candidate for candidate in (*first.candidates, *later) if candidate.text == heads[32] + "好"
    )
    assert target.token_path == (33, 34)
    assert target.ranking_tier == 0
    assert target.completes_input
    assert (33,) in {path for path, _ in runtime.continuation_calls}


def test_thirty_third_child_can_recover_and_win_after_continuation(
    make_index, monkeypatch: pytest.MonkeyPatch
) -> None:
    suffixes = [
        character
        for codepoint in range(0x4E00, 0x5000)
        if is_simplified_han(character := chr(codepoint))
    ][:33]
    assert len(suffixes) == 33
    index = make_index(
        [(1, "比", "bi", "bi", 1, 0)]
        + [
            (token_id, character, "hao", "hao", 1, 0)
            for token_id, character in enumerate(suffixes, start=2)
        ]
        + [(35, "你", "ni", "ni", 1, 0)]
    )
    logits = np.full(40, -100.0, dtype=np.float32)
    logits[1] = 20.0
    runtime = LateEdgeRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    monkeypatch.setattr(
        engine.candidate_pages,
        "_lexical_completion_fallback",
        lambda *_args, **_kwargs: [],
    )

    first = _coherent_page(engine, "bihaoni")
    later = _later_candidates(engine, first.candidate_set_id)
    assert any(path == (1, 34) for path, _allowed in runtime.continuation_calls)
    target = next(
        candidate
        for candidate in (*first.candidates, *later)
        if candidate.text == "比" + suffixes[32] + "你"
    )
    assert target.token_path == (1, 34, 35)
    assert target.ranking_tier == 0
    assert target.completes_input


@pytest.mark.parametrize(
    ("raw", "expected_text", "expected_path"),
    [
        ("nihao", "你好", (1, 2)),
        ("nh", "你好", (1, 2)),
        ("nihaoma", "你好吗", (1, 2, 3)),
    ],
)
def test_exact_han_cover_can_span_multiple_base_tokens(
    make_index,
    raw: str,
    expected_text: str,
    expected_path: tuple[int, ...],
) -> None:
    engine, runtime = _engine(make_index)

    first = _coherent_page(engine, raw)
    assert all(
        candidate.completes_input for candidate in first.candidates if candidate.script == "han"
    )
    refreshed = _page(engine, raw, presentation_refresh=True)
    assert refreshed.candidate_set_id == first.candidate_set_id
    assert refreshed.candidates == first.candidates
    assert refreshed.candidate_ids == first.candidate_ids

    exact = next(candidate for candidate in first.candidates if candidate.text == expected_text)
    assert exact.token_path == expected_path
    assert exact.completes_input is True
    assert exact.consumed_keys == len(raw)
    assert exact.predicted_syllables == 0
    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[0][1] == tuple(range(runtime.logits.size))
    assert all(7 not in candidate.token_path for candidate in refreshed.candidates)


def test_han_continuation_scores_full_vocab_but_generates_only_legal_edges(
    make_index,
) -> None:
    engine, runtime = _engine(make_index)

    first = _coherent_page(engine, "nihaoma")
    refreshed = _page(engine, "nihaoma", presentation_refresh=True)

    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[1][0] == (1, 2)
    assert all(
        allowed == tuple(range(runtime.logits.size))
        for _, allowed in runtime.continuation_calls[:2]
    )
    assert all(7 not in candidate.token_path for candidate in refreshed.candidates)
    exact = next(candidate for candidate in first.candidates if candidate.text == "你好吗")
    assert exact.token_path == (1, 2, 3)


def test_first_multitoken_page_is_coherent_and_immutable(
    make_index,
) -> None:
    engine, _ = _engine(make_index)

    first = _coherent_page(engine, "nihao")
    first_candidates = first.candidates
    first_ids = first.candidate_ids
    assert "你好" in {candidate.text for candidate in first_candidates}

    refreshed = _page(engine, "nihao", presentation_refresh=True)
    assert refreshed.candidate_set_id == first.candidate_set_id
    assert refreshed.candidates == first.candidates
    assert refreshed.candidate_ids == first.candidate_ids
    # Replaying does not mutate the already-returned coherent page zero.
    assert first.candidates == first_candidates
    assert first.candidate_ids == first_ids
    assert "你好" in {candidate.text for candidate in first.candidates}

    repeated_page_zero = _page(engine, "nihao")
    assert repeated_page_zero.candidate_set_id == first.candidate_set_id
    assert repeated_page_zero.candidates == first.candidates
    assert repeated_page_zero.candidate_ids == first.candidate_ids

    # The newly learned baseline path is also available to a later input revision.
    next_revision = _page(engine, "nihao", composition_revision=2)
    assert next_revision.candidate_set_id != first.candidate_set_id
    assert any(candidate.text == "你好" for candidate in next_revision.candidates)
