from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pytest

from neural_weasel import neural_candidate_pages_v3 as v3
from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.neural_candidates import NeuralLanguageMode, _SearchIdentity
from neural_weasel.unified import LatinPrefixConstraint


def test_existing_startup_letter_plans_survive_dynamic_cache_eviction(make_index, monkeypatch):
    monkeypatch.setattr(v3, "_MAX_ROOT_HAN_PLAN_RECORDS", 3)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "吗", "ma", 1, 0),
                (4, "马", "ma", 1, 0),
            ]
        ),
        latin_constraint=LatinPrefixConstraint(()),
    )
    manager.install_baseline_scores(np.zeros(16, dtype=np.float32))
    original = manager._iter_root_han_plan
    scans = []

    def counted(raw, **kwargs):
        scans.append(raw)
        return original(raw, **kwargs)

    monkeypatch.setattr(manager, "_iter_root_han_plan", counted)
    manager.prewarm_single_letter_pages()
    assert scans.count("h") == 1, "reuse the existing startup scan without adding another"
    assert "h" not in manager._root_han_plans
    scans.clear()
    identity = _SearchIdentity(
        "public-startup-reuse", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "h"
    )
    manager._prepare_cold_root_plan(identity, manager.clock() + 0.035)
    manager._root_han_plan("h")
    assert not scans, "the first letter must not rebuild its discarded startup plan"
    assert not manager._root_plan_preparations
    assert set(manager._startup_letter_root_plans) == set("abcdefghijklmnopqrstuvwxyz")
    manager._root_han_plan("hao")
    assert "hao" not in manager._startup_letter_root_plans


def test_failed_startup_pass_does_not_enable_later_plan_retention(make_index, monkeypatch):
    manager = NeuralCandidatePageManager(
        backend=None, pinyin_index=make_index([]), latin_constraint=LatinPrefixConstraint(())
    )
    manager.install_baseline_scores(np.zeros(16, dtype=np.float32))

    def failed(**kwargs):
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(manager, "_root_candidates", failed)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        manager.prewarm_single_letter_pages()
    assert not manager._retaining_startup_letter_roots
    manager._root_han_plan("h")
    assert not manager._startup_letter_root_plans


def test_concurrent_eviction_during_root_promotion_keeps_completed_result_valid():
    class EvictDuringPromotion(OrderedDict):
        def move_to_end(self, key, last=True):
            self.pop(key)
            raise KeyError(key)

    cache = EvictDuringPromotion()
    v3.NeuralCandidatePageManager._remember_root_han_plan(cache, "hao", ())
    assert not cache


def test_baseline_page_cache_does_not_retain_evicted_root_plans(make_index, monkeypatch):
    monkeypatch.setattr(v3, "_MAX_ROOT_HAN_PLAN_RECORDS", 3)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "吗", "ma", 1, 0),
                (4, "马", "ma", 1, 0),
            ]
        ),
        latin_constraint=LatinPrefixConstraint(()),
    )
    manager.install_baseline_scores(np.zeros(16, dtype=np.float32))
    mode = NeuralLanguageMode.CHINESE_FIRST
    expected = manager._root_candidates(
        raw_keys="hao", state=None, response_epoch=0, mode=mode, allow_prewarm_cache=True
    )
    manager._root_candidates(
        raw_keys="ma", state=None, response_epoch=0, mode=mode, allow_prewarm_cache=True
    )
    records = sum(
        len(item.plan)
        for cached in manager._baseline_root_pages.values()
        for item in cached[1]
        if isinstance(item, v3._DeferredHanFrontier)
    )
    assert records <= 3, "page cache must not bypass the root-plan record budget"
    assert ("hao", mode) not in manager._baseline_root_pages
    rebuilt = manager._root_candidates(
        raw_keys="hao", state=None, response_epoch=0, mode=mode, allow_prewarm_cache=True
    )
    assert rebuilt[0] == expected[0]
    assert [seed.plan for seed in rebuilt[1]] == [seed.plan for seed in expected[1]]


@pytest.mark.parametrize("path", ["normal", "exact", "background"])
def test_root_plan_cache_evicts_records_without_truncating_results(make_index, monkeypatch, path):
    monkeypatch.setattr(v3, "_MAX_ROOT_HAN_PLAN_RECORDS", 3, raising=False)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "吗", "ma", 1, 0),
                (4, "马", "ma", 1, 0),
            ]
        ),
        latin_constraint=LatinPrefixConstraint(()),
    )

    def build(raw):
        if path != "background":
            return manager._root_han_plan(raw, exact_only=path == "exact")
        cursor = manager._iter_root_han_plan(raw)
        while True:
            try:
                next(cursor)
            except StopIteration as completed:
                manager._store_root_plan_locked(raw, completed.value)
                return completed.value

    expected = build("hao")
    assert len(expected) == 2
    assert len(build("ma")) == 2
    cache = manager._exact_root_han_plans if path == "exact" else manager._root_han_plans
    assert sum(map(len, cache.values())) <= 3, (
        "root plans must be bounded by records, not just keys"
    )
    assert "hao" not in cache
    assert build("hao") == expected


@pytest.mark.parametrize("exact", [False, True])
def test_single_oversized_root_plan_remains_available_for_cold_recovery(
    make_index, monkeypatch, exact
):
    monkeypatch.setattr(v3, "_MAX_ROOT_HAN_PLAN_RECORDS", 2, raising=False)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "浩", "hao", 1, 0),
                (4, "吗", "ma", 1, 0),
            ]
        ),
        latin_constraint=LatinPrefixConstraint(()),
    )
    plan = manager._root_han_plan("hao", exact_only=exact)
    assert len(plan) == 3
    assert manager._root_han_plan("hao", exact_only=exact) is plan
    manager._root_han_plan("ma", exact_only=exact)
    cache = manager._exact_root_han_plans if exact else manager._root_han_plans
    assert "hao" not in cache, "an oversized previous plan must not retain the whole typing history"
    assert sum(map(len, cache.values())) <= 2
    assert manager._root_han_plan("hao", exact_only=exact) == plan
    assert list(cache) == ["hao"]
