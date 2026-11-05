"""Actual eighth P reader and H preparation; synthetic roles, no runtime host."""

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.resource_store import ResourceError
from orchestrator.services.restore_history.model import HistoryError
from tests import test_history_output_reservation_postgres as r
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
ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "migrations/062_resource_output_preparation.sql"
SIGNATURE = "public.resource_output_preparation_snapshot(pg_catalog.jsonb)"
ROWS = [["column"], ["synthetic"]]


@pytest_asyncio.fixture
async def preparer(reservation_owner):
    s = reservation_owner
    s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    s.db.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO reserved_p")
    yield s


async def prepare(s, *, identity=None, job=None, slot=0, rows=ROWS, filename="synthetic.csv"):
    return await s.effects.prepare_output(
        s.publisher,
        identity or s.attempt,
        job or s.command.job,
        slot,
        rows,
        filename,
        deadline=r.p.r.deadline(),
    )


def native_unchanged(s):
    assert r.counts(s) == "0:0" and r.p.r.rows(s) == "0:0:0:0"
    assert not r.p.r.files(s)


def unchanged(s):
    native_unchanged(s)
    assert len(r.p.r.entries(s)) == 1


async def raw(conn, identity):
    return json.loads(
        await conn.fetchval(
            "SELECT public.resource_output_preparation_snapshot($1::pg_catalog.jsonb)",
            identity.wire(),
        )
    )


@pytest.mark.asyncio
async def test_preparation_uses_real_attempt_and_retained_packet_through_all_four_phases(preparer):
    s = preparer
    job = s.command.job
    command, content = await prepare(s)
    unchanged(s)
    assert content == r.p.r.CONTENT and command.job == job
    assert command.binding.attempt_id == s.command.binding.attempt_id
    assert command.binding.intent_id != s.command.binding.intent_id
    assert command.binding.measured_bytes == len(content)
    assert s.publisher._descriptors.decode(command)["sha256"] == hashlib.sha256(content).hexdigest()
    assert available(s.path, "OP_LOCK") and not s.effects._blocked
    s.command = command
    packet = command.wire()
    await r.reserve(s)
    await r.p.r.publish(s, content=content)
    await r.p.present(s)
    assert (await r.terminal(s)).value == "completed"
    assert s.command.wire() == packet and r.counts(s) == "1:1"
    assert r.p.r.rows(s) == "1:1:1:1"


@pytest.mark.asyncio
async def test_eighth_reader_is_closed_and_existing_seven_formats_remain_unchanged(preparer):
    s = preparer
    async with s.db.connection("reserved_p") as conn, conn.transaction():
        previous = await conn.fetchval(
            "SELECT public.resource_output_presentation_snapshot($1::pg_catalog.jsonb)",
            s.attempt.wire(),
        )
        assert set(json.loads(previous)) == {"task", "records", "observed_at"}
        result = await raw(conn, s.attempt)
        assert set(result) == {"schema_version", "task", "attempt_id"}
        assert result["schema_version"] == 1
        assert UUID(result["attempt_id"]) == s.command.binding.attempt_id
    # The optional eighth capability is not required by the older seven paths.
    s.db.sql("REVOKE EXECUTE ON FUNCTION " + SIGNATURE + " FROM reserved_p")
    async with s.db.connection("reserved_p") as conn, conn.transaction():
        previous = await conn.fetchval(
            "SELECT public.resource_output_presentation_snapshot($1::pg_catalog.jsonb)",
            s.attempt.wire(),
        )
        assert set(json.loads(previous)) == {"task", "records", "observed_at"}
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with s.db.connection("reserved_p") as conn, conn.transaction():
            await raw(conn, s.attempt)
    unchanged(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "md_acceptor", "reserved_r"])
