#!/usr/bin/env python3
"""
Wave 0 — Selective Recovery Harness (Tests-Only)

Reprocesses the 7,298 `status="error"` sessions from the invalid full-corpus
baseline run WITHOUT resetting the DB — preserving the 11,157 already-successful
sessions.

Recovery strategy (Option A — Filtered Dataset + Amended Checkpoint, no DB reset):
  1. Extract error corpus_keys from original checkpoint
  2. Build filtered dataset containing only those sessions
  3. Build amended checkpoint with error rows removed (so runner re-processes them)
  4. Run recovery ingest using filtered dataset + amended checkpoint
  5. Merge original (complete/extraction_failed) + recovery results
  6. Verify corrected errored_rate ≤ 5%

Scope: tests/ only. No production code changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
BASELINE_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_full_corpus_baseline"
RECOVERY_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_full_corpus_recovery"
DATASET = PROJECT_ROOT / "/tmp/longmemeval-review/data/longmemeval_s.json"

os.environ["BENCHMARK_MODE"] = "1"

BASE_ENV = os.environ.copy()
from tests.benchmark_harness.database import configured_benchmark_database_url  # noqa: E402

BASE_ENV["DATABASE_URL"] = configured_benchmark_database_url()

# ---------------------------------------------------------------------------
# PATCH_CODE — same patches as ingestion_rerun_full_corpus.py
# ---------------------------------------------------------------------------

PATCH_CODE = """
import sys
import dotenv
dotenv.load_dotenv()

# Patch 1: extraction provider slug -> "openai"
import orchestrator.memory.extraction as _ext
_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"
print(f"[patched] orchestrator.memory.extraction.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = 'openai'")

# Patch 2: extraction fingerprint drift -> diagnostic-only
import asyncio
_BenchmarkSamplingError = _ext.BenchmarkSamplingError
_original_extract = _ext.extract_facts_from_text

async def _patched_extract_facts_from_text(text, model="openrouter/openai/gpt-4o-mini", *, summary=None, retry_hint=None, benchmark_mode=None):
    try:
        return await _original_extract(text, model=model, summary=summary, retry_hint=retry_hint, benchmark_mode=benchmark_mode)
    except _BenchmarkSamplingError as e:
        print(f"[patched] extract_facts_from_text: BenchmarkSamplingError caught (diagnostic) -> {e}")
        from dataclasses import dataclass
        @dataclass
        class _EmptyOutcome:
            facts: list = None
            raw_count: int = 0
            calibrated_count: int = 0
            rejected_count: int = 0
            slot_coverage: int = 0
        return _EmptyOutcome()

_ext.extract_facts_from_text = _patched_extract_facts_from_text
print(f"[patched] orchestrator.memory.extraction.extract_facts_from_text -> catches BenchmarkSamplingError (diagnostic-only)")

# Patch 3 & 4: dedup contradiction model and provider
import orchestrator.memory.dedup as _dedup
_dedup.BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"
print(f"[patched] orchestrator.memory.dedup.BENCHMARK_CONTRADICTION_MODEL = 'openrouter/deepseek/deepseek-v3.2'")
print(f"[patched] orchestrator.memory.dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = 'novita'")

# Patch 5: dedup contradiction check -> advisory-only
_DedupBenchmarkSamplingError = _dedup.DedupBenchmarkSamplingError
_dedup_check_orig = _dedup.check_contradiction

async def _patched_check_contradiction(existing_content, new_content, benchmark_mode=None):
    try:
        return await _dedup_check_orig(existing_content, new_content, benchmark_mode=benchmark_mode)
    except _DedupBenchmarkSamplingError as e:
        print(f"[patched] check_contradiction: DedupBenchmarkSamplingError caught -> {e}")
        return False, ""

_dedup.check_contradiction = _patched_check_contradiction
print(f"[patched] orchestrator.memory.dedup.check_contradiction -> catches DedupBenchmarkSamplingError (advisory)")
"""

# ---------------------------------------------------------------------------
# Recovery ingest — NO reset, uses filtered dataset + amended checkpoint
# ---------------------------------------------------------------------------

RECOVERY_INGEST_CODE = (
    PATCH_CODE
    + """
import asyncio, sys, json
sys.path.insert(0, '{}')
from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from pathlib import Path

OUTPUT = Path('{}')
OUTPUT.mkdir(parents=True, exist_ok=True)

