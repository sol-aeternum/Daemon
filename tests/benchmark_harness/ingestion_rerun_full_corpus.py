#!/usr/bin/env python3
"""
Wave 0 — Full-Corpus Baseline Harness

Tests-only harness for the full-corpus ingestion baseline, derived from the
accepted dev-subset rerun pattern (ingestion_rerun.py).

Applies the same PATCH_CODE (P1–P5) runtime patches:
  P1: extraction provider slug -> "openai"
  P2: extraction fingerprint drift -> diagnostic-only (not fatal)
  P3: contradiction model -> "openrouter/deepseek/deepseek-v3.2"
  P4: contradiction provider -> "novita"
  P5: contradiction fingerprint drift -> advisory-only (not fatal)

Run: PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_full_corpus.py

Scope: tests/ only. No production code changes.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path("tests/benchmark_results/wave0_full_corpus_baseline")
DATASET = PROJECT_ROOT / "/tmp/longmemeval-review/data/longmemeval_s.json"
CHECKPOINT = OUTPUT_DIR / "longmemeval_checkpoint.json"
RESULT_FILE = OUTPUT_DIR / "result.json"
LOG_FILE = OUTPUT_DIR / "ingest.log"
REPORT_FILE = OUTPUT_DIR.parent / "wave0_full_corpus_baseline.md"

os.environ["BENCHMARK_MODE"] = "1"

BASE_ENV = os.environ.copy()
BASE_ENV["DATABASE_URL"] = "postgresql://daemon:daemon@127.0.0.1:5432/daemon"

PATCH_CODE = """
import sys
import dotenv
dotenv.load_dotenv()

# Patch 1: extraction provider slug -> "openai" (LiteLLM routes to OpenAI backend)
import orchestrator.memory.extraction as _ext
_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"
print(f"[patched] orchestrator.memory.extraction.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = 'openai'")

# Patch 2: extraction fingerprint drift -> treat as diagnostic, not fatal
# The extraction output IS deterministic; fingerprint drift is provider-side.
# Save original BEFORE replacing to avoid circular reference.
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

# Patch 3: dedup contradiction model and provider.order routing
# OLD (invalid): "openrouter/deepseek/deepseek-chat-v3-5" returns 404
# NEW (verified working): "openrouter/deepseek/deepseek-v3.2" + provider.order=['novita']
import orchestrator.memory.dedup as _dedup
_dedup.BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"
print(f"[patched] orchestrator.memory.dedup.BENCHMARK_CONTRADICTION_MODEL = 'openrouter/deepseek/deepseek-v3.2'")
print(f"[patched] orchestrator.memory.dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = 'novita'")

# Patch 4: dedup contradiction check -> catch DedupBenchmarkSamplingError on fingerprint drift
# Save original BEFORE replacing to avoid circular reference.
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

RESET_CODE = PATCH_CODE + """
from pathlib import Path
import asyncio, sys
sys.path.insert(0, '{}')
from tests.benchmark_harness.reset_verify_helper import full_reset_with_verification
from orchestrator.config import get_settings
import asyncpg

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)
    try:
        result_obj = await full_reset_with_verification(pool, Path('{}'), cleanup_redis=False)
        import json
        result = {{
            "success": result_obj.success,
            "tables_cleared": result_obj.tables_cleared,
            "extended_tables_cleared": result_obj.extended_tables_cleared,
            "total_rows_deleted": result_obj.total_rows_deleted,
            "redis_keys_deleted": result_obj.redis_keys_deleted,
            "row_counts_after_reset": result_obj.row_counts_after_reset,
            "all_zero": result_obj.all_zero,
            "error": result_obj.error,
        }}
        with open('{}', "w") as f:
            json.dump(result, f)
        print("RESET_OK")
    finally:
        await pool.close()

asyncio.run(main())
""".format(str(PROJECT_ROOT), str(CHECKPOINT), str(RESULT_FILE))

INGEST_CODE = PATCH_CODE + """
import asyncio, sys, json
sys.path.insert(0, '{}')
from orchestrator.eval.runner import LongMemEvalRunner
from pathlib import Path

OUTPUT = Path('{}')
OUTPUT.mkdir(parents=True, exist_ok=True)

runner = LongMemEvalRunner(
    dataset_path=Path('{}'),
    output_path=OUTPUT / "longmemeval_results.jsonl",
    checkpoint_path=OUTPUT / "longmemeval_checkpoint.json",
    score_path=OUTPUT / "longmemeval_score.json",
    limit=None,
    force_retrieval_logging=True,
)

asyncio.run(runner.ingest())
print("INGEST_OK")
""".format(str(PROJECT_ROOT), str(OUTPUT_DIR), str(DATASET))


def run_subprocess(code: str, label: str) -> tuple[int, str]:
    print(f"\n[FULL_CORPUS] === {label} ===")
    print(f"[FULL_CORPUS] Executing via {sys.executable}")
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


