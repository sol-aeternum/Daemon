"""Metadata JSONB isolation: unit guards for the shared isolated-DB helper and
migration-backed persistence regressions for ``memories.metadata``.

The unit-guard half mocks ``asyncpg.connect``/``asyncpg.create_pool`` so it runs
with no database at all: it pins the fixture contract — explicit
``ENTITLEMENTS_TEST_DATABASE_URL`` only, skip before any connection, hard
credential-redacted failure on an unreachable DSN, no application
``DATABASE_URL`` fallback, and the close-then-drop teardown lifecycle.

The persistence half reuses the same shared fixture against a real disposable
PostgreSQL schema (full migration replay) to prove the actual ``MemoryStore``
insert/supersede write paths behind migration 040.
"""

from __future__ import annotations

import json
import secrets
import uuid
from contextlib import aclosing
from typing import Any, cast
from urllib.parse import unquote_plus

import asyncpg
import pytest
from cryptography.fernet import Fernet

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.benchmark_longmemeval.isolated_database import (
    TEST_DSN_ENV_VAR,
    assert_memory_metadata_contract,
    isolated_database_fixture,
    provision_isolated_database,
)

SCHEMA_PREFIX = "metadata_isolation_"
OMIT_METADATA = object()

FAKE_DSN = "postgresql://iso_user:iso_secret@iso-host/iso_db"

# DB-backed regression tests below discover the shared fixture through this
# module-level import.
isolated_meta_pool = isolated_database_fixture(SCHEMA_PREFIX, audited_tables=["users", "memories"])


# ---------------------------------------------------------------------------
# Unit guards: fixture contract, no database required
# ---------------------------------------------------------------------------


class FakePoolConn:
    """Stands in for the connection yielded by ``FakePool.acquire()``.

    Probes are stubbed in every unit test, so the real probe methods must
    never run against this double.
    """

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        raise AssertionError("real probes must be stubbed in unit-guard tests")

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        raise AssertionError("real probes must be stubbed in unit-guard tests")


class FakeAcquireContext:
    """Async context manager mirroring ``asyncpg.Pool.acquire()``."""

    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakePoolConn:
        self._pool.events.append("acquire")
        return FakePoolConn()

    async def __aexit__(self, *exc: Any) -> bool:
        self._pool.events.append("release")
        return False


class FakePool:
    """Pool double sharing one timeline with the admin double.

    ``events`` is the single ordered lifecycle log shared with
    ``FakeAdminConn`` so "close strictly before drop" is asserted against one
    real timeline instead of two separate arrays.
    """

    def __init__(
        self,
        events: list[str],
        *,
        close_failure: BaseException | None = None,
        terminate_failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.close_failure = close_failure
        self.terminate_failure = terminate_failure
        self.closed = 0
        self.terminated = 0

    def acquire(self) -> FakeAcquireContext:
        return FakeAcquireContext(self)

    async def close(self) -> None:
        self.events.append("pool.close")
        self.closed += 1
        if self.close_failure is not None:
            raise self.close_failure

    def terminate(self) -> None:
        self.events.append("pool.terminate")
        self.terminated += 1
        if self.terminate_failure is not None:
            raise self.terminate_failure


class FakeAdminConn:
    """Admin double writing into the shared lifecycle timeline."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.executed: list[str] = []
        self.closed = 0

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)
        self.events.append(f"admin.execute:{sql.split()[0]}")

    async def close(self) -> None:
        self.events.append("admin.close")
        self.closed += 1


class RecordingProbe:
    """Captures ``_assert_migration_probes`` invocations."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, Any]] = []

    async def __call__(self, conn: Any, schema: str, *, audited_tables: Any = None) -> None:
        self.calls.append((conn, schema, audited_tables))


def _install_fake_asyncpg(
    monkeypatch: pytest.MonkeyPatch,
    *,
    admin: FakeAdminConn,
    pool: FakePool | None,
    fail_connect: bool = False,
) -> list[str]:
    """Replace ``asyncpg.connect``/``create_pool`` behind the helper module."""
    dsn_capture: list[str] = []

    async def fake_connect(dsn: str, timeout: float) -> FakeAdminConn:
        dsn_capture.append(dsn)
        if fail_connect:
            raise OSError("connect failed (simulated)")
        return admin

    async def fake_create_pool(dsn: str, **kwargs: Any) -> FakePool:
        dsn_capture.append(f"create_pool:{dsn}")
        assert pool is not None, "create_pool must not be reached in this scenario"
        return pool

    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database.asyncpg.connect", fake_connect
    )
    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database.asyncpg.create_pool", fake_create_pool
    )
    return dsn_capture


