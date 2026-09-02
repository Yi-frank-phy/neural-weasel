from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from neural_weasel.acquire_model import AcquiredGguf
from neural_weasel.gguf_artifact import PRODUCTION_GGUF
from neural_weasel.gpu import NvidiaGpu
from neural_weasel.llama_runtime import LlamaCppBackend


class FakeContext:
    def __init__(self, owner: FakeLlama) -> None:
        self.owner = owner

    def get_logits(self):
        return self.owner.last_logits

    def kv_cache_clear(self) -> None:
        self.owner.clear_calls += 1

    def capture_sequence_state(self, sequence_id: int) -> bytes:
        assert sequence_id == 0
        return f"root:{self.owner.n_tokens}".encode("ascii")

    def restore_sequence_state(self, payload: bytes, sequence_id: int) -> bool:
        assert sequence_id == 0
        self.owner.restored_states.append(bytes(payload))
        return True


class FakeLlama:
    def __init__(self, model_path: str, **kwargs: object) -> None:
        del model_path, kwargs
        self._pieces = [b"<bos>", "你".encode(), "好".encode(), b"n", b"<eos>"]
        self._ctx = FakeContext(self)
        self.last_logits = np.arange(5, dtype=np.float32)
        self.eval_calls: list[list[int]] = []
        self.reset_calls = 0
        self.clear_calls = 0
        self.restored_states: list[bytes] = []
        self.n_tokens = 0

    def n_vocab(self) -> int:
        return len(self._pieces)

    def token_bos(self) -> int:
        return 0

    def token_eos(self) -> int:
        return 4

    def detokenize(self, tokens: list[int], special: bool = False) -> bytes:
        del special
        return b"".join(self._pieces[token] for token in tokens)

    def tokenize(self, text: bytes, add_bos: bool = False, special: bool = False) -> list[int]:
        del add_bos, special
        if not text:
            return []
        if text == "你".encode():
            return [1]
        return [3]

    def reset(self) -> None:
        self.reset_calls += 1
        self.n_tokens = 0

    def eval(self, tokens: list[int]) -> None:
        self.eval_calls.append(list(tokens))
        self.n_tokens += len(tokens)
        self.last_logits = np.arange(5, dtype=np.float32) + len(tokens) * 10


@dataclass
class Probe:
    def before(self) -> NvidiaGpu:
        return NvidiaGpu(0, "NVIDIA GeForce RTX 4060 Laptop GPU", "GPU-test", 8192, 7600)

    def after(self) -> NvidiaGpu:
        return NvidiaGpu(0, "NVIDIA GeForce RTX 4060 Laptop GPU", "GPU-test", 8192, 3300)


def _backend(tmp_path: Path) -> LlamaCppBackend:
    model = tmp_path / PRODUCTION_GGUF.filename
    model.write_bytes(b"GGUF")
    acquired = AcquiredGguf(model, "a" * 64)
    probe = Probe()
    return LlamaCppBackend(
        acquired,
        llama_factory=FakeLlama,
        cuda_backend_probe=lambda: True,
        gpu_before_probe=probe.before,
        gpu_after_probe=probe.after,
    )


def test_context_free_continuation_replays_only_short_candidate_paths(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    before = len(backend.llama.eval_calls)

    scores = backend.continue_from_empty(
        [(1,), (1, 2)],
        [(2, 3), (3, 4)],
        deadline_ms=1000.0,
    )

    assert scores is not None
    assert backend.llama.eval_calls[before:] == [[0, 1], [0, 1, 2]]
    assert np.array_equal(scores[0], np.array([22.0, 23.0], dtype=np.float32))
    assert np.array_equal(scores[1], np.array([33.0, 34.0], dtype=np.float32))


def test_snapshot_root_restores_exact_context_before_candidate_branch(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    snapshot = backend.create_snapshot("你")
    root = snapshot.continuation_root
    assert root is not None
    assert root.n_tokens == 1

    before = len(backend.llama.eval_calls)
    scores = backend.continue_from_root(
        root,
        [(2,)],
        [(1, 3)],
        deadline_ms=1000.0,
    )

    assert scores is not None
    assert backend.llama.restored_states[-1] == root.state_bytes
    # The branch evaluates only its suffix from the saved editor root: no BOS or
    # editor-text replay is mixed into the candidate search path.
    assert backend.llama.eval_calls[before:] == [[2]]
    assert np.array_equal(scores[0], np.array([11.0, 13.0], dtype=np.float32))


def test_continuation_never_queues_past_model_lock_budget(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    snapshot = backend.create_snapshot("你")
    assert snapshot.continuation_root is not None
    assert backend._lock.acquire(blocking=False)
    try:
        started = time.perf_counter()
        scores = backend.continue_from_root(
            snapshot.continuation_root,
            [(1,)],
            [(2,)],
            deadline_ms=5.0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
    finally:
        backend._lock.release()

    assert scores is None
    assert elapsed_ms < 50.0


def test_candidate_branch_invalidates_editor_incremental_cache(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    snapshot = backend.create_snapshot("你")
    assert snapshot.continuation_root is not None
    assert backend._cached_token_ids == (1,)

    assert (
        backend.continue_from_root(
            snapshot.continuation_root,
            [(1,)],
            [(2,)],
            deadline_ms=1000.0,
        )
        is not None
    )

    assert backend._cached_token_ids is None
    assert backend._cached_logits is None
    before = len(backend.llama.eval_calls)
    backend.create_snapshot("你")
    assert backend.llama.eval_calls[before:] == [[1]]
