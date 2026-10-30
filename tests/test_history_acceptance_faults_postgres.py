"""Owned SQL/POSIX fault proof for uncalled storage APIs, never runtime readiness."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest
from asyncpg.transaction import Transaction

from orchestrator.services.account_control.transaction import (
    TransactionUnavailable,
    control_transaction,
)
from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    Command,
    HistoryError,
    decode_entry,
    encode_entry,
)
from orchestrator.services.restore_history.projection import compare_projections
from orchestrator.tasks._invocation import InferenceInvocation, invocation
from tests.restore_history_support import HISTORY, ZERO, accept
from tests.test_history_acceptance_postgres import (
    CANDIDATE,
    PAGE,
    PROJECT,
    adapter_for,
    native_task,
    projection_db as projection_db,
    db as db,
    metadata_cluster as metadata_cluster,
)
from tests.test_restore_history_store import owned, open_store


def deadline():
    return asyncio.get_running_loop().time() + 5


class Borrowed:
    """Tripwires prove the adapter does not take over caller resources."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def transaction(self, *_args, **_kwargs):
        raise AssertionError("adapter acquired transaction ownership")

    def close(self):
        raise AssertionError("adapter closed borrowed connection")

    def terminate(self):
        raise AssertionError("adapter terminated borrowed connection")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "lost_result",
        "cancel",
        "bad_created",
        "bad_digest",
        "no_result",
        "extra_field",
        "late_result",
    ],
)
async def test_staged_result_failure_never_acknowledges_or_retries(
    projection_db, monkeypatch, fault
):
    value = await native_task(projection_db)
    adapter = adapter_for()
    calls = []
    cancelled = asyncio.CancelledError()
    late = False
    original_time = adapter._time

    def check_time(limit):
        if late:
            raise HistoryError("projection_deadline")
        original_time(limit)

    monkeypatch.setattr(adapter, "_time", check_time)

    class Fault(Borrowed):
        async def fetchrow(self, sql, *args):
            nonlocal late
            row = await self.conn.fetchrow(sql, *args)
            if sql == PROJECT:
                calls.append("project")
                if fault == "lost_result":
                    raise ConnectionError("synthetic staged result lost")
                if fault == "cancel":
                    raise cancelled
                if fault == "late_result":
                    late = True
                    return row
                if fault == "no_result":
                    return None
                row = dict(row)
                if fault == "bad_created":
                    row["created"] = "true"
                elif fault == "bad_digest":
                    row["digest"] = bytes(32)
                elif fault == "extra_field":
                    row["extra"] = "forbidden"
            return row

    state = InferenceInvocation()
    token = invocation.set(state)
    try:
        async with projection_db.connection("md_acceptor") as conn:
            with pytest.raises(
                asyncio.CancelledError if fault == "cancel" else HistoryError
            ) as caught:
                async with conn.transaction():
                    await adapter.stage(Fault(conn), value.entry, deadline=deadline())
            assert not conn.is_in_transaction()
        if fault == "cancel":
            assert caught.value is cancelled
        else:
            assert isinstance(caught.value, HistoryError)
            assert caught.value.uncertain
        assert state.uncertainty == "history_projection_unavailable"
        assert calls == ["project"]
    finally:
        invocation.reset(token)
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_caller_commit_acknowledgement_loss_keeps_history_and_native_outcome(
    projection_db, tmp_path, monkeypatch, committed
):
    value = await native_task(projection_db)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    with open_store(path, identity, ownership) as history:
        recorded = history.append(
            value.entry.command, deadline=asyncio.get_running_loop().time() + 5
        )
        assert recorded.entry == value.entry
        original_commit = Transaction.commit
        original_rollback = Transaction.rollback
        armed = False

        async def commit(tx):
            nonlocal armed
            if not armed:
                return await original_commit(tx)
            armed = False
            if committed:
                await original_commit(tx)
            else:
                await original_rollback(tx)
            raise ConnectionError("synthetic native COMMIT acknowledgement lost")

        monkeypatch.setattr(Transaction, "commit", commit)
        pool = await asyncpg.create_pool(
            host=str(projection_db.host),
            user="md_acceptor",
            password="",
            database=projection_db.name,
            ssl=False,
            min_size=0,
            max_size=1,
        )
        state = InferenceInvocation()
        token = invocation.set(state)
        try:
            with pytest.raises(TransactionUnavailable) as caught:
                async with control_transaction(pool) as conn:
                    staged = await adapter_for().stage(
                        Borrowed(conn), value.entry, deadline=deadline()
                    )
                    assert staged.inserted and conn.is_in_transaction()
                    armed = True
            assert caught.value.commit_attempted and not caught.value.commit_acknowledged
            assert state.uncertainty is not None
        finally:
            invocation.reset(token)
            monkeypatch.setattr(Transaction, "commit", original_commit)
            await asyncio.wait_for(pool.close(), 3)
        assert history.verify(deadline=asyncio.get_running_loop().time() + 5).entries == (
            recorded.entry,
        )
        async with projection_db.connection("md_app") as conn:
            async with conn.transaction():
                facts = await adapter_for().page(Borrowed(conn), after_seq=0, deadline=deadline())
        assert len(facts) == int(committed)
        # A recovered fact/duplicate is never a revived native invocation.
        assert not history.append(
            value.entry.command, deadline=asyncio.get_running_loop().time() + 5
        ).created


