from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from neural_weasel.acquire_model import AcquiredGguf
from neural_weasel.backends import FullLogitsSnapshotBackend
from neural_weasel.context_prompt import ContextPromptConfig
from neural_weasel.gpu import NvidiaGpu
from neural_weasel.internal_cli import _build_production, _parser
from neural_weasel.llama_runtime import LlamaCppBackend
from neural_weasel.production_pipe import _safe_runtime_metric


class Context:
    def __init__(self, owner):
        self.owner = owner

    def get_logits(self):
        return self.owner.logits

    def capture_sequence_state(self, sequence_id):
        return bytes(self.owner.tokens)

    def restore_sequence_state(self, payload, sequence_id):
        self.owner.tokens = list(payload)
        return True

    def kv_cache_clear(self):
        self.owner.tokens = []


class Llama:
    pieces = (
        b"<bos>",
        b"a",
        b"b",
        b"c",
        b"d",
        b"<eos>",
        b"<|fim_prefix|>",
        b"<|fim_suffix|>",
        b"<|fim_middle|>",
    )

    def __init__(self, **kwargs):
        self.tokens = []
        self.n_tokens = 0
        self.calls = []
        self._ctx = Context(self)
        self.logits = np.arange(len(self.pieces), dtype=np.float32)

    def n_vocab(self):
        return len(self.pieces)

    def token_bos(self):
        return 0

    def token_eos(self):
        return 5

    def token_get_attr(self, token):
        return 8 if token in (6, 7, 8) else 1

    def token_prefix(self):
        return 6

    def token_suffix(self):
        return 7

    def token_middle(self):
        return 8

    def add_bos_token(self):
        return False

    def tokenize(self, text, *, add_bos=False, special=False):
        if special and text in self.pieces[6:]:
            return [self.pieces.index(text)]
        return [
            self.pieces.index(bytes([c])) if bytes([c]) in self.pieces[1:5] else 1 for c in text
        ]

    def detokenize(self, ids, *, special=False):
        return b"".join(self.pieces[i] for i in ids)

    def reset(self):
        self.tokens = []
        self.n_tokens = 0

    def eval(self, ids):
        self.calls.append(tuple(ids))
        self.tokens.extend(ids)
        self.n_tokens += len(ids)
        self.logits = np.arange(self.n_vocab(), dtype=np.float32) + sum(self.tokens)


def runtime(tmp_path, *, mode="fim"):
    return LlamaCppBackend(
        AcquiredGguf(tmp_path / "fixture.gguf", "a" * 64),
        max_before_tokens=32,
        n_ctx=64,
        n_batch=8,
        prompt_config=ContextPromptConfig(mode=mode),
        llama_factory=Llama,
        cuda_backend_probe=lambda: True,
        gpu_before_probe=lambda: NvidiaGpu(0, "GPU", "id", 8192, 7600),
        gpu_after_probe=lambda: NvidiaGpu(0, "GPU", "id", 8192, 3300),
    )


def test_suffix_refreshes_logits_root_and_cache_identity(tmp_path):
    model = runtime(tmp_path)
    first = model.create_snapshot("ab", "c")
    assert first.continuation_root.replay_token_ids == (6, 1, 2, 7, 3, 8)
    assert first.continuation_root.n_tokens == 6
    calls = len(model.llama.calls)
    same = model.create_snapshot("ab", "c")
    assert len(model.llama.calls) == calls
    assert np.array_equal(first.logits, same.logits)
    second = model.create_snapshot("ab", "d")
    assert second.continuation_root.replay_token_ids == (6, 1, 2, 7, 4, 8)
    assert not np.array_equal(first.logits, second.logits)
    assert first.before_hash == second.before_hash
    assert first.after_hash != second.after_hash


