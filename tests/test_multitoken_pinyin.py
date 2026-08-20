from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.backends import RuntimeSnapshot
from neural_weasel.conditional_backend import ConditionalFullLogitsBackend
from neural_weasel.mixed_pinyin import MixedPinyinConstraint
from neural_weasel.pinyin_partial import PartialPinyinMatcher
from neural_weasel.qwen_continuation import QwenContinuationSession
from neural_weasel.unified import UnifiedConstraintEngine


class FakeConditionalSession:
    def __init__(self, advance_latency_ms: float = 0.0) -> None:
        self.paths: list[tuple[int, ...]] = [()]
        self.advances: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        self.advance_latency_ms = advance_latency_ms

    def advance(self, parent_indices, token_ids) -> float:
        parents = tuple(int(value) for value in parent_indices)
        tokens = tuple(int(value) for value in token_ids)
        self.advances.append((parents, tokens))
        self.paths = [
            self.paths[parent] + (token,) for parent, token in zip(parents, tokens, strict=True)
        ]
        return self.advance_latency_ms

    def score_allowed(self, allowed_token_ids_by_beam):
        results = []
        for path, allowed in zip(self.paths, allowed_token_ids_by_beam, strict=True):
            table = {
                (10,): {20: 9.0, 21: 1.0, 60: 9.0},
                (10, 20): {30: 8.0},
                (10, 21): {30: 0.5},
                (10, 60): {70: 8.0},
            }.get(path, {})
            results.append(
                np.asarray(
                    [table.get(int(token_id), -20.0) for token_id in allowed],
                    dtype=np.float32,
                )
            )
        return tuple(results)


class FakeConditionalBackend:
    def __init__(
        self,
        root_scores: dict[int, float],
        *,
        within_budget: bool = True,
        advance_latency_ms: float = 0.0,
    ) -> None:
        self.root_scores = root_scores
        self.session = FakeConditionalSession(advance_latency_ms)
        self.started = 0
        self.within_budget = within_budget
        self.recorded_latencies: list[float] = []

    def score_allowed_tokens(self, state, allowed_token_ids):
        del state
        return np.asarray(
            [self.root_scores.get(int(token_id), -20.0) for token_id in allowed_token_ids],
            dtype=np.float32,
        )

    def start_conditional_continuation(self, state):
        del state
        self.started += 1
        return self.session

    def conditional_continuation_within_budget(self, state, budget_ms):
        del state, budget_ms
        return self.within_budget

    def record_conditional_continuation_latency(self, state, latency_ms):
        del state
        self.recorded_latencies.append(float(latency_ms))


class StartupLatencyRuntime:
    def __init__(self, latency_ms: float) -> None:
        self.latency_ms = latency_ms

    def full_logits(self, before, after):
        return RuntimeSnapshot(
            payload=np.zeros(64, dtype=np.float32),
            before_hash=before,
            after_hash=after,
            latency_ms=self.latency_ms,
        )

    def diagnostics(self):
        return {}

    def invalidate_private_state(self):
        return None


def test_startup_forward_latency_gates_conditional_key_path() -> None:
    slow = ConditionalFullLogitsBackend(StartupLatencyRuntime(500.0))
    slow_state = slow.update_context("", "")
    fast = ConditionalFullLogitsBackend(StartupLatencyRuntime(20.0))
    fast_state = fast.update_context("", "")

    assert not slow.conditional_continuation_within_budget(slow_state, 80.0)
    assert slow.diagnostics()["conditional_continuation_enabled"] is False
    assert fast.conditional_continuation_within_budget(fast_state, 80.0)
    assert fast.diagnostics()["conditional_continuation_enabled"] is True

    fast.record_conditional_continuation_latency(fast_state, 500.0)
    assert not fast.conditional_continuation_within_budget(fast_state, 80.0)
    assert fast.diagnostics()["conditional_observed_forward_latency_ms"] == 500.0
    assert fast.diagnostics()["conditional_continuation_enabled"] is False


def test_fast_incremental_context_reenables_continuation_after_cold_start() -> None:
    runtime = StartupLatencyRuntime(500.0)
    backend = ConditionalFullLogitsBackend(runtime)
    backend.update_context("", "")
    assert not backend.conditional_continuation_within_budget(backend.latest_state(), 80.0)

    runtime.latency_ms = 20.0
    state = backend.update_context("这些话我打出来", "")

    assert backend.conditional_continuation_within_budget(state, 80.0)
    diagnostics = backend.diagnostics()
    assert diagnostics["conditional_startup_forward_latency_ms"] == pytest.approx(500.0, abs=1.0)
    assert diagnostics["conditional_latest_context_latency_ms"] == pytest.approx(20.0, abs=1.0)


def test_continuation_session_never_waits_for_an_in_progress_context_forward() -> None:
    model_lock = threading.Lock()
    model_lock.acquire()
    runtime = SimpleNamespace(
        _lock=model_lock,
        _cache_state_lock=threading.Lock(),
        _context_cache=None,
        torch=object(),
        model=object(),
    )

    started = time.perf_counter()
    try:
        session = QwenContinuationSession.from_runtime(runtime)
    finally:
        model_lock.release()

    assert session is None
    assert time.perf_counter() - started < 0.05


