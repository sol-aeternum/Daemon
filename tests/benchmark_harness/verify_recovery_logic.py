#!/usr/bin/env python3
"""
Bounded verification for the selective recovery logic.

Tests the filtering and amended-checkpoint logic WITHOUT running any DB operations
or long ingestion. Verifies the logic is correct before launching the full recovery.

Run: PYTHONPATH=. python tests/benchmark_harness/verify_recovery_logic.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_full_corpus_baseline"
RECOVERY_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_full_corpus_recovery"
DATASET = Path(tempfile.gettempdir()) / "longmemeval-review/data/longmemeval_s.json"

sys.path.insert(0, str(PROJECT_ROOT))

from tests.longmemeval.ingest import build_corpus_key, build_corpus_plan  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def test_error_corpus_key_extraction():
    print("\n[TEST 1] extract_error_corpus_keys")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_keys = {ck for ck, row in results.items() if row.get("status") == "error"}
    assert len(error_keys) == 7298, f"Expected 7298 error keys, got {len(error_keys)}"
    assert all(
        "error" not in row.get("status", "") for ck, row in results.items() if ck not in error_keys
    )
    print(f"  PASS: extracted {len(error_keys)} error corpus_keys")


def test_filtered_dataset_correctness():
    print("\n[TEST 2] build_filtered_dataset")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_corpus_keys = {ck for ck, row in results.items() if row.get("status") == "error"}

    with open(DATASET) as f:
        original_dataset = json.load(f)

    from tests.benchmark_harness.ingestion_rerun_recovery import build_filtered_dataset

    filtered = build_filtered_dataset(original_dataset, error_corpus_keys)

    total_filtered_sessions = sum(len(item["haystack_sessions"]) for item in filtered)
    unique_filtered_corpus_keys = set()
    for item in filtered:
        for session_messages in item["haystack_sessions"]:
            unique_filtered_corpus_keys.add(build_corpus_key(session_messages))
    assert unique_filtered_corpus_keys == error_corpus_keys, (
        "Filtered dataset corpus_keys don't match error corpus_keys"
    )
    assert len(unique_filtered_corpus_keys) == 7298, (
        f"Expected 7298 unique sessions in filtered, got {len(unique_filtered_corpus_keys)}"
    )
    print(
        f"  PASS: filtered dataset has {len(filtered)} questions, "
        f"{total_filtered_sessions} raw sessions, "
        f"{len(unique_filtered_corpus_keys)} unique corpus_keys"
    )


def test_corpus_key_stability():
    print("\n[TEST 3] corpus_key stability (determinism)")
    with open(DATASET) as f:
        dataset = json.load(f)
    plan = build_corpus_plan(dataset)
    corpus_keys = {cs.corpus_key for cs in plan.corpus_sessions}
    assert len(corpus_keys) == 18475, f"Expected 18475 unique corpus_keys, got {len(corpus_keys)}"
    print("  PASS: 18475 unique corpus_keys computed from dataset")


def test_amended_checkpoint_excludes_errors():
    print("\n[TEST 4] amended checkpoint excludes error corpus_keys")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_corpus_keys = {ck for ck, row in results.items() if row.get("status") == "error"}

    from tests.benchmark_harness.ingestion_rerun_recovery import build_amended_checkpoint

    amended = build_amended_checkpoint(baseline, error_corpus_keys)
    amended_results = amended["phases"]["ingest"]["results"]

    assert len(amended_results) == 18475 - 7298, (
        f"Expected {18475 - 7298} rows in amended checkpoint, got {len(amended_results)}"
    )
    assert not any(row.get("status") == "error" for row in amended_results.values()), (
        "amended checkpoint still contains error rows"
    )
    preserved = {
        ck: row
        for ck, row in results.items()
        if row.get("status") in ("complete", "extraction_failed")
    }
    assert len(preserved) == 11177, f"Expected 11177 preserved rows, got {len(preserved)}"
    for ck, row in preserved.items():
        assert amended_results[ck] == row, f"Preserved row mismatch for {ck}"
    print(f"  PASS: amended checkpoint has {len(amended_results)} rows, 0 error rows")


def test_filtered_dataset_produces_error_corpus_keys():
    print("\n[TEST 5] filtered dataset corpus_keys match error corpus_keys")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_corpus_keys = {ck for ck, row in results.items() if row.get("status") == "error"}

    with open(DATASET) as f:
        original_dataset = json.load(f)

    from tests.benchmark_harness.ingestion_rerun_recovery import build_filtered_dataset

    filtered = build_filtered_dataset(original_dataset, error_corpus_keys)

    filtered_corpus_keys = set()
    for item in filtered:
        for session_messages in item["haystack_sessions"]:
            filtered_corpus_keys.add(build_corpus_key(session_messages))

    assert filtered_corpus_keys == error_corpus_keys, (
        f"Filtered dataset corpus_keys != error corpus_keys\n"
        f"  Missing from filtered: {error_corpus_keys - filtered_corpus_keys}\n"
        f"  Extra in filtered: {filtered_corpus_keys - error_corpus_keys}"
    )
    print(
        f"  PASS: filtered dataset corpus_keys exactly match error corpus_keys ({len(filtered_corpus_keys)})"
    )


def test_merge_checkpoints_produces_correct_totals():
    print("\n[TEST 6] merge_checkpoints correctness")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_corpus_keys = {ck for ck, row in results.items() if row.get("status") == "error"}

    from tests.benchmark_harness.ingestion_rerun_recovery import build_amended_checkpoint

    amended = build_amended_checkpoint(baseline, error_corpus_keys)

    recovery_results: dict[str, dict[str, object]] = {
        ck: dict(row, status="complete", outcome="completed", error=None)
        for ck, row in amended["phases"]["ingest"]["results"].items()
    }
    recovery_results.update(
        {
            ck: {
                "session_id": f"recovered_{ck[:16]}",
                "status": "complete",
                "outcome": "completed",
                "error": None,
                "corpus_key": ck,
                "raw_session_ids": [f"recovered_{ck[:16]}"],
            }
            for ck in error_corpus_keys
        }
    )
    recovery_checkpoint = {
        "phases": {
            "ingest": {
                "results": recovery_results,
                "completed_count": len(recovery_results),
            }
        }
    }

    from tests.benchmark_harness.ingestion_rerun_recovery import merge_checkpoints

    merged = merge_checkpoints(baseline, recovery_checkpoint)
    merged_results = merged["phases"]["ingest"]["results"]

    assert len(merged_results) == 18475, (
        f"Expected 18475 total rows after merge, got {len(merged_results)}"
    )
    error_count = sum(1 for row in merged_results.values() if row.get("status") == "error")
    assert error_count == 0, f"Expected 0 error rows after merge, got {error_count}"
    complete_count = sum(1 for row in merged_results.values() if row.get("status") == "complete")
    assert complete_count == 18475, (
        f"Expected all 18475 complete after recovery, got {complete_count}"
    )
    print(
        f"  PASS: merged checkpoint has {len(merged_results)} rows, {complete_count} complete, {error_count} error"
    )


def test_guardrail_canonical_mapping():
    print("\n[TEST 7] guardrail canonical mapping on merged checkpoint")
    baseline = load_json(BASELINE_DIR / "longmemeval_checkpoint.json")
    results = baseline["phases"]["ingest"]["results"]
    error_corpus_keys = {ck for ck, row in results.items() if row.get("status") == "error"}

    from tests.benchmark_harness.ingestion_rerun_recovery import build_amended_checkpoint

    amended = build_amended_checkpoint(baseline, error_corpus_keys)

    recovery_results: dict[str, dict[str, object]] = {
        ck: dict(row, status="complete", outcome="completed", error=None)
        for ck, row in amended["phases"]["ingest"]["results"].items()
    }
    recovery_results.update(
        {
            ck: {
                "session_id": f"recovered_{ck[:16]}",
                "status": "complete",
                "outcome": "completed",
                "error": None,
                "corpus_key": ck,
                "raw_session_ids": [f"recovered_{ck[:16]}"],
            }
            for ck in error_corpus_keys
        }
    )
    recovery_checkpoint = {
        "phases": {
            "ingest": {
                "results": recovery_results,
                "completed_count": len(recovery_results),
            }
        }
    }

    from tests.benchmark_harness.ingestion_rerun_recovery import merge_checkpoints, summarize

    merged = merge_checkpoints(baseline, recovery_checkpoint)
    summary = summarize(merged)

    assert summary["total_sessions"] == 18475, (
        f"Expected 18475 total, got {summary['total_sessions']}"
    )
    assert summary["outcome_counts"]["completed"] == 18475, (
        f"Expected all 18475 completed outcome, got {summary['outcome_counts']['completed']}"
    )
    assert summary["outcome_counts"]["errored"] == 0, (
        f"Expected 0 errored outcome, got {summary['outcome_counts']['errored']}"
    )
    assert summary["errored_rate"] == 0.0, (
        f"Expected 0.0% errored rate, got {summary['errored_rate']}"
    )
    print(
        f"  PASS: canonical mapping produces errored_rate={summary['errored_rate']:.1f}% "
        f"(would PASS G3 at 5% threshold)"
    )


def main() -> int:
    print("=" * 60)
    print("Selective Recovery — Bounded Verification Tests")
    print("=" * 60)
    print(f"BASELINE_DIR : {BASELINE_DIR}")
    print(f"RECOVERY_DIR : {RECOVERY_DIR}")
    print(f"DATASET      : {DATASET}")

    if not (BASELINE_DIR / "longmemeval_checkpoint.json").exists():
        print(
            f"\nERROR: Baseline checkpoint not found at {BASELINE_DIR / 'longmemeval_checkpoint.json'}"
        )
        print("Run the full-corpus baseline first, then recovery.")
        return 1

    if not Path(DATASET).exists():
        print(f"\nERROR: Dataset not found at {DATASET}")
        return 1

    tests = [
        test_error_corpus_key_extraction,
        test_filtered_dataset_correctness,
        test_corpus_key_stability,
        test_amended_checkpoint_excludes_errors,
        test_filtered_dataset_produces_error_corpus_keys,
        test_merge_checkpoints_produces_correct_totals,
        test_guardrail_canonical_mapping,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All bounded verification tests PASSED.")
        print("The filtering/amended-checkpoint logic is correct.")
        print("Ready to launch selective recovery ingest.")
    else:
        print("Some tests FAILED — fix before launching recovery ingest.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
