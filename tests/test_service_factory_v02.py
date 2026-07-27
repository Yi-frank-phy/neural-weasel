from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from neural_weasel.backends import RuntimeSnapshot
from neural_weasel.bilingual_engine import BilingualImeEngine
from neural_weasel.internal_cli import _parser
from neural_weasel.service_factory import build_bilingual_engine


class FakeTokenizer:
    all_special_ids = []

    def __len__(self) -> int:
        return 2

    def decode(self, token_ids, **kwargs) -> str:
        return {0: " asymmetric", 1: " Qwen"}[token_ids[0]]


class FakeRuntime:
    tokenizer = FakeTokenizer()

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(np.asarray([2.0, 1.0], dtype=np.float32), before, after, 1.0)

    def continuation_hidden(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(torch.tensor([1.0, 2.0]), before, after, 1.0)

    def output_weight(self):
        return torch.tensor([[2.0, 0.0], [0.0, 0.5]])

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


class EmptyPinyinIndex:
    def query_plan(self, parsed):
        return SimpleNamespace(groups=())


@pytest.mark.parametrize(
    ("backend_kind", "expected"),
    [("full", "full_logits"), ("sparse", "sparse_projection")],
)
def test_factory_builds_real_unified_engine_for_each_backend(
    backend_kind: str,
    expected: str,
) -> None:
    """AT-MB-01/04: service selection changes backend, not constraint engine."""
    engine = build_bilingual_engine(
        runtime=FakeRuntime(),
        index=EmptyPinyinIndex(),
        backend_kind=backend_kind,
    )

    assert isinstance(engine, BilingualImeEngine)
    state = engine.update_context("The receiver-centred placement is operationally")
    assert state.backend_kind == expected
    assert any(candidate.text == "asymmetric" for candidate in engine.query("asy"))


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="backend"):
        build_bilingual_engine(
            runtime=FakeRuntime(),
            index=EmptyPinyinIndex(),
            backend_kind="magic",
        )


def test_serve_cli_exposes_explicit_backend_selection() -> None:
    """The PowerShell service entry can select the measured backend."""
    parser = _parser()

    full = parser.parse_args(["serve", "--backend", "full"])
    sparse = parser.parse_args(["serve", "--backend", "sparse"])

    assert full.backend == "full"
    assert sparse.backend == "sparse"


def test_backend_benchmark_cli_is_directly_runnable() -> None:
    parser = _parser()

    args = parser.parse_args(
        [
            "benchmark-backends",
            "--before",
            "The protocol is",
            "--allowed-counts",
            "8",
            "32",
        ]
    )

    assert args.command == "benchmark-backends"
    assert args.allowed_counts == [8, 32]
