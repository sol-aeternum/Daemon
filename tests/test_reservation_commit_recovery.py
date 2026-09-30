"""Commit-ack fault injection against isolated real PostgreSQL schemas.

No provider calls. The fault wrapper really commits/rolls back on PostgreSQL
before raising, or retains the original transaction to exercise the lock barrier.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio

from orchestrator import compute_runtime as runtime
from orchestrator.entitlements.errors import (
    BudgetExceeded,
    ReservationCommitUncertain,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)
from orchestrator.entitlements.models import Reservation
from orchestrator.entitlements.plans import ChargeKind, Plan, ReservationStatus
from orchestrator.entitlements.service import EntitlementService


@pytest_asyncio.fixture
async def recovery_database() -> AsyncIterator[tuple[Any, uuid.UUID]]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"reservation_recovery_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=6,
            server_settings={
                "search_path": schema,
                "default_transaction_isolation": "repeatable read",
            },
        )
        uid = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute("INSERT INTO users VALUES ($1)", uid)
            await conn.execute(
                (
                    Path(__file__).resolve().parents[1]
                    / "migrations/039_entitlements_commercial.sql"
                ).read_text()
            )
        yield pool, uid
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


class _FaultPool:
    """Intercept only transaction exit after one real reservation INSERT."""

    def __init__(self, pool: Any, mode: str) -> None:
        self.pool = pool
        self.mode = mode
        self.armed = True
        self.inserted = False
        self.insert_count = 0
        self.pending_connection: Any = None
        self.pending_transaction: Any = None
        self.recovery_acquired = asyncio.Event()

    def acquire(self) -> _FaultLease:
        return _FaultLease(self)

    async def finish(self, *, committed: bool) -> None:
        conn, tx = self.pending_connection, self.pending_transaction
        if conn is None:
            return
        try:
            if committed:
                await tx.__aexit__(None, None, None)
            else:
                await tx.__aexit__(RuntimeError, RuntimeError("synthetic rollback"), None)
        finally:
            self.pending_connection = self.pending_transaction = None
            await self.pool.release(conn)


class _FaultLease:
    def __init__(self, fault: _FaultPool) -> None:
        self.fault = fault
        self.conn: Any = None

    async def __aenter__(self) -> _FaultConnection:
        self.conn = await self.fault.pool.acquire()
        if not self.fault.armed:
            self.fault.recovery_acquired.set()
        return _FaultConnection(self.fault, self.conn)

    async def __aexit__(self, *args: Any) -> None:
        if self.fault.pending_connection is not self.conn:
            await self.fault.pool.release(self.conn)


class _FaultConnection:
    def __init__(self, fault: _FaultPool, conn: Any) -> None:
        self.fault = fault
        self.conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        row = await self.conn.fetchrow(query, *args)
        if "INSERT INTO entitlement_reservations" in query:
            self.fault.inserted = True
            self.fault.insert_count += 1
        return row

    def transaction(self, *args: Any, **kwargs: Any) -> _FaultTransaction:
        return _FaultTransaction(self.fault, self.conn, self.conn.transaction(*args, **kwargs))


class _FaultTransaction:
    def __init__(self, fault: _FaultPool, conn: Any, tx: Any) -> None:
        self.fault, self.conn, self.tx = fault, conn, tx

    async def __aenter__(self) -> Any:
        return await self.tx.__aenter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if exc_type is not None or not self.fault.armed or not self.fault.inserted:
            return await self.tx.__aexit__(exc_type, exc, tb)
        self.fault.armed = False
        if self.fault.mode in ("commit", "cancel"):
            await self.tx.__aexit__(None, None, None)
        elif self.fault.mode == "rollback":
            await self.tx.__aexit__(RuntimeError, RuntimeError("synthetic rollback"), None)
        else:
            self.fault.pending_connection = self.conn
            self.fault.pending_transaction = self.tx
        if self.fault.mode == "cancel":
            raise asyncio.CancelledError
        raise RuntimeError("synthetic transaction-exit acknowledgement failure")


def _approval(monkeypatch: pytest.MonkeyPatch) -> runtime.ToolServiceApproval:
    approval = runtime.ToolServiceApproval(
        service_id="test-search",
        service="web_search",
        provider="brave",
        unit="call",
        ceiling_microusd=7000,
        fixed_microusd=5000,
    )
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **kwargs: approval)
    return approval


async def _assert_zero_money(pool: Any, uid: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM entitlement_reservations WHERE user_id=$1 AND status='open'",
                uid,
            )
            == 0
        )
        row = await conn.fetchrow(
            "SELECT COALESCE(sum(spent_microusd),0) spent, "
            "COALESCE(sum(reserved_microusd),0) reserved, "
            "COALESCE(sum(extended_spent_microusd),0) extended_spent, "
            "COALESCE(sum(extended_reserved_microusd),0) extended_reserved "
            "FROM entitlement_usage_periods "
            "WHERE user_id=$1",
            uid,
        )
        assert dict(row) == {
            "spent": 0,
            "reserved": 0,
            "extended_spent": 0,
            "extended_reserved": 0,
        }
        assert (
            await conn.fetchval(
                "SELECT COALESCE(sum(overage_microusd),0) FROM entitlement_reservations WHERE user_id=$1",
                uid,
            )
            == 0
        )
        trial = await conn.fetchrow(
            "SELECT trial_consumed_microusd, trial_reserved_microusd "
            "FROM entitlement_accounts WHERE user_id=$1",
            uid,
        )
        assert trial is None or dict(trial) == {
            "trial_consumed_microusd": 0,
            "trial_reserved_microusd": 0,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["commit", "rollback"])
@pytest.mark.parametrize("dispatch_aware", [False, True])
async def test_metered_commit_exit_failure_tracks_and_zero_settles_without_http(
    recovery_database: Any, monkeypatch: pytest.MonkeyPatch, mode: str, dispatch_aware: bool
) -> None:
    pool, uid = recovery_database
    fault = _FaultPool(pool, mode)
    service = EntitlementService(cast(asyncpg.Pool, fault))
    scope = runtime.ComputeScope(uid, service)
    http = AsyncMock()
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_tool_call(
            _approval(monkeypatch),
            scope=scope,
            dispatch_aware=dispatch_aware,
        ):
            await http()
    assert caught.value.code == "account_unavailable"
    assert scope.outstanding == {}
    assert fault.insert_count == 1
    http.assert_not_awaited()
    await _assert_zero_money(pool, uid)
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT status, actual_microusd FROM entitlement_reservations")
        assert [dict(row) for row in rows] == (
            [{"status": "settled", "actual_microusd": 0}] if mode == "commit" else []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("new_account", [False, True])
async def test_receipt_lookup_cannot_overtake_original_account_transaction(
    recovery_database: Any, committed: bool, new_account: bool
) -> None:
    pool, uid = recovery_database
    if not new_account:
        await EntitlementService(pool).resolve(uid)
    fault = _FaultPool(pool, "pending")
    service = EntitlementService(cast(asyncpg.Pool, fault))
    with pytest.raises(ReservationCommitUncertain) as caught:
        await service.reserve(uid, 7000, operation="chat", route_id="test-search")
    receipt = caught.value.receipt
    get = AsyncMock(wraps=service._store.get_reservation)
    service._store.get_reservation = get
    recovery = asyncio.create_task(service.recover_reservation(receipt))
    try:
        await asyncio.wait_for(fault.recovery_acquired.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert not recovery.done()
        get.assert_not_awaited()  # neither account/uniqueness barrier was skipped
        await fault.finish(committed=committed)
        result = await asyncio.wait_for(recovery, timeout=2)
        assert (result is not None) is committed
        if result is not None:
            assert result.id == receipt.id
        await service.settle(receipt, 0)
        await _assert_zero_money(pool, uid)
    finally:
        await fault.finish(committed=False)
        if not recovery.done():
            recovery.cancel()
        await asyncio.gather(recovery, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "amount", "route", "scope", "operation"])
async def test_receipt_binding_mismatch_never_settles_another_context(
    recovery_database: Any, mismatch: str
) -> None:
    pool, uid = recovery_database
    service = EntitlementService(cast(asyncpg.Pool, _FaultPool(pool, "commit")))
    with pytest.raises(ReservationCommitUncertain) as caught:
        await service.reserve(uid, 7000, operation="chat", route_id="test-search")
    receipt = caught.value.receipt
    if mismatch == "owner":
        receipt = replace(receipt, user_id=uuid.uuid4())
    elif mismatch == "amount":
        receipt = replace(receipt, reservation=replace(receipt.reservation, reserved_microusd=1))
    elif mismatch == "route":
        receipt = replace(receipt, route_id="different-route")
    elif mismatch == "scope":
        receipt = replace(receipt, scope_id=uuid.uuid4())
    else:
        receipt = replace(receipt, reservation=replace(receipt.reservation, operation="research"))
    for attempt in (service.recover_reservation(receipt), service.settle(receipt, 0)):
        with pytest.raises(ReservationRecoveryUnresolved):
            await attempt
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, actual_microusd FROM entitlement_reservations WHERE id=$1",
            receipt.id,
        )
        assert dict(row) == {"status": "open", "actual_microusd": None}
        assert (
            await conn.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1", uid
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interruption", "dispatch_aware"), [("cancel", False), ("cancel", True), ("deadline", True)]
)
async def test_interrupted_commit_recovery_waits_barrier_and_settles_once(
    recovery_database: Any, monkeypatch: pytest.MonkeyPatch, interruption: str, dispatch_aware: bool
) -> None:
    pool, uid = recovery_database
    await EntitlementService(pool).resolve(uid)
    fault = _FaultPool(pool, "pending")
    service = EntitlementService(cast(asyncpg.Pool, fault))
    mark = AsyncMock(wraps=service._store.mark_reservation)
    service._store.mark_reservation = mark
    scope = runtime.ComputeScope(uid, service)
    http = AsyncMock()

    async def call() -> None:
        async with runtime.metered_tool_call(
            _approval(monkeypatch),
            scope=scope,
            dispatch_aware=dispatch_aware,
            work_deadline=asyncio.get_running_loop().time() + 0.05
            if interruption == "deadline"
            else None,
        ):
            await http()

    task = asyncio.create_task(call())
    try:
        await asyncio.wait_for(fault.recovery_acquired.wait(), timeout=1)
        if interruption == "cancel":
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        else:
            await asyncio.sleep(0.06)
        await asyncio.sleep(0)
        assert not task.done()
        assert len(scope.outstanding) == 1
        hold = next(iter(scope.outstanding.values()))
        assert isinstance(hold.reservation, ReservationReceipt) and hold.actual == 0
        await fault.finish(committed=True)
        with pytest.raises(asyncio.CancelledError if interruption == "cancel" else TimeoutError):
            await asyncio.wait_for(task, timeout=2)
        assert scope.outstanding == {}
        assert mark.await_count == fault.insert_count == 1
        http.assert_not_awaited()
        await _assert_zero_money(pool, uid)
    finally:
        await fault.finish(committed=False)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _receipt(uid: uuid.UUID, scope_id: uuid.UUID) -> ReservationReceipt:
    return ReservationReceipt(
        reservation=Reservation(
            id=uuid.uuid4(),
            user_id=uid,
            period_key="2026-09",
            plan=Plan.FREE,
            operation="chat",
            charge_kind=ChargeKind.PLAN,
            premium=False,
            extended=False,
            reserved_microusd=7000,
            status=ReservationStatus.OPEN,
            created_at=datetime.now(timezone.utc),
        ),
        user_id=uid,
        scope_id=scope_id,
        provider="brave",
        model=None,
        route_id="test-search",
        extended_run=False,
        background=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [False, True])
async def test_database_down_during_recovery_retains_known_zero_and_fails_cleanup(
    monkeypatch: pytest.MonkeyPatch, deadline: bool
) -> None:
    pool = MagicMock(spec=asyncpg.Pool)
    pool.acquire.side_effect = ConnectionError("database unavailable")
    service = EntitlementService(pool)
    scope = runtime.ComputeScope(uuid.uuid4(), service)
    receipt = _receipt(scope.user_id, scope.scope_id)

    async def reserve(*args: Any, **kwargs: Any) -> Any:
        if deadline:
            await asyncio.sleep(0.03)
        raise ReservationCommitUncertain(receipt)

    service.reserve = AsyncMock(side_effect=reserve)
    http = AsyncMock()
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_tool_call(
            _approval(monkeypatch),
            scope=scope,
            dispatch_aware=True,
            work_deadline=asyncio.get_running_loop().time() + 0.01 if deadline else None,
        ):
            await http()
    assert caught.value.code == "reservation_outcome_unresolved"
    assert not caught.value.retryable
    hold = scope.outstanding[receipt.id]
    assert hold.reservation is receipt and hold.actual == 0
    with pytest.raises(runtime.ComputeUnavailable) as cleanup:
        await scope.settle(receipt, 0)
    assert cleanup.value.code == "settlement_failed"
    assert scope.outstanding[receipt.id].actual == 0
    http.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatched_metered_receipt_cannot_be_freed_by_scope_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EntitlementService(MagicMock(spec=asyncpg.Pool))
    scope = runtime.ComputeScope(uuid.uuid4(), service)
    receipt = _receipt(uuid.uuid4(), uuid.uuid4())
    service.reserve = AsyncMock(side_effect=ReservationCommitUncertain(receipt))
    service.recover_reservation = AsyncMock(return_value=receipt.reservation)
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_tool_call(
            _approval(monkeypatch), scope=scope, dispatch_aware=True
        ):
            pytest.fail("mismatched receipt must not permit dispatch")
    assert caught.value.code == "reservation_outcome_unresolved"
    with pytest.raises(runtime.ComputeUnavailable):
        await scope.settle(receipt, 0)
    service.recover_reservation.assert_not_awaited()
    assert scope.outstanding[receipt.id].actual == 0


@pytest.mark.asyncio
async def test_transaction_body_denial_does_not_become_commit_uncertainty(
    recovery_database: Any,
) -> None:
    pool, uid = recovery_database
    fault = _FaultPool(pool, "commit")
    service = EntitlementService(cast(asyncpg.Pool, fault))
    with pytest.raises(BudgetExceeded):
        await service.reserve(uid, 10**9, operation="chat")
    assert fault.insert_count == 0
    await _assert_zero_money(pool, uid)


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_aware", [False, True])
async def test_cancelled_commit_ack_still_recovers_identity_and_zero_settles(
    recovery_database: Any, monkeypatch: pytest.MonkeyPatch, dispatch_aware: bool
) -> None:
    pool, uid = recovery_database
    fault = _FaultPool(pool, "cancel")
    service = EntitlementService(cast(asyncpg.Pool, fault))
    scope = runtime.ComputeScope(uid, service)
    http = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        async with runtime.metered_tool_call(
            _approval(monkeypatch),
            scope=scope,
            dispatch_aware=dispatch_aware,
        ):
            await http()
    assert fault.insert_count == 1
    assert scope.outstanding == {}
    http.assert_not_awaited()
    await _assert_zero_money(pool, uid)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "route"])
async def test_changed_committed_row_is_retained_as_unresolved_not_blindly_settled(
    recovery_database: Any, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    pool, uid = recovery_database
    service = EntitlementService(cast(asyncpg.Pool, _FaultPool(pool, "commit")))
    scope = runtime.ComputeScope(uid, service)
    recover = service.recover_reservation
    other = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users VALUES ($1)", other)

    async def changed(receipt: ReservationReceipt) -> Reservation | None:
        async with pool.acquire() as conn:
            if mismatch == "owner":
                await conn.execute(
                    "UPDATE entitlement_reservations SET user_id=$2 WHERE id=$1", receipt.id, other
                )
            else:
                await conn.execute(
                    "UPDATE entitlement_reservations SET route_id=$2 WHERE id=$1",
                    receipt.id,
                    "different-route",
                )
        return await recover(receipt)

    service.recover_reservation = changed
    http = AsyncMock()
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_tool_call(
            _approval(monkeypatch), scope=scope, dispatch_aware=True
        ):
            await http()
    assert caught.value.code == "reservation_outcome_unresolved"
    assert len(scope.outstanding) == 1
    hold = next(iter(scope.outstanding.values()))
    assert hold.actual == 0 and isinstance(hold.reservation, ReservationReceipt)
    with pytest.raises(runtime.ComputeUnavailable) as cleanup:
        await scope.settle(hold.reservation, 0)
    assert cleanup.value.code == "settlement_failed"
    assert len(scope.outstanding) == 1
    http.assert_not_awaited()
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT status FROM entitlement_reservations WHERE id=$1", hold.reservation.id
            )
            == "open"
        )
        assert (
            await conn.fetchval(
                "SELECT spent_microusd FROM entitlement_usage_periods WHERE user_id=$1", uid
            )
            == 0
        )
