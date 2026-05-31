#!/usr/bin/env python3
"""
Preservation query script - extracts preservation artifacts from existing run data.
"""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import dotenv  # noqa: E402

dotenv.load_dotenv()
os.environ["DATABASE_URL"] = "postgresql://daemon:daemon@127.0.0.1:5432/daemon"
os.environ["BENCHMARK_MODE"] = "1"


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


async def query_preservation(run_dir):
    from orchestrator.config import get_settings
    from orchestrator.memory.encryption import ContentEncryption
    from orchestrator.memory.extraction import get_benchmark_tracking
    import asyncpg

    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)
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
               confidence, status, memory_slot, created_at
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
            raw_facts = row["extracted_facts"]
            if isinstance(raw_facts, str):
                facts = json.loads(raw_facts) if raw_facts else []
            else:
                facts = raw_facts or []
            entry = {
                "session_id": str(row["conversation_id"]),
                "conversation_id": str(row["conversation_id"]),
                "input_snippet_hash": input_hash,
                "extracted_count": len(facts),
                "extracted_facts": facts,
                "facts_sha256": fact_sha256(facts) if facts else "",
                "model": row["model_used"],
                "system_fingerprint": ext_tracking.get("fingerprint"),
                "dedup_results": row["dedup_results"],
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
                "slot": row["memory_slot"],
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

    return len(extraction_rows), len(memory_rows)


async def main():
    base = PROJECT_ROOT / "tests" / "benchmark_results" / "wave0_rerun_v1"

    for run_num in [1, 2, 3]:
        run_dir = base / f"run_{run_num}"
        print(f"\n=== Processing {run_dir} ===")

        if not run_dir.exists():
            print("  Skipping - directory does not exist")
            continue

        checkpoint = run_dir / "longmemeval_checkpoint.json"
        if not checkpoint.exists():
            print("  Skipping - no checkpoint found")
            continue

        try:
            ext_count, mem_count = await query_preservation(run_dir)
            print(f"  Preservation: {ext_count} extractions, {mem_count} memories")

            with open(run_dir / "run_metrics.json") as f:
                metrics = json.load(f)
            print(f"  Outcome counts: {metrics.get('extraction_outcome_counts')}")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
