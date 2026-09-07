from __future__ import annotations

from pathlib import Path

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidate_pages_scored import _selected_log_probs
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint

ROOT = Path(__file__).resolve().parents[1]


class ContinuationRuntime:
    def __init__(self) -> None:
        self.root_logits = np.asarray([0.0, 5.0, 1.0, -2.0], dtype=np.float32)
        self.full_logits_calls = 0
        self.continuation_calls = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.full_logits_calls += 1
        return RuntimeSnapshot(
            self.root_logits,
            before,
            after,
            0.1,
            continuation_root=("root", before, after),
        )

    def continue_from_root(
        self,
        root,
        token_paths,
        allowed_token_sets,
        *,
        deadline_ms: float,
    ):
        del root, deadline_ms
        self.continuation_calls += 1
        outputs = []
        for token_path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            logits = np.full(len(allowed), -20.0, dtype=np.float32)
            if token_path == (1,):
                logits[2] = 8.0  # 你 -> 好
            outputs.append(logits)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _page(
    engine,
    revision: int,
    page_index: int = 0,
    candidate_set_id: str | None = None,
    *,
    presentation_refresh: bool = False,
):
    return engine.query_candidate_page(
        client_session_id="scoring-test",
        composition_revision=revision,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="nihao",
        page_index=page_index,
        candidate_set_id=candidate_set_id,
        deadline_ms=1000.0,
        presentation_refresh=presentation_refresh,
    )


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


def test_selected_log_probs_normalize_over_full_vocabulary() -> None:
    scores = _selected_log_probs([10.0, 0.0, -2.0], [0, 1, 2])

    assert np.all(scores <= 0.0)
    assert np.isclose(float(np.exp(scores).sum()), 1.0, rtol=1e-5, atol=1e-6)
    assert scores[0] > scores[1] > scores[2]


def test_latin_root_reuse_is_baseline_only_and_returns_independent_lists(monkeypatch) -> None:
    from neural_weasel.neural_candidate_pages_scored import (
        NeuralCandidatePageManager,
        _V3CandidatePageManager,
    )
    from neural_weasel.neural_candidates import _literal_candidate

    manager = object.__new__(NeuralCandidatePageManager)
    manager._baseline_latin_roots = {}
    calls = []

    def roots(self, raw, state, epoch):
        calls.append((raw, state, epoch))
        return [_literal_candidate(raw, epoch)], []

    monkeypatch.setattr(_V3CandidatePageManager, "_root_latin_candidates_and_frontier", roots)
    first, frontier = manager._root_latin_candidates_and_frontier("m", None, 0)
    first.clear()
    frontier.append("caller mutation")
    second, second_frontier = manager._root_latin_candidates_and_frontier("m", None, 17)
    assert len(calls) == 1
    assert second[0].context_epoch == 17
    assert second_frontier == []
    assert manager._baseline_latin_roots["m"][0][0].context_epoch == 0
    private_state = object()
    manager._root_latin_candidates_and_frontier("m", private_state, 18)
    manager._root_latin_candidates_and_frontier("m", private_state, 19)
    manager._root_latin_candidates_and_frontier("many", None, 0)
    manager._root_latin_candidates_and_frontier("many", None, 0)
    assert len(calls) == 5
    assert set(manager._baseline_latin_roots) == {"m"}


def test_dirty_latin_prewarm_invalidates_root_before_rebuilding(monkeypatch) -> None:
    from neural_weasel.neural_candidate_pages_scored import NeuralCandidatePageManager

    manager = object.__new__(NeuralCandidatePageManager)
    manager._baseline_scores = np.zeros(4)
    manager._dirty_single_letter_prewarms = {"m"}
    manager._baseline_latin_roots = {"m": ((), ())}
    manager._baseline_single_letter = {}

    def rebuild(**kwargs):
        assert kwargs["raw_keys"] not in manager._baseline_latin_roots
        return [], [], "baseline"

    monkeypatch.setattr(manager, "_root_candidates", rebuild)
    manager._refresh_dirty_single_letter_prewarms()
    assert not manager._dirty_single_letter_prewarms
    assert len(manager._baseline_single_letter) == 2


def test_baseline_multitoken_han_path_becomes_page_zero_supplement(make_index) -> None:
    index = make_index(
        [
            (1, "你", "ni", "ni", 1, 0),
            (2, "好", "hao", "hao", 1, 0),
        ]
    )
    runtime = ContinuationRuntime()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    first = _page(engine, 1)
    assert runtime.full_logits_calls == 1
    assert not any(candidate.text == "你好" for candidate in first.candidates)

    _wait_for_async_han(engine, first.candidate_set_id)
    second = _page(engine, 1, presentation_refresh=True)
    assert second.candidate_set_id != first.candidate_set_id
    learned = [candidate for candidate in second.candidates if candidate.text == "你好"]
    assert learned
    assert learned[0].token_path == (1, 2)
    assert learned[0].completes_input
    assert learned[0].predicted_syllables == 0
    assert learned[0].model_score is not None
    assert learned[0].model_score <= 0.0

    replacement = _page(engine, 2)
    cached = [candidate for candidate in replacement.candidates if candidate.text == "你好"]
    assert cached
    assert cached[0].token_path == (1, 2)
    # C2 may immediately continue preparing later pages in the background, so
    # total continuation-call count is intentionally not stable after page 0.
    assert runtime.full_logits_calls == 1


def test_native_candidate_end_preserves_unconsumed_pinyin() -> None:
    translator = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")

    assert "segment.start, segment.start + consumed" in translator
    candidate_block = translator[translator.index("New<::rime::SimpleCandidate>") :]
    candidate_block = candidate_block[: candidate_block.index("translation->Append")]
    assert "segment.end" not in candidate_block