async def test_ordinary_principals_do_not_gain_preparation_capability(preparer, role):
    s = preparer
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with s.db.connection(role) as conn, conn.transaction():
            await raw(conn, s.attempt)
    unchanged(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "SELECT id FROM task_attempts",
        "UPDATE tasks SET status='completed'",
        "SELECT public.resource_output_preparation_principal()",
        "SELECT public.resource_output_presentation_scope('{}'::jsonb)",
    ],
)
async def test_p_still_cannot_read_tables_mutate_or_invoke_helpers(preparer, query):
    s = preparer
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with s.db.connection("reserved_p") as conn, conn.transaction():
            await conn.execute(query)
    unchanged(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["grant_option", "extra_grantee", "helper", "table"])
async def test_new_reader_refuses_privilege_drift_before_returning_a_packet(preparer, drift):
    s = preparer
    if drift == "grant_option":
        s.db.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO reserved_p WITH GRANT OPTION")
    elif drift == "extra_grantee":
        s.db.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO md_app")
    elif drift == "helper":
        s.db.sql(
            "GRANT EXECUTE ON FUNCTION public.resource_output_preparation_principal() TO reserved_p"
        )
    else:
        s.db.sql("GRANT SELECT ON task_attempts TO reserved_p")
    with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
        await prepare(s)
    unchanged(s)
    assert s.effects._blocked and s.history._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["generation", "task", "producer", "job_account", "job_task"])
