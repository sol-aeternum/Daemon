"""Real isolated PostgreSQL, snapshot COPY dumps and test-only replay projection.

No production phase applier exists; this suite never purges application accounts.
"""

from __future__ import annotations

import asyncio
import io
import multiprocessing
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
import asyncpg

from orchestrator.config import Settings
from orchestrator.services.deletion_journal import barrier
from orchestrator.services.deletion_journal.barrier import require_recovery_barrier, verify_applied
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    AckFields,
    Command,
    ReadyFields,
    RequestFields,
    TerminalFields,
    encode_entry,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request
from tests.durable_tasks_support import Env, durable_env_fixture
from tests.durable_tasks_support import DSN_ENV

env = durable_env_fixture()


def configured(path, identity) -> Settings:
    return cast(Any, Settings)(
        _env_file=None,
        daemon_deletion_journal_dir=str(path),
        daemon_deletion_journal_id=str(identity),
    )


@pytest.fixture
def directory(tmp_path: Path):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    return path, identity


async def project(conn, entry) -> None:
    """TEST-ONLY: both projection and marker use the caller's single transaction."""
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM deletion_journal_applied WHERE seq=$1)", entry.seq
    ):
        return
    await conn.execute(
        "INSERT INTO j1_projection(seq,payload) VALUES($1,$2)", entry.seq, encode_entry(entry)
    )
    await conn.execute(
        "INSERT INTO deletion_journal_applied(journal_id,seq,event_id,account_id,operation_id,entry_digest) "
        "VALUES($1,$2,$3,$4,$5,$6)",
        entry.journal_id,
        entry.seq,
        entry.event_id,
        entry.command.account_id,
        entry.command.operation_id,
        entry.digest,
    )


@pytest.mark.asyncio
async def test_empty_activation_and_disabled_binding_guard(env: Env, directory):
    path, identity = directory
    await require_recovery_barrier(env.pool, cast(Any, Settings)(_env_file=None))
    await require_recovery_barrier(env.pool, configured(path, identity))
    assert await env.pool.fetchval("SELECT journal_id FROM deletion_journal_binding") == identity
    await require_recovery_barrier(env.pool, configured(path, identity))
    with pytest.raises(JournalError, match="invalid_config"):
        await require_recovery_barrier(env.pool, cast(Any, Settings)(_env_file=None))
    with pytest.raises(JournalError, match="marker_mismatch"):
        await require_recovery_barrier(env.pool, configured(path, uuid4()))


@pytest.mark.asyncio
async def test_pending_append_blocks_without_marking_or_dispatch(env: Env, directory):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    with Journal(path, identity) as journal:
        entry = journal.append(request(env.alice), uuid4(), None, None)
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, configured(path, identity))
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM users WHERE id=$1", env.alice) == 1
    assert entry.command.account_id == env.alice


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["corrupt_head", "missing_storage", "unsafe_mode"])
async def test_real_database_barrier_storage_failure_never_binds(env: Env, directory, damage):
    path, identity = directory
    if damage == "corrupt_head":
        (path / "HEAD").write_bytes(b"{")
    elif damage == "missing_storage":
        path = path.parent / "absent"
    else:
        (path / "entries.log").chmod(0o644)
    with pytest.raises(JournalError):
        await require_recovery_barrier(env.pool, configured(path, identity))
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_binding") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0


@pytest.mark.asyncio
async def test_append_before_failed_commit_snapshot_restores_then_test_replay(env: Env, directory):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    await env.pool.execute(
        "CREATE TABLE j1_projection(seq bigint PRIMARY KEY,payload bytea NOT NULL)"
    )
    with Journal(path, identity) as journal:
        entry = journal.append(request(env.alice), uuid4(), None, None)
        view = journal.verify()
    dumps = {table: io.BytesIO() for table in ("j1_projection", "deletion_journal_applied")}
    async with env.pool.acquire() as writer, env.pool.acquire() as snapshot:
        transaction = writer.transaction()
        await transaction.start()
        await project(writer, entry)
        # Actual server consistent snapshot after append but before DB commit.
        async with snapshot.transaction(isolation="repeatable_read", readonly=True):
            await snapshot.fetchval("SELECT pg_export_snapshot()")
            for table, output in dumps.items():
                await snapshot.copy_from_table(table, output=output, format="binary")
        await transaction.rollback()  # failed transition commit, no fake marker
    assert await env.pool.fetchval("SELECT count(*) FROM j1_projection") == 0
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, configured(path, identity))
    async with env.pool.acquire() as conn:
        async with conn.transaction():
            for old in view.entries:
                await project(conn, old)
        await verify_applied(conn, view)
        assert await conn.fetchval("SELECT count(*) FROM j1_projection") == 1
        # Offline logical restoration of the older snapshot's marker/projection
        # tables. The independent journal is deliberately not rolled back.
        async with conn.transaction():
            await conn.execute("TRUNCATE j1_projection,deletion_journal_applied")
            for table, dump in dumps.items():
                dump.seek(0)
                await conn.copy_to_table(table, source=dump, format="binary")
        with pytest.raises(JournalError, match="recovery_required"):
            await verify_applied(conn, view)
        async with conn.transaction():
            for old in view.entries:
                await project(conn, old)
        # Repeat-safe mechanics and immutable deadline/generation bytes.
        async with conn.transaction():
            for old in view.entries:
                await project(conn, old)
        assert await conn.fetchval("SELECT payload FROM j1_projection WHERE seq=1") == encode_entry(
            entry
        )
        await verify_applied(conn, view)
    await require_recovery_barrier(env.pool, configured(path, identity))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column", ["entry_digest", "account_id", "operation_id", "event_id", "seq"]
)
async def test_marker_mismatch_never_skips_effect(env: Env, directory, column):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    await env.pool.execute(
        "CREATE TABLE j1_projection(seq bigint PRIMARY KEY,payload bytea NOT NULL)"
    )
    with Journal(path, identity) as journal:
        entry = journal.append(request(), uuid4(), None, None)
    async with env.pool.acquire() as conn:
        async with conn.transaction():
            await project(conn, entry)
    value = bytes(32) if column == "entry_digest" else 2 if column == "seq" else uuid4()
    # Parameterized values; column drawn solely from the literal test allowlist.
    await env.pool.execute(f"UPDATE deletion_journal_applied SET {column}=$1", value)
    with pytest.raises(JournalError, match="marker_mismatch"):
        await require_recovery_barrier(env.pool, configured(path, identity))
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 1