@pytest.mark.asyncio
async def test_provision_skips_before_any_connection_without_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing explicit DSN skips BEFORE any connection — never an app fallback."""
    monkeypatch.delenv(TEST_DSN_ENV_VAR, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app_user:app_pw@app-host/app_db")

    events: list[str] = []
    admin = FakeAdminConn(events)
    dsn_capture = _install_fake_asyncpg(monkeypatch, admin=admin, pool=None)

    gen = provision_isolated_database("guard_")
    with pytest.raises(pytest.skip.Exception):
        await gen.__anext__()

    assert dsn_capture == [], "no connection attempt may be made without the explicit env var"
    assert events == [], "nothing may be provisioned when the explicit env var is missing"
    await gen.aclose()


@pytest.mark.asyncio
async def test_provision_uses_configured_dsn_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured DSN is used as-is; Unix-socket/query DSNs are not rewritten."""
    monkeypatch.setenv(
        TEST_DSN_ENV_VAR, "postgresql://iso_user:iso_secret@/var/run/postgresql/iso_db"
    )

    events: list[str] = []
    admin = FakeAdminConn(events)
    dsn_capture: list[str] = []

    async def fake_connect(dsn: str, timeout: float) -> FakeAdminConn:
        dsn_capture.append(dsn)
        return admin

    async def fake_create_pool(dsn: str, **kwargs: Any) -> FakePool:
        dsn_capture.append(f"create_pool:{dsn}")
        raise OSError("pool creation refused (simulated)")

    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database.asyncpg.connect", fake_connect
    )
    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database.asyncpg.create_pool", fake_create_pool
    )

    gen = provision_isolated_database("guard_")
    with pytest.raises(OSError, match="pool creation refused"):
        await gen.__anext__()

    assert dsn_capture[:2] == [
        "postgresql://iso_user:iso_secret@/var/run/postgresql/iso_db",
        "create_pool:postgresql://iso_user:iso_secret@/var/run/postgresql/iso_db",
    ], "the DSN must pass through unchanged, with no loopback rewrite"
    await gen.aclose()
    # The owned schema was created before pool creation failed, so the fixture
    # cleans it up; the DSN itself was never rewritten.
    assert any(sql.startswith("DROP SCHEMA") for sql in admin.executed)
    assert admin.closed == 1


@pytest.mark.asyncio
async def test_provision_fails_redacted_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable DSN FAILs with a fixed, credential-redacted error."""
    monkeypatch.setenv(TEST_DSN_ENV_VAR, "postgresql://iso_user:iso_secret@unreachable-host/iso_db")

    events: list[str] = []
    admin = FakeAdminConn(events)
    dsn_capture = _install_fake_asyncpg(monkeypatch, admin=admin, pool=None, fail_connect=True)

    gen = provision_isolated_database("guard_")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        await gen.__anext__()

    message = str(excinfo.value)
    assert "iso_secret" not in message
    assert "iso_user" not in message
    assert "unreachable-host" not in message, "the fixed error must not echo the DSN"
    assert "iso_db" not in message
    assert "OSError" in message
    assert dsn_capture != [], "a configured DSN is actually attempted, then hard-fails"
    assert admin.closed == 0
    await gen.aclose()


@pytest.mark.asyncio
async def test_provision_fails_redacted_on_malformed_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed (ValueError) DSN FAILs with the same fixed redacted error."""
    monkeypatch.setenv(TEST_DSN_ENV_VAR, "postgresql://iso_user:iso_secret@[bad-port/iso_db")

    events: list[str] = []
    admin = FakeAdminConn(events)
    dsn_capture = _install_fake_asyncpg(monkeypatch, admin=admin, pool=None, fail_connect=False)

    async def raising_connect(dsn: str, timeout: float) -> FakeAdminConn:
        dsn_capture.append(dsn)
        # asyncpg surfaces malformed DSNs/hosts (including bad Unix-socket
        # specs or unparseable ports) as ValueError.
        raise ValueError("invalid DSN components (simulated)")

    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database.asyncpg.connect", raising_connect
    )

    gen = provision_isolated_database("guard_")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        await gen.__anext__()

    message = str(excinfo.value)
    assert "iso_secret" not in message
    assert "bad-port" not in message, "the fixed error must not echo DSN fragments"
    assert "ValueError" in message
    await gen.aclose()


