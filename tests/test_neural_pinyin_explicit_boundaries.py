from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class BoundaryRuntime:
    logits: np.ndarray
    continuation_calls: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(
        default_factory=list
    )

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
        outputs = []
        for raw_path, raw_allowed in zip(
            token_paths,
            allowed_token_sets,
            strict=True,
        ):
            path = tuple(int(token_id) for token_id in raw_path)
            allowed = tuple(int(token_id) for token_id in raw_allowed)
            self.continuation_calls.append((path, allowed))
            values = np.full(len(allowed), -20.0, dtype=np.float32)
            if path == (2,) and 3 in allowed:
                values[allowed.index(3)] = 20.0
            outputs.append(values)
        return outputs

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def _engine(make_index, *, include_phrase_token: bool):
    rows = [
        (1, "先", "xian", "xian", 1, 0),
        (2, "西", "xi", "xi", 1, 0),
        (3, "安", "an", "an", 1, 0),
    ]
    if include_phrase_token:
        rows.append((4, "西安", "xian", "xi'an", 2, 0))
    index = make_index(rows)
    logits = np.full(8, -20.0, dtype=np.float32)
    logits[1] = 100.0  # Deliberately prefer the illegal cross-boundary token.
    logits[2] = 10.0
    logits[3] = 9.0
    if include_phrase_token:
        logits[4] = 8.0
    runtime = BoundaryRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    return engine, runtime


def _page(engine, *, page_index: int = 0, candidate_set_id: str | None = None):
    return engine.query_candidate_page(
        client_session_id="explicit-boundary-session",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        language_mode="chinese_first",
        raw_keys="xi'an",
        page_index=page_index,
        candidate_set_id=candidate_set_id,
        deadline_ms=120.0 if page_index else None,
    )


def test_explicit_apostrophe_blocks_one_syllable_path_that_crosses_it(make_index) -> None:
    engine, _ = _engine(make_index, include_phrase_token=True)

    page = _page(engine)
    full_cover = [
        candidate
        for candidate in page.candidates
        if candidate.completes_input and candidate.consumed_keys == len("xi'an")
    ]

    assert any(candidate.text == "西安" for candidate in full_cover)
    assert all(candidate.text != "先" for candidate in full_cover)
    phrase = next(candidate for candidate in full_cover if candidate.text == "西安")
    assert phrase.pinyin == "xi'an"
    assert phrase.predicted_syllables == 0


def test_explicit_apostrophe_is_preserved_across_multitoken_exact_search(make_index) -> None:
    engine, runtime = _engine(make_index, include_phrase_token=False)

    first = _page(engine)
    assert "西安" not in {candidate.text for candidate in first.candidates}
    assert first.has_more is True

    second = _page(
        engine,
        page_index=1,
        candidate_set_id=first.candidate_set_id,
    )

    phrase = next(candidate for candidate in second.candidates if candidate.text == "西安")
    assert phrase.token_path == (2, 3)
    assert phrase.pinyin == "xi'an"
    assert phrase.completes_input is True
    assert phrase.consumed_keys == len("xi'an")
    assert phrase.predicted_syllables == 0
    assert runtime.continuation_calls[0][0] == (2,)
    assert all(1 not in candidate.token_path for candidate in second.candidates)
