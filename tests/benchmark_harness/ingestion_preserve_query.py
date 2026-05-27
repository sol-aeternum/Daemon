#!/usr/bin/env python3
"""
Preservation Query Script - runs after ingestion to generate preservation artifacts.

Scope: tests/ only. No production code changes.

Run after ingestion completes:
    PYTHONPATH=. python tests/benchmark_harness/ingestion_preserve_query.py <run_dir>
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
dotenv.load_dotenv()


def canonicalize_facts_for_hash(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for f in facts:
        normalized.append({
            "content": f.get("content", "").strip(),
            "category": f.get("category", "").strip(),
            "slot": f.get("slot"),
        })
    normalized.sort(key=lambda x: (x["content"], x["category"], str(x["slot"])))
    return normalized


def fact_list_sha256(facts: list[dict[str, Any]]) -> str:
    canon = canonicalize_facts_for_hash(facts)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


async def query_preservation_data(pool, test_user_id, encryption) -> tuple[list, list, dict]:
    extraction_rows = await pool.fetch(
        """
        SELECT id, conversation_id, user_id, input_snippet, extracted_facts,
               dedup_results, model_used, created_at
        FROM memory_extraction_log
        WHERE user_id = $1
        ORDER BY created_at ASC
        """,
        test_user_id,
    )

    memory_rows = await pool.fetch(
        """
        SELECT id, user_id, content, category, source_conversation_id,
               confidence, status, slot, created_at
        FROM memories
        WHERE user_id = $1
        ORDER BY created_at ASC
        """,
        test_user_id,
    )

    from orchestrator.memory.extraction import get_benchmark_tracking
    tracking = get_benchmark_tracking()
    ext_tracking = tracking.get("extraction", {})

    return extraction_rows, memory_rows, ext_tracking


async def write_preservation_artifacts(run_dir: Path):
    from orchestrator.config import get_settings
    from orchestrator.memory.encryption import ContentEncryption
    import asyncpg

    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)
    test_user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    extraction_rows, memory_rows, ext_tracking = await query_preservation_data(pool, test_user_id, encryption)
    await pool.close()

    extraction_log_path = run_dir / "extraction_log.jsonl"
    with open(extraction_log_path, "w") as f:
        for row in extraction_rows:
            decrypted_snippet = encryption.decrypt(row["input_snippet"])
            input_hash = hashlib.sha256(decrypted_snippet[:1000].encode("utf-8")).hexdigest()
            facts = row["extracted_facts"] or []
            entry = {
                "session_id": str(row["conversation_id"]),
                "conversation_id": str(row["conversation_id"]),
                "input_snippet_hash": input_hash,
                "extracted_count": len(facts),
                "extracted_facts": facts,
                "facts_sha256": fact_list_sha256(facts) if facts else "",
                "model": row["model_used"],
                "system_fingerprint": ext_tracking.get("fingerprint"),
                "dedup_results": row["dedup_results"] or {},
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            f.write(json.dumps(entry) + "\n")

    memories_path = run_dir / "memories.jsonl"
    with open(memories_path, "w") as f:
        for row in memory_rows:
            decrypted_content = encryption.decrypt(row["content"])
            content_hash = hashlib.sha256(decrypted_content.strip().encode("utf-8")).hexdigest()
            entry = {
                "memory_id": str(row["id"]),
                "content_sha256": content_hash,
                "category": row["category"],
                "slot": row["slot"],
                "confidence": row["confidence"],
                "status": row["status"],
                "source_conversation_id": str(row["source_conversation_id"]) if row["source_conversation_id"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            f.write(json.dumps(entry) + "\n")

    checkpoint_path = run_dir / "longmemeval_checkpoint.json"
    outcome_counts = {"completed": 0, "errored": 0, "empty": 0, "unknown": 0}
    if checkpoint_path.exists():
        with open(checkpoint_path) as cf:
            cp = json.load(cf)
        results = cp.get("phases", {}).get("ingest", {}).get("results", {})
        for r in results.values():
            status = r.get("status", "unknown")
            if status in outcome_counts:
                outcome_counts[status] += 1
            else:
                outcome_counts["unknown"] += 1

    run_metrics = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_extraction_calls": len(extraction_rows),
        "unique_fingerprints": list(set(ext_tracking.get("fingerprint") for _ in [1] if ext_tracking.get("fingerprint"))),
        "observed_fingerprint": ext_tracking.get("fingerprint"),
        "observed_model": ext_tracking.get("model"),
        "extraction_tracking": dict(ext_tracking),
        "voyage_balance_delta": None,
        "openrouter_balance_delta": None,
        "total_memories_created": len(memory_rows),
        "active_memories": sum(1 for r in memory_rows if r["status"] == "active"),
        "superseded_memories": sum(1 for r in memory_rows if r["status"] == "superseded"),
        "extraction_outcome_counts": outcome_counts,
    }

    metrics_path = run_dir / "run_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(run_metrics, f, indent=2)

    return len(extraction_rows), len(memory_rows)


def main():
    if len(sys.argv) < 2:
        print("Usage: python ingestion_preserve_query.py <run_dir>")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    print(f"Generating preservation artifacts for: {run_dir}")

    row_counts = asyncio.run(write_preservation_artifacts(run_dir))
    print(f"Extraction log: {run_dir / 'extraction_log.jsonl'} ({row_counts[0]} rows)")
    print(f"Memories: {run_dir / 'memories.jsonl'} ({row_counts[1]} rows)")
    print(f"Metrics: {run_dir / 'run_metrics.json'}")
    print("Preservation artifacts generated successfully")


if __name__ == "__main__":
    main()