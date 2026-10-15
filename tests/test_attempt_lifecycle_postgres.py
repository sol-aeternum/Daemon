"""Owned disposable real-PostgreSQL qualification, never an application DSN.

Explicit --compute-metadata-postgres opt-in reuses the bounded owned cluster and
wire-fault bridge. All scenarios below execute real SQL/driver and existing writers.
"""

import asyncio
from dataclasses import replace
from datetime import datetime
import json
import os
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator import attempt_lifecycle as lc
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.store import account_lock_key
from orchestrator.tasks.store import TaskStore
from tests.compute_metadata_support import (
    Bridge,
    Database,
    ROOT,
    metadata_cluster as metadata_cluster,
)

SQL = (ROOT / "migrations/049_attempt_lifecycle.sql").read_text()
PUBLIC_FUNCTIONS = lc._SIGNATURES[3:]


@pytest_asyncio.fixture
async def db(metadata_cluster):
    cluster = metadata_cluster
    name = "md_" + uuid4().hex
    cluster.prepare(name)
    cluster.sql("BEGIN;" + SQL + "COMMIT;", database=name, role="md_owner")
    cluster.sql(
        ";".join(
            "GRANT EXECUTE ON FUNCTION public.attempt_lifecycle_" + s + " TO md_app"
            for s in PUBLIC_FUNCTIONS
        ),
        database=name,
        role="md_owner",
    )
    resource = cluster.resource / ("l-" + uuid4().hex[:6])
    resource.mkdir(mode=0o700)
    assert len(os.fsencode(resource / ".s.PGSQL.5432")) < 108
    bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
    await bridge.start()
    database = Database(cluster, name, resource, bridge)
    try:
        yield database
    finally:
        await bridge.close()
        async with asyncio.timeout(3):
            while (
                cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'") != "0"
            ):
                await asyncio.sleep(0.01)
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.sql("CHECKPOINT")
        cluster.databases.remove(name)
        cluster.record("lifecycle_database_reconciled", database=name, connections=0)


async def seed(db, owner, *, account=None):
    account = account or await db.account(owner)
    conversation, user_message, result_message, task, scope, metadata = [uuid4() for _ in range(6)]
    enc = ContentEncryption(Fernet.generate_key().decode())
    await owner.execute(
        "INSERT INTO conversations(id,user_id,pipeline) VALUES($1,$2,'cloud')",
        conversation,
        account,
    )
    await owner.execute(
        "INSERT INTO messages(id,conversation_id,user_id,role,content) VALUES($1,$3,$4,'user',$5),($2,$3,$4,'assistant',$5)",
        user_message,
        result_message,
        conversation,
        account,
        enc.encrypt("synthetic"),
    )
    await owner.execute(
        "INSERT INTO tasks(id,user_id,conversation_id,user_message_id,result_message_id,input_ciphertext,request_hash) VALUES($1,$2,$3,$4,$5,$6,$7)",
        task,
        account,
        conversation,
        user_message,
        result_message,
        enc.encrypt("{}"),
        "0" * 64,
    )
    # Existing writer methods, not copied SQL. Owner pool only for synthetic setup.
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_owner",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
    )
    memory = MemoryStore(pool, enc)
    tasks = TaskStore(pool, enc, memory)
    try:
        claim = await tasks.claim(task, worker_id="fixture", lease_s=45)
        assert claim is not None
        await tasks.record_compute_scope(task, claim.epoch, scope)
        await owner.execute("SELECT public.compute_metadata_create_scope($1,$2)", account, metadata)
        ident = lc.AttemptIdentity(uuid4(), account, 0, task, claim.epoch, scope, metadata, uuid4())
        return ident, conversation, pool, tasks, memory
    except BaseException:
        await pool.close()
        raise


async def raw(conn, op, ident, cmd=None, why="requested"):
    args = (
        (ident.wire(),)
        if op == "assert_open"
        else (ident.wire(), cmd, why)
        if op == "close"
        else (ident.wire(), cmd)
    )
    params = (
        "$1::jsonb"
        if op == "assert_open"
        else "$1::jsonb,$2::uuid,$3::text"
        if op == "close"
        else "$1::jsonb,$2::uuid"
    )
    return await conn.fetchval(f"SELECT public.attempt_lifecycle_{op}({params})", *args)


