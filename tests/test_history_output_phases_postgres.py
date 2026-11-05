"""Complete retained-command phases under H; no host or transport installation.

Each native transaction remains separate. Fault readback below is synthetic
owner evidence, never an application retry or uncertain-COMMIT promotion.
"""

import asyncio
import json

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.account_control.transaction import TransactionUnavailable
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.tasks.states import TaskStatus
from tests import test_history_output_reservation_postgres as r
from tests import test_history_output_preparation_postgres as p
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_history_coordination import available

metadata_cluster = r.metadata_cluster
db = r.db
projection_db = r.projection_db
scenario = r.scenario
effects = r.effects
reserved = r.reserved
presenter = r.presenter
reservation_owner = r.reservation_owner

preparer = p.preparer
PHASES = ("preparation", "reservation", "catalog", "presentation", "terminal")


@pytest_asyncio.fixture
async def phase_owner(preparer):
    s = preparer
    # The base fixture's valid arguments also probe every blocked entrypoint
    # after a failed preparation. Only an actual successful prepare replaces
    # the packet and bytes; this fixture supplies no runtime fallback/adoption.
    s.prepared_content = r.p.r.CONTENT
    s.preparation_returned = False
    yield s


async def phase(s, name):
    if name == "preparation":
        command, content = await p.prepare(s)
        assert content == r.p.r.CONTENT
        s.command, s.prepared_content = command, content
        s.preparation_returned = True
        return None
    if name == "reservation":
        return await r.reserve(s)
    if name == "catalog":
        return await r.p.r.publish(s, content=s.prepared_content)
    if name == "presentation":
        return await r.p.present(s)
    assert name == "terminal"
    return await r.terminal(s)


async def before(s, name):
    for previous in PHASES[: PHASES.index(name)]:
        await phase(s, previous)


def state(s):
    return (
        r.counts(s),
        r.p.r.rows(s),
        s.db.sql("SELECT status FROM tasks"),
        tuple(r.p.r.files(s)),
    )


@pytest.mark.asyncio
async def test_prepared_packet_crosses_five_phases_and_reopens_original_results(phase_owner):
    s = phase_owner
    assert await phase(s, "preparation") is None
    assert s.preparation_returned
    assert r.counts(s) == "0:0" and r.p.r.rows(s) == "0:0:0:0"
    assert not r.p.r.files(s)
    packet = s.command.wire()
    assert await phase(s, "reservation") is None
    publication = await phase(s, "catalog")
    assert publication is not None
    card = await phase(s, "presentation")
    assert await phase(s, "terminal") is TaskStatus.COMPLETED
    assert s.command.wire() == packet and r.counts(s) == "1:1"
    reopened = await s.worker.task_for_key(s.account, "stable-client-key")
    assert reopened is not None
    assert reopened.task_id == s.claim.task_id and reopened.status is TaskStatus.COMPLETED
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0), publication.resource_id, publication.version_id
    )
    assert loaded.content == r.p.r.CONTENT and loaded.publication == publication
    async with s.db.connection() as conn:
        message = await conn.fetchrow(
            "SELECT content,metadata FROM messages WHERE id=$1", s.claim.result_message_id
        )
        row = await conn.fetchrow(
            "SELECT * FROM resource_presentations WHERE presentation_id=$1", card
        )
    assert message is not None and row is not None
    assert s.tasks._enc.decrypt(message["content"]) == "synthetic final"
    assert json.loads(message["metadata"])["terminal_status"] == "complete"
    envelope = s.publisher._presentation_open(row, CapturedGeneration(s.account, 0), item_limit=10)
    assert envelope["envelope"]["items"] == r.p.pins(s)
    assert r.p.r.rows(s) == "1:1:1:1" and len(r.p.r.entries(s)) == 1
    assert available(s.path, "OP_LOCK") and not s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("next_phase", PHASES[1:])
