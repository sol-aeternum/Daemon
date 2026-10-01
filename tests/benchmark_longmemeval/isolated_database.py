"""Shared isolated test-database provisioning for benchmark longmemeval tests.

Every consumer must configure ``ENTITLEMENTS_TEST_DATABASE_URL`` explicitly: a
DSN pointing at a disposable PostgreSQL server that the operator has designated
for destructive schema replay. The application ``DATABASE_URL`` is never read
here, and an unreachable or invalid DSN fails the test with a
credential-redacted error instead of silently skipping or falling back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

import asyncpg
import pytest
import pytest_asyncio

MIGRATIONS_DIR_UP_LEVELS = 2
TEST_DSN_ENV_VAR = "ENTITLEMENTS_TEST_DATABASE_URL"
DEFAULT_SCHEMA_PREFIX = "bench_isolated_"
_POOL_CLOSE_TIMEOUT_SECONDS = 10.0

LOGGER = logging.getLogger(__name__)

SCHEMA_NAME_MAX_LENGTH = 63
SCHEMA_PREFIX_MAX_LENGTH = 31  # prefix + 32-char uuid hex must fit in 63 chars
_SCHEMA_PREFIX_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*", re.ASCII)


def _validate_schema_prefix(schema_prefix: str) -> None:
    """Eagerly reject unsafe schema prefixes before any database is touched.

    The prefix is embedded inside a double-quoted PostgreSQL identifier, so it
    must be a plain ASCII identifier fragment. The total schema name is
    ``prefix + uuid4().hex`` (32 chars) and PostgreSQL caps identifiers at 63
    bytes, so ``len(prefix) + 32 <= 63``.
    """
    if not _SCHEMA_PREFIX_PATTERN.fullmatch(schema_prefix):
        raise ValueError(
            "schema_prefix must be a non-empty ASCII identifier fragment "
            "(first char a letter, then letters/digits/underscores): got "
            f"{schema_prefix!r}"
        )
    if len(schema_prefix) > SCHEMA_PREFIX_MAX_LENGTH:
        raise ValueError(
            f"schema_prefix longer than {SCHEMA_PREFIX_MAX_LENGTH} chars would truncate or "
            f"overflow the {SCHEMA_NAME_MAX_LENGTH}-char PostgreSQL identifier limit: got "
            f"{len(schema_prefix)} chars"
        )


def _test_dsn() -> str:
    """Return the explicitly configured disposable test DSN, or skip.

    The skip happens before any connection attempt. This deliberately never
    falls back to the application ``DATABASE_URL``: provisioning here replays
    every migration and creates/drops schemas, so it must only ever touch a
    database the operator explicitly designated as disposable. DSN values are
    passed through unchanged (including Unix-socket DSNs with ``?host=``
    query parameters); no hostname is rewritten to loopback.
    """
    dsn = os.environ.get(TEST_DSN_ENV_VAR, "").strip()
    if not dsn:
        pytest.skip(f"requires isolated {TEST_DSN_ENV_VAR}")
    return dsn


async def _connect(dsn: str) -> asyncpg.Connection:
    """Connect for schema administration, failing with a redacted error.

    A configured-but-unreachable or invalid DSN is a configuration error, not
    a skip: falling back or skipping here would let either the application
    database or an absent environment silently pass the audit. The failure is
    a fixed message naming only the exception type — neither the raw DSN nor
    any exception payload (which can embed connection strings).
    """
    try:
        return await asyncpg.connect(dsn=dsn, timeout=5)
    except (OSError, TimeoutError, ValueError, asyncpg.PostgresError) as exc:
        raise pytest.fail.Exception(
            "Configured isolated test database is unreachable or the DSN is "
            f"invalid: {type(exc).__name__}",
            pytrace=False,
        ) from None


def _scoped_dsn(dsn: str, schema: str) -> str:
    """Pin ``search_path`` on the DSN itself, not just on the provisioning pool.

    Tests may monkeypatch ``DATABASE_URL`` with the scoped DSN so harness code
    that opens its own pool from ``get_settings().database_url`` binds to the
    isolated schema instead of ``public``. Any existing query parameters (for
    example a Unix-socket ``?host=`` value) are preserved and the option is
    appended with the correct separator.
    """
    options = urlencode({"options": f"-csearch_path={schema},public"})
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}{options}"


async def _apply_migrations(conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy) -> None:
    """Apply the shipped migrations into the isolated schema.

    ``scripts/migrate.py`` bookkeeping (``_migrations``) is intentionally
    skipped: with ``public`` still on ``search_path`` an ``IF NOT EXISTS``
    create would bind to the shared table and the bookkeeping INSERT would
    write outside the isolated schema. The schema is disposable, so it simply
    replays every migration.
    """
    migrations_dir = Path(__file__).resolve().parents[MIGRATIONS_DIR_UP_LEVELS] / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))
    assert migration_files, f"no migration files found under {migrations_dir}"
    for path in migration_files:
        async with conn.transaction():
            await conn.execute(path.read_text())


async def _public_relations(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
) -> frozenset[tuple[str, str]]:
    """Fingerprint every relation in the shared ``public`` schema of the test DB.

    Replaying migrations must not create, drop, or rename anything in
    ``public`` (notably ``CREATE EXTENSION`` in ``001``, which is expected to
    be a no-op because the extensions are already installed). Comparing this
    before and after the replay is what proves schema isolation rather than
    assuming it.
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


