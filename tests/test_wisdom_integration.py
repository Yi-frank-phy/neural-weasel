from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.candidate import Candidate
from neural_weasel.http_server import (
    FIRST_PAGE_CANDIDATES,
    WisdomHttpServer,
    _encode_bridge_candidates,
    _validate_request,
)
from neural_weasel.model import QwenBaseBackend
from neural_weasel.pinyin import parse_raw_pinyin
from neural_weasel.unified import (
    Constraint,
    UnifiedConstraintEngine,
    _build_fuzzy_aliases,
    _covers_current_pinyin,
    _pinyin_query_variants,
)


@dataclass
class FakeRuntime:
    logits: np.ndarray

    def load(self) -> None:
        pass

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(self.logits, before, after, 0.0)

    def diagnostics(self) -> dict[str, object]:
        return {}

    def invalidate_private_state(self) -> None:
        pass


@dataclass(frozen=True)
class FakePronunciation:
    pinyin: str
    syllable_path: tuple[str, ...]

    @property
    def boundaries(self) -> frozenset[int]:
        total = 0
        result = set()
        for syllable in self.syllable_path:
            total += len(syllable)
            result.add(total)
        return frozenset(result)

    @property
    def syllables(self) -> int:
        return len(self.syllable_path)

    def matched_syllables(self, raw_length: int) -> int:
        return sum(boundary <= raw_length for boundary in self.boundaries)


def test_final_neural_candidate_must_follow_all_current_pinyin() -> None:
    exact = FakePronunciation("shenjing", ("shen", "jing"))
    short = FakePronunciation("shen", ("shen",))
    extra_word = FakePronunciation("shenjingwangluo", ("shen", "jing", "wang", "luo"))

    assert _covers_current_pinyin(parse_raw_pinyin("shenjing"), exact)
    assert not _covers_current_pinyin(parse_raw_pinyin("shenjing"), short)
    assert _covers_current_pinyin(parse_raw_pinyin("shenjing"), extra_word)
    assert _covers_current_pinyin(parse_raw_pinyin("shenj"), exact)
    assert _covers_current_pinyin(parse_raw_pinyin("shen"), exact)


def test_standard_fuzzy_pinyin_variants_are_bounded_and_explicit() -> None:
    aliases = _build_fuzzy_aliases(("shen", "sen", "jing", "jin", "lan", "lang"))
    variants = {
        parsed.compact: cost
        for parsed, cost in _pinyin_query_variants(parse_raw_pinyin("senjin"), aliases)
    }

    assert variants["senjin"] == 0
    assert variants["shenjing"] == 2
    assert len(variants) <= 24


class MixedPinyinConstraint(Constraint):
    def candidates(self, raw_keys, *, backend, state, after_text):
        del raw_keys, backend, after_text
        return [
            Candidate(
                "神经",
                "shen'jing",
                8,
                1.0,
                state.epoch,
                True,
                True,
                2,
                model_score=1.0,
            ),
            Candidate(
                "neural",
                "",
                8,
                100.0,
                state.epoch,
                True,
                True,
                1,
                script="latin",
                model_score=100.0,
            ),
        ]


class NegativeLogitConstraint(Constraint):
    def candidates(self, raw_keys, *, backend, state, after_text):
        del raw_keys, backend, after_text
        return [
            Candidate(
                "神经",
                "shen'jing",
                8,
                -7.0,
                state.epoch,
                False,
                True,
                2,
                script="han",
                model_score=-7.0,
                constraint_cost=0.0,
            ),
            Candidate(
                "覆盖字",
                "shen'jing",
                8,
                None,
                state.epoch,
                True,
                True,
                2,
                script="han",
                model_score=None,
                constraint_cost=0.0,
            ),
        ]


def test_unified_neural_query_keeps_chinese_and_english_candidates() -> None:
    backend = FullLogitsSnapshotBackend(FakeRuntime(np.zeros(8, dtype=np.float32)))
    state = backend.update_context("The surrounding context is English", "")
    engine = UnifiedConstraintEngine(backend=backend, pinyin_constraint=MixedPinyinConstraint())

    candidates = engine.query(
        "The surrounding context is English",
        "shenjing",
        state=state,
        limit=9,
    )

    texts = {candidate.text for candidate in candidates}
    assert {"神经", "neural", "shenjing"} <= texts


def test_literal_english_is_last_and_never_crowds_model_scored_first_page() -> None:
    backend = FullLogitsSnapshotBackend(FakeRuntime(np.zeros(8, dtype=np.float32)))
    state = backend.update_context("中文上下文", "")
    engine = UnifiedConstraintEngine(backend=backend, pinyin_constraint=NegativeLogitConstraint())

    candidates = engine.query("中文上下文", "shenjing", state=state, limit=2)

    assert [candidate.text for candidate in candidates] == ["神经", "shenjing"]