@pytest.mark.asyncio
async def test_whole_pre_j1_schema_distinct_from_partial_schema(env: Env):
    # The shared disposable fixture applies every migration. Remove explicit C1
    # namespace dependents and J1b first, never CASCADE away unexpected objects.
    # This models absent J1 tables rather than a partially broken binding schema.
    await env.pool.execute("DROP TABLE resource_publications,artifact_namespaces")
    await env.pool.execute("DROP TABLE account_deletions,account_suspension_causes")
    await env.pool.execute("ALTER TABLE entitlement_accounts DROP COLUMN content_generation")
    await env.pool.execute("DROP TABLE deletion_journal_applied,deletion_journal_binding")
    assert await env.pool.fetchval("SELECT to_regclass('artifact_namespaces')") is None
    assert await env.pool.fetchval("SELECT to_regclass('resource_versions')") is not None
    await require_recovery_barrier(env.pool, cast(Any, Settings)(_env_file=None))
    await env.pool.execute(
        "CREATE TABLE deletion_journal_binding(singleton boolean,journal_id uuid)"
    )
    with pytest.raises(JournalError, match="schema_required"):
        await require_recovery_barrier(env.pool, cast(Any, Settings)(_env_file=None))


@pytest.mark.asyncio
async def test_leader_connection_loss_and_timeout_do_not_leak_session_lock(
    env: Env, directory, monkeypatch
):
    path, identity = directory
    leader = await env.pool.acquire()
    await leader.execute("SELECT pg_advisory_lock($1,$2)", *barrier.RECOVERY_KEY)
    pid = await leader.fetchval("SELECT pg_backend_pid()")
    waiter = asyncio.create_task(require_recovery_barrier(env.pool, configured(path, identity)))
    async with env.pool.acquire() as observer:
        async with asyncio.timeout(5):
            while not await observer.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"
            ):
                await asyncio.sleep(0)
        assert not waiter.done()
        await observer.execute("SELECT pg_terminate_backend($1)", pid)
    # Server termination is asynchronous; retire the local connection explicitly
    # instead of racing pool reset against the server's final termination packet.
    leader.terminate()
    await env.pool.release(leader)
    await asyncio.wait_for(waiter, 5)
    assert await env.pool.fetchval("SELECT journal_id FROM deletion_journal_binding") == identity
    monkeypatch.setattr(barrier, "STARTUP_BUDGET_S", 0.05)
    async with env.pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock($1,$2)", *barrier.RECOVERY_KEY)
        with pytest.raises(JournalError, match="unavailable"):
            await require_recovery_barrier(env.pool, configured(path, identity))
        await holder.execute("SELECT pg_advisory_unlock($1,$2)", *barrier.RECOVERY_KEY)
    monkeypatch.undo()
    await asyncio.wait_for(require_recovery_barrier(env.pool, configured(path, identity)), 5)


@pytest.mark.asyncio
async def test_non_cascading_markers_and_binding_restrict(env: Env, directory):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    await env.pool.execute(
        "CREATE TABLE j1_projection(seq bigint PRIMARY KEY,payload bytea NOT NULL)"
    )
    with Journal(path, identity) as journal:
        entry = journal.append(request(env.alice), uuid4(), None, None)
    async with env.pool.acquire() as conn:
        async with conn.transaction():
            await project(conn, entry)
        await conn.execute("DELETE FROM users WHERE id=$1", env.alice)
        assert await conn.fetchval("SELECT account_id FROM deletion_journal_applied") == env.alice
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("DELETE FROM deletion_journal_binding")


