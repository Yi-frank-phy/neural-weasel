from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.unified import (
    ContextScriptPolicy,
    LatinCompletion,
    LatinPrefixConstraint,
    PinyinConstraint,
    Script,
    UnifiedConstraintEngine,
    contains_han,
)


@dataclass
class FakeRuntime:
    logits: np.ndarray

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            payload=self.logits,
            before_hash=before,
            after_hash=after,
            latency_ms=0.0,
        )

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


def make_backend(scores: dict[int, float], vocabulary_size: int = 64):
    logits = np.full(vocabulary_size, -20.0, dtype=np.float32)
    for token_id, score in scores.items():
        logits[token_id] = score
    backend = FullLogitsSnapshotBackend(FakeRuntime(logits))
    state = backend.update_context("fixture", "")
    return backend, state


def test_chinese_pinyin_and_latin_share_candidate_pipeline(make_index) -> None:
    """AT-CN-01/02 and AT-UC-01/02."""
    index = make_index(
        [
            (10, "纠缠", "jiuchan", "jiu'chan", 2, 0),
            (11, "就产", "jiuchan", "jiu'chan", 2, 0),
        ]
    )
    backend, state = make_backend({10: 8.0, 11: 2.0, 20: 7.0, 21: 6.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(
            [
                LatinCompletion("Qwen", (20,)),
                LatinCompletion("Qwen3.5", (21,)),
            ]
        ),
    )

    chinese = engine.query("该协议所消耗的", "jiuchan", state=state)
    latin = engine.query("这个模型使用", "qwen", state=state)

    assert chinese[0].text == "纠缠"
    assert any(candidate.text == "Qwen" for candidate in latin)
    assert type(chinese[0]) is type(latin[0])
    for candidate in (*chinese, *latin):
        assert candidate.constraint_kind
        assert candidate.script
        assert candidate.total_score is not None
        assert candidate.context_epoch == state.epoch
        assert isinstance(candidate.token_path, tuple)


def test_exact_short_pinyin_excludes_latin_from_the_chinese_candidate_set(make_index) -> None:
    index = make_index(
        [
            (10, "这", "zhe", "zhe", 1, 0),
            (11, "这是一个", "zheshiyige", "zhe'shi'yi'ge", 4, 0),
        ]
    )
    backend, state = make_backend({10: -100.0, 11: 100.0, 20: 200.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint([LatinCompletion("zher", (20,))]),
    )

    candidates = engine.query("这是中文上下文", "zhe", state=state)

    assert candidates[0].text == "这"
    assert "这是一个" in {candidate.text for candidate in candidates}
    assert "zher" not in {candidate.text for candidate in candidates}


def test_exact_pinyin_excludes_higher_scored_fuzzy_reading(make_index) -> None:
    index = make_index(
        [
            (10, "森津", "senjin", "sen'jin", 2, 0),
            (11, "神经", "shenjing", "shen'jing", 2, 0),
        ]
    )
    backend, state = make_backend({10: -100.0, 11: 100.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
    )

    candidates = engine.query("中文上下文", "senjin", state=state)

    assert candidates[0].text == "森津"
    assert next(candidate for candidate in candidates if candidate.text == "森津").fuzzy_cost == 0
    assert "神经" not in {candidate.text for candidate in candidates}


def test_valid_lowercase_pinyin_remains_hard_even_after_english_context(make_index) -> None:
    index = make_index([(10, "阿西", "asy", "a'sy", 2, 0)])
    backend, state = make_backend({10: 100.0, 20: 8.0, 21: 7.0, 22: 6.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(
            [
                LatinCompletion("asymmetric", (20,)),
                LatinCompletion("asymmetry", (21,)),
                LatinCompletion("asymmetrical", (22,)),
            ]
        ),
    )

    candidates = engine.query(
        "The receiver-centred placement is operationally",
        "asy",
        state=state,
    )

    assert any(contains_han(candidate.text) for candidate in candidates)
    assert "asymmetric" not in {candidate.text for candidate in candidates}
    assert not any(candidate.constraint_kind == "latin_prefix" for candidate in candidates)


def test_raw_literal_does_not_consume_a_first_page_slot_when_pinyin_is_legal(make_index) -> None:
    index = make_index(
        [(token_id, text, "wo", "wo", 1, 0) for token_id, text in enumerate("我窝沃卧握", 10)]
    )
    backend, state = make_backend({token_id: float(token_id) for token_id in range(10, 15)})
    engine = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=PinyinConstraint(index),
    )

    candidates = engine.query("这些话", "wo", state=state, limit=5)

    assert len(candidates) == 5
    assert all(candidate.constraint_kind == "pinyin" for candidate in candidates)


def test_literal_latin_prefix_is_always_commit_able() -> None:
    """AT-EN-03: model silence cannot remove literal English input."""
    backend, state = make_backend({})
    engine = UnifiedConstraintEngine(
        backend=backend,
        latin_prefix_constraint=LatinPrefixConstraint([]),
    )

    candidates = engine.query("The protocol is", "non", state=state)

    literal = next(candidate for candidate in candidates if candidate.constraint_kind == "literal")
    assert literal.text == "non"
    assert literal.consumed_keys == 3
    assert literal.model_score is None


def test_latin_prefix_supports_multitoken_case_hyphen_and_apostrophe() -> None:
    """AT-EN-04/05: bounded paths are not restricted to one lowercase token."""
    backend, state = make_backend({1: 2.0, 2: 4.0, 3: 3.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        latin_prefix_constraint=LatinPrefixConstraint(
            [
                LatinCompletion("receiver-centred", (1, 2)),
                LatinCompletion("Qwen3.5", (2,)),
                LatinCompletion("don't", (3,)),
            ]
        ),
    )

    assert any(
        candidate.text == "receiver-centred" and len(candidate.token_path) == 2
        for candidate in engine.query("The", "receiver-", state=state)
    )
    assert any(
        candidate.text == "Qwen3.5"
        for candidate in engine.query("这个模型使用", "qwen", state=state)
    )
    assert any(candidate.text == "don't" for candidate in engine.query("I", "DON", state=state))


def test_context_policy_is_asymmetric_and_no_context_is_deterministic() -> None:
    """AT-SP-02/03/04."""
    policy = ContextScriptPolicy()

    assert policy.classify("The receiver-centred placement") == "english"
    assert policy.classify("这个模型使用") == "chinese"
    assert policy.classify("") == "ambiguous"
    assert policy.allows("english", Script.LATIN)
    assert policy.allows("english", Script.HAN)
    assert policy.allows("chinese", Script.HAN)
    assert policy.allows("chinese", Script.LATIN)
    assert policy.language_prior("ambiguous", Script.HAN, "abc") > policy.language_prior(
        "ambiguous", Script.LATIN, "abc"
    )
    assert policy.language_prior("chinese", Script.LATIN, "QWEN") == 0.0


def test_cross_script_penalty_is_fixed_configurable_and_not_margin_driven() -> None:
    default_policy = ContextScriptPolicy()
    configured_policy = ContextScriptPolicy(cross_script_penalty=-0.5)

    assert default_policy.language_prior("chinese", Script.LATIN, "qwen") == -0.15
    assert configured_policy.language_prior("chinese", Script.LATIN, "qwen") == -0.5
    assert configured_policy.language_prior("english", Script.HAN, "shen") == -0.5
    assert configured_policy.language_prior("chinese", Script.LATIN, "Qwen3.5") == 0.0


def test_retyping_uses_only_the_current_prefix() -> None:
    """AT-EN-06: Latin constraint has no hidden composition history."""
    backend, state = make_backend({1: 3.0, 2: 2.0})
    engine = UnifiedConstraintEngine(
        backend=backend,
        latin_prefix_constraint=LatinPrefixConstraint(
            [
                LatinCompletion("asymmetric", (1,)),
                LatinCompletion("nonlocal", (2,)),
            ]
        ),
    )

    assert any(
        candidate.text == "asymmetric" for candidate in engine.query("The", "asy", state=state)
    )
    retyped = engine.query("The", "non", state=state)
    assert any(candidate.text == "nonlocal" for candidate in retyped)
    assert not any(candidate.text == "asymmetric" for candidate in retyped)