@pytest.mark.asyncio
async def test_old_owned_database_restore_loses_projection_but_retains_h(projection_db, tmp_path):
    resource = projection_db
    cluster = resource.cluster
    assert resource.name in cluster.databases and cluster.container is not None
    snapshot = "md_hprojection_" + uuid4().hex
    assert (
        cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{resource.name}'") == "0"
    )
    cluster.sql(f'CREATE DATABASE "{snapshot}" TEMPLATE "{resource.name}"')
    cluster.databases.add(snapshot)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    try:

        async def hook(conn, frame):
            await adapter_for().stage(Borrowed(conn), decode_entry(frame), deadline=deadline())

        value = await native_task(resource, hook=hook)
        with open_store(path, identity, ownership) as history:
            # Historical observation only. This test does not wire H OP_LOCK or
            # create a fresh dispatch receipt in the native acceptance handler.
            history.append(value.entry.command, deadline=asyncio.get_running_loop().time() + 5)
            assert (
                cluster.sql(
                    f"SELECT count(*) FROM pg_stat_activity WHERE datname='{resource.name}'"
                )
                == "0"
            )
            cluster.sql(f'DROP DATABASE "{resource.name}"')
            cluster.sql(f'CREATE DATABASE "{resource.name}" TEMPLATE "{snapshot}"')
            async with resource.connection("md_app") as conn:
                async with conn.transaction():
                    facts = await adapter_for().page(
                        Borrowed(conn), after_seq=0, deadline=deadline()
                    )
            state = history.verify(deadline=asyncio.get_running_loop().time() + 5)
            assert compare_projections(HISTORY, state, facts).missing == (1,)
            async with resource.connection() as conn:
                assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0
                assert await conn.fetchval("SELECT count(*) FROM tasks") == 0
    finally:
        cluster.sql(f'DROP DATABASE "{snapshot}"')
        cluster.databases.remove(snapshot)


@pytest.mark.asyncio
async def test_bounded_pages_and_closed_row_validation(projection_db):
    entries = []
    previous = ZERO
    seed = accept().data
    assert isinstance(seed, AcceptPrepare)
    async with projection_db.connection() as owner:
        async with owner.transaction():
            for seq in range(1, 34):
                data = replace(
                    seed,
                    account_id=uuid4(),
                    task_id=uuid4(),
                    conversation_id=uuid4(),
                    user_message_id=uuid4(),
                    result_message_id=uuid4(),
                    key_commitment=f"{seq:064x}",
                )
                entry = decode_entry(encode_entry(HISTORY, seq, previous, Command(uuid4(), data)))
                frame = encode_entry(HISTORY, seq, previous, entry.command)
                previous = entry.digest
                entries.append(entry)
                # Synthetic schema-owner seed for pagination, not A authority.
                # It models retained rows whose native account/task has gone.
                await owner.execute(
                    "INSERT INTO task_history_acceptance VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)",
                    HISTORY,
                    seq,
                    entry.command.record_id,
                    entry.digest,
                    frame,
                    data.account_id,
                    data.account_generation,
                    data.task_id,
                    data.conversation_id,
                    data.user_message_id,
                    data.result_message_id,
                    1,
                    1,
                    data.key_commitment,
                    data.request_commitment,
                    data.request_schema,
                )
    async with projection_db.connection("md_app") as conn:
        async with conn.transaction():
            first = await adapter_for().page(Borrowed(conn), after_seq=0, deadline=deadline())
            last = await adapter_for().page(
                Borrowed(conn), after_seq=first[-1].seq, deadline=deadline()
            )
            empty = await adapter_for().page(
                Borrowed(conn), after_seq=last[-1].seq, deadline=deadline()
            )
            assert (
                [f.seq for f in first] == list(range(1, 33))
                and [f.seq for f in last] == [33]
                and not empty
            )

    class Corrupt(Borrowed):
        async def fetch(self, sql, *args):
            rows = await self.conn.fetch(sql, *args)
            if sql == PAGE:
                rows = [dict(r) for r in rows]
                rows[0]["digest"] = bytes(32)
            return rows

    async with projection_db.connection("md_app") as conn:
        with pytest.raises(HistoryError):
            async with conn.transaction():
                await adapter_for().page(Corrupt(conn), after_seq=0, deadline=deadline())


