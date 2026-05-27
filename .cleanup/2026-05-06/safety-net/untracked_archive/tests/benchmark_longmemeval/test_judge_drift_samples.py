from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import cast


BENCHMARK_RESULTS_DIR = Path("tests/benchmark_results")

CURRENT_RESULTS = (
    BENCHMARK_RESULTS_DIR
    / "longmemeval_optimized_retry"
    / "longmemeval_fast_results.jsonl"
)
HISTORICAL_RESULTS = (
    BENCHMARK_RESULTS_DIR
    / "longmemeval_tier2_fast"
    / "longmemeval_fast_results.jsonl"
)
JUDGE_RESTORE_RESULTS = (
    BENCHMARK_RESULTS_DIR
    / "longmemeval_optimized_judge_restore"
    / "longmemeval_fast_results.jsonl"
)

STRICTNESS_SAMPLES = {
    "58bf7951": {
        "category": "IE-user",
        "current": "correct",
        "historical": "partially_correct",
        "judge_restore": "partially_correct",
    },
    "c5e8278d": {
        "category": "IE-user",
        "current": "correct",
        "historical": "partially_correct",
        "judge_restore": "partially_correct",
    },
    "6f9b354f": {
        "category": "IE-user",
        "current": "correct",
        "historical": "partially_correct",
        "judge_restore": "partially_correct",
    },
    "6aeb4375": {
        "category": "KU",
        "current": "correct",
        "historical": "partially_correct",
        "judge_restore": "partially_correct",
    },
}

LENIENCY_SAMPLES = {
    "0a995998": {
        "category": "MR",
        "current": "partially_correct",
        "historical": "correct",
        "judge_restore": "incorrect",
    },
    "6d550036": {
        "category": "MR",
        "current": "incorrect",
        "historical": "correct",
        "judge_restore": "incorrect",
    },
    "gpt4_5501fe77": {
        "category": "MR",
        "current": "incorrect",
        "historical": "correct",
        "judge_restore": "partially_correct",
    },
    "gpt4_31ff4165": {
        "category": "MR",
        "current": "partially_correct",
        "historical": "correct",
        "judge_restore": "incorrect",
    },
    "f4f1d8a4_abs": {
        "category": "IE-user",
        "current": "incorrect",
        "historical": "correct",
        "judge_restore": "incorrect",
    },
    "66f24dbb": {
        "category": "IE-user",
        "current": "incorrect",
        "historical": "partially_correct",
        "judge_restore": "partially_correct",
    },
}

JUDGMENT_ORDER = {"incorrect": 0, "partially_correct": 1, "correct": 2}


def _load_results(path: Path) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for line in path.read_text().splitlines():
        row = cast(dict[str, str], json.loads(line))
        results[row["question_id"]] = row
    return results


def test_current_vs_judge_restore_strictness_totals_are_stable() -> None:
    current = _load_results(CURRENT_RESULTS)
    judge_restore = _load_results(JUDGE_RESTORE_RESULTS)

    flips = Counter(
        (current[qid]["judgment"], judge_restore[qid]["judgment"])
        for qid in current
    )

    assert flips[("correct", "partially_correct")] == 149
    assert flips[("correct", "incorrect")] == 23
    assert flips[("incorrect", "partially_correct")] == 28
    assert flips[("incorrect", "correct")] == 5
    assert flips[("partially_correct", "correct")] == 2
    assert flips[("partially_correct", "incorrect")] == 6


def test_historical_bundle_leniency_totals_on_current_failures_are_stable() -> None:
    current = _load_results(CURRENT_RESULTS)
    historical = _load_results(HISTORICAL_RESULTS)

    non_correct_qids = [
        qid for qid, row in current.items() if row["judgment"] != "correct"
    ]
    lenient_qids = [
        qid
        for qid in non_correct_qids
        if JUDGMENT_ORDER[historical[qid]["judgment"]]
        > JUDGMENT_ORDER[current[qid]["judgment"]]
    ]

    transitions = Counter(
        (current[qid]["judgment"], historical[qid]["judgment"])
        for qid in lenient_qids
    )

    assert len(non_correct_qids) == 161
    assert len(lenient_qids) == 137
    assert transitions[("incorrect", "correct")] == 102
    assert transitions[("incorrect", "partially_correct")] == 26
    assert transitions[("partially_correct", "correct")] == 9


def test_strictness_samples_match_saved_artifacts() -> None:
    current = _load_results(CURRENT_RESULTS)
    historical = _load_results(HISTORICAL_RESULTS)
    judge_restore = _load_results(JUDGE_RESTORE_RESULTS)

    for qid, expected in STRICTNESS_SAMPLES.items():
        assert current[qid]["category"] == expected["category"]
        assert current[qid]["judgment"] == expected["current"]
        assert historical[qid]["judgment"] == expected["historical"]
        assert judge_restore[qid]["judgment"] == expected["judge_restore"]


def test_leniency_samples_match_saved_artifacts() -> None:
    current = _load_results(CURRENT_RESULTS)
    historical = _load_results(HISTORICAL_RESULTS)
    judge_restore = _load_results(JUDGE_RESTORE_RESULTS)

    for qid, expected in LENIENCY_SAMPLES.items():
        assert current[qid]["category"] == expected["category"]
        assert current[qid]["judgment"] == expected["current"]
        assert historical[qid]["judgment"] == expected["historical"]
        assert judge_restore[qid]["judgment"] == expected["judge_restore"]
