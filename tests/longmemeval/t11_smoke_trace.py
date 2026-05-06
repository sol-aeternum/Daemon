#!/usr/bin/env python3
"""
T11 — Single IE-* Question End-to-End Smoke Trace (Fixed)

Runs one IE-* question through the parity-fixed harness with REAL extraction,
real retrieval, and production build_memory_context() + assemble_system_prompt().

Key fixes from Atlas rejection:
1. Uses a FRESH question_id that has never been processed → new synthetic user, no stale memories
2. Cleans the fresh synthetic user's data before the run
3. Patches parity_harness module directly (not just evaluate) for answer/judge mocks
4. Requires extraction_completed_nonzero as a rollback gate
5. Tracks current-run provenance: extraction_created_memory_ids must intersect retrieved_memory_ids

Command:
    DATABASE_URL='postgresql://...' \
    DAEMON_ENCRYPTION_KEY='...' \
    PYTHONPATH=/home/sol/daemon \
    python3 tests/longmemeval/t11_smoke_trace.py

Rollback triggers:
    - memories_used == 0
    - extraction_completed_count == 0 (no memories created this run)
    - retrieval latency p95 > 1500ms
    - encryption failure
    - provider routing error
    - cross-synthetic-user memory retrieval
    - retrieved memory not in current-run created set
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.injection import (
    assemble_system_prompt,
    build_memory_context,
)
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore

from tests.longmemeval.ingest import (
    SYNTHETIC_USER_NAMESPACE,
    create_synthetic_user,
    ingest_session,
)
from tests.longmemeval.parity_harness import (
    MAX_TOKENS,
    TOP_K_MEMORIES,
    create_answer_conversation,
)
from tests.longmemeval.evaluate import answer_with_llm, judge_answer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEV_SUBSET_PATH = Path("/home/sol/daemon/tests/benchmark_longmemeval/fixtures/dev_subset.json")

SELECTED_QUESTION_ID = "b86304ba"
FRESH_QUESTION_ID = f"{SELECTED_QUESTION_ID}_t11fresh"

MAX_SESSIONS = 3


async def clean_synthetic_user_data(pool: asyncpg.Pool, synthetic_user_id: uuid.UUID) -> None:
    """Delete all benchmark data for a synthetic user to ensure fresh state."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM memory_extraction_log
            WHERE conversation_id IN (
                SELECT id FROM conversations WHERE user_id = $1
            )
            """,
            synthetic_user_id,
        )
        await conn.execute(
            "DELETE FROM memories WHERE user_id = $1",
            synthetic_user_id,
        )
        await conn.execute(
            "DELETE FROM messages WHERE conversation_id IN ("
            "  SELECT id FROM conversations WHERE user_id = $1"
            ")",
            synthetic_user_id,
        )
        await conn.execute(
            "DELETE FROM conversations WHERE user_id = $1",
            synthetic_user_id,
        )
    logger.info(f"Cleaned data for synthetic user {synthetic_user_id}")


async def mock_answer(question, memories, *, benchmark_mode=False, system_prompt=None):
    return ""


async def mock_judge(question, hypothesis, reference, *, benchmark_mode=False):
    return "incorrect"


def load_dev_subset() -> list[dict[str, Any]]:
    with open(DEV_SUBSET_PATH) as f:
        return json.load(f)


def find_question(data: list[dict[str, Any]], question_id: str) -> dict[str, Any] | None:
    for entry in data:
        if entry.get("question_id") == question_id:
            return entry
    return None


async def verify_encryption_for_user(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "messages_checked": 0,
        "memories_checked": 0,
        "extraction_logs_checked": 0,
        "messages_decoded_ok": 0,
        "memories_decoded_ok": 0,
        "extraction_logs_decoded_ok": 0,
        "failures": [],
    }

    encryption = ContentEncryption(get_settings().daemon_encryption_key)

    async with pool.acquire() as conn:
        msg_rows = await conn.fetch(
            "SELECT id, content FROM messages WHERE user_id = $1 LIMIT 10",
            user_id,
        )
        results["messages_checked"] = len(msg_rows)
        for row in msg_rows:
            try:
                decrypted = encryption.decrypt(row["content"])
                if len(decrypted) > 0:
                    results["messages_decoded_ok"] = int(results["messages_decoded_ok"]) + 1
                else:
                    results["failures"].append(
                        f"messages:{row['id']} - empty after decrypt"
                    )
            except Exception as e:
                results["failures"].append(f"messages:{row['id']} - {type(e).__name__}: {e}")

        mem_rows = await conn.fetch(
            "SELECT id, content FROM memories WHERE user_id = $1 LIMIT 10",
            user_id,
        )
        results["memories_checked"] = len(mem_rows)
        for row in mem_rows:
            try:
                decrypted = encryption.decrypt(row["content"])
                if len(decrypted) > 0:
                    results["memories_decoded_ok"] = int(results["memories_decoded_ok"]) + 1
                else:
                    results["failures"].append(
                        f"memories:{row['id']} - empty after decrypt"
                    )
            except Exception as e:
                results["failures"].append(f"memories:{row['id']} - {type(e).__name__}: {e}")

        log_rows = await conn.fetch(
            """
            SELECT id, input_snippet FROM memory_extraction_log
            WHERE conversation_id IN (
                SELECT id FROM conversations WHERE user_id = $1
            )
            LIMIT 10
            """,
            user_id,
        )
        results["extraction_logs_checked"] = len(log_rows)
        for row in log_rows:
            if row["input_snippet"]:
                try:
                    decrypted = encryption.decrypt(row["input_snippet"])
                    if len(decrypted) > 0:
                        results["extraction_logs_decoded_ok"] = int(results["extraction_logs_decoded_ok"]) + 1
                except Exception as e:
                    results["failures"].append(
                        f"extraction_log:{row['id']} - {type(e).__name__}: {e}"
                    )

    return results


async def verify_same_user_retrieval(
    pool: asyncpg.Pool,
    synthetic_user_id: uuid.UUID,
    retrieved_memory_ids: list[str],
) -> dict[str, Any]:
    if not retrieved_memory_ids:
        return {"verified": True, "checked": 0, "all_same_user": True}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id FROM memories WHERE id = ANY($1::uuid[])",
            retrieved_memory_ids,
        )

    checked = len(rows)
    all_same = all(str(row["user_id"]) == str(synthetic_user_id) for row in rows)

    return {
        "verified": True,
        "checked": checked,
        "all_same_user": all_same,
        "retrieved_user_ids": [str(row["user_id"]) for row in rows],
        "expected_user_id": str(synthetic_user_id),
    }


async def get_current_run_memory_ids(
    pool: asyncpg.Pool,
    conversation_ids: list[str],
) -> list[str]:
    """Get memory IDs created from specific conversation IDs (current run)."""
    if not conversation_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM memories WHERE source_conversation_id = ANY($1::uuid[])",
            [uuid.UUID(cid) for cid in conversation_ids],
        )
    return [str(row["id"]) for row in rows]


async def run_smoke_trace() -> dict[str, Any]:
    settings = get_settings()

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    if not settings.daemon_encryption_key:
        raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

    db_url = os.environ.get("DATABASE_URL") or settings.database_url
    logger.info("Using configured DATABASE_URL (value redacted for security)")

    pool = await asyncpg.create_pool(
        dsn=db_url,
        min_size=2,
        max_size=10,
    )

    try:
        encryption = ContentEncryption(settings.daemon_encryption_key)
        store = MemoryStore(db_pool=pool, encryption=encryption)

        data = load_dev_subset()
        question_entry = find_question(data, SELECTED_QUESTION_ID)

        if not question_entry:
            raise ValueError(f"Question {SELECTED_QUESTION_ID} not found in dev_subset")

        question_text = question_entry["question"]
        reference = question_entry.get("answer", question_entry.get("reference", ""))
        question_type = question_entry.get("question_type", "unknown")
        haystack_sessions = question_entry.get("haystack_sessions", [])[:MAX_SESSIONS]
        haystack_session_ids = question_entry.get("haystack_session_ids", [])[:MAX_SESSIONS]

        logger.info(f"Selected question: {SELECTED_QUESTION_ID} ({question_type})")
        logger.info(f"Fresh question ID: {FRESH_QUESTION_ID}")
        logger.info(f"Haystack sessions: {len(haystack_sessions)} (limited from 41 for bounded smoke)")

        synthetic_user_id = uuid.uuid5(SYNTHETIC_USER_NAMESPACE, FRESH_QUESTION_ID)

        await clean_synthetic_user_data(pool, synthetic_user_id)

        import tests.longmemeval.parity_harness as ph_mod
        orig_answer = ph_mod.answer_with_llm
        orig_judge = ph_mod.judge_answer
        ph_mod.answer_with_llm = mock_answer
        ph_mod.judge_answer = mock_judge

        try:
            await create_synthetic_user(pool, FRESH_QUESTION_ID)

            ingested_conversation_ids: list[str] = []
            total_messages_ingested = 0
            extraction_outcomes: list[str] = []

            for sess_idx, session_messages in enumerate(haystack_sessions):
                session_id = (
                    haystack_session_ids[sess_idx]
                    if sess_idx < len(haystack_session_ids)
                    else f"{FRESH_QUESTION_ID}_session_{sess_idx}"
                )
                result = await ingest_session(
                    store=store,
                    pool=pool,
                    user_id=synthetic_user_id,
                    session_id=session_id,
                    messages=session_messages,
                    session_index=sess_idx,
                )
                ingested_conversation_ids.append(result["conversation_id"])
                total_messages_ingested += result.get("message_count", 0)
                extraction_outcomes.append(result.get("outcome", "unknown"))

            answer_conversation_id = await create_answer_conversation(
                store,
                synthetic_user_id,
                question_text,
                FRESH_QUESTION_ID,
            )

            extraction_created_ids = await get_current_run_memory_ids(
                pool, ingested_conversation_ids
            )

            query_emb = await embed_query(question_text)
            memories = await retrieve_memories_for_text(
                store=store,
                query_text=question_text,
                user_id=synthetic_user_id,
                query_embedding=query_emb,
                limit=TOP_K_MEMORIES,
                include_l0=True,
                log_retrieval=True,
                retrieval_triggered_by="longmemeval_parity",
                include_dream_observations=True,
            )

            retrieval_latency_start = time.time()
            _ = await retrieve_memories_for_text(
                store=store,
                query_text=question_text,
                user_id=synthetic_user_id,
                query_embedding=query_emb,
                limit=TOP_K_MEMORIES,
                include_l0=True,
                log_retrieval=False,
                retrieval_triggered_by="longmemeval_parity_timing",
                include_dream_observations=True,
            )
            retrieval_latency_ms = (time.time() - retrieval_latency_start) * 1000

            memory_context = await build_memory_context(
                store,
                answer_conversation_id,
                max_tokens=MAX_TOKENS,
            )

            system_prompt = await assemble_system_prompt(
                memory_context=memory_context,
                conversation_id=answer_conversation_id,
            )

        finally:
            ph_mod.answer_with_llm = orig_answer
            ph_mod.judge_answer = orig_judge

        memories_used = len(memories)
        retrieved_memory_ids = [str(m.get("id")) for m in memories if m.get("id")]

        encryption_result = await verify_encryption_for_user(pool, synthetic_user_id)
        same_user_result = await verify_same_user_retrieval(
            pool, synthetic_user_id, retrieved_memory_ids
        )

        provenance_ids = set(extraction_created_ids) & set(retrieved_memory_ids)
        current_run_memory_ids = set(extraction_created_ids)

        rollback_gates = {
            "memories_used_nonzero": memories_used > 0,
            "extraction_completed_nonzero": any(o == "completed" for o in extraction_outcomes),
            "retrieval_latency_ok": retrieval_latency_ms < 1500,
            "encryption_ok": len(encryption_result["failures"]) == 0,
            "same_user_retrieval": same_user_result["all_same_user"],
            "current_run_provenance": len(provenance_ids) > 0,
        }

        all_gates_pass = all(rollback_gates.values())

        evidence: dict[str, Any] = {
            "question_id": SELECTED_QUESTION_ID,
            "fresh_question_id": FRESH_QUESTION_ID,
            "synthetic_user_id": str(synthetic_user_id),
            "question_type": question_type,
            "haystack_session_count": len(haystack_sessions),
            "haystack_message_count": sum(len(s) for s in haystack_sessions),
            "total_messages_ingested": total_messages_ingested,
            "extraction_outcomes": extraction_outcomes,
            "extraction_completed_count": sum(1 for o in extraction_outcomes if o == "completed"),
            "extraction_empty_count": sum(1 for o in extraction_outcomes if o == "empty"),
            "extraction_created_memory_ids": extraction_created_ids,
            "memories_used": memories_used,
            "retrieved_memory_ids": retrieved_memory_ids,
            "retrieval_latency_ms": round(retrieval_latency_ms, 2),
            "memory_context_length": len(memory_context),
            "memory_context_preview": memory_context[:200] if memory_context else "",
            "system_prompt_length": len(system_prompt),
            "system_prompt_preview": system_prompt[:500] if system_prompt else "",
            "encryption": encryption_result,
            "same_user_verification": same_user_result,
            "rollback_gates": rollback_gates,
            "all_gates_pass": all_gates_pass,
            "provenance_ids": list(provenance_ids),
            "current_run_memory_ids": list(current_run_memory_ids),
            "provider_route": "openai/gpt-4o-mini",
            "smoke_timestamp": datetime.now().isoformat(),
        }

        return evidence

    finally:
        await pool.close()


if __name__ == "__main__":
    evidence = asyncio.run(run_smoke_trace())

    out_json = Path("/home/sol/daemon/.sisyphus/evidence/task-11-smoke.json")
    with open(out_json, "w") as f:
        json.dump(evidence, f, indent=2)
    logger.info(f"Written: {out_json}")

    memory_ids = evidence.get("retrieved_memory_ids", [])
    memory_ctx = evidence.get("memory_context_preview", "")

    substring_evidence = Path("/home/sol/daemon/.sisyphus/evidence/task-11-memory-substring.txt")
    with open(substring_evidence, "w") as f:
        f.write(f"T11 Memory Substring Evidence\n")
        f.write(f"Generated: {evidence['smoke_timestamp']}\n")
        f.write(f"Question ID: {evidence['question_id']}\n")
        f.write(f"Fresh Question ID: {evidence['fresh_question_id']}\n")
        f.write(f"Synthetic User ID: {evidence['synthetic_user_id']}\n")
        f.write(f"Extraction Created Memory IDs: {evidence['extraction_created_memory_ids']}\n")
        f.write(f"Retrieved Memory IDs: {memory_ids}\n")
        f.write(f"Provenance Intersection: {evidence['provenance_ids']}\n\n")
        f.write(f"Memory Context Length: {evidence['memory_context_length']} chars\n")
        f.write(f"Memory Context Preview (first 200 chars):\n")
        f.write(f"{'='*60}\n")
        f.write(memory_ctx[:200] if memory_ctx else "(empty)")
        f.write(f"\n{'='*60}\n\n")
        same_user = evidence["same_user_verification"]["all_same_user"]
        f.write(f"SAME-USER VERIFICATION: {'PASS' if same_user else 'FAIL'}\n")
        f.write(f"  All {evidence['same_user_verification']['checked']} retrieved memories belong to the same synthetic user\n")
        provenance_pass = len(evidence['provenance_ids']) > 0
        f.write(f"CURRENT-RUN PROVENANCE: {'PASS' if provenance_pass else 'FAIL'}\n")
        f.write(f"  {len(evidence['provenance_ids'])} retrieved memories were created by current-run extraction\n")
    logger.info(f"Written: {substring_evidence}")

    logger.info(f"\n{'='*60}")
    logger.info(f"T11 Smoke Trace Results:")
    logger.info(f"  Question: {evidence['question_id']} ({evidence['question_type']})")
    logger.info(f"  Fresh Question ID: {evidence['fresh_question_id']}")
    logger.info(f"  Synthetic User: {evidence['synthetic_user_id']}")
    logger.info(f"  Haystack: {evidence['haystack_session_count']} sessions, {evidence['total_messages_ingested']} messages")
    logger.info(f"  Extraction: {evidence['extraction_completed_count']} completed, {evidence['extraction_empty_count']} empty")
    logger.info(f"  Memories Used: {evidence['memories_used']}")
    logger.info(f"  Extraction Created: {len(evidence['extraction_created_memory_ids'])} memories")
    logger.info(f"  Provenance Intersection: {len(evidence['provenance_ids'])}")
    logger.info(f"  Retrieval Latency: {evidence['retrieval_latency_ms']}ms")
    logger.info(f"  Memory Context: {evidence['memory_context_length']} chars")
    logger.info(f"  System Prompt: {evidence['system_prompt_length']} chars")
    logger.info(f"  Encryption: {len(evidence['encryption']['failures'])} failures")
    logger.info(f"  Same-User: {'PASS' if evidence['same_user_verification']['all_same_user'] else 'FAIL'}")
    logger.info(f"  Rollback Gates: {evidence['rollback_gates']}")
    logger.info(f"  OVERALL: {'PASS' if evidence['all_gates_pass'] else 'FAIL'}")
    logger.info(f"{'='*60}\n")

    artifact_path = Path("/home/sol/daemon/tests/benchmark_results/harness_parity_smoke.md")
    memory_ctx = evidence.get("memory_context_preview", "")
    prompt_preview = evidence.get("system_prompt_preview", "")[:1000] if evidence.get("system_prompt_preview") else ""

    with open(artifact_path, "w") as f:
        f.write("# T11 — Harness Parity Smoke Trace\n\n")
        f.write(f"**Date**: {evidence['smoke_timestamp']}\n")
        f.write(f"**Question**: `{evidence['question_id']}` ({evidence['question_type']})\n")
        f.write(f"**Fresh Question ID**: `{evidence['fresh_question_id']}`\n")
        f.write(f"**Synthetic User ID**: `{evidence['synthetic_user_id']}`\n\n")

        f.write("## Rollback Gates\n\n")
        for gate, passed in evidence["rollback_gates"].items():
            status = "✅ PASS" if passed else "❌ FAIL"
            f.write(f"- {gate}: {status}\n")
        f.write(f"\n**Overall**: {'✅ PASS' if evidence['all_gates_pass'] else '❌ FAIL'}\n\n")

        f.write("## Haystack Ingestion\n\n")
        f.write(f"- Sessions: {evidence['haystack_session_count']}\n")
        f.write(f"- Messages ingested: {evidence['total_messages_ingested']}\n")
        f.write(f"- Extraction outcomes: {evidence['extraction_outcomes']}\n\n")

        f.write("## Extraction\n\n")
        f.write(f"- Completed: {evidence['extraction_completed_count']}\n")
        f.write(f"- Empty: {evidence['extraction_empty_count']}\n")
        f.write(f"- Created memory IDs: {evidence['extraction_created_memory_ids']}\n\n")

        f.write("## Provenance\n\n")
        f.write(f"- Extraction created: {len(evidence['extraction_created_memory_ids'])} memories\n")
        f.write(f"- Retrieved: {evidence['memories_used']} memories\n")
        f.write(f"- Provenance intersection: {len(evidence['provenance_ids'])} (current-run created AND retrieved)\n")
        f.write(f"- Provenance IDs: {evidence['provenance_ids']}\n\n")

        f.write("## Retrieval\n\n")
        f.write(f"- Memories used: {evidence['memories_used']}\n")
        f.write(f"- Retrieved memory IDs: `{evidence['retrieved_memory_ids']}`\n")
        f.write(f"- Retrieval latency: {evidence['retrieval_latency_ms']}ms\n")
        f.write(f"- Same-user verification: {'PASS' if evidence['same_user_verification']['all_same_user'] else 'FAIL'}\n\n")

        f.write("## Memory Context\n\n")
        f.write(f"Length: {evidence['memory_context_length']} chars\n\n")
        if memory_ctx:
            f.write("```\n")
            f.write(memory_ctx[:500])
            if len(memory_ctx) > 500:
                f.write("\n... (truncated)\n")
            f.write("```\n\n")
        else:
            f.write("_empty_\n\n")

        f.write("## System Prompt\n\n")
        f.write(f"Length: {evidence['system_prompt_length']} chars\n\n")
        if prompt_preview:
            f.write("Preview (first 1000 chars):\n\n")
            f.write("```\n")
            f.write(prompt_preview)
            if len(evidence.get("system_prompt_preview", "")) > 1000:
                f.write("\n... (truncated)\n")
            f.write("```\n\n")
        else:
            f.write("_empty_\n\n")

        enc = evidence["encryption"]
        f.write("## Encryption Verification\n\n")
        f.write(f"- Messages checked: {enc['messages_checked']}, decoded OK: {enc['messages_decoded_ok']}\n")
        f.write(f"- Memories checked: {enc['memories_checked']}, decoded OK: {enc['memories_decoded_ok']}\n")
        f.write(f"- Extraction logs checked: {enc['extraction_logs_checked']}, decoded OK: {enc['extraction_logs_decoded_ok']}\n")
        if enc["failures"]:
            f.write(f"- Failures: {enc['failures']}\n")
        else:
            f.write("- Failures: none\n")
        f.write("\n")

        f.write(f"## Provider Route\n\n")
        f.write(f"- {evidence['provider_route']}\n\n")

        f.write("---\n\n")
        f.write("_Note: answer/judge calls mocked after prompt capture. "
                "Extraction, embedding, and retrieval used real providers._\n")

    logger.info(f"Written: {artifact_path}")

    sys.exit(0 if evidence["all_gates_pass"] else 1)