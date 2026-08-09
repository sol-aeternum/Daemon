from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from scripts import migrate


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        return None


class _SharedLockState:
    def __init__(self) -> None:
        self.locked = False
        self.acquire_count = 0
        self.unlock_count = 0
        self.applied: list[str] = []
        self.first_sql_started = asyncio.Event()
        self.allow_first_sql_finish = asyncio.Event()


class _FakeConnection:
    def __init__(self, state: _SharedLockState) -> None:
        self.state = state
        self.closed = False

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, bool]:
        assert sql == "SELECT pg_try_advisory_lock($1) AS acquired"
        assert args == (migrate.MIGRATION_ADVISORY_LOCK_ID,)
        if self.state.locked:
            return {"acquired": False}
        self.state.locked = True
        self.state.acquire_count += 1
        return {"acquired": True}

    async def fetch(self, sql: str) -> list[dict[str, str]]:
        assert sql == "SELECT filename FROM _migrations"
        return [{"filename": filename} for filename in self.state.applied]

    async def execute(self, sql: str, *args: Any) -> str:
        if sql == "SELECT pg_advisory_unlock($1)":
            assert args == (migrate.MIGRATION_ADVISORY_LOCK_ID,)
            self.state.locked = False
            self.state.unlock_count += 1
            return "SELECT 1"
        if "CREATE TABLE IF NOT EXISTS _migrations" in sql:
            return "CREATE TABLE"
        if sql == "SELECT 1;":
            if not self.state.first_sql_started.is_set():
                self.state.first_sql_started.set()
                await self.state.allow_first_sql_finish.wait()
            return "SELECT 1"
        if sql == "INSERT INTO _migrations (filename) VALUES ($1)":
            self.state.applied.append(str(args[0]))
            return "INSERT 0 1"
        raise AssertionError(f"unexpected SQL: {sql}")

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def close(self) -> None:
        self.closed = True


def _write_migration(tmp_path: Path) -> Path:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_test.sql").write_text("SELECT 1;")
    return migrations_dir


@pytest.mark.asyncio
async def test_concurrent_migration_runners_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _SharedLockState()
    migrations_dir = _write_migration(tmp_path)

    async def connect(_: str) -> _FakeConnection:
        return _FakeConnection(state)

    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(migrate.asyncpg, "connect", connect)

    first_runner = asyncio.create_task(migrate.run_migrations())
    await state.first_sql_started.wait()

    with pytest.raises(migrate.MigrationLockError, match="another migration runner is in progress"):
        await migrate.run_migrations()

    state.allow_first_sql_finish.set()
    await first_runner

    assert state.applied == ["001_test.sql"]
    assert state.acquire_count == 1
    assert state.unlock_count == 1
    assert state.locked is False


@pytest.mark.asyncio
async def test_migration_lock_released_after_runner_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _SharedLockState()
    state.allow_first_sql_finish.set()
    migrations_dir = _write_migration(tmp_path)

    async def connect(_: str) -> _FakeConnection:
        return _FakeConnection(state)

    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(migrate.asyncpg, "connect", connect)

    await migrate.run_migrations()
    await migrate.run_migrations()

    assert state.applied == ["001_test.sql"]
    assert state.acquire_count == 2
    assert state.unlock_count == 2
    assert state.locked is False


@pytest.mark.asyncio
async def test_migration_runner_derives_database_url_from_postgres_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _SharedLockState()
    state.allow_first_sql_finish.set()
    migrations_dir = _write_migration(tmp_path)
    captured: list[str] = []

    async def connect(database_url: str) -> _FakeConnection:
        captured.append(database_url)
        return _FakeConnection(state)

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_USER", "daemon")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unique@secret")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "daemon")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", migrations_dir)
    monkeypatch.setattr(migrate.asyncpg, "connect", connect)

    await migrate.run_migrations()

    assert captured == ["postgresql://daemon:unique%40secret@postgres:5432/daemon"]
