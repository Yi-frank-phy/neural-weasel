from __future__ import annotations

from neural_weasel import pinyin_partial
from neural_weasel.search_batches import cooperative_sorted
from neural_weasel.simplified_chinese import is_simplified_han


def test_unextended_match_cache_does_not_replace_full_matches(make_index):
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index(
            [
                (1, "好", "hao", "hao", 1, 0),
                (2, "好吗", "haoma", "hao'ma", 2, 0),
            ]
        )
    )
    short = tuple(
        match
        for match in matcher.iter_neural_matches("h", include_descendants=False)
        if match is not None
    )
    assert [match.entry.text for match in short] == ["好"]
    full = matcher.neural_matches("h")
    assert [match.entry.text for match in full] == ["好", "好吗"]
    assert [match.completion_syllables for match in full] == [0, 1]
    assert (
        tuple(
            match
            for match in matcher.iter_neural_matches("h", include_descendants=False)
            if match is not None
        )
        == short
    )


def test_neural_match_scan_yields_before_materializing_a_large_terminal(make_index, monkeypatch):
    characters = [chr(code) for code in range(0x4E00, 0x6000) if is_simplified_han(chr(code))][
        :1024
    ]
    rows = [
        (i, f"词{character}", "nihao", "ni'hao", 2, 0) for i, character in enumerate(characters, 1)
    ]
    matcher = pinyin_partial.PartialPinyinMatcher(make_index(rows))
    original = pinyin_partial.PartialPinyinMatch
    constructed = 0

    def counted_match(*args):
        nonlocal constructed
        constructed += 1
        return original(*args)

    monkeypatch.setattr(pinyin_partial, "PartialPinyinMatch", counted_match)
    cursor = matcher.iter_neural_matches("nihao")
    assert next(cursor) is None
    assert constructed <= 64
    assert not matcher._neural_cache, "interrupted scans must not cache incomplete results"
    matches = [match for match in cursor if match is not None]
    assert [match.entry.token_id for match in matches] == list(range(1, 1025))
    assert all(match.next_position == 5 and match.completion_syllables == 0 for match in matches)
    assert matcher.neural_matches("nihao") == tuple(matches)


def test_neural_match_scan_retains_explicit_boundaries_and_best_route(make_index):
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index([(1, "西安", "xian", "xi'an", 2, 0), (2, "先", "xian", "xian", 1, 0)])
    )
    matches = [
        match
        for match in matcher.iter_neural_matches("xian", boundaries=frozenset({2}))
        if match is not None
    ]
    assert [
        (match.entry.text, match.next_position) for match in matches if match.next_position == 4
    ] == [("西安", 4)]
    assert all(match.next_position <= 2 for match in matches if match.entry.text == "先")
    unseparated = matcher.neural_matches("xian")
    assert {match.entry.text for match in unseparated} == {"西安", "先"}


def test_cooperative_sort_preserves_global_order_and_stable_ties():
    values = [(index % 9, object()) for index in range(129)]
    cursor = cooperative_sorted(values, key=lambda item: item[0])
    checkpoints = 0
    while True:
        try:
            assert next(cursor) is None
            checkpoints += 1
        except StopIteration as finished:
            assert finished.value == sorted(values, key=lambda item: item[0])
            break
    assert checkpoints >= 4


def test_frontier_identity_reuses_immutable_pending_edges():
    from dataclasses import replace

    from neural_weasel.neural_candidate_pages_v3 import (
        NeuralCandidatePageManager,
        _HanEdge,
        _HanSearchPath,
    )

    reads = 0

    class Entry:
        @property
        def token_path(self):
            nonlocal reads
            reads += 1
            return (42, 43)

    edges = tuple(_HanEdge(Entry(), 4, 0) for _ in range(1024))
    path = _HanSearchPath("你好", ("ni", "hao"), (1,), 0.0, 0, 2, pending_options=edges)
    expected = ("han", (1,), 2, ("ni", "hao"), (((42, 43), 4, 0),) * 1024)
    for _ in range(20):
        assert NeuralCandidatePageManager._path_key(path) == expected
    assert reads == len(edges), "frontier deduplication must not rescan immutable edge lists"
    changed = replace(path, pending_options=(_HanEdge(Entry(), 5, 1),))
    assert NeuralCandidatePageManager._path_key(changed) != expected
    assert NeuralCandidatePageManager._path_key(changed)[-1] == (((42, 43), 5, 1),)


