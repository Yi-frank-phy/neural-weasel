from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import (
    CandidatePageTimeout,
    NeuralLanguageMode,
    _SearchIdentity,
)
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


def _engine(
    make_index,
    release,
    *,
    multi_token=False,
    root_token_count=None,
    short_suffix=False,
    suffix_ties=False,
):
    rows = [
        (i, text, pinyin, pinyin, 1, 0)
        for i, (text, pinyin) in enumerate(
            zip(
                "你好人民中国书画",
                ("ni", "hao", "ren", "min", "zhong", "guo", "shu", "hua"),
                strict=True,
            ),
            1,
        )
    ]
    rows += [(i, text, "feng", "feng", 1, 0) for i, text in enumerate("风峰丰封枫锋疯", 11)]
    if multi_token:
        rows[0] = (None, "你", "ni", "ni", 1, 1, (1, 19))
        rows[1] = (None, "好", "hao", "hao", 1, 1, (2, 20))
        rows = [row for row in rows if row[1] != "画"]
        rows = [
            (None, row[1], "feng", "feng", 1, 1, (row[0], 21)) if row[2] == "feng" else row
            for row in rows
        ]
        if root_token_count is not None:
            rows[0] = (None, "你", "ni", "ni", 1, 1, (1,) * root_token_count)
        if short_suffix:
            rows[-7] = (11, "风", "feng", "feng", 1, 0)
        if suffix_ties:
            rows[-7] = (None, "风", "feng", "feng", 1, 1, (22, 23))
            rows[-6] = (None, "峰", "feng", "feng", 1, 1, (24, 1))
    scores = np.arange(32, dtype=np.float32) * -0.01
    if short_suffix:
        scores[21] = 1000
    if suffix_ties:
        scores.fill(0)

    def delayed_continuation(*args, **kwargs):
        release.wait(1.0)
        return []

    runtime = SimpleNamespace(
        load=lambda: None,
        full_logits=lambda before, after: RuntimeSnapshot(
            scores, before, after, 0.0, continuation_root=("public-root",)
        ),
        continue_from_root=delayed_continuation,
    )
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    return engine


@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("multi_token", [False, True])
def test_long_initial_string_publishes_complete_han_without_waiting_for_model(
    make_index, contextual, multi_token
):
    release = threading.Event()
    engine = _engine(make_index, release, multi_token=multi_token)
    epoch = engine.update_context("public-test-context").epoch if contextual else 0
    deadline = time.monotonic() + 0.5
    try:
        while True:
            try:
                page = engine.query_candidate_page(
                    client_session_id="public-long-initials",
                    composition_revision=1,
                    context_epoch=epoch,
                    context_session=None,
                    source_revision=None,
                    language_mode="chinese_first",
                    raw_keys="nhrmzgshf",
                    page_index=0,
                )
                break
            except CandidatePageTimeout:
                assert time.monotonic() < deadline, "long initials never published a first page"
                time.sleep(0.005)
        han = [candidate for candidate in page.candidates if candidate.script == "han"]
        assert len(han) == 7
        assert all(candidate.completes_input and candidate.consumed_keys == 9 for candidate in han)
        if multi_token:
            for candidate in han:
                final_token = 11 + "风峰丰封枫锋疯".index(candidate.text[-1])
                assert candidate.token_path == (1, 19, 2, 20, 3, 4, 5, 6, 7, 2, 20, final_token, 21)
        else:
            assert all(len(candidate.token_path) == 9 for candidate in han)
        assert all(candidate.ranking_tier == 50 for candidate in han)
        assert all(candidate.context_epoch == epoch for candidate in han)
    finally:
        release.set()
        engine.candidate_pages.clear_sessions()


@pytest.mark.parametrize(
    ("raw", "eligible"),
    [
        ("nh", False),
        ("nhrm", False),
        ("nhrsh", False),
        ("n'h'r'sh", False),
        ("nhrmzgshf", True),
        ("n'h'r'm'z'g's'h'f", True),
        ("nhrmzgshfi", False),
    ],
)
def test_long_initial_policy_counts_complete_syllables_and_final_prefix(make_index, raw, eligible):
    release = threading.Event()
    engine = _engine(make_index, release)
    try:
        assert engine.candidate_pages._lexical_completion_eligible(raw) is eligible
    finally:
        release.set()


def _lexical_candidates(engine):
    manager = engine.candidate_pages
    identity = _SearchIdentity(
        "public-lexical-path", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "nhrmzgshf"
    )
    session = manager._new_session(identity, None)
    return [
        candidate
        for candidate in manager._iter_lexical_completion_candidates(session)
        if candidate is not None
    ]


@pytest.mark.parametrize(("root_token_count", "count"), [(5, 7), (6, 0)])
def test_lexical_paths_obey_actual_sixteen_token_budget(make_index, root_token_count, count):
    release = threading.Event()
    engine = _engine(make_index, release, multi_token=True, root_token_count=root_token_count)
    try:
        candidates = _lexical_candidates(engine)
        assert len(candidates) == count
        assert all(len(candidate.token_path) == 16 for candidate in candidates)
    finally:
        release.set()


def test_mixed_suffix_lengths_do_not_hide_shorter_complete_path(make_index):
    release = threading.Event()
    engine = _engine(make_index, release, multi_token=True, short_suffix=True)
    try:
        candidates = _lexical_candidates(engine)
        assert len(candidates) == 7
        assert candidates[0].text.endswith("风")
        assert len(candidates[0].token_path) == 12
        assert all(len(candidate.token_path) == 13 for candidate in candidates[1:])
    finally:
        release.set()


def test_multi_token_suffix_ties_follow_complete_path_priority(make_index):
    release = threading.Event()
    engine = _engine(make_index, release, multi_token=True, suffix_ties=True)
    try:
        candidates = _lexical_candidates(engine)
        assert len(candidates) == 7
        assert candidates[0].text.endswith("峰")
        assert candidates[0].token_path[-2:] == (24, 1)
    finally:
        release.set()
        engine.candidate_pages.clear_sessions()
