"""Real-PG source qualification; execution requires explicit fixture approval.

Never interpret the default opt-in skip as financial/SQL qualification. Uses the
existing exact-owned synthetic cluster, real ledger and wire-fault bridge.
"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import asyncpg
import asyncpg.transaction
import pytest
import pytest_asyncio

from orchestrator.entitlements.errors import (
    AttemptReservationUnavailable,
    ReservationCommitUncertain,
    ReservationCommittedResultUnavailable,
    ReservationRecoveryUnresolved,
)
from orchestrator.entitlements import attempt
from orchestrator.entitlements.service import EntitlementService
from tests.compute_metadata_support import metadata_cluster as metadata_cluster
from tests.test_attempt_lifecycle_postgres import db as db, raw, seed


@pytest_asyncio.fixture
async def admission(db):
    async with db.connection() as owner:
        identity, _, owner_pool, tasks, _ = await seed(db, owner)
        try:
            await raw(owner, "register", identity, uuid4())
            # Synthetic test-only financial DML; no lifecycle table/helper grants.
            # Does NOT qualify deployed principals or raw-DML bypass prevention.
            db.sql(
                "GRANT SELECT,INSERT,UPDATE ON public.entitlement_accounts,"
                "public.entitlement_usage_periods,public.entitlement_reservations TO md_app"
            )
            async with db.pool() as pool:
                yield db, owner, pool, identity, tasks
        finally:
            await owner_pool.close()


async def counts(owner, identity):
    return await owner.fetchrow(
        "SELECT (SELECT count(*) FROM entitlement_reservations WHERE user_id=$1) AS reservations,"
        "(SELECT COALESCE(sum(reserved_microusd),0) FROM entitlement_usage_periods WHERE user_id=$1) AS held,"
        "(SELECT trial_reserved_microusd FROM entitlement_accounts WHERE user_id=$1) AS trial",
        identity.account_id,
    )


@pytest.mark.asyncio
async def test_real_initial_and_fallback_shaped_admission_share_attempt_not_cached_grant(admission):
    _, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    first = await service.reserve_for_attempt(identity, 13, operation="chat")
    second = await service.reserve_for_attempt(identity, 17, operation="chat")
    assert first.id != second.id
    result = await counts(owner, identity)
    assert (result["reservations"], result["held"]) == (2, 30)
    assert await owner.fetchval(
        "SELECT bool_and(scope_id=$2) FROM entitlement_reservations WHERE user_id=$1",
        identity.account_id,
        identity.financial_scope_id,
    )
    await raw(owner, "close", identity, uuid4())
    with pytest.raises(AttemptReservationUnavailable):
        await service.reserve_for_attempt(identity, 19, operation="chat")
    assert dict(await counts(owner, identity)) == dict(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "generation",
        "epoch",
        "account_id",
        "financial_scope_id",
        "metadata_scope_id",
        "producer_instance_id",
    ],
)
async def test_real_wrong_stale_identity_has_no_partial_financial_effect(admission, field):
    _, owner, pool, identity, _ = admission
    before = dict(await counts(owner, identity))
    bad = replace(
        identity,
        **{field: getattr(identity, field) + 1 if field in {"generation", "epoch"} else uuid4()},
    )
    with pytest.raises(AttemptReservationUnavailable):
        await EntitlementService(pool).reserve_for_attempt(bad, 13, operation="chat")
    assert dict(await counts(owner, identity)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["close", "cancel"])
@pytest.mark.parametrize("admission_first", [False, True])
async def test_real_close_cancel_commit_orderings(admission, monkeypatch, control, admission_first):
    db, owner, pool, identity, tasks = admission
    service = EntitlementService(pool)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = service._store.ensure_period

    async def pause(conn, **kw):
        await original(conn, **kw)
        entered.set()
        await proceed.wait()

    async def control_write():
        if control == "close":
            return await raw(owner, "close", identity, uuid4())
        return await tasks.request_cancel(identity.account_id, identity.task_id)

    if not admission_first:
        await control_write()
        with pytest.raises(AttemptReservationUnavailable):
            await service.reserve_for_attempt(identity, 13, operation="chat")
        assert (await counts(owner, identity))["reservations"] == 0
        return
    monkeypatch.setattr(service._store, "ensure_period", pause)
    running = asyncio.create_task(service.reserve_for_attempt(identity, 13, operation="chat"))
    controlling = None
    try:
        async with asyncio.timeout(1):
            await entered.wait()
        # Monitor as the same synthetic role as the control writer; do not query
        # its busy connection or depend on cross-role pg_stat_activity visibility.
        async with db.connection() as monitor:
            controlling = asyncio.create_task(control_write())
            async with asyncio.timeout(1):
                while not await monitor.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                    "AND usename=current_user AND wait_event_type='Lock' AND pid<>pg_backend_pid())"
                ):
                    await asyncio.sleep(0)
        assert not controlling.done()
        proceed.set()
        await running
        await controlling
        assert (await counts(owner, identity))["reservations"] == 1
    finally:
        proceed.set()
        for task in (running, controlling):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (running, controlling) if t is not None), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_real_expiry_after_financial_row_wait_rolls_back(admission, monkeypatch):
    db, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    period = service.current_period_key()
    await owner.execute(
        "INSERT INTO entitlement_usage_periods(user_id,period_key) VALUES($1,$2)",
        identity.account_id,
        period,
    )
    entered = asyncio.Event()
    original = service._store.hold
    pid = []

    async def observe(conn, **kw):
        pid.append(conn.get_server_pid())
        entered.set()
        return await original(conn, **kw)

    monkeypatch.setattr(service._store, "hold", observe)
    async with db.connection() as blocker:
        tx = blocker.transaction()
        await tx.start()
        await blocker.execute(
            "SELECT 1 FROM entitlement_usage_periods WHERE user_id=$1 FOR UPDATE",
            identity.account_id,
        )
        await owner.execute(
            "UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '100ms' WHERE id=$1",
            identity.task_id,
        )
        running = asyncio.create_task(service.reserve_for_attempt(identity, 13, operation="chat"))
        try:
            async with asyncio.timeout(1):
                await entered.wait()
            # pg_stat_activity wait details are visible to the same role, not
            # this fixture's different md_owner setup principal.
            async with db.connection("md_app") as monitor:
                await db.wait_lock(monitor, pid[0])
            await asyncio.sleep(0.12)
            await tx.rollback()
            with pytest.raises(AttemptReservationUnavailable) as caught:
                await running
            assert caught.value.lifecycle_code == "server_refused"
            assert caught.value.rollback_acknowledged
        finally:
            if blocker.is_in_transaction():
                await tx.rollback()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    state = await counts(owner, identity)
    assert (state["reservations"], state["held"]) == (0, 0)


@pytest.mark.asyncio
async def test_real_partial_write_python_refusal_caught_by_caller_rolls_back(
    admission, monkeypatch
):
    _, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    original = service._store.insert_reservation

    async def fail_after_insert(conn, **kw):
        await original(conn, **kw)
        raise RuntimeError("synthetic after actual INSERT")

    monkeypatch.setattr(service._store, "insert_reservation", fail_after_insert)
    before = dict(await counts(owner, identity))
    async with owner.transaction():
        try:
            await service.reserve_for_attempt(identity, 13, operation="chat")
        except AttemptReservationUnavailable as exc:
            assert exc.rollback_acknowledged
        else:
            pytest.fail("partial body committed")
    assert dict(await counts(owner, identity)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["before", "after"])
async def test_real_wire_commit_fault_receipt_recovery_no_readmission(admission, mode):
    db, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    db.bridge.commit_fault = mode
    try:
        with pytest.raises(ReservationCommitUncertain) as caught:
            await service.reserve_for_attempt(identity, 13, operation="chat")
        assert db.bridge.commit_seen.is_set()
        assert not caught.value.commit_acknowledged
    finally:
        db.bridge.commit_fault = None
    result = await service.recover_attempt_reservation(
        caught.value.receipt, commit_acknowledged=False
    )
    assert (result is None) == (mode == "before")
    assert (await counts(owner, identity))["reservations"] == (0 if mode == "before" else 1)


@pytest.mark.asyncio
async def test_real_acknowledged_commit_release_failure_does_not_kill_released_lease(
    admission, monkeypatch
):
    _, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    original = asyncpg.Pool.release
    once = True

    async def fail_after_release(releasing_pool, conn, **kw):
        nonlocal once
        await original(releasing_pool, conn, **kw)
        if releasing_pool is pool and once:
            once = False
            raise RuntimeError("synthetic after actual pool release")

    monkeypatch.setattr(asyncpg.Pool, "release", fail_after_release)
    with pytest.raises(ReservationCommittedResultUnavailable) as caught:
        await service.reserve_for_attempt(identity, 13, operation="chat")
    assert caught.value.commit_acknowledged and not caught.value.termination_failed
    async with pool.acquire() as next_lease:
        assert await next_lease.fetchval("SELECT 1") == 1
    result = await service.recover_attempt_reservation(
        caught.value.receipt, commit_acknowledged=True
    )
    assert result is not None and result.id == caught.value.receipt.id
    assert (await counts(owner, identity))["reservations"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["acquire", "open", "write", "commit", "release", "rollback"])
async def test_real_external_cancellation_preserves_phase_receipt_and_accounting(
    admission, monkeypatch, phase
):
    db, owner, pool, identity, _ = admission
    entered, proceed = asyncio.Event(), asyncio.Event()
    service = EntitlementService(pool)
    leases = []
    original_cancellation = []
    commit_seen = []

    async def wait_here():
        entered.set()
        try:
            await proceed.wait()
        except asyncio.CancelledError as exc:
            original_cancellation.append(exc)
            raise

    if phase == "acquire":
        leases = [await pool.acquire(), await pool.acquire()]
    elif phase == "open":
        original = attempt._AttemptTransaction.open

        async def pause_open(state):
            await original(state)
            await wait_here()

        monkeypatch.setattr(attempt._AttemptTransaction, "open", pause_open)
    elif phase in {"write", "rollback"}:
        original = service._store.insert_reservation

        async def pause_write(conn, **kw):
            row = await original(conn, **kw)
            if phase == "rollback":
                raise RuntimeError("synthetic refusal after real INSERT")
            await wait_here()
            return row

        monkeypatch.setattr(service._store, "insert_reservation", pause_write)
        if phase == "rollback":
            original_rollback = asyncpg.transaction.Transaction.rollback

            async def pause_rollback(tx):
                await wait_here()
                await original_rollback(tx)

            monkeypatch.setattr(asyncpg.transaction.Transaction, "rollback", pause_rollback)
    elif phase == "commit":
        db.bridge.commit_fault = "after"
    else:
        # Real asyncpg shielded release, with a synthetic reset pause. The
        # driver owns cleanup once release starts; the service must not reclaim it.
        async def reset(conn):
            await wait_here()
            await conn.reset()

        # No borrowed connection/transaction enters the API; it still owns its pool lease.
        async with db.pool(reset=reset) as reset_pool:
            service = EntitlementService(reset_pool)
            running = asyncio.create_task(
                service.reserve_for_attempt(identity, 13, operation="chat")
            )
            try:
                async with asyncio.timeout(1):
                    await entered.wait()
                running.cancel("real release cancellation")
                with pytest.raises(asyncio.CancelledError) as caught:
                    await running
                assert caught.value.args == ("real release cancellation",)
                assert getattr(caught.value, "reservation_outcome") == "committed"
                receipt = getattr(caught.value, "reservation_receipt")
                assert receipt is not None and getattr(
                    caught.value, "reservation_commit_acknowledged"
                )
            finally:
                proceed.set()
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        result = await EntitlementService(pool).recover_attempt_reservation(
            receipt, commit_acknowledged=True
        )
        assert result is not None and result.id == receipt.id
        assert (await counts(owner, identity))["held"] == 13
        return

    running = asyncio.create_task(service.reserve_for_attempt(identity, 13, operation="chat"))
    try:
        if phase == "acquire":
            async with asyncio.timeout(1):
                while not pool._queue._getters:
                    await asyncio.sleep(0)
        elif phase == "commit":
            async with asyncio.timeout(1):
                await db.bridge.commit_seen.wait()
            commit_seen.append(True)
        else:
            async with asyncio.timeout(1):
                await entered.wait()
        running.cancel("real phase cancellation")
        with pytest.raises(asyncio.CancelledError) as caught:
            await running
        assert caught.value.args == ("real phase cancellation",)
        if original_cancellation:
            assert caught.value is original_cancellation[0]
        assert getattr(caught.value, "reservation_identity") is identity
        assert getattr(caught.value, "reservation_outcome") == (
            "uncertain" if phase == "commit" else "not_committed"
        )
        assert not getattr(caught.value, "reservation_commit_acknowledged")
        receipt = getattr(caught.value, "reservation_receipt")
    finally:
        proceed.set()
        db.bridge.commit_fault = None
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        for lease in leases:
            await pool.release(lease)
    if commit_seen:
        assert receipt is not None
        result = await service.recover_attempt_reservation(receipt, commit_acknowledged=False)
        # asyncpg may forward a server CancelRequest through a second bridge
        # connection. Unknown COMMIT must allow either durable truth, not assume
        # forwarding the COMMIT frame guaranteed server acknowledgement.
        assert result is None or result.id == receipt.id
        state = await counts(owner, identity)
        assert (state["reservations"], state["held"]) == ((0, 0) if result is None else (1, 13))
    else:
        assert receipt is None
        # Cross the same account barrier before observing rolled-back accounting.
        async with owner.transaction():
            await owner.fetchval(
                "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                identity.account_id,
            )
            state = await counts(owner, identity)
            assert (state["reservations"], state["held"]) == (0, 0)


@pytest.mark.asyncio
async def test_real_recovery_barrier_uses_one_absolute_deadline_and_never_readmits(
    admission, monkeypatch
):
    db, owner, pool, identity, _ = admission
    service = EntitlementService(pool)
    receipts = []
    original = attempt._AttemptTransaction.commit

    async def capture(state):
        receipts.append(state.receipt)
        await original(state)

    monkeypatch.setattr(attempt._AttemptTransaction, "commit", capture)
    reservation = await service.reserve_for_attempt(identity, 13, operation="chat")
    receipt = receipts[0]
    assert receipt.reservation is reservation
    before = dict(await counts(owner, identity))
    async with db.connection() as blocker:
        tx = blocker.transaction()
        await tx.start()
        await blocker.fetchval(
            "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", identity.account_id
        )
        monkeypatch.setattr(attempt, "OPERATION_S", 0.08)
        start = asyncio.get_running_loop().time()
        try:
            with pytest.raises(ReservationRecoveryUnresolved) as caught:
                await service.recover_attempt_reservation(receipt, commit_acknowledged=True)
            assert caught.value.receipt is receipt and caught.value.commit_acknowledged
            # Distinguish the absolute operation timeout from the longer SQL
            # lock timeout, which maps to the same public unresolved error.
            assert isinstance(caught.value.__context__, TimeoutError)
            assert not caught.value.rollback_acknowledged
            assert asyncio.get_running_loop().time() - start < 0.5
        finally:
            await tx.rollback()
    assert dict(await counts(owner, identity)) == before
