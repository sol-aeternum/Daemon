"""Shared disposable-PostgreSQL fixture for durable task tests.

Requires ``TASKS_TEST_DATABASE_URL`` pointing at a disposable database; every
test replays all migrations into its own schema and drops it afterwards. The
application ``DATABASE_URL`` is never read.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, capture_generation
from orchestrator.tasks.store import TaskStore

DSN_ENV = "TASKS_TEST_DATABASE_URL"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
LEASE_S = 45.0


def migration_sql_for_schema(path: Path, schema: str, *, schema_prefix: str | None = None) -> str:
    """Relocate explicit public namespaces for existing isolated-schema tests.

    Production SQL is unchanged. The metadata PostgreSQL suite separately runs
    the original file in public; this adapter is not its security qualification.
    PUBLIC (the privilege grantee) and fixed pg_catalog search paths stay intact.
    """
    if schema_prefix is None:
        owned = re.fullmatch(r"(?:tasks_test|identity_legacy)_[0-9a-f]{32}", schema)
        namespace = schema
    else:
        # The benchmark provisioner supplies its already validated prefix and
        # generated UUID suffix. No arbitrary schema or public fallback.
        owned = re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,30}", schema_prefix, re.ASCII
        ) and re.fullmatch(re.escape(schema_prefix) + r"[0-9a-f]{32}", schema)
        namespace = f'"{schema}"'
    if not owned:
        raise ValueError("expected an owned durable-test schema")
    sql = path.read_text()
    if path.name not in ("048_compute_metadata.sql", "049_attempt_lifecycle.sql"):
        return sql
    return sql.replace("public.", namespace + ".").replace(
        "n.nspname='public'", f"n.nspname='{schema}'"
    )


@dataclass
class Env:
    pool: asyncpg.Pool
    tasks: TaskStore
    memory: MemoryStore
    alice: uuid.UUID
    bob: uuid.UUID


def durable_env_fixture(*, initialized: bool = False) -> Any:
    """Build the ``env`` fixture; assign it in each test module (``env = ...``)."""

    @pytest_asyncio.fixture
    async def env() -> AsyncIterator[Env]:
        async for value in _provision():
            if initialized:
                # Historical initialized-account fixtures, NOT sign-up evidence.
                for uid in (value.alice, value.bob):
                    await seed_entitlement(value.pool, uid)
            yield value

    return env


async def seed_entitlement(conn: Any, uid: uuid.UUID) -> None:
    """Explicit synthetic initialized state; never used for fresh-user HTTP tests."""
    from orchestrator.entitlements.policy import load_policy

    policy = load_policy()
    budget = policy.trial.budget_for_new_account()
    await conn.execute(
        "INSERT INTO entitlement_accounts(user_id,trial_state,trial_budget_microusd,"
        "trial_extended_agents) VALUES($1,$2,$3,$4)",
        uid,
        "active" if budget > 0 else "exhausted",
        budget,
        policy.trial.extended_agents_for_new_account(),
    )


async def _provision() -> AsyncGenerator[Env, None]:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"requires disposable {DSN_ENV}")
    schema = f"tasks_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool: asyncpg.Pool | None = None
    try:
        # Extensions are database-wide. Create them in public first so the
        # replayed migration 001 cannot place them in (and drop them with) a
        # disposable schema.
        for extension in ("pgcrypto", "vector"):
            await admin.execute(f"CREATE EXTENSION IF NOT EXISTS {extension} WITH SCHEMA public")
            home = await admin.fetchval(
                "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = $1",
                extension,
            )
            if home != "public":
                # An interrupted earlier run left it in its disposable schema.
                await admin.execute(f"ALTER EXTENSION {extension} SET SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=2, max_size=8, server_settings={"search_path": f"{schema}, public"}
        )
        async with pool.acquire() as conn:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                async with conn.transaction():
                    await conn.execute(migration_sql_for_schema(path, schema))
        encryption = ContentEncryption(Fernet.generate_key().decode())
        memory = MemoryStore(pool, encryption)
        users = []
        for name in ("alice", "bob"):
            user_id = uuid.uuid4()
            await pool.execute(
                "INSERT INTO users (id, email, name, username) VALUES ($1, $2, $3, $3)",
                user_id,
                f"{name}+{user_id.hex}@daemon.test",
                f"{name}-{user_id.hex[:8]}",
            )
            users.append(user_id)
        yield Env(pool, TaskStore(pool, encryption, memory), memory, users[0], users[1])
    finally:
        if pool is not None:
            await asyncio.wait_for(pool.close(), timeout=10)
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def task_capture(env: Env, user: uuid.UUID | None = None) -> CapturedGeneration:
    """Capture explicit initialized fixture state; never repair missing rows."""
    owner = user or env.alice
    async with env.pool.acquire() as conn:
        return await capture_generation(conn, owner)


async def accept_task(env: Env, user: uuid.UUID | None = None, **task_input: object):
    """Accept a fresh chat task with a minimal input for ``user`` (Alice by default)."""
    message = str(task_input.get("message", "hello"))
    return await env.tasks.accept(
        user_id=user or env.alice,
        conversation_id=None,
        new_conversation_title="t",
        pipeline="cloud",
        user_message=message,
        task_input={"message": message, **task_input},
        request_hash=uuid.uuid4().hex * 2,
        idempotency_key=None,
        assistant_model=None,
        captured_generation=await task_capture(env, user),
    )


async def expire_lease(env: Env, task_id: uuid.UUID) -> None:
    await env.pool.execute(
        "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = $1", task_id
    )


class FakePubSub:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self.channels: set[str] = set()

    async def subscribe(self, channel: str) -> None:
        self.channels.add(channel)
        self._redis.subscribers.append(self)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 0):
        if timeout <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def deliver(self, channel: str, data: str) -> None:
        if channel in self.channels:
            self._queue.put_nowait({"type": "message", "channel": channel, "data": data})

    async def unsubscribe(self) -> None:
        self.channels.clear()

    async def aclose(self) -> None:
        if self in self._redis.subscribers:
            self._redis.subscribers.remove(self)


class FakeRedis:
    """Records publishes and enqueues; stands in for arq's Redis in tests."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.enqueued: list[tuple[str, tuple, dict]] = []
        self.subscribers: list[FakePubSub] = []
        #: Set to drop live messages, as if Redis pub/sub were unavailable.
        self.drop_live = False

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def publish(self, channel: str, message: str) -> int:
        import json

        self.published.append((channel, json.loads(message)))
        if not self.drop_live:
            for subscriber in list(self.subscribers):
                subscriber.deliver(channel, message)
        return 1

    async def enqueue_job(self, name: str, *args: object, **kwargs: object) -> object:
        self.enqueued.append((name, args, kwargs))
        return object()

    async def delete(self, *_keys: str) -> int:
        return 0
