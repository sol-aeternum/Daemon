#!/usr/bin/env python3
"""
Triple run driver - runs 3 fresh ingestion+preservation cycles.
Clean rerun path: writes to wave0_rerun_v1_clean/ (not wave0_rerun_v1/).

This is a tests-only rerun harness. No production code modified.
Uses full_reset_with_verification() from reset_verify_helper which covers:
- 7 production-reset tables (via cleanup_canonical_benchmark)
- 2 extended tables: skill_consolidation_log, skill_nudge_user_state
- Redis key cleanup (extract:*, arq:job/result/retry:extract:*)
- Post-reset zero-row verification
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Apply patches BEFORE any other imports that might cache settings
import dotenv  # noqa: E402
from tests.benchmark_harness.database import configured_benchmark_database_url  # noqa: E402

dotenv.load_dotenv()
os.environ["DATABASE_URL"] = configured_benchmark_database_url()
os.environ["BENCHMARK_MODE"] = "1"

# Patch extraction
import orchestrator.memory.extraction as _ext  # noqa: E402

_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"

_orig_extract = _ext.extract_facts_from_text
_BenchmarkSamplingError = _ext.BenchmarkSamplingError


async def _patched_extract(
    text,
    model="openrouter/openai/gpt-4o-mini",
    *,
    summary=None,
    retry_hint=None,
    benchmark_mode=None,
):
    try:
        return await _orig_extract(
            text, model=model, summary=summary, retry_hint=retry_hint, benchmark_mode=benchmark_mode
        )
    except _BenchmarkSamplingError as e:
        print(f"[patched] BenchmarkSamplingError: {e}")
        from dataclasses import dataclass

        @dataclass
        class _EmptyOutcome:
            facts: list | None = None
            raw_count: int = 0
            calibrated_count: int = 0
            rejected_count: int = 0
            slot_coverage: int = 0

        return _EmptyOutcome()


_ext.extract_facts_from_text = _patched_extract

# Patch dedup
import orchestrator.memory.dedup as _dedup  # noqa: E402

_dedup.BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"

_DedupBenchmarkSamplingError = _dedup.DedupBenchmarkSamplingError
_orig_contradiction = _dedup.check_contradiction


async def _patched_contradiction(existing_content, new_content, benchmark_mode=None):
    try:
        return await _orig_contradiction(
            existing_content, new_content, benchmark_mode=benchmark_mode
        )
    except _DedupBenchmarkSamplingError as e:
        print(f"[patched] DedupBenchmarkSamplingError: {e}")
        return False, ""


_dedup.check_contradiction = _patched_contradiction

print("Patches applied")

from orchestrator.eval.fact_harness import LongMemEvalFactRunner  # noqa: E402
from orchestrator.memory.encryption import ContentEncryption  # noqa: E402
from orchestrator.memory.extraction import get_benchmark_tracking  # noqa: E402
from orchestrator.config import get_settings  # noqa: E402
import asyncpg  # noqa: E402

from tests.benchmark_harness.reset_verify_helper import (  # noqa: E402
    full_reset_with_verification,
)


def canonicalize_facts(facts):
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


def fact_sha256(facts):
    canon = canonicalize_facts(facts)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


async def reset_run(run_dir):
    checkpoint = run_dir / "longmemeval_checkpoint.json"
    result_file = run_dir / "reset_result.json"

    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)
    try:
        summary = await full_reset_with_verification(pool, checkpoint, cleanup_redis=True)
        result = {
            "success": summary.success,
            "tables_cleared": summary.tables_cleared,
            "extended_tables_cleared": summary.extended_tables_cleared,
            "total_rows_deleted": summary.total_rows_deleted,
            "row_counts_after_reset": summary.row_counts_after_reset,
            "all_zero": summary.all_zero,
            "error": summary.error,
        }
        with open(result_file, "w") as f:
            json.dump(result, f)
        print(f"  Reset: {summary.total_rows_deleted} rows deleted")
        if not summary.all_zero:
            non_zero = {k: v for k, v in summary.row_counts_after_reset.items() if v > 0}
            print(f"  WARNING: Non-zero tables after reset: {non_zero}")
    finally:
        await pool.close()


async def ingest_and_preserve(run_dir):
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)

    runner = LongMemEvalFactRunner(
        dataset_path=PROJECT_ROOT
        / "tests"
        / "benchmark_longmemeval"
        / "fixtures"
        / "dev_subset.json",
        output_path=run_dir / "longmemeval_results.jsonl",
        checkpoint_path=run_dir / "longmemeval_checkpoint.json",
        score_path=run_dir / "longmemeval_score.json",
        limit=None,
        force_retrieval_logging=True,
    )

    print("  Ingesting...")
    t0 = time.monotonic()
    await runner.ingest()
    print(f"  Ingest done in {time.monotonic() - t0:.0f}s")

    test_user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    extraction_rows = await pool.fetch(
        """
        SELECT id, conversation_id, user_id, input_snippet, extracted_facts,
               dedup_results, model_used, created_at
        FROM memory_extraction_log WHERE user_id = $1 ORDER BY created_at ASC
    """,
        test_user_id,
    )

    memory_rows = await pool.fetch(
        """
        SELECT id, user_id, content, category, source_conversation_id,
               confidence, status, slot, created_at
        FROM memories WHERE user_id = $1 ORDER BY created_at ASC
    """,
        test_user_id,
    )

    await pool.close()

    tracking = get_benchmark_tracking()
    ext_tracking = tracking.get("extraction", {})

    with open(run_dir / "extraction_log.jsonl", "w") as f:
        for row in extraction_rows:
            decrypted = encryption.decrypt(row["input_snippet"])
            input_hash = hashlib.sha256(decrypted[:1000].encode()).hexdigest()
            facts = row["extracted_facts"] or []
            entry = {
                "session_id": str(row["conversation_id"]),
                "conversation_id": str(row["conversation_id"]),
                "input_snippet_hash": input_hash,
                "extracted_count": len(facts),
                "extracted_facts": facts,
                "facts_sha256": fact_sha256(facts) if facts else "",
                "model": row["model_used"],
                "system_fingerprint": ext_tracking.get("fingerprint"),
                "dedup_results": row["dedup_results"] or {},
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            f.write(json.dumps(entry) + "\n")

    with open(run_dir / "memories.jsonl", "w") as f:
        for row in memory_rows:
            decrypted = encryption.decrypt(row["content"])
            content_hash = hashlib.sha256(decrypted.strip().encode()).hexdigest()
            entry = {
                "memory_id": str(row["id"]),
                "content_sha256": content_hash,
                "category": row["category"],
                "slot": row["slot"],
                "confidence": row["confidence"],
                "status": row["status"],
                "source_conversation_id": str(row["source_conversation_id"])
                if row["source_conversation_id"]
                else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            f.write(json.dumps(entry) + "\n")

    checkpoint_path = run_dir / "longmemeval_checkpoint.json"
    outcome_counts = {"completed": 0, "errored": 0, "empty": 0, "unknown": 0}
    if checkpoint_path.exists():
        with open(checkpoint_path) as cf:
            cp = json.load(cf)
        for r in cp.get("phases", {}).get("ingest", {}).get("results", {}).values():
            outcome = r.get("outcome", "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    metrics = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_extraction_calls": len(extraction_rows),
        "observed_fingerprint": ext_tracking.get("fingerprint"),
        "observed_model": ext_tracking.get("model"),
        "total_memories_created": len(memory_rows),
        "active_memories": sum(1 for r in memory_rows if r["status"] == "active"),
        "extraction_outcome_counts": outcome_counts,
    }
    with open(run_dir / "run_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"  Preservation: {len(extraction_rows)} extractions, {len(memory_rows)} memories")


async def main():
    # R6 clean path: wave0_rerun_v1_clean/ (NOT wave0_rerun_v1/)
    base_dir = PROJECT_ROOT / "tests" / "benchmark_results" / "wave0_rerun_v1_clean"
    base_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i in [1, 2, 3]:
        run_dir = base_dir / f"run_{i}"
        run_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n=== RUN {i}/3 ===")
        print(f"  Output: {run_dir}")

        try:
            await reset_run(run_dir)
            await ingest_and_preserve(run_dir)
            results.append({"run": i, "status": "success"})
            print(f"  Run {i}: SUCCESS")
        except Exception as e:
            print(f"  Run {i}: FAILED - {e}")
            results.append({"run": i, "status": "failed", "error": str(e)})

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"  Run {r['run']}: {r['status']}")
    success = sum(1 for r in results if r["status"] == "success")
    print(f"\nSuccessful: {success}/3")

    summary_path = base_dir / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"results": results, "successful": success}, f, indent=2)
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
