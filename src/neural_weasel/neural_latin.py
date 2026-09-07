from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .unified import LatinCompletion, LatinPrefixConstraint, contains_han

_LATIN_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9.'-]*$")


class NeuralLatinPrefixConstraint(LatinPrefixConstraint):
    """Model-vocabulary Latin graph with explicit word-internal token edges.

    ``LatinPrefixConstraint`` historically stripped tokenizer word-boundary
    whitespace because it only needed one-token display completions. Neural
    multi-token search must retain that distinction: a token decoded as
    ``" metry"`` is a legal word-start token but must never be appended to an
    existing ``"asym"`` path as if it decoded to ``"metry"``.

    ``continuation_fragments`` therefore contains only vocabulary tokens whose
    *exact* one-token decode is already a word-internal Latin fragment (no
    leading/trailing whitespace). Root completions remain the user-visible,
    left-trimmed forms so ordinary word-start tokens are still available on
    page 0.
    """

    def __init__(
        self,
        completions: Sequence[LatinCompletion],
        *,
        continuation_fragments: Mapping[int, str] | None = None,
    ) -> None:
        super().__init__(completions)
        if continuation_fragments is None:
            # Explicit/manual fixtures are treated as exact token decodes. The
            # production tokenizer constructor below supplies boundary-aware
            # metadata instead.
            continuation_fragments = {
                int(completion.token_path[0]): completion.text
                for completion in self.completions
                if len(completion.token_path) == 1
                and completion.token_path
                and _LATIN_TOKEN.fullmatch(completion.text) is not None
                and not contains_han(completion.text)
            }
        self.continuation_fragments = {
            int(token_id): fragment
            for token_id, fragment in continuation_fragments.items()
            if fragment
            and _LATIN_TOKEN.fullmatch(fragment) is not None
            and not contains_han(fragment)
        }

    @classmethod
    def from_tokenizer(cls, tokenizer) -> NeuralLatinPrefixConstraint:
        special_ids = set(getattr(tokenizer, "all_special_ids", ()))
        completions: list[LatinCompletion] = []
        continuation_fragments: dict[int, str] = {}
        seen: set[tuple[str, int]] = set()
        for token_id in range(len(tokenizer)):
            if token_id in special_ids:
                continue
            decoded = tokenizer.decode(
                [token_id],
                skip_special_tokens=False,
                clean_up_tokenization_spaces=False,
            )
            root_text = decoded.lstrip()
            if (
                not root_text
                or len(root_text) > 48
                or _LATIN_TOKEN.fullmatch(root_text) is None
                or contains_han(root_text)
            ):
                continue
            key = (root_text, token_id)
            if key in seen:
                continue
            seen.add(key)
            completions.append(LatinCompletion(text=root_text, token_path=(token_id,)))

            # Word-internal continuation must preserve the tokenizer's exact
            # decode. Never erase a leading/trailing word boundary here.
            if decoded == root_text and _LATIN_TOKEN.fullmatch(decoded) is not None:
                continuation_fragments[token_id] = decoded

        return cls(
            completions,
            continuation_fragments=continuation_fragments,
        )