@pytest.mark.parametrize(
    ("bad_prefix",),
    [
        ('foo"; DROP SCHEMA public',),
        ("schéma_",),
        ("123leading_digit",),
        ("{brace}",),
        ("a" * 32,),
    ],
)
def test_provision_rejects_unsafe_schema_prefix(bad_prefix: str) -> None:
    """Prefix injection/truncation is rejected eagerly, before any connection."""
    with pytest.raises(ValueError):
        provision_isolated_database(bad_prefix)


@pytest.mark.asyncio
async def test_provision_lifecycle_close_before_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owned-schema lifecycle: create → probes → close pool → drop → admin close."""
    monkeypatch.setenv(TEST_DSN_ENV_VAR, FAKE_DSN)

    events: list[str] = []
    admin = FakeAdminConn(events)
    pool = FakePool(events)
    dsn_capture = _install_fake_asyncpg(monkeypatch, admin=admin, pool=pool)
    probe = RecordingProbe()
    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database._assert_migration_probes", probe
    )

    gen = provision_isolated_database("guard_", audited_tables=["memories"])
    async with aclosing(gen):
        yielded_pool, scoped_dsn = await gen.__anext__()

    assert yielded_pool is pool
    for entry in dsn_capture:
        if entry.startswith("create_pool:"):
            continue
        assert entry == FAKE_DSN, "admin and helper pools must use the configured DSN verbatim"

    create_sql = [sql for sql in admin.executed if sql.startswith("CREATE SCHEMA")]
    drop_sql = [sql for sql in admin.executed if sql.startswith("DROP SCHEMA")]
    assert len(create_sql) == 1
    assert len(drop_sql) == 1
    schema_name = create_sql[0].split('"')[1]
    assert drop_sql[0] == f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'

    assert scoped_dsn.startswith(FAKE_DSN)
    assert "search_path=" in unquote_plus(scoped_dsn)
    assert schema_name in scoped_dsn

    assert len(probe.calls) == 1
    assert probe.calls[0][1] == schema_name
    assert probe.calls[0][2] == ["memories"]

    # Single ordered timeline: acquire → pool.close → admin DROP → admin.close.
    assert events == [
        "admin.execute:CREATE",
        "acquire",
        "release",
        "pool.close",
        "admin.execute:DROP",
        "admin.close",
    ], events


@pytest.mark.asyncio
async def test_provision_terminates_pool_then_still_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool that fails to close is terminated and the schema is still dropped.

    The cleanup failure must not be silently forgotten: it surfaces at the
    awaited generator close (context exit), chained from the close failure.
    """
    monkeypatch.setenv(TEST_DSN_ENV_VAR, FAKE_DSN)

    events: list[str] = []
    admin = FakeAdminConn(events)
    pool = FakePool(events, close_failure=RuntimeError("graceful close timed out (simulated)"))
    _install_fake_asyncpg(monkeypatch, admin=admin, pool=pool)

    async def probe_stub(conn: Any, schema: str, *, audited_tables: Any = None) -> None:
        _ = (conn, schema, audited_tables)

    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database._assert_migration_probes", probe_stub
    )

    gen = provision_isolated_database("guard_")
    pool_yielded, _scoped = await gen.__anext__()  # pool acquired successfully
    assert pool_yielded is pool

    # The cleanup failure is reported on the awaited generator close, not on
    # the first yield itself.
    with pytest.raises(RuntimeError, match="isolated database cleanup failed"):
        await gen.aclose()

    assert events[3:5] == ["pool.close", "pool.terminate"]
    assert any(sql.startswith("DROP SCHEMA") for sql in admin.executed), (
        "the owned schema must still be dropped after the successful terminate"
    )
    assert "admin.close" in events


