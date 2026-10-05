from __future__ import annotations

import numpy as np
import pytest

from neural_weasel import neural_candidate_pages_scored as scored
from neural_weasel import neural_candidate_pages_v2 as v2
from neural_weasel.backends import BackendState
from neural_weasel.unified import LatinCompletion, LatinPrefixConstraint


def _manager(manager_type):
    class Backend:
        def score_allowed_tokens(self, state, token_ids):
            return state.payload[np.asarray(token_ids)]

    manager = manager_type(
        backend=Backend(),
        pinyin_index=None,
        latin_constraint=LatinPrefixConstraint(
            [LatinCompletion(f"debug{i:03}", (i,)) for i in range(240)]
            + [LatinCompletion("DEbug000", (240,)), LatinCompletion("debug", (241,))]
        ),
    )
    manager.install_baseline_scores(np.linspace(-4.0, -1.0, 242, dtype=np.float32))
    return manager


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
def test_growing_public_prefix_does_not_reallocate_same_baseline_frontier(
    monkeypatch, manager_type
):
    manager = _manager(manager_type)
    _, initial = manager._root_latin_candidates_and_frontier("de", None, 0)
    original = {path.token_path: path for path in initial if isinstance(path, v2._SearchPath)}
    constructor = v2._SearchPath
    allocations = []

    def counted_path(**kwargs):
        allocations.append(1)
        return constructor(**kwargs)

    monkeypatch.setattr(v2, "_SearchPath", counted_path)
    _, grown = manager._root_latin_candidates_and_frontier("deb", None, 7)
    paths = [path for path in grown if isinstance(path, constructor)]
    assert [path.token_path for path in paths] == list(original)
    assert not allocations, "growing a prefix must not rebuild every unchanged baseline path"
    assert all(path is original[path.token_path] for path in paths)


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
def test_context_scoring_does_not_reuse_or_poison_baseline_paths(manager_type):
    manager = _manager(manager_type)
    _, baseline = manager._root_latin_candidates_and_frontier("deb", None, 0)
    by_token = {path.token_path: path for path in baseline if isinstance(path, v2._SearchPath)}
    values = np.linspace(-0.1, -8.0, 242, dtype=np.float32)
    state = BackendState(7, "test", "", "", 0.0, 0.0, values)
    expected_scores = manager._score_root(state, range(242))
    _, contextual = manager._root_latin_candidates_and_frontier("deb", state, 7)
    for path in contextual:
        if isinstance(path, v2._SearchPath):
            assert path is not by_token[path.token_path]
            assert path.score == pytest.approx(float(expected_scores[path.token_path[0]]))
    _, replay = manager._root_latin_candidates_and_frontier("deb", None, 9)
    assert [path for path in replay if isinstance(path, v2._SearchPath)] == list(by_token.values())


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
def test_reinstalling_baseline_invalidates_reused_root_scores(manager_type):
    manager = _manager(manager_type)
    _, before = manager._root_latin_candidates_and_frontier("deb", None, 0)
    old = {path.token_path: path for path in before if isinstance(path, v2._SearchPath)}
    values = np.linspace(-9.0, -5.0, 242, dtype=np.float32)
    manager.install_baseline_scores(values)
    expected_scores = manager._score_root(None, range(242))
    _, after = manager._root_latin_candidates_and_frontier("deb", None, 0)
    for path in after:
        if isinstance(path, v2._SearchPath):
            assert path is not old[path.token_path]
            assert path.score == pytest.approx(float(expected_scores[path.token_path[0]]))


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
def test_context_only_search_does_not_populate_public_root_cache(manager_type):
    manager = _manager(manager_type)
    state = BackendState(7, "test", "", "", 0.0, 0.0, np.zeros(242, dtype=np.float32))
    manager._root_latin_candidates_and_frontier("deb", state, 7)
    assert not manager._baseline_latin_root_paths


@pytest.mark.parametrize(
    "manager_type", [v2.NeuralCandidatePageManager, scored.NeuralCandidatePageManager]
)
def test_root_cache_is_bounded_by_public_vocabulary_not_input_variations(manager_type):
    manager = _manager(manager_type)
    for prefix in ("de", "DE", "deb", "debug", "debug0", "debug00", "unmatched"):
        manager._root_latin_candidates_and_frontier(prefix, None, 8)
    public_keys = {(entry.text, entry.token_path) for entry in manager._latin_root_completions}
    assert set(manager._baseline_latin_root_paths) <= public_keys
    assert len(manager._baseline_latin_root_paths) == len(public_keys)