@pytest.mark.asyncio
async def test_real_identity_replay_closure_missing_account_and_namespaces(db):
    async with db.connection() as owner, db.pool() as pool:
        ident, _, writer_pool, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            cmd = uuid4()
            first = await store.register(ident, cmd)
            assert first["control"]["revision"] == 1 and first["references_current"]
            anchor = await owner.fetchval(
                "SELECT started_at FROM task_attempts WHERE task_id=$1", ident.task_id
            )
            assert datetime.fromisoformat(first["control"]["attempt_started_at"]) == anchor
            assert await store.register(ident, cmd) == first
            for bad in (
                replace(ident, account_id=uuid4()),
                replace(ident, epoch=2),
                replace(ident, producer_instance_id=uuid4()),
                replace(
                    ident,
                    financial_scope_id=ident.metadata_scope_id,
                    metadata_scope_id=ident.financial_scope_id,
                ),
            ):
                with pytest.raises(lc.LifecycleError):
                    await store.register(bad, cmd)
            async with db.connection("md_app") as app:
                assert await app.fetchval(lc.ROLE_PRECONDITION) is True
                async with app.transaction():
                    await lc.assert_open(app, ident)
                # SQL itself, without Python preflight, rejects changed identities.
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", replace(ident, generation=1))
                with pytest.raises(lc.LifecycleError, match="transaction_required"):
                    await lc.assert_open(app, ident)
            close_cmd = uuid4()
            closed = await store.close(ident, close_cmd)
            assert closed["control"]["revision"] == 2 and closed["receipt"]["opcode"] == "close"
            replay = await store.register(ident, cmd)
            assert replay["receipt"] == first["receipt"] and replay["control"] == closed["control"]
            with pytest.raises(lc.LifecycleError):
                await store.close(ident, close_cmd, "cancelled")
            async with db.connection("md_app") as app:
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
            # No cascade from old account/task/metadata to retained control/history.
            await owner.execute("DELETE FROM users WHERE id=$1", ident.account_id)
            result = await store.readback(ident, cmd)
            assert (
                result["control"] == closed["control"]
                and not result["account_present"]
                and not result["references_current"]
            )
            assert (await store.close(ident, uuid4()))["control"] == closed["control"]
            with pytest.raises(lc.LifecycleError):
                await store.readback(replace(ident, producer_instance_id=uuid4()), cmd)
            with pytest.raises(lc.LifecycleError):
                await store.readback(replace(ident, lifecycle_id=uuid4()), uuid4())
        finally:
            await writer_pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "generation",
        "cancel",
        "ended",
        "expired",
        "metadata",
        "scope",
        "epoch",
        "missing_account",
        "cause",
        "operation",
    ],
)
@pytest.mark.parametrize("registered", [False, True])
async def test_gate_and_registration_recheck_actual_references(db, field, registered):
    async with db.connection() as owner, db.pool() as pool:
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            cmd = uuid4()
            if registered:
                await store.register(ident, cmd)
            changes = {
                "generation": (
                    "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                    ident.account_id,
                ),
                "cancel": ("UPDATE tasks SET cancel_requested_at=now() WHERE id=$1", ident.task_id),
                "ended": (
                    "UPDATE task_attempts SET outcome='lost',ended_at=now() WHERE task_id=$1",
                    ident.task_id,
                ),
                "expired": (
                    "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                    ident.task_id,
                ),
                "metadata": (
                    "DELETE FROM compute_scope_metadata WHERE id=$1",
                    ident.metadata_scope_id,
                ),
                "scope": (
                    "UPDATE task_attempts SET compute_scope_id=NULL WHERE task_id=$1",
                    ident.task_id,
                ),
                "epoch": ("UPDATE tasks SET lease_epoch=2 WHERE id=$1", ident.task_id),
                "missing_account": (
                    "DELETE FROM entitlement_accounts WHERE user_id=$1",
                    ident.account_id,
                ),
                "cause": (
                    "INSERT INTO account_suspension_causes(account_id,cause_id,cause_code) VALUES($1,gen_random_uuid(),'operator')",
                    ident.account_id,
                ),
            }
            if field == "operation":
                await owner.execute(
                    "INSERT INTO deletion_journal_binding(singleton,journal_id) VALUES(true,$1)",
                    uuid4(),
                )
                await owner.execute(
                    "INSERT INTO account_deletions(id,account_id,kind,journal_id,request_seq,request_event_id,request_digest,command_digest,requested_at,prior_status,fence_generation,drain_deadline,revocation_deadline,billing_deadline) SELECT gen_random_uuid(),$1,'reset',journal_id,1,gen_random_uuid(),decode(repeat('00',32),'hex'),decode(repeat('00',32),'hex'),now(),'active',1,now(),now(),now() FROM deletion_journal_binding",
                    ident.account_id,
                )
            else:
                await owner.execute(*changes[field])
            async with db.connection("md_app") as app:
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
                # No pre-existing uniqueness collision in the unregistered case:
                # stale references themselves must reject an original registration.
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "register", replace(ident, lifecycle_id=uuid4()), uuid4())
            if registered:
                assert not (await store.readback(ident, cmd))["references_current"]
                # Replaying history never repairs references or reopens anything.
                assert (await store.register(ident, cmd))["receipt"]["command_id"] == str(cmd)
            else:
                assert await owner.fetchval("SELECT count(*) FROM attempt_lifecycle_control") == 0
                assert await owner.fetchval("SELECT count(*) FROM attempt_lifecycle_commands") == 0
        finally:
            await wp.close()


