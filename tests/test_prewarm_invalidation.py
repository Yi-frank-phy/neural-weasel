from collections import OrderedDict
from dataclasses import replace

import numpy as np

from neural_weasel.neural_candidate_pages_scored import NeuralCandidatePageManager
from neural_weasel.neural_candidates import _literal_candidate


def test_han_learning_does_not_rebuild_unchanged_latin_roots(monkeypatch):
    manager = object.__new__(NeuralCandidatePageManager)
    manager._baseline_scores = np.zeros(4)
    manager._dirty_single_letter_prewarms = set()
    cached = ((), ())
    manager._baseline_latin_roots = {"m": cached}
    manager._baseline_single_letter = {}
    manager._baseline_han_cache = OrderedDict()
    candidate = replace(_literal_candidate("明白", 0), script="han", token_path=(1, 2))
    manager._remember_baseline_han_candidate("m", candidate)

    def rebuild(**kwargs):
        assert manager._baseline_latin_roots.get("m") is cached
        return [], [], "baseline"

    monkeypatch.setattr(manager, "_root_candidates", rebuild)
    manager._refresh_dirty_single_letter_prewarms()
    assert len(manager._baseline_single_letter) == 2


def test_latin_learning_invalidates_only_its_own_prefix():
    manager = object.__new__(NeuralCandidatePageManager)
    manager._baseline_latin_roots = {"m": ((), ()), "n": ((), ())}
    manager._baseline_latin_cache = OrderedDict()
    manager._dirty_single_letter_prewarms = set()
    candidate = replace(_literal_candidate("more", 0), script="latin", token_path=(1, 2))
    manager._remember_baseline_latin_candidate(candidate)
    assert set(manager._baseline_latin_roots) == {"n"}
    assert manager._dirty_single_letter_prewarms == {"m"}
