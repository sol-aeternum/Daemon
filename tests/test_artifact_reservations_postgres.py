"""Real, explicitly owned PostgreSQL tests; no application DSN fallback."""

import asyncio
from dataclasses import replace
import json
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator import artifact_reservations as ar
from orchestrator import attempt_lifecycle as lc
from orchestrator.services.account_control.store import account_lock_key
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster
from tests import test_attempt_lifecycle_postgres as lifecycle_tests
from tests.test_attempt_lifecycle_postgres import seed

lifecycle_db = lifecycle_tests.db


@pytest_asyncio.fixture
async def db(lifecycle_db):
    db = lifecycle_db
    for name in ("050_task_inference_recovery.sql", "051_artifact_output_reservations.sql"):
        db.sql("BEGIN;" + (ROOT / "migrations" / name).read_text() + "COMMIT;")
    db.sql(
        ";".join(
            "GRANT EXECUTE ON FUNCTION public.artifact_output_" + s + " TO md_app"
            for s in ar.SIGNATURES[3:]
        )
    )
    yield db


@pytest_asyncio.fixture
async def scenario(db):
    async with db.connection() as owner, db.pool() as pool:
        identity, conversation, wp, _, _ = await seed(db, owner)
        try:
            await lc.AttemptLifecycleStore(pool).register(identity, uuid4())
            attempt = await owner.fetchval(
                "SELECT id FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                identity.task_id,
                identity.epoch,
            )
            job = ar.JobBinding(
                uuid4(), identity.account_id, identity.generation, identity.task_id, uuid4()
            )
            binding = ar.IntentBinding(
                uuid4(), 0, uuid4(), uuid4(), identity.lifecycle_id, attempt, identity.epoch, 20
            )
            codec = ar.DescriptorCodec(Fernet(Fernet.generate_key()))
            command = codec.encode(job, binding, filename="private-sentinel.csv", sha256="a" * 64)
            yield identity, command, ar.OutputReservations(codec), codec, conversation
        finally:
            await wp.close()


async def reserve(app, store, identity, command):
    async with app.transaction():
        job = await store.register_output_job(app, identity, command.job)
        intent = await store.reserve_output_intent(app, identity, command)
    return job, intent


async def readback(app, store, command):
    async with app.transaction():
        return await store.readback_output_reservation(app, command.job, command)


@pytest.mark.asyncio
async def test_real_roundtrip_exact_replay_job_only_conflict_and_rollback(db, scenario):
    ident, c, store, codec, _ = scenario
    async with db.connection("md_app") as app, db.connection() as owner:
        assert (await readback(app, store, c)).outcome == "absent"
        async with app.transaction():
            pending = await store.register_output_job(app, ident, c.job)
            assert pending.outcome == "pending_commit" and pending.inserted
        assert (await readback(app, store, c)).outcome == "job_present_intent_absent"
        async with app.transaction():
            first = await store.reserve_output_intent(app, ident, c)
        assert first.inserted and first.outcome == "pending_commit"
        job2, replay = await reserve(app, store, ident, c)
        assert not job2.inserted and not replay.inserted and replay.intent == first.intent
        assert (await readback(app, store, c)).outcome == "present"
        fresh = codec.encode(c.job, c.binding, filename="private-sentinel.csv", sha256="a" * 64)
        with pytest.raises(ar.ReservationError, match="conflict"):
            await reserve(app, store, ident, fresh)
        raw = await owner.fetchval("SELECT to_jsonb(i)::text FROM artifact_output_intents i")
        assert "private-sentinel" not in raw and "a" * 64 not in raw
        other = replace(c.job, job_id=uuid4(), operation_id=uuid4())
        tx = app.transaction()
        await tx.start()
        await store.register_output_job(app, ident, other)
        await tx.rollback()
        async with app.transaction():
            assert (await store.readback_output_reservation(app, other)).outcome == "absent"


