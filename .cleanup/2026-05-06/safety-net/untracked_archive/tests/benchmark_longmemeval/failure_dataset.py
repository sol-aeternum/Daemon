from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Final, cast

from tests.benchmark_longmemeval.dev_subset import load_fixture
from tests.longmemeval.ingest import CorpusSession, build_corpus_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_SUBSET_BASELINE_DIR = (
    REPO_ROOT / "tests" / "benchmark_results" / "dev_subset_baseline"
)
FAILURES_PATH = DEV_SUBSET_BASELINE_DIR / "failures.jsonl"
LOCKED_RUN_IDS: Final[tuple[str, ...]] = ("run1", "run2")
RETRIEVAL_LOG_SCHEMA_FIELDS: Final[tuple[str, ...]] = (
    "query_text",
    "query_embedding_model",
    "candidate_memory_ids",
    "candidate_scores",
    "selected_memory_ids",
    "l0_included",
    "latency_ms",
    "retrieval_context",
    "retrieval_triggered_by",
    "created_at",
)
JUDGE_RUBRIC: Final[tuple[str, ...]] = (
    "CORRECT when the assistant answer contains the same core factual information as the reference.",
    "PARTIAL only when the reference is multi-part and the answer omits one or more required facts.",
    "INCORRECT when the core fact is wrong, contradicts the reference, or abstains despite available information.",
)


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return cast(dict[str, Any], payload)


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object rows in {path}")
        rows.append(cast(dict[str, Any], payload))
    return rows


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _run_dir(run_id: str) -> Path:
    return DEV_SUBSET_BASELINE_DIR / run_id


def load_locked_run_artifacts() -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for run_id in LOCKED_RUN_IDS:
        run_dir = _run_dir(run_id)
        results = _load_jsonl_rows(run_dir / "longmemeval_results.jsonl")
        checkpoint = _load_json_object(run_dir / "longmemeval_checkpoint.json")
        score = _load_json_object(run_dir / "longmemeval_score.json")

        if checkpoint.get("phases", {}).get("score", {}).get("status") != "completed":
            raise ValueError(f"Locked baseline run is not fully scored: {run_id}")

        artifacts[run_id] = {
            "run_id": run_id,
            "results": results,
            "results_by_question_id": {
                str(row.get("question_id", "")): row for row in results
            },
            "checkpoint": checkpoint,
            "score": score,
        }
    return artifacts


def failure_question_ids_for_run(run_artifacts: dict[str, Any]) -> list[str]:
    results = cast(list[dict[str, Any]], run_artifacts["results"])
    return [
        str(row.get("question_id", ""))
        for row in results
        if row.get("judgment") != "correct"
    ]