async def _assert_migration_probes(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    schema: str,
    *,
    audited_tables: list[str] | None = None,
) -> None:
    """Run the shared isolation + metadata contract probes against the schema."""
    public_before = await _public_relations(conn)
    await _apply_migrations(conn)
    public_after = await _public_relations(conn)
    added = sorted(public_after - public_before)
    removed = sorted(public_before - public_after)
    assert not added, f"migration replay created relations in public: {added}"
    assert not removed, f"migration replay dropped relations in public: {removed}"

    if audited_tables:
        await assert_tables_isolated_in_schema(conn, schema, audited_tables)
    await assert_memory_metadata_contract(conn)


async def assert_tables_isolated_in_schema(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    schema: str,
    tables: list[str],
) -> None:
    """Fail loudly if any listed table would resolve outside the isolated schema.

    ``public`` stays on ``search_path`` so pgvector/pgcrypto types resolve,
    which means an unqualified table name could silently bind to the shared
    schema. Every listed table must therefore exist inside the isolated schema.
    """
    rows = await conn.fetch(
        """
        SELECT c.relname AS name, n.nspname AS schema_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND c.oid IN (SELECT to_regclass(name) FROM unnest($1::text[]) AS name)
        """,
        tables,
    )
    located = {row["name"]: row["schema_name"] for row in rows}

    missing = [table for table in tables if table not in located]
    assert not missing, f"tables absent after migration replay: {missing}"

    outside = sorted(name for name, name_schema in located.items() if name_schema != schema)
    assert not outside, f"tables resolved outside schema {schema}: {outside}"


