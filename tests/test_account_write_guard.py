"""Real PG/POSIX prerequisite proofs, NOT integrated runtime fencing.

Only TASKS_TEST_DATABASE_URL is used, with fixture-owned disposable schemas.
Blocking edges/events establish order; deadlines are failure watchdogs, not proof.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    ContentWriteScope,
    WriteGuardRefusal,
    capture_generation,
    guard_content_write,
)
from orchestrator.services.account_control.store import lock_account_control, record_request_facts
from orchestrator.services.deletion_journal.barrier import require_recovery_barrier
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import RequestFields
from orchestrator.services.deletion_journal.store import Journal
from tests.deletion_journal_support import request
from tests.durable_tasks_support import DSN_ENV, durable_env_fixture
from tests.test_account_control_persistence import directory as directory, prepare, settings

env = durable_env_fixture()


async def setup(env):
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id) VALUES($1),($2)", env.alice, env.bob
    )
    await env.pool.execute(
        "CREATE TABLE guard_probe(owner uuid REFERENCES users(id) "
        "DEFERRABLE INITIALLY DEFERRED, value text)"
    )


async def blocked(observer, waiter, holder):
    """Observe actual PG wait-for edge, never infer contention from elapsed time."""
    async with asyncio.timeout(10):
        while holder not in await observer.fetchval("SELECT pg_blocking_pids($1)", waiter):
            await asyncio.sleep(0)  # scheduler yield; the query is the evidence


async def probe(conn, owner):
    await conn.execute("INSERT INTO guard_probe VALUES($1,'synthetic')", owner)


@pytest.mark.parametrize("value", [-1, True, 1.0, "1", 2**63])
def test_capture_rejects_invalid_generation(value):
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        CapturedGeneration(uuid4(), value)


def test_capture_is_immutable_value_not_authority():
    captured = CapturedGeneration(uuid4(), 0)
    with pytest.raises(FrozenInstanceError):
        cast(Any, captured).generation = 1
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        CapturedGeneration(cast(Any, "not-uuid"), 0)
    with pytest.raises(TypeError):
        ContentWriteScope()


@pytest.mark.asyncio
async def test_share_survives_scope_exit_until_caller_commit(env):
    await setup(env)
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
        expected = await capture_generation(writer, env.alice)
        tx = writer.transaction()
        await tx.start()
        async with guard_content_write(writer, expected) as scope:
            await probe(writer, env.alice)
        acquired = asyncio.Event()

        async def control():
            async with fence.transaction():
                await fence.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                    env.alice,
                )
                acquired.set()
                assert await fence.fetchval("SELECT count(*) FROM guard_probe") == 1
                await fence.execute(
                    "UPDATE entitlement_accounts SET status='suspended',content_generation=1 "
                    "WHERE user_id=$1",
                    env.alice,
                )

        async with asyncio.TaskGroup() as group:
            group.create_task(control())
            await blocked(env.pool, fence.get_server_pid(), writer.get_server_pid())
            assert not acquired.is_set()
            with pytest.raises(WriteGuardRefusal, match="scope_expired"):
                await scope.revalidate(writer, expected)
            await tx.commit()
        assert acquired.is_set()
        assert await writer.fetchval("SELECT count(*) FROM guard_probe") == 1


@pytest.mark.asyncio
async def test_fence_first_waiting_writer_refuses_no_partial_write(env):
    await setup(env)
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
        expected = await capture_generation(writer, env.alice)
        tx = fence.transaction()
        await tx.start()
        await fence.execute(
            "UPDATE entitlement_accounts SET status='suspended',content_generation=1 "
            "WHERE user_id=$1",
            env.alice,
        )

        async def content():
            with pytest.raises(WriteGuardRefusal, match="account_restricted"):
                async with writer.transaction(), guard_content_write(writer, expected):
                    await probe(writer, env.alice)

        async with asyncio.TaskGroup() as group:
            group.create_task(content())
            await blocked(env.pool, writer.get_server_pid(), fence.get_server_pid())
            await tx.commit()
        assert await writer.fetchval("SELECT count(*) FROM guard_probe") == 0


@pytest.mark.asyncio
async def test_old_generation_refuses_even_active_new_generation_and_other_account_works(env):
    await setup(env)
    async with env.pool.acquire() as conn:
        old = await capture_generation(conn, env.alice)
        # Test-only simulation, NOT reset/activation implementation.
        await conn.execute(
            "UPDATE entitlement_accounts SET content_generation=2 WHERE user_id=$1", env.alice
        )
        with pytest.raises(WriteGuardRefusal, match="stale_generation"):
            async with conn.transaction(), guard_content_write(conn, old):
                await probe(conn, env.alice)
        for account in (env.alice, env.bob):
            fresh = await capture_generation(conn, account)
            async with conn.transaction(), guard_content_write(conn, fresh):
                await probe(conn, account)
        assert await conn.fetchval("SELECT count(*) FROM guard_probe") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("isolation", ["repeatable_read", "serializable"])
async def test_stronger_snapshot_isolation_refused(env, isolation):
    await setup(env)
    async with env.pool.acquire() as conn:
        expected = await capture_generation(conn, env.alice)
        async with conn.transaction(isolation=isolation):
            await conn.fetchval("SELECT count(*) FROM account_suspension_causes")
            async with env.pool.acquire() as other, other.transaction():
                await other.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                    env.alice,
                )
                await other.execute(
                    "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')",
                    env.alice,
                    uuid4(),
                )
            with pytest.raises(WriteGuardRefusal, match="unsupported_isolation"):
                async with guard_content_write(conn, expected):
                    pytest.fail("stale snapshot cannot authorize")


@pytest.mark.asyncio
async def test_cause_only_change_after_lock_wait_uses_fresh_statement_snapshot(env):
    await setup(env)
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
        expected = await capture_generation(writer, env.alice)
        tx = fence.transaction()
        await tx.start()
        await fence.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        await fence.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
        )

        async def content():
            with pytest.raises(WriteGuardRefusal, match="inconsistent_state"):
                async with writer.transaction(), guard_content_write(writer, expected):
                    await probe(writer, env.alice)

        async with asyncio.TaskGroup() as group:
            group.create_task(content())
            await blocked(env.pool, writer.get_server_pid(), fence.get_server_pid())
            await tx.commit()
        assert await writer.fetchval("SELECT count(*) FROM guard_probe") == 0
        assert (
            await writer.fetchval(
                "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
            )
            == 0
        )


@pytest.mark.asyncio
async def test_scope_connection_account_transaction_lifetime_and_no_auto_commit(env):
    await setup(env)
    async with env.pool.acquire() as conn, env.pool.acquire() as other:
        expected = await capture_generation(conn, env.alice)
        with pytest.raises(WriteGuardRefusal, match="transaction_required"):
            async with guard_content_write(conn, expected):
                pytest.fail("caller transaction required")
        tx = conn.transaction()
        await tx.start()
        async with guard_content_write(conn, expected) as scope:
            with pytest.raises(WriteGuardRefusal, match="scope_mismatch"):
                await scope.revalidate(other, expected)
            with pytest.raises(WriteGuardRefusal, match="scope_mismatch"):
                await scope.revalidate(conn, CapturedGeneration(env.bob, 0))
            await probe(conn, env.alice)
            assert await other.fetchval("SELECT count(*) FROM guard_probe") == 0
            await tx.rollback()
            with pytest.raises(WriteGuardRefusal, match="transaction_required"):
                await scope.revalidate(conn, expected)
            async with conn.transaction():
                with pytest.raises(WriteGuardRefusal, match="scope_expired"):
                    await scope.revalidate(conn, expected)
        assert await other.fetchval("SELECT count(*) FROM guard_probe") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [False, True])
async def test_savepoint_rollback_revalidation_reacquires_or_refuses(env, change):
    await setup(env)
    async with env.pool.acquire() as conn, env.pool.acquire() as other, conn.transaction():
        expected = await capture_generation(conn, env.alice)
        savepoint = conn.transaction()
        await savepoint.start()
        async with guard_content_write(conn, expected) as scope:
            await savepoint.rollback()
            tx = other.transaction()
            await tx.start()
            # If the savepoint lock leaked this NOWAIT fails deterministically.
            await other.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT",
                env.alice,
            )
            if change:
                await other.execute(
                    "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                    env.alice,
                )

            async def recheck():
                if change:
                    with pytest.raises(WriteGuardRefusal, match="stale_generation"):
                        await scope.revalidate(conn, expected)
                else:
                    await scope.revalidate(conn, expected)

            async with asyncio.TaskGroup() as group:
                group.create_task(recheck())
                await blocked(env.pool, conn.get_server_pid(), other.get_server_pid())
                await tx.commit()
            if not change:
                with pytest.raises(asyncpg.LockNotAvailableError):
                    async with other.transaction():
                        await other.fetchrow(
                            "SELECT user_id FROM entitlement_accounts "
                            "WHERE user_id=$1 FOR UPDATE NOWAIT",
                            env.alice,
                        )


@pytest.mark.asyncio
async def test_missing_account_schema_and_legacy_suspension_fail_closed(env):
    await setup(env)
    async with env.pool.acquire() as conn:
        unknown = uuid4()
        with pytest.raises(WriteGuardRefusal, match="missing_account"):
            await capture_generation(conn, unknown)
        with pytest.raises(WriteGuardRefusal, match="missing_account"):
            async with (
                conn.transaction(),
                guard_content_write(conn, CapturedGeneration(unknown, 0)),
            ):
                pytest.fail("missing row must not be provisioned")
        await conn.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
        with pytest.raises(WriteGuardRefusal, match="account_restricted"):
            async with (
                conn.transaction(),
                guard_content_write(conn, CapturedGeneration(env.alice, 0)),
            ):
                pytest.fail("legacy suspension cannot be activated")
        with pytest.raises(asyncpg.UndefinedTableError):
            async with conn.transaction():
                await conn.execute("SET LOCAL search_path=pg_catalog")
                async with guard_content_write(conn, CapturedGeneration(env.bob, 0)):
                    pytest.fail("missing schema cannot authorize")
        assert await conn.fetchval("SELECT count(*) FROM entitlement_accounts") == 2


@pytest.mark.asyncio
async def test_unknown_state_and_missing_subordinate_schema_never_authorize(env):
    await setup(env)
    async with env.pool.acquire() as conn:
        # Synthetic corruption/compatibility fixture shadows only this connection,
        # not a production schema or a migration change.
        async with conn.transaction():
            await conn.execute(
                "CREATE TEMP TABLE entitlement_accounts "
                "(user_id uuid,status text,content_generation bigint) ON COMMIT DROP"
            )
            await conn.execute("INSERT INTO entitlement_accounts VALUES($1,'unknown',0)", env.alice)
            with pytest.raises(WriteGuardRefusal, match="inconsistent_state"):
                async with guard_content_write(conn, CapturedGeneration(env.alice, 0)):
                    pytest.fail("unknown status cannot authorize")
        with pytest.raises(asyncpg.UndefinedTableError):
            async with conn.transaction():
                await conn.execute(
                    "ALTER TABLE account_suspension_causes RENAME TO guard_hidden_causes"
                )
                async with guard_content_write(conn, CapturedGeneration(env.alice, 0)):
                    pytest.fail("missing subordinate schema cannot mean no causes")


@pytest.mark.asyncio
async def test_cancel_blocked_waiters_unwinds_caller_transaction_without_leak(env):
    await setup(env)
    async with env.pool.acquire() as holder, holder.transaction():
        await holder.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        for _ in range(3):
            async with env.pool.acquire() as waiter:

                async def write():
                    async with (
                        waiter.transaction(),
                        guard_content_write(waiter, CapturedGeneration(env.alice, 0)),
                    ):
                        await probe(waiter, env.alice)

                task = asyncio.create_task(write())
                await blocked(env.pool, waiter.get_server_pid(), holder.get_server_pid())
                task.cancel()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 10)
                assert not waiter.is_in_transaction()
                assert await waiter.fetchval("SELECT 1") == 1
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", env.alice
        )
        assert await conn.fetchval("SELECT count(*) FROM guard_probe") == 0


@pytest.mark.asyncio
async def test_kill_owned_writer_rolls_back_and_releases_share(env):
    await setup(env)
    schema = await env.pool.fetchval("SHOW search_path")
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import asyncio,asyncpg,sys\n"
        "from orchestrator.services.account_control.guard import capture_generation,guard_content_write\n"
        "from uuid import UUID\n"
        "async def run():\n"
        " c=await asyncpg.connect(sys.argv[1],server_settings={'search_path':sys.argv[2]})\n"
        " async with c.transaction():\n"
        "  x=await capture_generation(c,UUID(sys.argv[3]))\n"
        "  async with guard_content_write(c,x):\n"
        "   await c.execute(\"INSERT INTO guard_probe VALUES($1,'synthetic')\",x.account_id)\n"
        "   print(c.get_server_pid(),flush=True)\n"
        "   await asyncio.Event().wait()\n"
        "asyncio.run(run())\n",
        os.environ[DSN_ENV],
        schema,
        str(env.alice),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        async with asyncio.timeout(15):
            line = await child.stdout.readline()
        assert line, "owned child failed before establishing SHARE"
        pid = int(line)
        async with env.pool.acquire() as fence:

            async def lock():
                async with fence.transaction():
                    await fence.fetchrow(
                        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        env.alice,
                    )
                    assert await fence.fetchval("SELECT count(*) FROM guard_probe") == 0

            async with asyncio.TaskGroup() as group:
                group.create_task(lock())
                await blocked(env.pool, fence.get_server_pid(), pid)
                child.kill()  # ONLY this test-owned process
                await asyncio.wait_for(child.wait(), 10)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


@pytest.mark.asyncio
async def test_posix_append_failed_commit_retry_lost_reply_and_pending_operation(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    assert isinstance(command.fields, RequestFields)
    await env.pool.execute(
        "CREATE TABLE guard_probe(owner uuid REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,value text)"
    )

    async def driver(fail_commit=False):
        # TEST-ONLY J1b producer, no production fence/marker implementation.
        async with env.pool.acquire() as conn, conn.transaction():
            async with lock_account_control(conn, env.alice, command.operation_id) as scope:
                with Journal(*directory) as journal:
                    view = journal.verify()
                    if not view.entries:
                        journal.append(command, event, None, None)
                    verified = await scope.verify_request(journal, event)
                    record = await record_request_facts(conn, verified)
                    if fail_commit:
                        await probe(conn, uuid4())  # deferred FK fails at real commit
                    return record

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await driver(True)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0
    # DB-only guard is not a fence for an unprojected durable request: startup
    # must stay blocked even before J1b retry projects the request facts.
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, settings(*directory))
    first = await driver()
    # Simulate loss of the success reply, then retry original journal identity.
    assert await driver() == first
    assert first.id == command.operation_id and first.request_event_id == event
    assert first.fence_generation == command.fields.generation
    assert first.drain_deadline == command.fields.drain_deadline
    assert first.billing_deadline == command.fields.billing_deadline
    assert first.revocation_deadline == command.fields.revocation_deadline
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    async with env.pool.acquire() as conn:
        with pytest.raises(WriteGuardRefusal, match="open_operation"):
            async with (
                conn.transaction(),
                guard_content_write(conn, CapturedGeneration(env.alice, 1)),
            ):
                await probe(conn, env.alice)
        async with conn.transaction(), guard_content_write(conn, CapturedGeneration(env.bob, 0)):
            await probe(conn, env.bob)
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, settings(*directory))