async def test_h_stop_between_commits_preserves_prior_phase_without_late_output(
    phase_owner, next_phase
):
    s = phase_owner
    await before(s, next_phase)
    saved = state(s)
    packet = s.command.wire()
    assert (
        await s.effects.request_cancel(
            s.account, s.claim.task_id, key=KEY, deadline=r.p.r.deadline()
        )
    ).accepted
    with pytest.raises(HistoryError, match="historical_stop"):
        await phase(s, next_phase)
    assert state(s) == saved and s.command.wire() == packet
    assert len(r.p.r.entries(s)) == 2 and s.effects._blocked
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT cancel_requested_at IS NOT NULL FROM tasks")
        content = await conn.fetchval(
            "SELECT content FROM messages WHERE id=$1", s.claim.result_message_id
        )
    assert s.tasks._enc.decrypt(content) != "synthetic final"


@pytest.mark.asyncio
@pytest.mark.parametrize("next_phase", PHASES[1:])
async def test_projection_loss_between_phases_refuses_without_adoption_or_new_effects(
    phase_owner, next_phase
):
    s = phase_owner
    await before(s, next_phase)
    saved = state(s)
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
        "DELETE FROM task_history_projection;"
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
    )
    assert len(r.p.r.entries(s)) == 1
    with pytest.raises(HistoryError, match="projection_unavailable"):
        await phase(s, next_phase)
    assert state(s) == saved and s.effects._blocked and s.history._blocked
    assert available(s.path, "OP_LOCK")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", PHASES)
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_unknown_phase_commit_never_runs_later_phases_or_replaces_packet(
    phase_owner, monkeypatch, name, fault
):
    s = phase_owner
    await before(s, name)
    packet = s.command.wire()
    native = Transaction.commit
    role = (
        "reserved_r" if name == "reservation" else "md_app" if name == "terminal" else "reserved_p"
    )
    s.db.bridge.commit_fault_role = role
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
            await phase(s, name)
    finally:
        s.db.bridge.commit_fault = None
        s.db.bridge.commit_fault_role = "reserved_p"
    assert fired == 1 and s.db.bridge.commit_seen.is_set()
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    assert s.command.wire() == packet
    committed = max(0, PHASES.index(name) + int(fault == "after") - 1)
    assert r.counts(s) == ("1:1" if committed else "0:0")
    assert r.p.r.rows(s) == (
        "1:1:1:1" if committed >= 3 else "1:1:1:0" if committed == 2 else "0:0:0:0"
    )
    assert s.db.sql("SELECT status FROM tasks") == ("completed" if committed == 4 else "running")
    assert bool(r.p.r.files(s)) is (name in ("catalog", "presentation", "terminal"))
    assert s.preparation_returned is (name != "preparation")
    saved = state(s)
    # Refusal is tested on all entrypoints; none can advance a later phase,
    # retry this phase, replace the packet, or promote owner's diagnostic rows.
    for candidate in PHASES:
        with pytest.raises(HistoryError, match="blocked"):
            await phase(s, candidate)
    assert state(s) == saved and s.command.wire() == packet and fired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", PHASES)
@pytest.mark.parametrize("mode", ["cancel", "failure"])
async def test_phase_reset_failure_drains_under_h_and_never_advances(
    phase_owner, monkeypatch, name, mode
):
    s = phase_owner
    await before(s, name)
    pool = (
        s.r0_pool
        if name == "reservation"
        else s.worker_pool
        if name == "terminal"
        else s.publisher._pool
    )
    # The terminal phase first releases its protected-projection scan, then its
    # native terminal transaction. Hold the actual second holder reset there.
    target = 2 if name == "terminal" else 1
    packet = s.command.wire()
    async with held_reset(pool, monkeypatch, target, mode == "failure") as (entered, release):
        task = asyncio.create_task(phase(s, name))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original phase cleanup cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeated phase cleanup cancellation")
            await asyncio.sleep(0.03)
            assert not task.done() and not available(s.path, "OP_LOCK")
            assert not available(s.path, "RUNTIME_LOCK")
            saved = state(s)
            release.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TransactionUnavailable
                if name == "terminal"
                else RuntimeError
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original phase cleanup cancellation",)
            assert state(s) == saved and s.command.wire() == packet
            assert s.preparation_returned is (name != "preparation")
            assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
            for candidate in PHASES:
                with pytest.raises(HistoryError, match="blocked"):
                    await phase(s, candidate)
            assert state(s) == saved
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
