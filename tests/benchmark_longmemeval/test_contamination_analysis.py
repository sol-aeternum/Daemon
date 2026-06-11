from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "tests" / "benchmark_results"
FAST_RESULTS_PATH = RESULTS_DIR / "longmemeval_tier2_fast" / "longmemeval_fast_results.jsonl"
FAST_SUMMARY_PATH = RESULTS_DIR / "longmemeval_tier2_fast.json"
FAST_RUN_LOG_PATH = RESULTS_DIR / "longmemeval_tier2_fast" / "run.log"


def _load_fast_results() -> list[dict[str, object]]:
    return [json.loads(line) for line in FAST_RESULTS_PATH.read_text().splitlines() if line.strip()]


def _load_failed_qids() -> list[str]:
    failed_qids: list[str] = []
    for line in FAST_RUN_LOG_PATH.read_text().splitlines():
        marker = "Question "
        suffix = " failed"
        if marker in line and suffix in line:
            failed_qids.append(line.split(marker, 1)[1].split(suffix, 1)[0])
    return failed_qids


def test_historical_fast_summary_preserves_weighted_vs_strict_split() -> None:
    summary = cast(dict[str, object], json.loads(FAST_SUMMARY_PATH.read_text()))
    results = _load_fast_results()

    counts = {"correct": 0, "partially_correct": 0, "incorrect": 0}
    for row in results:
        judgment = str(row["judgment"])
        counts[judgment] = counts.get(judgment, 0) + 1

    assert len(results) == 500
    assert counts == {"correct": 311, "partially_correct": 189, "incorrect": 0}
    assert counts["correct"] / len(results) == 0.622
    assert summary["overall_accuracy"] == 0.811
    assert summary["strict_correct_only_accuracy"] == 0.622


def test_failed_run_log_questions_reappear_as_clean_rows() -> None:
    if not FAST_RUN_LOG_PATH.exists():
        pytest.skip(
            f"{FAST_RUN_LOG_PATH} is an untracked benchmark artifact; "
            "this analysis only runs on machines that have the fast-run log."
        )
    results_by_qid = {str(row["question_id"]): row for row in _load_fast_results()}
    failed_qids = _load_failed_qids()

    assert failed_qids == [
        "21436231",
        "95bcc1c8",
        "0862e8bf",
        "853b0a1d",
        "a06e4cfe",
        "37d43f65",
        "b86304ba",
        "d52b4f67",
        "25e5aa4f",
        "caf9ead2",
        "8550ddae",
    ]

    for question_id in failed_qids:
        row = results_by_qid[question_id]
        assert row["chunk_count"]
        assert row["session_count"]
        assert "error" not in row
