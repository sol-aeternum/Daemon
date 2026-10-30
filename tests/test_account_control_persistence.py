"""J1b real PostgreSQL/POSIX persistence, NOT real cancellation/fence/purge proof.

Explicit test-only append drivers never insert an applied marker except where a
test deliberately sets up contradictory/previous-history evidence. App DSNs are
never read; durable_env_fixture owns isolated schemas in a disposable database.
"""

from __future__ import annotations

import asyncio
import ast
import contextvars
import gc
import io
import multiprocessing
import os
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from orchestrator.config import Settings
from orchestrator.services.account_control import store
from orchestrator.services.account_control.store import lock_account_control, record_request_facts
from orchestrator.services.deletion_journal.barrier import require_recovery_barrier
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import (
    Cause,
    Command,
    ReadyFields,
    RequestFields,
    TerminalFields,
)
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import NOW, request
from tests.durable_tasks_support import DSN_ENV, durable_env_fixture

env = durable_env_fixture()


def settings(path, identity):
    return cast(Any, Settings)(
        _env_file=None,
        daemon_deletion_journal_dir=str(path),
        daemon_deletion_journal_id=str(identity),
    )


@pytest.fixture
def directory(tmp_path):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    return path, identity


async def prepare(env, directory):
    path, identity = directory
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id) VALUES($1),($2)", env.alice, env.bob
    )
    await require_recovery_barrier(env.pool, settings(path, identity))