def test_constrained_score_reads_snapshot_and_continuation_replays_fim_root(tmp_path):
    model = runtime(tmp_path)
    backend = FullLogitsSnapshotBackend(model)
    state = backend.update_context("ab", "cd")
    calls = len(model.llama.calls)
    assert np.array_equal(backend.score_allowed_tokens(state, [1, 2]), state.payload[[1, 2]])
    assert len(model.llama.calls) == calls
    result = model.continue_from_root(state.continuation_root, [(1,)], [(2,)], deadline_ms=1000)
    assert result is not None
    assert model.llama.calls[calls] == (1,)
    # A subsequent refresh rebuilds/captures the same root, never the branch.
    again = model.create_snapshot("ab", "cd")
    assert again.continuation_root.replay_token_ids == state.continuation_root.replay_token_ids


def test_invalidation_wipes_fim_and_diagnostics_never_expose_raw_context(tmp_path):
    model = runtime(tmp_path)
    backend = FullLogitsSnapshotBackend(model)
    state = backend.update_context("secret-before", "secret-after")
    encoded = json.dumps(model.performance_diagnostics())
    assert "secret" not in encoded and "replay" not in encoded and "hash" not in encoded
    assert model.performance_diagnostics()["context_mode"] == "fim"
    backend.invalidate_private_state()
    assert model._cached_token_ids is None
    assert model._cached_logits is None
    assert model._continuation_state_token_ids is None
    assert model.llama.tokens == []
    with pytest.raises(RuntimeError, match="stale state"):
        backend.score_allowed_tokens(state, [1])


def test_cold_native_branch_replays_both_sides_and_preserves_root_cache(tmp_path, monkeypatch):
    model = runtime(tmp_path)
    snapshot = model.create_snapshot("ab", "cd")
    root = snapshot.continuation_root
    model.llama._ctx.ctx = object()
    restores = []
    low_level = SimpleNamespace(
        llama_state_seq_get_size=lambda *args: 4,
        llama_state_seq_get_data=lambda ctx, buffer, size, seq: size,
        llama_state_seq_set_data=lambda ctx, buffer, size, seq: restores.append(seq) or size,
    )
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(llama_cpp=low_level))
    start = len(model.llama.calls)
    assert model.continue_from_root(root, [(1,), (2,)], [(3,), (4,)], deadline_ms=1000) is not None
    assert model.llama.calls[start:] == [(6, 1, 2, 7, 3, 4, 8), (1,), (2,)]
    assert len(restores) == 2
    assert model._continuation_state_token_ids == root.replay_token_ids
    assert model._cached_token_ids == root.replay_token_ids
    assert np.array_equal(model.create_snapshot("ab", "cd").logits, snapshot.logits)


def test_continuation_default_still_ignores_suffix_for_model_input(tmp_path):
    model = runtime(tmp_path, mode="continuation")
    model.create_snapshot("ab", "c")
    calls = len(model.llama.calls)
    snapshot = model.create_snapshot("ab", "d")
    assert len(model.llama.calls) == calls
    assert snapshot.continuation_root.replay_token_ids == (1, 2)


@pytest.mark.parametrize("command", ["serve", "serve-http", "predict", "simulate", "benchmark"])
def test_cli_fim_is_explicit_and_default_remains_continuation(command):
    required = [] if command.startswith("serve") else ["--before", "a"]
    if command in {"predict", "benchmark"}:
        required.extend(["--pinyin", "a"])
    assert _parser().parse_args([command, *required]).context_mode == "continuation"
    assert _parser().parse_args([command, *required, "--context-mode", "fim"]).context_mode == "fim"


def test_cli_forwards_fim_into_production_runtime(monkeypatch):
    captured = {}

    def build(index_path, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("neural_weasel.production.build_production_runtime", build)
    _build_production(None, context_mode="fim")
    assert captured["runtime_config"].prompt_config.mode == "fim"


def test_pipe_context_mode_enum_cannot_echo_untrusted_editor_text():
    assert _safe_runtime_metric("context_mode", "fim") == "fim"
    with pytest.raises(RuntimeError, match="invalid cached runtime enum"):
        _safe_runtime_metric("context_mode", "private editor text")