async def test_original_identity_and_job_cannot_be_retargeted(preparer, kind):
    s = preparer
    identity, job = s.attempt, s.command.job
    if kind == "generation":
        identity = replace(identity, generation=1)
    if kind == "task":
        identity = replace(identity, task_id=uuid4())
    if kind == "producer":
        identity = replace(identity, producer_instance_id=uuid4())
    if kind == "job_account":
        job = replace(job, account_id=uuid4())
    if kind == "job_task":
        job = replace(job, task_id=uuid4())
    with pytest.raises((HistoryError, asyncpg.PostgresError)):
        await prepare(s, identity=identity, job=job)
    unchanged(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "projection",
        "ciphertext",
        "source",
        "lease",
        "generation",
        "suspension",
        "suspension_cause",
        "deleted_source",
        "closed_lifecycle",
        "history_tail",
        "stop",
    ],
)
async def test_current_authority_and_history_are_required_before_packet_creation(
    preparer, monkeypatch, damage
):
    s = preparer
    if damage == "projection":
        s.db.sql(
            "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;DELETE FROM task_history_projection;ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
        )
    elif damage == "ciphertext":
        s.db.sql("UPDATE tasks SET input_ciphertext='synthetic-invalid-ciphertext'")
    elif damage == "source":
        async with s.db.connection() as conn, conn.transaction():
            message = uuid4()
            await conn.execute(
                "INSERT INTO messages(id,conversation_id,user_id,role,content) SELECT $1,conversation_id,user_id,role,content FROM messages WHERE id=$2",
                message,
                s.claim.result_message_id,
            )
            await conn.execute("UPDATE tasks SET result_message_id=$1", message)
    elif damage == "lease":
        s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    elif damage == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=1")
    elif damage == "suspension":
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    elif damage == "suspension_cause":
        async with s.db.connection() as owner, owner.transaction():
            await owner.execute(
                "INSERT INTO account_suspension_causes VALUES ($1,$2,'operator')",
                s.account,
                uuid4(),
            )
    elif damage == "deleted_source":
        s.db.sql("DELETE FROM conversations")
    elif damage == "closed_lifecycle":
        async with s.db.connection() as owner, owner.transaction():
            await owner.execute(
                "SELECT public.attempt_lifecycle_close($1::jsonb,$2,'requested')",
                s.attempt.wire(),
                uuid4(),
            )
    elif damage == "history_tail":
        with (s.path / "LOG").open("ab") as log:
            log.write(b"!")
    else:
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=r.p.r.deadline()
            )
        ).accepted

    def forbidden(*args, **kwargs):
        pytest.fail("invalid authority reached command allocation")

    monkeypatch.setattr(s.publisher._descriptors, "encode", forbidden)
    with pytest.raises((HistoryError, asyncpg.PostgresError, ResourceError, ValueError)):
        await prepare(s)
    assert r.counts(s) == "0:0" and not r.p.r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"slot": True},
        {"slot": -1},
        {"rows": [[False]]},
        {"rows": [["x"]] * 10001},
        {"filename": "x" * 1025},
        {"filename": "\ud800"},
    ],
)
async def test_bounded_request_validation_precedes_any_sql(preparer, monkeypatch, invalid):
    s = preparer

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached SQL")

    monkeypatch.setattr(s.publisher, "_preparation_snapshot", forbidden)
    with pytest.raises((HistoryError, ResourceError)):
        await prepare(s, **invalid)
    unchanged(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_unknown_preparation_commit_returns_no_packet_and_blocks_regeneration(
    preparer, monkeypatch, fault
):
    s = preparer
    native, encode = Transaction.commit, s.publisher._descriptors.encode
    packets = []

    def encoded(*args, **kwargs):
        command = encode(*args, **kwargs)
        packets.append(command)
        return command

    async def commit(tx):
        if tx._connection is getattr(s.publisher._connection, "_con", None):
            s.db.bridge.commit_fault = fault
        try:
            return await native(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(s.publisher._descriptors, "encode", encoded)
    monkeypatch.setattr(Transaction, "commit", commit)
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            await prepare(s)
    finally:
        s.db.bridge.commit_fault = None
    assert len(packets) == 1 and s.db.bridge.commit_seen.is_set()
    unchanged(s)
    assert s.effects._blocked and s.history._blocked
    with pytest.raises(HistoryError, match="blocked"):
        await prepare(s)
    with pytest.raises(HistoryError, match="blocked"):
        await r.reserve(s, command=packets[0])
    assert len(packets) == 1 and available(s.path, "OP_LOCK")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_preparation_reset_remains_owned_until_settlement(preparer, monkeypatch, mode):
    s = preparer
    async with held_reset(s.publisher._pool, monkeypatch, 1, mode == "failure") as (
        entered,
        release,
    ):
        task = asyncio.create_task(prepare(s))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original preparation cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeated preparation cancellation")
            elif mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeated after logical deadline")
            await asyncio.sleep(0.03)
            assert not task.done() and not available(s.path, "OP_LOCK")
            assert not available(s.path, "RUNTIME_LOCK")
            # A second HistoryStore must not acquire the writer's retained H
            # ownership. Inspect native rows/files now, then verify H after the
            # actual reset settles and the operation owner exits below.
            native_unchanged(s)
            release.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TimeoutError
                if mode == "deadline"
                else RuntimeError
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original preparation cancellation",)
            assert s.effects._blocked and s.history._blocked and available(s.path, "OP_LOCK")
            with pytest.raises(HistoryError, match="blocked"):
                await prepare(s)
            unchanged(s)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_preparation_refuses_a_borrowed_savepoint_before_allocating(preparer, monkeypatch):
    s = preparer
    native = asyncpg.Pool.acquire

    async def borrowed(pool, *args, **kwargs):
        conn = await native(pool, *args, **kwargs)
        if pool is s.publisher._pool:
            await conn.execute("BEGIN")
        return conn

    def forbidden(*args, **kwargs):
        pytest.fail("borrowed transaction reached packet allocation")

    monkeypatch.setattr(asyncpg.Pool, "acquire", borrowed)
    monkeypatch.setattr(s.publisher._descriptors, "encode", forbidden)
    with pytest.raises(ResourceError, match="transaction"):
        await prepare(s)
    unchanged(s)
    assert s.effects._blocked and s.publisher._blocked


@pytest.mark.asyncio
async def test_lost_history_release_acknowledgement_cannot_return_a_packet(preparer, monkeypatch):
    s = preparer
    native = s.history._exit
    fired = False

    def lost(original):
        nonlocal fired
        owned = s.history._context is not None
        native(original)
        if owned and original is None:
            fired = True
            raise RuntimeError("synthetic preparation cleanup acknowledgement")

    monkeypatch.setattr(s.history, "_exit", lost)
    with pytest.raises(RuntimeError, match="cleanup acknowledgement"):
        await prepare(s)
    assert fired and s.effects._blocked and s.history._blocked
    assert available(s.path, "OP_LOCK")
    unchanged(s)


@pytest.mark.asyncio
async def test_shutdown_waits_for_cancelled_preparation_and_actual_cleanup(preparer, monkeypatch):
    s = preparer
    async with held_reset(s.publisher._pool, monkeypatch, 1, False) as (entered, release):
        writer = asyncio.create_task(prepare(s))
        shutdown = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            writer.cancel("original preparation shutdown cancellation")
            shutdown = asyncio.create_task(s.history.aclose(deadline=r.p.r.deadline()))
            await asyncio.sleep(0.03)
            assert not writer.done() and not shutdown.done()
            assert not available(s.path, "OP_LOCK") and not available(s.path, "RUNTIME_LOCK")
            writer.cancel("repeated preparation shutdown cancellation")
            await asyncio.sleep(0.03)
            assert not writer.done() and not shutdown.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await writer
            assert caught.value.args == ("original preparation shutdown cancellation",)
            await shutdown
            assert available(s.path, "OP_LOCK") and available(s.path, "RUNTIME_LOCK")
            unchanged(s)
        finally:
            release.set()
            await asyncio.gather(writer, *([shutdown] if shutdown else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "generation", "source"])
async def test_native_post_wait_checks_cannot_return_a_packet_after_control_wins(
    preparer, operation
):
    s = preparer
    # Hold a native source/account lock on an owned synthetic connection, then
    # start the selected P reader. Only that exact backend PID is monitored.
    async with s.db.connection() as owner, s.db.connection("postgres") as monitor:
        tx = owner.transaction()
        await tx.start()
        await owner.execute(
            "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", s.account
        )
        before = s.publisher._preparation_snapshot
        entered = asyncio.Event()
        pid = None

        async def observed(conn, identity):
            nonlocal pid
            pid = conn.get_server_pid()
            entered.set()
            return await before(conn, identity)

        # Scoped manual override is restored before fixture teardown; it only
        # exposes the exact synthetic PID, never grants monitoring to P.
        s.publisher._preparation_snapshot = observed
        reader = asyncio.create_task(prepare(s))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            async with asyncio.timeout(2):
                while not await monitor.fetchval(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", pid
                ):
                    assert not reader.done()
                    await asyncio.sleep(0.005)
            if operation == "stop":
                await owner.execute(
                    "UPDATE tasks SET cancel_requested_at=clock_timestamp() WHERE id=$1",
                    s.claim.task_id,
                )
            elif operation == "generation":
                await owner.execute(
                    "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                    s.account,
                )
            else:
                await owner.execute(
                    "UPDATE tasks SET input_ciphertext='synthetic-invalid-ciphertext' WHERE id=$1",
                    s.claim.task_id,
                )
            await tx.commit()
            with pytest.raises((HistoryError, asyncpg.PostgresError, ValueError)):
                await reader
            unchanged(s)
            assert s.effects._blocked
        finally:
            s.publisher._preparation_snapshot = before
            if owner.is_in_transaction():
                await tx.rollback()
            await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["deletion", "reset"])
async def test_open_native_deletion_or_reset_request_blocks_preparation(preparer, kind):
    s = preparer
    # Synthetic request facts satisfy the production constraints. This tests the
    # independent native refusal, not a completed J1 lifecycle implementation.
    async with s.db.connection() as owner, owner.transaction():
        await owner.execute(
            "INSERT INTO account_deletions(id,account_id,kind,journal_id,request_seq,request_event_id,request_digest,command_digest,requested_at,prior_status,fence_generation,drain_deadline,revocation_deadline,billing_deadline) VALUES($1,$2,$3,$4,2,$5,$6,$6,clock_timestamp(),'active',1,clock_timestamp()+interval '1 day',clock_timestamp()+interval '1 day',clock_timestamp()+interval '1 day')",
            uuid4(),
            s.account,
            kind,
            s.journal_id,
            uuid4(),
            b"x" * 32,
        )
    with pytest.raises((asyncpg.PostgresError, HistoryError)):
        await prepare(s)
    unchanged(s)
    assert s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["md_app", "md_acceptor", "reserved_r", "md_owner"])