async def persist(env, directory, command, event):
    """TEST-ONLY producer; no such runtime integration exists in J1b."""
    path, identity = directory
    async with env.pool.acquire() as conn, conn.transaction():
        async with lock_account_control(conn, command.account_id, command.operation_id) as scope:
            with Journal(path, identity) as journal:
                view = journal.verify()
                journal.append(
                    command,
                    event,
                    view.account_heads.get(command.account_id),
                    view.operation_heads.get(command.operation_id),
                )
                verified = await scope.verify_request(journal, event)
                return await record_request_facts(conn, verified)


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", [False, True])
async def test_request_records_original_facts_without_status_marker_or_readiness(
    env, directory, reset
):
    await prepare(env, directory)
    command, event = request(env.alice, reset=reset), uuid4()
    record = await persist(env, directory, command, event)
    assert record.id == command.operation_id
    assert record.kind == ("reset" if reset else "deletion")
    assert record.requested_at == NOW
    assert record.fence_generation == 1
    assert record.drain_deadline == NOW + timedelta(minutes=13)
    assert record.revocation_deadline == record.billing_deadline == NOW + timedelta(days=1)
    assert record.closed_at is None
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    assert (
        await env.pool.fetchval(
            "SELECT status FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == "active"
    )
    assert (
        await env.pool.fetchval(
            "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == 1
    )
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, settings(*directory))


@pytest.mark.asyncio
async def test_same_request_concurrent_connections_record_once(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    records = await asyncio.gather(*(persist(env, directory, command, event) for _ in range(8)))
    assert all(record == records[0] for record in records)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1
    assert await env.pool.fetchval("SELECT count(*) FROM account_suspension_causes") == 1
    with Journal(*directory) as journal:
        assert len(journal.verify().entries) == 1


@pytest.mark.asyncio
async def test_competing_deletion_reset_requests_and_partial_index(env, directory):
    await prepare(env, directory)
    commands = [request(env.alice), request(env.alice, reset=True)]
    outcomes = await asyncio.gather(
        *(persist(env, directory, command, uuid4()) for command in commands), return_exceptions=True
    )
    assert sum(isinstance(result, store.OperationRecord) for result in outcomes) == 1
    assert sum(isinstance(result, JournalError) for result in outcomes) == 1
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1
    # Test-only direct SQL bypasses the library; the DB still enforces one open
    # deletion/reset. No closure API or second-operation producer is supplied.
    async with env.pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO account_deletions SELECT $1,account_id,kind,journal_id,"
                    "request_seq+10,$2,request_digest,command_digest,requested_at,prior_status,"
                    "prior_reason,fence_generation,drain_deadline,revocation_deadline,"
                    "billing_deadline,no_grace,reason,closed_at FROM account_deletions",
                    uuid4(),
                    uuid4(),
                )


@pytest.mark.asyncio
async def test_retry_does_not_rewind_later_cause_or_generation_state(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    record = await persist(env, directory, command, event)
    async with env.pool.acquire() as conn, conn.transaction():
        async with lock_account_control(conn, env.alice) as scope:
            await scope._remove_cause_exact(Cause("deletion", command.operation_id))
            await scope._insert_cause(Cause("operator", uuid4()))
            await conn.execute(
                "UPDATE entitlement_accounts SET content_generation=9 WHERE user_id=$1", env.alice
            )
    changed_allocations = replace(
        command,
        decision_at=NOW + timedelta(days=5),
        fields=replace(
            command.fields,
            generation=20,
            drain_deadline=NOW + timedelta(days=6),
            revocation_deadline=NOW + timedelta(days=6),
            billing_deadline=NOW + timedelta(days=6),
        ),
    )
    assert await persist(env, directory, changed_allocations, event) == record
    assert (
        await env.pool.fetchval(
            "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == 9
    )
    assert await env.pool.fetchval("SELECT cause_code FROM account_suspension_causes") == "operator"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["account", "operation", "command"])
async def test_event_identity_conflicts_leave_original_untouched(env, directory, change):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    record = await persist(env, directory, command, event)
    changed = (
        replace(command, account_id=env.bob)
        if change == "account"
        else request(env.alice)
        if change == "operation"
        else replace(command, fields=replace(command.fields, reason="abuse"))
    )
    with pytest.raises(JournalError, match="conflict"):
        await persist(env, directory, changed, event)
    assert await persist(env, directory, command, event) == record
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1


@pytest.mark.asyncio
async def test_cause_projection_and_exact_removal_preserve_other_causes_accounts(env, directory):
    await prepare(env, directory)
    causes = tuple(Cause(code, uuid4()) for code in ("billing", "billing", "abuse", "operator"))
    command, event = request(env.alice, reset=True), uuid4()
    assert isinstance(command.fields, RequestFields)
    command = replace(
        command,
        fields=replace(
            command.fields,
            prior_status="suspended",
            prior_reason="billing",
            causes=causes + command.fields.causes,
        ),
    )
    # A genuine older snapshot need not contain all journal-recorded causes.
    async with env.pool.acquire() as conn, conn.transaction():
        async with lock_account_control(conn, env.alice) as scope:
            await scope._insert_cause(causes[0])
        async with lock_account_control(conn, env.bob) as other:
            await other._insert_cause(causes[1])
    await persist(env, directory, command, event)
    async with env.pool.acquire() as conn, conn.transaction():
        async with lock_account_control(conn, env.alice) as scope:
            await scope._remove_cause_exact(Cause("deletion", command.operation_id))
            assert len(await scope._list_causes()) == 5  # wrong code removes nothing
            await scope._remove_cause_exact(Cause("reset", command.operation_id))
            assert set(await scope._list_causes()) == set(causes)
            await scope._insert_cause(causes[0])  # identical insertion is idempotent
            with pytest.raises(JournalError, match="conflict"):
                await scope._insert_cause(Cause("operator", causes[0].id))
        async with lock_account_control(conn, env.bob) as other:
            assert await other._list_causes() == (causes[1],)
    # Primitives never overwrite the existing entitlement status.
    assert (
        await env.pool.fetchval(
            "SELECT status FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == "active"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    ["extra_cause", "changed_code", "newer_generation", "equal_generation", "legacy_status"],
)
async def test_contradictory_first_projection_is_refused_without_partial_rows(
    env, directory, damage
):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    async with env.pool.acquire() as conn, conn.transaction():
        async with lock_account_control(conn, env.alice) as scope:
            if damage in {"extra_cause", "changed_code"}:
                await scope._insert_cause(
                    Cause("billing", command.operation_id if damage == "changed_code" else uuid4())
                )
            elif damage in {"newer_generation", "equal_generation"}:
                await conn.execute(
                    "UPDATE entitlement_accounts SET content_generation=$2 WHERE user_id=$1",
                    env.alice,
                    8 if damage == "newer_generation" else 1,
                )
            else:
                await conn.execute(
                    "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
                )
    with pytest.raises(JournalError, match="conflict"):
        await persist(env, directory, command, event)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0


@pytest.mark.asyncio
async def test_unattributed_suspension_not_inferred_from_reason(env, directory):
    await prepare(env, directory)
    command = request(env.alice)
    command = replace(
        command, fields=replace(command.fields, prior_status="suspended", prior_reason="operator")
    )
    with pytest.raises(JournalError, match="conflict"):
        await persist(env, directory, command, uuid4())
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0


@pytest.mark.asyncio
async def test_verification_requires_live_correct_transaction_and_account(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    with Journal(*directory) as journal:
        entry = journal.append(command, event, None, None)
        async with env.pool.acquire() as conn:
            with pytest.raises(JournalError, match="conflict"):
                async with lock_account_control(conn, env.alice):
                    pytest.fail("no transaction")
            with pytest.raises(JournalError, match="invalid_entry"):
                await record_request_facts(conn, cast(Any, entry))
            with pytest.raises(TypeError):
                store.VerifiedRequest()
            async with conn.transaction():
                async with lock_account_control(conn, env.bob) as wrong:
                    with pytest.raises(JournalError, match="conflict"):
                        await wrong.verify_request(journal, event)
                async with lock_account_control(conn, env.alice) as scope:
                    verified = await scope.verify_request(journal, event)
                    async with env.pool.acquire() as other, other.transaction():
                        with pytest.raises(JournalError, match="conflict"):
                            await record_request_facts(other, verified)
                with pytest.raises(JournalError, match="conflict"):
                    await record_request_facts(conn, verified)
            async with conn.transaction():
                with pytest.raises(JournalError, match="conflict"):
                    await record_request_facts(conn, verified)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0


@pytest.mark.asyncio
async def test_transaction_replacement_inside_scope_cannot_reuse_verified_request(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    with Journal(*directory) as journal:
        journal.append(command, event, None, None)
        async with env.pool.acquire() as conn:
            await conn.execute("BEGIN")
            try:
                async with lock_account_control(conn, env.alice) as scope:
                    verified = await scope.verify_request(journal, event)
                    await conn.execute("COMMIT")
                    await conn.execute("BEGIN")
                    with pytest.raises(JournalError, match="conflict"):
                        await record_request_facts(conn, verified)
            finally:
                await conn.execute("ROLLBACK")
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0


@pytest.mark.asyncio
async def test_missing_account_or_binding_cannot_be_manufactured(env, directory):
    await prepare(env, directory)
    async with env.pool.acquire() as conn, conn.transaction():
        with pytest.raises(JournalError, match="conflict"):
            async with lock_account_control(conn, uuid4()):
                pytest.fail("unknown account")
    await env.pool.execute("DELETE FROM deletion_journal_binding")
    with pytest.raises(JournalError, match="marker_mismatch"):
        await persist(env, directory, request(env.alice), uuid4())
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_binding") == 0


@pytest.mark.asyncio
async def test_request_record_survives_account_cascade_without_applied_marker(env, directory):
    await prepare(env, directory)
    record = await persist(env, directory, request(env.alice), uuid4())
    # Test-only schema proof, NOT account purge implementation.
    await env.pool.execute("DELETE FROM users WHERE id=$1", env.alice)
    assert await env.pool.fetchval("SELECT id FROM account_deletions") == record.id
    assert await env.pool.fetchval("SELECT count(*) FROM account_suspension_causes") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0


@pytest.mark.asyncio
async def test_later_operation_blocked_by_earlier_unapplied_request(env, directory):
    await prepare(env, directory)
    first = request(env.alice, reset=True)
    start = NOW + timedelta(hours=1)
    second = request(env.alice)
    second = replace(second, decision_at=start, fields=replace(second.fields, generation=3))
    event = uuid4()
    with Journal(*directory) as journal:
        journal.append(first, uuid4(), None, None)
        journal.append(
            Command(
                env.alice,
                first.operation_id,
                "reset_report_ready",
                start,
                ReadyFields(start, start + timedelta(days=7)),
            ),
            uuid4(),
            1,
            1,
        )
        journal.append(
            Command(
                env.alice,
                first.operation_id,
                "reset_cancelled",
                start,
                TerminalFields((), 2, "owner"),
            ),
            uuid4(),
            2,
            2,
        )
        journal.append(second, event, 3, None)
        async with env.pool.acquire() as conn, conn.transaction():
            async with lock_account_control(conn, env.alice, second.operation_id) as scope:
                with pytest.raises(JournalError, match="recovery_required"):
                    await scope.verify_request(journal, event)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0


def _crash_writer(path, identity, command, event, schema, phase):
    async def run():
        conn = await asyncpg.connect(os.environ[DSN_ENV], server_settings={"search_path": schema})
        transaction = conn.transaction()
        await transaction.start()
        async with lock_account_control(conn, command.account_id, command.operation_id) as scope:
            with Journal(path, identity) as journal:
                journal.append(command, event, None, None)
                if phase == "after_append":
                    os._exit(76)
                verified = await scope.verify_request(journal, event)
                await record_request_facts(conn, verified)
                if phase == "before_commit":
                    os._exit(76)
            await transaction.commit()
            os._exit(76)  # committed without delivering a reply

    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_append", "before_commit", "after_commit"])
async def test_process_crash_append_commit_and_lost_reply(env, directory, phase):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    schema = await env.pool.fetchval("SHOW search_path")
    child = multiprocessing.get_context("spawn").Process(
        target=_crash_writer, args=(*directory, command, event, schema, phase)
    )
    child.start()
    try:
        await asyncio.to_thread(child.join, 20)
        assert child.exitcode == 76
        assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == int(
            phase == "after_commit"
        )
        record = await persist(env, directory, command, event)
        assert record.fence_generation == 1
        assert record.request_event_id == event
        assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1
        assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
        with Journal(*directory) as journal:
            assert len(journal.verify().entries) == 1
        with pytest.raises(JournalError, match="recovery_required"):
            await require_recovery_barrier(env.pool, settings(*directory))
    finally:
        if child.is_alive():
            child.terminate()
            await asyncio.to_thread(child.join, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_position", ["pre_request", "between_append_commit"])
async def test_actual_pre_request_snapshot_restores_original_generation_deadlines(
    env, directory, snapshot_position
):
    await prepare(env, directory)
    tables = (
        "entitlement_accounts",
        "account_deletions",
        "account_suspension_causes",
        "deletion_journal_applied",
    )
    dumps = {table: io.BytesIO() for table in tables}
    command, event = request(env.alice, reset=True), uuid4()
    async with env.pool.acquire() as writer:
        transaction = writer.transaction()
        await transaction.start()
        if snapshot_position == "between_append_commit":
            async with lock_account_control(writer, env.alice) as scope:
                with Journal(*directory) as journal:
                    journal.append(command, event, None, None)
                    await record_request_facts(writer, await scope.verify_request(journal, event))
        async with (
            env.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            await conn.fetchval("SELECT pg_export_snapshot()")
            for table, output in dumps.items():
                await conn.copy_from_table(table, output=output, format="binary")
        await transaction.rollback()
    original = await persist(env, directory, command, event)
    # Offline restoration of TEST-OWNED tables only. Journal is not restored.
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "TRUNCATE entitlement_accounts,account_deletions,deletion_journal_applied CASCADE"
        )
        for table, dump in dumps.items():
            dump.seek(0)
            await conn.copy_to_table(table, source=dump, format="binary")
    replay = await persist(env, directory, command, event)
    assert replay == original
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, settings(*directory))


@pytest.mark.asyncio
async def test_real_process_concurrent_same_identity_records_once(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    schema = await env.pool.fetchval("SHOW search_path")
    children = [
        multiprocessing.get_context("spawn").Process(
            target=_crash_writer,
            args=(*directory, command, event, schema, "after_commit"),
        )
        for _ in range(2)
    ]
    for child in children:
        child.start()
    try:
        await asyncio.gather(*(asyncio.to_thread(child.join, 20) for child in children))
        assert all(child.exitcode == 76 for child in children)
        assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 1
        assert await env.pool.fetchval("SELECT count(*) FROM account_suspension_causes") == 1
        assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
        with Journal(*directory) as journal:
            assert len(journal.verify().entries) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                await asyncio.to_thread(child.join, 5)


@pytest.mark.asyncio
async def test_advisory_collision_serializes_without_coalescing_accounts(
    env, directory, monkeypatch
):
    await prepare(env, directory)
    monkeypatch.setattr(store, "account_lock_key", lambda _: 123)
    commands = [request(env.alice), request(env.bob)]
    records = await asyncio.gather(
        *(persist(env, directory, command, uuid4()) for command in commands)
    )
    assert {record.account_id for record in records} == {env.alice, env.bob}
    assert len({record.id for record in records}) == 2
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 2


@pytest.mark.asyncio
async def test_savepoint_failure_rolls_back_only_helper_writes_even_if_caller_catches(
    env, directory
):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    await env.pool.execute(
        "CREATE FUNCTION reject_generation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'synthetic generation failure'; END $$; "
        "CREATE TRIGGER reject_generation BEFORE UPDATE OF content_generation ON entitlement_accounts "
        "FOR EACH ROW EXECUTE FUNCTION reject_generation()"
    )
    with Journal(*directory) as journal:
        journal.append(command, event, None, None)
        async with env.pool.acquire() as conn, conn.transaction():
            async with lock_account_control(conn, env.alice) as scope:
                verified = await scope.verify_request(journal, event)
                with pytest.raises(asyncpg.RaiseError):
                    await record_request_facts(conn, verified)
                await conn.execute(
                    "UPDATE users SET name='unrelated outer write' WHERE id=$1", env.bob
                )
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM account_suspension_causes") == 0
    assert (
        await env.pool.fetchval(
            "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == 0
    )
    assert (
        await env.pool.fetchval("SELECT name FROM users WHERE id=$1", env.bob)
        == "unrelated outer write"
    )


@pytest.mark.asyncio
async def test_rollback_savepoint_cannot_leave_stale_lock_authority(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    with Journal(*directory) as journal:
        journal.append(command, event, None, None)
        async with env.pool.acquire() as conn, conn.transaction():
            savepoint = conn.transaction()
            await savepoint.start()
            async with lock_account_control(conn, env.alice) as scope:
                verified = await scope.verify_request(journal, event)
                await savepoint.rollback()  # releases the locks acquired after the savepoint
                async with env.pool.acquire() as other, other.transaction():
                    assert (
                        await other.fetchval(
                            "SELECT pg_try_advisory_xact_lock($1)",
                            store.account_lock_key(env.alice),
                        )
                        is True
                    )
                    await other.execute(
                        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                        env.alice,
                    )
                with pytest.raises(JournalError, match="conflict"):
                    await record_request_facts(conn, verified)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0


@pytest.mark.asyncio
async def test_fake_or_mutated_verification_cannot_supply_other_request_facts(env, directory):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    with Journal(*directory) as journal:
        original = journal.append(command, event, None, None)
        async with env.pool.acquire() as conn, conn.transaction():
            async with lock_account_control(conn, env.alice) as scope:
                fake = object.__new__(store.VerifiedRequest)
                fake._scope = scope
                fake._entry = original
                with pytest.raises(JournalError, match="conflict"):
                    await record_request_facts(conn, fake)
                verified = await scope.verify_request(journal, event)
                verified._entry = replace(
                    original, command=replace(command, decision_at=NOW + timedelta(days=1))
                )
                record = await record_request_facts(conn, verified)
                assert (
                    record.requested_at == NOW
                )  # captured verified entry, not mutable caller field


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column",
    [
        "request_digest",
        "command_digest",
        "requested_at",
        "fence_generation",
        "drain_deadline",
        "revocation_deadline",
        "billing_deadline",
        "prior_reason",
        "reason",
    ],
)
async def test_contradictory_record_retry_is_refused(env, directory, column):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    await persist(env, directory, command, event)
    value = (
        bytes(32)
        if column.endswith("digest")
        else 7
        if column == "fence_generation"
        else "abuse"
        if column == "reason"
        else "operator"
        if column == "prior_reason"
        else NOW + timedelta(days=8)
    )
    if column == "prior_reason":
        # Preserve the SQL prior-status constraint while testing journal contradiction.
        await env.pool.execute("UPDATE account_deletions SET prior_status='suspended'")
    await env.pool.execute(f"UPDATE account_deletions SET {column}=$1", value)
    with pytest.raises(JournalError, match="conflict"):
        await persist(env, directory, command, event)


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["event_id", "account_id", "operation_id", "entry_digest"])
async def test_exact_marker_checks_cannot_be_replaced_by_sequence_presence(env, directory, column):
    await prepare(env, directory)
    command, event = request(env.alice), uuid4()
    with Journal(*directory) as journal:
        entry = journal.append(command, event, None, None)
    # Deliberately contradictory TEST-ONLY evidence; no real effect/applier claim.
    values = dict(
        journal_id=entry.journal_id,
        seq=entry.seq,
        event_id=entry.event_id,
        account_id=env.alice,
        operation_id=command.operation_id,
        entry_digest=entry.digest,
    )
    values[column] = bytes(32) if column == "entry_digest" else uuid4()
    await env.pool.execute(
        "INSERT INTO deletion_journal_applied(journal_id,seq,event_id,account_id,operation_id,entry_digest) "
        "VALUES($1,$2,$3,$4,$5,$6)",
        *values.values(),
    )
    with pytest.raises(JournalError, match="marker_mismatch"):
        await persist(env, directory, command, event)
    assert await env.pool.fetchval("SELECT count(*) FROM account_deletions") == 0


TITLE_GUARD_IMPORTS = {
    # Approved explicit isolated C1 composition only. Exact guard/lock/refusal
    # symbols, never deletion/reset request or phase mutation APIs.
    "orchestrator/artifact_namespaces.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
        ("orchestrator.services.account_control.store", "Connection"),
        ("orchestrator.services.account_control.store", "lock_account_control"),
    },
    "orchestrator/resource_store.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
        ("orchestrator.services.account_control.store", "Connection"),
        ("orchestrator.services.account_control.store", "lock_account_control"),
    },
    "orchestrator/resource_inputs.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "capture_generation"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
        ("orchestrator.services.account_control.store", "Connection"),
        ("orchestrator.services.account_control.store", "lock_account_control"),
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    "orchestrator/resource_catalog.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    "orchestrator/resource_presentations.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    "orchestrator/resource_http.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "capture_generation"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    "orchestrator/resource_csv.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    "orchestrator/routes/conversations.py": {
        ("orchestrator.services.account_control.guard", "WriteGuardRefusal"),
    },
    # Approved birth-only primitive access, NOT request persistence/applier APIs.
    "orchestrator/services/identity/account_service.py": {
        ("orchestrator.services.account_control.store", "Connection"),
    },
    "orchestrator/services/identity/initialization.py": {
        ("orchestrator.services.account_control.store", "Connection"),
        ("orchestrator.services.account_control.store", "account_lock_key"),
    },
    "orchestrator/main.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "capture_generation"),
    },
    "orchestrator/memory/store.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    "orchestrator/worker/jobs.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "WriteGuardRefusal"),
    },
    "orchestrator/daemon.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
    },
    "orchestrator/tasks/runner.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "WriteGuardRefusal"),
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    # Approved original-origin task/output and operator reinstatement seams.
    # Only the advisory key is exposed from the request store, never phase APIs.
    "orchestrator/entitlements/service.py": {
        ("orchestrator.services.account_control.store", "account_lock_key"),
        ("orchestrator.services.account_control.transaction", "control_transaction"),
    },
    "orchestrator/tasks/fence.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    # Source-only inference quarantine: exact fail-closed/owned-transaction seams.
    # No request/phase API access, production composition or general writer fence.
    "orchestrator/compute_runtime.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    "orchestrator/tasks/_invocation.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    "orchestrator/tasks/inference.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
        ("orchestrator.services.account_control.transaction", "control_transaction"),
    },
    # Explicit isolated composition/selectors use refusal only, never J1b APIs.
    "orchestrator/tasks/composition.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    "orchestrator/routes/tasks.py": {
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
    },
    "orchestrator/tasks/store.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "WriteGuardRefusal"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
        ("orchestrator.services.account_control.store", "account_lock_key"),
        ("orchestrator.services.account_control.transaction", "ObservationBlocked"),
        ("orchestrator.services.account_control.transaction", "control_transaction"),
    },
    "orchestrator/memory/extraction.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
    },
    "orchestrator/memory/summary.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "WriteGuardRefusal"),
    },
    "orchestrator/memory/entities.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
    },
    "orchestrator/memory/entity_projection.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "Connection"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
    # Approved queued fact/checkpoint seam only; request/phase APIs stay denied.
    # Its actual connection/lock/commit behavior is qualified by real PG tests.
    "orchestrator/memory/extraction_fencing.py": {
        ("orchestrator.services.account_control.guard", "CapturedGeneration"),
        ("orchestrator.services.account_control.guard", "Connection"),
        ("orchestrator.services.account_control.guard", "guard_content_write"),
    },
}


