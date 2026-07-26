from __future__ import annotations

import contextlib
import threading
from types import SimpleNamespace

import pytest

import neural_weasel.model as model_module
from neural_weasel.model import QwenBaseBackend


class FakeTensor:
    def __init__(self, data) -> None:
        self.data = data


class FakeLogits:
    def __getitem__(self, key):
        assert key == (0, -1)
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        import numpy as np

        return np.asarray([0.25, 0.75], dtype=np.float32)


class FakeCuda:
    def __init__(self) -> None:
        self.synchronizations = 0

    def synchronize(self, device: int) -> None:
        assert device == 0
        self.synchronizations += 1


class FakeTorch:
    long = object()

    def __init__(self) -> None:
        self.cuda = FakeCuda()
        self.tensors: list[FakeTensor] = []

    def inference_mode(self):
        return contextlib.nullcontext()

    def tensor(self, data, *, dtype, device: str) -> FakeTensor:
        assert dtype is self.long
        assert device == "cuda:0"
        tensor = FakeTensor(data)
        self.tensors.append(tensor)
        return tensor

    @staticmethod
    def ones_like(tensor: FakeTensor) -> FakeTensor:
        return FakeTensor([[1 for _ in tensor.data[0]]])


class FakeTokenizer:
    bos_token_id = 99
    eos_token_id = 100

    @staticmethod
    def encode(text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        return list(range(len(text)))

    @staticmethod
    def decode(
        token_ids: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert not skip_special_tokens
        assert not clean_up_tokenization_spaces
        return ",".join(str(token_id) for token_id in token_ids)


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(logits=FakeLogits())


def make_backend() -> QwenBaseBackend:
    backend = object.__new__(QwenBaseBackend)
    backend.torch = FakeTorch()
    backend.tokenizer = FakeTokenizer()
    backend.model = FakeModel()
    backend.max_before_tokens = 3
    backend.max_after_tokens = 2
    backend._lock = threading.Lock()
    backend._epoch = 0
    return backend


def test_snapshot_keeps_recent_before_tokens_and_bounded_after_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()

    snapshot = backend.create_snapshot("123456", "abcd")

    assert backend.torch.tensors[0].data == [[3, 4, 5]]
    assert snapshot.after_text == "0,1"
    assert snapshot.logits.tolist() == [0.25, 0.75]
    assert not snapshot.logits.flags.writeable
    assert snapshot.epoch == 1
    assert backend.model.calls[0]["use_cache"] is True
    assert backend.model.calls[0]["logits_to_keep"] == 1
    assert backend.torch.cuda.synchronizations == 2


def test_snapshot_uses_base_token_for_empty_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()

    backend.create_snapshot("")

    assert backend.torch.tensors[0].data == [[99]]
