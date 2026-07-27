from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from neural_weasel.beam import (
    BeamStep,
    SerialReplayBackend,
    constrained_beam_search,
)
from neural_weasel.index import IndexedPronunciation
from neural_weasel.pinyin import ParsedPinyinInput


def _entry(
    token_id: int,
    text: str,
    *syllables: str,
) -> IndexedPronunciation:
    return IndexedPronunciation(
        token_id=token_id,
        text=text,
        pinyin="".join(syllables),
        syllable_path=tuple(syllables),
        syllables=len(syllables),
        coverage=False,
    )


class FakeIndex:
    def __init__(self, entries: Sequence[IndexedPronunciation]) -> None:
        self.entries = entries
        self.queries: list[str] = []

    def compatible(self, parsed: ParsedPinyinInput) -> list[IndexedPronunciation]:
        self.queries.append(parsed.compact)
        return [
            entry
            for entry in self.entries
            if entry.pinyin.startswith(parsed.compact) or parsed.compact.startswith(entry.pinyin)
        ]


class FakeTokenizer:
    def __init__(self, canonical: dict[str, tuple[int, ...]]) -> None:
        self.canonical = canonical

    def encode(self, text: str, *, add_special_tokens: bool = False) -> Sequence[int]:
        assert not add_special_tokens
        return self.canonical.get(text, ())


class FakeBackend:
    def __init__(self, tables: dict[tuple[int, ...], list[float]]) -> None:
        self.tables = tables
        self.advanced: list[tuple[tuple[int, ...], int]] = []

    def root(self) -> BeamStep[tuple[int, ...]]:
        return BeamStep((), self.tables[()])

    def advance(
        self,
        parent_state: tuple[int, ...],
        token_id: int,
    ) -> BeamStep[tuple[int, ...]]:
        self.advanced.append((parent_state, token_id))
        child = (*parent_state, token_id)
        return BeamStep(child, self.tables[child])


def _probs(size: int, **values: float) -> list[float]:
    result = [-math.inf] * size
    for token_id, value in values.items():
        result[int(token_id)] = value
    return result


def test_filters_by_pinyin_before_selecting_highest_scores() -> None:
    index = FakeIndex([_entry(1, "你", "ni"), _entry(2, "泥", "ni")])
    backend = FakeBackend({(): _probs(4, **{"0": -0.001, "1": -3.0, "2": -2.0})})
    tokenizer = FakeTokenizer({"你": (1,), "泥": (2,)})

    candidates = constrained_beam_search(
        backend=backend,
        index=index,
        tokenizer=tokenizer,
        raw_pinyin="ni",
    )

    assert [candidate.text for candidate in candidates] == ["泥", "你"]
    assert all(candidate.token_ids != (0,) for candidate in candidates)
    assert index.queries == ["ni"]


def test_accumulates_log_probabilities_across_multiple_tokens() -> None:
    entries = [
        _entry(1, "你", "ni"),
        _entry(2, "拟", "ni"),
        _entry(3, "好", "hao"),
        _entry(4, "号", "hao"),
        _entry(5, "吗", "ma"),
    ]
    backend = FakeBackend(
        {
            (): _probs(6, **{"1": -0.2, "2": -0.1}),
            (1,): _probs(6, **{"3": -0.1, "4": -3.0}),
            (2,): _probs(6, **{"3": -2.0, "4": -0.2}),
            (1, 3): _probs(6, **{"5": -0.1}),
            (1, 4): _probs(6, **{"5": -0.1}),
            (2, 3): _probs(6, **{"5": -0.1}),
            (2, 4): _probs(6, **{"5": -0.1}),
        }
    )
    tokenizer = FakeTokenizer(
        {
            "你": (1,),
            "拟": (2,),
            "你好": (1, 3),
            "你号": (1, 4),
            "拟好": (2, 3),
            "拟号": (2, 4),
            "你好吗": (1, 3, 5),
            "你号吗": (1, 4, 5),
            "拟好吗": (2, 3, 5),
            "拟号吗": (2, 4, 5),
        }
    )

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="nihaoma",
    )

    assert candidates[0].text == "你好吗"
    assert candidates[0].token_ids == (1, 3, 5)
    assert candidates[0].score == pytest.approx(-0.4)
    assert candidates[0].pinyin == "ni'hao'ma"
    assert candidates[0].exact_pinyin


def test_beam_width_is_hard_limited_to_four_legal_active_paths() -> None:
    entries = [_entry(token_id, chr(0x4E00 + token_id), "a") for token_id in range(1, 6)]
    root = _probs(7, **{str(token_id): -float(token_id) for token_id in range(1, 6)})
    tables = {(): root}
    for token_id in range(1, 5):
        tables[(token_id,)] = _probs(7, **{"6": -0.1})
    backend = FakeBackend(tables)
    canonical = {entry.text: (entry.token_id,) for entry in entries}
    tokenizer = FakeTokenizer(canonical)

    constrained_beam_search(
        backend=backend,
        index=FakeIndex([*entries, _entry(6, "乙", "b")]),
        tokenizer=tokenizer,
        raw_pinyin="ab",
    )

    assert len(backend.advanced) == 4
    assert [token_id for _, token_id in backend.advanced] == [1, 2, 3, 4]


