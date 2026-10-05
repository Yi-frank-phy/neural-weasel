from __future__ import annotations

import gc
import sys
import weakref
from types import SimpleNamespace

import pytest

from neural_weasel.index import IndexedPronunciation
from neural_weasel.neural_candidate_pages_v3 import NeuralCandidatePageManager
from neural_weasel.neural_candidates import _RootHanPlanEntry
from neural_weasel.pinyin_partial import PartialPinyinMatch, PartialPinyinMatcher
from neural_weasel.unified import LatinPrefixConstraint


def root(cls=_RootHanPlanEntry, text="测"):
    return cls(text, "ce", "测", 2, True, 1, 7, 0, 0, 1, (7,))


def match(cls=PartialPinyinMatch, text="测"):
    entry = IndexedPronunciation(7, text, "ce", ("ce",), 1, False)
    return cls(entry, 2)


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython GC optimization")
@pytest.mark.parametrize("kind", ["root", "matched_cache"])
def test_atomic_records_stop_participating_in_cyclic_scans(kind, make_index):
    if kind == "root":
        manager = NeuralCandidatePageManager(
            backend=None,
            pinyin_index=make_index([(7, "测", "ce", 1, 0)]),
            latin_constraint=LatinPrefixConstraint(()),
        )
        value = manager._root_han_plan("ce")[0]
    else:
        matcher = PartialPinyinMatcher(make_index([(7, "测", "ce", 1, 0)]))
        value = matcher.neural_matches("ce")[0]
    gc.collect()
    assert not gc.is_tracked(value), "cached atomic records still enter every full GC scan"
    # Reference counting still keeps the live record and all payload available.
    text = value.text if isinstance(value, _RootHanPlanEntry) else value.entry.text
    assert text == "测"


@pytest.mark.parametrize("factory,base", [(root, _RootHanPlanEntry), (match, PartialPinyinMatch)])
def test_subclass_cycles_remain_collectible(factory, base):
    class MutableSubclass(base):
        pass

    value = factory(MutableSubclass)
    object.__setattr__(value, "backreference", value)
    if isinstance(value, PartialPinyinMatch):
        value._exclude_from_cyclic_scans()
    observed = weakref.ref(value)
    assert gc.is_tracked(value)
    del value
    gc.collect()
    assert observed() is None


@pytest.mark.parametrize("factory", [root, match])
def test_missing_native_api_preserves_normal_gc(factory, monkeypatch):
    monkeypatch.setattr("neural_weasel.immutable_record_gc._untrack", None)
    value = factory()
    if isinstance(value, PartialPinyinMatch):
        value._exclude_from_cyclic_scans()
    assert gc.is_tracked(value)


@pytest.mark.parametrize("factory", [root, match])
def test_scalar_subclasses_with_backreferences_remain_collectible(factory):
    class MutableText(str):
        pass

    text = MutableText("测")
    value = factory(text=text)
    text.backreference = value
    if isinstance(value, PartialPinyinMatch):
        value._exclude_from_cyclic_scans()
    observed = weakref.ref(text)
    assert gc.is_tracked(value)
    del text, value
    gc.collect()
    assert observed() is None


def test_unvalidated_oversized_matches_keep_root_records_tracked(make_index, monkeypatch):
    monkeypatch.setattr("neural_weasel.pinyin_partial._MAX_NEURAL_MATCH_CACHE_RECORDS", 2)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index(
            [(7, "测", "ce", 1, 0), (8, "策", "ce", 1, 0), (9, "册", "ce", 1, 0)]
        ),
        latin_constraint=LatinPrefixConstraint(()),
    )
    plan = manager._root_han_plan("ce")
    assert len(plan) == 3
    assert all(gc.is_tracked(record) for record in plan)


def test_instrumented_root_constructor_cannot_bypass_payload_validation(make_index, monkeypatch):
    from neural_weasel import neural_candidate_pages_v3 as v3

    class MutableText(str):
        pass

    original = v3._RootHanPlanEntry

    def instrumented_constructor(**kwargs):
        record = original(**kwargs)
        text = MutableText(record.text)
        object.__setattr__(record, "text", text)
        text.backreference = record
        return record

    monkeypatch.setattr(v3, "_RootHanPlanEntry", instrumented_constructor)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index([(7, "测", "ce", 1, 0)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    value = manager._root_han_plan("ce")[0]
    observed = weakref.ref(value.text)
    assert gc.is_tracked(value)
    del value, manager
    gc.collect()
    assert observed() is None


def test_non_native_computed_rank_keeps_root_cycle_collectible(make_index, monkeypatch):
    from neural_weasel import neural_candidate_pages_v3 as v3

    class MutableRank(int):
        pass

    class Marker:
        pass

    rank = MutableRank(0)
    marker = Marker()
    observed = weakref.ref(marker)
    rank.marker = marker
    ranks = {7: rank}
    monkeypatch.setattr(v3, "_single_initial_static_ranks", lambda *args: ranks)
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index([(7, "测", "ce", 1, 0)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    value = manager._root_han_plan("ce")[0]
    rank.backreference = value
    assert gc.is_tracked(value)
    ranks.clear()
    del value, manager, rank, marker
    gc.collect()
    assert observed() is None


def test_cached_index_proof_does_not_skip_mutable_match_fields(make_index):
    from neural_weasel.pinyin_partial import _exclude_cached_match

    class MutableCount(int):
        pass

    class Marker:
        pass

    matcher = PartialPinyinMatcher(make_index([(7, "测", "ce", 1, 0)]))
    count = MutableCount(0)
    marker = Marker()
    count.marker = marker
    value = PartialPinyinMatch(matcher.entries[0], 2, shorthand=count)
    count.backreference = value
    observed = weakref.ref(marker)
    _exclude_cached_match(value, matcher._validated_atomic_entry_ids)
    assert gc.is_tracked(value)
    del count, value, matcher, marker
    gc.collect()
    assert observed() is None


def test_cyclic_index_payload_never_receives_reusable_proof():
    class MutableText(str):
        pass

    text = MutableText("测")
    entry = IndexedPronunciation(7, text, "ce", ("ce",), 1, False)
    text.backreference = entry
    observed = weakref.ref(text)
    index = SimpleNamespace(root=SimpleNamespace(children={}, terminals=[entry]), syllables=("ce",))
    matcher = PartialPinyinMatcher(index)
    assert not matcher._validated_atomic_entry_ids
    value = matcher.neural_matches("ce")[0]
    assert gc.is_tracked(value)
    del text, entry, value, index, matcher
    gc.collect()
    assert observed() is None


def test_index_proof_is_local_and_does_not_retain_matcher(make_index):
    matcher = PartialPinyinMatcher(make_index([(7, "测", "ce", 1, 0)]))
    matcher.neural_matches("c")
    matcher.neural_matches("ce")
    observed = weakref.ref(matcher)
    del matcher
    gc.collect()
    assert observed() is None