def _assert_only_title_guard_imports(sources):
    # J1c admits only these symbols at the exact approved named seams.
    # This is not permission to call the J1b request/phase store.
    observed = {}
    for path, source in sources.items():
        imports = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.update(
                    (node.module, name.name)
                    for name in node.names
                    if "account_control" in f"{node.module}.{name.name}"
                )
            elif isinstance(node, ast.Import):
                imports.update(
                    (name.name, "") for name in node.names if "account_control" in name.name
                )
        if imports:
            assert imports <= TITLE_GUARD_IMPORTS.get(path, set()), path
            observed[path] = imports
    assert observed == TITLE_GUARD_IMPORTS


def test_only_title_guard_runtime_imports_no_marker_status_or_phase_api():
    root = Path(__file__).resolve().parents[1]
    module = root / "orchestrator/services/account_control/store.py"
    source = module.read_text()
    assert "INSERT INTO deletion_journal_applied" not in source
    assert "SET status" not in source
    assert "SET closed_at" not in source
    _assert_only_title_guard_imports(
        {
            path.relative_to(root).as_posix(): path.read_text()
            for path in (root / "orchestrator").rglob("*.py")
            if "account_control" not in path.parts
        }
    )


@pytest.mark.parametrize(
    "path,extra",
    [
        (
            "orchestrator/resource_inputs.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/resource_store.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/resource_http.py",
            "from orchestrator.services.account_control.transaction import control_transaction",
        ),
        (
            "orchestrator/services/identity/initialization.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/services/identity/account_service.py",
            "from orchestrator.services.account_control.store import lock_account_control",
        ),
        (
            "orchestrator/main.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        ("orchestrator/main.py", "import orchestrator.services.account_control.store"),
        ("orchestrator/main.py", "from orchestrator.services import account_control"),
        (
            "orchestrator/main.py",
            "from orchestrator.services.account_control.guard import guard_content_write",
        ),
        (
            "orchestrator/memory/store.py",
            "from orchestrator.services.account_control.guard import *",
        ),
        (
            "orchestrator/unapproved_writer.py",
            "from orchestrator.services.account_control.guard import guard_content_write",
        ),
        (
            "orchestrator/memory/entity_projection.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/memory/entities.py",
            "from orchestrator.services.account_control.guard import capture_generation",
        ),
        (
            "orchestrator/memory/extraction_fencing.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/memory/extraction_fencing.py",
            "from orchestrator.services.account_control.guard import capture_generation",
        ),
        (
            "orchestrator/tasks/store.py",
            "from orchestrator.services.account_control.guard import capture_generation",
        ),
        (
            "orchestrator/tasks/store.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/entitlements/service.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/tasks/fence.py",
            "from orchestrator.services.account_control.transaction import control_transaction",
        ),
        (
            "orchestrator/unapproved_writer.py",
            "from orchestrator.services.account_control.transaction import control_transaction",
        ),
        (
            "orchestrator/compute_runtime.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/tasks/_invocation.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/tasks/inference.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
        (
            "orchestrator/unapproved_writer.py",
            "from orchestrator.services.account_control.transaction import ObservationBlocked",
        ),
        (
            "orchestrator/tasks/composition.py",
            "from orchestrator.services.account_control.transaction import control_transaction",
        ),
        (
            "orchestrator/routes/tasks.py",
            "from orchestrator.services.account_control.store import record_request_facts",
        ),
    ],
)
def test_runtime_import_contract_rejects_other_account_control_integration(path, extra):
    sources = {
        name: "\n".join(f"from {module} import {symbol}" for module, symbol in sorted(imports))
        for name, imports in TITLE_GUARD_IMPORTS.items()
    }
    _assert_only_title_guard_imports(sources)
    sources[path] = sources.get(path, "") + "\n" + extra
    with pytest.raises(AssertionError):
        _assert_only_title_guard_imports(sources)


def _cancel_scan_during_shutdown(path, identity):
    """Owned process: cancel the waiter and every new task, as loop shutdown does."""

    async def run():
        started, released, finished = threading.Event(), threading.Event(), threading.Event()
        with Journal(path, identity) as journal:
            verify = journal.verify

            def held_verify():
                started.set()
                assert released.wait(5)
                result = verify()
                finished.set()
                return result

            journal.verify = held_verify
            previous = asyncio.all_tasks()
            waiter = asyncio.create_task(store._scan(journal))
            assert await asyncio.to_thread(started.wait, 5)
            for task in asyncio.all_tasks() - previous:
                task.cancel()
            released.set()
            try:
                await waiter
            except asyncio.CancelledError:
                # Thread must have finished its actual verification before the
                # outer owner can close the journal's descriptors.
                assert finished.is_set()
                os._exit(76)
            pytest.fail("shutdown cancellation was swallowed")

    asyncio.run(run())


@pytest.mark.asyncio
async def test_all_task_shutdown_cancellation_does_not_spin(directory):
    child = multiprocessing.get_context("spawn").Process(
        target=_cancel_scan_during_shutdown, args=directory
    )
    child.start()
    try:
        await asyncio.to_thread(child.join, 10)
        assert child.exitcode == 76
    finally:
        if child.is_alive():
            child.terminate()
            await asyncio.to_thread(child.join, 5)


def _scan_failure_in_owned_loop(scenario, failure, passed):
    """Owned process: the journal's synchronous verify ends with ``failure`` (#516)."""

    context = contextvars.ContextVar("account_control_scan_failure", default="unset")

    async def run():
        unretrieved: list[str] = []

        def report(_loop, ctx):
            # Only a failure nobody ever retrieved; asyncio's own shield report
            # of a failure under a cancelled waiter is expected.
            if "never retrieved" in ctx.get("message", ""):
                unretrieved.append(ctx["message"])

        asyncio.get_running_loop().set_exception_handler(report)
        context.set("caller")
        error: BaseException | None = None
        if failure == "cancelled":
            error = asyncio.CancelledError("raised by verify")
        elif failure == "os":
            error = OSError("fictional I/O failure")
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        class FailingJournal:
            def verify(self):
                assert context.get() == "caller"
                context.set("thread")
                started.set()
                try:
                    if scenario != "callable":
                        assert release.wait(5)
                    if error is not None:
                        raise error
                    return "view"
                finally:
                    finished.set()

        waiter = asyncio.create_task(store._scan(cast(Journal, FailingJournal())))
        if scenario == "cancel_after_done":
            assert await asyncio.to_thread(started.wait, 5)
            release.set()
            # Block the loop until the verification has ended and its result is
            # posted, so the cancellation lands on a finished future.
            assert finished.wait(5)
            time.sleep(0.1)
            waiter.cancel()
        elif scenario == "caller_cancels":
            assert await asyncio.to_thread(started.wait, 5)
            for _ in range(2):
                waiter.cancel()
                await asyncio.sleep(0)
            assert not waiter.done() and not finished.is_set()
            release.set()
        try:
            await waiter
        except BaseException as raised:
            caught = raised
        else:
            raise AssertionError("the caller's cancellation or verify's failure was lost")
        assert finished.is_set(), "cancellation abandoned live verification"
        if scenario != "callable":
            assert isinstance(caught, asyncio.CancelledError) and caught is not error
        else:
            assert caught is error
        assert context.get() == "caller"
        del waiter, caught
        gc.collect()
        assert not unretrieved, unretrieved
        passed.set()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("scenario", "failure"),
    [
        ("callable", "cancelled"),
        ("caller_cancels", "cancelled"),
        ("caller_cancels", "os"),
        ("cancel_after_done", "cancelled"),
        ("cancel_after_done", "os"),
        ("cancel_after_done", "none"),
    ],
)
def test_cancelled_error_raised_by_verify_ends_the_scan(scenario, failure):
    """#516: as for the barrier helper, verify's own CancelledError is the
    scan's outcome, never caller cancellation awaited again forever."""
    context = multiprocessing.get_context("spawn")
    passed = context.Event()
    child = context.Process(target=_scan_failure_in_owned_loop, args=(scenario, failure, passed))
    child.start()
    try:
        child.join(10)
        assert not child.is_alive(), "the scan spun on a finished verification"
        assert child.exitcode == 0 and passed.is_set()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        assert not child.is_alive(), "owned regression subprocess did not stop"
        child.close()


@pytest.mark.asyncio
async def test_recorded_suspension_into_active_older_snapshot_is_facts_only(env, directory):
    await prepare(env, directory)
    cause = Cause("billing", uuid4())
    command, event = request(env.alice), uuid4()
    assert isinstance(command.fields, RequestFields)
    command = replace(
        command,
        fields=replace(
            command.fields,
            prior_status="suspended",
            prior_reason="billing",
            causes=command.fields.causes + (cause,),
        ),
    )
    assert await env.pool.fetchval("SELECT count(*) FROM account_suspension_causes") == 0
    record = await persist(env, directory, command, event)
    assert record.prior_status == "suspended"
    assert (
        await env.pool.fetchval(
            "SELECT status FROM entitlement_accounts WHERE user_id=$1", env.alice
        )
        == "active"
    )
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM account_suspension_causes WHERE cause_id=$1", cause.id
        )
        == 1
    )
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    with pytest.raises(JournalError, match="recovery_required"):
        await require_recovery_barrier(env.pool, settings(*directory))
