"""Real PostgreSQL admission and accounting checks in disposable schemas."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.entitlements.errors import (
    AccountSuspended,
    CapabilityDenied,
    ConcurrencyExceeded,
    ExtendedRunExceeded,
    SettlementConflict,
    SubscriptionEventConflict,
)
from orchestrator.entitlements.models import SubscriptionEvent
from orchestrator.entitlements.plans import ChargeKind, Plan, ReservationStatus
from orchestrator.entitlements.service import EntitlementService


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[asyncpg.Pool, uuid.UUID]]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"entitlements_service_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": schema}
        )
        user_id = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute("INSERT INTO users (id) VALUES ($1)", user_id)
            sql = (
                Path(__file__).resolve().parents[1]
                / "migrations"
                / "039_entitlements_commercial.sql"
            ).read_text()
            await conn.execute(sql)
        yield pool, user_id
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_concurrent_trial_holds_serialize_and_release(database):
    pool, user = database
    service = EntitlementService(pool)
    budget = (await service.resolve(user)).trial.budget_microusd
    results = await asyncio.gather(
        service.reserve(user, budget, operation="chat", premium=True),
        service.reserve(user, budget, operation="chat", premium=True),
        return_exceptions=True,
    )
    holds = [value for value in results if not isinstance(value, BaseException)]
    assert len(holds) == 1
    assert len([value for value in results if isinstance(value, BaseException)]) == 1
    assert holds[0].charge_kind is ChargeKind.TRIAL
    pending = await service.resolve(user)
    assert pending.trial.state.value == "active"
    assert pending.trial.consumed_microusd == 0
    assert pending.trial.remaining_microusd == 0
    await service.release(holds[0])
    assert (await service.resolve(user)).trial.remaining_microusd == budget
    await service.release(await service.reserve(user, budget, operation="chat", premium=True))


@pytest.mark.asyncio
async def test_zero_cost_execution_consumes_trial_agent_but_cancel_returns_it(database):
    pool, user = database
    service = EntitlementService(pool)
    initial = await service.resolve(user)
    assert initial.trial.extended_agents_remaining > 0
    cancelled = await service.reserve(
        user, 0, operation="extended_agent", premium=True, extended=True
    )
    assert (await service.resolve(user)).trial.extended_agents_reserved == 1
    released = await service.release(cancelled)
    assert released.status is ReservationStatus.RELEASED
    assert (
        await service.resolve(user)
    ).trial.extended_agents_remaining == initial.trial.extended_agents_remaining
    for _ in range(initial.trial.extended_agents_remaining):
        held = await service.reserve(
            user, 0, operation="extended_agent", premium=True, extended=True
        )
        settled = await service.settle(held, 0)
        assert settled.status is ReservationStatus.SETTLED
        assert not (await service.settle(held, 0)).applied
    resolved = await service.resolve(user)
    assert resolved.trial.extended_agents_remaining == 0
    assert resolved.has("premium_routing")
    assert not resolved.has("extended_agents")
    with pytest.raises(CapabilityDenied):
        await service.reserve(user, 0, operation="extended_agent", premium=True, extended=True)


@pytest.mark.asyncio
async def test_settlement_overrun_closes_hold_records_truth_and_suspends(database):
    pool, user = database
    service = EntitlementService(pool)
    hold = await service.reserve(user, 100, operation="chat")
    result = await service.settle(hold, 300_000, usage={"input_tokens": 2})
    assert result.actual_microusd == 300_000
    assert result.overage_microusd == 50_000
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, actual_microusd, overage_microusd FROM entitlement_reservations WHERE id=$1",
            hold.id,
        )
        assert (row["status"], row["actual_microusd"], row["overage_microusd"]) == (
            "settled",
            300_000,
            50_000,
        )
        assert (
            await conn.fetchval(
                "SELECT reserved_microusd FROM entitlement_usage_periods WHERE user_id=$1", user
            )
            == 0
        )
    assert not (await service.settle(hold, 300_000)).applied
    with pytest.raises(SettlementConflict):
        await service.settle(hold, 300_001)
    with pytest.raises(AccountSuspended):
        await service.reserve(user, 0, operation="chat")


@pytest.mark.asyncio
async def test_trial_underspend_restores_remainder_and_overrun_is_audited(database):
    pool, user = database
    service = EntitlementService(pool)
    budget = (await service.resolve(user)).trial.budget_microusd
    first = await service.reserve(user, budget, operation="chat", premium=True)
    await service.settle(first, 1)
    assert (await service.resolve(user)).trial.remaining_microusd == budget - 1
    second = await service.reserve(user, budget - 1, operation="chat", premium=True)
    settled = await service.settle(second, budget + 10)
    assert settled.actual_microusd == budget + 10
    assert settled.overage_microusd == 11
    status = await service.resolve(user)
    assert status.trial.consumed_microusd == budget
    assert status.trial.state.value == "exhausted"
    assert not status.has("premium_routing")
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT status FROM entitlement_reservations WHERE id=$1", second.id
            )
            == "settled"
        )


@pytest.mark.asyncio
async def test_rate_window_counts_completed_requests_until_reset(database):
    pool, user = database
    now = [datetime(2026, 1, 3, 12, tzinfo=timezone.utc)]
    service = EntitlementService(pool, clock=lambda: now[0])
    for _ in range(service.policy.plan(Plan.FREE).limits.requests_per_minute):
        await service.release(await service.reserve(user, 0, operation="chat"))
    from orchestrator.entitlements.errors import RateLimitExceeded

    with pytest.raises(RateLimitExceeded):
        await service.reserve(user, 0, operation="chat")
    now[0] += timedelta(seconds=61)
    await service.release(await service.reserve(user, 0, operation="chat"))


@pytest.mark.asyncio
async def test_extended_count_denial_is_recorded_with_valid_sql_code(database):
    pool, user = database
    service = EntitlementService(pool)
    await service.apply_subscription_event(
        SubscriptionEvent(
            event_id=f"paid-{uuid.uuid4()}", user_id=user, plan=Plan.PRO, source="admin"
        )
    )
    period = service.current_period_key()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entitlement_usage_periods (user_id, period_key, extended_agents_used) VALUES ($1, $2, $3)",
            user,
            period,
            service.policy.plan(Plan.PRO).limits.extended_agents_per_period,
        )
    with pytest.raises(ExtendedRunExceeded) as raised:
        await service.reserve(user, 0, operation="extended_agent", extended=True)
    assert raised.value.code == "extended_agents_exceeded"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT denial_code FROM entitlement_limit_encounters WHERE user_id=$1", user
            )
            == "extended_agents_exceeded"
        )


@pytest.mark.asyncio
async def test_rollover_old_hold_keeps_old_period_and_global_concurrency(database):
    pool, user = database
    clock = [datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)]
    service = EntitlementService(pool, clock=lambda: clock[0])
    hold = await service.reserve(user, 100, operation="chat")
    clock[0] += timedelta(minutes=2)
    with pytest.raises(ConcurrencyExceeded):
        await service.reserve(user, 1, operation="chat")
    await service.settle(hold, 80)
    next_hold = await service.reserve(user, 1, operation="chat")
    assert hold.period_key != next_hold.period_key
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                user,
                hold.period_key,
            )
            == 80
        )
        assert (
            await conn.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                user,
                next_hold.period_key,
            )
            == 0
        )
    await service.release(next_hold)


@pytest.mark.asyncio
async def test_trusted_events_keep_metadata_replay_and_trial_grant(database):
    pool, user = database
    service = EntitlementService(pool)
    initial = await service.resolve(user)
    now = datetime.now(timezone.utc)
    event = SubscriptionEvent(
        event_id=f"upgrade-{uuid.uuid4()}",
        user_id=user,
        plan=Plan.POWER,
        source="subscription_import",
        occurred_at=now,
        metadata={"external_ref": "fixture"},
    )
    assert (await service.apply_subscription_event(event)).applied
    assert (await service.apply_subscription_event(event)).duplicate
    with pytest.raises(SubscriptionEventConflict):
        await service.apply_subscription_event(
            SubscriptionEvent(
                event_id=event.event_id,
                user_id=user,
                plan=Plan.PRO,
                source="subscription_import",
                occurred_at=now,
            )
        )
    async with pool.acquire() as conn:
        assert "fixture" in await conn.fetchval(
            "SELECT metadata::text FROM entitlement_subscription_events WHERE event_id=$1",
            event.event_id,
        )
    stale = SubscriptionEvent(
        event_id=f"stale-{uuid.uuid4()}",
        user_id=user,
        plan=Plan.FREE,
        source="subscription_import",
        occurred_at=now - timedelta(days=1),
    )
    assert not (await service.apply_subscription_event(stale)).applied
    assert (await service.resolve(user)).plan is Plan.POWER
    await service.apply_subscription_event(
        SubscriptionEvent(
            event_id=f"downgrade-{uuid.uuid4()}",
            user_id=user,
            plan=Plan.FREE,
            source="subscription_import",
            active=False,
            occurred_at=now + timedelta(minutes=1),
        )
    )
    restored = await service.resolve(user)
    assert restored.trial.remaining_microusd == initial.trial.remaining_microusd
    assert restored.trial.extended_agents_remaining == initial.trial.extended_agents_remaining
    assert restored.charge_kind_for(True) is ChargeKind.TRIAL
