"""Shared disposable-PostgreSQL fixture for durable task tests.

Requires ``TASKS_TEST_DATABASE_URL`` pointing at a disposable database; every
test replays all migrations into its own schema and drops it afterwards. The
application ``DATABASE_URL`` is never read.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.tasks.store import TaskStore

DSN_ENV = "TASKS_TEST_DATABASE_URL"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
LEASE_S = 45.0


@dataclass
class Env:
    pool: asyncpg.Pool
    tasks: TaskStore
    memory: MemoryStore
    alice: uuid.UUID
    bob: uuid.UUID


def durable_env_fixture() -> Any:
    """Build the ``env`` fixture; assign it in each test module (``env = ...``)."""

    @pytest_asyncio.fixture
    async def env() -> AsyncIterator[Env]:
        async for value in _provision():
            yield value

    return env


async def _provision() -> AsyncIterator[Env]:
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
                    await conn.execute(path.read_text())
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
