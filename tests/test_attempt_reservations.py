"""Fake driver/store tests ONLY: not PostgreSQL or financial-race qualification."""

from dataclasses import replace
from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import asyncpg.pool
import pytest

from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.entitlements import attempt
from orchestrator.entitlements.errors import (
    AttemptReservationUnavailable,
    BudgetExceeded,
    ReservationCommitUncertain,
    ReservationCommittedResultUnavailable,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)
from orchestrator.entitlements.ledger import PeriodState
from orchestrator.entitlements.models import AccountRecord
from orchestrator.entitlements.plans import AccountStatus, Plan, TrialState
from orchestrator.entitlements.service import EntitlementService
from orchestrator.entitlements.store import EntitlementStore


class FakeDriver:
    """Observable transaction model, not a replacement for actual SQL assertions."""

    def __init__(self):
        self.events = []
        self.pending = []
        self.durable = []
        self.in_tx = False
        self.fail = None
        self.cancel = None
        self.cancellation = asyncio.CancelledError("original synthetic cancellation")
        self.gates = 0
        self.refuse_gate = None
        self.commit_mode = None
        self.delay = {}
        self.terminated = False
        self.released = False
        self.isolation = None
        self.seen_receipt = None

    async def step(self, name):
        self.events.append(name)
        if name in self.delay:
            await asyncio.sleep(self.delay[name])
        if self.cancel == name:
            raise self.cancellation
        if self.fail == name:
            raise RuntimeError("synthetic " + name)

    async def execute(self, sql, *args):
        if "attempt_lifecycle_assert_open" in sql:
            self.gates += 1
            await self.step("gate" + str(self.gates))
            if self.gates == self.refuse_gate:
                raise asyncpg.RaiseError("synthetic lifecycle refusal")
        else:
            assert "lock_timeout='250ms'" in sql
            assert "statement_timeout='1000ms'" in sql
            await self.step("settings")

    async def fetchval(self, *args):
        await self.step("preflight")
        return True

    def is_in_transaction(self):
        return self.in_tx

    def transaction(self, *, isolation):
        self.isolation = isolation
        return self

    async def start(self):
        await self.step("start")
        self.in_tx = True

    async def commit(self):
        if self.commit_mode == "sent":
            self.durable.extend(self.pending)
            self.pending.clear()
            self.in_tx = False
            raise ConnectionError("synthetic lost COMMIT acknowledgement")
        await self.step("commit")
        if self.commit_mode == "unsent":
            raise ConnectionError("synthetic unsent COMMIT")
        self.durable.extend(self.pending)
        self.pending.clear()
        self.in_tx = False

    async def rollback(self):
        await self.step("rollback")
        self.pending.clear()
        self.in_tx = False

    def terminate(self):
        self.events.append("terminate")
        self.terminated = True
        self.pending.clear()
        self.in_tx = False


@pytest.fixture
def rig(monkeypatch):
    ident = AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    driver = FakeDriver()
    pool = MagicMock(spec=asyncpg.Pool)

    async def acquire(**kw):
        assert 0 < kw["timeout"] <= 0.5
        await driver.step("acquire")
        return driver

    async def release(conn, **kw):
        assert conn is driver
        assert 0 < kw["timeout"] <= 0.5
        await driver.step("release")
        driver.released = True

    pool.acquire = AsyncMock(side_effect=acquire)
    pool.release = AsyncMock(side_effect=release)
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    service = EntitlementService(pool, clock=lambda: now)
    store = MagicMock(spec=EntitlementStore)
    record = AccountRecord(
        ident.account_id,
        Plan.FREE,
        "fixture",
        AccountStatus.ACTIVE,
        False,
        TrialState.ACTIVE,
        1000000,
        0,
        0,
    )
    row = {
        "id": uuid4(),
        "user_id": ident.account_id,
        "period_key": "2026-10",
        "plan": "free",
        "operation": "chat",
        "charge_kind": "trial",
        "premium": True,
        "extended": False,
        "reserved_microusd": 1,
        "status": "open",
        "overage_microusd": 0,
        "created_at": now,
        "scope_id": ident.financial_scope_id,
        "provider": None,
        "model": None,
        "route_id": None,
        "extended_run": False,
        "background": False,
    }

    def read(name, value):
        async def fn(conn, *args, **kw):
            assert conn is driver
            await driver.step(name)
            return value

        return AsyncMock(side_effect=fn)

    def write(name, value=None):
        async def fn(conn, *args, **kw):
            assert conn is driver
            await driver.step(name)
            driver.pending.append(name)
            return value

        return AsyncMock(side_effect=fn)

    store.get_account = read("get_account", record)
    store.lock_account = read("lock_account", record)
    store.operation_is_open = read("operation_open", False)
    store.operation_has_rate_slot = read("rate_slot", False)
    store.read_period_state = read("period_state", PeriodState("2026-10"))
    store.ensure_period = write("ensure_period")
    store.hold = write("hold", {})
    store.insert_reservation = write("insert", row)
    store.hold_trial = write("trial")
    store.get_reservation = read("lookup", row)
    monkeypatch.setattr(service, "_store", store)
    monkeypatch.setattr(service, "_record_encounter", AsyncMock())
    original = attempt._AttemptTransaction.commit

    async def observe_commit(state):
        driver.seen_receipt = state.receipt
        await original(state)

    monkeypatch.setattr(attempt._AttemptTransaction, "commit", observe_commit)
    return service, ident, driver, row


