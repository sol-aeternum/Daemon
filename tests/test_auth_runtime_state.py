from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import pytest

from orchestrator.auth_pepper import (
    initialize_development_pepper,
    set_development_pepper_cache,
    validate_and_get_pepper,
)
from orchestrator.auth_runtime_state import (
    SETUP_TOKEN_HASH_KEY,
    create_setup_token_if_absent,
    lock_auth_runtime_state,
)
from orchestrator.auth_tokens import verify_token
from orchestrator.config import Settings
from orchestrator.main import lifespan
from orchestrator.worker.worker import on_startup


class RuntimeStateConn:
    def __init__(self, state: dict[str, str]) -> None:
        self.state = state
        self.lock_count = 0

    async def execute(self, sql: str, *args: object) -> None:
        if "pg_advisory_xact_lock" in sql:
            self.lock_count += 1
            return
        if "INSERT INTO system_state" in sql:
            key, value = str(args[0]), str(args[1])
            self.state[key] = value
            return

    async def fetchval(self, sql: str, *args: object) -> str | None:
        if "FROM system_state" in sql:
            return self.state.get(str(args[0]))
        return None

    async def fetchrow(self, sql: str, *args: object) -> dict[str, str] | None:
        if "INSERT INTO system_state" in sql and "ON CONFLICT" in sql:
            key, value = str(args[0]), str(args[1])
            if key in self.state:
                return None
            self.state[key] = value
            return {"value": value}
        return None

    @asynccontextmanager
    async def transaction(self):
        yield self


class RuntimeStatePool:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}
        self.connections: list[RuntimeStateConn] = []

    @asynccontextmanager
    async def acquire(self):
        conn = RuntimeStateConn(self.state)
        self.connections.append(conn)
        yield conn


@pytest.mark.asyncio
async def test_setup_token_insert_is_singleton_across_workers() -> None:
    state: dict[str, str] = {}
    worker_one = RuntimeStateConn(state)
    worker_two = RuntimeStateConn(state)

    await lock_auth_runtime_state(worker_one)
    token = await create_setup_token_if_absent(worker_one)

    await lock_auth_runtime_state(worker_two)
    duplicate = await create_setup_token_if_absent(worker_two)

    assert token is not None
    assert duplicate is None
    assert verify_token(token, state[SETUP_TOKEN_HASH_KEY]) is True
    assert worker_one.lock_count == 1
    assert worker_two.lock_count == 1


@pytest.mark.asyncio
async def test_development_pepper_is_shared_through_db_across_process_caches() -> None:
    pool = RuntimeStatePool()
    settings = Settings(daemon_environment="development", daemon_auth_pepper=None)

    try:
        set_development_pepper_cache(None)
        await initialize_development_pepper(settings, pool)  # type: ignore[arg-type]
        first_process_pepper = validate_and_get_pepper(settings)

        set_development_pepper_cache(None)
        await initialize_development_pepper(settings, pool)  # type: ignore[arg-type]
        second_process_pepper = validate_and_get_pepper(settings)

        assert first_process_pepper == second_process_pepper
        assert first_process_pepper == pool.state["auth.development_pepper"]
    finally:
        set_development_pepper_cache(None)


def test_lifespan_initializes_development_pepper_before_memory_hash_backfill() -> None:
    source = inspect.getsource(lifespan)

    assert source.index("initialize_development_pepper") < source.index(
        "backfill_memory_content_hashes"
    )


def test_worker_startup_initializes_development_pepper_before_memory_store() -> None:
    source = inspect.getsource(on_startup)

    assert source.index("initialize_development_pepper") < source.index("MemoryStore")
