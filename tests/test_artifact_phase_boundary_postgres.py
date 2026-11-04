"""Synthetic cross-phase evidence, including the still-uncomposed R0 boundary.

No host, service identity, transport or live restore is installed. The projection
loss probe intentionally removes only synthetic SQL evidence; it is not a full
backup/restore rehearsal or a claim that native R0 itself promises H protection.
"""

import json

import asyncpg
import pytest

from orchestrator.artifact_reservations import OutputReservations, ReservationError
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.tasks.states import TaskStatus
from tests.restore_history_support import KEY
from tests import test_reserved_presentation_postgres as p

metadata_cluster = p.metadata_cluster
db = p.db
projection_db = p.projection_db
scenario = p.scenario
effects = p.effects
reserved = p.reserved
presenter = p.presenter


def reservations(s):
    return s.db.sql(
        "SELECT (SELECT count(*) FROM artifact_output_jobs)||':'||"
        "(SELECT count(*) FROM artifact_output_intents)"
    )


async def terminal(s, content="synthetic final"):
    return await s.effects.complete(
        s.claim.task_id,
        s.claim.epoch,
        binding=s.claim,
        content=content,
        deadline=p.r.deadline(),
    )


@pytest.mark.asyncio
async def test_known_catalog_card_and_terminal_commits_reopen_the_original_result(presenter):
    s = presenter
    publication = await p.r.publish(s)
    card = await p.present(s)
    assert await terminal(s) is TaskStatus.COMPLETED

    # This is trusted internal readback, not an authenticated HTTP/device probe.
    reopened = await s.worker.task_for_key(s.account, "stable-client-key")
    assert reopened is not None
    assert reopened.task_id == s.claim.task_id and reopened.status is TaskStatus.COMPLETED
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0), publication.resource_id, publication.version_id
    )
    assert loaded.content == p.r.CONTENT and loaded.publication == publication
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
    assert envelope["envelope"]["items"] == p.pins(s)
    assert reservations(s) == "1:1" and p.r.rows(s) == "1:1:1:1"
    assert len(p.r.entries(s)) == 1  # Still no independent artifact history.


@pytest.mark.asyncio
async def test_stop_after_card_preserves_pins_but_refuses_late_terminal_output(presenter):
    s = presenter
    publication = await p.r.publish(s)
    card = await p.present(s)
    assert (
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=p.r.deadline())
    ).accepted
    with pytest.raises(HistoryError, match="historical_stop"):
        await terminal(s, "must not become the final answer")
    assert p.count(s) == "1" and p.r.rows(s) == "1:1:1:1"
    async with s.db.connection() as conn:
        assert (
            await conn.fetchval("SELECT status FROM tasks WHERE id=$1", s.claim.task_id)
            != "completed"
        )
        assert await conn.fetchval("SELECT presentation_id FROM resource_presentations") == card
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0), publication.resource_id, publication.version_id
    )
    assert loaded.content == p.r.CONTENT


@pytest.mark.asyncio
async def test_uncomposed_r0_can_commit_despite_missing_h_sql_projection(presenter):
    s = presenter
    s.db.sql(
        "DELETE FROM artifact_output_jobs;"
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
        "DELETE FROM task_history_projection;"
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable;"
    )
    assert reservations(s) == "0:0" and len(p.r.entries(s)) == 1
    r0 = OutputReservations(s.publisher._descriptors)
    async with s.db.connection("reserved_r") as conn, conn.transaction():
        await r0.register_output_job(conn, s.attempt, s.command.job)
        await r0.reserve_output_intent(conn, s.attempt, s.command)
    assert reservations(s) == "1:1"  # Diagnostic: native R0 has no H owner yet.
    with pytest.raises(HistoryError, match="projection_unavailable"):
        await p.r.publish(s)
    assert not p.r.files(s) and p.r.rows(s) == "0:0:0:0"


@pytest.mark.asyncio
async def test_native_stop_still_refuses_new_r0_reservation(presenter):
    s = presenter
    s.db.sql("DELETE FROM artifact_output_jobs")
    assert (
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=p.r.deadline())
    ).accepted
    r0 = OutputReservations(s.publisher._descriptors)
    with pytest.raises(ReservationError):
        async with s.db.connection("reserved_r") as conn, conn.transaction():
            await r0.register_output_job(conn, s.attempt, s.command.job)
            await r0.reserve_output_intent(conn, s.attempt, s.command)
    assert reservations(s) == "0:0" and not p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_uncomposed_r0_lost_commit_keeps_packet_but_does_not_block_h_owner(presenter, fault):
    s = presenter
    s.db.sql("DELETE FROM artifact_output_jobs")
    command = s.command
    packet = command.wire()
    r0 = OutputReservations(s.publisher._descriptors)
    s.db.bridge.commit_fault_role = "reserved_r"
    conn = await s.db.connect("reserved_r")
    try:
        tx = conn.transaction(isolation="read_committed")
        await tx.start()
        await r0.register_output_job(conn, s.attempt, command.job)
        await r0.reserve_output_intent(conn, s.attempt, command)
        s.db.bridge.commit_fault = fault
        with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)):
            await conn.execute("COMMIT;", timeout=0.3)
    finally:
        # Same owned uncertain-channel disposal as native R0's existing fault
        # test. Do not issue SQL/COMMIT again or infer a successful response.
        conn.terminate()
        s.db.bridge.commit_fault = None
        s.db.bridge.commit_fault_role = "reserved_p"
    assert s.db.bridge.commit_seen.is_set()
    assert reservations(s) == ("1:1" if fault == "after" else "0:0")
    assert s.command is command and command.wire() == packet
    # No writer replacement/retry or file effect is attempted here. These false
    # blocked bits document the missing join, not permission to use this owner.
    assert not s.effects._blocked and not s.history._blocked
    assert not p.r.files(s) and p.r.rows(s) == "0:0:0:0"
