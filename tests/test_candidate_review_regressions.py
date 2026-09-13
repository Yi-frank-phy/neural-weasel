from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import NeuralLanguageMode
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint, PinyinConstraint


@dataclass
class _BlockingContinuationRuntime:
    logits: np.ndarray
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

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
        return [
            np.full(len(tuple(allowed)), -20.0, dtype=np.float32) for allowed in allowed_token_sets
        ]

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _candidate_engine(make_index) -> tuple[BilingualImeEngine, _BlockingContinuationRuntime]:
    index = make_index(
        [
            (1, "暗", "an", "an", 1, 0),
            (2, "啊", "a", "a", 1, 0),
            (3, "阿", "a", "a", 1, 0),
            (4, "安", "an", "an", 1, 0),
            (5, "爱", "ai", "ai", 1, 0),
            (6, "奥", "ao", "ao", 1, 0),
            (7, "昂", "ang", "ang", 1, 0),
        ]
    )
    logits = np.full(240, 20.0, dtype=np.float32)
    logits[1:8] = np.arange(0, -7, -1)
    # Mirror the production failure: the exact a -> 啊 token scores below
    # enough a -> an/ai/ao shorthand tokens to fall off the visible Han slots
    # unless pinyin structure is a hard tier ahead of model probability.
    logits[2] = -20.0
    runtime = _BlockingContinuationRuntime(logits)
    runtime.release.set()
    completions = tuple(
        LatinCompletion(
            "a" + chr(97 + index // 26) + chr(97 + index % 26),
            (index + 8,),
        )
        for index in range(200)
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(completions),
    )
    return engine, runtime


def _page_zero(engine: BilingualImeEngine, *, client: str, refresh: bool = False):
    return engine.query_candidate_page(
        client_session_id=client,
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode=NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="a",
        page_index=0,
        presentation_refresh=refresh,
    )


def test_single_letter_prewarm_preserves_unwarmed_han_coverage(make_index, monkeypatch) -> None:
    engine, runtime = _candidate_engine(make_index)
    manager = engine.candidate_pages
    manager.install_baseline_scores(runtime.logits, continuation_root=("root", ""))
    monkeypatch.setattr(manager, "_maybe_start_page_preparation", lambda session: None)

    try:
        unwarmed = _page_zero(engine, client="unwarmed")
        unwarmed_han = {
            candidate.text for candidate in unwarmed.candidates if candidate.script == "han"
        }
        assert "啊" in unwarmed_han, "exact a -> 啊 must remain visible despite lower logits"
        assert any(candidate.script == "latin" for candidate in unwarmed.candidates)

        manager.clear_sessions()
        manager.prewarm_single_letter_pages()
        warmed = _page_zero(engine, client="warmed")
        warmed_han = {
            candidate.text for candidate in warmed.candidates if candidate.script == "han"
        }

        assert warmed_han == unwarmed_han
        assert "啊" in warmed_han
    finally:
        runtime.release.set()
        manager.clear_sessions()


def test_lone_c_initial_keeps_rare_high_logit_tokens_off_page_zero(make_index) -> None:
    texts = "出成产长重程车场常种处单次查城从传持创此才参存层吃承材村超称采除仓财茶触朝"
    rows = [
        (token_id, text, "ci", "ci", 1, 0)
        for token_id, text in enumerate(texts, start=1)
    ]
    index = make_index(rows)
    logits = np.full(64, -20.0, dtype=np.float32)
    logits[1] = 0.0
    logits[33:38] = 20.0
    runtime = _BlockingContinuationRuntime(logits)
    runtime.release.set()
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()

    try:
        page = engine.query_candidate_page(
            client_session_id="lone-c",
            composition_revision=1,
            context_epoch=0,
            context_session=None,
            source_revision=None,
            language_mode=NeuralLanguageMode.CHINESE_FIRST,
            raw_keys="c",
            page_index=0,
        )
        han = [candidate for candidate in page.candidates if candidate.script == "han"]
        assert han
        assert han[0].text == "出"
        assert all(candidate.token_id is not None and candidate.token_id <= 32 for candidate in han)
    finally:
        runtime.release.set()
        engine.candidate_pages.clear_sessions()


def test_active_page_preparation_does_not_make_published_page_replaceable(
    make_index, monkeypatch
) -> None:
    engine, runtime = _candidate_engine(make_index)
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    entered = threading.Event()
    release_preparation = threading.Event()
    original_prepare = manager._prepare_page_search

    def gated_prepare(session, cancel):
        entered.set()
        assert release_preparation.wait(3.0)
        return original_prepare(session, cancel)

    monkeypatch.setattr(manager, "_prepare_page_search", gated_prepare)

    try:
        first = _page_zero(engine, client="deferred-preparation")
        _page_zero(engine, client="deferred-preparation", refresh=True)
        assert entered.wait(1.0), "the synthetic page preparation did not start"

        pending_while_preparing = manager.presentation_update_pending(first.candidate_set_id)
        preparation = manager._page_preparation_events[first.candidate_set_id]
        release_preparation.set()
        assert preparation.wait(3.0), "the synthetic page preparation did not finish"

        refreshed = _page_zero(engine, client="deferred-preparation", refresh=True)
        assert "啊" in {candidate.text for candidate in refreshed.candidates}
        assert refreshed.candidates == first.candidates
        assert pending_while_preparing is False
    finally:
        release_preparation.set()
        runtime.release.set()
        manager.clear_sessions()
