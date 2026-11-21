"""Explicit opt-in real PostgreSQL checks for Free-turn accounting fixtures.

This module is the only caller of ``tests.turn_budget_accounting_support``.
It uses the existing cached, network-none ``metadata_cluster`` fixture, the real
``EntitlementService``/``EntitlementStore`` and the opt-in 075 schema. No
provider, transport, network or production startup path is exercised.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import asyncpg
import pytest

from orchestrator.entitlements.errors import AccountSuspended, BudgetExceeded, LimitExceeded
from orchestrator.entitlements.ledger import period_key
from orchestrator.entitlements.service import EntitlementService
from tests.turn_budget_accounting_support import (
    Allocation,
    BatchMember,
    FreeTurnCommitUncertain,
    FreeTurnDenied,
    free_turn_database as db,
    metadata_cluster as metadata_cluster,
)

__all__ = ["db"]


def member(operation: str = "chat", quote: int = 0, *, op_id=None, role: str | None = None):
    return BatchMember(
        op_id=uuid.uuid4() if op_id is None else op_id,
        operation=operation,
        quote_microusd=quote,
        role=role,
    )


async def period_state(acc, key=None):
    value = await acc.period_state(key)
    assert value is not None
    return value


async def reservation_row(acc, reservation):
    value = await acc.reservation_row(reservation)
    assert value is not None
    return value


def simulate_rollover(acc, monkeypatch):
    real_clock = acc._clock

    async def next_clock(conn):
        now, _ = await real_clock(conn)
        future = now + timedelta(days=40)
        return future, period_key(future)

    monkeypatch.setattr(acc, "_clock", next_clock)


@pytest.mark.asyncio(loop_scope="module")
async def test_escrow_takes_no_slot_and_legacy_balance_sees_it(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=400)
    assert root.funded_microusd == 400
    assert root.scope_id is not None
    assert (await period_state(acc))["reserved_microusd"] == 400

    # F itself owns no rate/concurrency slot: a zero-price call still takes it.
    first = await acc.reserve_one(root.turn_root_id, member("chat", 0))
    other_root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    with pytest.raises(LimitExceeded):
        await acc.reserve_one(other_root.turn_root_id, member("chat", 0))
    await acc.release(first)
    second = await acc.reserve_one(other_root.turn_root_id, member("chat", 0))
    await acc.release(second)

    # The recurring monthly ceiling sees the escrow even for legacy callers.
    with pytest.raises(LimitExceeded):
        await acc.legacy_reserve(249601)
    legacy = await acc.legacy_reserve(249600)
    assert (await period_state(acc))["reserved_microusd"] == 250000
    await acc.release(legacy)


@pytest.mark.asyncio(loop_scope="module")
async def test_zero_price_call_takes_slot_and_escrow_does_not(db):
    acc = await db.accounting()
    a = await acc.open_root(bound_microusd=100, funded_microusd=0)
    b = await acc.open_root(bound_microusd=100, funded_microusd=0)
    first = await acc.reserve_one(a.turn_root_id, member("chat", 0))
    with pytest.raises(LimitExceeded):
        await acc.reserve_one(b.turn_root_id, member("chat", 0))
    await acc.release(first)
    second = await acc.reserve_one(b.turn_root_id, member("chat", 0))
    await acc.release(second)
    assert (await period_state(acc))["reserved_microusd"] == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_add_funded_uses_same_period_ceiling_without_slot(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1_000_000, funded_microusd=100)
    updated = await acc.add_funded(root.turn_root_id, 50, expected_revision=1)
    assert updated.funded_microusd == 150
    assert updated.revision == 2
    assert (await period_state(acc))["reserved_microusd"] == 150
    with pytest.raises(FreeTurnDenied) as caught:
        await acc.add_funded(root.turn_root_id, 250000)
    assert caught.value.code == "period_ceiling"
    observation = await acc.observe(root.turn_root_id)
    assert observation.funded_microusd == 150
    assert observation.period_reserved_microusd == 150
    sealed = await acc.seal(root.turn_root_id, expected_revision=2)
    assert sealed.released_funded_microusd == 150


@pytest.mark.asyncio(loop_scope="module")
async def test_batch_aggregates_same_root_and_replays_exactly(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=200)
    batch_id = uuid.uuid4()
    members = (member("chat", 100), member("chat", 200, role="worker"))
    result = await acc.reserve_batch(root.turn_root_id, batch_id, members, final=False)
    assert result.applied is True and result.replayed is False
    assert len(result.reservation_ids) == 2
    observation = await acc.observe(root.turn_root_id)
    assert observation.funded_microusd == 200
    assert observation.held_microusd == 300
    assert observation.period_reserved_microusd == 500
    assert observation.permission_remaining_microusd == 500

    replay = await acc.reserve_batch(root.turn_root_id, batch_id, members, final=False)
    assert replay.applied is False and replay.replayed is True
    assert replay.reservation_ids == result.reservation_ids

    with pytest.raises(FreeTurnDenied) as caught:
        await acc.reserve_batch(root.turn_root_id, batch_id, (members[0],), final=False)
    assert caught.value.code == "batch_conflict"

    final_call = (member("chat", 250, role="synthesis"),)
    final_id = uuid.uuid4()
    await acc.reserve_batch(root.turn_root_id, final_id, final_call, final=True)
    observation = await acc.observe(root.turn_root_id)
    assert observation.funded_microusd == 0
    assert observation.held_microusd == 550
    assert observation.period_reserved_microusd == 550
    with pytest.raises(FreeTurnDenied, match="batch_conflict"):
        await acc.reserve_batch(root.turn_root_id, final_id, final_call, final=False)


@pytest.mark.asyncio(loop_scope="module")
async def test_batch_mixed_existing_new_and_duplicate_members_refused(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    existing = member("chat", 10)
    await acc.reserve_one(root.turn_root_id, existing)
    batch_members = (existing, member("chat", 10))
    with pytest.raises(FreeTurnDenied) as caught:
        await acc.reserve_batch(root.turn_root_id, uuid.uuid4(), batch_members, final=False)
    assert caught.value.code == "batch_mixed"
    assert (await acc.observe(root.turn_root_id)).held_microusd == 10

    duplicate = member("chat", 10)
    with pytest.raises(ValueError):
        await acc.reserve_batch(
            root.turn_root_id, uuid.uuid4(), (duplicate, duplicate), final=False
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_refused_batch_rolls_back_fund_and_rows(db):
    acc = await db.accounting()
    permission_root = await acc.open_root(bound_microusd=200, funded_microusd=100)
    with pytest.raises(FreeTurnDenied) as caught:
        await acc.reserve_batch(
            permission_root.turn_root_id,
            uuid.uuid4(),
            (member("chat", 150), member("chat", 100)),
            final=False,
        )
    assert caught.value.code == "permission_exceeded"
    observation = await acc.observe(permission_root.turn_root_id)
    assert observation.funded_microusd == 100
    assert observation.held_microusd == 0
    assert observation.period_reserved_microusd == 100

    ceiling_root = await acc.open_root(bound_microusd=10000, funded_microusd=100)
    await acc.seed_spend(249800)
    # Another root still owns F=100. This draw releases only the final
    # root's F=100, so adding 150 would leave 250050 against 250000.
    with pytest.raises(BudgetExceeded):
        await acc.reserve_batch(
            ceiling_root.turn_root_id,
            uuid.uuid4(),
            (member("chat", 150),),
            final=True,
        )
    observation = await acc.observe(ceiling_root.turn_root_id)
    assert observation.funded_microusd == 100
    assert observation.held_microusd == 0
    assert observation.period_reserved_microusd == 200


@pytest.mark.asyncio(loop_scope="module")
async def test_second_member_denial_rolls_back_first_member(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    await acc.seed_spend(249750)
    batch = uuid.uuid4()
    calls = (member("chat", 100, role="routing"), member("chat", 100, role="child"))
    with pytest.raises(BudgetExceeded):
        await acc.reserve_batch(root.turn_root_id, batch, calls, final=False)
    assert await acc.observe_batch(root.turn_root_id, batch, calls, final=False) is None
    assert (await acc.observe(root.turn_root_id)).period_reserved_microusd == 100


@pytest.mark.asyncio(loop_scope="module")
async def test_competing_roots_and_legacy_call_share_one_monthly_balance(db):
    acc = await db.accounting()
    await acc.seed_spend(240000)

    async def open_root():
        return await acc.open_root(bound_microusd=10000, funded_microusd=6000)

    results = await asyncio.gather(open_root(), open_root(), return_exceptions=True)
    roots = [item for item in results if isinstance(item, Allocation)]
    denials = [item for item in results if isinstance(item, FreeTurnDenied)]
    assert len(roots) == 1 and len(denials) == 1
    assert denials[0].code == "period_ceiling"
    observation = await acc.observe(roots[0].turn_root_id)
    assert observation.funded_microusd == 6000
    assert observation.period_reserved_microusd == 6000

    with pytest.raises(LimitExceeded):
        await acc.legacy_reserve(4001)
    legacy = await acc.legacy_reserve(4000)
    assert (await period_state(acc))["reserved_microusd"] == 10000
    await acc.release(legacy)


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_and_final_transfer_wait_for_same_account_lock(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=10000, funded_microusd=6000)
    await acc.seed_spend(240000)
    final_member = (member("chat", 7000, role="synthesis"),)
    contenders = []
    try:
        async with db.database.connection("postgres") as owner:
            async with owner.transaction():
                await owner.execute(
                    "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", acc.account
                )
                contenders.append(
                    asyncio.create_task(
                        acc.reserve_batch(root.turn_root_id, uuid.uuid4(), final_member, final=True)
                    )
                )
                # Background is still month-funded; it isolates the money race
                # from the independent one-foreground-slot refusal.
                contenders.append(
                    asyncio.create_task(
                        acc.service.reserve(acc.account, 4000, operation="chat", background=True)
                    )
                )
                async with asyncio.timeout(1):
                    while True:
                        await owner.execute("SELECT pg_stat_clear_snapshot()")
                        waiting = await owner.fetchval(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname=current_database() AND usename='md_app' "
                            "AND wait_event_type='Lock'"
                        )
                        if waiting == 2:
                            break
    finally:
        result = await asyncio.gather(*contenders, return_exceptions=True)
    assert sum(isinstance(item, BudgetExceeded) for item in result) == 1
    assert sum(not isinstance(item, BaseException) for item in result) == 1
    observation = await acc.observe(root.turn_root_id)
    expected = 7000 if observation.funded_microusd == 0 else 10000
    assert observation.period_reserved_microusd == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_optional_children_retry_and_tool_share_turn_permission(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=500, funded_microusd=175)
    route = await acc.reserve_one(root.turn_root_id, member("chat", 100, role="routing"))
    await acc.settle(route, 90)
    batch = uuid.uuid4()
    parallel = (
        member("chat", 120, role="child"),
        member("chat", 90, role="tool"),
    )
    admitted = await acc.reserve_batch(root.turn_root_id, batch, parallel, final=False)
    with pytest.raises(FreeTurnDenied, match="permission_exceeded"):
        await acc.reserve_one(root.turn_root_id, member("chat", 30, role="retry"))
    observation = await acc.observe(root.turn_root_id)
    assert (observation.spent_microusd, observation.held_microusd) == (90, 210)
    assert (observation.funded_microusd, observation.permission_remaining_microusd) == (175, 25)
    final = await acc.reserve_batch(
        root.turn_root_id, uuid.uuid4(), (member("chat", 175, role="synthesis"),), final=True
    )
    assert (await acc.observe(root.turn_root_id)).period_reserved_microusd == 385
    for reservation in admitted.reservation_ids + final.reservation_ids:
        await acc.release(reservation)


@pytest.mark.asyncio(loop_scope="module")
async def test_seal_is_revision_bounded_idempotent_and_never_clamps(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    sealed = await acc.seal(root.turn_root_id, expected_revision=1)
    assert sealed.applied is True
    assert sealed.released_funded_microusd == 100
    assert sealed.allocation.status == "sealed"
    assert sealed.allocation.revision == 2
    assert sealed.allocation.funded_microusd == 0
    assert (await period_state(acc))["reserved_microusd"] == 0

    repeat = await acc.seal(root.turn_root_id, expected_revision=1)
    assert repeat.applied is False
    assert repeat.allocation.revision == 2
    with pytest.raises(FreeTurnDenied) as caught:
        await acc.seal(root.turn_root_id, expected_revision=2)
    assert caught.value.code == "stale_revision"

    # A missing reserve must fail loudly, never clamp, and leave the root open.
    broken = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    async with acc.database.database.connection() as owner:
        await owner.execute(
            "UPDATE entitlement_usage_periods SET reserved_microusd=0 "
            "WHERE user_id=$1 AND period_key=$2",
            acc.account,
            (await period_state(acc))["period_key"],
        )
    with pytest.raises(FreeTurnDenied) as caught:
        await acc.seal(broken.turn_root_id, expected_revision=1)
    assert caught.value.code == "reserve_underflow"
    observation = await acc.observe(broken.turn_root_id)
    assert observation.allocation.status == "open"
    assert observation.allocation.funded_microusd == 100


@pytest.mark.asyncio(loop_scope="module")
async def test_seal_survives_suspension_and_rollover(db, monkeypatch):
    acc = await db.accounting()
    suspended_root = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    rolled = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    simulate_rollover(acc, monkeypatch)
    async with acc.database.database.connection() as owner:
        await owner.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", acc.account
        )
    assert (await acc.seal(suspended_root.turn_root_id, expected_revision=1)).applied is True
    sealed = await acc.seal(rolled.turn_root_id, expected_revision=rolled.revision)
    assert sealed.applied is True and sealed.allocation.period_key == rolled.period_key


@pytest.mark.asyncio(loop_scope="module")
async def test_operator_control_blocks_fixture_seal_without_releasing_escrow(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=100)
    async with db.database.connection() as owner:
        await owner.execute(
            "INSERT INTO account_suspension_causes(account_id,cause_id,cause_code) "
            "VALUES ($1,$2,'operator')",
            acc.account,
            uuid.uuid4(),
        )
    with pytest.raises(FreeTurnDenied, match="account_control_pending"):
        await acc.seal(root.turn_root_id, expected_revision=root.revision)
    assert (await period_state(acc))["reserved_microusd"] == 100


@pytest.mark.asyncio(loop_scope="module")
async def test_old_period_rejects_new_calls_and_settles_original_work(db, monkeypatch):
    acc = await db.accounting()
    old_root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    reservation = await acc.reserve_one(old_root.turn_root_id, member("chat", 50))
    release_reservation = await acc.reserve_one(old_root.turn_root_id, member("chat", 20))
    simulate_rollover(acc, monkeypatch)
    with pytest.raises(FreeTurnDenied, match="period_closed"):
        await acc.reserve_one(old_root.turn_root_id, member("chat", 1))
    await acc.open_root(bound_microusd=1000, funded_microusd=0)
    settled = await acc.settle(reservation, 30)
    assert settled.actual_microusd == 30
    old = await period_state(acc, old_root.period_key)
    current = await period_state(acc)
    assert old["spent_microusd"] == 30 and old["reserved_microusd"] == 20
    assert current["spent_microusd"] == 0

    await acc.release(release_reservation)
    assert (await period_state(acc, old_root.period_key))["reserved_microusd"] == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_quote_breach_suspends_but_release_of_original_work_remains(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    breached = await acc.reserve_one(root.turn_root_id, member("chat", 10))
    survivor = await acc.reserve_one(root.turn_root_id, member("chat", 10))
    settlement = await acc.settle(breached, 11)
    assert settlement.actual_microusd == 11
    with pytest.raises(AccountSuspended):
        await acc.reserve_one(root.turn_root_id, member("chat", 1))
    released = await acc.release(survivor)
    assert released.actual_microusd == 0
    period = await period_state(acc)
    assert period["spent_microusd"] == 11 and period["reserved_microusd"] == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_unknown_charges_full_but_proved_not_sent_releases(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    unknown = await acc.reserve_one(root.turn_root_id, member("chat", 10))
    full = await acc.settle(unknown, 10, usage={"estimated_cost": True})
    assert full.actual_microusd == 10 and full.released_microusd == 0

    proved = await acc.reserve_one(root.turn_root_id, member("chat", 10))
    released = await acc.release(
        proved, usage={"estimated_cost": True, "recovered_after_timeout": True}
    )
    assert released.actual_microusd == 0 and released.released_microusd == 10
    period = await period_state(acc)
    assert period["spent_microusd"] == 10 and period["reserved_microusd"] == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_receipt_refund_returns_only_to_the_original_period(db):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=0)
    reservation = await acc.reserve_one(root.turn_root_id, member("chat", 10))
    await acc.settle(
        reservation, 10, usage={"estimated_cost": True, "generation_id": "fixture-generation"}
    )
    assert (await period_state(acc))["spent_microusd"] == 10
    future = EntitlementService(
        acc.database.pool,
        clock=lambda: datetime.now(timezone.utc) + timedelta(days=40),
    )
    assert await future.reconcile_to_receipt(reservation, 4) == 6
    period = await period_state(acc)
    assert period["spent_microusd"] == 4
    assert await acc.period_state(period_key(future.now())) is None
    row = await reservation_row(acc, reservation)
    assert row["actual_microusd"] == 4 and row["status"] == "settled"


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("phase", ["before", "after"])
async def test_lost_commit_ack_recovers_by_exact_batch_observation(db, phase):
    acc = await db.accounting()
    root = await acc.open_root(bound_microusd=1000, funded_microusd=200)
    batch_id = uuid.uuid4()
    members = (member("chat", 300, role="synthesis"),)
    bridge = acc.database.database.bridge
    bridge.commit_fault = phase
    bridge.commit_seen.clear()
    bridge.commit_fault_role = "md_app"
    try:
        pending = asyncio.create_task(
            acc.reserve_batch(root.turn_root_id, batch_id, members, final=True)
        )
        await asyncio.wait_for(bridge.commit_seen.wait(), 1.5)
        with pytest.raises(FreeTurnCommitUncertain):
            await asyncio.wait_for(pending, 4)
    finally:
        bridge.commit_fault = None
    replay = await acc.observe_batch(root.turn_root_id, batch_id, members, final=True)
    observation = await acc.observe(root.turn_root_id)
    if phase == "after":
        assert replay is not None and not replay.applied and replay.replayed
        assert observation.funded_microusd == 0
        assert observation.held_microusd == 300
    else:
        assert replay is None
        assert observation.funded_microusd == 200
        assert observation.held_microusd == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_production_path_and_cross_owner_are_unchanged(db):
    acc = await db.accounting()
    ordinary = await acc.legacy_reserve(10)
    await acc.settle(ordinary, 10)
    row = await reservation_row(acc, ordinary.id)
    assert row["turn_root_id"] is None and row["turn_op_id"] is None
    assert row["turn_batch_id"] is None and row["turn_final"] is None
    with pytest.raises(LimitExceeded):
        await acc.legacy_reserve(250000)

    other = await db.accounting()
    root = await acc.open_root(bound_microusd=100, funded_microusd=0)
    with pytest.raises(FreeTurnDenied) as caught:
        await other.reserve_one(root.turn_root_id, member("chat", 1))
    assert caught.value.code == "cross_account"

    other_reservation = await other.legacy_reserve(1)
    async with acc.database.database.connection() as owner:
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute(
                "UPDATE entitlement_reservations SET turn_root_id=$2, "
                "turn_op_id=$3, turn_batch_id=$3, turn_final=false WHERE id=$1",
                other_reservation.id,
                root.turn_root_id,
                uuid.uuid4(),
            )
    assert (await reservation_row(other, other_reservation.id))["turn_root_id"] is None
