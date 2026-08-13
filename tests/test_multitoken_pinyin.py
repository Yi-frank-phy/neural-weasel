from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from neural_weasel.unified import PinyinConstraint


class FakeConditionalSession:
    def __init__(self) -> None:
        self.paths: list[tuple[int, ...]] = [()]
        self.advances: list[tuple[tuple[int, ...], tuple[int, ...]]] = []

    def advance(self, parent_indices, token_ids) -> float:
        parents = tuple(int(value) for value in parent_indices)
        tokens = tuple(int(value) for value in token_ids)
        self.advances.append((parents, tokens))
        self.paths = [self.paths[parent] + (token,) for parent, token in zip(parents, tokens, strict=True)]
        return 0.0

    def score_allowed(self, allowed_token_ids_by_beam):
        results = []
        for path, allowed in zip(self.paths, allowed_token_ids_by_beam, strict=True):
            table = {
                (10,): {20: 9.0, 21: 1.0},
                (10, 20): {30: 8.0},
                (10, 21): {30: 0.5},
            }.get(path, {})
            results.append(
                np.asarray([table.get(int(token_id), -20.0) for token_id in allowed], dtype=np.float32)
            )
        return tuple(results)


class FakeConditionalBackend:
    def __init__(self, root_scores: dict[int, float]) -> None:
        self.root_scores = root_scores
        self.session = FakeConditionalSession()
        self.started = 0

    def score_allowed_tokens(self, state, allowed_token_ids):
        del state
        return np.asarray(
            [self.root_scores.get(int(token_id), -20.0) for token_id in allowed_token_ids],
            dtype=np.float32,
        )

    def start_conditional_continuation(self, state):
        del state
        self.started += 1
        return self.session


def test_partial_matches_accept_full_syllables_initials_and_incomplete_final(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
            (40, "这么好", "zhemehao", "zhe'me'hao", 3, 0),
            (50, "神经", "shenjing", "shen'jing", 2, 0),
        ]
    )

    first = index.partial_matches("zhendezmh", 0)
    assert any(match.entry.text == "真的" and match.next_position == 6 for match in first)

    second = index.partial_matches("zhendezmh", 6)
    assert any(match.entry.text == "这么" and match.next_position == 8 for match in second)

    third = index.partial_matches("zhendezmh", 8)
    assert any(match.entry.text == "好" and match.next_position == 9 for match in third)

    single_token = index.partial_matches("zhemhao", 0)
    assert any(match.entry.text == "这么好" and match.next_position == 7 for match in single_token)

    incomplete_final = index.partial_matches("shenji", 0)
    assert any(match.entry.text == "神经" and match.next_position == 6 for match in incomplete_final)


def test_multitoken_fallback_uses_conditional_scores_for_zhendezmh(make_index) -> None:
    index = make_index(
        [
            (10, "真的", "zhende", "zhen'de", 2, 0),
            (20, "这么", "zheme", "zhe'me", 2, 0),
            (21, "怎么", "zenme", "zen'me", 2, 0),
            (30, "好", "hao", "hao", 1, 0),
        ]
    )
    backend = FakeConditionalBackend({10: 10.0})
    state = SimpleNamespace(epoch=7)
    constraint = PinyinConstraint(index)

    candidates = constraint.candidates(
        "zhendezmh",
        backend=backend,
        state=state,
        after_text="",
    )

    assert backend.started == 1
    assert candidates[0].text == "真的这么好"
    assert candidates[0].token_path == (10, 20, 30)
    assert len(backend.session.advances) == 2


def test_single_token_partial_fallback_precedes_multitoken_search(make_index) -> None:
    index = make_index([(40, "这么好", "zhemehao", "zhe'me'hao", 3, 0)])
    backend = FakeConditionalBackend({40: 10.0})
    state = SimpleNamespace(epoch=3)
    constraint = PinyinConstraint(index)

    candidates = constraint.candidates(
        "zhemhao",
        backend=backend,
        state=state,
        after_text="",
    )

    assert candidates[0].text == "这么好"
    assert candidates[0].token_path == (40,)
    assert backend.started == 0


def test_normal_full_pinyin_never_starts_multitoken_fallback(make_index) -> None:
    index = make_index([(5, "神经", "shenjing", "shen'jing", 2, 0)])
    backend = FakeConditionalBackend({5: 10.0})
    state = SimpleNamespace(epoch=2)
    constraint = PinyinConstraint(index)

    candidates = constraint.candidates(
        "shenjing",
        backend=backend,
        state=state,
        after_text="",
    )

    assert candidates[0].text == "神经"
    assert backend.started == 0