def ordered_failure_question_ids(
    *,
    fixture: list[dict[str, Any]] | None = None,
    artifacts: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    dataset = fixture or load_fixture()
    locked_artifacts = artifacts or load_locked_run_artifacts()
    failed_ids = {
        question_id
        for run_artifacts in locked_artifacts.values()
        for question_id in failure_question_ids_for_run(run_artifacts)
    }
    return [
        str(entry.get("question_id", ""))
        for entry in dataset
        if str(entry.get("question_id", "")) in failed_ids
    ]


def _build_question_metadata(
    entry: dict[str, Any],
    *,
    category: str,
    corpus_keys: tuple[str, ...],
    question_position: int,
) -> dict[str, Any]:
    haystack_dates = _normalize_string_list(entry.get("haystack_dates"))
    return {
        "question_position": question_position,
        "question": str(entry.get("question", "")),
        "question_type": str(entry.get("question_type", "")),
        "category": category,
        "is_abstention_variant": str(entry.get("question_id", "")).endswith("_abs"),
        "question_date": str(entry.get("question_date", "")),
        "answer_session_ids": _normalize_string_list(entry.get("answer_session_ids")),
        "haystack_session_ids": _normalize_string_list(entry.get("haystack_session_ids")),
        "haystack_session_count": len(_normalize_string_list(entry.get("haystack_session_ids"))),
        "haystack_date_range": {
            "first": haystack_dates[0] if haystack_dates else None,
            "last": haystack_dates[-1] if haystack_dates else None,
        },
        "corpus_key_count": len(corpus_keys),
        "corpus_keys": list(corpus_keys),
    }


def _build_scoped_sessions(
    *,
    run_artifacts: dict[str, Any],
    corpus_keys: tuple[str, ...],
    corpus_session_lookup: dict[str, CorpusSession],
) -> list[dict[str, Any]]:
    ingest_results = cast(
        dict[str, dict[str, Any]],
        run_artifacts["checkpoint"]["phases"]["ingest"]["results"],
    )
    scoped_sessions: list[dict[str, Any]] = []
    for corpus_key in corpus_keys:
        corpus_session = corpus_session_lookup.get(corpus_key)
        ingest_result = ingest_results.get(corpus_key, {})
        scoped_sessions.append(
            {
                "corpus_key": corpus_key,
                "canonical_session_id": (
                    corpus_session.canonical_session_id if corpus_session else None
                ),
                "raw_session_ids": (
                    list(corpus_session.raw_session_ids)
                    if corpus_session
                    else _normalize_string_list(ingest_result.get("raw_session_ids"))
                ),
                "conversation_id": ingest_result.get("conversation_id"),
                "message_count": ingest_result.get("message_count"),
                "status": ingest_result.get("status", "missing_artifact"),
                "error": ingest_result.get("error"),
            }
        )
    return scoped_sessions


def _build_extraction_evidence(
    *,
    scoped_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = Counter(str(session.get("status", "missing_artifact")) for session in scoped_sessions)
    complete_conversation_ids = [
        str(session["conversation_id"])
        for session in scoped_sessions
        if session.get("status") == "complete"
        and isinstance(session.get("conversation_id"), str)
    ]
    incomplete_sessions = [
        session for session in scoped_sessions if session.get("status") != "complete"
    ]
    return {
        "status_counts": dict(status_counts),
        "complete_conversation_ids": complete_conversation_ids,
        "failed_or_incomplete_sessions": incomplete_sessions,
        "scoped_sessions": scoped_sessions,
    }


def _build_judge_reasoning(
    *,
    run_artifacts: dict[str, Any],
    result_row: dict[str, Any],
) -> dict[str, Any]:
    judge_config = cast(
        dict[str, Any],
        run_artifacts["checkpoint"]["benchmark_effective_config"]["pinned_authority"][
            "shared"
        ]["judge"],
    )
    return {
        "available": False,
        "reason": (
            "Locked canonical artifacts persist only the normalized judge label; "
            "the one-sentence judge explanation returned by judge_answer() was not checkpointed."
        ),
        "judgment_label": result_row.get("judgment"),
        "judge_model": judge_config.get("model"),
        "temperature": judge_config.get("temperature"),
        "max_tokens": judge_config.get("max_tokens"),
        "judge_prompt_sha256": judge_config.get("prompt_sha256"),
        "rubric_contract": list(JUDGE_RUBRIC),
    }


def _build_retrieval_evidence(
    *,
    run_artifacts: dict[str, Any],
    question_text: str,
    scoped_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_config = cast(
        dict[str, Any],
        run_artifacts["checkpoint"]["benchmark_effective_config"]["pinned_authority"][
            "shared"
        ]["retrieval"],
    )
    runtime_config = cast(
        dict[str, Any], run_artifacts["checkpoint"]["benchmark_effective_config"]["runtime"]
    )
    return {
        "available": False,
        "reason": (
            "Committed locked run artifacts do not include retrieval_log rows or selected "
            "memory snapshots; retrieval evidence only existed in the benchmark database at run time."
        ),
        "query_text": question_text,
        "scoped_conversation_ids": [
            str(session["conversation_id"])
            for session in scoped_sessions
            if isinstance(session.get("conversation_id"), str)
        ],
        "retrieval_contract": {
            "top_k_memories": retrieval_config["call_contract"].get("top_k_memories"),
            "include_l0": retrieval_config["call_contract"].get("include_l0"),
            "include_dream_observations": retrieval_config["call_contract"].get(
                "include_dream_observations"
            ),
            "retrieval_triggered_by": retrieval_config["call_contract"].get(
                "retrieval_triggered_by"
            ),
            "include_historical": retrieval_config["scope_defaults"].get(
                "include_historical"
            ),
            "memory_slot": retrieval_config["scope_defaults"].get("memory_slot"),
            "force_retrieval_logging": runtime_config.get("force_retrieval_logging"),
        },
        "expected_retrieval_log_schema": {
            "table": "retrieval_log",
            "fields": list(RETRIEVAL_LOG_SCHEMA_FIELDS),
        },
    }


def _build_active_memory_state(
    *,
    run_artifacts: dict[str, Any],
    result_row: dict[str, Any],
    scoped_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_config = cast(
        dict[str, Any],
        run_artifacts["checkpoint"]["benchmark_effective_config"]["pinned_authority"][
            "shared"
        ]["retrieval"],
    )
    scoped_conversation_ids = [
        str(session["conversation_id"])
        for session in scoped_sessions
        if isinstance(session.get("conversation_id"), str)
    ]
    return {
        "available": False,
        "reason": (
            "evaluate_single() checkpointed only memories_used; it did not persist the exact "
            "memory IDs or contents returned at query time."
        ),
        "memories_used": result_row.get("memories_used"),
        "allowed_source_conversation_ids": scoped_conversation_ids,
        "allowed_conversation_statuses": {
            str(session["conversation_id"]): session.get("status")
            for session in scoped_sessions
            if isinstance(session.get("conversation_id"), str)
        },
        "retrieval_scope": {
            "top_k_memories": retrieval_config["call_contract"].get("top_k_memories"),
            "include_l0": retrieval_config["call_contract"].get("include_l0"),
            "include_dream_observations": retrieval_config["call_contract"].get(
                "include_dream_observations"
            ),
            "include_historical": retrieval_config["scope_defaults"].get(
                "include_historical"
            ),
            "memory_slot": retrieval_config["scope_defaults"].get("memory_slot"),
        },
    }


def _build_failure_occurrence(
    *,
    run_artifacts: dict[str, Any],
    result_row: dict[str, Any],
    entry: dict[str, Any],
    corpus_keys: tuple[str, ...],
    corpus_session_lookup: dict[str, CorpusSession],
) -> dict[str, Any]:
    scoped_sessions = _build_scoped_sessions(
        run_artifacts=run_artifacts,
        corpus_keys=corpus_keys,
        corpus_session_lookup=corpus_session_lookup,
    )
    return {
        "run_id": run_artifacts["run_id"],
        "judgment": result_row.get("judgment"),
        "model_answer": result_row.get("hypothesis"),
        "judge_reasoning": _build_judge_reasoning(
            run_artifacts=run_artifacts,
            result_row=result_row,
        ),
        "retrieval_evidence": _build_retrieval_evidence(
            run_artifacts=run_artifacts,
            question_text=str(entry.get("question", "")),
            scoped_sessions=scoped_sessions,
        ),
        "extraction_evidence": _build_extraction_evidence(scoped_sessions=scoped_sessions),
        "active_memory_state": _build_active_memory_state(
            run_artifacts=run_artifacts,
            result_row=result_row,
            scoped_sessions=scoped_sessions,
        ),
    }


def build_failure_dataset_rows() -> list[dict[str, Any]]:
    fixture = load_fixture()
    corpus_plan = build_corpus_plan(fixture)
    corpus_session_lookup = {
        session.corpus_key: session for session in corpus_plan.corpus_sessions
    }
    artifacts = load_locked_run_artifacts()
    ordered_question_ids = ordered_failure_question_ids(
        fixture=fixture,
        artifacts=artifacts,
    )
    fixture_lookup = {
        str(entry.get("question_id", "")): entry for entry in fixture
    }

    rows: list[dict[str, Any]] = []
    for question_position, question_id in enumerate(ordered_question_ids, start=1):
        entry = fixture_lookup[question_id]
        corpus_keys = corpus_plan.question_corpus_refs.get(question_id, ())
        failure_occurrences: list[dict[str, Any]] = []
        category = ""

        for run_id in LOCKED_RUN_IDS:
            result_row = cast(
                dict[str, Any] | None,
                artifacts[run_id]["results_by_question_id"].get(question_id),
            )
            if result_row is None or result_row.get("judgment") == "correct":
                continue
            category = str(result_row.get("category", category or ""))
            failure_occurrences.append(
                _build_failure_occurrence(
                    run_artifacts=artifacts[run_id],
                    result_row=result_row,
                    entry=entry,
                    corpus_keys=corpus_keys,
                    corpus_session_lookup=corpus_session_lookup,
                )
            )

        rows.append(
            {
                "question_id": question_id,
                "question_metadata": _build_question_metadata(
                    entry,
                    category=category,
                    corpus_keys=corpus_keys,
                    question_position=question_position,
                ),
                "expected_answer": entry.get("answer"),
                "observed_failure_runs": [
                    occurrence["run_id"] for occurrence in failure_occurrences
                ],
                "failure_run_count": len(failure_occurrences),
                "failure_occurrences": failure_occurrences,
            }
        )

    return rows


def write_failure_dataset(output_path: Path = FAILURES_PATH) -> list[dict[str, Any]]:
    rows = build_failure_dataset_rows()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    return rows


def main() -> None:
    rows = write_failure_dataset()
    print(f"Wrote {len(rows)} failure rows to {FAILURES_PATH}")


if __name__ == "__main__":
    main()