async def test_dormant_service_membership_cannot_become_preparation_authority(preparer, principal):
    s = preparer
    # Synthetic role graph only. A NOINHERIT membership still permits SET ROLE;
    # restore it even when the capability check fails.
    s.db.cluster.sql("GRANT " + principal + " TO reserved_p WITH INHERIT FALSE")
    try:
        async with s.db.connection("reserved_p") as conn:
            assert await conn.fetchval(
                "SELECT pg_has_role(current_user,$1::name,'MEMBER')", principal
            )
            assert not await conn.fetchval(
                "SELECT pg_has_role(current_user,$1::name,'USAGE')", principal
            )
        with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
            async with s.db.connection("reserved_p") as conn, conn.transaction():
                await conn.fetchval(
                    "SELECT public.resource_output_presentation_snapshot($1::jsonb)",
                    s.attempt.wire(),
                )
        with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
            await prepare(s)
        unchanged(s)
    finally:
        s.db.cluster.sql("REVOKE " + principal + " FROM reserved_p")


@pytest.mark.asyncio
async def test_unsafe_default_execute_grant_aborts_only_new_migration(reservation_owner):
    s = reservation_owner
    before = s.db.sql(
        "SELECT md5(pg_get_functiondef('public.resource_output_presentation_principal()'::regprocedure))"
    )
    s.db.sql(
        "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO md_app"
    )
    try:
        with pytest.raises(AssertionError, match="resource_output_unsafe_default_privileges"):
            s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
        assert (
            s.db.sql(
                "SELECT to_regprocedure('public.resource_output_preparation_snapshot(jsonb)') IS NULL"
            )
            == "t"
        )
        assert (
            s.db.sql(
                "SELECT md5(pg_get_functiondef('public.resource_output_presentation_principal()'::regprocedure))"
            )
            == before
        )
        assert (
            s.db.sql(
                "SELECT to_regprocedure('public.resource_output_preparation_principal()') IS NULL"
            )
            == "t"
        )
        # Existing selected interfaces survive the failed source-only migration.
        async with s.db.connection("reserved_p") as conn, conn.transaction():
            observed = await conn.fetchval(
                "SELECT public.resource_output_presentation_snapshot($1::jsonb)", s.attempt.wire()
            )
            assert set(json.loads(observed)) == {"task", "records", "observed_at"}
        unchanged(s)
    finally:
        s.db.sql(
            "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM md_app"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["table", "column", "sequence"])
async def test_noninherited_raw_authority_is_not_a_clean_p_profile(preparer, authority):
    s = preparer
    s.db.cluster.sql(
        "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='prep_raw') THEN CREATE ROLE prep_raw NOLOGIN; END IF; END $$"
    )
    if authority == "table":
        s.db.sql("GRANT SELECT ON tasks TO prep_raw")
    elif authority == "column":
        s.db.sql("GRANT SELECT (id) ON tasks TO prep_raw")
    else:
        s.db.sql(
            "CREATE SEQUENCE preparation_probe; GRANT USAGE ON SEQUENCE preparation_probe TO prep_raw"
        )
    s.db.cluster.sql("GRANT prep_raw TO reserved_p WITH INHERIT FALSE")
    try:
        with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
            async with s.db.connection("reserved_p") as conn, conn.transaction():
                await conn.fetchval(
                    "SELECT public.resource_output_presentation_snapshot($1::jsonb)",
                    s.attempt.wire(),
                )
        with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
            await prepare(s)
        unchanged(s)
    finally:
        s.db.cluster.sql("REVOKE prep_raw FROM reserved_p")
