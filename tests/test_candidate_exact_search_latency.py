from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest

from neural_weasel import neural_candidate_pages_v3 as search
from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import NeuralLanguageMode, _SearchIdentity
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class _Runtime:
    logits: np.ndarray

    def load(self):
        pass

    def full_logits(self, before, after):
        return RuntimeSnapshot(self.logits, before, after, 0.1)

    def diagnostics(self):
        return {}

    def invalidate_private_state(self):
        pass


@pytest.mark.parametrize("single_token", [True, False])
def test_exact_yanchi_does_not_materialize_longer_readings_first(
    make_index, monkeypatch, single_token
):
    rows = [
        (1, "延", "yan", "yan", 1, 0),
        (2, "迟", "chi", "chi", 1, 0),
        *[(token, "延迟入", "yanchiru", "yan'chi'ru", 3, 0) for token in range(4, 324)],
    ]
    if single_token:
        rows.append((3, "延迟", "yanchi", "yan'chi", 2, 0))
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(_Runtime(np.zeros(324, dtype=np.float32))),
        pinyin_constraint=PinyinConstraint(make_index(rows)),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )
    engine.initialize_neural_baseline()
    manager = engine.candidate_pages
    session = manager._new_session(
        _SearchIdentity(
            "exact-search", 1, 0, None, None, NeuralLanguageMode.CHINESE_FIRST, "yanchi"
        ),
        None,
    )
    # Exercise legal-path discovery independently of the already-visible roots.
    session = replace(session, pending=[], seen_candidates=set())
    original_path = search._HanSearchPath
    longer_readings = []

    def tracked_path(**kwargs):
        if kwargs["pinyin_path"] == ("yan", "chi", "ru"):
            longer_readings.append(kwargs["token_path"])
        return original_path(**kwargs)

    monkeypatch.setattr(search, "_HanSearchPath", tracked_path)
    cursor = manager._iter_lexical_completion_candidates(session)
    first = next(candidate for candidate in cursor if candidate is not None)
    assert (first.text, first.pinyin) == ("延迟", "yan'chi")
    assert not longer_readings, "exact discovery waited for all longer-reading roots"
    remaining = [candidate for candidate in cursor if candidate is not None]
    assert any(candidate.text == "延迟入" for candidate in remaining)
    assert (
        len({(candidate.text, candidate.consumed_keys) for candidate in [first, *remaining]}) == 2
    )
