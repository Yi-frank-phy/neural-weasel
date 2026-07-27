from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .candidate import Candidate
from .unified import ContextScriptPolicy, Script, contains_han, detect_script


@dataclass(frozen=True, slots=True)
class ReplayCase:
    id: str
    style: str
    context: str
    input: str
    expected: str
    requested_epoch: int


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    candidates: Sequence[Candidate]
    snapshot_age_ms: float
    used_epoch: int


@dataclass(frozen=True, slots=True)
class ReplayReport:
    case_count: int
    top_1_accuracy: float
    top_5_recall: float
    literal_fallback_rate: float
    wrong_script_candidate_count: int
    snapshot_age_ms_at_query: tuple[float, ...]
    candidate_query_ms_p50: float
    candidate_query_ms_p95: float
    candidate_query_ms_p99: float
    model_refresh_ms_p50: float
    model_refresh_ms_p95: float
    english_context_han_candidate_count: int
    chinese_context_latin_expected_found: bool
    stale_snapshot_query_count: int
    stale_snapshot_error_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_replay_cases(path: Path) -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        cases.append(ReplayCase(**payload))
    return cases


def _percentile(measurements: Sequence[float], percentile: float) -> float:
    if not measurements:
        raise ValueError("at least one measured duration is required")
    return float(np.percentile(np.asarray(measurements, dtype=np.float64), percentile))


def run_replay(
    cases: Sequence[ReplayCase],
    query: Callable[[ReplayCase], ReplayObservation],
    *,
    model_refresh_measurements_ms: Sequence[float],
) -> ReplayReport:
    if not model_refresh_measurements_ms:
        raise ValueError("real model refresh measurements are required")

    query_measurements: list[float] = []
    observations: list[tuple[ReplayCase, ReplayObservation]] = []
    for case in cases:
        started = time.perf_counter()
        observation = query(case)
        query_measurements.append((time.perf_counter() - started) * 1000)
        if not isinstance(observation, ReplayObservation):
            raise TypeError("query must return ReplayObservation")
        observations.append((case, observation))

    case_count = len(observations)
    divisor = max(1, case_count)
    top_1_hits = 0
    top_5_hits = 0
    literal_top_1 = 0
    wrong_script = 0
    english_han = 0
    chinese_latin_expected = 0
    chinese_latin_found = 0
    stale_queries = 0
    stale_errors = 0
    snapshot_ages: list[float] = []
    policy = ContextScriptPolicy()

    for case, observation in observations:
        candidates = tuple(observation.candidates)
        top_texts = [candidate.text for candidate in candidates[:5]]
        top_1_correct = bool(top_texts) and top_texts[0] == case.expected
        top_1_hits += top_1_correct
        top_5_hits += case.expected in top_texts
        literal_top_1 += bool(candidates) and candidates[0].constraint_kind == "literal"
        snapshot_ages.append(float(observation.snapshot_age_ms))

        context_kind = policy.classify(case.context)
        if context_kind == "english":
            leaked = sum(contains_han(candidate.text) for candidate in candidates)
            english_han += leaked
            wrong_script += leaked
        if context_kind == "chinese" and detect_script(case.expected) == Script.LATIN:
            chinese_latin_expected += 1
            chinese_latin_found += case.expected in top_texts

        stale = observation.used_epoch < case.requested_epoch
        stale_queries += stale
        stale_errors += stale and not top_1_correct

    return ReplayReport(
        case_count=case_count,
        top_1_accuracy=top_1_hits / divisor,
        top_5_recall=top_5_hits / divisor,
        literal_fallback_rate=literal_top_1 / divisor,
        wrong_script_candidate_count=wrong_script,
        snapshot_age_ms_at_query=tuple(snapshot_ages),
        candidate_query_ms_p50=_percentile(query_measurements, 50),
        candidate_query_ms_p95=_percentile(query_measurements, 95),
        candidate_query_ms_p99=_percentile(query_measurements, 99),
        model_refresh_ms_p50=_percentile(model_refresh_measurements_ms, 50),
        model_refresh_ms_p95=_percentile(model_refresh_measurements_ms, 95),
        english_context_han_candidate_count=english_han,
        chinese_context_latin_expected_found=(
            chinese_latin_expected > 0 and chinese_latin_found == chinese_latin_expected
        ),
        stale_snapshot_query_count=stale_queries,
        stale_snapshot_error_count=stale_errors,
    )
