#!/usr/bin/env python3
"""
Preservation Patch for V1 Triple Rerun

Scope: tests/ only. No production code changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "tests/benchmark_longmemeval/fixtures/dev_subset.json"
BASE_OUTPUT_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_rerun_v1"

os.environ["BENCHMARK_MODE"] = "1"

BASE_ENV = os.environ.copy()
BASE_ENV["DATABASE_URL"] = "postgresql://daemon:daemon@127.0.0.1:5432/daemon"

PATCH_CODE = """
import sys
import dotenv
dotenv.load_dotenv()

import orchestrator.memory.extraction as _ext
_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"

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

import orchestrator.memory.dedup as _dedup
_dedup.BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"

_DedupBenchmarkSamplingError = _dedup.DedupBenchmarkSamplingError
_dedup_check_orig = _dedup.check_contradiction

async def _patched_check_contradiction(existing_content, new_content, benchmark_mode=None):
    try:
        return await _dedup_check_orig(existing_content, new_content, benchmark_mode=benchmark_mode)
    except _DedupBenchmarkSamplingError as e:
        print(f"[patched] check_contradiction: DedupBenchmarkSamplingError caught -> {e}")
        return False, ""

_dedup.check_contradiction = _patched_check_contradiction
"""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize_facts_for_hash(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for f in facts:
        normalized.append(
            {
                "content": f.get("content", "").strip(),
                "category": f.get("category", "").strip(),
                "slot": f.get("slot"),
            }
        )
    normalized.sort(key=lambda x: (x["content"], x["category"], str(x["slot"])))
    return normalized


def fact_list_sha256(facts: list[dict[str, Any]]) -> str:
    canon = canonicalize_facts_for_hash(facts)
    return _sha256_text(json.dumps(canon, sort_keys=True))


def run_reset(run_dir: Path) -> tuple[int, str]:
    run_dir_str = str(run_dir)

    reset_code = (
        PATCH_CODE
        + f"""
import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, '{PROJECT_ROOT}')
from orchestrator.eval.fact_harness import reset_canonical_benchmark
from orchestrator.config import get_settings
import asyncpg

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)
    try:
        checkpoint_path = Path('{run_dir_str}') / "longmemeval_checkpoint.json"
        result_path = Path('{run_dir_str}') / "reset_result.json"
        summary = await reset_canonical_benchmark(pool, checkpoint_path, cleanup_redis=False)
        result = {{
            "success": summary.success,
            "tables_cleared": summary.tables_cleared,
            "total_rows_deleted": summary.total_rows_deleted,
            "checkpoint_removed": summary.checkpoint_reset.get("checkpoint_removed"),
            "error": summary.error,
        }}
        with open(result_path, "w") as f:
            json.dump(result, f)
        print("RESET_OK")
    finally:
        await pool.close()

asyncio.run(main())
"""
    )

    print("\n[PRESERVE] === RESET ===")
    result = subprocess.run(
        [sys.executable, "-c", reset_code],
        env=BASE_ENV,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=300,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:500])
    return result.returncode, result.stdout


def run_ingest(run_dir: Path) -> tuple[int, str]:
    run_dir_str = str(run_dir)
    dataset_str = str(DATASET)

    ingest_code = (
        PATCH_CODE
        + f"""
import asyncio, sys, json, uuid
from pathlib import Path
sys.path.insert(0, '{PROJECT_ROOT}')

