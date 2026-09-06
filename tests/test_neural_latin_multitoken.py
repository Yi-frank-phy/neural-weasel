from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidate_pages_scored import _selected_log_probs
from neural_weasel.neural_candidates import NeuralLanguageMode
from neural_weasel.neural_latin import NeuralLatinPrefixConstraint


class FakeTokenizer:
    pieces = {
        0: "<special>",
        10: " asym",
        11: "metry",
        12: " metry",
        13: "x",
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


@dataclass
class ObservedCompletion:
    value: str
    token_path: tuple[int, ...]
    text_reads: int = 0

    @property
    def text(self) -> str:
        self.text_reads += 1
        return self.value


@dataclass
class LatinContinuationRuntime:
    logits: np.ndarray
    continuation_calls: int = 0
    allowed_sets: list[tuple[int, ...]] = field(default_factory=list)

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
        del deadline_ms
        assert root[0] == "root"
        self.continuation_calls += 1
        outputs = []
        for token_path, allowed in zip(token_paths, allowed_token_sets, strict=True):
            allowed = tuple(int(token_id) for token_id in allowed)
            self.allowed_sets.append(allowed)
            values = np.full(len(allowed), -np.inf, dtype=np.float32)
            if tuple(token_path) == (10,):
                values[allowed.index(11)] = 7.0
                values[allowed.index(13)] = 6.0
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine() -> tuple[BilingualImeEngine, LatinContinuationRuntime]:
    logits = np.full(14, -20.0, dtype=np.float32)
    logits[10] = 5.0
    logits[13] = 4.0
    runtime = LatinContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        latin_prefix_constraint=NeuralLatinPrefixConstraint.from_tokenizer(FakeTokenizer()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine: BilingualImeEngine, raw: str, revision: int, **kwargs):
    values = {
        "client_session_id": "latin-session",
        "composition_revision": revision,
        "context_epoch": 0,
        "context_session": None,
        "source_revision": None,
        "language_mode": "latin_first",
        "raw_keys": raw,
        "page_index": 0,
    }
    values.update(kwargs)
    return engine.query_candidate_page(**values)


def _wait_for_page_preparation(engine: BilingualImeEngine, candidate_set_id: str) -> None:
    manager = engine.candidate_pages
    with manager._state_lock:
        event = manager._page_preparation_events.get(candidate_set_id)
    assert event is not None
    assert event.wait(1.0)


def test_multitoken_latin_path_sums_base_log_probs_without_length_normalization() -> None:
    engine, runtime = _engine()

    first = _page(engine, "asymmetry", 1)
    assert len(first.candidates) == 1
    assert first.candidates[0].text == "asymmetry"
    assert first.candidates[0].constraint_kind == "literal"
    assert first.has_more is True
    assert runtime.continuation_calls == 0

    _wait_for_page_preparation(engine, first.candidate_set_id)
    second = _page(
        engine,
        "asymmetry",
        1,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )

    asymmetry = next(candidate for candidate in second.candidates if candidate.text == "asymmetry")
    assert asymmetry.token_path == (10, 11)

    root_log_prob = float(_selected_log_probs(runtime.logits, [10])[0])
    continuation_logits = np.full(runtime.logits.size, -np.inf, dtype=np.float32)
    continuation_logits[11] = 7.0
    continuation_logits[13] = 6.0
    continuation_log_prob = float(_selected_log_probs(continuation_logits, [11])[0])
    expected_path_score = root_log_prob + continuation_log_prob

    assert np.isclose(asymmetry.model_score, expected_path_score, atol=1e-6)
    assert not np.isclose(asymmetry.model_score, expected_path_score / 2.0, atol=1e-6)
    assert asymmetry.script == "latin"
    assert runtime.allowed_sets[0] == tuple(range(runtime.logits.size))
    assert all(13 not in candidate.token_path for candidate in second.candidates)


def test_scored_multitoken_baseline_refreshes_prewarm_and_rebinds_current_raw() -> None:
    engine, runtime = _engine()
    prewarm_key = ("a", NeuralLanguageMode.LATIN_FIRST)
    assert prewarm_key in engine.candidate_pages._baseline_single_letter

    first = _page(engine, "asymmetry", 1)
    _wait_for_page_preparation(engine, first.candidate_set_id)
    _page(
        engine,
        "asymmetry",
        1,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )
    calls_after_search = runtime.continuation_calls

    # Learning a reusable empty-context path refreshes the permanent first-letter
    # prewarm instead of deleting it and forcing a future first-key rebuild.
    assert prewarm_key in engine.candidate_pages._baseline_single_letter
    prewarmed = next(
        candidate
        for candidate in engine.candidate_pages._baseline_single_letter[prewarm_key]
        if candidate.text == "asymmetry"
    )
    assert prewarmed.token_path == (10, 11)
    assert prewarmed.consumed_keys == len("a")
    assert prewarmed.completes_input is False

    cached = _page(engine, "a", 2)
    completion = next(candidate for candidate in cached.candidates if candidate.text == "asymmetry")

    assert completion.token_path == (10, 11)
    assert completion.consumed_keys == len("a")
    assert completion.completes_input is False
    assert runtime.continuation_calls == calls_after_search

    # The same cached Base-model path rebinds to the full raw word when queried
    # as an exact completion in a later revision.
    exact_page = _page(engine, "asymmetry", 3)
    exact = next(candidate for candidate in exact_page.candidates if candidate.text == "asymmetry")
    assert exact.consumed_keys == len("asymmetry")
    assert exact.completes_input is True
    assert runtime.continuation_calls == calls_after_search


def test_page_zero_does_not_rescan_unrelated_latin_initials() -> None:
    matching = ObservedCompletion("native", (10,))
    unrelated = [
        ObservedCompletion(f"apple{suffix}", (token_id,))
        for token_id, suffix in enumerate(range(1_000), start=20)
    ]
    latin = NeuralLatinPrefixConstraint(
        [matching, *unrelated],
        continuation_fragments={},
    )
    logits = np.full(1_020, -20.0, dtype=np.float32)
    logits[10] = 5.0
    runtime = LatinContinuationRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        latin_prefix_constraint=latin,
    )
    engine.initialize_neural_baseline()
    matching.text_reads = 0
    for completion in unrelated:
        completion.text_reads = 0

    page = _page(engine, "na", 1)

    assert any(candidate.text == "native" for candidate in page.candidates)
    assert sum(completion.text_reads for completion in unrelated) == 0
