"""LongMemEval dataset ingestion adapter.

Loads LongMemEval chat history sessions and feeds them through Daemon's
message persistence + extraction pipeline. Each session becomes a Daemon
conversation.

Canonical benchmark/admin entrypoint:
    python -m orchestrator.eval.longmemeval ingest --dataset /path/to/longmemeval_s.json

This legacy script remains as a compatibility adapter for ingestion-specific
fixtures and direct debugging.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.extraction import messages_to_extraction_text, process_extraction
from orchestrator.memory.store import MemoryStore

DATASET_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s.json"
)
DATASET_PATH = Path(tempfile.gettempdir()) / "longmemeval-review/data/longmemeval_s.json"
TEST_USER_NAME = "longmemeval_test_user"
TEST_USER_EMAIL = "longmemeval@daemon.test"
TEST_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CorpusSession:
    corpus_key: str
    canonical_session_id: str
    messages: list[dict[str, Any]]
    raw_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusPlan:
    corpus_sessions: tuple[CorpusSession, ...]
    question_corpus_refs: dict[str, tuple[str, ...]]
    total_haystack_refs: int
    unique_session_ids: int
    unique_normalized_contents: int


def normalize_question_id(entry: dict[str, Any], idx: int) -> str:
    return str(entry.get("question_id", f"q{idx}"))


def normalize_session_messages(messages: list[dict[str, Any]]) -> str:
    normalized_messages: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user")).strip().lower() or "user"
        content = " ".join(str(message.get("content", "")).split())
        normalized_messages.append({"role": role, "content": content})
    return json.dumps(normalized_messages, separators=(",", ":"), ensure_ascii=True)


def build_corpus_key(messages: list[dict[str, Any]]) -> str:
    normalized = normalize_session_messages(messages)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_corpus_plan(dataset: list[dict[str, Any]]) -> CorpusPlan:
    corpus_sessions_by_key: dict[str, CorpusSession] = {}
    session_ids_by_key: dict[str, list[str]] = {}
    question_corpus_refs: dict[str, tuple[str, ...]] = {}
    total_haystack_refs = 0
    unique_session_ids: set[str] = set()

    for entry_idx, entry in enumerate(dataset):
        question_id = normalize_question_id(entry, entry_idx)
        haystack_sessions = entry.get("haystack_sessions", [])
        haystack_session_ids = entry.get("haystack_session_ids", [])
        if not isinstance(haystack_sessions, list):
            question_corpus_refs[question_id] = ()
            continue

        question_refs: list[str] = []
        seen_question_refs: set[str] = set()
        for sess_idx, session_messages in enumerate(haystack_sessions):
            if not isinstance(session_messages, list):
                continue
            total_haystack_refs += 1
            session_id = (
                str(haystack_session_ids[sess_idx])
                if sess_idx < len(haystack_session_ids)
                else f"{question_id}_session_{sess_idx}"
            )
            unique_session_ids.add(session_id)
            corpus_key = build_corpus_key(session_messages)

            raw_ids = session_ids_by_key.setdefault(corpus_key, [])
            if session_id not in raw_ids:
                raw_ids.append(session_id)

            if corpus_key not in corpus_sessions_by_key:
                corpus_sessions_by_key[corpus_key] = CorpusSession(
                    corpus_key=corpus_key,
                    canonical_session_id=session_id,
                    messages=session_messages,
                    raw_session_ids=(session_id,),
                )

            if corpus_key not in seen_question_refs:
                question_refs.append(corpus_key)
                seen_question_refs.add(corpus_key)

        question_corpus_refs[question_id] = tuple(question_refs)

    corpus_sessions: list[CorpusSession] = []
    for corpus_key, session in corpus_sessions_by_key.items():
        corpus_sessions.append(
            CorpusSession(
                corpus_key=corpus_key,
                canonical_session_id=session.canonical_session_id,
                messages=session.messages,
                raw_session_ids=tuple(
                    session_ids_by_key.get(corpus_key, [session.canonical_session_id])
                ),
            )
        )

    return CorpusPlan(
        corpus_sessions=tuple(corpus_sessions),
        question_corpus_refs=question_corpus_refs,
        total_haystack_refs=total_haystack_refs,
        unique_session_ids=len(unique_session_ids),
        unique_normalized_contents=len(corpus_sessions),
    )


async def ensure_dataset() -> list[dict[str, Any]]:
    """Ensure dataset is available locally, downloading if needed."""
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DATASET_PATH.exists():
        logger.info(f"Loading existing dataset from {DATASET_PATH}")
        with open(DATASET_PATH) as f:
            return json.load(f)

    logger.info(f"Dataset not found at {DATASET_PATH}, downloading...")
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(DATASET_URL, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
        with open(DATASET_PATH, "w") as f:
            json.dump(data, f)
        logger.info(f"Downloaded dataset to {DATASET_PATH}")
        return data
    except Exception as e:
        logger.error(f"Failed to download dataset: {e}")
        raise


async def create_test_user(pool: asyncpg.Pool) -> uuid.UUID:
    """Create or get the dedicated test user for benchmark isolation."""
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", TEST_USER_EMAIL)
        if existing:
            user_id = existing["id"]
            logger.info(f"Using existing test user: {user_id}")
            return user_id

        user_id = TEST_USER_ID
        await conn.execute(
            """
            INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
            VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            TEST_USER_EMAIL,
            TEST_USER_NAME,
        )
        logger.info(f"Created test user: {user_id}")
        return user_id