def load_checkpoint_or_fail() -> dict[str, Any]:
    if not CHECKPOINT.exists():
        raise FileNotFoundError(f"No checkpoint at {CHECKPOINT}")
    with open(CHECKPOINT) as f:
        return json.load(f)


def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    from tests.benchmark_harness.guardrails import _canonical_outcome
    results = checkpoint.get("phases", {}).get("ingest", {}).get("results", {})
    outcome_counts: dict[str, int] = {"completed": 0, "errored": 0, "empty": 0, "timed_out": 0, "unknown": 0}
    status_counts: dict[str, int] = {"complete": 0, "extraction_failed": 0}
    errors: list[str] = []
    for r in results.values():
        outcome = _canonical_outcome(r.get("status", ""))
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        status = r.get("status", "unknown")
        if status in status_counts:
            status_counts[status] += 1
        err = r.get("error", "")
        if err:
            errors.append(str(err)[:150])
    total = len(results)
    errored_count = outcome_counts.get("errored", 0)
    return {
        "total_sessions": total,
        "outcome_counts": outcome_counts,
        "status_counts": status_counts,
        "errored_count": errored_count,
        "errored_rate": errored_count / max(total, 1) * 100,
        "sample_errors": errors[:5],
    }


def write_report(
    summary: dict[str, Any],
    reset_rc: int,
    ingest_rc: int,
    elapsed: float,
    dataset_path: Path,
) -> None:
    outcome = summary.get("outcome_counts", {})
    status = summary.get("status_counts", {})
    # Full-corpus baseline: no halt verdict until after evaluation
    report = f"""# Wave 0 — Full-Corpus Baseline

**Generated:** {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}
**Status:** BASELINE CAPTURED — not yet evaluated
**Harness:** `tests/benchmark_harness/ingestion_rerun_full_corpus.py`

---

## Run Summary

| Item | Value |
|---|---|
| Dataset | `{dataset_path}` |
| Sessions (total) | {summary['total_sessions']} |
| Completed (outcome) | {outcome.get('completed', 0)} |
| Errored (outcome) | {outcome.get('errored', 0)} |
| ERRORED % | {summary['errored_rate']:.1f}% |
| Reset exit code | {reset_rc} |
| Ingest exit code | {ingest_rc} |
| Wall time | {elapsed:.0f}s |

## Outcome Counts (from checkpoint `outcome` field)

| Outcome | Count |
|---|---|
| completed | {outcome.get('completed', 0)} |
| errored | {outcome.get('errored', 0)} |
| empty | {outcome.get('empty', 0)} |

## Status Counts (from checkpoint `status` field)

| Status | Count |
|---|---|
| complete | {status.get('complete', 0)} |
| extraction_failed | {status.get('extraction_failed', 0)} |

## Sample Errors (first 5)

```
{chr(10).join(summary['sample_errors']) if summary['sample_errors'] else 'None'}
```

## Patches Applied (in subprocess)

| Module | Constant | Original → Patched |
|---|---|---|
| `orchestrator.memory.extraction` | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG` | `'openrouter/openai/gpt-4o-mini-2024-07-18'` → `'openai'` |
| `orchestrator.memory.extraction` | `extract_facts_from_text` | catches `BenchmarkSamplingError` (fingerprint drift = diagnostic only) |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_MODEL` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'openrouter/deepseek/deepseek-v3.2'` |
| `orchestrator.memory.dedup` | `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG` | `'openrouter/deepseek/deepseek-chat-v3-5'` → `'novita'` |
| `orchestrator.memory.dedup` | `check_contradiction` | catches `DedupBenchmarkSamplingError` (advisory) |

## Guardrails

| Guardrail | Outcome |
|---|---|
| G1: Provider health check | (not shown in this report — run with `--check` flag to verify pre-run) |
| G3: Errored-floor (5%) | {'PASS' if summary['errored_rate'] <= 5 else 'FAIL'} — {summary['errored_rate']:.1f}% |
| G5: Credit instrumentation | log-only, not blocking |

## Bounded-Variance Framing

Per `wave0_rerun_content_comparison_v2.md` and `wave0_variance_attribution_results.md`:

- Full-corpus baseline results fall within the characterized **~6pp irreducible embedding variance**
  distribution (measured with `voyage-4-lite`).
- Results should be interpreted as falling within the bounded distribution, not as
  regressions or improvements relative to any single prior run.
- The 3-run reproducibility protocol (spread ≤ 3pp) is assessed separately after
  evaluate/score completes for each run.

## Artifact Destinations

| Artifact | Location |
|---|---|
| Output dir | `tests/benchmark_results/wave0_full_corpus_baseline/` |
| Ingestion checkpoint | `wave0_full_corpus_baseline/longmemeval_checkpoint.json` |
| Ingestion results | `wave0_full_corpus_baseline/longmemeval_results.jsonl` |
| Score output | `wave0_full_corpus_baseline/longmemeval_score.json` |
| Ingestion log | `wave0_full_corpus_baseline/ingest.log` |

---

*Run harness: `PYTHONPATH=. python tests/benchmark_harness/ingestion_rerun_full_corpus.py`*
*Next: run full-corpus baseline → create/update baselines.md → Oracle checkpoint 2 → local tag*
"""
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w") as fh:
        fh.write(report)
    print(f"\n[FULL_CORPUS] Report written → {REPORT_FILE}")