@pytest.mark.asyncio
async def test_provision_never_drops_when_both_close_and_terminate_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If close AND terminate both fail the pool may still be pending: no DROP.

    The owned schema must NOT be dropped while pool activity is unconfirmed;
    the admin connection is still closed and the cleanup failure is raised so
    the test reports data uncertainty explicitly instead of claiming a drop.
    """
    monkeypatch.setenv(TEST_DSN_ENV_VAR, FAKE_DSN)

    events: list[str] = []
    admin = FakeAdminConn(events)
    pool = FakePool(
        events,
        close_failure=RuntimeError("graceful close timed out (simulated)"),
        terminate_failure=RuntimeError("terminate rejected (simulated)"),
    )
    _install_fake_asyncpg(monkeypatch, admin=admin, pool=pool)

    async def probe_stub(conn: Any, schema: str, *, audited_tables: Any = None) -> None:
        _ = (conn, schema, audited_tables)

    monkeypatch.setattr(
        "tests.benchmark_longmemeval.isolated_database._assert_migration_probes", probe_stub
    )

    gen = provision_isolated_database("guard_")
    pool_yielded, _scoped = await gen.__anext__()
    assert pool_yielded is pool
    with pytest.raises(RuntimeError, match="isolated database cleanup failed"):
        await gen.aclose()

    assert events[3:5] == ["pool.close", "pool.terminate"], (
        "both graceful close and terminate were attempted exactly once"
    )
    assert pool.terminated == 1
    assert not any(sql.startswith("DROP SCHEMA") for sql in admin.executed), (
        "the owned schema must not be dropped while the pool state is uncertain"
    )
    assert admin.closed == 1, "the admin connection is closed even when the drop is withheld"


@pytest.mark.asyncio
async def test_provision_failing_create_drops_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure before the schema exists must never drop an unowned name."""
    monkeypatch.setenv(TEST_DSN_ENV_VAR, FAKE_DSN)

    class StrictAdminConn(FakeAdminConn):
        async def execute(self, sql: str) -> None:
            if sql.startswith("CREATE SCHEMA"):
                raise RuntimeError("server rejected schema creation")
            await super().execute(sql)

    events: list[str] = []
    admin = StrictAdminConn(events)
    _install_fake_asyncpg(monkeypatch, admin=admin, pool=None)

    gen = provision_isolated_database("guard_")
    async with aclosing(gen):
        with pytest.raises(RuntimeError, match="server rejected schema creation"):
            await gen.__anext__()

    assert not any(sql.startswith("DROP SCHEMA") for sql in admin.executed), (
        "a schema that was never created must not be dropped"
    )
    assert admin.closed == 1


# ---------------------------------------------------------------------------
# Migration-backed persistence regressions (real disposable schema)
# ---------------------------------------------------------------------------


def _fresh_runtime_crypto(monkeypatch: pytest.MonkeyPatch) -> ContentEncryption:
    """Fresh per-run Fernet key + hash pepper injected via process settings.

    The isolated database fixture never reads the application
    ``DATABASE_URL``/storage keys; runtime crypto doubles are generated
    in-process and injected through the environment so every runtime call
    below uses fresh, disposable secrets.
    """
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", secrets.token_urlsafe(48))
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.daemon_encryption_key
    assert settings.daemon_auth_pepper
    return ContentEncryption(settings.daemon_encryption_key)


async def _insert_isolated_user(pool: asyncpg.Pool, label: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        f"metadata-isolation+{user_id.hex}@daemon.test",
        label,
    )
    return user_id


