"""Marked synthetic benchmark clones on the existing owned PostgreSQL fixture."""

from __future__ import annotations

import asyncio
import uuid
from secrets import token_hex

import asyncpg
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.eval.bench_db import BENCHMARK_DB_MARKER
from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.tasks.store import TaskStore
from tests.durable_database import database_dsn
from tests.durable_tasks_support import Env, _seed_env, seed_entitlement


def benchmark_env_fixture():
    @pytest_asyncio.fixture
    async def env(durable_database_template, monkeypatch):
        template = durable_database_template
        name = "daemon_lme_bench_" + uuid.uuid4().hex
        admin = await asyncpg.connect(template.control_dsn)
        pool = None
        created = False
        try:
            await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template.name}"')
            created = True
            await admin.execute(f"COMMENT ON DATABASE \"{name}\" IS '{BENCHMARK_DB_MARKER}'")
            dsn = database_dsn(template.control_dsn, name)
            pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=8,
                server_settings={"search_path": f"{template.schema}, public"},
            )
            seeded = await _seed_env(pool)
            for owner in (seeded.alice, seeded.bob):
                await seed_entitlement(pool, owner)
            await pool.execute(
                "UPDATE users SET email=$2 WHERE id=$1",
                seeded.alice,
                f"longmemeval+{seeded.alice.hex}@daemon.test",
            )
            key = Fernet.generate_key().decode()
            monkeypatch.setenv("DATABASE_URL", dsn)
            monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", key)
            monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
            get_settings.cache_clear()
            encryption = ContentEncryption(key)
            memory = MemoryStore(pool, encryption)
            yield Env(pool, TaskStore(pool, encryption, memory), memory, seeded.alice, seeded.bob)
        finally:
            try:
                if pool is not None:
                    await asyncio.wait_for(pool.close(), 10)
                if created:
                    await admin.execute(f'DROP DATABASE "{name}"')
            finally:
                await admin.close()

    return env
