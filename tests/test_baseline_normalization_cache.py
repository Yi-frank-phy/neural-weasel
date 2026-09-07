import numpy as np
import pytest
from test_candidate_page_concurrency import _engine

from neural_weasel import neural_candidate_pages_scored as scored


@pytest.mark.parametrize(
    "values", [[0.0, 3.0, -2.0], [float("inf"), 0.0, float("inf")], [float("-inf")] * 3]
)
def test_baseline_normalizes_once_and_replacement_invalidates(make_index, monkeypatch, values):
    engine, _ = _engine(make_index)
    manager = engine.candidate_pages
    original = scored._selected_log_probs
    calls = []

    def count(logits, ids):
        calls.append(1)
        return original(logits, ids)

    monkeypatch.setattr(scored, "_selected_log_probs", count)
    manager.install_baseline_scores(values)
    for ids in [(2, 0), (1,), (0, 2, 1)]:
        np.testing.assert_array_equal(manager._score_root(None, ids), original(values, ids))
    assert len(calls) == 1
    replacement = np.array([2.0, 1.0, 0.0], dtype=np.float32)
    manager.install_baseline_scores(replacement)
    replacement[:] = 100
    np.testing.assert_array_equal(manager._score_root(None, (1,)), original([2.0, 1.0, 0.0], (1,)))
    assert len(calls) == 2