async def assert_memory_metadata_contract(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
) -> None:
    """Assert ``migrations/040_memory_metadata.sql`` really landed.

    The column is required by ``MemoryStore.supersede_memory()``,
    ``MemoryStore.update_memory_metadata()`` and the chunk harness. Asserting
    the catalog contract *and* a real omitted-column write catches a migration
    that is present but wrong (nullable, wrong type, or a NULL default), which
    a mere "column exists" check would miss.
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


def provision_isolated_database(
    schema_prefix: str,
    *,
    audited_tables: list[str] | None = None,
) -> AsyncGenerator[tuple[asyncpg.Pool, str], None]:
    """Validate arguments eagerly and return the provisioning async generator.

    Consumers may safely pass the DSN only via ``ENTITLEMENTS_TEST_DATABASE_URL``;
    the application ``DATABASE_URL`` is never read. The schema name is
    ``schema_prefix + uuid4().hex``. Consumers should drive the returned
    generator directly (explicit ``aclose()``) or via
    ``isolated_database_fixture()`` so teardown is awaited deterministically
    rather than deferred to garbage collection.
    """
    _validate_schema_prefix(schema_prefix)
    return _provision_isolated_database(schema_prefix, audited_tables)


async def _provision_isolated_database(
    schema_prefix: str,
    audited_tables: list[str] | None,
) -> AsyncGenerator[tuple[asyncpg.Pool, str], None]:
    """Disposable, migration-complete schema for PostgreSQL-backed tests.

    The lifecycle contract is strict:

    - the pool is fully closed (or terminated) before the schema is dropped;
    - a pending pool is never left active while the drop runs;
    - only the schema this call actually created is dropped;
    - cleanup failures are never silently forgotten.
    """
    _validate_schema_prefix(schema_prefix)
    dsn = _test_dsn()
    schema = f"{schema_prefix}{uuid.uuid4().hex}"
    admin = await _connect(dsn)
    pool: asyncpg.Pool | None = None
    schema_created = False
    # Set when neither graceful close nor terminate could be confirmed: the
    # pool may still hold pending connections, so the owned schema must NOT
    # be dropped.
    pool_state_uncertain = False

    cleanup_error: BaseException | None = None

    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            server_settings={"search_path": f"{schema}, public"},
        )
        async with pool.acquire() as conn:
            await _assert_migration_probes(conn, schema, audited_tables=audited_tables)
        yield pool, _scoped_dsn(dsn, schema)
    finally:
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=_POOL_CLOSE_TIMEOUT_SECONDS)
            except BaseException as close_exc:
                cleanup_error = close_exc
                # Never leave a pending pool active across the DROP: if the
                # graceful close failed or timed out, hard-terminate first.
                try:
                    pool.terminate()
                except BaseException as term_exc:
                    LOGGER.warning("isolated database pool terminate() also failed during cleanup")
                    cleanup_error = cleanup_error or term_exc
                    # Neither close nor terminate succeeded: the pool may
                    # still be active, so dropping the schema now could kill
                    # live connections midway. The disposable rows remain;
                    # the RuntimeError below reports this explicitly.
                    pool_state_uncertain = True
            pool = None

        if schema_created and not pool_state_uncertain:
            try:
                await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except BaseException as drop_exc:
                if cleanup_error is None:
                    cleanup_error = drop_exc
                else:
                    LOGGER.warning("isolated database schema drop failed during cleanup")

        try:
            await admin.close()
        except BaseException as admin_exc:
            if cleanup_error is None:
                cleanup_error = admin_exc

        # A cleanup failure is never silently forgotten. If the test body also
        # failed, the cleanup error replaces it here and the original failure
        # stays attached as the contextual "__context__" chain.
        if cleanup_error is not None:
            raise RuntimeError(
                "isolated database cleanup failed; disposable data may remain"
            ) from cleanup_error


def isolated_database_fixture(
    schema_prefix: str = DEFAULT_SCHEMA_PREFIX,
    *,
    audited_tables: list[str] | None = None,
) -> Any:
    """Build a pytest fixture yielding ``(pool, scoped_dsn)``.

    Importing consumers share one implementation, so pytest discovers the
    fixture through their module-level import and lifecycle behaviour stays
    identical for every lane (teardown audit, retrieval smoke, persistence
    tests). The inner provisioning generator's teardown is explicitly
    ``await gen.aclose()`` at fixture-finalization time — it is never left
    to garbage collection or event-loop shutdown.
    """
    _validate_schema_prefix(schema_prefix)

    @pytest_asyncio.fixture
    async def _isolated_database() -> AsyncIterator[tuple[asyncpg.Pool, str]]:
        gen = provision_isolated_database(schema_prefix, audited_tables=audited_tables)
        try:
            pool, scoped_dsn = await gen.__anext__()
        except BaseException:
            # The generator already finished (it raised); no pending schema.
            await gen.aclose()
            raise
        try:
            yield pool, scoped_dsn
        finally:
            # Deterministically awaited teardown: closes the pool, then drops
            # the owned schema, then closes the admin connection.
            await gen.aclose()

    return _isolated_database
