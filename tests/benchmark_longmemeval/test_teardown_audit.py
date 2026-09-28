from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.config import get_settings
from orchestrator.eval.chunk_harness import cleanup_benchmark_state, ingest_question_chunks
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import evaluate_single
from tests.longmemeval.ingest import ingest_session

REPORT_PATH = Path(__file__).with_name("TEARDOWN_AUDIT.md")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
AUDIT_SCHEMA_PREFIX = "teardown_audit_"
TEST_DSN_ENV_VAR = "ENTITLEMENTS_TEST_DATABASE_URL"
COUNT_QUERIES = {
    "users": "SELECT COUNT(*) FROM users WHERE id = $1",
    "conversations": "SELECT COUNT(*) FROM conversations WHERE user_id = $1",
    "messages": "SELECT COUNT(*) FROM messages WHERE user_id = $1",
    "memories": "SELECT COUNT(*) FROM memories WHERE user_id = $1",
    "memory_extraction_log": ("SELECT COUNT(*) FROM memory_extraction_log WHERE user_id = $1"),
    "retrieval_log": "SELECT COUNT(*) FROM retrieval_log WHERE user_id = $1",
    "entities": "SELECT COUNT(*) FROM entities WHERE user_id = $1",
    "dream_log": "SELECT COUNT(*) FROM dream_log WHERE user_id = $1",
}
TABLES = list(COUNT_QUERIES)


@dataclass(slots=True)
class SnapshotRow:
    label: str
    counts: dict[str, int]
    note: str


@dataclass(slots=True)
class RetrievalGate:
    entered: asyncio.Event
    release: asyncio.Event


def _test_vector(value: float = 0.25) -> list[float]:
    return [value] * get_settings().embedding_dimensions


def _test_dsn() -> str:
    """Return the isolated disposable test DSN, or skip.

    This deliberately never falls back to the application ``DATABASE_URL``: the
    audit replays every migration and writes benchmark rows, so it must only ever
    touch a database the operator has explicitly designated as disposable.
    """
    dsn = os.environ.get(TEST_DSN_ENV_VAR, "").strip()
    if not dsn:
        pytest.skip(f"requires isolated {TEST_DSN_ENV_VAR}")

    parsed = urlparse(dsn)
    if parsed.hostname != "postgres" or not parsed.username or parsed.password is None:
        return dsn

    return (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@127.0.0.1:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )


def _scoped_dsn(dsn: str, schema: str) -> str:
    """Pin ``search_path`` on the DSN itself, not just on the audit pool.

    The test monkeypatches ``DATABASE_URL`` so the benchmark harness sees a
    reachable DSN. If that DSN did not carry the audit schema, any helper that
    opened its own pool from ``get_settings().database_url`` would silently bind
    to ``public`` in the test database. Embedding the same ``search_path`` makes
    the monkeypatched value behaviourally identical to the pool.
    """
    options = urlencode({"options": f"-csearch_path={schema},public"})
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}{options}"


