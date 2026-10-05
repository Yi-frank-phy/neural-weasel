from __future__ import annotations

import gc
import weakref

import pytest

from neural_weasel.neural_candidate_pages import NeuralCandidatePageManager
from neural_weasel.unified import LatinPrefixConstraint


@pytest.mark.parametrize("exit_kind", ["completed", "timeout", "error"])
def test_lexical_path_count_releases_manager_before_cyclic_gc(make_index, exit_kind):
    manager = NeuralCandidatePageManager(
        backend=None,
        pinyin_index=make_index([(1, "好", "hao", 1, 0), (2, "吗", "ma", 1, 0)]),
        latin_constraint=LatinPrefixConstraint(()),
    )
    # Force the path counter instead of the large-plan shortcut.
    manager._root_han_plan = lambda raw: ()
    if exit_kind == "timeout":
        ticks = iter((0.0, 0.003))
        manager.clock = lambda: next(ticks)
    else:
        manager.clock = lambda: 0.0
    if exit_kind == "error":

        def failing_matches(*args):
            raise RuntimeError("synthetic matcher failure")

        manager.matcher.iter_neural_matches = failing_matches
    reference = weakref.ref(manager)
    enabled = gc.isenabled()
    # Isolated fixture only: production automatic collection stays enabled.
    gc.disable()
    try:
        if exit_kind == "error":
            with pytest.raises(RuntimeError, match="synthetic matcher failure"):
                manager._lexical_tail_may_fill_page("haoma", 7)
        else:
            assert manager._lexical_tail_may_fill_page("haoma", 7) is (exit_kind == "timeout")
        del manager
        assert reference() is None, "finished path checks must not retain the manager until GC"
    finally:
        if enabled:
            gc.enable()
        gc.collect()
