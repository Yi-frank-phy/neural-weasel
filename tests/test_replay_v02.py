from __future__ import annotations

import json
import time
from pathlib import Path

from neural_weasel.candidate import Candidate
from neural_weasel.replay import ReplayObservation, load_replay_cases, run_replay

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "replay_v02.jsonl"


def candidate(
    text: str,
    *,
    kind: str,
    script: str,
    epoch: int,
    score: float = 1.0,
) -> Candidate:
    return Candidate(
        text=text,
        pinyin="",
        consumed_keys=3,
        score=score,
        context_epoch=epoch,
        coverage=kind == "literal",
        completes_input=True,
        syllables=0,
        constraint_kind=kind,
        script=script,
        model_score=None if kind == "literal" else score,
        total_score=score,
        token_path=(),
    )


def test_replay_fixture_covers_required_styles() -> None:
    cases = load_replay_cases(FIXTURE)

    assert {case.style for case in cases} == {
        "english_theoretical_physics",
        "chinese_technical",
        "mixed_chinese_english",
        "model_name_acronym",
        "rapid_input",
    }


def test_replay_emits_measured_metrics_and_special_reports() -> None:
    """AT-RP-01..05."""
    cases = load_replay_cases(FIXTURE)
    responses = {
        "english-physics": ["asymmetric", "asymmetry", "asy"],
        "chinese-technical": ["纠缠", "就产", "jiuchan"],
        "mixed-model": ["Qwen", "Qwen3.5", "qwen"],
        "acronym": ["GPT-5.6", "gpt"],
        # This intentionally stale response is wrong at top-1.
        "rapid-stale": ["non", "nonlocal"],
    }

    def query(case):
        time.sleep(0.0002)
        used_epoch = case.requested_epoch - 1 if case.id == "rapid-stale" else case.requested_epoch
        items = [
            candidate(
                text,
                kind="literal" if text == case.input else "latin_prefix",
                script="han" if any("\u4e00" <= char <= "\u9fff" for char in text) else "latin",
                epoch=used_epoch,
                score=10.0 - index,
            )
            for index, text in enumerate(responses[case.id])
        ]
        return ReplayObservation(
            candidates=items,
            snapshot_age_ms=450.0 if case.id == "rapid-stale" else 25.0,
            used_epoch=used_epoch,
        )

    report = run_replay(
        cases,
        query,
        model_refresh_measurements_ms=[80.0, 150.0, 260.0],
    )
    payload = report.to_dict()

    assert payload.keys() >= {
        "top_1_accuracy",
        "top_5_recall",
        "literal_fallback_rate",
        "wrong_script_candidate_count",
        "snapshot_age_ms_at_query",
        "candidate_query_ms_p50",
        "candidate_query_ms_p95",
        "candidate_query_ms_p99",
        "model_refresh_ms_p50",
        "model_refresh_ms_p95",
    }
    assert payload["case_count"] == 5
    assert payload["top_1_accuracy"] == 0.8
    assert payload["top_5_recall"] == 1.0
    assert payload["wrong_script_candidate_count"] == 0
    assert payload["english_context_han_candidate_count"] == 0
    assert payload["chinese_context_latin_expected_found"] is True
    assert payload["stale_snapshot_query_count"] == 1
    assert payload["stale_snapshot_error_count"] == 1
    assert payload["candidate_query_ms_p50"] > 0
    assert payload["model_refresh_ms_p95"] > 100


def test_replay_rejects_fixed_or_missing_measurements(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert load_replay_cases(empty) == []

    cases = load_replay_cases(FIXTURE)
    try:
        run_replay(cases, lambda case: None, model_refresh_measurements_ms=[])
    except ValueError as error:
        assert "refresh" in str(error)
    else:
        raise AssertionError("replay accepted missing real refresh measurements")


def test_fixture_is_valid_json_lines() -> None:
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        assert isinstance(json.loads(line), dict)

