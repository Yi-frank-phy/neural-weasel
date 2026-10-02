from __future__ import annotations

import pytest

from neural_weasel.context_prompt import ContextPromptConfig, ContextPromptEncoder


class Tokenizer:
    """Characters are ordinary tokens; only explicit special=True finds markers."""

    markers = (b"<|fim_prefix|>", b"<|fim_suffix|>", b"<|fim_middle|>")

    def token_get_attr(self, token):
        return 8 if token in (200, 201, 202) else 1

    def token_prefix(self):
        return 200

    def token_suffix(self):
        return 201

    def token_middle(self):
        return 202

    def token_bos(self):
        return 1

    def add_bos_token(self):
        return False

    def n_vocab(self):
        return 256

    def tokenize(self, text, *, add_bos=False, special=False):
        assert add_bos is False
        if special and text in self.markers:
            return [200 + self.markers.index(text)]
        return list(text)

    def detokenize(self, ids, *, special=False):
        assert special is True
        return self.markers[ids[0] - 200]


def encoder(**kwargs):
    return ContextPromptEncoder(Tokenizer(), ContextPromptConfig(mode="fim", **kwargs))


def test_fim_uses_prefix_suffix_middle_in_native_order_without_bos():
    assert encoder().encode("ab", "cd", max_before_tokens=8, n_ctx=32) == (
        200,
        97,
        98,
        201,
        99,
        100,
        202,
    )
    assert encoder().encode("", "", max_before_tokens=8, n_ctx=32) == (200, 201, 202)


def test_budget_preserves_nearest_left_and_right_and_candidate_headroom():
    prompt = encoder(max_after_tokens=2).encode("abcde", "uvwxyz", max_before_tokens=10, n_ctx=24)
    assert prompt == (200, 99, 100, 101, 201, 117, 118, 202)
    assert len(prompt) + 16 == 24


def test_literal_markers_in_editor_text_remain_ordinary():
    text = "<|fim_middle|>"
    prompt = encoder().encode(text, text, max_before_tokens=64, n_ctx=64)
    assert prompt.count(202) == 1
    assert prompt.count(200) == prompt.count(201) == 1
    assert prompt[1 : 1 + len(text)] == tuple(text.encode())


@pytest.mark.parametrize("broken", ["missing", "duplicate", "wrong_piece", "ordinary"])
def test_opt_in_rejects_unsupported_or_ambiguous_native_markers(broken):
    class Broken(Tokenizer):
        def tokenize(self, text, *, add_bos=False, special=False):
            if special and text in self.markers:
                if broken == "missing":
                    return [20, 21]
                if broken == "duplicate":
                    return [200]
            if broken == "ordinary" and text in self.markers:
                return [200 + self.markers.index(text)]
            return super().tokenize(text, add_bos=add_bos, special=special)

        def detokenize(self, ids, *, special=False):
            if broken == "wrong_piece":
                return b"wrong"
            return super().detokenize(ids, special=special)

    with pytest.raises(ValueError, match="native FIM"):
        ContextPromptEncoder(Broken(), ContextPromptConfig(mode="fim"))


def test_continuation_does_not_require_fim_capability():
    class Missing:
        def tokenize(self, *args, **kwargs):
            raise AssertionError("continuation must not inspect FIM tokens")

    ContextPromptEncoder(Missing(), ContextPromptConfig())


@pytest.mark.parametrize(
    "kwargs", [{"mode": "auto"}, {"max_after_tokens": -1}, {"continuation_reserve_tokens": 15}]
)
def test_invalid_configuration_fails_closed(kwargs):
    with pytest.raises(ValueError):
        ContextPromptConfig(**kwargs)


def test_tiny_fim_window_rejected_before_model_evaluation():
    with pytest.raises(ValueError, match="FIM context window"):
        encoder().encode("", "", max_before_tokens=5, n_ctx=19)


def test_native_bos_policy_is_applied_once_and_counted_in_budget():
    class WithBos(Tokenizer):
        def add_bos_token(self):
            return True

    prompt = ContextPromptEncoder(WithBos(), ContextPromptConfig(mode="fim"))
    assert prompt.encode("", "", max_before_tokens=8, n_ctx=21) == (1, 200, 201, 202)
    assert prompt.encode("ab", "cd", max_before_tokens=8, n_ctx=24) == (
        1,
        200,
        97,
        98,
        201,
        99,
        100,
        202,
    )
    with pytest.raises(ValueError, match="FIM context window"):
        prompt.encode("", "", max_before_tokens=8, n_ctx=20)


@pytest.mark.parametrize("broken", ["attribute", "native_id", "capability"])
def test_marker_parsing_alone_cannot_prove_native_support(broken):
    class Broken(Tokenizer):
        token_get_attr = (
            None
            if broken == "capability"
            else lambda self, token: 1 if broken == "attribute" else 8
        )

        def token_suffix(self):
            return 202 if broken == "native_id" else 201

    with pytest.raises(ValueError, match="native FIM"):
        ContextPromptEncoder(Broken(), ContextPromptConfig(mode="fim"))


def test_candidate_reserve_tracks_the_actual_search_contract():
    from neural_weasel.context_prompt import MIN_CONTINUATION_RESERVE_TOKENS
    from neural_weasel.neural_candidates import MAX_MODEL_TOKENS

    assert MIN_CONTINUATION_RESERVE_TOKENS >= MAX_MODEL_TOKENS
