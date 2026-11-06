"""Packet ownership around real H/native phases, using test-only bindings.

This fixture deliberately retains native objects out of band. It does not define
a production request/result codec, issuer, adapter, listener or memory budget.
Synthetic owner SQL readback is an oracle, never application retry authority.
"""

import asyncio
from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.account_control.transaction import TransactionUnavailable
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.packet_owner import (
    AssignmentAuthorization,
    AuthenticatedWorker,
    Budgets,
    Established,
    PacketOwner,
    PreparedPacket,
)
from orchestrator.services.restore_history.publication_protocol import ProtocolError, Request
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


class ControlledFixtureOutcome(Exception):
    """Test-only refusal to invent a wire mapping for a known control result."""

    def __init__(self, status):
        super().__init__(status.value)
        self.status = status


class NativeFixture:
    """Fixed test bridge; body/result bytes are synthetic, not a wire contract."""

    def __init__(self, s, authorization):
        self.s, self.authorization = s, authorization
        self.calls = []
        self.packet = None

    @property
    def blocked(self):
        return self.s.effects._blocked or self.s.history._blocked

    def check(self, authorization, packet):
        assert authorization is self.authorization and packet is self.packet
        assert packet.command == json.dumps(self.s.command.wire(), sort_keys=True).encode()
        assert packet.content == q.r.p.r.CONTENT

    async def prepare(self, assignment, body, *, deadline):
        assert assignment is self.authorization and body == b"fixture"
        self.calls.append("prepare")
        s = self.s
        command, content = await s.effects.prepare_output(
            s.publisher,
            s.attempt,
            s.command.job,
            0,
            q.p.ROWS,
            "synthetic.csv",
            deadline=deadline,
        )
        s.command, s.prepared_content = command, content
        self.packet = PreparedPacket(
            assignment.binding, json.dumps(command.wire(), sort_keys=True).encode(), content
        )
        return self.packet, Established(b"prepare")

    async def reserve(self, assignment, packet, *, deadline):
        self.check(assignment, packet)
        self.calls.append("reserve")
        s = self.s
        await s.effects.reserve_output(
            s.publisher, s.r0, s.r0_pool, s.attempt, s.command, deadline=deadline
        )
        return Established(b"reserve")

    async def catalog(self, assignment, packet, *, deadline):
        self.check(assignment, packet)
        self.calls.append("catalog")
        s = self.s
        publication = await s.effects.publish_reserved_output(
            s.publisher, s.attempt, s.command.binding.intent_id, packet.content, deadline=deadline
        )
        assert publication is not None
        return Established(b"catalog")

    async def present(self, assignment, packet, *, deadline):
        self.check(assignment, packet)
        self.calls.append("present")
        s = self.s
        await s.effects.present_reserved_output(
            s.publisher, s.attempt, s.command.binding.intent_id, deadline=deadline
        )
        return Established(b"present")

    async def terminal(self, assignment, packet, *, deadline):
        self.check(assignment, packet)
        self.calls.append("terminal")
        s = self.s
        result = await s.effects.complete(
            s.claim.task_id,
            s.claim.epoch,
            binding=s.claim,
            content="synthetic final",
            deadline=deadline,
        )
        if result is not TaskStatus.COMPLETED:
            raise ControlledFixtureOutcome(result)
        return Established(b"terminal")


@pytest_asyncio.fixture
async def packet_native(phase_owner):
    s = phase_owner
    session, worker, handle = uuid4(), uuid4(), uuid4()
    # This fixture hash is not a selected production binding or authorization.
    binding = hashlib.sha256(
        b"test-only-native-facts:"
        + s.attempt.wire().encode()
        + json.dumps(s.command.job.wire(), sort_keys=True).encode()
    ).digest()
    grant = AssignmentAuthorization(
        session,
        UUID(str(s.command.job.job_id)),
        worker,
        UUID(str(s.command.job.operation_id)),
        binding,
        time.monotonic() + 60,
    )
    bridge = NativeFixture(s, grant)
    owner = PacketOwner(session, bridge, Budgets(2, 1, 1, 8192, 8192, 60.0, 2.0))
    owner.admit(grant, handle)
    try:
        yield SimpleNamespace(
            s=s,
            bridge=bridge,
            owner=owner,
            grant=grant,
            handle=handle,
            peer=AuthenticatedWorker(worker),
        )
    finally:
        await owner.aclose()


