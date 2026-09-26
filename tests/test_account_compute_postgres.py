"""Real PostgreSQL checks for cross-process account admission and migration.

Set ENTITLEMENTS_TEST_DATABASE_URL to an isolated disposable database. Each test
uses its own schema and drops only that schema; no provider requests are made.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.entitlements.errors import AccountSuspended, LimitExceeded, StaleSubscriptionEvent
from orchestrator.entitlements.models import SubscriptionEvent
from orchestrator.entitlements.plans import Plan
from orchestrator.entitlements.service import EntitlementService


@pytest_asyncio.fixture
async def account_database() -> AsyncIterator[tuple[asyncpg.Pool, uuid.UUID]]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"entitlements_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=12, server_settings={"search_path": schema}
        )
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute(
                "CREATE TABLE memories (user_id UUID REFERENCES users(id), content TEXT)"
            )
            user_id = uuid.uuid4()
            await conn.execute("INSERT INTO users VALUES ($1)", user_id)
            await conn.execute("INSERT INTO memories VALUES ($1, 'retained fixture')", user_id)
            migration = (
                Path(__file__).resolve().parents[1]
                / "migrations"
                / "039_entitlements_commercial.sql"
            )
            await conn.execute(migration.read_text())
        yield pool, user_id
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_trial_exhaustion_preserves_recurring_free_compute(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    initial = await service.public_snapshot(user_id)
    assert initial["plan"] == "free"
    assert initial["trial"]["state"] == "active"

    routine = await service.reserve(user_id, 1, operation="chat")
    await service.settle(routine, 1)
    after_routine = await service.public_snapshot(user_id)
    assert after_routine["trial"]["remaining_microusd"] == initial["trial"]["remaining_microusd"]

    premium_amount = initial["trial"]["remaining_microusd"]
    premium = await service.reserve(user_id, premium_amount, operation="chat", premium=True)
    await service.settle(premium, premium_amount)
    exhausted = await service.public_snapshot(user_id)
    assert exhausted["plan"] == "free"
    assert exhausted["trial"]["state"] == "exhausted"
    assert "premium_routing" not in exhausted["capabilities"]
    assert "chat" in exhausted["capabilities"]
    remaining_routine = await service.reserve(user_id, 1, operation="chat")
    await service.settle(remaining_routine, 1)


@pytest.mark.asyncio
async def test_trial_hold_is_not_consumption_and_release_restores_capacity(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    initial = await service.public_snapshot(user_id)
    amount = initial["trial"]["remaining_microusd"]
    held = await service.reserve(user_id, amount, operation="chat", premium=True)
    pending = await service.public_snapshot(user_id)
    assert pending["trial"]["state"] == "active"
    assert pending["trial"]["consumed_microusd"] == 0
    await service.release(held)
    released = await service.public_snapshot(user_id)
    assert released["trial"]["state"] == "active"
    assert released["trial"]["remaining_microusd"] == amount
    next_hold = await service.reserve(user_id, amount, operation="chat", premium=True)
    await service.settle(next_hold, amount)


@pytest.mark.asyncio
async def test_independent_services_cannot_race_concurrency_ceiling(account_database):
    pool, user_id = account_database
    policy = await EntitlementService(pool).resolve(user_id)
    ceiling = policy.limits.max_concurrent_operations
    attempts = await asyncio.gather(
        *[
            EntitlementService(pool).reserve(user_id, 0, operation="chat")
            for _ in range(ceiling + 3)
        ],
        return_exceptions=True,
    )
    admitted = [item for item in attempts if not isinstance(item, BaseException)]
    denied = [item for item in attempts if isinstance(item, BaseException)]
    assert len(admitted) == ceiling
    assert len(denied) == 3
    assert all(isinstance(item, LimitExceeded) for item in denied)
    for reservation in admitted:
        await EntitlementService(pool).settle(reservation, 0)
    reopened = await EntitlementService(pool).reserve(user_id, 0, operation="chat")
    await EntitlementService(pool).settle(reopened, 0)


@pytest.mark.asyncio
async def test_funded_ceiling_retains_genuinely_free_capacity(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    policy = await service.resolve(user_id)
    ceiling = policy.limits.monthly_budget_microusd
    assert ceiling > 0
    reservation = await service.reserve(user_id, ceiling, operation="chat")
    await service.settle(reservation, ceiling)
    with pytest.raises(LimitExceeded):
        await service.reserve(user_id, 1, operation="chat")
    free_route = await service.reserve(user_id, 0, operation="chat")
    await service.settle(free_route, 0)


@pytest.mark.asyncio
async def test_import_replay_and_downgrade_preserve_memory_and_trial(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    initial = await service.public_snapshot(user_id)
    event_id = f"fixture-import-{uuid.uuid4()}"
    await service.import_legacy_tier(user_id=user_id, legacy_tier="max", event_id=event_id)
    await service.import_legacy_tier(user_id=user_id, legacy_tier="max", event_id=event_id)
    assert (await service.public_snapshot(user_id))["plan"] == "power"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM entitlement_subscription_events WHERE event_id=$1", event_id
            )
            == 1
        )
    await service.import_legacy_tier(
        user_id=user_id, legacy_tier="free", event_id=f"fixture-downgrade-{uuid.uuid4()}"
    )
    downgraded = await service.public_snapshot(user_id)
    assert downgraded["plan"] == "free"
    assert downgraded["trial"]["remaining_microusd"] == initial["trial"]["remaining_microusd"]
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT content FROM memories WHERE user_id=$1", user_id) == (
            "retained fixture"
        )


@pytest.mark.asyncio
async def test_downgrade_does_not_reset_spend_or_block_zero_cost_routes(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    await service.import_legacy_tier(
        user_id=user_id, legacy_tier="max", event_id=f"upgrade-{uuid.uuid4()}"
    )
    ceiling = (await service.resolve(user_id)).limits.monthly_budget_microusd
    paid = await service.reserve(user_id, ceiling, operation="chat")
    await service.settle(paid, ceiling)
    await service.import_legacy_tier(
        user_id=user_id, legacy_tier="free", event_id=f"downgrade-{uuid.uuid4()}"
    )
    with pytest.raises(LimitExceeded):
        await service.reserve(user_id, 1, operation="chat")
    zero_cost = await service.reserve(user_id, 0, operation="chat")
    await service.settle(zero_cost, 0)


@pytest.mark.asyncio
async def test_crashed_hold_recovery_charges_bound_and_restores_concurrency(account_database):
    pool, user_id = account_database
    now = datetime.now(timezone.utc)
    crashed = EntitlementService(pool, clock=lambda: now - timedelta(hours=1))
    hold = await crashed.reserve(user_id, 10, operation="chat")
    service = EntitlementService(pool, clock=lambda: now)
    assert (
        await service.reconcile_expired_reservations(user_id, before=now - timedelta(minutes=5))
        == 1
    )
    assert (
        await service.reconcile_expired_reservations(user_id, before=now - timedelta(minutes=5))
        == 0
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, actual_microusd FROM entitlement_reservations WHERE id=$1", hold.id
        )
        assert dict(row) == {"status": "settled", "actual_microusd": 10}
    live = await service.reserve(user_id, 1, operation="chat")
    # Recovery must not consume a current, potentially live execution.
    assert (
        await service.reconcile_expired_reservations(user_id, before=now - timedelta(minutes=5))
        == 0
    )
    await service.settle(live, 1)


@pytest.mark.asyncio
async def test_upgrade_with_pending_free_hold_does_not_discard_its_cost(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    pending_free = await service.reserve(user_id, 10, operation="chat")
    await service.import_legacy_tier(
        user_id=user_id, legacy_tier="pro", event_id=f"upgrade-in-flight-{uuid.uuid4()}"
    )
    ceiling = (await service.resolve(user_id)).limits.monthly_budget_microusd
    paid = await service.reserve(user_id, ceiling - 10, operation="chat")
    await service.settle(paid, ceiling - 10)
    await service.settle(pending_free, 10)
    resolved = await service.resolve(user_id)
    assert resolved.status.value == "active"
    assert resolved.budget_remaining_microusd == 0
    with pytest.raises(LimitExceeded):
        await service.reserve(user_id, 1, operation="chat")
    zero_cost = await service.reserve(user_id, 0, operation="chat")
    await service.settle(zero_cost, 0)


@pytest.mark.asyncio
async def test_quote_breach_is_not_hidden_by_spare_monthly_budget(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    hold = await service.reserve(user_id, 1, operation="chat")
    settled = await service.settle(hold, 2)
    assert settled.actual_microusd == 2
    assert settled.reservation.status.value == "settled"
    with pytest.raises(AccountSuspended):
        await service.reserve(user_id, 1, operation="chat")


@pytest.mark.asyncio
async def test_quote_overrun_cannot_take_another_inflight_reservations_funding(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    await service.import_legacy_tier(
        user_id=user_id, legacy_tier="pro", event_id=f"paid-concurrency-{uuid.uuid4()}"
    )
    ceiling = (await service.resolve(user_id)).limits.monthly_budget_microusd
    first_amount = ceiling // 2
    first = await service.reserve(user_id, first_amount, operation="chat")
    second = await service.reserve(user_id, ceiling - first_amount, operation="chat")
    first_result = await service.settle(first, first_amount + 1)
    second_result = await service.settle(second, ceiling - first_amount)
    assert first_result.overage_microusd == 1
    assert second_result.overage_microusd == 0
    async with pool.acquire() as conn:
        period = await conn.fetchrow(
            "SELECT spent_microusd, reserved_microusd FROM entitlement_usage_periods WHERE user_id=$1",
            user_id,
        )
        assert dict(period) == {"spent_microusd": ceiling, "reserved_microusd": 0}


@pytest.mark.asyncio
async def test_stale_subscription_event_cannot_override_newer_state(account_database):
    pool, user_id = account_database
    service = EntitlementService(pool)
    now = datetime.now(timezone.utc)
    await service.apply_subscription_event(
        SubscriptionEvent(
            event_id=f"new-{uuid.uuid4()}",
            user_id=user_id,
            plan=Plan.POWER,
            source="subscription_import",
            occurred_at=now,
        )
    )
    # Both a typed stale rejection and an idempotent no-op are safe adapter contracts.
    with suppress(StaleSubscriptionEvent):
        await service.apply_subscription_event(
            SubscriptionEvent(
                event_id=f"old-{uuid.uuid4()}",
                user_id=user_id,
                plan=Plan.PRO,
                source="subscription_import",
                occurred_at=now - timedelta(days=1),
            )
        )
    assert (await service.public_snapshot(user_id))["plan"] == "power"
