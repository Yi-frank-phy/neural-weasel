from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FIM_MARKERS = (b"<|fim_prefix|>", b"<|fim_suffix|>", b"<|fim_middle|>")
MIN_CONTINUATION_RESERVE_TOKENS = 16
_CONTROL_TOKEN_ATTRIBUTE = 8


@dataclass(frozen=True, slots=True)
class ContextPromptConfig:
    """Explicit experimental prompt mode; continuation remains the release default."""

    mode: str = "continuation"
    max_after_tokens: int = 4096
    continuation_reserve_tokens: int = MIN_CONTINUATION_RESERVE_TOKENS

    def __post_init__(self) -> None:
        if self.mode not in {"continuation", "fim"}:
            raise ValueError("context mode must be continuation or fim")
        if self.max_after_tokens < 0:
            raise ValueError("max_after_tokens must be non-negative")
        if self.continuation_reserve_tokens < MIN_CONTINUATION_RESERVE_TOKENS:
            raise ValueError("continuation_reserve_tokens must be at least 16")

    def validate_window(self, n_ctx: int) -> None:
        if self.mode == "fim" and n_ctx <= len(FIM_MARKERS) + self.continuation_reserve_tokens:
            raise ValueError("FIM context window must fit markers, text and candidate reserve")


class ContextPromptEncoder:
    """Serialize native Qwen FIM tokens without interpreting controls in editor text.

    Only the three trusted markers use special-token parsing. Editor text uses
    the same literal tokenizer path as continuation. The native vocabulary
    decides whether one BOS is needed; no EOS is injected before fim_middle.
    """

    def __init__(self, llama: Any, config: ContextPromptConfig) -> None:
        self._llama = llama
        self.config = config
        self._markers: tuple[int, ...] = ()
        self._bos: tuple[int, ...] = ()
        if config.mode == "fim":
            native = getattr(llama, "_model", llama)
            attr = getattr(native, "token_get_attr", None)
            add_bos = getattr(native, "add_bos_token", None)
            if not callable(attr) or not callable(add_bos):
                raise ValueError("GGUF tokenizer does not expose native FIM capability")
            ids = []
            for marker, getter_name in zip(
                FIM_MARKERS, ("token_prefix", "token_suffix", "token_middle"), strict=True
            ):
                tokens = llama.tokenize(marker, add_bos=False, special=True)
                if len(tokens) != 1:
                    raise ValueError("GGUF tokenizer does not expose native FIM markers")
                token = int(tokens[0])
                getter = getattr(native, getter_name, None)
                if (
                    not 0 <= token < int(llama.n_vocab())
                    or token in ids
                    or not callable(getter)
                    or int(getter()) != token
                    or not int(attr(token)) & _CONTROL_TOKEN_ATTRIBUTE
                    or bytes(llama.detokenize([token], special=True)) != marker
                    # An ordinary vocabulary piece is not a native control.
                    or token in llama.tokenize(marker, add_bos=False, special=False)
                ):
                    raise ValueError("GGUF tokenizer does not expose distinct native FIM markers")
                ids.append(token)
            self._markers = tuple(ids)
            needs_bos = add_bos()
            if not isinstance(needs_bos, bool):
                raise ValueError("GGUF tokenizer does not expose a native BOS policy")
            if needs_bos:
                bos = int(native.token_bos())
                if not 0 <= bos < int(llama.n_vocab()) or bos in ids:
                    raise ValueError("GGUF tokenizer does not expose a valid native BOS")
                self._bos = (bos,)

    def encode(
        self, before: str, after: str, *, max_before_tokens: int, n_ctx: int
    ) -> tuple[int, ...]:
        if self.config.mode != "fim":
            raise ValueError("FIM encoder requires explicit fim mode")
        self.config.validate_window(n_ctx)
        if max_before_tokens < 0:
            raise ValueError("max_before_tokens must be non-negative")
        budget = (
            n_ctx - len(self._bos) - len(self._markers) - self.config.continuation_reserve_tokens
        )
        if budget < 1:
            raise ValueError("FIM context window must fit BOS, markers and candidate reserve")
        before_ids = self._llama.tokenize(before.encode("utf-8"), add_bos=False, special=False)
        after_ids = self._llama.tokenize(after.encode("utf-8"), add_bos=False, special=False)
        # Bound the right side while retaining at least half the text budget for
        # a nonempty left side. Spare left capacity is available to the suffix.
        left_floor = min(len(before_ids), max_before_tokens, budget // 2)
        after_count = min(len(after_ids), self.config.max_after_tokens, budget - left_floor)
        before_count = min(len(before_ids), max_before_tokens, budget - after_count)
        prefix, suffix, middle = self._markers
        return (
            *self._bos,
            prefix,
            *(int(token) for token in before_ids[-before_count:] if before_count),
            suffix,
            *(int(token) for token in after_ids[:after_count]),
            middle,
        )
