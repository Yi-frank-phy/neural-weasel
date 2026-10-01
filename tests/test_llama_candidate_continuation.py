from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from neural_weasel import llama_runtime
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


def test_sequence_state_copy_uses_one_native_buffer_copy(monkeypatch) -> None:
    buffer = (ctypes.c_uint8 * 4)(1, 2, 3, 4)
    calls: list[int] = []
    original = ctypes.string_at

    def capture(pointer: object, size: int) -> bytes:
        calls.append(size)
        return original(pointer, size)

    monkeypatch.setattr(llama_runtime.ctypes, "string_at", capture)

    assert llama_runtime._copy_sequence_state_bytes(buffer, 4) == b"\x01\x02\x03\x04"
    assert calls == [4]


def test_production_snapshot_skips_unused_sequence_state_copy(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.llama._ctx.ctx = object()

    def fail_capture(sequence_id: int) -> bytes:
        del sequence_id
        raise AssertionError("production replay must not copy sequence state")

    backend.llama._ctx.capture_sequence_state = fail_capture
    snapshot = backend.create_snapshot("你")

    assert snapshot.continuation_root is not None
    assert snapshot.continuation_root.state_bytes == b""
    assert snapshot.continuation_root.replay_token_ids == (1,)


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


def test_candidate_continuation_warmup_covers_common_path_depths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("").continuation_root
    assert root is not None
    calls: list[tuple[object, tuple[tuple[int, ...], ...], tuple[object, ...], float]] = []

    def observe(root_arg, token_paths, allowed_token_sets, *, deadline_ms: float):
        calls.append(
            (
                root_arg,
                tuple(tuple(path) for path in token_paths),
                tuple(allowed_token_sets),
                deadline_ms,
            )
        )
        return [np.zeros(1, dtype=np.float32) for _ in token_paths]

    monkeypatch.setattr(backend, "continue_from_root", observe)

    assert backend.warm_candidate_continuation(root, deadline_ms=4_000.0) is True
    assert len(calls) == 1
    root_arg, paths, allowed_sets, deadline_ms = calls[0]
    assert root_arg is root
    fallback = backend._fallback_token()
    expected_paths = tuple(
        (fallback,) * path_length
        for path_length in range(1, llama_runtime.CANDIDATE_WARMUP_MAX_PATH_TOKENS + 1)
        for _ in range(llama_runtime.DEFAULT_PARALLEL_SEQUENCES)
    )
    assert paths == expected_paths
    assert allowed_sets == ((fallback,),) * len(expected_paths)
    assert len({id(allowed) for allowed in allowed_sets}) == 1
    assert deadline_ms == 4_000.0


def test_shared_allowed_vocabulary_is_materialized_once_per_batch(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("你").continuation_root
    assert root is not None

    class Vocabulary(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    vocabulary = Vocabulary([1, 3])
    scores = backend.continue_from_root(
        root,
        [(2,)] * 8,
        [vocabulary] * 8,
        deadline_ms=1000.0,
    )
    assert scores is not None and len(scores) == 8
    assert all(np.array_equal(scores[0], item) for item in scores)
    assert vocabulary.iterations == 1


def test_snapshot_root_restores_exact_context_before_candidate_branch(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    snapshot = backend.create_snapshot("你")
    root = snapshot.continuation_root
    assert root is not None
    assert root.n_tokens == 1
    assert root.replay_token_ids == (1,)

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


def test_production_context_replays_root_once_and_restores_candidate_branches(
    tmp_path: Path, monkeypatch
) -> None:
    backend = _backend(tmp_path)
    snapshot = backend.create_snapshot("你")
    root = snapshot.continuation_root
    assert root is not None
    backend.llama._ctx.ctx = object()
    restores: list[tuple[object, int, int]] = []
    low_level = SimpleNamespace(
        llama_state_seq_get_size=lambda raw_context, seq_id: 4,
        llama_state_seq_get_data=lambda raw_context, buffer, size, seq_id: size,
        llama_state_seq_set_data=lambda raw_context, buffer, size, seq_id: (
            restores.append((raw_context, size, seq_id)) or size
        ),
    )
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(llama_cpp=low_level))
    restored_before = list(backend.llama.restored_states)
    eval_before = len(backend.llama.eval_calls)

    scores = backend.continue_from_root(
        root,
        [(1,), (2,), (3,)],
        [(0,), (0,), (0,)],
        deadline_ms=1000.0,
    )

    assert scores is not None
    assert len(scores) == 3
    assert backend.llama.eval_calls[eval_before:] == [[1], [1], [2], [3]]
    # Two branch-to-branch restores plus the final editor-root restore.
    assert restores == [(backend.llama._ctx.ctx, 4, 0)] * 3
    assert backend.llama.restored_states == restored_before


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


def test_context_refresh_preempts_remaining_candidate_branches(tmp_path: Path, monkeypatch) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("你").continuation_root
    assert root is not None
    backend.llama._ctx.ctx = object()
    restores: list[int] = []
    low_level = SimpleNamespace(
        llama_state_seq_get_size=lambda raw_context, seq_id: 4,
        llama_state_seq_get_data=lambda raw_context, buffer, size, seq_id: size,
        llama_state_seq_set_data=lambda raw_context, buffer, size, seq_id: (
            restores.append(size) or size
        ),
    )
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(llama_cpp=low_level))
    with backend._context_waiters_lock:
        backend._context_waiters = 1
    eval_before = len(backend.llama.eval_calls)

    try:
        scores = backend.continue_from_root(
            root,
            [(1,), (2,), (3,)],
            [(0,), (0,), (0,)],
            deadline_ms=1000.0,
        )
    finally:
        with backend._context_waiters_lock:
            backend._context_waiters = 0

    assert scores is None
    assert backend.llama.eval_calls[eval_before:] == [[1], [1]]
    # Preemption still restores the editor root before releasing the model lock.
    assert restores == [4]
    diagnostics = backend.performance_diagnostics()
    assert diagnostics["last_continuation_requested_branches"] == 3
    assert diagnostics["last_continuation_completed_branches"] == 1
    assert diagnostics["last_continuation_returned_tokens"] == 1
    assert diagnostics["last_continuation_cache_preserved"] is True
    assert diagnostics["last_continuation_outcome"] == "preempted"


def test_production_continuation_reuses_cached_root_state_across_batches(
    tmp_path: Path, monkeypatch
) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("你").continuation_root
    assert root is not None
    backend.llama._ctx.ctx = object()
    captures: list[int] = []
    restores: list[int] = []
    low_level = SimpleNamespace(
        llama_state_seq_get_size=lambda raw_context, seq_id: 4,
        llama_state_seq_get_data=lambda raw_context, buffer, size, seq_id: (
            captures.append(size) or size
        ),
        llama_state_seq_set_data=lambda raw_context, buffer, size, seq_id: (
            restores.append(size) or size
        ),
    )
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(llama_cpp=low_level))

    first = backend.continue_from_root(root, [(1,)], [(0,)], deadline_ms=1000.0)
    eval_after_first = len(backend.llama.eval_calls)
    second = backend.continue_from_root(root, [(2,), (3,)], [(0,), (0,)], deadline_ms=1000.0)

    assert first is not None and second is not None
    assert captures == [4]
    assert backend.llama.eval_calls[eval_after_first:] == [[2], [3]]
    # Each batch restores the editor root on exit; the second also installs the
    # cached root at entry and restores once between its two branches.
    assert restores == [4, 4, 4, 4]


def test_private_state_invalidation_discards_cached_continuation_root(
    tmp_path: Path, monkeypatch
) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("你").continuation_root
    assert root is not None
    backend.llama._ctx.ctx = object()
    low_level = SimpleNamespace(
        llama_state_seq_get_size=lambda raw_context, seq_id: 4,
        llama_state_seq_get_data=lambda raw_context, buffer, size, seq_id: size,
        llama_state_seq_set_data=lambda raw_context, buffer, size, seq_id: size,
    )
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(llama_cpp=low_level))
    assert backend.continue_from_root(root, [(1,)], [(0,)], deadline_ms=1000.0) is not None
    assert backend._continuation_state_buffer is not None

    backend.invalidate_private_state()

    assert backend._continuation_state_token_ids is None
    assert backend._continuation_state_buffer is None
    assert backend._continuation_state_size == 0