def test_partial_matches_accept_full_initial_and_incomplete_final(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
            (40, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
            (50, "神经", "shenjing", "shen'jing", 2, 0),
        ]
    )
    matcher = PartialPinyinMatcher(index)

    first = matcher.partial_matches("zhendezmh", 0)
    assert any(match.entry.text == "真的" and match.next_position == 6 for match in first)

    second = matcher.partial_matches("zhendezmh", 6)
    assert any(match.entry.text == "这么" and match.next_position == 8 for match in second)

    third = matcher.partial_matches("zhendezmh", 8)
    assert any(match.entry.text == "好" and match.next_position == 9 for match in third)

    single_token = matcher.partial_matches("zhemhao", 0)
    assert any(match.entry.text == "这么好" and match.next_position == 7 for match in single_token)

    incomplete_final = matcher.partial_matches("shenji", 0)
    assert any(
        match.entry.text == "神经" and match.next_position == 6 for match in incomplete_final
    )


def test_multitoken_fallback_uses_conditional_scores_for_zhendezmh(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (21, "怎么", "zenme", "zen'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
        ]
    )
    backend = FakeConditionalBackend({10: 10.0})
    state = SimpleNamespace(epoch=7)
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendezmh",
        backend=backend,
        state=state,
        after_text="",
    )

    assert backend.started == 1
    assert candidates[0].text == "真的这么好"
    assert candidates[0].token_path == (10, 20, 30)
    assert len(backend.session.advances) == 2