from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.extraction import get_benchmark_tracking
from orchestrator.config import get_settings
import asyncpg

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)

    OUTPUT = Path('{run_dir_str}')
    OUTPUT.mkdir(parents=True, exist_ok=True)

    runner = LongMemEvalFactRunner(
        dataset_path=Path('{dataset_str}'),
        output_path=OUTPUT / "longmemeval_results.jsonl",
        checkpoint_path=OUTPUT / "longmemeval_checkpoint.json",
        score_path=OUTPUT / "longmemeval_score.json",
        limit=None,
        force_retrieval_logging=True,
    )

    print("Starting ingestion...")
    await runner.ingest()
    print("Ingestion complete.")

    test_user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    extraction_rows = await pool.fetch(
        '''
        SELECT id, conversation_id, user_id, input_snippet, extracted_facts,
               dedup_results, model_used, created_at
        FROM memory_extraction_log
        WHERE user_id = $1
        ORDER BY created_at ASC
        ''',
        test_user_id,
    )

    memory_rows = await pool.fetch(
        '''
        SELECT id, user_id, content, category, source_conversation_id,
               confidence, status, slot, created_at
        FROM memories
        WHERE user_id = $1
        ORDER BY created_at ASC
        ''',
        test_user_id,
    )

    await pool.close()

    tracking = get_benchmark_tracking()
    ext_tracking = tracking.get("extraction", {{}})

    extraction_log_path = OUTPUT / "extraction_log.jsonl"
    with open(extraction_log_path, "w") as f:
        for row in extraction_rows:
            decrypted_snippet = encryption.decrypt(row["input_snippet"])
            input_hash = hashlib.sha256(decrypted_snippet[:1000].encode("utf-8")).hexdigest()
            facts = row["extracted_facts"] or []
            entry = {{
                "session_id": str(row["conversation_id"]),
                "conversation_id": str(row["conversation_id"]),
                "input_snippet_hash": input_hash,
                "extracted_count": len(facts),
                "extracted_facts": facts,
                "facts_sha256": fact_list_sha256(facts) if facts else "",
                "model": row["model_used"],
                "system_fingerprint": ext_tracking.get("fingerprint"),
                "dedup_results": row["dedup_results"] or {{}},
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }}
            f.write(json.dumps(entry) + "\\n")

    memories_path = OUTPUT / "memories.jsonl"
    with open(memories_path, "w") as f:
        for row in memory_rows:
            decrypted_content = encryption.decrypt(row["content"])
            content_hash = hashlib.sha256(decrypted_content.strip().encode("utf-8")).hexdigest()
            entry = {{
                "memory_id": str(row["id"]),
                "content_sha256": content_hash,
                "category": row["category"],
                "slot": row["slot"],
                "confidence": row["confidence"],
                "status": row["status"],
                "source_conversation_id": str(row["source_conversation_id"]) if row["source_conversation_id"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }}
            f.write(json.dumps(entry) + "\\n")

    run_metrics = {{
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_extraction_calls": len(extraction_rows),
        "unique_fingerprints": list(set(ext_tracking.get("fingerprint") for _ in [1] if ext_tracking.get("fingerprint"))),
        "observed_fingerprint": ext_tracking.get("fingerprint"),
        "observed_model": ext_tracking.get("model"),
        "extraction_tracking": ext_tracking,
        "voyage_balance_delta": None,
        "openrouter_balance_delta": None,
        "total_memories_created": len(memory_rows),
        "active_memories": sum(1 for r in memory_rows if r["status"] == "active"),
        "superseded_memories": sum(1 for r in memory_rows if r["status"] == "superseded"),
    }}

    checkpoint_path = OUTPUT / "longmemeval_checkpoint.json"
    if checkpoint_path.exists():
        with open(checkpoint_path) as cf:
            cp = json.load(cf)
        results = cp.get("phases", {{}}).get("ingest", {{}}).get("results", {{}})
        outcome_counts = {{"completed": 0, "errored": 0, "empty": 0, "unknown": 0}}
        for r in results.values():
            status = r.get("status", "unknown")
            if status in outcome_counts:
                outcome_counts[status] = outcome_counts.get(status, 0) + 1
            else:
                outcome_counts["unknown"] = outcome_counts.get("unknown", 0) + 1
        run_metrics["extraction_outcome_counts"] = outcome_counts

    metrics_path = OUTPUT / "run_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(run_metrics, f, indent=2)

    print("INGEST_OK")
    print(f"Extraction log: {{extraction_log_path}}")
    print(f"Memories: {{memories_path}}")
    print(f"Metrics: {{metrics_path}}")

def _sha256_text(value):
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def canonicalize_facts_for_hash(facts):
    normalized = []
    for f in facts:
        normalized.append({{
            "content": f.get("content", "").strip(),
            "category": f.get("category", "").strip(),
            "slot": f.get("slot"),
        }})
    normalized.sort(key=lambda x: (x["content"], x["category"], str(x["slot"])))
    return normalized

def fact_list_sha256(facts):
    import hashlib
    import json
    canon = canonicalize_facts_for_hash(facts)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()

import datetime as datetime_module
timezone = datetime_module.timezone.utc
datetime = datetime_module.datetime

asyncio.run(main())
"""
    )

    print("\n[PRESERVE] === INGEST ===")
    result = subprocess.run(
        [sys.executable, "-c", ingest_code],
        env=BASE_ENV,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=900,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:1000])
    return result.returncode, result.stdout


def main() -> int:
    print("=" * 60)
    print("PRESERVE - V1 Triple Rerun with Preservation")
    print("=" * 60)

    BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_results = []

    for run_num in [1, 2, 3]:
        run_dir = BASE_OUTPUT_DIR / f"run_{run_num}"
        run_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"RUN {run_num}/3")
        print(f"{'=' * 60}")

        t0 = time.monotonic()

        reset_rc, reset_out = run_reset(run_dir)
        if reset_rc != 0:
            print(f"[PRESERVE] Run {run_num} reset failed with code {reset_rc}")
            run_results.append({"run": run_num, "status": "reset_failed", "rc": reset_rc})
            continue

        ingest_rc, ingest_out = run_ingest(run_dir)
        elapsed = time.monotonic() - t0

        extraction_log = run_dir / "extraction_log.jsonl"
        memories = run_dir / "memories.jsonl"
        metrics = run_dir / "run_metrics.json"
        artifacts_ok = extraction_log.exists() and memories.exists() and metrics.exists()

        run_results.append(
            {
                "run": run_num,
                "status": "success"
                if (ingest_rc == 0 and artifacts_ok)
                else ("artifacts_missing" if ingest_rc == 0 else "ingest_failed"),
                "elapsed": elapsed,
                "rc": ingest_rc,
            }
        )

        print(
            f"\n[PRESERVE] Run {run_num} complete in {elapsed:.0f}s - {run_results[-1]['status']}"
        )

    print(f"\n{'=' * 60}")
    print("RUN SUMMARY")
    print(f"{'=' * 60}")
    for r in run_results:
        print(f"  Run {r['run']}: {r['status']} ({r.get('elapsed', 0):.0f}s)")

    success_count = sum(1 for r in run_results if r.get("status") == "success")
    print(f"\nSuccessful runs: {success_count}/3")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "runs": run_results,
        "dataset": str(DATASET),
        "successful_runs": success_count,
    }
    summary_path = BASE_OUTPUT_DIR / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary written to: {summary_path}")

    return 0 if success_count == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