async def reserve(rig, **kw):
    service, ident, _, _ = rig
    return await service.reserve_for_attempt(ident, 1, operation="chat", premium=True, **kw)


@pytest.mark.asyncio
async def test_success_orders_gates_writes_commit_release_and_original_receipt(rig):
    result = await reserve(rig)
    service, identity, d, row = rig
    assert result.id == row["id"]
    assert d.isolation == "read_committed"
    assert d.events.index("gate1") < d.events.index("lock_account")
    assert [x for x in d.events if x.startswith("gate")] == ["gate" + str(n) for n in range(1, 7)]
    for write in ("ensure_period", "hold", "insert", "trial"):
        assert d.events[d.events.index(write) - 1].startswith("gate")
    assert d.events[-3:] == ["gate6", "commit", "release"]
    assert d.durable == ["ensure_period", "hold", "insert", "trial"]
    assert d.seen_receipt.reservation is result
    assert d.seen_receipt.scope_id == identity.financial_scope_id
    service._record_encounter.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, {}, "receipt", "epoch", "generation"])
async def test_exact_identity_no_downgrade_or_acquisition(rig, bad):
    service, identity, d, _ = rig
    if bad == "epoch":
        bad = replace(identity, epoch=0)
    elif bad == "generation":
        bad = replace(identity, generation=True)
    with pytest.raises(AttemptReservationUnavailable) as caught:
        await service.reserve_for_attempt(bad, 1, operation="chat")
    assert caught.value.code == "identity_refused"
    assert not d.events


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["conn", "tx", "scope_id", "user_id"])
async def test_no_supplied_connection_outer_transaction_or_duplicate_identity(rig, keyword):
    with pytest.raises(TypeError):
        await reserve(rig, **{keyword: object()})
    assert not rig[2].events


@pytest.mark.asyncio
async def test_existing_transaction_is_refused_not_nested(rig):
    service, _, d, _ = rig
    d.in_tx = True
    with pytest.raises(AttemptReservationUnavailable) as caught:
        await reserve(rig)
    assert caught.value.code == "connection_state"
    assert "start" not in d.events and "commit" not in d.events
    service._record_encounter.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [1, 3, 4, 5, 6])
async def test_fake_refusal_after_partial_writes_cannot_commit_if_caller_catches(rig, gate):
    service, _, d, _ = rig
    d.refuse_gate = gate
    try:
        await reserve(rig)
    except AttemptReservationUnavailable as exc:
        assert exc.code == "lifecycle_refused"
        assert exc.rollback_acknowledged
    else:
        pytest.fail("refusal swallowed")
    assert not d.durable and not d.pending
    assert "commit" not in d.events and d.released
    service._record_encounter.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [
        "acquire",
        "start",
        "settings",
        "preflight",
        "gate1",
        "get_account",
        "lock_account",
        "ensure_period",
        "hold",
        "insert",
        "trial",
        "gate6",
        "commit",
        "release",
    ],
)
async def test_original_cancellation_phase_and_receipt_are_preserved(rig, phase):
    _, identity, d, _ = rig
    d.cancel = phase
    with pytest.raises(asyncio.CancelledError) as caught:
        await reserve(rig)
    assert caught.value is d.cancellation
    assert getattr(caught.value, "reservation_identity") is identity
    expected = (
        "committed" if phase == "release" else "uncertain" if phase == "commit" else "not_committed"
    )
    assert getattr(caught.value, "reservation_outcome") == expected
    assert getattr(caught.value, "reservation_receipt") is d.seen_receipt
    assert ("release" in d.events) == (phase == "release")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sent", "unsent"])
