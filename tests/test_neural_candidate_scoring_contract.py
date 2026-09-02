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


def _page(engine, revision: int, page_index: int = 0, candidate_set_id: str | None = None):
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
    )


def test_selected_log_probs_normalize_over_full_vocabulary() -> None:
    scores = _selected_log_probs([10.0, 0.0, -2.0], [0, 1, 2])

    assert np.all(scores <= 0.0)
    assert np.isclose(float(np.exp(scores).sum()), 1.0, rtol=1e-5, atol=1e-6)
    assert scores[0] > scores[1] > scores[2]


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

    second = _page(engine, 1, 1, first.candidate_set_id)
    learned = [candidate for candidate in second.candidates if candidate.text == "你好"]
    assert learned
    assert learned[0].token_path == (1, 2)
    assert learned[0].completes_input
    assert learned[0].predicted_syllables == 0
    assert learned[0].model_score is not None
    assert learned[0].model_score <= 0.0

    continuation_calls = runtime.continuation_calls
    replacement = _page(engine, 2)
    cached = [candidate for candidate in replacement.candidates if candidate.text == "你好"]
    assert cached
    assert cached[0].token_path == (1, 2)
    assert runtime.continuation_calls == continuation_calls
    assert runtime.full_logits_calls == 1


def test_native_candidate_end_preserves_unconsumed_pinyin() -> None:
    translator = (ROOT / "native/rime/ai_translator.cc").read_text(encoding="utf-8")

    assert "segment.start, segment.start + consumed" in translator
    candidate_block = translator[translator.index("New<::rime::SimpleCandidate>") :]
    candidate_block = candidate_block[: candidate_block.index("translation->Append")]
    assert "segment.end" not in candidate_block