@pytest.mark.asyncio
async def test_native_lock_timeout_refuses_without_extending_deadline(projection_db):
    value = await native_task(projection_db)
    async with projection_db.connection() as owner:
        async with owner.transaction():
            await owner.fetchval(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                value.account,
            )
            async with projection_db.connection("md_acceptor") as conn:
                with pytest.raises(HistoryError) as caught:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL lock_timeout='80ms'")
                        await adapter_for().stage(Borrowed(conn), value.entry, deadline=deadline())
                assert caught.value.uncertain
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0


@pytest.mark.asyncio
async def test_unsafe_default_grants_abort_candidate_atomically(db):
    db.sql(
        "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner IN SCHEMA public GRANT SELECT ON TABLES TO md_third"
    )
    with pytest.raises(AssertionError, match="task_history_unsafe_default_privileges"):
        db.sql("BEGIN;" + CANDIDATE.read_text() + "COMMIT;")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT to_regclass('public.task_history_binding')") is None


@pytest.mark.asyncio
async def test_expired_deadline_and_missing_transaction_do_not_borrow_resources():
    entry = decode_entry(encode_entry(HISTORY, 1, ZERO, accept()))

    class NoConnection:
        def is_in_transaction(self):
            return False

        def __getattr__(self, _name):
            raise AssertionError("unexpected native operation")

    for operation in ["page", "stage"]:
        args = {"after_seq": 0} if operation == "page" else {"entry": entry}
        method = getattr(adapter_for(), operation)
        with pytest.raises(HistoryError, match="projection_deadline"):
            await method(NoConnection(), deadline=asyncio.get_running_loop().time() - 1, **args)
        with pytest.raises(HistoryError, match="projection_transaction_required"):
            await method(NoConnection(), deadline=deadline(), **args)


@pytest.mark.asyncio
async def test_real_task_cancellation_during_native_row_lock_keeps_caller_cleanup(projection_db):
    value = await native_task(projection_db)
    started = asyncio.Event()
    state = InferenceInvocation()

    class Notify(Borrowed):
        async def fetchrow(self, sql, *args):
            if sql == PROJECT:
                started.set()
            return await self.conn.fetchrow(sql, *args)

    async with (
        projection_db.connection() as owner,
        projection_db.connection("postgres") as observer,
    ):
        async with owner.transaction():
            await owner.fetchval(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                value.account,
            )
            async with projection_db.connection("md_acceptor") as conn:

                async def project():
                    async with conn.transaction():
                        await adapter_for().stage(Notify(conn), value.entry, deadline=deadline())

                token = invocation.set(state)
                task = None
                try:
                    task = asyncio.create_task(project())
                    await asyncio.wait_for(started.wait(), 2)
                    await projection_db.wait_lock(observer, conn.get_server_pid())
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert not conn.is_in_transaction()
                    assert state.uncertainty == "history_projection_unavailable"
                finally:
                    invocation.reset(token)
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0


@pytest.mark.asyncio
async def test_unsafe_default_function_grant_aborts_candidate_atomically(db):
    db.sql(
        "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO md_third"
    )
    with pytest.raises(AssertionError, match="task_history_unsafe_default_privileges"):
        db.sql("BEGIN;" + CANDIDATE.read_text() + "COMMIT;")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT to_regclass('public.task_history_binding')") is None
        assert (
            await conn.fetchval(
                "SELECT to_regprocedure('public.task_history_acceptance_project(bytea)')"
            )
            is None
        )
