from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.neural_candidates import NeuralLanguageMode
from neural_weasel.unified import LatinPrefixConstraint, PinyinConstraint


@dataclass
class BaselineRuntime:
    logits: np.ndarray
    calls: int = 0

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.calls += 1
        return RuntimeSnapshot(self.logits, before, after, 0.1)

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


# Ordinary Mandarin syllables start with these ASCII letters. i/u do not start
# a syllable in the pinyin graph, while v is the ASCII spelling of umlaut-u
# inside syllables such as lv/nv rather than an initial of its own.
_VALID_SINGLE_LETTER_ENTRANCES = {
    "a": (1, "啊", "a"),
    "b": (2, "吧", "ba"),
    "c": (3, "擦", "ca"),
    "d": (4, "的", "de"),
    "e": (5, "饿", "e"),
    "f": (6, "发", "fa"),
    "g": (7, "个", "ge"),
    "h": (8, "和", "he"),
    "j": (9, "就", "jiu"),
    "k": (10, "看", "kan"),
    "l": (11, "了", "le"),
    "m": (12, "吗", "ma"),
    "n": (13, "你", "ni"),
    "o": (14, "哦", "o"),
    "p": (15, "怕", "pa"),
    "q": (16, "去", "qu"),
    "r": (17, "人", "ren"),
    "s": (18, "是", "shi"),
    "t": (19, "他", "ta"),
    "w": (20, "我", "wo"),
    "x": (21, "想", "xiang"),
    "y": (22, "有", "you"),
    "z": (23, "在", "zai"),
}


def test_all_valid_single_letter_entrances_are_prewarmed_neural_han(make_index) -> None:
    rows = [
        (token_id, text, syllable, syllable, 1, 0)
        for token_id, text, syllable in _VALID_SINGLE_LETTER_ENTRANCES.values()
    ]
    index = make_index(rows)
    logits = np.full(32, -20.0, dtype=np.float32)
    for token_id, _, _ in _VALID_SINGLE_LETTER_ENTRANCES.values():
        logits[token_id] = float(100 - token_id)
    runtime = BaselineRuntime(logits)
    engine = BilingualImeEngine(
        backend=FullLogitsSnapshotBackend(runtime),
        pinyin_constraint=PinyinConstraint(index),
        latin_prefix_constraint=LatinPrefixConstraint(()),
    )

    engine.initialize_neural_baseline()

    assert runtime.calls == 1
    assert set(engine.candidate_pages._baseline_single_letter) == {
        (letter, mode)
        for letter in "abcdefghijklmnopqrstuvwxyz"
        for mode in NeuralLanguageMode
    }

    for revision, letter in enumerate(_VALID_SINGLE_LETTER_ENTRANCES, start=1):
        page = engine.query_candidate_page(
            client_session_id="single-letter-contract",
            composition_revision=revision,
            context_epoch=0,
            context_session=None,
            source_revision=None,
            language_mode="chinese_first",
            raw_keys=letter,
            page_index=0,
        )
        assert page.score_source == "baseline"
        assert any(candidate.script == "han" for candidate in page.candidates), letter
        assert all(candidate.constraint_kind != "literal" for candidate in page.candidates), letter

    # Querying every entrance consumed only the permanent startup scores. Page 0
    # did not trigger another model forward for any letter.
    assert runtime.calls == 1