@pytest.mark.asyncio
async def test_current_identity_uniqueness_cross_account_mix_and_pool_slot_recovery(db):
    async with (
        db.connection() as owner,
        db.pool(max_size=1) as pool,
        db.connection("md_app") as app,
    ):
        ident, _, wp, _, _ = await seed(db, owner)
        other, _, wp2, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            await store.register(ident, uuid4())
            await store.register(other, uuid4())
            bads = [
                replace(ident, lifecycle_id=uuid4()),  # current task/epoch collision
                replace(
                    other, lifecycle_id=uuid4(), account_id=ident.account_id
                ),  # both accounts exist, wrong task
                replace(
                    other, lifecycle_id=uuid4(), metadata_scope_id=ident.metadata_scope_id
                ),  # foreign metadata
            ]
            # Make a fresh live attempt use an already mapped owned scope. Both
            # row identities are valid; only prospective one-to-one uniqueness fails.
            third, _, wp3, _, _ = await seed(db, owner, account=ident.account_id)
            try:
                await owner.execute(
                    "UPDATE task_attempts SET compute_scope_id=$1 WHERE task_id=$2",
                    ident.financial_scope_id,
                    third.task_id,
                )
                bad = replace(third, financial_scope_id=ident.financial_scope_id)
                assert await owner.fetchval(
                    "SELECT public.attempt_lifecycle_live($1::jsonb)", bad.wire()
                )
                with pytest.raises(lc.LifecycleError):
                    await store.register(bad, uuid4())
                await owner.execute(
                    "UPDATE task_attempts SET compute_scope_id=$1 WHERE task_id=$2",
                    third.financial_scope_id,
                    third.task_id,
                )
                bad = replace(third, metadata_scope_id=ident.metadata_scope_id)
                assert await owner.fetchval(
                    "SELECT public.attempt_lifecycle_live($1::jsonb)", bad.wire()
                )
                with pytest.raises(lc.LifecycleError):
                    await store.register(bad, uuid4())
                for bad in bads:
                    with pytest.raises(lc.LifecycleError):
                        await store.register(bad, uuid4())
                    # terminate() must release max_size=1 holder even on failure.
                    assert (await store.readback(ident, uuid4()))["control"] is not None
                # Cross-namespace UUID equality is legal: separate typed columns
                # are NOT one global UUID namespace. Swapped references still fail.
                await owner.execute(
                    "UPDATE compute_scope_metadata SET id=$1 WHERE id=$2",
                    third.financial_scope_id,
                    third.metadata_scope_id,
                )
                third = replace(third, metadata_scope_id=third.financial_scope_id)
                assert (await store.register(third, uuid4()))["references_current"]
                assert await owner.fetchval("SELECT count(*) FROM attempt_lifecycle_control") == 3
            finally:
                await wp3.close()
            async with owner.transaction():
                await owner.execute(
                    "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                    ident.account_id,
                )
                async with app.transaction():
                    with pytest.raises(lc.LifecycleError) as refused:
                        await lc.assert_open(app, ident)
                    assert refused.value.sqlstate == "55P03"
            assert (await store.readback(ident, uuid4()))["control"] is not None
        finally:
            await wp.close()
            await wp2.close()