def _decoded_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return cast(dict[str, Any], json.loads(value))
    return cast(dict[str, Any], value)


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_conn", [False, True], ids=["pool_path", "caller_conn_path"])
async def test_insert_memory_persists_metadata_contract(
    caller_conn: bool,
    monkeypatch: pytest.MonkeyPatch,
    isolated_meta_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Real ``insert_memory`` writes honor the 040 contract, conn path included."""
    pool, _scoped_dsn = isolated_meta_pool
    encryption = _fresh_runtime_crypto(monkeypatch)
    store = MemoryStore(pool, encryption)
    user_id = await _insert_isolated_user(pool, "metadata_persistence_insert")

    nested = {"provenance": {"extraction_model": "unit", "score": 0.75}}
    metadata_cases: list[tuple[str, Any, dict[str, Any]]] = [
        ("omitted", OMIT_METADATA, {}),
        ("none", None, {}),
        ("empty", {}, {}),
        ("nested", nested, nested),
    ]

    try:
        for label, metadata_value, expected in metadata_cases:
            kwargs: dict[str, Any] = {}
            if metadata_value is not OMIT_METADATA:
                kwargs["metadata"] = metadata_value

            if caller_conn:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        memory = await store.insert_memory(
                            user_id=user_id,
                            content=f"insert case {label}",
                            category="fact",
                            source_type="import",
                            embedding=None,
                            conn=conn,
                            **kwargs,
                        )
            else:
                memory = await store.insert_memory(
                    user_id=user_id,
                    content=f"insert case {label}",
                    category="fact",
                    source_type="import",
                    embedding=None,
                    **kwargs,
                )
            assert memory is not None, f"{label}: insert_memory returned no row"

            contract = await pool.fetchrow(
                """
                SELECT metadata IS NULL AS is_null,
                       jsonb_typeof(metadata) AS kind,
                       metadata = '{}'::jsonb AS is_empty_object,
                       metadata = ($2::jsonb) AS exact_match
                FROM memories
                WHERE id = $1
                """,
                memory["id"],
                json.dumps(expected),
            )
            assert contract is not None, f"{label}: memory row not persisted"
            assert contract["is_null"] is False, f"{label}: metadata must not be NULL"
            assert contract["kind"] == "object", f"{label}: metadata must be a jsonb object"
            assert contract["exact_match"] is True, f"{label}: persisted contents mismatch"
            if label == "nested":
                assert contract["is_empty_object"] is False
                assert _decoded_metadata(memory["metadata"]) == nested, (
                    f"{label}: nested metadata must round-trip exactly"
                )
            else:
                assert contract["is_empty_object"] is True, (
                    f"{label}: must land as the empty object"
                )
                assert _decoded_metadata(memory["metadata"]) == {}, (
                    f"{label}: returned metadata shape must be the empty object"
                )
    finally:
        await pool.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.asyncio
async def test_supersede_memory_persists_metadata_and_supersedes_old_row(
    monkeypatch: pytest.MonkeyPatch,
    isolated_meta_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Real ``supersede_memory`` writes the 040 contract and closes the old row."""
    pool, _scoped_dsn = isolated_meta_pool
    encryption = _fresh_runtime_crypto(monkeypatch)
    store = MemoryStore(pool, encryption)
    user_id = await _insert_isolated_user(pool, "metadata_persistence_supersede")

    nested = {"evidence": {"detected_by": "dedup", "score": 0.9}}
    metadata_cases: list[tuple[str, Any, dict[str, Any]]] = [
        ("omitted", OMIT_METADATA, {}),
        ("none", None, {}),
        ("empty", {}, {}),
        ("nested", nested, nested),
    ]

    try:
        for label, metadata_value, expected in metadata_cases:
            suppressed_kwargs: dict[str, Any] = {}
            if metadata_value is not OMIT_METADATA:
                suppressed_kwargs["metadata"] = metadata_value

            old = await store.insert_memory(
                user_id=user_id,
                content=f"supersede old case {label}",
                category="fact",
                source_type="import",
                embedding=None,
                **suppressed_kwargs,
            )
            assert old["metadata"] is not None, f"{label}: old row metadata missing"

            result = await store.supersede_memory(
                old["id"],
                f"supersede new case {label}",
                "fact",
                "import",
                user_id,
                embedding=None,
                **({"metadata": metadata_value} if metadata_value is not OMIT_METADATA else {}),
            )
            assert result is not None, f"{label}: supersede returned no new row"

            contract = await pool.fetchrow(
                """
                SELECT metadata IS NULL AS is_null,
                       jsonb_typeof(metadata) AS kind,
                       metadata = ($2::jsonb) AS exact_match
                FROM memories
                WHERE id = $1
                """,
                result["id"],
                json.dumps(expected),
            )
            assert contract is not None, f"{label}: supersede inserted no new row"
            assert contract["is_null"] is False, f"{label}: new-row metadata must not be NULL"
            assert contract["kind"] == "object", f"{label}: new-row metadata must be an object"
            assert contract["exact_match"] is True, f"{label}: new-row contents mismatch"

            lifecycle = await pool.fetchrow(
                """
                SELECT valid_to IS NOT NULL AS superseded
                FROM memories
                WHERE id = $1
                """,
                old["id"],
            )
            assert lifecycle is not None, f"{label}: old row disappeared"
            assert lifecycle["superseded"] is True, (
                f"{label}: old row must be closed by the supersede transaction"
            )

            assert _decoded_metadata(result["metadata"]) == expected, (
                f"{label}: returned metadata shape mismatch"
            )
    finally:
        await pool.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.asyncio
async def test_metadata_contract_probe_runs_on_isolated_schema(
    isolated_meta_pool: tuple[asyncpg.Pool, str],
) -> None:
    """The shared migration-040 probe runs against the isolated schema itself."""
    pool, scoped_dsn = isolated_meta_pool
    async with pool.acquire() as conn:
        current_schema = await conn.fetchval("SELECT current_schema()")
        assert current_schema is not None
        assert current_schema.startswith(SCHEMA_PREFIX)
        search_path = await conn.fetchval("SHOW search_path")
        assert current_schema in cast(str, search_path)
        await assert_memory_metadata_contract(conn)

    # The scoped DSN carries the isolated schema so any helper pool built from
    # it binds to the same schema instead of the shared ``public``.
    option_tail = scoped_dsn.rsplit("?", 1)[-1].rsplit("&", 1)[-1]
    assert unquote_plus(option_tail) == f"options=-csearch_path={current_schema},public", (
        f"scoped DSN lost the schema search_path: {option_tail}"
    )
