"""H-owned R0 join using distinct synthetic P/R0/W pools and real POSIX history."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import asyncpg
from asyncpg import connect_utils
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.artifact_reservations import (
    OutputReservations,
    ReservationError,
    ROLE_PRECONDITION,
)
from orchestrator.services.restore_history.model import HistoryError
from tests import test_reserved_presentation_postgres as p
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_artifact_phase_boundary_postgres import reservations as counts, terminal
from tests.test_history_coordination import available

metadata_cluster = p.metadata_cluster
db = p.db
projection_db = p.projection_db
scenario = p.scenario
effects = p.effects
reserved = p.reserved
presenter = p.presenter


@pytest_asyncio.fixture
async def reservation_owner(presenter):
    s = presenter
    # Retain the same exact sealed command, but let the new H join own its first
    # transaction instead of the fixture's deliberately uncomposed native setup.
    s.db.sql("DELETE FROM artifact_output_jobs")
    s.r0 = OutputReservations(s.publisher._descriptors)
    s.r0_pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user="reserved_r",
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=2,
        command_timeout=2,
    )
    try:
        yield s
    finally:
        try:
            async with asyncio.timeout(5):
                await s.r0_pool.close()
        finally:
            s.r0_pool.terminate()


async def reserve(s, *, command=None, identity=None, pool=None):
    return await s.effects.reserve_output(
        s.publisher,
        s.r0,
        pool or s.r0_pool,
        identity or s.attempt,
        command or s.command,
        deadline=p.r.deadline(),
    )


@pytest.mark.asyncio
async def test_known_reservation_commit_releases_p_before_r0_and_reuses_exact_packet(
    reservation_owner, monkeypatch
):
    s = reservation_owner
    original = s.r0.register_output_job
    seen = []
    packet = s.command.wire()

    async def register(conn, identity, job):
        assert s.publisher._active_task is None and s.publisher._connection is None
        assert not available(s.path, "OP_LOCK") and conn.is_in_transaction()
        assert await conn.fetchval("SELECT current_user") == "reserved_r"
        seen.append(job)
        return await original(conn, identity, job)

    monkeypatch.setattr(s.r0, "register_output_job", register)
    assert await reserve(s) is None and counts(s) == "1:1"
    assert await reserve(s) is None and counts(s) == "1:1"
    assert seen == [s.command.job, s.command.job] and s.command.wire() == packet
    assert not p.r.files(s) and p.r.rows(s) == "0:0:0:0"
    assert available(s.path, "OP_LOCK") and not s.effects._blocked
    await p.r.publish(s)
    await p.present(s)
    assert (await terminal(s)).value == "completed"
    assert p.r.rows(s) == "1:1:1:1"


@pytest.mark.asyncio
async def test_missing_protected_evidence_refuses_before_p_and_r0(reservation_owner, monkeypatch):
    s = reservation_owner
    s.db.sql(
        "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
        "DELETE FROM task_history_projection;"
        "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("reservation reached native operation before full H/SQL parity")

    monkeypatch.setattr(s.publisher, "_presentation_snapshot", forbidden)
    monkeypatch.setattr(s.r0, "register_output_job", forbidden)
    with pytest.raises(HistoryError, match="projection_unavailable"):
        await reserve(s)
    assert counts(s) == "0:0" and s.effects._blocked and s.history._blocked
    assert not p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["result_message", "conversation"])
async def test_locked_source_must_match_original_acceptance(reservation_owner, changed):
    s = reservation_owner
    conversation = s.claim.conversation_id if changed == "result_message" else uuid4()
    message = uuid4()
    async with s.db.connection() as conn, conn.transaction():
        if changed == "conversation":
            await conn.execute(
                "INSERT INTO conversations(id,user_id,pipeline,title) SELECT $1,user_id,pipeline,title FROM conversations WHERE id=$2",
                conversation,
                s.claim.conversation_id,
            )
        await conn.execute(
            "INSERT INTO messages(id,conversation_id,user_id,role,content) SELECT $1,$2,user_id,role,content FROM messages WHERE id=$3",
            message,
            conversation,
            s.claim.result_message_id,
        )
        await conn.execute(
            "UPDATE tasks SET conversation_id=$1,result_message_id=$2 WHERE id=$3",
            conversation,
            message,
            s.claim.task_id,
        )
    with pytest.raises(HistoryError, match="effects_origin_refused"):
        await reserve(s)
    assert counts(s) == "0:0" and not p.r.files(s) and s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "task", "lifecycle", "cipher", "attempt"])
async def test_forged_or_stale_command_never_reserves(reservation_owner, change):
    s = reservation_owner
    command, identity = s.command, s.attempt
    if change == "generation":
        identity = replace(identity, generation=1)
    elif change == "task":
        identity = replace(identity, task_id=uuid4())
    elif change == "lifecycle":
        identity = replace(identity, lifecycle_id=uuid4())
    elif change == "cipher":
        command = replace(command, descriptor_ciphertext="synthetic-invalid-token")
    else:
        command = s.publisher._descriptors.encode(
            command.job,
            replace(command.binding, attempt_id=uuid4()),
            filename="synthetic.csv",
            sha256=s.publisher._descriptors.decode(command)["sha256"],
        )
    with pytest.raises((HistoryError, ReservationError, asyncpg.PostgresError)):
        await reserve(s, command=command, identity=identity)
    assert counts(s) == "0:0" and not p.r.files(s)


@pytest.mark.asyncio
async def test_conflicting_ciphertext_blocks_instead_of_replacing_slot(reservation_owner):
    s = reservation_owner
    await reserve(s)
    command = s.publisher._descriptors.encode(
        s.command.job,
        s.command.binding,
        filename="changed.csv",
        sha256=s.publisher._descriptors.decode(s.command)["sha256"],
    )
    with pytest.raises(ReservationError, match="conflict"):
        await reserve(s, command=command)
    assert counts(s) == "1:1" and s.effects._blocked and s.history._blocked
    assert not p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_h", "between_p_and_r0"])
async def test_stop_refuses_reservation_at_both_boundaries(reservation_owner, monkeypatch, when):
    s = reservation_owner
    if when == "before_h":
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=p.r.deadline())
    else:
        original = s.r0.register_output_job

        async def stop_then_register(*args):
            # Simulate a native cooperating control action between the two SQL
            # transactions; no P account lock may remain to deadlock it.
            assert s.publisher._active_task is None
            assert (await s.worker.request_cancel(s.account, s.claim.task_id)).accepted
            return await original(*args)

        monkeypatch.setattr(s.r0, "register_output_job", stop_then_register)
    with pytest.raises((HistoryError, ReservationError)):
        await reserve(s)
    assert counts(s) == "0:0" and not p.r.files(s) and s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_lost_r0_commit_ack_blocks_h_without_readback_or_retry(
    reservation_owner, monkeypatch, fault
):
    s = reservation_owner
    original, register = Transaction.commit, s.r0.register_output_job
    observed = {}
    packet = s.command.wire()

    async def registered(conn, *args):
        observed["connection"] = conn._con
        return await register(conn, *args)

    async def commit(tx):
        if tx._connection is observed.get("connection"):
            s.db.bridge.commit_fault = fault
            observed["fired"] = True
        try:
            return await original(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(s.r0, "register_output_job", registered)
    monkeypatch.setattr(Transaction, "commit", commit)
    s.db.bridge.commit_fault_role = "reserved_r"
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            await reserve(s)
    finally:
        s.db.bridge.commit_fault = None
        s.db.bridge.commit_fault_role = "reserved_p"
    assert observed["fired"] and s.db.bridge.commit_seen.is_set()
    assert counts(s) == ("1:1" if fault == "after" else "0:0")
    assert s.command.wire() == packet and not p.r.files(s)
    assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await reserve(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_r0_reset_stays_owned_until_physical_completion(
    reservation_owner, monkeypatch, mode
):
    s = reservation_owner
    async with held_reset(s.r0_pool, monkeypatch, 1, mode == "failure") as (entered, release):
        task = asyncio.create_task(reserve(s))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert counts(s) == "1:1"
            if mode == "cancel":
                task.cancel("original reservation cancellation")
                await asyncio.sleep(0.02)
                task.cancel("repeated cancellation")
            elif mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeated cancellation after deadline")
            await asyncio.sleep(0.02)
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
            error = {
                "cancel": asyncio.CancelledError,
                "deadline": TimeoutError,
                "failure": RuntimeError,
            }[mode]
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original reservation cancellation",)
            assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
            assert not p.r.files(s)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline"])
async def test_interrupted_insert_drains_actual_cancel_transport_before_h_release(
    reservation_owner, monkeypatch, mode
):
    s = reservation_owner
    entered, release = asyncio.Event(), asyncio.Event()
    original = connect_utils._cancel

    async def held_cancel(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(connect_utils, "_cancel", held_cancel)
    async with s.db.connection() as conn:
        await conn.execute(
            "CREATE FUNCTION public.synthetic_r0_pause() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN PERFORM pg_advisory_xact_lock(17003,17004); PERFORM pg_sleep(5); RETURN NEW; END $$; "
            "REVOKE ALL ON FUNCTION public.synthetic_r0_pause() FROM PUBLIC; "
            "CREATE TRIGGER synthetic_r0_pause BEFORE INSERT ON artifact_output_intents "
            "FOR EACH ROW EXECUTE FUNCTION public.synthetic_r0_pause()"
        )
        task = asyncio.create_task(reserve(s))
        try:
            async with asyncio.timeout(5):
                while not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=17003 AND objid=17004)"
                ):
                    await asyncio.sleep(0.005)
            if mode == "cancel":
                task.cancel("original insert cancellation")
            await asyncio.wait_for(entered.wait(), 4)
            assert not task.done() and not available(s.path, "OP_LOCK")
            task.cancel("repeated cancellation while native transport is held")
            await asyncio.sleep(0.02)
            assert not task.done() and not available(s.path, "OP_LOCK")
            release.set()
            with pytest.raises(asyncio.CancelledError if mode == "cancel" else TimeoutError):
                await task
            assert counts(s) == "0:0" and s.effects._blocked and s.history._blocked
            assert available(s.path, "OP_LOCK") and not p.r.files(s)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_borrowed_r0_transaction_never_becomes_known_commit(reservation_owner, monkeypatch):
    s = reservation_owner
    original = asyncpg.Pool.acquire

    async def borrowed(pool, *args, **kwargs):
        conn = await original(pool, *args, **kwargs)
        if pool is s.r0_pool:
            await conn.execute("BEGIN")
        return conn

    monkeypatch.setattr(asyncpg.Pool, "acquire", borrowed)
    with pytest.raises(HistoryError, match="effects_transaction_owner_refused"):
        await reserve(s)
    assert counts(s) == "0:0" and not p.r.files(s) and s.effects._blocked


@pytest.mark.asyncio
async def test_known_reservation_cannot_succeed_after_h_cleanup_failure(
    reservation_owner, monkeypatch
):
    s = reservation_owner
    original = s.history._exit

    def failed(original_error):
        owned = s.history._context is not None
        original(original_error)
        if owned and original_error is None:
            raise RuntimeError("synthetic H reservation cleanup acknowledgement")

    monkeypatch.setattr(s.history, "_exit", failed)
    with pytest.raises(RuntimeError, match="cleanup acknowledgement"):
        await reserve(s)
    assert counts(s) == "1:1" and s.effects._blocked and s.history._blocked
    assert not p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["suspended", "generation", "deleted", "expired", "extra_privilege"]
)
async def test_current_native_fences_and_r0_principal_remain_required(reservation_owner, change):
    s = reservation_owner
    if change == "suspended":
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    elif change == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "deleted":
        s.db.sql("DELETE FROM conversations")
    elif change == "expired":
        s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    else:
        s.db.sql("GRANT SELECT ON artifact_output_intents TO reserved_r")
    with pytest.raises((HistoryError, ReservationError, asyncpg.PostgresError)):
        await reserve(s)
    assert counts(s) == "0:0" and not p.r.files(s) and s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_competing_commands_keep_one_immutable_reservation(
    reservation_owner, monkeypatch, conflict
):
    s = reservation_owner
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = s.r0.reserve_output_intent
    calls = 0

    async def held(*args):
        nonlocal calls
        value = await original(*args)
        calls += 1
        if calls == 1:
            entered.set()
            await proceed.wait()
        return value

    monkeypatch.setattr(s.r0, "reserve_output_intent", held)
    command = s.command
    if conflict:
        command = s.publisher._descriptors.encode(
            command.job,
            command.binding,
            filename="conflicting.csv",
            sha256=s.publisher._descriptors.decode(command)["sha256"],
        )
    first = asyncio.create_task(reserve(s))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(reserve(s, command=command))
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done()
        proceed.set()
        assert await first is None
        if conflict:
            with pytest.raises(ReservationError, match="conflict"):
                await second
            assert s.effects._blocked and s.history._blocked
        else:
            assert await second is None and not s.effects._blocked
        assert counts(s) == "1:1" and not p.r.files(s)
    finally:
        proceed.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["generation", "source_delete", "account_delete"])
async def test_fence_committed_after_p_release_is_rechecked_by_r0(
    reservation_owner, monkeypatch, fence
):
    s = reservation_owner
    original = s.r0.register_output_job
    applied = []

    async def changed(*args):
        assert s.publisher._active_task is None
        async with s.db.connection() as owner:
            if fence == "generation":
                await owner.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
            elif fence == "source_delete":
                await owner.execute(
                    "DELETE FROM conversations WHERE id=$1", s.claim.conversation_id
                )
            else:
                await owner.execute("DELETE FROM users WHERE id=$1", s.account)
        applied.append(True)
        return await original(*args)

    monkeypatch.setattr(s.r0, "register_output_job", changed)
    with pytest.raises(ReservationError):
        await reserve(s)
    assert applied and counts(s) == "0:0" and s.effects._blocked and not p.r.files(s)


@pytest.mark.asyncio
async def test_shutdown_waits_for_actual_r0_cleanup_and_cancelled_writer(
    reservation_owner, monkeypatch
):
    s = reservation_owner
    async with held_reset(s.r0_pool, monkeypatch, 1, False) as (entered, release):
        writer = asyncio.create_task(reserve(s))
        shutdown = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            writer.cancel("original shutdown reservation cancellation")
            shutdown = asyncio.create_task(s.history.aclose(deadline=p.r.deadline()))
            await asyncio.sleep(0.02)
            assert not writer.done() and not shutdown.done()
            assert not available(s.path, "OP_LOCK") and not available(s.path, "RUNTIME_LOCK")
            writer.cancel("repeated shutdown cancellation")
            await asyncio.sleep(0.02)
            assert not writer.done() and not shutdown.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await writer
            assert caught.value.args == ("original shutdown reservation cancellation",)
            await shutdown
            assert available(s.path, "OP_LOCK") and available(s.path, "RUNTIME_LOCK")
            assert counts(s) == "1:1" and not p.r.files(s)
            assert s.effects._blocked and s.history._blocked
        finally:
            release.set()
            await asyncio.gather(writer, *([shutdown] if shutdown else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_original_failure_survives_failed_termination(reservation_owner, monkeypatch):
    s = reservation_owner
    native_terminate = asyncpg.Connection.terminate
    original = ReservationError("synthetic_original_reservation_failure")
    observed = {}

    async def fail(conn, *args):
        observed["connection"] = conn._con
        raise original

    def terminate(conn):
        if conn is observed.get("connection"):
            raise RuntimeError("synthetic termination failure")
        return native_terminate(conn)

    monkeypatch.setattr(s.r0, "reserve_output_intent", fail)
    monkeypatch.setattr(asyncpg.Connection, "terminate", terminate)
    with pytest.raises(ReservationError) as caught:
        await reserve(s)
    assert caught.value is original
    assert "Native reservation termination also failed" in original.__notes__
    assert s.effects._blocked and s.history._blocked and counts(s) == "0:0"
    assert not p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("pool_kind", ["worker", "publication", "owner"])
async def test_r0_pool_is_distinct_and_actual_current_role_is_restricted(
    reservation_owner, pool_kind
):
    s = reservation_owner
    pool = {
        "worker": s.worker._pool,
        "publication": s.publisher._pool,
        "owner": s.native_store._namespaces._pool,
    }[pool_kind]
    with pytest.raises(ReservationError if pool_kind == "owner" else HistoryError):
        await reserve(s, pool=pool)
    assert counts(s) == "0:0" and not p.r.files(s)
    if pool_kind == "owner":
        assert s.effects._blocked and s.history._blocked
    else:
        assert not s.effects._blocked and not s.history._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["md_app", "md_acceptor"])
async def test_another_pool_cannot_turn_a_service_login_into_r0(
    reservation_owner, monkeypatch, principal
):
    s = reservation_owner
    # Owned synthetic privilege-drift fault. The unchanged native R0 predicate
    # alone admits these capability sets; pool-object separation is insufficient.
    s.db.sql(
        "GRANT EXECUTE ON FUNCTION artifact_output_readback(jsonb,jsonb),"
        "artifact_output_register(jsonb,jsonb),artifact_output_reserve(jsonb,jsonb,jsonb) "
        "TO md_app,md_acceptor"
    )
    pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user=principal,
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=2,
        command_timeout=2,
    )
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval(ROLE_PRECONDITION) is True
            assert await conn.fetchval("SELECT CURRENT_USER::text") == principal

        async def forbidden(*args, **kwargs):
            pytest.fail("service login reached reservation writes")

        monkeypatch.setattr(s.r0, "register_output_job", forbidden)
        with pytest.raises(HistoryError, match="artifact_principal_refused"):
            await reserve(s, pool=pool)
        assert counts(s) == "0:0" and s.effects._blocked and s.history._blocked
        assert not p.r.files(s)
    finally:
        try:
            async with asyncio.timeout(5):
                await pool.close()
        finally:
            pool.terminate()


@pytest.mark.asyncio
async def test_noninherited_service_membership_is_not_r0_authority(reservation_owner, monkeypatch):
    s = reservation_owner
    # Membership is a SET ROLE path even when its privileges are not inherited.
    # This is a single owned synthetic cluster; restore its role graph in finally.
    s.db.cluster.sql("GRANT md_app TO reserved_r WITH INHERIT FALSE")
    try:
        async with s.r0_pool.acquire() as conn:
            assert await conn.fetchval(ROLE_PRECONDITION) is True
            assert await conn.fetchval("SELECT pg_has_role(current_user,'md_app','MEMBER')")
            assert not await conn.fetchval("SELECT pg_has_role(current_user,'md_app','USAGE')")

        async def forbidden(*args, **kwargs):
            pytest.fail("service membership reached reservation writes")

        monkeypatch.setattr(s.r0, "register_output_job", forbidden)
        with pytest.raises(HistoryError, match="artifact_principal_refused"):
            await reserve(s)
        assert counts(s) == "0:0" and s.effects._blocked and s.history._blocked
        assert not p.r.files(s)
    finally:
        s.db.cluster.sql("REVOKE md_app FROM reserved_r")