async def _connect(dsn: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=dsn, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Benchmark teardown audit could not reach test database: {exc}")


async def _apply_migrations(conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy) -> None:
    """Apply the shipped migrations into the audit's own schema.

    ``scripts/migrate.py`` bookkeeping (``_migrations``) is intentionally skipped:
    with ``public`` still on ``search_path`` an ``IF NOT EXISTS`` create would bind
    to the shared table and the bookkeeping INSERT would write outside the audit
    schema. The audit schema is disposable, so it simply replays every migration.
    """
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migration_files, f"no migration files found under {MIGRATIONS_DIR}"
    for path in migration_files:
        async with conn.transaction():
            await conn.execute(path.read_text())


async def _public_relations(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
) -> frozenset[tuple[str, str]]:
    """Fingerprint every relation in the shared ``public`` schema of the test DB.

    Replaying migrations must not create, drop, or rename anything in ``public``
    (notably ``CREATE EXTENSION`` in ``001``, which is expected to be a no-op
    because the extensions are already installed). Comparing this before and after
    the replay is what proves schema isolation rather than assuming it.
    """
    rows = await conn.fetch(
        """
        SELECT c.relkind, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        """
    )
    return frozenset((row["relkind"], row["relname"]) for row in rows)


async def _assert_audit_tables_isolated(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, schema: str
) -> None:
    """Fail loudly if any audited table would resolve outside the audit schema.

    ``public`` stays on ``search_path`` so pgvector/pgcrypto types resolve, which
    means an unqualified table name could silently bind to the shared schema. Every
    audited table must therefore exist inside the audit schema and shadow it.
    """
    rows = await conn.fetch(
        """
        SELECT c.relname AS name, n.nspname AS schema_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND c.oid IN (SELECT to_regclass(name) FROM unnest($1::text[]) AS name)
        """,
        list(TABLES),
    )
    located = {row["name"]: row["schema_name"] for row in rows}

    missing = [table for table in TABLES if table not in located]
    assert not missing, f"audit tables absent after migration replay: {missing}"

    outside = sorted(name for name, name_schema in located.items() if name_schema != schema)
    assert not outside, f"audit tables resolved outside schema {schema}: {outside}"


async def _assert_memory_metadata_contract(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
) -> None:
    """Assert ``migrations/040_memory_metadata.sql`` really landed.

    The column is required by ``MemoryStore.supersede_memory()``,
    ``MemoryStore.update_memory_metadata()`` and the chunk harness. Asserting the
    catalog contract *and* a real omitted-column write catches a migration that is
    present but wrong (nullable, wrong type, or a NULL default), which a mere
    "column exists" check would miss.
    """
    column = await conn.fetchrow(
        """
        SELECT data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'memories'
          AND column_name = 'metadata'
        """
    )
    assert column is not None, "migrations/040_memory_metadata.sql did not add memories.metadata"
    assert column["data_type"] == "jsonb", column["data_type"]
    assert column["is_nullable"] == "NO", column["is_nullable"]

    default = cast(str, column["column_default"])
    assert "jsonb" in default, f"memories.metadata default is not jsonb: {default!r}"
    assert "{}" in default, f"memories.metadata default is not the empty object: {default!r}"

    user_id = uuid.uuid4()
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
            VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
            """,
            user_id,
            f"metadata-contract+{user_id.hex}@daemon.test",
            "metadata_contract",
        )
        try:
            # An omitted column must land as a real empty JSON object, decided
            # server-side so the check does not depend on asyncpg JSON codecs.
            probe = await conn.fetchrow(
                """
                INSERT INTO memories (user_id, content, category, source_type)
                VALUES ($1, $2, 'fact', 'import')
                RETURNING
                    metadata IS NULL AS is_null,
                    jsonb_typeof(metadata) AS kind,
                    metadata = '{}'::jsonb AS is_empty_object
                """,
                user_id,
                "migration 040 default probe",
            )
            assert probe is not None, "metadata default probe returned no row"
            assert probe["is_null"] is False, "memories.metadata default produced NULL"
            assert probe["kind"] == "object", probe["kind"]
            assert probe["is_empty_object"] is True, "omitted metadata was not the empty object"

            # NOT NULL must be enforced, not merely declared. The probe runs in a
            # savepoint so the deliberate violation does not poison the cleanup.
            with pytest.raises(asyncpg.NotNullViolationError):
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO memories (user_id, content, category, source_type, metadata)
                        VALUES ($1, $2, 'fact', 'import', NULL)
                        """,
                        user_id,
                        "migration 040 null probe",
                    )
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest_asyncio.fixture
async def isolated_audit_pool() -> AsyncIterator[tuple[asyncpg.Pool, str]]:
    """Disposable, migration-complete schema for the teardown audit.

    Requires an explicitly configured isolated test DSN (``ENTITLEMENTS_TEST_DATABASE_URL``,
    the same variable the other PostgreSQL-backed tests use). The audit used to connect
    straight to the application ``DATABASE_URL`` and measure whatever that schema
    happened to contain, so it both failed and mutated shared state whenever the local
    database lagged the migrations. It now provisions a throwaway schema on the isolated
    test server, replays the real migrations into it, and drops it on teardown.
    """
    dsn = _test_dsn()
    schema = f"{AUDIT_SCHEMA_PREFIX}{uuid.uuid4().hex}"
    admin = await _connect(dsn)
    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            server_settings={"search_path": f"{schema}, public"},
        )
        async with pool.acquire() as conn:
            public_before = await _public_relations(conn)
            await _apply_migrations(conn)
            public_after = await _public_relations(conn)
            added = sorted(public_after - public_before)
            removed = sorted(public_before - public_after)
            assert not added, f"migration replay created relations in public: {added}"
            assert not removed, f"migration replay dropped relations in public: {removed}"

            await _assert_audit_tables_isolated(conn, schema)
            await _assert_memory_metadata_contract(conn)
        yield pool, _scoped_dsn(dsn, schema)
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _insert_user(
    pool: asyncpg.Pool,
    *,
    user_id: uuid.UUID,
    email: str,
    name: str,
) -> None:
    _ = await pool.execute(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        email,
        name,
    )