@pytest.mark.asyncio
async def test_account_advisory_exact_key_and_privilege_envelope(db):
    async with (
        db.connection() as owner,
        db.pool() as pool,
        db.connection("md_app") as app,
        db.connection("postgres") as admin,
    ):
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            async with owner.transaction():
                await owner.execute(
                    "SELECT pg_advisory_xact_lock($1)", account_lock_key(ident.account_id)
                )
                with pytest.raises(lc.LifecycleError) as error:
                    await store.register(ident, uuid4())
                assert error.value.sqlstate == "55P03"
            await store.register(ident, uuid4())
            for table in ("attempt_lifecycle_control", "attempt_lifecycle_commands"):
                for sql in (
                    f"SELECT * FROM public.{table}",
                    f"DELETE FROM public.{table}",
                    f"UPDATE public.{table} SET "
                    + ("revision=1" if table.endswith("control") else "reason='stale'"),
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await app.execute(sql)
                await owner.execute(f"GRANT SELECT(revision) ON public.{table} TO md_app")
                assert await app.fetchval(lc.ROLE_PRECONDITION) is False
                await owner.execute(f"REVOKE SELECT(revision) ON public.{table} FROM md_app")
            for helper in lc._SIGNATURES[:3]:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(
                        "SELECT public.attempt_lifecycle_" + helper.split("(")[0] + "($1::jsonb)",
                        ident.wire(),
                    )
            async with db.connection("md_third") as third:
                for op in ("register", "close", "readback", "assert_open"):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await raw(third, op, ident, uuid4())
            assert await owner.fetchval(lc.ROLE_PRECONDITION) is False
            await admin.execute(
                "GRANT md_owner TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE"
            )
            try:
                assert await app.fetchval(lc.ROLE_PRECONDITION) is False
            finally:
                await admin.execute("REVOKE md_mid FROM md_app; REVOKE md_owner FROM md_mid")
            assert await app.fetchval(lc.ROLE_PRECONDITION) is True
            await app.execute("CREATE TEMP TABLE tasks(id uuid); SET search_path=pg_temp")
            await raw(app, "assert_open", ident)
            async with app.transaction(isolation="repeatable_read"):
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
        finally:
            await wp.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_first", [False, True])
async def test_same_connection_gate_close_and_separate_connection_negative(db, close_first):
    async with db.connection() as owner, db.pool() as pool, db.connection("md_app") as app:
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            await store.register(ident, uuid4())
            await owner.execute(
                "CREATE TABLE public.lc_witness(id int); GRANT INSERT ON public.lc_witness TO md_app"
            )
            async with app.transaction():
                if close_first:
                    await raw(app, "close", ident, uuid4())
                    with pytest.raises(asyncpg.RaiseError):
                        async with app.transaction():
                            await raw(app, "assert_open", ident)
                else:
                    before = await app.fetchval("SHOW lock_timeout")
                    await lc.assert_open(app, ident)
                    assert await app.fetchval("SHOW lock_timeout") == before
                    # Separate pool cannot join the caller's transaction or locks.
                    with pytest.raises(lc.LifecycleError) as e:
                        await store.close(ident, uuid4())
                    assert e.value.sqlstate == "55P03"
                    await app.execute("INSERT INTO public.lc_witness VALUES(1)")
            if not close_first:
                await store.close(ident, uuid4())
                assert await owner.fetchval("SELECT count(*) FROM lc_witness") == 1
            async with app.transaction():
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
        finally:
            await wp.close()


@pytest.mark.asyncio
async def test_savepoint_rollback_revalidation_and_close_rollback(db):
    async with db.connection() as owner, db.pool() as pool, db.connection("md_app") as app:
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            await store.register(ident, uuid4())
            async with app.transaction():
                save = app.transaction()
                await save.start()
                await lc.assert_open(app, ident)
                await save.rollback()
                # Locks acquired inside savepoint are gone. Another conn can close.
                await store.close(ident, uuid4())
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
            ident2, _, wp2, _, _ = await seed(db, owner)
            try:
                await store.register(ident2, uuid4())
                cmd = uuid4()
                tx = app.transaction()
                await tx.start()
                await raw(app, "close", ident2, cmd)
                await tx.rollback()
                result = await store.readback(ident2, cmd)
                assert result["receipt"] is None and result["control"]["closed_at"] is None
            finally:
                await wp2.close()
        finally:
            await wp.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["before", "after", "failed", "decode", "release", "missing_receipt", "mismatched_receipt"],
)
async def test_real_commit_wire_faults_and_postcommit_failures(db, mode, monkeypatch):
    async with db.connection() as owner, db.pool(max_size=1) as pool:
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            cmd = uuid4()
            store = lc.AttemptLifecycleStore(pool)
            if mode in ("before", "after"):
                db.bridge.commit_fault = mode
            if mode == "failed":
                await owner.execute(
                    "CREATE FUNCTION lc_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'deliberate deferred commit rejection'; END $$; CREATE CONSTRAINT TRIGGER lc_fail AFTER INSERT ON attempt_lifecycle_commands DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lc_fail()"
                )
            if mode == "decode":

                def bad_decode(*args):
                    raise ValueError("deliberate after real COMMIT")

                monkeypatch.setattr(lc, "_decode", bad_decode)
            if mode in ("missing_receipt", "mismatched_receipt"):
                real_decode = lc._decode

                def malformed_receipt(*args):
                    result = real_decode(*args)
                    if mode == "missing_receipt":
                        result["receipt"] = None
                    else:
                        result["receipt"]["opcode"] = "close"
                    return result

                monkeypatch.setattr(lc, "_decode", malformed_receipt)

            async def reset(conn):
                await conn.execute("SELECT pg_sleep(2)")

            if mode == "release":
                async with db.pool(reset=reset) as slow:
                    with pytest.raises(lc.LifecycleError) as e:
                        await lc.AttemptLifecycleStore(slow).register(ident, cmd)
            else:
                with pytest.raises(lc.LifecycleError) as e:
                    await store.register(ident, cmd)
            assert e.value.identity == ident and e.value.command_id == cmd
            assert e.value.commit_acknowledged == (
                mode in ("decode", "release", "missing_receipt", "mismatched_receipt")
            )
            if e.value.commit_acknowledged:
                assert e.value.code == "committed_result_unavailable"
            db.bridge.commit_fault = None
            monkeypatch.undo()
            if mode == "failed":
                await owner.execute("DROP TRIGGER lc_fail ON attempt_lifecycle_commands")
            result = await store.readback(ident, cmd)
            committed = mode in (
                "after",
                "decode",
                "release",
                "missing_receipt",
                "mismatched_receipt",
            )
            assert (result["receipt"] is not None) == committed
            assert (result["control"] is not None) == committed
            if mode == "after":
                assert db.bridge.discarded_commit_bytes > 0
            db.cluster.record(
                "lifecycle_actual_commit_fault", mode=mode, committed=committed, command=str(cmd)
            )
        finally:
            db.bridge.commit_fault = None
            await wp.close()