async def cleanup_test_user(pool: asyncpg.Pool) -> None:
    """Remove test user and all associated data."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE email = $1", TEST_USER_EMAIL)
        logger.info("Cleaned up test user and associated data")


async def poll_extraction_complete(
    pool: asyncpg.Pool,
    conversation_id: uuid.UUID,
    max_wait_seconds: int = 90,
    poll_interval: float = 2.0,
) -> bool:
    """Poll extraction_log until we see an entry for this conversation."""
    start = datetime.now()
    while (datetime.now() - start).total_seconds() < max_wait_seconds:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, extracted_facts
                FROM memory_extraction_log
                WHERE conversation_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                conversation_id,
            )
            if row is not None:
                facts_count = len(row["extracted_facts"]) if row["extracted_facts"] else 0
                logger.info(
                    f"Extraction complete for conversation {conversation_id}: "
                    f"{facts_count} facts extracted"
                )
                return True
            elapsed = (datetime.now() - start).total_seconds()
            logger.debug(
                f"Waiting for extraction to complete for conversation {conversation_id} "
                f"({elapsed:.0f}s elapsed, {max_wait_seconds}s timeout)"
            )
        await asyncio.sleep(poll_interval)

    logger.warning(
        f"Extraction polling timed out for conversation {conversation_id} after {max_wait_seconds}s"
    )
    return False


async def ingest_session(
    store: MemoryStore,
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    session_id: str,
    messages: list[dict[str, Any]],
    session_index: int,
) -> dict[str, Any]:
    """Ingest a single LongMemEval session as one conversation."""
    conversation = await store.create_conversation(
        user_id=user_id,
        pipeline="cloud",
        title=f"LongMemEval Session {session_index}: {session_id[:16]}",
    )
    conversation_id = conversation["id"]

    for msg_idx, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content:
            continue

        try:
            await store.insert_message(
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
                content=content,
                status="complete",
                metadata={
                    "source": "longmemeval",
                    "session_id": session_id,
                    "msg_idx": msg_idx,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to insert message {msg_idx} in session {session_id}: {e}")

    extraction_text = "\n".join(
        f"[{'user' if m.get('role') == 'user' else 'assistant'}]: {m.get('content', '')}"
        for m in messages
        if m.get("content")
    )

    try:
        await process_extraction(
            store=store,
            user_id=user_id,
            conversation_id=conversation_id,
            text=extraction_text,
        )
    except Exception as e:
        logger.error(f"Extraction failed for session {session_id}: {e}")
        return {
            "session_id": session_id,
            "conversation_id": str(conversation_id),
            "status": "extraction_failed",
            "error": str(e),
        }

    extraction_ok = await poll_extraction_complete(pool, conversation_id)

    return {
        "session_id": session_id,
        "conversation_id": str(conversation_id),
        "message_count": len(messages),
        "status": "complete" if extraction_ok else "extraction_timeout",
    }


ScopeFactory = Callable[[], AbstractAsyncContextManager[Any]]


async def ingest_session_parity(
    store: MemoryStore,
    user_id: uuid.UUID,
    session_id: str,
    messages: list[dict[str, Any]],
    session_index: int,
    *,
    scope_factory: ScopeFactory,
    on_conversation_created: Callable[[uuid.UUID], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Ingest one session the way the production extraction worker does.

    Messages are persisted, then split losslessly by the worker's own
    ``_chunk_messages_for_extraction`` (bounded at ``MAX_EXTRACTION_INPUT_CHARS``)
    and each chunk is extracted inside the account compute scope the worker
    uses. ``process_extraction`` awaits its own ``log_extraction`` write, so its
    return value is the completion barrier; no polling is needed.
    """
    from orchestrator.worker.jobs import _chunk_messages_for_extraction

    conversation = await store.create_conversation(
        user_id=user_id,
        pipeline="cloud",
        title=f"LongMemEval Session {session_index}: {session_id[:16]}",
    )
    conversation_id = conversation["id"]
    if on_conversation_created is not None:
        await on_conversation_created(conversation_id)

    parsed: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []
    for msg_idx, msg in enumerate(messages):
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        if not content:
            continue
        row = await store.insert_message(
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            content=content,
            status="complete",
            metadata={
                "source": "longmemeval",
                "session_id": session_id,
                "msg_idx": msg_idx,
            },
        )
        parsed.append({"role": role, "content": content})
        raw.append(
            {
                "id": row.get("id") if isinstance(row, dict) else None,
                "role": role,
                "content": content,
                "created_at": row.get("created_at") if isinstance(row, dict) else None,
            }
        )

    chunks = _chunk_messages_for_extraction(parsed, raw) if parsed else []  # type: ignore[arg-type]
    chunk_results: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks):
        text = messages_to_extraction_text(chunk["messages"])
        if not text:
            continue
        cursor_message = chunk["raw_messages"][-1]
        advances_cursor = bool(chunk["advances_cursor"])
        observed_at = cursor_message.get("created_at") if advances_cursor else None
        message_id = cursor_message.get("id") if advances_cursor else None
        try:
            async with scope_factory():
                success, new_memories, _continuation = await process_extraction(
                    store=store,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    text=text,
                    last_message_observed_at=observed_at,
                    last_message_id=str(message_id) if message_id is not None else None,
                    chunk_index=chunk_index,
                    cursor_checkpoint=advances_cursor,
                )
        except Exception as exc:
            # Provider/store exceptions can carry conversation text; keep only the type.
            chunk_results.append(
                {"chunk_index": chunk_index, "success": False, "error": type(exc).__name__}
            )
            continue
        chunk_results.append(
            {
                "chunk_index": chunk_index,
                "success": bool(success),
                "memories": len(new_memories),
            }
        )

    failed = [result for result in chunk_results if not result["success"]]
    return {
        "session_id": session_id,
        "conversation_id": str(conversation_id),
        "message_count": len(parsed),
        "chunk_count": len(chunk_results),
        "chunks_failed": len(failed),
        "memories_created": sum(int(result.get("memories", 0)) for result in chunk_results),
        "chunk_errors": sorted({str(result["error"]) for result in failed if "error" in result}),
        "status": "complete" if not failed else "extraction_failed",
    }