def test_exact_lexical_candidate_precedes_extended_reading_materialization(make_index, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from neural_weasel.neural_candidate_pages_v3 import NeuralCandidatePageManager

    characters = [chr(code) for code in range(0x4E00, 0x6000) if is_simplified_han(chr(code))][
        :1024
    ]
    rows = [(1, "你", "ni", "ni", 1, 0), (2, "好", "hao", "hao", 1, 0)]
    rows.extend((i, f"好{char}", "haoma", "hao'ma", 2, 0) for i, char in enumerate(characters, 3))
    manager = NeuralCandidatePageManager(
        backend=object(),
        pinyin_index=make_index(rows),
        latin_constraint=SimpleNamespace(completions=()),
    )
    manager._baseline_scores = np.zeros(2048, dtype=np.float32)
    snapshot = SimpleNamespace(
        identity=SimpleNamespace(raw_keys="nihao", context_epoch=0),
        frontier=[],
        pending=[],
        seen_candidates=set(),
    )
    constructed = 0
    original = pinyin_partial.PartialPinyinMatch

    def counted_match(*args):
        nonlocal constructed
        constructed += 1
        return original(*args)

    monkeypatch.setattr(pinyin_partial, "PartialPinyinMatch", counted_match)
    cursor = manager._iter_lexical_completion_candidates(snapshot)
    first = next(candidate for candidate in cursor if candidate is not None)
    assert first.text == "你好"
    assert constructed <= 64, "exact candidates must precede the thousand-entry extended tail"
    tail = [candidate for candidate in cursor if candidate is not None]
    assert len(tail) == 1024
    assert all(
        candidate.text.startswith("你好") and candidate.completes_input for candidate in tail
    )


def test_interrupted_match_scan_releases_recursive_work_without_cyclic_gc(make_index):
    import gc
    import weakref

    characters = [chr(code) for code in range(0x4E00, 0x6000) if is_simplified_han(chr(code))][
        :1024
    ]
    matcher = pinyin_partial.PartialPinyinMatcher(
        make_index(
            [(i, f"词{char}", "nihao", "ni'hao", 2, 0) for i, char in enumerate(characters, 1)]
        )
    )
    enabled = gc.isenabled()
    gc.disable()
    try:
        cursor = matcher.iter_neural_matches("nihao")
        assert next(cursor) is None
        visit = weakref.ref(cursor.gi_frame.f_locals["visit"])
        cursor.close()
        assert visit() is None, "cancelled scans must release recursive work before cyclic GC"
        assert not matcher._neural_cache
    finally:
        if enabled:
            gc.enable()


def test_background_navigation_retry_preserves_the_manager_clock(monkeypatch):
    import threading
    from types import SimpleNamespace

    from neural_weasel import neural_candidate_pages_scored as scored

    manager = scored.NeuralCandidatePageManager(
        backend=object(),
        pinyin_index=None,
        latin_constraint=SimpleNamespace(completions=()),
        clock=lambda: 7.0,
    )
    identity = scored._SearchIdentity(
        client_session_id="clock-domain",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        mode=scored.NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
    )
    manager._sessions["pending-page"] = SimpleNamespace(identity=identity, frozen_pages={})
    manager._background_searches.add("pending-page")
    event = threading.Event()
    event.set()
    manager._background_search_events["pending-page"] = event
    monkeypatch.setattr(manager, "query_page", lambda **kwargs: kwargs)
    retry = scored.NeuralCandidatePageManager.query_page(
        manager,
        client_session_id="clock-domain",
        composition_revision=1,
        context_epoch=0,
        context_session=None,
        source_revision=None,
        state=None,
        mode=scored.NeuralLanguageMode.CHINESE_FIRST,
        raw_keys="nihao",
        page_index=1,
        candidate_set_id="pending-page",
        deadline_ms=5.0,
        deadline_started=7.0,
    )
    assert retry["deadline_started"] == 7.0
    assert retry["deadline_ms"] == 5.0


def test_incomplete_lexical_tail_materializes_only_selected_children(make_index, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from neural_weasel import neural_candidate_pages_v3 as pages

    characters = [chr(code) for code in range(0x4E00, 0x6000) if is_simplified_han(chr(code))][
        :1024
    ]
    rows = [(1, "你", "ni", "ni", 1, 0)]
    rows.extend((i, char, "hao", "hao", 1, 0) for i, char in enumerate(characters, 2))
    manager = pages.NeuralCandidatePageManager(
        backend=object(),
        pinyin_index=make_index(rows),
        latin_constraint=SimpleNamespace(completions=()),
    )
    manager._baseline_scores = np.zeros(2048, dtype=np.float32)
    snapshot = SimpleNamespace(
        identity=SimpleNamespace(raw_keys="nih", context_epoch=0),
        frontier=[],
        pending=[],
        seen_candidates=set(),
    )
    constructed = 0
    original = pages._HanSearchPath

    def counted_path(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pages, "_HanSearchPath", counted_path)
    cursor = manager._iter_lexical_completion_candidates(snapshot)
    first = []
    while len(first) < 7:
        candidate = next(cursor)
        if candidate is not None:
            first.append(candidate)
    assert constructed <= 32, "the first page must not materialize every suffix path"
    rest = [candidate for candidate in cursor if candidate is not None]
    assert len(first) + len(rest) == 1024
    assert [candidate.token_path for candidate in first + rest] == [(1, i) for i in range(2, 1026)]


def test_lazy_suffix_order_preserves_parent_score_rounding(make_index):
    from types import SimpleNamespace

    import numpy as np

    from neural_weasel import neural_candidate_pages_v3 as pages

    manager = pages.NeuralCandidatePageManager(
        backend=object(),
        pinyin_index=make_index(
            [
                (1, "你", "ni", "ni", 1, 0),
                (2, "好", "hao", "hao", 1, 0),
                (3, "号", "hao", "hao", 1, 0),
            ]
        ),
        latin_constraint=SimpleNamespace(completions=()),
    )
    manager._baseline_scores = np.array([0, 0, 1, 2], dtype=np.float32)
    snapshot = SimpleNamespace(
        identity=SimpleNamespace(raw_keys="nih", context_epoch=1),
        frontier=[
            pages._DeferredHanFrontier(
                raw_keys="nih", plan=manager._root_han_plan("nih"), scores=np.array([1e30])
            )
        ],
        pending=[],
        seen_candidates=set(),
    )
    result = [
        candidate
        for candidate in manager._iter_lexical_completion_candidates(snapshot)
        if candidate is not None
    ]
    assert [candidate.token_path for candidate in result] == [(1, 2), (1, 3)]


def test_incomplete_first_page_precedes_predicted_suffix_scan(make_index, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from neural_weasel import neural_candidate_pages_v3 as pages

    characters = [chr(code) for code in range(0x4E00, 0x6000) if is_simplified_han(chr(code))][
        :1024
    ]
    rows = [(1, "你", "ni", "ni", 1, 0)]
    rows.extend((i, char, "hao", "hao", 1, 0) for i, char in enumerate(characters[:7], 2))
    rows.extend((i, f"好{char}", "haoma", "hao'ma", 2, 0) for i, char in enumerate(characters, 9))
    manager = pages.NeuralCandidatePageManager(
        backend=object(),
        pinyin_index=make_index(rows),
        latin_constraint=SimpleNamespace(completions=()),
    )
    manager._baseline_scores = np.zeros(2048, dtype=np.float32)
    snapshot = SimpleNamespace(
        identity=SimpleNamespace(raw_keys="nih", context_epoch=0),
        frontier=[],
        pending=[],
        seen_candidates=set(),
    )
    constructed = 0
    original = pinyin_partial.PartialPinyinMatch

    def counted_match(*args):
        nonlocal constructed
        constructed += 1
        return original(*args)

    monkeypatch.setattr(pinyin_partial, "PartialPinyinMatch", counted_match)
    cursor = manager._iter_lexical_completion_candidates(snapshot)
    first = []
    while len(first) < 7:
        candidate = next(cursor)
        if candidate is not None:
            first.append(candidate)
    assert constructed <= 64, "first-page work must precede extra predicted syllables"
    rest = [candidate for candidate in cursor if candidate is not None]
    assert len(rest) == 1024
    assert all(candidate.predicted_syllables == 0 for candidate in first)
    assert all(candidate.predicted_syllables == 1 for candidate in rest)