def test_candidate_branch_restores_matching_editor_incremental_cache(tmp_path: Path) -> None:
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

    assert backend._cached_token_ids == (1,)
    assert backend._cached_logits is not None
    diagnostics = backend.performance_diagnostics()
    assert diagnostics["last_continuation_requested_branches"] == 1
    assert diagnostics["last_continuation_completed_branches"] == 1
    assert diagnostics["last_continuation_returned_tokens"] == 1
    assert diagnostics["last_continuation_cache_preserved"] is True
    assert diagnostics["last_continuation_outcome"] == "completed"
    assert (
        diagnostics["last_continuation_elapsed_ms"]
        >= diagnostics["last_continuation_queue_wait_ms"]
    )
    before = len(backend.llama.eval_calls)
    backend.create_snapshot("你")
    assert backend.llama.eval_calls[before:] == []


def test_candidate_branch_from_stale_root_invalidates_editor_cache(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    stale_root = backend.create_snapshot("你").continuation_root
    assert stale_root is not None
    backend.create_snapshot("n")
    assert backend._cached_token_ids == (3,)

    assert (
        backend.continue_from_root(
            stale_root,
            [(1,)],
            [(2,)],
            deadline_ms=1000.0,
        )
        is not None
    )

    assert backend._cached_token_ids is None
    assert backend._cached_logits is None
    diagnostics = backend.performance_diagnostics()
    assert diagnostics["last_continuation_cache_preserved"] is False
    assert diagnostics["last_continuation_outcome"] == "completed"


def test_log_prob_continuation_normalizes_over_full_vocabulary(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    root = backend.create_snapshot("你").continuation_root
    assert root is not None

    scores = backend.continue_log_probs_from_root(
        root,
        [(2,)],
        [(1, 3)],
        deadline_ms=1000.0,
    )

    assert scores is not None
    full_logits = np.arange(5, dtype=np.float64) + 10.0
    expected = full_logits[[1, 3]] - np.log(np.exp(full_logits).sum())
    assert np.allclose(scores[0], expected.astype(np.float32))


def test_log_prob_selection_preserves_nonfinite_semantics() -> None:
    allowed = np.array([0, 1, 2], dtype=np.int64)
    positive_infinity = LlamaCppBackend._select_continuation_values(
        [np.inf, np.inf, -np.inf],
        allowed,
        normalize_log_probs=True,
    )
    assert np.array_equal(
        positive_infinity,
        np.array([-np.log(2.0), -np.log(2.0), -np.inf], dtype=np.float32),
    )
    all_nonfinite = LlamaCppBackend._select_continuation_values(
        [-np.inf, -np.inf, -np.inf],
        allowed,
        normalize_log_probs=True,
    )
    assert np.array_equal(all_nonfinite, np.full(3, -np.inf, dtype=np.float32))
    with np.testing.assert_raises_regex(ValueError, "must not contain NaN"):
        LlamaCppBackend._select_continuation_values(
            [0.0, np.nan, 1.0],
            allowed,
            normalize_log_probs=True,
        )
