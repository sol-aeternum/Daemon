"""Real H/native profile phases under one packet owner; no installed runtime."""

import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.resource_store import Publication
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.account_control.transaction import TransactionUnavailable
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.native_publication import (
    NativeContext,
    NativePreparation,
    NativeProfile,
    NativeRequest,
    NativeTerminal,
    native_binding,
)
from orchestrator.services.restore_history.packet_owner import (
    AssignmentAuthorization,
    AuthenticatedWorker,
    NativeBudgets,
    PacketOwner,
)
from orchestrator.services.restore_history.publication_protocol import ProtocolError
from orchestrator.tasks.states import TaskStatus
from tests import test_history_output_phases_postgres as q
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_history_coordination import available

metadata_cluster = q.metadata_cluster
db = q.db
projection_db = q.projection_db
scenario = q.scenario
effects = q.effects
reserved = q.reserved
presenter = q.presenter
reservation_owner = q.reservation_owner
preparer = q.preparer
phase_owner = q.phase_owner
PHASES = ("prepare", "reserve", "catalog", "present", "terminal")


@pytest_asyncio.fixture
async def native(phase_owner):
    s = phase_owner
    session, worker, assignment, handle = [uuid4() for _ in range(4)]
    binding = native_binding(s.attempt, s.command.job, s.claim, 0, worker, session, assignment)
    auth = AssignmentAuthorization(
        session, assignment, worker, s.command.job.operation_id, binding, time.monotonic() + 60
    )
    context = NativeContext(auth, s.attempt, s.command.job, s.claim, 0)
    profile = NativeProfile(s.effects, s.publisher, s.r0, s.r0_pool)
    budgets = NativeBudgets(4, 2, 1, 8650752, 17301504, 60.0, 2.0, 8388608, 262144, 100000, 1048576)
    owner = PacketOwner(session, profile, budgets)
    owner.admit_native(context, handle)
    try:
        yield SimpleNamespace(
            s=s,
            owner=owner,
            profile=profile,
            context=context,
            peer=AuthenticatedWorker(worker),
            handle=handle,
        )
    finally:
        await owner.aclose()


def request(b, phase):
    payload = (
        NativePreparation(tuple(tuple(row) for row in q.p.ROWS), "synthetic.csv")
        if phase == "prepare"
        else NativeTerminal("synthetic final")
        if phase == "terminal"
        else None
    )
    return NativeRequest(
        b.owner.session, b.handle, b.context.authorization.operation_id, phase, payload
    )


async def call(b, phase, *, deadline=None):
    return await b.owner.handle_native(
        b.peer, request(b, phase), deadline=deadline or time.monotonic() + 2
    )


async def before(b, phase):
    for previous in PHASES[: PHASES.index(phase)]:
        assert (await call(b, previous)).status == "established"


async def blocked(b, saved):
    assert (await call(b, "observe")).status == "needs_attention"
    for phase in PHASES:
        with pytest.raises(ProtocolError, match="blocked"):
            await call(b, phase)
    assert q.state(b.s) == saved


@pytest.mark.asyncio
async def test_real_native_profile_retains_one_packet_and_reopens_original_results(native):
    b, s = native, native.s
    retained = None
    for phase in PHASES:
        result = await call(b, phase)
        assert result.status == "established" and result.result.phase == phase
        saved = q.state(s)
        assert await call(b, phase) == result and q.state(s) == saved
        packet = b.owner._entries[b.handle].packet
        if retained is None:
            retained = packet
        assert packet is retained
        if phase in ("prepare", "reserve"):
            assert result.result.value is None
        if phase == "catalog":
            assert type(result.result.value) is Publication
        if phase == "terminal":
            assert result.result.value is TaskStatus.COMPLETED
        assert available(s.path, "OP_LOCK") and not b.owner._blocked and not b.profile.blocked
    reopened = await s.worker.task_for_key(s.account, "stable-client-key")
    assert reopened.task_id == s.claim.task_id and reopened.status is TaskStatus.COMPLETED
    assert retained is not None
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0),
        retained.command.binding.resource_id,
        retained.command.binding.version_id,
    )
    assert loaded.content == retained.content == q.r.p.r.CONTENT
    assert q.r.counts(s) == "1:1" and q.r.p.r.rows(s) == "1:1:1:1"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES[1:])