def request(b, name):
    return Request(
        b.owner.session,
        b.handle,
        b.grant.operation_id,
        name,
        b"fixture" if name == "prepare" else b"",
    )


async def call(b, name, *, deadline=None):
    return await b.owner.handle(b.peer, request(b, name), deadline=deadline or time.monotonic() + 2)


async def before(b, name):
    for previous in PHASES[: PHASES.index(name)]:
        assert (await call(b, previous)).status == "established"


async def refuses_later_effects(b, saved):
    calls = b.bridge.calls.copy()
    assert (await call(b, "observe")).status == "needs_attention"
    for phase in PHASES:
        with pytest.raises(ProtocolError, match="blocked"):
            await call(b, phase)
    assert q.state(b.s) == saved and b.bridge.calls == calls


@pytest.mark.asyncio
async def test_established_duplicate_replies_do_not_repeat_any_native_phase(packet_native):
    b, s = packet_native, packet_native.s
    retained = None
    for phase in PHASES:
        reply = await call(b, phase)
        assert reply.status == "established" and reply.result == phase.encode()
        saved = q.state(s)
        assert await call(b, phase) == reply
        assert q.state(s) == saved
        if retained is None:
            retained = b.bridge.packet
        assert b.bridge.packet is retained
        assert len(retained.command) + len(retained.content) <= 8192
        assert b.bridge.calls == list(PHASES[: PHASES.index(phase) + 1])
        assert available(s.path, "OP_LOCK") and not b.bridge.blocked
    reopened = await s.worker.task_for_key(s.account, "stable-client-key")
    assert reopened.task_id == s.claim.task_id and reopened.status is TaskStatus.COMPLETED
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0),
        s.command.binding.resource_id,
        s.command.binding.version_id,
    )
    assert retained is not None and loaded.content == retained.content == q.r.p.r.CONTENT
    assert q.r.counts(s) == "1:1" and q.r.p.r.rows(s) == "1:1:1:1"
    assert (await call(b, "observe")).result == b"terminal"


@pytest.mark.asyncio
async def test_wrong_peer_handle_or_operation_refuses_before_native_io(packet_native):
    b = packet_native
    saved = q.state(b.s)
    for peer, req in (
        (AuthenticatedWorker(uuid4()), request(b, "prepare")),
        (b.peer, replace(request(b, "prepare"), request_id=uuid4())),
        (b.peer, replace(request(b, "prepare"), handle=uuid4())),
        (b.peer, replace(request(b, "prepare"), session=uuid4())),
    ):
        with pytest.raises(ProtocolError, match="assignment_refused"):
            await b.owner.handle(peer, req, deadline=time.monotonic() + 2)
    assert not b.bridge.calls and q.state(b.s) == saved
    assert not b.owner._blocked and not b.bridge.blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES[1:])