def test_search_never_expands_beyond_four_model_tokens() -> None:
    entries = [
        _entry(1, "甲", "a"),
        _entry(2, "乙", "b"),
        _entry(3, "丙", "c"),
        _entry(4, "丁", "d"),
        _entry(5, "戊", "e"),
    ]
    backend = FakeBackend(
        {
            (): _probs(6, **{"1": -0.1}),
            (1,): _probs(6, **{"2": -0.1}),
            (1, 2): _probs(6, **{"3": -0.1}),
            (1, 2, 3): _probs(6, **{"4": -0.1}),
        }
    )
    tokenizer = FakeTokenizer(
        {
            "甲": (1,),
            "甲乙": (1, 2),
            "甲乙丙": (1, 2, 3),
            "甲乙丙丁": (1, 2, 3, 4),
            "甲乙丙丁戊": (1, 2, 3, 4, 5),
        }
    )

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="abcde",
    )

    assert candidates == []
    assert backend.advanced[-1] == ((1, 2), 3)
    assert all(len(parent) < 3 for parent, _ in backend.advanced)


def test_rejects_paths_longer_than_twelve_han_characters() -> None:
    allowed = "一" * 12
    too_long = "丁" * 13
    entries = [_entry(1, allowed, "a"), _entry(2, too_long, "a")]
    backend = FakeBackend({(): _probs(3, **{"1": -1.0, "2": -0.01})})
    tokenizer = FakeTokenizer({allowed: (1,), too_long: (2,)})

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="a",
    )

    assert [candidate.text for candidate in candidates] == [allowed]


def test_only_canonical_token_sequences_are_returned_or_expanded() -> None:
    entries = [_entry(1, "你", "ni"), _entry(2, "好", "hao"), _entry(3, "你好", "ni", "hao")]
    backend = FakeBackend(
        {
            (): _probs(4, **{"1": -0.01, "3": -1.0}),
            (1,): _probs(4, **{"2": -0.01}),
        }
    )
    tokenizer = FakeTokenizer({"你": (1,), "你好": (3,)})

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="nihao",
    )

    assert [candidate.token_ids for candidate in candidates] == [(3,)]
    assert backend.advanced == [((), 1)]


def test_incomplete_final_syllable_can_finish_with_a_multi_token_candidate() -> None:
    entries = [_entry(1, "纠", "jiu"), _entry(2, "缠", "chan")]
    backend = FakeBackend(
        {
            (): _probs(3, **{"1": -0.1}),
            (1,): _probs(3, **{"2": -0.2}),
        }
    )
    tokenizer = FakeTokenizer({"纠": (1,), "纠缠": (1, 2)})

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="jiuc",
    )

    assert len(candidates) == 1
    assert candidates[0].text == "纠缠"
    assert candidates[0].pinyin == "jiu'chan"
    assert candidates[0].consumed_keys == 4
    assert not candidates[0].exact_pinyin


def test_serial_replay_backend_reuses_parent_without_copying_state() -> None:
    calls: list[tuple[int, ...]] = []
    tables = {
        (): _probs(4, **{"1": -0.1, "2": -0.2}),
        (1,): _probs(4, **{"3": -0.1}),
        (2,): _probs(4, **{"3": -0.1}),
    }

    def evaluate(path: tuple[int, ...]) -> Sequence[float]:
        calls.append(path)
        return tables[path]

    backend = SerialReplayBackend(evaluate)
    tokenizer = FakeTokenizer(
        {
            "你": (1,),
            "泥": (2,),
            "你好": (1, 3),
            "泥好": (2, 3),
        }
    )
    entries = [_entry(1, "你", "ni"), _entry(2, "泥", "ni"), _entry(3, "好", "hao")]

    candidates = constrained_beam_search(
        backend=backend,
        index=FakeIndex(entries),
        tokenizer=tokenizer,
        raw_pinyin="nihao",
    )
    backend.root()

    assert [candidate.text for candidate in candidates] == ["你好", "泥好"]
    assert calls == [(), (1,), (2,)]


def test_rejects_raw_logits_and_out_of_range_token_ids() -> None:
    tokenizer = FakeTokenizer({"你": (1,)})
    index = FakeIndex([_entry(1, "你", "ni")])

    with pytest.raises(ValueError, match="normalized"):
        constrained_beam_search(
            backend=FakeBackend({(): [0.0, 2.0]}),
            index=index,
            tokenizer=tokenizer,
            raw_pinyin="ni",
        )

    with pytest.raises(IndexError, match="outside"):
        constrained_beam_search(
            backend=FakeBackend({(): [0.0]}),
            index=index,
            tokenizer=tokenizer,
            raw_pinyin="ni",
        )

