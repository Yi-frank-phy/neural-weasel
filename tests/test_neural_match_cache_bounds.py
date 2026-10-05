from __future__ import annotations

from collections import OrderedDict

import pytest

from neural_weasel import pinyin_partial


def test_concurrent_eviction_keeps_obtained_immutable_result_valid(make_index):
    matcher = pinyin_partial.PartialPinyinMatcher(make_index([(1, "好", "hao", 1, 0)]))
    result = matcher.neural_matches("hao")
    key = ("hao", 0, ())

    class EvictDuringPromotion(OrderedDict):
        def move_to_end(self, key, last=True):
            self.pop(key)
            raise KeyError(key)

    cache = EvictDuringPromotion({key: result})
    assert matcher._cached_neural_matches(cache, key) is result
    matcher._remember_neural_matches(cache, key, result)
    assert not cache


@pytest.mark.parametrize(
    "options,cache_name",
    [
        ({}, "_neural_cache"),
        ({"exact_only": True}, "_neural_exact_cache"),
        ({"include_descendants": False}, "_neural_unextended_cache"),
    ],
)
def test_neural_cache_churn_is_bounded_without_changing_matches(
    make_index, monkeypatch, options, cache_name
):
    monkeypatch.setattr(pinyin_partial, "_MAX_NEURAL_MATCH_CACHE_KEYS", 4, raising=False)
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index([(1, "好", "hao", 1, 0), (2, "好吗", "haoma", "hao'ma", 2, 0)])
    )

    def matches(raw):
        return tuple(m for m in matcher.iter_neural_matches(raw, **options) if m is not None)

    expected = matches("h")
    for raw in ("hao", "ha", "haoma", "hm", "hmm", "haom"):
        matches(raw)
    cache = getattr(matcher, cache_name)
    assert len(cache) <= 4, "neural match caches must not grow with the entire typing history"
    assert "h" not in {key[0] for key in cache}
    assert matches("h") == expected


@pytest.mark.parametrize(
    "options,cache_name",
    [
        ({}, "_neural_cache"),
        ({"exact_only": True}, "_neural_exact_cache"),
        ({"include_descendants": False}, "_neural_unextended_cache"),
    ],
)
def test_neural_cache_record_budget_does_not_truncate_large_results(
    make_index, monkeypatch, options, cache_name
):
    monkeypatch.setattr(pinyin_partial, "_MAX_NEURAL_MATCH_CACHE_RECORDS", 2, raising=False)
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "浩", "hao", 1, 0),
            ]
        )
    )
    values = tuple(m for m in matcher.iter_neural_matches("hao", **options) if m is not None)
    assert len(values) == 3
    cache = getattr(matcher, cache_name)
    assert sum(map(len, cache.values())) <= 2, "oversized matches must bypass the cache"
    assert (
        tuple(m for m in matcher.iter_neural_matches("hao", **options) if m is not None) == values
    )


def test_neural_cache_evicts_by_total_records_and_preserves_recent_hits(make_index, monkeypatch):
    monkeypatch.setattr(pinyin_partial, "_MAX_NEURAL_MATCH_CACHE_RECORDS", 3, raising=False)
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index(
            [
                (1, "好", "hao", 1, 0),
                (2, "号", "hao", 1, 0),
                (3, "吗", "ma", 1, 0),
            ]
        )
    )
    matcher.neural_matches("h")
    expected = matcher.neural_matches("h")
    matcher.neural_matches("m")
    assert matcher.neural_matches("h") is expected
    matcher.neural_matches("hao")
    assert sum(map(len, matcher._neural_cache.values())) <= 3
    assert ("m", 0, ()) not in matcher._neural_cache
    assert matcher.neural_matches("h") == expected