@pytest.mark.parametrize("damage", ["stop", "projection", "generation"])
async def test_original_native_fences_still_apply_between_packet_phases(
    packet_native, phase, damage
):
    b, s = packet_native, packet_native.s
    await before(b, phase)
    retained = b.bridge.packet
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
            "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
            "DELETE FROM task_history_projection;"
            "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
        )
    saved = q.state(s)
    if damage == "generation" and phase == "terminal":
        with pytest.raises(ControlledFixtureOutcome) as caught:
            await call(b, phase)
        assert caught.value.status is TaskStatus.FAILED
        current = q.state(s)
        assert current[:2] == saved[:2] and current[3] == saved[3]
        assert s.db.sql("SELECT status||':'||terminal_code FROM tasks") == (
            "failed:task_generation_stale"
        )
        async with s.db.connection() as conn:
            message = await conn.fetchrow(
                "SELECT content,metadata FROM messages WHERE id=$1", s.claim.result_message_id
            )
        assert s.tasks._enc.decrypt(message["content"]) != "synthetic final"
        assert json.loads(message["metadata"])["terminal_reason"] == "task_generation_stale"
        assert not b.bridge.blocked  # known control-only commit, not unknown native COMMIT
        saved = current
    else:
        with pytest.raises((HistoryError, asyncpg.PostgresError)):
            await call(b, phase)
        assert b.bridge.blocked
    assert b.bridge.packet is retained and b.owner._blocked
    assert phase not in b.owner._entries[b.handle].results
    assert available(s.path, "OP_LOCK")
    await refuses_later_effects(b, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_lost_native_commit_ack_cannot_be_retried_by_packet_owner(
    packet_native, monkeypatch, phase, fault
):
    b, s = packet_native, packet_native.s
    await before(b, phase)
    retained, command = b.bridge.packet, s.command.wire()
    role = "reserved_r" if phase == "reserve" else "md_app" if phase == "terminal" else "reserved_p"
    s.db.bridge.commit_fault_role = role
    native = Transaction.commit
    fired = 0

    async def commit(tx):
        nonlocal fired
        if tx._connection._params.user == role:
            fired += 1
            s.db.bridge.commit_fault = fault
        try:
            return await native(tx)
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
    assert b.owner._blocked and b.bridge.blocked and available(s.path, "OP_LOCK")
    assert phase not in b.owner._entries[b.handle].results
    assert b.bridge.packet is retained and s.command.wire() == command
    committed = max(0, PHASES.index(phase) + int(fault == "after") - 1)
    assert q.r.counts(s) == ("1:1" if committed else "0:0")
    assert q.r.p.r.rows(s) == (
        "1:1:1:1" if committed >= 3 else "1:1:1:0" if committed == 2 else "0:0:0:0"
    )
    assert s.db.sql("SELECT status FROM tasks") == ("completed" if committed == 4 else "running")
    await refuses_later_effects(b, q.state(s))
    assert fired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_native_reset_stays_owned_without_late_packet_success(
    packet_native, monkeypatch, phase, mode
):
    b, s = packet_native, packet_native.s
    await before(b, phase)
    pool = (
        s.r0_pool
        if phase == "reserve"
        else s.worker_pool
        if phase == "terminal"
        else s.publisher._pool
    )
    target = 2 if phase == "terminal" else 1
    async with held_reset(pool, monkeypatch, target, mode == "failure") as (entered, release):
        task = asyncio.create_task(
            call(b, phase, deadline=time.monotonic() + (1 if mode == "deadline" else 2))
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original packet cleanup cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeated packet cleanup cancellation")
            elif mode == "deadline":
                async with asyncio.timeout(3):
                    while not b.owner._blocked:
                        await asyncio.sleep(0.01)
            await asyncio.sleep(0.03)
            assert not task.done() and b.owner._inflight == 1 and b.owner.held_bytes == 8192
            assert not available(s.path, "OP_LOCK") and not available(s.path, "RUNTIME_LOCK")
            assert (await call(b, "observe")).status == "pending"
            saved = q.state(s)
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
                assert caught.value.args == ("original packet cleanup cancellation",)
                # Diagnostic boundary: the generic owner does not cancel the
                # driver. Known native cleanup can finish with H still healthy;
                # only this packet owner latches the abandoned caller. A real
                # adapter/host must define interest propagation before wiring.
                assert not b.bridge.blocked
            else:
                assert b.bridge.blocked
            assert q.state(s) == saved and b.owner._inflight == 0 and b.owner._blocked
            assert phase not in b.owner._entries[b.handle].results
            assert available(s.path, "OP_LOCK")
            await refuses_later_effects(b, saved)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_restart_refuses_old_native_assignment_without_repreparing(packet_native):
    b, s = packet_native, packet_native.s
    await call(b, "prepare")
    retained, saved = b.bridge.packet, q.state(s)
    await b.owner.aclose()
    fresh = PacketOwner(uuid4(), b.bridge, b.owner.budgets)
    try:
        with pytest.raises(ProtocolError, match="assignment_refused"):
            fresh.admit(b.grant, uuid4())
        with pytest.raises(ProtocolError, match="assignment_refused"):
            await fresh.handle(
                b.peer,
                replace(request(b, "prepare"), session=fresh.session, request_id=uuid4()),
                deadline=time.monotonic() + 2,
            )
        assert b.bridge.packet is retained and b.bridge.calls == ["prepare"]
        assert q.state(s) == saved
    finally:
        await fresh.aclose()
