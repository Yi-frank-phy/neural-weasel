from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
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

    first = _page(engine, raw)
    assert expected_text not in {candidate.text for candidate in first.candidates}
    assert first.has_more is True

    second = _page(
        engine,
        raw,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )

    exact = next(candidate for candidate in second.candidates if candidate.text == expected_text)
    assert exact.token_path == expected_path
    assert exact.completes_input is True
    assert exact.consumed_keys == len(raw)
    assert exact.predicted_syllables == 0
    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[0][1] == tuple(range(runtime.logits.size))
    assert all(7 not in candidate.token_path for candidate in second.candidates)


def test_han_continuation_scores_full_vocab_but_generates_only_legal_edges(
    make_index,
) -> None:
    engine, runtime = _engine(make_index)

    first = _page(engine, "nihaoma")
    second = _page(
        engine,
        "nihaoma",
        page_index=1,
        candidate_set_id=first.candidate_set_id,
        deadline_ms=120.0,
    )

    assert runtime.continuation_calls[0][0] == (1,)
    assert runtime.continuation_calls[1][0] == (1, 2)
    assert all(
        allowed == tuple(range(runtime.logits.size))
        for _, allowed in runtime.continuation_calls[:2]
    )
    assert all(7 not in candidate.token_path for candidate in second.candidates)
    exact = next(candidate for candidate in second.candidates if candidate.text == "你好吗")
    assert exact.token_path == (1, 2, 3)