def _crash_transition(path, identity, command, event, schema, phase):
    """Test-owned process, connection and projection; no production producer."""

    async def run():
        conn = await asyncpg.connect(os.environ[DSN_ENV], server_settings={"search_path": schema})
        if phase == "before_append":
            os._exit(76)
        with Journal(path, identity) as journal:
            entry = journal.append(command, event, None, None)
        if phase == "before_mutation":
            os._exit(76)
        transaction = conn.transaction()
        await transaction.start()
        await project(conn, entry)
        if phase == "before_commit":
            os._exit(76)
        await transaction.commit()
        os._exit(76)  # Committed, but no response delivered to the caller.

    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase", ["before_append", "before_mutation", "before_commit", "after_commit"]
)
async def test_real_process_append_commit_crashes_and_lost_reply(env: Env, directory, phase):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    await env.pool.execute(
        "CREATE TABLE j1_projection(seq bigint PRIMARY KEY,payload bytea NOT NULL)"
    )
    schema = await env.pool.fetchval("SHOW search_path")
    command, event = request(env.alice), uuid4()
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_transition, args=(path, identity, command, event, schema, phase)
    )
    child.start()
    try:
        await asyncio.to_thread(child.join, 15)
        assert child.exitcode == 76
        with Journal(path, identity) as journal:
            view = journal.verify()
            assert len(view.entries) == (0 if phase == "before_append" else 1)
            if phase == "before_append":
                assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
                return
            # No allocation of a new event or regenerated deadlines after loss.
            assert journal.append(command, event, None, None) == view.entries[0]
        committed = phase == "after_commit"
        assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == int(
            committed
        )
        assert await env.pool.fetchval("SELECT count(*) FROM j1_projection") == int(committed)
        async with env.pool.acquire() as conn:
            if not committed:
                with pytest.raises(JournalError, match="recovery_required"):
                    await verify_applied(conn, view)
            async with conn.transaction():
                await project(conn, view.entries[0])
            async with conn.transaction():
                await project(conn, view.entries[0])
            await verify_applied(conn, view)
        assert await env.pool.fetchval("SELECT count(*) FROM j1_projection") == 1
        assert await env.pool.fetchval("SELECT payload FROM j1_projection") == encode_entry(
            view.entries[0]
        )
        await require_recovery_barrier(env.pool, configured(path, identity))
    finally:
        if child.is_alive():
            child.terminate()
            await asyncio.to_thread(child.join, 5)


@pytest.mark.asyncio
async def test_older_reset_then_restored_deletion_replays_every_original_frame(env: Env, directory):
    path, identity = directory
    await require_recovery_barrier(env.pool, configured(path, identity))
    await env.pool.execute(
        "CREATE TABLE j1_projection(seq bigint PRIMARY KEY,payload bytea NOT NULL)"
    )
    reset = request(env.alice, reset=True)
    start = reset.decision_at + timedelta(days=1)
    with Journal(path, identity) as journal:
        journal.append(reset, uuid4(), None, None)
        journal.append(
            Command(
                env.alice,
                reset.operation_id,
                "reset_report_ready",
                start,
                ReadyFields(start, start + timedelta(days=7)),
            ),
            uuid4(),
            1,
            1,
        )
        journal.append(
            Command(env.alice, reset.operation_id, "reset_acknowledged", start, AckFields(start)),
            uuid4(),
            2,
            2,
        )
        journal.append(
            Command(env.alice, reset.operation_id, "reset_purged", start, TerminalFields((), 1)),
            uuid4(),
            3,
            3,
        )
        deletion = request(env.alice)
        assert isinstance(deletion.fields, RequestFields)
        deletion = replace(
            deletion,
            decision_at=start + timedelta(days=2),
            fields=replace(deletion.fields, generation=2),
        )
        journal.append(deletion, uuid4(), 4, None)
        grace = deletion.decision_at + timedelta(days=1)
        journal.append(
            Command(
                env.alice,
                deletion.operation_id,
                "grace_started",
                grace,
                ReadyFields(grace, grace + timedelta(days=7)),
            ),
            uuid4(),
            5,
            5,
        )
        journal.append(
            Command(env.alice, deletion.operation_id, "restored", grace, TerminalFields((), 3)),
            uuid4(),
            6,
            6,
        )
        view = journal.verify()
    # A real backup already containing the first reset's markers must skip that
    # prefix. Replaying a later restore must not collapse away earlier entries.
    async with env.pool.acquire() as conn:
        async with conn.transaction():
            for entry in view.entries[:4]:
                await project(conn, entry)
        with pytest.raises(JournalError, match="recovery_required"):
            await verify_applied(conn, view)
        async with conn.transaction():
            for entry in view.entries:
                await project(conn, entry)
        rows = await conn.fetch("SELECT seq,payload FROM j1_projection ORDER BY seq")
        assert [(r["seq"], bytes(r["payload"])) for r in rows] == [
            (e.seq, encode_entry(e)) for e in view.entries
        ]
        await verify_applied(conn, view)
    await require_recovery_barrier(env.pool, configured(path, identity))