def test_http_request_candidate_count_defaults_to_first_page_and_caps_at_fifty() -> None:
    _, _, default_count = _validate_request({"prompt": "", "pinyin_constraints": ["shen", "jing"]})
    assert default_count == FIRST_PAGE_CANDIDATES
    assert (
        _validate_request(
            {"prompt": "", "pinyin_constraints": ["shenjing"], "candidate_count": 50}
        )[2]
        == 50
    )

    for invalid in (0, 51, True, "5"):
        try:
            _validate_request(
                {
                    "prompt": "",
                    "pinyin_constraints": ["shenjing"],
                    "candidate_count": invalid,
                }
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"candidate_count {invalid!r} should be rejected")


def test_file_bridge_preserves_partial_candidate_consumption() -> None:
    candidates = [Candidate("神", "shen", 4, 1.0, 1, False, False, 1)]

    assert _encode_bridge_candidates(candidates) == "4\t神"


class AsyncFakeEngine:
    def __init__(self) -> None:
        self.requested_epoch = 2
        self.query_epochs: list[int] = []
        self.query_limits: list[int] = []

    def request_context_update(self, prompt: str, after: str) -> int:
        del prompt, after
        return self.requested_epoch

    def has_snapshot(self, epoch: int) -> bool:
        return False

    def wait_for_epoch(self, epoch: int, timeout_seconds: float) -> bool:
        assert epoch == self.requested_epoch
        assert timeout_seconds > 0
        return False

    def query(self, raw_keys: str, limit: int, context_epoch: int):
        del raw_keys
        self.query_epochs.append(context_epoch)
        self.query_limits.append(limit)
        return [Candidate("神经", "shen'jing", 8, 1.0, 1, True, True, 2)]


def test_context_prefill_uses_latest_completed_snapshot_as_nonblocking_fallback() -> None:
    engine = AsyncFakeEngine()
    server = WisdomHttpServer(("127.0.0.1", 0), engine)
    try:
        response, exact = server.generate("new context", "shenjing")

        assert response == "神经"
        assert exact is False
        assert engine.query_epochs == [0]
        assert engine.query_limits == [FIRST_PAGE_CANDIDATES]
        assert server.stats()["stale_fallback_count"] == 1
    finally:
        server.server_close()


class FakeCache:
    def __init__(self, length: int) -> None:
        self.length = length

    def get_seq_length(self) -> int:
        return self.length


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
        return np.asarray([0.25, 0.75], dtype=np.float32)


class FakeTorch:
    long = object()

    class Cuda:
        @staticmethod
        def synchronize(device: int) -> None:
            assert device == 0

    cuda = Cuda()

    @staticmethod
    def inference_mode():
        return contextlib.nullcontext()

    @staticmethod
    def tensor(data, *, dtype, device):
        del dtype, device
        return FakeTensor(data)

    @staticmethod
    def ones(size, *, dtype, device):
        del dtype, device
        return FakeTensor([[1] * size[1]])


class FakeTokenizer:
    bos_token_id = 99
    eos_token_id = 100

    @staticmethod
    def encode(text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        return list(range(len(text)))

    @staticmethod
    def decode(token_ids, *, skip_special_tokens, clean_up_tokenization_spaces):
        del skip_special_tokens, clean_up_tokenization_spaces
        return ",".join(str(token_id) for token_id in token_ids)


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        count = len(kwargs["input_ids"].data[0])
        cache = kwargs.get("past_key_values") or FakeCache(0)
        cache.length += count
        return SimpleNamespace(logits=FakeLogits(), past_key_values=cache)


def make_qwen_backend() -> QwenBaseBackend:
    backend = object.__new__(QwenBaseBackend)
    backend.torch = FakeTorch()
    backend.tokenizer = FakeTokenizer()
    backend.model = FakeModel()
    backend.max_before_tokens = 8
    backend.max_after_tokens = 2
    backend._lock = threading.Lock()
    backend._cache_state_lock = threading.Lock()
    backend._cache_generation = 0
    backend._context_cache = None
    backend._epoch = 0
    backend.target_gpu = SimpleNamespace(name="fixture", uuid="fixture")
    return backend


def test_qwen_cache_reuses_identical_logits_and_only_forwards_appended_tokens(
    monkeypatch,
) -> None:
    monkeypatch.setattr("neural_weasel.model.require_runtime_headroom", lambda torch: None)
    backend = make_qwen_backend()

    first = backend.create_snapshot("a")
    same = backend.create_snapshot("a")
    backend.create_snapshot("ab")

    assert same.logits is first.logits
    assert same.latency_ms == 0.0
    assert len(backend.model.calls) == 2
    assert backend.model.calls[1]["input_ids"].data == [[1]]
    assert backend.model.calls[1]["past_key_values"].get_seq_length() == 2


def test_qwen_cache_invalidation_prevents_reuse_of_private_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("neural_weasel.model.require_runtime_headroom", lambda torch: None)
    backend = make_qwen_backend()
    backend.create_snapshot("private")

    backend.invalidate_private_state()

    assert backend._context_cache is None
    assert backend._cache_generation == 1