@pytest.mark.asyncio
async def test_cross_account_slot_and_attempt_collisions(db, scenario):
    ident, c, store, codec, _ = scenario
    async with db.connection("md_app") as app:
        await reserve(app, store, ident, c)
        for bad in (
            replace(c.binding, intent_id=uuid4()),
            replace(c.binding, attempt_id=uuid4()),
            replace(c.binding, lifecycle_id=uuid4()),
            replace(c.binding, output_slot=1),
        ):
            command = codec.encode(c.job, bad, filename="private-sentinel.csv", sha256="a" * 64)
            with pytest.raises(ar.ReservationError):
                await reserve(app, store, ident, command)
        for bad_job in (
            replace(c.job, account_id=uuid4()),
            replace(c.job, task_id=uuid4()),
            replace(c.job, account_generation=1),
            replace(c.job, job_id=uuid4()),
        ):
            with pytest.raises(ar.ReservationError):
                async with app.transaction():
                    await store.register_output_job(app, ident, bad_job)
        # Same private bytes, distinct operation => independent resource/version IDs.
        job = replace(c.job, job_id=uuid4(), operation_id=uuid4())
        binding = replace(c.binding, intent_id=uuid4(), resource_id=uuid4(), version_id=uuid4())
        new = codec.encode(job, binding, filename="private-sentinel.csv", sha256="a" * 64)
        assert (await reserve(app, store, ident, new))[1].inserted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["generation", "suspend", "cause", "cancel", "epoch", "expired", "attempt", "closed"]
)
async def test_stale_authority_refuses_even_duplicate_history(db, scenario, change):
    ident, c, store, _, _ = scenario
    async with db.connection("md_app") as app, db.connection() as owner:
        await reserve(app, store, ident, c)
        async with owner.transaction():
            await owner.execute(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                ident.account_id,
            )
            if change == "generation":
                await owner.execute(
                    "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                    ident.account_id,
                )
            elif change == "suspend":
                await owner.execute(
                    "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1",
                    ident.account_id,
                )
            elif change == "cause":
                await owner.execute(
                    "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')",
                    ident.account_id,
                    uuid4(),
                )
            elif change == "cancel":
                await owner.execute(
                    "UPDATE tasks SET cancel_requested_at=clock_timestamp() WHERE id=$1",
                    ident.task_id,
                )
            elif change == "epoch":
                await owner.execute(
                    "UPDATE tasks SET lease_epoch=lease_epoch+1 WHERE id=$1", ident.task_id
                )
            elif change == "expired":
                await owner.execute(
                    "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                    ident.task_id,
                )
            elif change == "attempt":
                await owner.execute(
                    "UPDATE task_attempts SET worker_id='other' WHERE id=$1", c.binding.attempt_id
                )
            else:
                await owner.execute(
                    "UPDATE attempt_lifecycle_control SET revision=2,closed_at=clock_timestamp() WHERE id=$1",
                    ident.lifecycle_id,
                )
        with pytest.raises(ar.ReservationError, match="refused"):
            await reserve(app, store, ident, c)
        assert (await readback(app, store, c)).outcome == "present"


@pytest.mark.asyncio
async def test_chat_history_survives_account_cascade_and_missing_account_refuses(db, scenario):
    ident, c, store, _, conversation = scenario
    async with db.connection("md_app") as app, db.connection() as owner:
        await reserve(app, store, ident, c)
        await owner.execute("DELETE FROM conversations WHERE id=$1", conversation)
        assert (await readback(app, store, c)).outcome == "present"
        with pytest.raises(ar.ReservationError):
            await reserve(app, store, ident, c)
        await owner.execute("DELETE FROM users WHERE id=$1", ident.account_id)
        for table in ("artifact_output_jobs", "artifact_output_intents"):
            assert await owner.fetchval(f"SELECT count(*) FROM {table}") == 0
        with pytest.raises(ar.ReservationError, match="unavailable"):
            await readback(app, store, c)


@pytest.mark.asyncio
async def test_two_connection_same_packet_and_changed_packet_races(db, scenario):
    ident, c, store, _, _ = scenario
    async with (
        db.connection("md_app") as a,
        db.connection("md_app") as b,
        db.connection("postgres") as admin,
    ):
        tx = a.transaction()
        await tx.start()
        await store.register_output_job(a, ident, c.job)
        await store.reserve_output_intent(a, ident, c)
        contender = asyncio.create_task(reserve(b, store, ident, c))
        await db.wait_lock(admin, b.get_server_pid())
        await tx.commit()
        assert not (await contender)[1].inserted
        # The same serialization distinguishes a pending write from absence.
        tx = a.transaction()
        await tx.start()
        await store.readback_output_reservation(a, c.job, c)
        with pytest.raises(ar.ReservationError, match="unknown"):
            await readback(b, store, c)
        await tx.rollback()


