from __future__ import annotations

from neural_weasel.llama_vocab import LlamaVocabAdapter


class FakeLlama:
    def __init__(self) -> None:
        self._pieces = [b"<bos>", "你".encode(), b"hello", b"\xff", b"<eos>"]

    def n_vocab(self) -> int:
        return len(self._pieces)

    def token_bos(self) -> int:
        return 0

    def token_eos(self) -> int:
        return 4

    def detokenize(self, tokens: list[int], special: bool = False) -> bytes:
        if not special and tokens[0] in {0, 4}:
            return b""
        return b"".join(self._pieces[token] for token in tokens)

    def tokenize(self, text: bytes, add_bos: bool = True, special: bool = False) -> list[int]:
        mapping = {"你".encode(): [1], b"hello": [2]}
        result = list(mapping[text])
        return ([0] + result) if add_bos else result


def test_adapter_exposes_same_token_ids_used_by_llama_runtime() -> None:
    adapter = LlamaVocabAdapter(FakeLlama())

    assert len(adapter) == 5
    assert adapter.all_special_ids == frozenset({0, 4})
    assert adapter.decode([1]) == "你"
    assert adapter.encode("hello", add_special_tokens=False) == [2]


def test_vocab_fingerprint_is_stable_and_byte_safe() -> None:
    first = LlamaVocabAdapter(FakeLlama())
    second = LlamaVocabAdapter(FakeLlama())

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    # Invalid UTF-8 pieces remain fingerprintable even though display decoding
    # uses replacement characters for constraint code.
    assert first.token_bytes(3) == b"\xff"
