from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
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
            if tuple(token_path) == (10,) and 11 in allowed:
                values[allowed.index(11)] = 7.0
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine() -> tuple[BilingualImeEngine, LatinContinuationRuntime]:
    logits = np.full(14, -20.0, dtype=np.float32)
    logits[10] = 5.0
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


def test_multitoken_latin_path_is_scored_from_one_root_without_normalization() -> None:
    engine, runtime = _engine()

    first = _page(engine, "asymmetry", 1)
    assert first.candidates == ()
    assert first.has_more is True
    assert runtime.continuation_calls == 0

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
    assert asymmetry.model_score == 12.0
    assert asymmetry.script == "latin"
    assert 12 not in runtime.allowed_sets[0]


def test_scored_multitoken_baseline_invalidates_prewarms_and_becomes_page0_supplement() -> None:
    engine, runtime = _engine()
    prewarm_key = ("a", NeuralLanguageMode.LATIN_FIRST)
    assert prewarm_key in engine.candidate_pages._baseline_single_letter

    first = _page(engine, "asymmetry", 1)
    _page(
        engine,
        "asymmetry",
        1,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )
    calls_after_search = runtime.continuation_calls
    assert prewarm_key not in engine.candidate_pages._baseline_single_letter

    cached = _page(engine, "a", 2)

    assert any(
        candidate.text == "asymmetry" and candidate.token_path == (10, 11)
        for candidate in cached.candidates
    )
    assert runtime.continuation_calls == calls_after_search