def test_exact_full_pinyin_path_excludes_higher_scored_shorthand_detour(make_index) -> None:
    index = make_index(
        [
            (10, "老", "lao", "lao", 1, 0),
            (20, "费劲", "feijin", "fei'jin", 2, 0),
            (21, "费", "fei", "fei", 1, 0),
            (22, "劲", "jing", "jing", 1, 0),
            (23, "了", "le", "le", 1, 0),
            (30, "高了", "gaole", "gao'le", 2, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {10: 10.0, 20: 100.0, 21: -10.0, 22: -10.0, 23: -10.0, 30: 100.0},
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "laofeijingle",
        backend=backend,
        state=SimpleNamespace(epoch=12),
        after_text="",
    )

    assert candidates[0].text == "老费劲了"
    assert candidates[0].pinyin == "lao'fei'jing'le"
    assert candidates[0].fuzzy_cost == 0
    assert not any(candidate.text == "老费劲高了" for candidate in candidates)


def test_continuous_full_pinyin_does_not_split_an_inline_ng_interjection(make_index) -> None:
    index = make_index(
        [
            (10, "老", "lao", "lao", 1, 0),
            (11, "飞机", "feiji", "fei'ji", 2, 0),
            (12, "嗯", "ng", "ng", 1, 0),
            (13, "了", "le", "le", 1, 0),
            (20, "费", "fei", "fei", 1, 0),
            (21, "劲", "jing", "jing", 1, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {10: 10.0, 11: 100.0, 12: 100.0, 13: 100.0, 20: -10.0, 21: -10.0},
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "laofeijingle",
        backend=backend,
        state=SimpleNamespace(epoch=13),
        after_text="",
    )

    assert candidates[0].text == "老费劲了"
    assert not any("飞机嗯" in candidate.text for candidate in candidates)


def test_exact_multitoken_results_keep_priority_but_reserve_a_low_cost_fuzzy_slot(
    make_index,
) -> None:
    index = make_index(
        [
            (10, "老", "lao", "lao", 1, 0),
            (11, "费", "fei", "fei", 1, 0),
            (12, "醒", "jing", "jing", 1, 0),
            (13, "劲", "jin", "jin", 1, 0),
            (14, "了", "le", "le", 1, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {10: 10.0, 11: 10.0, 12: 100.0, 13: -10.0, 14: 10.0},
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "laofeijingle",
        backend=backend,
        state=SimpleNamespace(epoch=14),
        after_text="",
    )

    assert candidates[0].text == "老费醒了"
    desired = next(candidate for candidate in candidates[:5] if candidate.text == "老费劲了")
    assert desired.fuzzy_cost == 1


def test_slow_conditional_backend_uses_snapshot_scoring_without_opening_session(
    make_index,
) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {10: 10.0, 20: 9.0, 30: 8.0},
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendezmh",
        backend=backend,
        state=SimpleNamespace(epoch=8),
        after_text="",
    )

    assert candidates[0].text == "真的这么好"
    assert candidates[0].token_path == (10, 20, 30)
    assert backend.started == 0


def test_snapshot_fallback_uses_character_rank_for_ambiguous_shorthand(make_index) -> None:
    index = make_index(
        [
            (97896, "真的", "zhende", "zhen'de", 2, 0),
            (125330, "镇的", "zhende", "zhen'de", 2, 0),
            (113361, "怎么会", "zenmehui", "zen'me'hui", 3, 0),
            (137378, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
            (96196, "真", "zhen", "zhen", 1, 0),
            (95726, "的", "de", "de", 1, 0),
            (96769, "镇", "zhen", "zhen", 1, 0),
            (96673, "怎", "zen", "zen", 1, 0),
            (96080, "么", "me", "me", 1, 0),
            (95825, "会", "hui", "hui", 1, 0),
            (95854, "这", "zhe", "zhe", 1, 0),
            (95887, "好", "hao", "hao", 1, 0),
        ]
        + [
            (200000 + offset, "在无" + chr(0x4E00 + offset), "zaiwu", "zai'wu", 2, 0)
            for offset in range(40)
        ]
    )
    backend = FakeConditionalBackend(
        {125330: 100.0, 113361: 100.0, 97896: 1.0, 137378: 1.0},
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendezmh",
        backend=backend,
        state=SimpleNamespace(epoch=10),
        after_text="",
    )

    assert candidates[0].text == "真的这么好"
    assert candidates[0].token_path == (97896, 137378)

    ranked = UnifiedConstraintEngine(
        backend=backend,
        pinyin_constraint=constraint,
    ).query(
        "",
        "zhendezmh",
        state=SimpleNamespace(epoch=10),
    )
    assert ranked[0].text == "真的这么好"


def test_snapshot_fallback_uses_token_rank_for_long_full_pinyin(make_index) -> None:
    index = make_index(
        [
            (97896, "真的", "zhende", "zhen'de", 2, 0),
            (109069, "真的是", "zhendeshi", "zhen'de'shi", 3, 0),
            (96748, "世界", "shijie", "shi'jie", 2, 0),
            (96418, "界", "jie", "jie", 1, 0),
            (103876, "接着", "jiezhe", "jie'zhe", 2, 0),
            (103877, "皆这", "jiezhe", "jie'zhe", 2, 0),
            (103878, "街浙", "jiezhe", "jie'zhe", 2, 0),
            (103879, "阶者", "jiezhe", "jie'zhe", 2, 0),
            (96080, "么", "me", "me", 1, 0),
            (95887, "好", "hao", "hao", 1, 0),
            (137378, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {
            109069: 100.0,
            96418: 100.0,
            103876: 100.0,
            103877: 100.0,
            103878: 100.0,
            103879: 100.0,
            97896: 1.0,
            96748: 1.0,
            137378: 1.0,
        },
        within_budget=False,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendeshijiezhemehao",
        backend=backend,
        state=SimpleNamespace(epoch=11),
        after_text="",
    )

    assert candidates[0].text == "真的世界这么好"
    assert candidates[0].token_path == (97896, 96748, 137378)


def test_unexpected_conditional_overrun_returns_snapshot_candidate(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
        ]
    )
    backend = FakeConditionalBackend(
        {10: 10.0, 20: 9.0, 30: 8.0},
        advance_latency_ms=500.0,
    )
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendezmh",
        backend=backend,
        state=SimpleNamespace(epoch=9),
        after_text="",
    )

    assert candidates[0].text == "真的这么好"
    assert candidates[0].token_path == (10, 20, 30)
    assert backend.started == 1
    assert len(backend.session.advances) == 1
    assert backend.recorded_latencies == [500.0]


def test_single_token_partial_fallback_precedes_multitoken_search(make_index) -> None:
    index = make_index([(40, "这么好", "zhemehao", "zhe'me'hao", 3, 0)])
    backend = FakeConditionalBackend({40: 10.0})
    state = SimpleNamespace(epoch=3)
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhemhao",
        backend=backend,
        state=state,
        after_text="",
    )

    assert candidates[0].text == "这么好"
    assert candidates[0].token_path == (40,)
    assert backend.started == 0


def test_single_token_partial_can_complete_one_untyped_suffix(make_index) -> None:
    index = make_index(
        [
            (40, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
            (41, "这么好的", "zhemehaode", "zhe'me'hao'de", 4, 0),
        ]
    )
    backend = FakeConditionalBackend({40: 10.0, 41: 9.8})
    state = SimpleNamespace(epoch=4)
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhemhao",
        backend=backend,
        state=state,
        after_text="",
    )

    assert [candidate.text for candidate in candidates] == ["这么好", "这么好的"]
    assert backend.started == 0


def test_normal_full_pinyin_never_starts_multitoken_fallback(make_index) -> None:
    index = make_index([(5, "神经", "shenjing", "shen'jing", 2, 0)])
    backend = FakeConditionalBackend({5: 10.0})
    state = SimpleNamespace(epoch=2)
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "shenjing",
        backend=backend,
        state=state,
        after_text="",
    )

    assert candidates[0].text == "神经"
    assert backend.started == 0


def test_long_full_pinyin_can_use_bounded_multitoken_fallback(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (60, "世界", "shijie", "shi'jie", 2, 0),
            (70, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
        ]
    )
    backend = FakeConditionalBackend({10: 10.0})
    state = SimpleNamespace(epoch=9)
    constraint = MixedPinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendeshijiezhemehao",
        backend=backend,
        state=state,
        after_text="",
    )

    assert backend.started == 1
    assert candidates[0].text == "真的世界这么好"
    assert candidates[0].token_path == (10, 60, 70)
    assert len(backend.session.advances) == 2
