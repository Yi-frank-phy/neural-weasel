from __future__ import annotations

import contextlib
import threading
import time
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

    def ones(self, size, *, dtype, device: str) -> FakeTensor:
        assert size[0] == 1
        assert dtype is self.long
        assert device == "cuda:0"
        return FakeTensor([[1 for _ in range(size[1])]])


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
        self.fail_next_incremental = False

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        token_count = len(kwargs["input_ids"].data[0])
        cache = kwargs.get("past_key_values")
        if cache is None:
            cache = FakeCache(token_count)
        else:
            cache.length += token_count
            if self.fail_next_incremental:
                self.fail_next_incremental = False
                raise RuntimeError("injected incremental failure")
        return SimpleNamespace(logits=FakeLogits(), past_key_values=cache)


class FakeCache:
    def __init__(self, length: int) -> None:
        self.length = length

    def get_seq_length(self) -> int:
        return self.length


class BlockingFakeModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=2.0)
        return super().__call__(**kwargs)


def make_backend() -> QwenBaseBackend:
    backend = object.__new__(QwenBaseBackend)
    backend.torch = FakeTorch()
    backend.tokenizer = FakeTokenizer()
    backend.model = FakeModel()
    backend.max_before_tokens = 3
    backend.max_after_tokens = 2
    backend._lock = threading.Lock()
    backend._cache_state_lock = threading.Lock()
    backend._cache_generation = 0
    backend._context_cache = None
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


def test_snapshot_reuses_cache_only_for_exact_token_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()
    backend.max_before_tokens = 8

    backend.create_snapshot("a")
    first_cache = backend._context_cache.past_key_values
    backend.create_snapshot("ab")

    assert len(backend.model.calls) == 2
    assert backend.model.calls[1]["input_ids"].data == [[1]]
    assert backend.model.calls[1]["attention_mask"].data == [[1, 1]]
    assert backend.model.calls[1]["past_key_values"] is first_cache


def test_snapshot_reuses_logits_when_token_ids_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()

    first = backend.create_snapshot("abc", "x")
    second = backend.create_snapshot("xyz", "yz")

    assert len(backend.model.calls) == 1
    assert second.logits is first.logits
    assert second.latency_ms == 0.0
    assert second.after_text == "0,1"


def test_snapshot_fully_recomputes_after_tokenizer_resegmentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()
    backend.tokenizer.encode = lambda text, *, add_special_tokens: {
        "run": [7],
        "running": [8],
    }.get(text, [])

    backend.create_snapshot("run")
    backend.create_snapshot("running")

    assert len(backend.model.calls) == 2
    assert backend.model.calls[1]["input_ids"].data == [[8]]
    assert "past_key_values" not in backend.model.calls[1]


def test_snapshot_fully_recomputes_when_left_token_window_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()

    backend.create_snapshot("abc")
    backend.create_snapshot("abcd")

    assert len(backend.model.calls) == 2
    assert backend.model.calls[1]["input_ids"].data == [[1, 2, 3]]
    assert "past_key_values" not in backend.model.calls[1]


def test_incremental_failure_discards_mutated_cache_and_fully_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()
    backend.max_before_tokens = 8
    backend.create_snapshot("a")
    backend.model.fail_next_incremental = True

    backend.create_snapshot("ab")

    assert len(backend.model.calls) == 3
    assert backend.model.calls[1]["input_ids"].data == [[1]]
    assert "past_key_values" in backend.model.calls[1]
    assert backend.model.calls[2]["input_ids"].data == [[0, 1]]
    assert "past_key_values" not in backend.model.calls[2]
    assert backend._context_cache.past_key_values.get_seq_length() == 2


def test_cache_invalidation_does_not_wait_and_stale_forward_cannot_reinstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()
    backend.model = BlockingFakeModel()
    result: list[object] = []
    worker = threading.Thread(target=lambda: result.append(backend.create_snapshot("private")))
    worker.start()
    assert backend.model.started.wait(timeout=1.0)

    before = time.perf_counter()
    backend.invalidate_context_cache()
    elapsed = time.perf_counter() - before

    assert elapsed < 0.1
    backend.model.release.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert len(result) == 1
    assert backend._context_cache is None


class FakeHiddenVector:
    def detach(self):
        return self


class FakeLastHidden:
    def __init__(self, vector: FakeHiddenVector) -> None:
        self.vector = vector

    def __getitem__(self, key):
        assert key == (0, -1)
        return self.vector


class FakeBaseModel:
    def __init__(self, vector: FakeHiddenVector) -> None:
        self.vector = vector
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            last_hidden_state=FakeLastHidden(self.vector),
            past_key_values=FakeCache(len(kwargs["input_ids"].data[0])),
        )


class FakeCausalLMWithBase(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_vector = FakeHiddenVector()
        self.model = FakeBaseModel(self.hidden_vector)
        self.output_embedding = SimpleNamespace(weight=object(), bias=None)

    def get_output_embeddings(self):
        return self.output_embedding


def test_qwen_runtime_exposes_full_logits_backend_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT-MB-01/03: the legacy full snapshot remains the correctness runtime."""
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()

    result = backend.full_logits("context", "after")

    assert result.payload.tolist() == [0.25, 0.75]
    assert result.before_hash
    assert result.after_hash
    assert result.latency_ms >= 0
    assert backend.load() is None


def test_qwen_sparse_runtime_bypasses_full_vocabulary_lm_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT-MB-04: hidden snapshot calls the base model, not CausalLM.forward."""
    monkeypatch.setattr(model_module, "require_runtime_headroom", lambda torch: None)
    backend = make_backend()
    backend.model = FakeCausalLMWithBase()

    result = backend.continuation_hidden("context", "")

    assert result.payload is backend.model.hidden_vector
    assert len(backend.model.model.calls) == 1
    assert backend.model.calls == []
    assert backend.output_weight() is backend.model.output_embedding.weight


def test_qwen_runtime_private_invalidation_matches_backend_contract() -> None:
    """AT-MB-07: generic invalidation clears the Qwen context cache."""
    backend = make_backend()
    backend._context_cache = object()

    backend.invalidate_private_state()

    assert backend._context_cache is None
