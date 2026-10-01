from __future__ import annotations

import asyncio
import secrets
import uuid
from typing import cast

import asyncpg
import pytest
from cryptography.fernet import Fernet

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.benchmark_longmemeval.isolated_database import isolated_database_fixture
from tests.longmemeval.evaluate import retrieve_user_memories

VECTOR_DIMENSION = 1024
QUERY_TEXT = "Which codename was saved for the benchmark retrieval logging smoke test?"
MEMORY_TEXT = "The benchmark retrieval logging smoke test codename is Orion."

# Fresh, per-run disposable crypto fixtures. The smoke test never reads the
# application's storage/application keys: the isolated database fixture only
# provisions an explicitly configured disposable DSN
# (``ENTITLEMENTS_TEST_DATABASE_URL``), and the runtime Fernet key / hash
# pepper are generated in-process and injected through the process settings
# for every runtime call below.
RLS_SCHEMA_PREFIX = "retrieval_smoke_"
RLS_AUDITED_TABLES = ["users", "memories", "retrieval_log", "conversations"]


def _test_vector(value: float = 0.25) -> list[float]:
    return [value] * VECTOR_DIMENSION


isolated_rls_pool = isolated_database_fixture(RLS_SCHEMA_PREFIX, audited_tables=RLS_AUDITED_TABLES)


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
async def test_benchmark_retrieval_path_persists_one_retrieval_log_row(
    monkeypatch: pytest.MonkeyPatch,
    isolated_rls_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _scoped_dsn = isolated_rls_pool

    # Fresh process settings for runtime calls only: a new Fernet key and a
    # new hash pepper generated per run, injected via the environment so the
    # cached settings are rebuilt from the test's fixtures.
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.daemon_encryption_key
    assert settings.daemon_auth_pepper

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
            # The supplied query vector uses the configured storage space;
            # unrelated synthetic model labels are correctly excluded by retrieval.
            embedding_model=settings.embedding_document_model,
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
        # Pool teardown (graceful close, then drop of the owned schema) is owned
        # by the shared isolated-database fixture; the per-user delete above
        # keeps the disposable schema's benchmark rows scoped to this test.