@pytest.mark.asyncio
async def test_real_gate_timeout_rollback_keeps_single_slot_connection_usable(db, monkeypatch):
    async with db.connection() as owner, db.pool(max_size=1) as pool:
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            cmd = uuid4()
            await store.register(ident, cmd)
            original_bound = lc.OPERATION_S
            async with pool.acquire() as app:
                pid = app.get_server_pid()
                async with owner.transaction():
                    await owner.execute(
                        "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        ident.account_id,
                    )
                    monkeypatch.setattr(lc, "OPERATION_S", 0.05)
                    with pytest.raises(lc.LifecycleError) as error:
                        async with app.transaction():
                            await lc.assert_open(app, ident)
                    assert error.value.code == "unavailable" and error.value.sqlstate is None
                    assert not app.is_in_transaction()
                    assert await app.fetchval("SELECT 1") == 1
                    assert app.get_server_pid() == pid
            monkeypatch.setattr(lc, "OPERATION_S", original_bound)
            assert (await store.readback(ident, cmd))["control"]["closed_at"] is None
            assert pool.get_size() == pool.get_idle_size() == 1
            db.cluster.record(
                "lifecycle_real_gate_timeout_rollback", single_slot=True, same_backend_usable=True
            )
        finally:
            await wp.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["claim", "cancel", "task_delete", "memory_delete"])