async def run_ingestion(
    limit: int | None = None,
    cleanup: bool = False,
) -> list[dict[str, Any]]:
    """Run the full ingestion pipeline."""
    settings = get_settings()

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    if not settings.daemon_encryption_key:
        raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

    from orchestrator.eval.bench_db import assert_benchmark_database, check_benchmark_dsn

    check_benchmark_dsn(settings.database_url)
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )

    try:
        # Destructive (--cleanup deletes the benchmark user): benchmark databases only.
        await assert_benchmark_database(pool, settings.database_url)
        encryption = ContentEncryption(settings.daemon_encryption_key)
        store = MemoryStore(db_pool=pool, encryption=encryption)

        if cleanup:
            await cleanup_test_user(pool)
            logger.info("Cleanup complete")
            return []

        test_user_id = await create_test_user(pool)

        dataset = await ensure_dataset()

        if limit:
            dataset = dataset[:limit]
            logger.info(f"Limiting ingestion to {limit} entries")

        results = []
        total_entries = len(dataset)
        corpus_plan = build_corpus_plan(dataset)

        logger.info(
            "Starting ingestion of %s LongMemEval entries (%s haystack refs, %s unique session ids, %s unique normalized sessions)",
            total_entries,
            corpus_plan.total_haystack_refs,
            corpus_plan.unique_session_ids,
            corpus_plan.unique_normalized_contents,
        )

        for session_index, corpus_session in enumerate(corpus_plan.corpus_sessions):
            logger.info(
                "[%s/%s] Processing corpus session %s (%s raw session ids)",
                session_index + 1,
                len(corpus_plan.corpus_sessions),
                corpus_session.canonical_session_id,
                len(corpus_session.raw_session_ids),
            )

            try:
                result = await ingest_session(
                    store=store,
                    pool=pool,
                    user_id=test_user_id,
                    session_id=corpus_session.canonical_session_id,
                    messages=corpus_session.messages,
                    session_index=session_index,
                )
                result["corpus_key"] = corpus_session.corpus_key
                result["raw_session_ids"] = list(corpus_session.raw_session_ids)
                results.append(result)
            except Exception as e:
                logger.error(
                    "Failed to ingest corpus session %s: %s",
                    corpus_session.canonical_session_id,
                    e,
                )
                results.append(
                    {
                        "corpus_key": corpus_session.corpus_key,
                        "session_id": corpus_session.canonical_session_id,
                        "raw_session_ids": list(corpus_session.raw_session_ids),
                        "status": "error",
                        "error": str(e),
                    }
                )

        successful = sum(1 for r in results if r.get("status") == "complete")
        failed = sum(
            1
            for r in results
            if r.get("status") in ("error", "extraction_failed", "extraction_timeout")
        )

        logger.info(
            f"Ingestion complete: {successful} successful, {failed} failed, "
            f"{len(results)} total sessions processed"
        )

        return results

    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "LongMemEval dataset ingestion adapter. For canonical benchmark runs, "
            "prefer `python -m orchestrator.eval.longmemeval ingest --dataset ...`."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of dataset entries to process (for testing)",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Clean up test user and associated data",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    results = asyncio.run(run_ingestion(limit=args.limit, cleanup=args.cleanup))

    if results:
        output_path = Path(tempfile.gettempdir()) / "longmemeval_ingestion_results.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                delete=False,
            ) as output_file:
                temporary_path = Path(output_file.name)
                json.dump(results, output_file, indent=2)
            os.replace(temporary_path, output_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        logger.info(f"Results written to {output_path}")

    if args.cleanup:
        sys.exit(0)

    failed = [r for r in results if r.get("status") not in ("complete",)]
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
