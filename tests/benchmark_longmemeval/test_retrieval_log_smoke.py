from __future__ import annotations

import asyncio
import socket
import uuid
from typing import cast
from urllib.parse import urlparse

import asyncpg
import pytest

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import retrieve_user_memories

VECTOR_DIMENSION = 1024
QUERY_TEXT = "Which codename was saved for the benchmark retrieval logging smoke test?"
MEMORY_TEXT = "The benchmark retrieval logging smoke test codename is Orion."


def _test_vector(value: float = 0.25) -> list[float]:
    return [value] * VECTOR_DIMENSION


async def _create_test_pool() -> asyncpg.Pool:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured for retrieval-log smoke test")

    try:
        return await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=2,
        )
    except OSError as exc:
        parsed = urlparse(settings.database_url)
        if parsed.hostname == "postgres" and isinstance(exc, socket.gaierror):
            try:
                return await asyncpg.create_pool(
                    user=parsed.username,
                    password=parsed.password,
                    database=parsed.path.lstrip("/"),
                    host="127.0.0.1",
                    port=parsed.port or 5432,
                    min_size=1,
                    max_size=2,
                )
            except OSError as fallback_exc:
                pytest.skip(f"database unavailable for retrieval-log smoke test: {fallback_exc!s}")

        pytest.skip(f"database unavailable for retrieval-log smoke test: {exc!s}")


async def _wait_for_retrieval_log_count(
    pool: asyncpg.Pool,
    *,
    user_id: uuid.UUID,
    query_text: str,
    expected_count: int,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        count = int(
            await pool.fetchval(
                """
            SELECT COUNT(*)
            FROM retrieval_log
            WHERE user_id = $1
              AND query_text = $2
              AND retrieval_triggered_by = 'longmemeval'
            """,
                user_id,
                query_text,
            )
        )
        if count == expected_count:
            return
        await asyncio.sleep(0.05)

    raise AssertionError(
        f"retrieval_log count did not reach {expected_count} for query {query_text!r}"
    )


@pytest.mark.asyncio
async def test_benchmark_retrieval_path_persists_one_retrieval_log_row() -> None:
    settings = get_settings()
    if not settings.daemon_encryption_key:
        pytest.skip("DAEMON_ENCRYPTION_KEY not configured for retrieval-log smoke test")

    pool = await _create_test_pool()
    store = MemoryStore(pool, ContentEncryption(settings.daemon_encryption_key))
    user_id = uuid.uuid4()
    user_email = f"retrieval-log-smoke+{user_id.hex}@daemon.test"

    try:
        _ = await pool.execute(
            """
            INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
            VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
            """,
            user_id,
            user_email,
            "retrieval_log_smoke",
        )

        conversation = await store.create_conversation(
            user_id=user_id,
            pipeline="cloud",
            title="Retrieval log smoke",
        )
        conversation_id = cast(uuid.UUID, conversation["id"])

        memory = await store.insert_memory(
            user_id=user_id,
            content=MEMORY_TEXT,
            category="fact",
            source_type="import",
            embedding=_test_vector(),
            embedding_model="benchmark-smoke-vector",
            source_conversation_id=conversation_id,
        )

        baseline_count = int(
            await pool.fetchval(
                """
            SELECT COUNT(*)
            FROM retrieval_log
            WHERE user_id = $1
              AND query_text = $2
              AND retrieval_triggered_by = 'longmemeval'
            """,
                user_id,
                QUERY_TEXT,
            )
        )

        memories = await retrieve_user_memories(
            store=store,
            user_id=user_id,
            query_embedding=_test_vector(),
            query_text=QUERY_TEXT,
            limit=1,
            log_retrieval=True,
            allowed_source_conversation_ids=[conversation_id],
        )

        assert [item["id"] for item in memories] == [memory["id"]]

        await _wait_for_retrieval_log_count(
            pool,
            user_id=user_id,
            query_text=QUERY_TEXT,
            expected_count=baseline_count + 1,
        )

        row = cast(
            asyncpg.Record | None,
            await pool.fetchrow(
                """
            SELECT candidate_memory_ids,
                   selected_memory_ids,
                   retrieval_triggered_by,
                   l0_included,
                   conversation_id
            FROM retrieval_log
            WHERE user_id = $1
              AND query_text = $2
              AND retrieval_triggered_by = 'longmemeval'
            ORDER BY created_at DESC
            LIMIT 1
            """,
                user_id,
                QUERY_TEXT,
            ),
        )

        assert row is not None
        assert row["retrieval_triggered_by"] == "longmemeval"
        assert row["l0_included"] is False
        assert row["conversation_id"] is None
        assert row["candidate_memory_ids"] == [memory["id"]]
        assert row["selected_memory_ids"] == [memory["id"]]
    finally:
        _ = await pool.execute("DELETE FROM users WHERE id = $1", user_id)
        await pool.close()
