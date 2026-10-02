"""Routing labels on spend reservations (optional work O7)."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from orchestrator.entitlements.service import EntitlementService
from test_model_routing import LUNA, dispatch_fixture, named_route

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = MIGRATIONS / "042_reservation_routing_labels.sql"
ROLLBACK = MIGRATIONS / "rollback" / "042_reservation_routing_labels.down.sql"


def _statements(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("--")
    )


def test_migration_is_additive_and_nullable() -> None:
    sql = _statements(MIGRATION).upper()
    assert "ADD COLUMN IF NOT EXISTS WORKLOAD_PROFILE TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS REASONING_EFFORT TEXT" in sql
    assert "NOT NULL" not in sql
    for forbidden in ("DROP ", "UPDATE ", "DELETE ", "RENAME ", "ALTER COLUMN"):
        assert forbidden not in sql, forbidden


def test_rollback_drops_only_the_label_columns() -> None:
    sql = _statements(ROLLBACK).upper()
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS (\w+)", sql))
    assert dropped == {"WORKLOAD_PROFILE", "REASONING_EFFORT"}


@pytest.mark.asyncio
async def test_dispatch_records_the_profile_and_effort_actually_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with dispatch_fixture(monkeypatch, [named_route(LUNA, price=0)]) as (
        service,
        provider,
        _scope,
    ):
        with model_routing.routing_context("routine"):
            await runtime.guarded_completion(messages=[{"role": "user", "content": "hi"}])
    assert provider.await_args is not None
    sent = provider.await_args.kwargs["reasoning_effort"]
    kwargs: dict[str, Any] = service.reserve.await_args.kwargs
    assert kwargs["workload_profile"] == "routine"
    assert kwargs["reasoning_effort"] == sent == "low"


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[asyncpg.Pool, uuid.UUID]]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"labels_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
        )
        user_id = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute("INSERT INTO users VALUES ($1)", user_id)
            for name in ("039_entitlements_commercial.sql", "042_reservation_routing_labels.sql"):
                await conn.execute((MIGRATIONS / name).read_text())
            # Re-applying is a no-op, as scripts/migrate.py may do on a partial run.
            await conn.execute(MIGRATION.read_text())
        yield pool, user_id
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_reservation_rows_store_routing_labels(
    database: tuple[asyncpg.Pool, uuid.UUID],
) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    labelled = await service.reserve(
        user_id, 10, operation="chat", workload_profile="routine", reasoning_effort="low"
    )
    await service.settle(labelled, 10)
    unlabelled = await service.reserve(user_id, 10, operation="chat")
    await service.settle(unlabelled, 10)
    async with pool.acquire() as conn:
        rows = {
            row["id"]: row
            for row in await conn.fetch(
                "SELECT id, workload_profile, reasoning_effort FROM entitlement_reservations"
            )
        }
    assert rows[labelled.id]["workload_profile"] == "routine"
    assert rows[labelled.id]["reasoning_effort"] == "low"
    assert rows[unlabelled.id]["workload_profile"] is None
    assert rows[unlabelled.id]["reasoning_effort"] is None


@pytest.mark.asyncio
async def test_label_length_is_bounded(database: tuple[asyncpg.Pool, uuid.UUID]) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    with pytest.raises(Exception):
        await service.reserve(user_id, 10, operation="chat", workload_profile="x" * 64)