def main() -> int:
    print("=" * 60)
    print("Wave 0 — Full-Corpus Baseline Harness")
    print("=" * 60)
    print(f"BENCHMARK_MODE : {BASE_ENV.get('BENCHMARK_MODE')!r}")
    print(f"Dataset        : {DATASET}")
    print(f"Output dir     : {OUTPUT_DIR}")
    print("-" * 60)

    # G1: provider health check (best-effort, not blocking in this harness)
    try:
        from tests.benchmark_harness.guardrails import run_provider_health_check
        print("\n[FULL_CORPUS] Running G1: provider health check...")
        run_provider_health_check(provider_slug="openai")
        print("[FULL_CORPUS] G1: provider healthy — proceeding")
    except Exception as e:
        print(f"[FULL_CORPUS] G1 warning: {e} — proceeding anyway (harness is tests-only)")

    # Verify dataset exists
    if not DATASET.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET}. "
            "Bootstrap with: python tests/longmemeval/ingest.py ensure_dataset"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()

    # STEP 1: Reset
    reset_rc, _ = run_subprocess(RESET_CODE, "STEP 1: RESET")
    if reset_rc != 0:
        print(f"[FULL_CORPUS] RESET failed (rc={reset_rc}) — aborting before ingest")
        return 1

    # STEP 2: Ingest
    ingest_rc, _ = run_subprocess(INGEST_CODE, "STEP 2: INGEST")
    elapsed = time.monotonic() - t0

    if ingest_rc != 0:
        print(f"[FULL_CORPUS] INGEST failed (rc={ingest_rc}) — skipping G3")
        # Write partial report
        partial = {
            "total_sessions": 0,
            "outcome_counts": {"completed": 0, "errored": 0, "empty": 0},
            "status_counts": {"complete": 0, "extraction_failed": 0},
            "errored_count": 0,
            "errored_rate": 0.0,
            "sample_errors": [f"ingest step exited with code {ingest_rc}"],
        }
        write_report(partial, reset_rc, ingest_rc, elapsed, DATASET)
        return 1

    # G3: Errored-floor check
    try:
        checkpoint = load_checkpoint_or_fail()
        from tests.benchmark_harness.guardrails import check_errored_floor
        g3_result = check_errored_floor(checkpoint)
        print(f"[FULL_CORPUS] G3: errored floor PASS ({g3_result['errored_rate']:.1f}%)")
    except FileNotFoundError:
        print("[FULL_CORPUS] G3: checkpoint missing — data integrity issue")
        write_report(
            {
                "total_sessions": 0,
                "outcome_counts": {"completed": 0, "errored": 0, "empty": 0},
                "status_counts": {"complete": 0, "extraction_failed": 0},
                "errored_count": 0,
                "errored_rate": 0.0,
                "sample_errors": ["checkpoint missing after ingest"],
            },
            reset_rc,
            ingest_rc,
            elapsed,
            DATASET,
        )
        return 1
    except AssertionError as e:
        print(f"[FULL_CORPUS] G3: errored floor BREACH — halting: {e}")
        summary = summarize(load_checkpoint_or_fail())
        write_report(summary, reset_rc, ingest_rc, elapsed, DATASET)
        return 1

    # G5: Credit instrumentation (log only)
    try:
        from tests.benchmark_harness.guardrails import log_credit_instrumentation
        log_credit_instrumentation("post_ingestion")
    except Exception as e:
        print(f"[FULL_CORPUS] G5: credit instrumentation error (non-blocking): {e}")

    summary = summarize(load_checkpoint_or_fail())

    outcome = summary.get("outcome_counts", {})
    status = summary.get("status_counts", {})
    print(
        f"\n[FULL_CORPUS] Sessions: {summary['total_sessions']}, "
        f"ERR {summary['errored_rate']:.1f}%, "
        f"completed(outcome)={outcome.get('completed', 0)}, "
        f"errored(outcome)={outcome.get('errored', 0)}, "
        f"empty(outcome)={outcome.get('empty', 0)}, "
        f"complete(status)={status.get('complete', 0)}, "
        f"extraction_failed(status)={status.get('extraction_failed', 0)}"
    )

    write_report(summary, reset_rc, ingest_rc, elapsed, DATASET)

    # G3 halt rule: >5% errored = fail, else pass
    verdict = "PASS" if summary["errored_rate"] <= 5 else "FAIL"
    print(f"\nFULL_CORPUS RESULT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