@pytest.mark.parametrize("damage", ["stop", "projection", "generation"])
async def test_native_profile_keeps_fences_and_truthful_known_control_status(native, phase, damage):
    b, s = native, native.s
    await before(b, phase)
    packet = b.owner._entries[b.handle].packet
    if damage == "stop":
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=time.monotonic() + 2
            )
        ).accepted
    elif damage == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=1")
    else:
        s.db.sql(
            "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;DELETE FROM task_history_projection;ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
        )
    saved = q.state(s)
    if damage == "generation" and phase == "terminal":
        reply = await call(b, phase)
        assert reply.status == "established" and reply.result.value is TaskStatus.FAILED
        assert not b.owner._blocked and not b.profile.blocked
        assert (
            s.db.sql("SELECT status||':'||terminal_code FROM tasks")
            == "failed:task_generation_stale"
        )
        assert await call(b, phase) == reply
        assert (await call(b, "observe")).result == reply.result
        current = q.state(s)
        assert current[:2] == saved[:2] and current[3] == saved[3]
        async with s.db.connection() as conn:
            msg = await conn.fetchrow(
                "SELECT content,metadata FROM messages WHERE id=$1", s.claim.result_message_id
            )
        assert s.tasks._enc.decrypt(msg["content"]) != "synthetic final"
        assert json.loads(msg["metadata"])["terminal_reason"] == "task_generation_stale"
    else:
        with pytest.raises((HistoryError, asyncpg.PostgresError)):
            await call(b, phase)
        assert b.owner._blocked and b.profile.blocked
        await blocked(b, saved)
    assert b.owner._entries[b.handle].packet is packet and available(s.path, "OP_LOCK")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_native_profile_unknown_commit_never_establishes_or_retries(
    native, monkeypatch, phase, fault
):
    b, s = native, native.s
    await before(b, phase)
    retained = b.owner._entries[b.handle].packet
    role = "reserved_r" if phase == "reserve" else "md_app" if phase == "terminal" else "reserved_p"
    s.db.bridge.commit_fault_role = role
    original = Transaction.commit
    fired = 0

    async def commit(tx):
        nonlocal fired
        if tx._connection._params.user == role:
            fired += 1
            s.db.bridge.commit_fault = fault
        try:
            return await original(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(Transaction, "commit", commit)
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError, TransactionUnavailable)):
            await call(b, phase)
    finally:
        s.db.bridge.commit_fault = None
        s.db.bridge.commit_fault_role = "reserved_p"
    assert fired == 1 and s.db.bridge.commit_seen.is_set()
    assert b.owner._entries[b.handle].packet is retained
    assert phase not in b.owner._entries[b.handle].results
    assert b.owner._blocked and b.profile.blocked and available(s.path, "OP_LOCK")
    committed = max(0, PHASES.index(phase) + int(fault == "after") - 1)
    assert q.r.counts(s) == ("1:1" if committed else "0:0")
    assert q.r.p.r.rows(s) == (
        "1:1:1:1" if committed >= 3 else "1:1:1:0" if committed == 2 else "0:0:0:0"
    )
    assert s.db.sql("SELECT status FROM tasks") == ("completed" if committed == 4 else "running")
    await blocked(b, q.state(s))
    assert fired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_native_profile_drains_actual_reset_without_late_success(
    native, monkeypatch, phase, mode
):
    b, s = native, native.s
    await before(b, phase)
    pool = (
        s.r0_pool
        if phase == "reserve"
        else s.worker_pool
        if phase == "terminal"
        else s.publisher._pool
    )
    async with held_reset(
        pool, monkeypatch, 2 if phase == "terminal" else 1, mode == "failure"
    ) as (entered, release):
        task = asyncio.create_task(
            call(b, phase, deadline=time.monotonic() + (1 if mode == "deadline" else 2))
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original native-profile cancellation")
                await asyncio.sleep(0.03)
                task.cancel("again")
            elif mode == "deadline":
                async with asyncio.timeout(3):
                    while not b.owner._blocked:
                        await asyncio.sleep(0.01)
            await asyncio.sleep(0.03)
            assert not task.done() and b.owner._inflight == 1
            assert b.owner.held_bytes == b.owner.budgets.packet_bytes
            assert not available(s.path, "OP_LOCK") and not available(s.path, "RUNTIME_LOCK")
            saved = q.state(s)
            assert (await call(b, "observe")).status == "pending"
            release.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TimeoutError
                if mode == "deadline"
                else TransactionUnavailable
                if phase == "terminal"
                else RuntimeError
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert (
                    caught.value.args == ("original native-profile cancellation",)
                    and not b.profile.blocked
                )
            else:
                assert b.profile.blocked
            assert b.owner._blocked and b.owner._inflight == 0 and available(s.path, "OP_LOCK")
            assert phase not in b.owner._entries[b.handle].results
            await blocked(b, saved)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_profile_shutdown_drains_before_dropping_context_and_packet(
    native, monkeypatch
):
    b, s = native, native.s
    await before(b, "catalog")
    async with held_reset(s.publisher._pool, monkeypatch, 1, False) as (entered, release):
        task = asyncio.create_task(call(b, "catalog"))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            b.owner.close_admission()
            closer = asyncio.create_task(b.owner.aclose())
            await asyncio.sleep(0.03)
            closer.cancel("shutdown cancellation")
            await asyncio.sleep(0.03)
            assert not closer.done() and b.owner._entries[b.handle].context is b.context
            assert not available(s.path, "OP_LOCK") and b.owner.held_bytes > 0
            release.set()
            with pytest.raises(ProtocolError, match="uncertain"):
                await task
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert b.owner._entries[b.handle].context is None and b.owner.held_bytes == 0
            assert not b.owner._entries[b.handle].results and available(s.path, "OP_LOCK")
        finally:
            release.set()
            await asyncio.gather(*([task, closer] if closer else [task]), return_exceptions=True)