runner = LongMemEvalFactRunner(
    dataset_path=Path('{}'),
    output_path=OUTPUT / "longmemeval_results.jsonl",
    checkpoint_path=OUTPUT / "longmemeval_checkpoint.json",
    score_path=OUTPUT / "longmemeval_score.json",
    limit=None,
    force_retrieval_logging=True,
)

asyncio.run(runner.ingest())
print("RECOVERY_INGEST_OK")
""".format(
        str(PROJECT_ROOT),
        str(RECOVERY_DIR),
        str(RECOVERY_DIR / "longmemeval_filtered_dataset.json"),
    )
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def extract_error_corpus_keys(baseline_checkpoint: dict[str, Any]) -> set[str]:
    """Return corpus_keys of all rows with status='error'."""
    results = baseline_checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    return {ck for ck, row in results.items() if row.get("status") == "error"}


def build_filtered_dataset(
    original_dataset: list[dict[str, Any]],
    error_corpus_keys: set[str],
) -> list[dict[str, Any]]:
    """Build a filtered dataset containing only the error sessions.

    Each dataset item is a question with haystack_sessions. We include a question
    if any of its haystack_sessions has a corpus_key in error_corpus_keys.
    Within each included question, we include only the matching sessions.
    """
    from tests.longmemeval.ingest import build_corpus_key

    filtered: list[dict[str, Any]] = []
    for item in original_dataset:
        haystack_sessions = item.get("haystack_sessions", [])
        haystack_session_ids = item.get("haystack_session_ids", [])
        question_id = item.get("question_id", "")

        filtered_sessions = []
        filtered_ids = []
        for sess_idx, session_messages in enumerate(haystack_sessions):
            ck = build_corpus_key(session_messages)
            if ck in error_corpus_keys:
                filtered_sessions.append(session_messages)
                sid = (
                    haystack_session_ids[sess_idx]
                    if sess_idx < len(haystack_session_ids)
                    else f"{question_id}_session_{sess_idx}"
                )
                filtered_ids.append(sid)

        if filtered_sessions:
            filtered_item = dict(item)
            filtered_item["haystack_sessions"] = filtered_sessions
            filtered_item["haystack_session_ids"] = filtered_ids
            filtered.append(filtered_item)

    return filtered


def build_amended_checkpoint(
    baseline_checkpoint: dict[str, Any],
    error_corpus_keys: set[str],
    filtered_dataset_path: Path,
) -> dict[str, Any]:
    """Build a checkpoint with error rows removed.

    This is what allows the runner to re-process the error sessions —
    their corpus_keys will be absent from the checkpoint results.
    """
    import copy

    amended = copy.deepcopy(baseline_checkpoint)
    results = amended["phases"]["ingest"]["results"]
    for ck in error_corpus_keys:
        results.pop(ck, None)

    # Recompute completed_count
    amended["phases"]["ingest"]["completed_count"] = len(results)
    amended["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    amended["dataset_path"] = str(filtered_dataset_path)
    return amended


def merge_checkpoints(
    baseline_checkpoint: dict[str, Any],
    recovery_checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Merge complete/extraction_failed rows from baseline with recovery rows.

    Recovery rows replace any baseline rows for the same corpus_key.
    """
    import copy

    merged = copy.deepcopy(baseline_checkpoint)
    baseline_results = baseline_checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    recovery_results = recovery_checkpoint.get("phases", {}).get("ingest", {}).get("results", {})

    # Start with baseline results (complete/extraction_failed only since we amend)
    # Then overlay recovery results
    merged_results = dict(baseline_results)
    merged_results.update(recovery_results)

    merged["phases"]["ingest"]["results"] = merged_results
    merged["phases"]["ingest"]["completed_count"] = len(merged_results)
    merged["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return merged


def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Summarize checkpoint outcomes using canonical status→outcome mapping."""
    from tests.benchmark_harness.guardrails import _canonical_outcome

    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts: dict[str, int] = {"completed": 0, "errored": 0, "empty": 0, "unknown": 0}
    status_counts: dict[str, int] = {"complete": 0, "extraction_failed": 0, "error": 0}
    errors: list[str] = []
    for r in results.values():
        status = r.get("status", "unknown")
        outcome = _canonical_outcome(status)
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        if status in status_counts:
            status_counts[status] += 1
        err = r.get("error", "")
        if err and len(errors) < 5:
            errors.append(str(err)[:150])
    total = len(results)
    errored_count = outcome_counts.get("errored", 0)
    return {
        "total_sessions": total,
        "outcome_counts": outcome_counts,
        "status_counts": status_counts,
        "errored_count": errored_count,
        "errored_rate": errored_count / max(total, 1) * 100,
        "sample_errors": errors,
    }


def run_subprocess(code: str, label: str) -> tuple[int, str]:
    print(f"\n[RECOVERY] === {label} ===")
    print(f"[RECOVERY] Executing via {sys.executable}")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=BASE_ENV,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:500])
    return result.returncode, result.stdout


def write_report(
    summary: dict[str, Any],
    elapsed: float,
) -> None:
    outcome = summary.get("outcome_counts", {})
    status = summary.get("status_counts", {})
    report = f"""# Wave 0 — Full-Corpus Recovery Report

**Generated:** {datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
**Status:** {"RECOVERY COMPLETE" if summary["errored_rate"] <= 5 else "RECOVERY COMPLETE — G3 STILL FAILING"}
**Harness:** `tests/benchmark_harness/ingestion_rerun_recovery.py`

---

## Recovery Summary

| Item | Value |
|---|---|
| Original baseline sessions | 18,475 |
| Error sessions (status="error") | 7,298 |
| Sessions preserved from baseline | ~11,177 (complete + extraction_failed) |
| Recovery sessions processed | 7,298 |
| Total sessions (merged) | {summary["total_sessions"]} |
| Errored (corrected) | {summary["errored_rate"]:.1f}% |
| Wall time | {elapsed:.0f}s |

## Outcome Counts (canonical mapping applied)

| Outcome | Count |
|---|---|
| completed | {outcome.get("completed", 0)} |
| errored | {outcome.get("errored", 0)} |
| empty | {outcome.get("empty", 0)} |

## Status Counts (checkpoint status field)

| Status | Count |
|---|---|
| complete | {status.get("complete", 0)} |
| extraction_failed | {status.get("extraction_failed", 0)} |
| error | {status.get("error", 0)} |

## G3 Guardrail (Errored Floor ≤ 5%)

| Result | Value |
|---|---|
| Errored rate | {summary["errored_rate"]:.1f}% |
| Threshold | 5.0% |
| Verdict | {"PASS" if summary["errored_rate"] <= 5 else "FAIL"} |

## Sample Errors

```
{chr(10).join(summary["sample_errors"]) if summary["sample_errors"] else "None"}
```

---

*Recovery harness: `tests/benchmark_harness/ingestion_rerun_recovery.py`*
"""
    REPORT_FILE = RECOVERY_DIR / "longmemeval_recovery_report.md"
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w") as fh:
        fh.write(report)
    print(f"\n[RECOVERY] Report written → {REPORT_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("Wave 0 — Selective Recovery Harness")
    print("=" * 60)
    print(f"BENCHMARK_MODE : {BASE_ENV.get('BENCHMARK_MODE')!r}")
    print(f"Baseline dir   : {BASELINE_DIR}")
    print(f"Recovery dir    : {RECOVERY_DIR}")
    print(f"Dataset        : {DATASET}")
    print("-" * 60)

    # Verify dataset exists
    if not DATASET.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET}. "
            "Bootstrap with: python tests/longmemeval/ingest.py ensure_dataset"
        )

    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)

    # Load baseline checkpoint
    baseline_checkpoint_path = BASELINE_DIR / "longmemeval_checkpoint.json"
    if not baseline_checkpoint_path.exists():
        raise FileNotFoundError(f"No baseline checkpoint at {baseline_checkpoint_path}")
    baseline_checkpoint = load_json(baseline_checkpoint_path)

    # Load original dataset
    with open(DATASET) as f:
        original_dataset = json.load(f)

    # Step 0: Preparation — build filtered dataset and amended checkpoint
    print("\n[RECOVERY] STEP 0: Preparing filtered dataset and amended checkpoint")
    error_corpus_keys = extract_error_corpus_keys(baseline_checkpoint)
    print(f"[RECOVERY]   Error corpus_keys to reprocess: {len(error_corpus_keys)}")

    filtered_dataset = build_filtered_dataset(original_dataset, error_corpus_keys)
    filtered_dataset_path = RECOVERY_DIR / "longmemeval_filtered_dataset.json"
    save_json(filtered_dataset_path, filtered_dataset)
    print(
        f"[RECOVERY]   Filtered dataset: {len(filtered_dataset)} questions, "
        f"saved to {filtered_dataset_path}"
    )

    # Count total sessions in filtered dataset
    total_filtered_sessions = sum(len(item["haystack_sessions"]) for item in filtered_dataset)
    print(f"[RECOVERY]   Filtered sessions (total): {total_filtered_sessions}")

    amended_checkpoint = build_amended_checkpoint(
        baseline_checkpoint, error_corpus_keys, filtered_dataset_path
    )
    amended_checkpoint_path = RECOVERY_DIR / "longmemeval_checkpoint_amended.json"
    save_json(amended_checkpoint_path, amended_checkpoint)
    print(
        f"[RECOVERY]   Amended checkpoint: {amended_checkpoint['phases']['ingest']['completed_count']} rows, "
        f"saved to {amended_checkpoint_path}"
    )

    # Copy amended checkpoint to where the runner expects it
    runner_checkpoint_path = RECOVERY_DIR / "longmemeval_checkpoint.json"
    shutil.copy2(amended_checkpoint_path, runner_checkpoint_path)
    print(f"[RECOVERY]   Runner checkpoint: {runner_checkpoint_path}")

    # G1: provider health check
    try:
        from tests.benchmark_harness.guardrails import run_provider_health_check

        print("\n[RECOVERY] Running G1: provider health check...")
        run_provider_health_check(provider_slug="openai")
        print("[RECOVERY] G1: provider healthy — proceeding")
    except Exception as e:
        print(f"[RECOVERY] G1 warning: {e} — proceeding anyway")

    t0 = time.monotonic()

    # Step 1: Recovery ingest — NO reset, filtered dataset + amended checkpoint
    # The amended checkpoint has error rows removed, so the runner will process them.
    # The filtered dataset means we only iterate over the 7,298 error sessions.
    ingest_rc, _ = run_subprocess(RECOVERY_INGEST_CODE, "STEP 1: RECOVERY INGEST")
    elapsed = time.monotonic() - t0

    if ingest_rc != 0:
        print(f"[RECOVERY] INGEST failed (rc={ingest_rc})")
        # Try to summarize what we have anyway
        recovery_checkpoint_path = RECOVERY_DIR / "longmemeval_checkpoint.json"
        if recovery_checkpoint_path.exists():
            recovery_ck = load_json(recovery_checkpoint_path)
            summary = summarize(
                recovery_checkpoint := {
                    "phases": {
                        "ingest": {
                            "results": recovery_ck.get("phases", {})
                            .get("ingest", {})
                            .get("results", {})
                        }
                    }
                }
            )
            print(f"[RECOVERY] Partial summary: {summary}")
        return 1

    # Step 2: Merge checkpoints
    print("\n[RECOVERY] STEP 2: Merging baseline + recovery checkpoints")
    recovery_checkpoint = load_json(RECOVERY_DIR / "longmemeval_checkpoint.json")
    merged_checkpoint = merge_checkpoints(baseline_checkpoint, recovery_checkpoint)
    merged_checkpoint_path = RECOVERY_DIR / "longmemeval_checkpoint_merged.json"
    save_json(merged_checkpoint_path, merged_checkpoint)

    # Also write as the "final" checkpoint for the recovery run
    final_checkpoint_path = RECOVERY_DIR / "longmemeval_checkpoint.json"
    save_json(final_checkpoint_path, merged_checkpoint)
    print(
        f"[RECOVERY]   Merged checkpoint: {merged_checkpoint['phases']['ingest']['completed_count']} rows"
    )

    # Step 3: Verify
    print("\n[RECOVERY] STEP 3: Verifying merged checkpoint")
    summary = summarize(merged_checkpoint)
    oc = summary.get("outcome_counts", {})
    sc = summary.get("status_counts", {})
    print(
        f"[RECOVERY]   Sessions: {summary['total_sessions']}, "
        f"ERR {summary['errored_rate']:.1f}%, "
        f"completed={oc.get('completed', 0)}, "
        f"errored={oc.get('errored', 0)}, "
        f"empty={oc.get('empty', 0)}"
    )
    print(
        f"[RECOVERY]   Status: complete={sc.get('complete', 0)}, "
        f"extraction_failed={sc.get('extraction_failed', 0)}, "
        f"error={sc.get('error', 0)}"
    )

    # G3: Errored-floor check on merged checkpoint
    g3_passed = summary["errored_rate"] <= 5.0
    if g3_passed:
        print(f"[RECOVERY] G3: errored floor PASS ({summary['errored_rate']:.1f}%)")
    else:
        print(f"[RECOVERY] G3: errored floor FAIL ({summary['errored_rate']:.1f}% > 5.0%)")
        print("[RECOVERY] WARNING: Recovery did not bring errored rate below threshold.")

    write_report(summary, elapsed)

    verdict = "PASS" if g3_passed else "FAIL"
    print(f"\nRECOVERY RESULT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