@pytest.mark.parametrize("first", ["gate", "writer"])
@pytest.mark.parametrize("operation", ["register", "assert_open"])
async def test_existing_writers_both_orderings(db, writer, first, operation):
    async with (
        db.connection() as owner,
        db.pool() as pool,
        db.connection("md_app") as app,
        db.connection("postgres") as admin,
    ):
        ident, conversation, wp, tasks, memory = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            if operation == "assert_open":
                await store.register(ident, uuid4())

            async def invoke():
                return await raw(app, operation, ident, uuid4())

            async def write():
                if writer == "claim":
                    return await tasks.claim(ident.task_id, worker_id="replacement", lease_s=45)
                if writer == "cancel":
                    return await tasks.request_cancel(ident.account_id, ident.task_id)
                if writer == "task_delete":
                    return await tasks.delete_conversation(ident.account_id, conversation)
                return await memory.delete_conversation(conversation)

            if first == "gate":
                async with app.transaction():
                    await invoke()
                    job = asyncio.create_task(write())
                    try:
                        async with asyncio.timeout(1):
                            while not await admin.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND usename='md_owner' AND wait_event_type='Lock')",
                                db.name,
                            ):
                                await asyncio.sleep(0.005)
                    except BaseException:
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                        raise
                await asyncio.wait_for(job, 2)
            else:
                await write()
                # Duplicate claim retains live epoch. Cancellation and either
                # deletion path must make a fresh gate/registration refuse.
                if writer == "claim":
                    await invoke()
                else:
                    with pytest.raises(asyncpg.RaiseError):
                        await invoke()
            if writer != "claim":
                with pytest.raises(asyncpg.RaiseError):
                    await raw(app, "assert_open", ident)
            db.cluster.record(
                "lifecycle_existing_writer_order", writer=writer, first=first, operation=operation
            )
        finally:
            await wp.close()


@pytest.mark.asyncio
async def test_real_expired_claim_takeover_and_postwait_gate(db):
    async with (
        db.connection() as owner,
        db.pool() as pool,
        db.connection("md_app") as app,
        db.connection("postgres") as admin,
    ):
        ident, _, wp, tasks, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            cmd = uuid4()
            await store.register(ident, cmd)
            await owner.execute(
                "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                ident.task_id,
            )
            replacement = await tasks.claim(ident.task_id, worker_id="replacement", lease_s=45)
            assert replacement is not None and replacement.epoch == ident.epoch + 1
            assert (
                await owner.fetchval(
                    "SELECT outcome FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                    ident.task_id,
                    ident.epoch,
                )
                == "lost"
            )
            with pytest.raises(asyncpg.RaiseError):
                await raw(app, "assert_open", ident)
            assert not (await store.register(ident, cmd))["references_current"]
            # A lock wait that consumes the lease cannot pass its pre-wait predicate.
            ident2, _, wp2, _, _ = await seed(db, owner)
            try:
                await store.register(ident2, uuid4())
                async with owner.transaction():
                    await owner.execute(
                        "UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '80 milliseconds' WHERE id=$1",
                        ident2.task_id,
                    )
                    job = asyncio.create_task(raw(app, "assert_open", ident2))
                    await db.wait_lock(admin, app.get_server_pid())
                    await asyncio.sleep(0.1)
                with pytest.raises(asyncpg.RaiseError):
                    await job
            finally:
                await wp2.close()
        finally:
            await wp.close()