@pytest.mark.asyncio
async def test_account_fence_wait_and_savepoint_revalidation(db, scenario):
    ident, c, store, _, _ = scenario
    async with (
        db.connection("md_app") as app,
        db.connection() as owner,
        db.connection("postgres") as admin,
    ):
        fence = owner.transaction()
        await fence.start()
        await owner.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(ident.account_id))
        await owner.execute(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", ident.account_id
        )
        writer = asyncio.create_task(reserve(app, store, ident, c))
        await db.wait_lock(admin, app.get_server_pid())
        await owner.execute(
            "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
            ident.account_id,
        )
        await fence.commit()
        with pytest.raises(ar.ReservationError):
            await writer
        assert (await readback(app, store, c)).outcome == "absent"
        await owner.execute(
            "UPDATE entitlement_accounts SET content_generation=0 WHERE user_id=$1",
            ident.account_id,
        )
        async with app.transaction():
            save = app.transaction()
            await save.start()
            await store.register_output_job(app, ident, c.job)
            await save.rollback()
            await owner.execute(
                "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                ident.account_id,
            )
            with pytest.raises(ar.ReservationError):
                async with app.transaction():
                    await store.register_output_job(app, ident, c.job)
        assert (await readback(app, store, c)).outcome == "absent"


@pytest.mark.asyncio
async def test_lease_expiry_during_last_job_lock_wait(db, scenario):
    ident, c, store, _, _ = scenario
    async with (
        db.connection("md_app") as app,
        db.connection() as owner,
        db.connection("postgres") as admin,
    ):
        async with app.transaction():
            await store.register_output_job(app, ident, c.job)
        await owner.execute(
            "UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '120 milliseconds' WHERE id=$1",
            ident.task_id,
        )
        blocker = owner.transaction()
        await blocker.start()
        await owner.execute(
            "SELECT job_id FROM artifact_output_jobs WHERE job_id=$1 FOR UPDATE", c.job.job_id
        )

        async def waiting():
            async with app.transaction():
                return await store.reserve_output_intent(app, ident, c)

        writer = asyncio.create_task(waiting())
        await db.wait_lock(admin, app.get_server_pid())
        await asyncio.sleep(0.14)
        await blocker.commit()
        with pytest.raises(ar.ReservationError, match="refused"):
            await writer
        assert (await readback(app, store, c)).outcome == "job_present_intent_absent"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,expected", [("after", "present"), ("before", "absent")])
async def test_unknown_commit_response_requires_real_readback(db, scenario, fault, expected):
    ident, c, store, _, _ = scenario
    conn = await db.connect("md_app")
    try:
        tx = conn.transaction()
        await tx.start()
        await store.register_output_job(conn, ident, c.job)
        await store.reserve_output_intent(conn, ident, c)
        db.bridge.commit_fault = fault
        with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)):
            await conn.execute("COMMIT;", timeout=0.3)
        assert db.bridge.commit_seen.is_set()
        if fault == "after":
            assert db.bridge.discarded_commit_bytes > 0
    finally:
        conn.terminate()
        # Fault applies only to the original command, not reconciliation's COMMIT.
        db.bridge.commit_fault = None
    async with db.connection("md_app") as app:
        assert (await readback(app, store, c)).outcome == expected


