import numpy as np
import pytest
from test_candidate_page_concurrency import _engine

from neural_weasel import neural_candidate_pages_scored as scored
from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot


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


def test_context_normalizer_is_precomputed_off_the_page_zero_path(make_index, monkeypatch) -> None:
    logits = np.array([0.25, -0.5, 1.0, 0.75], dtype=np.float32)

    class Runtime:
        def full_logits(self, _before: str, _after: str) -> RuntimeSnapshot:
            return RuntimeSnapshot(
                payload=logits,
                before_hash="before",
                after_hash="after",
                latency_ms=1.0,
            )

    runtime = Runtime()
    engine, _ = _engine(make_index)
    backend = FullLogitsSnapshotBackend(runtime)
    engine.candidate_pages.backend = backend
    state = backend.update_context("context", "")
    expected = scored._selected_log_probs(logits, [3, 1])

    def fail_page_time_normalization(*_args, **_kwargs):
        raise AssertionError("full-vocabulary normalization must not run in page zero")

    monkeypatch.setattr(scored, "_selected_log_probs", fail_page_time_normalization)
    actual = engine.candidate_pages._score_root(state, [3, 1])

    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-6)