async def test_fake_sent_unsent_commit_is_uncertain_not_retried(rig, mode):
    _, _, d, _ = rig
    d.commit_mode = mode
    with pytest.raises(ReservationCommitUncertain) as caught:
        await reserve(rig)
    assert caught.value.receipt is d.seen_receipt
    assert not caught.value.commit_acknowledged
    assert bool(d.durable) == (mode == "sent")
    assert "rollback" not in d.events and d.terminated


@pytest.mark.asyncio
async def test_acknowledged_commit_release_failure_is_not_rollback(rig):
    _, _, d, _ = rig
    d.fail = "release"
    with pytest.raises(ReservationCommittedResultUnavailable) as caught:
        await reserve(rig)
    assert caught.value.commit_acknowledged
    assert caught.value.receipt is d.seen_receipt
    assert d.durable and "rollback" not in d.events


@pytest.mark.asyncio
async def test_financial_denial_does_not_add_unguarded_analytics(rig):
    service, identity, d, _ = rig
    with pytest.raises(BudgetExceeded):
        await service.reserve_for_attempt(identity, 10**12, operation="chat")
    assert not d.durable and not d.pending and "commit" not in d.events
    service._record_encounter.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_absolute_budget_not_restarted_after_each_step(rig, monkeypatch):
    _, _, d, _ = rig
    monkeypatch.setattr(attempt, "OPERATION_S", 0.08)
    d.delay = {"acquire": 0.03, "hold": 0.03, "trial": 0.03}
    start = asyncio.get_running_loop().time()
    with pytest.raises(AttemptReservationUnavailable):
        await reserve(rig)
    assert asyncio.get_running_loop().time() - start < 0.3
    assert "commit" not in d.events and "rollback" not in d.events
    assert d.terminated and not d.durable


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["rollback", "release"])
async def test_cleanup_cancellation_supersedes_prior_refusal_with_diagnostics(rig, phase):
    _, _, d, _ = rig
    d.refuse_gate = 6
    d.cancel = phase
    with pytest.raises(asyncio.CancelledError) as caught:
        await reserve(rig)
    assert caught.value is d.cancellation
    assert getattr(caught.value, "reservation_receipt") is d.seen_receipt
    assert getattr(caught.value, "reservation_outcome") == "not_committed"
    assert not getattr(caught.value, "reservation_commit_acknowledged")
    assert getattr(caught.value, "reservation_rollback_acknowledged") == (phase == "release")
    assert d.terminated == (phase == "rollback")
    assert not d.durable and "commit" not in d.events


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["rollback", "release"])
async def test_external_task_cancel_during_refusal_cleanup_is_not_consumed(rig, monkeypatch, phase):
    _, identity, d, _ = rig
    d.refuse_gate = 6
    entered = asyncio.Event()
    original = d.step

    async def wait_in_cleanup(name):
        await original(name)
        if name == phase:
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(d, "step", wait_in_cleanup)
    task = asyncio.create_task(reserve(rig))
    try:
        async with asyncio.timeout(1):
            await entered.wait()
        task.cancel("external cleanup cancellation")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("external cleanup cancellation",)
        assert task.cancelled()
        assert getattr(caught.value, "reservation_identity") is identity
        assert getattr(caught.value, "reservation_receipt") is d.seen_receipt
        assert getattr(caught.value, "reservation_outcome") == "not_committed"
        assert getattr(caught.value, "reservation_rollback_acknowledged") == (phase == "release")
        assert d.terminated == (phase == "rollback")
        assert "commit" not in d.events and not d.durable
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_final_commit_guard_refusal_retains_provisional_receipt_not_authority(rig):
    _, _, d, _ = rig
    d.refuse_gate = 6
    with pytest.raises(AttemptReservationUnavailable) as caught:
        await reserve(rig)
    assert caught.value.code == "lifecycle_refused"
    assert getattr(caught.value, "reservation_receipt") is d.seen_receipt
    assert isinstance(d.seen_receipt, ReservationReceipt)
    assert not caught.value.commit_acknowledged and caught.value.rollback_acknowledged
    assert "commit" not in d.events and not d.durable and not d.pending