@pytest.mark.asyncio
async def test_cancellation_readback_barrier_and_raw_argument_checks(db):
    async with (
        db.connection() as owner,
        db.connection("md_app") as app,
        db.pool() as pool,
        db.connection("postgres") as admin,
    ):
        ident, _, wp, _, _ = await seed(db, owner)
        try:
            store = lc.AttemptLifecycleStore(pool)
            cmd = uuid4()
            tx = app.transaction()
            await tx.start()
            await raw(app, "register", ident, cmd)
            # Separate authoritative readback must wait/refuse, never report absent
            # while the original transaction still owns its account serialization.
            with pytest.raises(lc.LifecycleError) as caught:
                await store.readback(ident, cmd)
            assert caught.value.sqlstate == "55P03"
            await tx.commit()
            assert (await store.readback(ident, cmd))["receipt"] is not None
            async with owner.transaction():
                await owner.execute(
                    "SELECT pg_advisory_xact_lock($1)", account_lock_key(ident.account_id)
                )
                cancel_cmd = uuid4()
                job = asyncio.create_task(store.close(ident, cancel_cmd))
                async with asyncio.timeout(1):
                    while not await admin.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND usename='md_app' AND wait_event_type='Lock')",
                        db.name,
                    ):
                        await asyncio.sleep(0.005)
                job.cancel("original cancellation")
                with pytest.raises(asyncio.CancelledError) as cancellation:
                    await job
                assert getattr(cancellation.value, "lifecycle_command_id") == cancel_cmd
                assert cancellation.value.args == ("original cancellation",)
            result = await store.readback(ident, cancel_cmd)
            assert result["receipt"] is None and result["control"]["closed_at"] is None
            for bad in (
                None,
                {},
                json.loads(ident.wire()) | {"extra": 1},
                json.loads(ident.wire()) | {"generation": True},
            ):
                with pytest.raises(asyncpg.RaiseError):
                    await app.execute(
                        "SELECT public.attempt_lifecycle_register($1::jsonb,$2)",
                        json.dumps(bad),
                        uuid4(),
                    )
            with pytest.raises(asyncpg.RaiseError):
                await raw(app, "close", ident, None)
            before = await owner.fetchval(
                "SELECT jsonb_agg(to_jsonb(c)) FROM attempt_lifecycle_commands c"
            )
            # Mappings and commands cannot be rewritten by the restricted principal.
            for sql in (
                "UPDATE attempt_lifecycle_control SET identity='{}'",
                "UPDATE attempt_lifecycle_commands SET revision=2",
                "TRUNCATE attempt_lifecycle_commands",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(sql)
            assert (
                await owner.fetchval(
                    "SELECT jsonb_agg(to_jsonb(c)) FROM attempt_lifecycle_commands c"
                )
                == before
            )
        finally:
            await wp.close()


@pytest.mark.asyncio
async def test_actual_writer_lock_inversion_is_bounded_refusal(db):
    # Existing claim's task -> message -> conversation path can oppose a caller
    # already holding content locks. We do NOT integrate into or change that path.
    # Qualify the exposed primitive with a real cycle and its exact SQLSTATE.
    async with (
        db.connection("postgres") as admin,
        db.connection("postgres") as owner,
        db.pool() as pool,
    ):
        ident, _, wp, tasks, _ = await seed(db, owner)
        try:
            await lc.AttemptLifecycleStore(pool).register(ident, uuid4())
            await admin.execute(
                f"ALTER ROLE md_app IN DATABASE {db.name} SET deadlock_timeout='10ms'"
            )
            # New connection picks up the disposable role setting.
            async with db.connection("md_app") as gate:
                await owner.execute(
                    "BEGIN; SET LOCAL deadlock_timeout='5s'; SET LOCAL statement_timeout='2s'"
                )
                await owner.execute("SELECT 1 FROM tasks WHERE id=$1 FOR UPDATE", ident.task_id)
                await gate.execute("BEGIN")
                job = asyncio.create_task(lc.assert_open(gate, ident))
                await db.wait_lock(admin, gate.get_server_pid())
                opposite = asyncio.create_task(
                    owner.execute(
                        "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        ident.account_id,
                    )
                )
                try:
                    with pytest.raises(lc.LifecycleError) as error:
                        await asyncio.wait_for(job, 1)
                    assert error.value.sqlstate == "40P01"
                    await gate.execute("ROLLBACK")
                    await opposite
                finally:
                    await gate.execute("ROLLBACK")
                    await owner.execute("ROLLBACK")
                    for pending in (job, opposite):
                        if not pending.done():
                            pending.cancel()
                    await asyncio.gather(job, opposite, return_exceptions=True)
            # State and history unchanged; no automatic command/write retry.
            assert await owner.fetchval("SELECT count(*) FROM attempt_lifecycle_commands") == 1
            db.cluster.record("lifecycle_real_deadlock_refused", sqlstate="40P01")
        finally:
            await wp.close()
