from __future__ import annotations

import json

from tests.benchmark_longmemeval.failure_dataset import (
    FAILURES_PATH,
    build_failure_dataset_rows,
    load_locked_run_artifacts,
    ordered_failure_question_ids,
)


def _load_generated_rows() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FAILURES_PATH.read_text().splitlines()
        if line.strip()
    ]


def test_failure_dataset_uses_union_semantics_across_locked_runs() -> None:
    artifacts = load_locked_run_artifacts()

    run1_failure_ids = ordered_failure_question_ids(
        fixture=None,
        artifacts={"run1": artifacts["run1"]},
    )
    run2_failure_ids = ordered_failure_question_ids(
        fixture=None,
        artifacts={"run2": artifacts["run2"]},
    )
    union_ids = ordered_failure_question_ids(artifacts=artifacts)

    assert len(run1_failure_ids) == 34
    assert len(run2_failure_ids) == 39
    assert len(union_ids) == 39
    assert set(run1_failure_ids).issubset(set(run2_failure_ids))
    assert set(run2_failure_ids) - set(run1_failure_ids) == {
        "25e5aa4f",
        "3ba21379",
        "8550ddae",
        "e8a79c70",
        "gpt4_372c3eed_abs",
    }

    rows = build_failure_dataset_rows()
    assert [row["question_id"] for row in rows] == union_ids

    row_by_question_id = {str(row["question_id"]): row for row in rows}
    assert row_by_question_id["8550ddae"]["observed_failure_runs"] == ["run2"]
    assert row_by_question_id["86f00804"]["observed_failure_runs"] == ["run1", "run2"]


def test_failure_dataset_marks_unavailable_judge_retrieval_and_active_memory_truthfully() -> None:
    rows = build_failure_dataset_rows()
    row_by_question_id = {str(row["question_id"]): row for row in rows}

    occurrence = row_by_question_id["86f00804"]["failure_occurrences"][0]

    assert occurrence["judge_reasoning"]["available"] is False
    assert occurrence["retrieval_evidence"]["available"] is False
    assert occurrence["active_memory_state"]["available"] is False
    assert occurrence["judge_reasoning"]["judgment_label"] == "incorrect"
    assert occurrence["retrieval_evidence"]["expected_retrieval_log_schema"]["table"] == (
        "retrieval_log"
    )
    assert occurrence["active_memory_state"]["memories_used"] == 5


def test_failure_dataset_captures_run_scoped_extraction_anomalies() -> None:
    rows = build_failure_dataset_rows()
    extraction_failed_errors = [
        session.get("error")
        for row in rows
        for occurrence in row["failure_occurrences"]
        for session in occurrence["extraction_evidence"]["scoped_sessions"]
        if session.get("status") == "extraction_failed"
    ]

    assert "Supersede failed to close source memory in active state" in extraction_failed_errors


def test_failure_dataset_file_is_in_sync_with_builder() -> None:
    assert FAILURES_PATH.exists(), f"Failure dataset missing: {FAILURES_PATH}"

    expected_lines = [
        json.dumps(row, sort_keys=True) for row in build_failure_dataset_rows()
    ]
    assert FAILURES_PATH.read_text().splitlines() == expected_lines
