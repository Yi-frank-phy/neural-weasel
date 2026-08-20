from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any


class LlamaVocabAdapter:
    """Minimal tokenizer-like view over the exact llama.cpp vocabulary.

    Constraint/index code must score the same token ids that llama.cpp emits.
    This adapter deliberately never loads a separate Hugging Face tokenizer.
    """

    def __init__(self, llama: Any) -> None:
        self._llama = llama
        self._n_vocab = int(llama.n_vocab())
        self.all_special_ids = frozenset(self._discover_special_ids())
        self.fingerprint = self._fingerprint()

    def __len__(self) -> int:
        return self._n_vocab

    def _discover_special_ids(self) -> set[int]:
        values: set[int] = set()
        for name in (
            "token_bos",
            "token_eos",
            "token_eot",
            "token_sep",
            "token_cls",
            "token_pad",
            "token_mask",
        ):
            getter = getattr(self._llama, name, None)
            if not callable(getter):
                continue
            try:
                token_id = int(getter())
            except (TypeError, ValueError, RuntimeError):
                continue
            if 0 <= token_id < self._n_vocab:
                values.add(token_id)
        return values

    def token_bytes(self, token_id: int) -> bytes:
        if not 0 <= token_id < self._n_vocab:
            raise IndexError(f"token id out of range: {token_id}")
        return bytes(self._llama.detokenize([int(token_id)], special=True))

    def decode(
        self,
        token_ids: Iterable[int],
        *,
        skip_special_tokens: bool = False,
        clean_up_tokenization_spaces: bool = False,
    ) -> str:
        del clean_up_tokenization_spaces
        ids = [int(token_id) for token_id in token_ids]
        if skip_special_tokens:
            ids = [token_id for token_id in ids if token_id not in self.all_special_ids]
        raw = bytes(self._llama.detokenize(ids, special=not skip_special_tokens))
        return raw.decode("utf-8", errors="replace")

    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]:
        return [
            int(token_id)
            for token_id in self._llama.tokenize(
                text.encode("utf-8"),
                add_bos=add_special_tokens,
                special=False,
            )
        ]

    def get_vocab(self) -> dict[str, int]:
        # Preserve duplicate byte pieces by including the token id in the key;
        # callers needing identity should use ``fingerprint`` instead.
        return {
            f"{self.token_bytes(token_id).hex()}:{token_id}": token_id
            for token_id in range(self._n_vocab)
        }

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(b"neural-weasel-llama-vocab-v1\0")
        digest.update(str(self._n_vocab).encode("ascii"))
        digest.update(b"\0")
        for token_id in range(self._n_vocab):
            piece = self.token_bytes(token_id)
            digest.update(token_id.to_bytes(4, "little", signed=False))
            digest.update(len(piece).to_bytes(4, "little", signed=False))
            digest.update(piece)
        return digest.hexdigest()