async def _delete_user(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    _ = await pool.execute("DELETE FROM users WHERE id = $1", user_id)


async def _count_rows(pool: asyncpg.Pool, table: str, user_id: uuid.UUID) -> int:
    query = COUNT_QUERIES[table]
    value = cast(int, await pool.fetchval(query, user_id))
    assert isinstance(value, int)
    return value


async def _snapshot_counts(pool: asyncpg.Pool, user_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLES:
        counts[table] = await _count_rows(pool, table, user_id)
    return counts


async def _wait_for_table_count(
    pool: asyncpg.Pool,
    *,
    table: str,
    user_id: uuid.UUID,
    expected: int,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _count_rows(pool, table, user_id) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{table} count did not reach {expected} for audit user {user_id}")


async def _retrieval_log_null_conversation_count(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    value = cast(
        int,
        await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM retrieval_log
            WHERE user_id = $1
              AND conversation_id IS NULL
            """,
            user_id,
        ),
    )
    assert isinstance(value, int)
    return value


def _render_table(rows: list[SnapshotRow]) -> str:
    header = (
        "| Snapshot | users | conversations | messages | memories | "
        "memory_extraction_log | retrieval_log | entities | dream_log | Notes |"
    )
    separator = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    body = [header, separator]
    for row in rows:
        counts = row.counts
        body.append(
            "| "
            + " | ".join(
                [
                    row.label,
                    str(counts["users"]),
                    str(counts["conversations"]),
                    str(counts["messages"]),
                    str(counts["memories"]),
                    str(counts["memory_extraction_log"]),
                    str(counts["retrieval_log"]),
                    str(counts["entities"]),
                    str(counts["dream_log"]),
                    row.note,
                ]
            )
            + " |"
        )
    return "\n".join(body)


def _render_report(
    *,
    canonical_rows: list[SnapshotRow],
    fast_rows: list[SnapshotRow],
    canonical_null_logs: dict[str, int],
    fast_null_logs: dict[str, int],
) -> str:
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    return f"""# LongMemEval Teardown Audit

Date: {timestamp}

## Scope

This audit exercised the live benchmark code paths with deterministic local doubles for extraction, embeddings, answer generation, and judging so the only variable under test was database teardown behavior.

- Both lanes ran against a disposable per-test schema on the isolated `{TEST_DSN_ENV_VAR}` server, created by replaying the shipped `migrations/*.sql`; the shared `public` schema of that server was neither read nor written, so the counts below are not affected by pre-existing data.
- Canonical lane exercised `tests.longmemeval.ingest.ingest_session()` plus `tests.longmemeval.evaluate.evaluate_single()`, which are the concrete units looped by `orchestrator/eval/runner.py`.
- Fast lane exercised `orchestrator.eval.chunk_harness.cleanup_benchmark_state()` plus `ingest_question_chunks()` plus `evaluate_single()`, mirroring the per-question loop in `LongMemEvalChunkRunner.run()`.

### Instrumentation note

The fast-lane audit deliberately held the background `store.log_retrieval()` task behind an event before releasing it. That does **not** change which row is written; it only makes the existing asynchronous retrieval-log timing window deterministic so the audit can prove whether late writes survive teardown.

## Canonical lane snapshots

{_render_table(canonical_rows)}

### Canonical interpretation

- `conversations`, `messages`, `memories`, `memory_extraction_log`, and `retrieval_log` all grow from case 1 to case 2 instead of returning to zero.
- The canonical retrieval rows were written with `conversation_id IS NULL` in both observed cases (`after case 1 settled = {canonical_null_logs["after_case1"]}`, `after case 2 settled = {canonical_null_logs["after_case2"]}`), so they are not tied to conversation deletion anyway.
- Manually deleting the audit user returns every table to zero, which shows the residual rows come from **missing per-case teardown**, not from broken foreign-key cleanup.

**Canonical verdict:** residual rows survive between benchmark cases because the canonical lane does not run teardown between cases. The only destructive cleanup is whole-user deletion, and `orchestrator/eval/runner.py` does not call it.

## Fast lane snapshots

{_render_table(fast_rows)}

### Fast interpretation

- `messages` and `memory_extraction_log` stay at zero for every fast-lane snapshot because `ingest_question_chunks()` direct-inserts `memories` and bypasses canonical message persistence and extraction logging.
- After each fast case returns, the post-case cleanup removes the synchronous tables (`conversations`, `memories`, etc.) back to zero.
- Releasing the delayed retrieval-log task **after** cleanup recreates a single `retrieval_log` row (`conversation_id IS NULL` count after case 1 release = {fast_null_logs["after_case1_release"]}; after case 2 release = {fast_null_logs["after_case2_release"]}). That row survives the post-case cleanup because it lands after the deletes have already run.
- The next case's pre-cleanup deletes the leftover row from the prior case, and final user deletion returns every table to zero.

**Fast verdict:** the fast lane has no stable leak in its synchronous tables, but `retrieval_log` can survive teardown through **async bleed** from the background persistence task. Any row left behind is finally removed by the next pre-case cleanup or, if it is the last case, by the end-of-run user deletion.

## Root-cause summary

| Lane | Residual rows observed between cases? | Root cause | Evidence |
| --- | --- | --- | --- |
| Canonical | Yes: `conversations`, `messages`, `memories`, `memory_extraction_log`, `retrieval_log` accumulate 1 -> 2 across the two cases | Missing teardown | Counts only reset after the audit manually deletes the whole user |
| Fast | Yes, but only for `retrieval_log` when the delayed background write lands after cleanup | Async bleed | Post-case cleanup reaches zero, then a late `retrieval_log` row reappears with all other tables still at zero |
| Fast end-of-run | No rows remain after deleting the benchmark user | End-of-run user deletion | Final user delete returns the run-scoped user and every user-linked table to zero |

## Bottom line

- The canonical lane leaks benchmark state between cases because it never tears the benchmark user down between cases.
- The fast lane cleans its synchronous benchmark tables, but retrieval evidence is vulnerable to async timing because the retrieval-log write is backgrounded.
- End-of-run user deletion is a separate mechanism from per-case teardown: it is not what causes the leak, but it guarantees the last fast-lane stray row disappears.
"""


@pytest.mark.asyncio
async def test_teardown_audit_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    isolated_audit_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, scoped_dsn = isolated_audit_pool

    monkeypatch.setenv("DATABASE_URL", scoped_dsn)
    get_settings.cache_clear()
    settings = get_settings()

    if not settings.daemon_encryption_key:
        pytest.skip("DAEMON_ENCRYPTION_KEY not configured for teardown audit")

    # The monkeypatched DSN must be behaviourally identical to the audit pool, so
    # any helper that opens its own pool from settings cannot reach shared state.
    assert settings.database_url == scoped_dsn
    scoped = await asyncpg.connect(dsn=scoped_dsn)
    try:
        scoped_schema = await scoped.fetchval("SELECT current_schema()")
    finally:
        await scoped.close()
    async with pool.acquire() as audit_conn:
        assert await audit_conn.fetchval("SELECT current_schema()") == scoped_schema
    assert scoped_schema.startswith(AUDIT_SCHEMA_PREFIX)

    encryption = ContentEncryption(settings.daemon_encryption_key)

    async def fake_process_extraction(
        store: MemoryStore,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        text: str,
    ) -> tuple[bool, list[dict[str, object]]]:
        memory = await store.insert_memory(
            user_id=user_id,
            content=f"AUDIT canonical memory for {conversation_id}",
            category="fact",
            source_type="conversation",
            embedding=_test_vector(0.25),
            embedding_model="benchmark-audit-document",
            source_conversation_id=conversation_id,
            memory_slot="profile",
        )
        _ = await store.log_extraction(
            user_id=user_id,
            conversation_id=conversation_id,
            input_snippet=text[:1000],
            extracted_facts=[
                {
                    "content": f"Audit fact for {conversation_id}",
                    "category": "fact",
                    "confidence": 1.0,
                    "slot": "profile",
                }
            ],
            dedup_results={"new": 1, "merged": 0, "superseded": 0},
            model_used="benchmark-audit-extractor",
        )
        return True, [memory]

    async def fake_embed_query(_text: str) -> list[float]:
        return _test_vector(0.25)

    async def fake_answer(
        _question_text: str, memories: list[dict[str, object]], **kwargs: Any
    ) -> str:
        if not memories:
            return "no-memory"
        content = memories[0].get("content")
        assert isinstance(content, str)
        return content

    async def fake_judge(
        _question_text: str, _hypothesis: str, _reference: str, **kwargs: Any
    ) -> str:
        return "correct"

    async def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        return [_test_vector(0.35) for _ in texts]

    monkeypatch.setattr("tests.longmemeval.ingest.process_extraction", fake_process_extraction)
    monkeypatch.setattr("tests.longmemeval.evaluate.embed_query", fake_embed_query)
    monkeypatch.setattr("tests.longmemeval.evaluate.answer_with_llm", fake_answer)
    monkeypatch.setattr("tests.longmemeval.evaluate.judge_answer", fake_judge)
    monkeypatch.setattr("orchestrator.eval.chunk_harness.embed_documents", fake_embed_documents)

    canonical_user_id: uuid.UUID | None = None
    fast_user_id: uuid.UUID | None = None

    try:
        canonical_store = MemoryStore(pool, encryption)
        fast_store = MemoryStore(pool, encryption)

        canonical_rows: list[SnapshotRow] = []
        fast_rows: list[SnapshotRow] = []
        canonical_null_logs: dict[str, int] = {}
        fast_null_logs: dict[str, int] = {}

        canonical_user_id = uuid.uuid4()
        await _insert_user(
            pool,
            user_id=canonical_user_id,
            email=f"teardown-audit-canonical+{canonical_user_id.hex}@daemon.test",
            name="teardown_audit_canonical",
        )

        canonical_rows.append(
            SnapshotRow(
                label="baseline",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="fresh isolated audit user before case 1",
            )
        )

        canonical_case_1 = await ingest_session(
            canonical_store,
            pool,
            canonical_user_id,
            "canonical-session-1",
            [
                {"role": "user", "content": "The user keeps a red notebook."},
                {
                    "role": "assistant",
                    "content": "I can remember the notebook color.",
                },
            ],
            0,
        )
        canonical_case_1_conversation_id = canonical_case_1.get("conversation_id")
        assert isinstance(canonical_case_1_conversation_id, str)
        canonical_case_1_conversation = uuid.UUID(canonical_case_1_conversation_id)
        _ = await evaluate_single(
            canonical_store,
            "canonical-q1",
            "What color is the notebook?",
            "red",
            "IE-user",
            log_retrieval=True,
            allowed_source_conversation_ids=[canonical_case_1_conversation],
            user_id=canonical_user_id,
        )
        await _wait_for_table_count(
            pool,
            table="retrieval_log",
            user_id=canonical_user_id,
            expected=1,
        )
        canonical_rows.append(
            SnapshotRow(
                label="after case 1 settled",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="no teardown ran after case 1",
            )
        )
        canonical_null_logs["after_case1"] = await _retrieval_log_null_conversation_count(
            pool, canonical_user_id
        )

        canonical_case_2 = await ingest_session(
            canonical_store,
            pool,
            canonical_user_id,
            "canonical-session-2",
            [
                {"role": "user", "content": "The user moved to Lisbon."},
                {"role": "assistant", "content": "I can remember the city."},
            ],
            1,
        )
        canonical_case_2_conversation_id = canonical_case_2.get("conversation_id")
        assert isinstance(canonical_case_2_conversation_id, str)
        canonical_case_2_conversation = uuid.UUID(canonical_case_2_conversation_id)
        _ = await evaluate_single(
            canonical_store,
            "canonical-q2",
            "Where did the user move?",
            "Lisbon",
            "IE-user",
            log_retrieval=True,
            allowed_source_conversation_ids=[canonical_case_2_conversation],
            user_id=canonical_user_id,
        )
        await _wait_for_table_count(
            pool,
            table="retrieval_log",
            user_id=canonical_user_id,
            expected=2,
        )
        canonical_rows.append(
            SnapshotRow(
                label="after case 2 settled",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="case 2 adds another full row-set on top of case 1",
            )
        )
        canonical_null_logs["after_case2"] = await _retrieval_log_null_conversation_count(
            pool, canonical_user_id
        )

        await _delete_user(pool, canonical_user_id)
        canonical_rows.append(
            SnapshotRow(
                label="after manual user delete",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="manual cleanup proves FK cascades work when invoked",
            )
        )
        canonical_user_id = None

        fast_user_id = uuid.uuid4()
        await _insert_user(
            pool,
            user_id=fast_user_id,
            email=f"teardown-audit-fast+{fast_user_id.hex}@daemon.test",
            name="teardown_audit_fast",
        )

        fast_rows.append(
            SnapshotRow(
                label="baseline",
                counts=await _snapshot_counts(pool, fast_user_id),
                note="fresh isolated fast-lane user before case 1",
            )
        )

        original_fast_log_retrieval = fast_store.log_retrieval
        active_gate: RetrievalGate | None = None

        async def delayed_log_retrieval(
            user_id: uuid.UUID,
            query_text: str,
            query_embedding_model: str,
            query_embedding: list[float] | None,
            candidate_memory_ids: list[uuid.UUID],
            candidate_scores: dict[str, object],
            selected_memory_ids: list[uuid.UUID],
            l0_included: bool,
            latency_ms: int,
            *,
            conversation_id: uuid.UUID | None = None,
            retrieval_context: str | None = None,
            retrieval_triggered_by: str | None = None,
        ) -> dict[str, Any]:
            gate = active_gate
            assert gate is not None
            gate.entered.set()
            await gate.release.wait()
            return await original_fast_log_retrieval(
                user_id=user_id,
                query_text=query_text,
                query_embedding_model=query_embedding_model,
                query_embedding=query_embedding,
                candidate_memory_ids=candidate_memory_ids,
                candidate_scores=candidate_scores,
                selected_memory_ids=selected_memory_ids,
                l0_included=l0_included,
                latency_ms=latency_ms,
                conversation_id=conversation_id,
                retrieval_context=retrieval_context,
                retrieval_triggered_by=retrieval_triggered_by,
            )

        monkeypatch.setattr(fast_store, "log_retrieval", delayed_log_retrieval)

        fast_cases = [
            (
                "case 1",
                {
                    "question_id": "fast-q1",
                    "haystack_session_ids": ["fast-session-1"],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "The train leaves at dawn.",
                            },
                            {
                                "role": "assistant",
                                "content": "I will remember the departure time.",
                            },
                        ]
                    ],
                },
                "When does the train leave?",
                "dawn",
            ),
            (
                "case 2",
                {
                    "question_id": "fast-q2",
                    "haystack_session_ids": ["fast-session-2"],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "The concert is on Friday.",
                            },
                            {
                                "role": "assistant",
                                "content": "I will remember the day.",
                            },
                        ]
                    ],
                },
                "What day is the concert?",
                "Friday",
            ),
        ]

        for index, (label, entry, question_text, reference) in enumerate(fast_cases, start=1):
            await cleanup_benchmark_state(pool, fast_user_id)
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after pre-case cleanup",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note=(
                        "baseline cleanup before case 1"
                        if index == 1
                        else "this pre-case cleanup removes any leftover row from the prior case"
                    ),
                )
            )

            active_gate = RetrievalGate(asyncio.Event(), asyncio.Event())
            conversation_ids, _ = await ingest_question_chunks(
                store=fast_store,
                pool=pool,
                encryption=encryption,
                user_id=fast_user_id,
                question_id=str(entry["question_id"]),
                entry=entry,
                chunk_max_chars=4000,
                overlap_turns=2,
            )
            _ = await evaluate_single(
                fast_store,
                str(entry["question_id"]),
                question_text,
                reference,
                "IE-user",
                log_retrieval=True,
                allowed_source_conversation_ids=conversation_ids,
                user_id=fast_user_id,
            )
            _ = await asyncio.wait_for(active_gate.entered.wait(), timeout=5.0)

            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after evaluate return",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="retrieval_log task is queued but still blocked behind the audit gate",
                )
            )

            await cleanup_benchmark_state(pool, fast_user_id)
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after post-case cleanup",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="cleanup removed synchronous tables before the retrieval-log task was released",
                )
            )

            active_gate.release.set()
            await _wait_for_table_count(
                pool,
                table="retrieval_log",
                user_id=fast_user_id,
                expected=1,
            )
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after delayed retrieval flush",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="late retrieval_log insert survives teardown while all other user tables stay at zero",
                )
            )
            fast_null_logs[
                f"after_case{index}_release"
            ] = await _retrieval_log_null_conversation_count(pool, fast_user_id)

        await _delete_user(pool, fast_user_id)
        fast_rows.append(
            SnapshotRow(
                label="after end-of-run user delete",
                counts=await _snapshot_counts(pool, fast_user_id),
                note="final user deletion clears the last leaked retrieval row",
            )
        )
        fast_user_id = None

        report = _render_report(
            canonical_rows=canonical_rows,
            fast_rows=fast_rows,
            canonical_null_logs=canonical_null_logs,
            fast_null_logs=fast_null_logs,
        )
        _ = REPORT_PATH.write_text(report)

        assert "missing per-case teardown" in report.lower()
        assert "async bleed" in report.lower()
        assert "end-of-run user deletion" in report.lower()
        assert canonical_rows[1].counts["conversations"] == 1
        assert canonical_rows[2].counts["conversations"] == 2
        assert canonical_rows[3].counts["retrieval_log"] == 0
        assert fast_rows[2].counts["retrieval_log"] == 0
        assert fast_rows[3].counts["retrieval_log"] == 0
        assert fast_rows[4].counts["retrieval_log"] == 1
        assert fast_rows[-1].counts["retrieval_log"] == 0
    finally:
        if canonical_user_id is not None:
            await _delete_user(pool, canonical_user_id)
        if fast_user_id is not None:
            await _delete_user(pool, fast_user_id)