@pytest.mark.asyncio
async def test_privilege_envelope_direct_sql_and_isolation(db, scenario):
    ident, c, store, _, _ = scenario
    async with (
        db.connection("md_app") as app,
        db.connection() as owner,
        db.connection("postgres") as admin,
        db.connection("md_third") as third,
    ):
        assert await app.fetchval(ar.ROLE_PRECONDITION)
        assert not await owner.fetchval(ar.ROLE_PRECONDITION)
        for table in ("artifact_output_jobs", "artifact_output_intents"):
            for sql in (
                f"SELECT * FROM {table}",
                f"DELETE FROM {table}",
                f"UPDATE {table} SET account_generation=0",
                f"TRUNCATE {table}",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(sql)
            await owner.execute(f"GRANT SELECT(account_id) ON {table} TO md_app")
            assert not await app.fetchval(ar.ROLE_PRECONDITION)
            await owner.execute(f"REVOKE SELECT(account_id) ON {table} FROM md_app")
        await admin.execute("GRANT md_owner TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE")
        try:
            assert not await app.fetchval(ar.ROLE_PRECONDITION)
        finally:
            await admin.execute("REVOKE md_mid FROM md_app; REVOKE md_owner FROM md_mid")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await third.fetchval(
                "SELECT public.artifact_output_readback($1::jsonb,NULL)", json.dumps(c.job.wire())
            )
        for func, arg in (
            ("job_arg", json.dumps(c.job.wire())),
            ("intent_arg", json.dumps(c.wire())),
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.fetchval(f"SELECT public.artifact_output_{func}($1::jsonb)", arg)
        with pytest.raises(ar.ReservationError, match="transaction_required"):
            await store.register_output_job(app, ident, c.job)
        async with app.transaction(isolation="repeatable_read"):
            with pytest.raises(ar.ReservationError, match="unsupported_isolation"):
                await store.register_output_job(app, ident, c.job)
            with pytest.raises(asyncpg.RaiseError):
                await app.fetchval(
                    "SELECT public.artifact_output_register($1::jsonb,$2::jsonb)",
                    ident.wire(),
                    json.dumps(c.job.wire()),
                )
        await app.execute("CREATE TEMP TABLE tasks(id uuid); SET search_path=pg_temp")
        assert (await reserve(app, store, ident, c))[1].inserted


@pytest.mark.asyncio
async def test_real_constraints_mixed_owner_and_bad_wire(db, scenario):
    ident, c, store, _, _ = scenario
    async with db.connection("md_app") as app, db.connection() as owner:
        await reserve(app, store, ident, c)
        foreign = await db.account(owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await owner.execute("UPDATE artifact_output_intents SET account_id=$1", foreign)
        for column, value in (("measured_bytes", -1), ("attempt_epoch", 0), ("output_slot", -1)):
            with pytest.raises(asyncpg.CheckViolationError):
                await owner.execute(f"UPDATE artifact_output_intents SET {column}=$1", value)
        for bad in (
            c.wire() | {"private_extra": "sentinel"},
            c.wire() | {"attempt_id": None},
            c.wire() | {"account_generation": "0"},
        ):
            with pytest.raises(asyncpg.RaiseError):
                await app.fetchval(
                    "SELECT public.artifact_output_reserve($1::jsonb,$2::jsonb,$3::jsonb)",
                    ident.wire(),
                    json.dumps(c.job.wire()),
                    json.dumps(bad),
                )


@pytest.mark.asyncio
async def test_writer_first_serializes_fence_and_conflicting_delivery(db, scenario):
    ident, c, store, codec, _ = scenario
    changed = codec.encode(c.job, c.binding, filename="different.csv", sha256="b" * 64)
    async with (
        db.connection("md_app") as a,
        db.connection("md_app") as b,
        db.connection() as owner,
        db.connection("postgres") as admin,
    ):
        tx = a.transaction()
        await tx.start()
        await store.register_output_job(a, ident, c.job)
        await store.reserve_output_intent(a, ident, c)
        contender = asyncio.create_task(reserve(b, store, ident, changed))
        await db.wait_lock(admin, b.get_server_pid())
        await tx.commit()
        with pytest.raises(ar.ReservationError, match="conflict"):
            await contender
        # A cooperating fence cannot commit while a reservation owns the account.
        tx = a.transaction()
        await tx.start()
        await store.reserve_output_intent(a, ident, c)

        async def fence():
            async with owner.transaction():
                await owner.execute(
                    "SELECT pg_advisory_xact_lock($1)", account_lock_key(ident.account_id)
                )
                await owner.execute(
                    "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                    ident.account_id,
                )

        fence_task = asyncio.create_task(fence())
        await db.wait_lock(admin, owner.get_server_pid())
        assert not fence_task.done()
        await tx.commit()
        await fence_task
        with pytest.raises(ar.ReservationError):
            await reserve(a, store, ident, c)
        assert (await readback(a, store, c)).intent[
            "descriptor_ciphertext"
        ] == c.descriptor_ciphertext


@pytest.mark.asyncio
async def test_migration_refuses_unsafe_default_privileges_atomically(lifecycle_db):
    db = lifecycle_db  # 049 applied; deliberately no reservation migration yet.
    source = (ROOT / "migrations/051_artifact_output_reservations.sql").read_text()
    async with db.connection() as owner:
        for target, privileges in (("TABLES", "SELECT"), ("FUNCTIONS", "EXECUTE")):
            await owner.execute(
                f"ALTER DEFAULT PRIVILEGES GRANT {privileges} ON {target} TO md_third"
            )
            try:
                with pytest.raises(
                    asyncpg.RaiseError, match="reservation_unsafe_default_privileges"
                ):
                    async with owner.transaction():
                        await owner.execute(source)
                assert (
                    await owner.fetchval("SELECT to_regclass('public.artifact_output_jobs')")
                    is None
                )
                assert (
                    await owner.fetchval(
                        "SELECT to_regprocedure('public.artifact_output_register(jsonb,jsonb)')"
                    )
                    is None
                )
            finally:
                await owner.execute(
                    f"ALTER DEFAULT PRIVILEGES REVOKE {privileges} ON {target} FROM md_third"
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["job_id", "intent_id", "resource_id", "version_id"])
async def test_lease_expiry_during_invisible_global_unique_insert_wait(db, scenario, collision):
    ident, c, store, _, _ = scenario
    async with (
        db.connection("md_app") as app,
        db.connection() as owner,
        db.connection("postgres") as admin,
    ):
        foreign = await db.account(owner)
        foreign_job = replace(
            c.job, account_id=foreign, job_id=uuid4(), operation_id=uuid4(), task_id=uuid4()
        )
        if collision != "job_id":
            async with app.transaction():
                await store.register_output_job(app, ident, c.job)
            await owner.execute(
                "INSERT INTO artifact_output_jobs(job_id,account_id,account_generation,task_id,operation_id) VALUES($1,$2,$3,$4,$5)",
                foreign_job.job_id,
                foreign,
                foreign_job.account_generation,
                foreign_job.task_id,
                foreign_job.operation_id,
            )
        await owner.execute(
            "UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '150 milliseconds' WHERE id=$1",
            ident.task_id,
        )
        blocking = owner.transaction()
        await blocking.start()
        if collision == "job_id":
            await owner.execute(
                "INSERT INTO artifact_output_jobs(job_id,account_id,account_generation,task_id,operation_id) VALUES($1,$2,0,$3,$4)",
                c.job.job_id,
                foreign,
                uuid4(),
                uuid4(),
            )
        else:
            value = c.wire() | {
                "account_id": str(foreign),
                "job_id": str(foreign_job.job_id),
                "task_id": str(foreign_job.task_id),
                "intent_id": str(uuid4()),
                "resource_id": str(uuid4()),
                "version_id": str(uuid4()),
            }
            value[collision] = c.wire()[collision]
            await owner.execute(
                "INSERT INTO artifact_output_intents SELECT r.* FROM jsonb_populate_record(NULL::artifact_output_intents,$1::jsonb||jsonb_build_object('created_at',clock_timestamp())) r",
                json.dumps(value),
            )

        async def waiting_insert():
            async with app.transaction():
                if collision == "job_id":
                    return await store.register_output_job(app, ident, c.job)
                return await store.reserve_output_intent(app, ident, c)

        writer = asyncio.create_task(waiting_insert())
        try:
            await db.wait_lock(admin, app.get_server_pid())
            # Wall-clock database evidence: expire the lease while uniqueness waits.
            async with asyncio.timeout(0.2):
                while await admin.fetchval(
                    "SELECT lease_expires_at>clock_timestamp() FROM tasks WHERE id=$1",
                    ident.task_id,
                ):
                    await asyncio.sleep(0.005)
        finally:
            await blocking.rollback()
        with pytest.raises(ar.ReservationError, match="refused"):
            await writer
        observed = await readback(app, store, c)
        assert observed.outcome == (
            "absent" if collision == "job_id" else "job_present_intent_absent"
        )