@pytest.mark.asyncio
async def test_server_commit_refusal_stays_conservatively_uncertain(rig, monkeypatch):
    _, _, d, _ = rig

    async def refused_commit():
        d.events.append("commit")
        raise asyncpg.SerializationError("synthetic server COMMIT refusal")

    monkeypatch.setattr(d, "commit", refused_commit)
    with pytest.raises(ReservationCommitUncertain) as caught:
        await reserve(rig)
    assert caught.value.receipt is d.seen_receipt
    assert not caught.value.commit_acknowledged
    assert "rollback" not in d.events and d.terminated and not d.durable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "present,ack", [(True, False), (True, True), (False, False), (False, True)]
)
async def test_explicit_recovery_known_commit_absence_and_matching(rig, present, ack):
    service, _, d, _ = rig
    await reserve(rig)
    receipt = d.seen_receipt
    assert isinstance(receipt, ReservationReceipt)
    d.events.clear()
    if not present:
        service._store.get_reservation = AsyncMock(return_value=None)
    if not present and ack:
        with pytest.raises(ReservationRecoveryUnresolved) as caught:
            await service.recover_attempt_reservation(receipt, commit_acknowledged=ack)
        assert caught.value.receipt is receipt and caught.value.commit_acknowledged
    else:
        result = await service.recover_attempt_reservation(receipt, commit_acknowledged=ack)
        assert (result is not None) == present
    assert not any(x.startswith("gate") for x in d.events)
    assert not any(x in d.events for x in ("ensure_period", "hold", "insert", "trial"))


@pytest.mark.asyncio
async def test_recovery_mismatch_and_non_bool_ack_never_mean_absence(rig):
    service, _, d, row = rig
    await reserve(rig)
    receipt = d.seen_receipt
    row["scope_id"] = uuid4()
    with pytest.raises(ReservationRecoveryUnresolved):
        await service.recover_attempt_reservation(receipt, commit_acknowledged=False)
    d.events.clear()
    with pytest.raises(ReservationRecoveryUnresolved):
        await service.recover_attempt_reservation(receipt, commit_acknowledged=1)
    assert not d.events


@pytest.mark.asyncio
async def test_budget_expires_during_release_after_acknowledged_commit(rig, monkeypatch):
    _, _, d, _ = rig
    monkeypatch.setattr(attempt, "OPERATION_S", 0.05)
    d.delay["release"] = 1
    with pytest.raises(ReservationCommittedResultUnavailable) as caught:
        await reserve(rig)
    assert caught.value.commit_acknowledged and caught.value.receipt is d.seen_receipt
    assert d.durable and "rollback" not in d.events and not d.terminated


@pytest.mark.asyncio
async def test_cleanup_has_no_new_grace_budget(rig, monkeypatch):
    _, _, d, _ = rig
    monkeypatch.setattr(attempt, "OPERATION_S", 0.08)
    d.delay = {"hold": 0.03, "rollback": 1}
    d.fail = "hold"
    start = asyncio.get_running_loop().time()
    with pytest.raises(AttemptReservationUnavailable) as caught:
        await reserve(rig)
    assert asyncio.get_running_loop().time() - start < 0.3
    assert not caught.value.rollback_acknowledged
    assert not d.durable and d.terminated


@pytest.mark.asyncio
async def test_suppressed_deadline_cancellation_cannot_return_success(rig, monkeypatch):
    service, _, d, _ = rig
    monkeypatch.setattr(attempt, "OPERATION_S", 0.04)

    async def suppress_in_release(conn, **kw):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        d.released = True

    service._pool.release.side_effect = suppress_in_release
    with pytest.raises(ReservationCommittedResultUnavailable) as caught:
        await reserve(rig)
    assert caught.value.commit_acknowledged and d.released
    assert not d.terminated  # release completed; connection must not be reclaimed


@pytest.mark.asyncio
async def test_pinned_driver_detached_proxy_never_terminates_new_physical_lease():
    # Real installed proxy class, synthetic holder/physical connection: no DB.
    physical = MagicMock(spec=asyncpg.Connection)
    proxy = asyncpg.pool.PoolConnectionProxy(MagicMock(), physical)
    proxy._con = None  # asyncpg's exact proxy invalidation on successful release
    state = attempt._AttemptTransaction(MagicMock(spec=asyncpg.Pool), None, None, False)
    state.conn = proxy
    state.terminate()
    physical.terminate.assert_not_called()
    assert not state.termination_failed and state.conn is None


@pytest.mark.asyncio
async def test_pinned_driver_owned_attached_proxy_terminates_exactly_once():
    class Physical:
        def __init__(self):
            self.calls = 0

        def _set_proxy(self, proxy):
            pass

        def terminate(self):
            self.calls += 1

    physical = Physical()
    # Concrete synthetic duck type avoids MagicMock's misleading __class__.
    proxy = asyncpg.pool.PoolConnectionProxy(MagicMock(), cast(asyncpg.Connection, physical))
    state = attempt._AttemptTransaction(MagicMock(spec=asyncpg.Pool), None, None, False)
    state.conn = proxy
    state.terminate()
    state.terminate()
    assert physical.calls == 1
    assert state.conn is None and not state.termination_failed


def test_attempt_outcome_errors_are_exported():
    from orchestrator.entitlements import errors

    assert "AttemptReservationUnavailable" in errors.__all__
    assert "ReservationCommittedResultUnavailable" in errors.__all__


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
async def test_release_ownership_prevents_old_reset_from_reclaiming_new_lease(interrupt):
    # Installed native holder/proxy/release, synthetic physical connections only.
    entered, finish = asyncio.Event(), asyncio.Event()
    pool = MagicMock(spec=asyncpg.Pool)
    pool._loop = asyncio.get_running_loop()
    pool._generation = 0
    pool._queue = asyncio.Queue()
    holder = asyncpg.pool.PoolConnectionHolder(
        pool, max_queries=50000, setup=None, max_inactive_time=0
    )

    class Physical:
        def __init__(self):
            self.closed = False
            self.termination_calls = 0
            self._protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)

        def _set_proxy(self, proxy):
            pass

        def _on_release(self):
            pass

        def is_closed(self):
            return self.closed

        def terminate(self):
            self.termination_calls += 1
            self.closed = True
            holder._release_on_close()

        async def _reset(self):
            pass

    old, new = Physical(), Physical()
    pool._get_new_connection = AsyncMock(return_value=old)
    proxy = await holder.acquire()
    inner = []

    async def delayed_reset(con):
        assert con is old
        inner.append(asyncio.current_task())
        entered.set()
        await finish.wait()
        raise RuntimeError("synthetic old reset failure")

    pool._reset = delayed_reset

    async def native_release(conn, *, timeout):
        await asyncpg.Pool.release(pool, conn, timeout=timeout)

    pool.release = AsyncMock(side_effect=native_release)
    state = attempt._AttemptTransaction(pool, None, None, False)
    state.conn = proxy

    async def releasing():
        async with asyncio.timeout(0.03 if interrupt == "timeout" else 1):
            await state.release()

    task = asyncio.create_task(releasing())
    try:
        async with asyncio.timeout(1):
            await entered.wait()
        if interrupt == "cancel":
            task.cancel("external release cancellation")
        with pytest.raises(
            asyncio.CancelledError if interrupt == "cancel" else TimeoutError
        ) as caught:
            await task
        await state.cleanup(caught.value)
        # Old reset still owns holder; no next borrower can obtain it yet.
        assert pool._queue.empty()
        assert proxy._con is old and old.termination_calls == 0
        assert state.conn is None
    finally:
        finish.set()
        outcomes = await asyncio.gather(*inner, return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)
    assert len(outcomes) == 1 and isinstance(outcomes[0], RuntimeError)
    assert pool._queue.get_nowait() is holder
    pool._get_new_connection = AsyncMock(return_value=new)
    new_proxy = await holder.acquire()
    assert old.termination_calls == 1
    assert new_proxy._con is new and new.termination_calls == 0
    assert proxy._con is None


@pytest.mark.asyncio
async def test_termination_failure_is_retained_without_masking_original_cancel(rig, monkeypatch):
    _, _, d, _ = rig
    d.cancel = "hold"

    def fail_terminate():
        raise RuntimeError("synthetic terminate failure")

    monkeypatch.setattr(d, "terminate", fail_terminate)
    with pytest.raises(asyncio.CancelledError) as caught:
        await reserve(rig)
    assert caught.value is d.cancellation
    assert getattr(caught.value, "reservation_termination_failed")
    assert not d.durable


@pytest.mark.asyncio
async def test_ordinary_reserve_keeps_its_denial_analytics_wrapper(rig, monkeypatch):
    service, identity, _, _ = rig
    denial = BudgetExceeded(requested=10, spent=0, reserved=0, ceiling=1)
    monkeypatch.setattr(service, "_reserve", AsyncMock(side_effect=denial))
    with pytest.raises(BudgetExceeded) as caught:
        await service.reserve(identity.account_id, 10, operation="chat")
    assert caught.value is denial
    service._record_encounter.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["lookup", "release"])
async def test_recovery_cancellation_preserves_original_receipt_and_acknowledgement(rig, phase):
    service, _, d, _ = rig
    await reserve(rig)
    receipt = d.seen_receipt
    d.cancel = phase
    with pytest.raises(asyncio.CancelledError) as caught:
        await service.recover_attempt_reservation(receipt, commit_acknowledged=True)
    assert caught.value is d.cancellation
    assert getattr(caught.value, "reservation_receipt") is receipt
    assert getattr(caught.value, "reservation_commit_acknowledged") is True
    assert getattr(caught.value, "reservation_outcome") == "committed"
