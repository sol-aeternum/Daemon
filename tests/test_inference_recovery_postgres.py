"""Opt-in actual SQL/task-store quarantine with synthetic, separated principals."""

import asyncio
import json
import os
from uuid import uuid4
from typing import Any, cast
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks._invocation import invocation, InferenceInterrupted
from orchestrator import compute_runtime as runtime
from orchestrator.tasks.inference import CODE, InferenceRecovery, ROLE_PRECONDITION
from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.tasks.store import TaskControlled, TaskStore
from tests.compute_metadata_support import (
    Bridge,
    Database,
    ROOT,
    metadata_cluster as metadata_cluster,
)


@pytest_asyncio.fixture
async def db(metadata_cluster):
    cluster = metadata_cluster
    name = "md_" + uuid4().hex
    cluster.prepare(name)
    for migration in ("049_attempt_lifecycle.sql", "050_task_inference_recovery.sql"):
        cluster.sql(
            "BEGIN;" + (ROOT / "migrations" / migration).read_text() + "COMMIT;",
            database=name,
            role="md_owner",
        )
    # Roles exist solely inside this newly owned network-none synthetic cluster.
    cluster.sql(
        "DO $$ BEGIN CREATE ROLE md_acceptor LOGIN NOSUPERUSER NOCREATEROLE; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    cluster.sql(
        """
        GRANT USAGE ON SCHEMA public TO md_app,md_acceptor;
        GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO md_acceptor;
        REVOKE ALL ON public.compute_scope_metadata,public.compute_reservation_metadata,
          public.attempt_lifecycle_control,public.attempt_lifecycle_commands,
          public.task_inference_recovery FROM md_app,md_acceptor;
        GRANT INSERT(task_id,account_id,account_generation,protocol_version) ON public.task_inference_recovery TO md_acceptor;
        GRANT EXECUTE ON FUNCTION public.attempt_lifecycle_register(jsonb,uuid),
          public.attempt_lifecycle_assert_open(jsonb),public.attempt_lifecycle_close(jsonb,uuid,text),
          public.attempt_lifecycle_readback(jsonb,uuid),
          public.task_inference_dispatch_begin(jsonb,uuid,uuid,uuid),
          public.task_inference_dispatch_readback(jsonb,uuid,uuid,uuid),
          public.task_inference_recovery_lock(uuid,bigint,uuid,bigint,uuid),
          public.attempt_lifecycle_abandoned_page(uuid),
          public.attempt_lifecycle_close_abandoned(jsonb,uuid) TO md_app;
    """,
        database=name,
        role="md_owner",
    )
    # Qualification lane: W receives only the preserved proposed exact map.
    # A remains the existing synthetic acceptor fixture; W has no A membership.
    from tests.exact_worker_privileges import grant_exact_worker

    grant_exact_worker(cluster, name)
    resource = cluster.resource / uuid4().hex[:6]
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
        cluster.record("quarantine_database_reconciled", database=name, connections=0)
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.sql("CHECKPOINT")
        cluster.databases.remove(name)


async def seed(db, owner, pool):
    account = await db.account(owner)
    enc = ContentEncryption(Fernet.generate_key().decode())
    accept_pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_acceptor",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
    )
    try:
        acceptor = TaskStore(
            accept_pool, enc, MemoryStore(accept_pool, enc), cover_new_inference=True
        )
        options: dict[str, Any] = dict(
            user_id=account,
            conversation_id=None,
            new_conversation_title="fixture",
            pipeline="cloud",
            user_message="synthetic",
            task_input={"message": "synthetic"},
            request_hash="0" * 64,
            idempotency_key=str(uuid4()),
            assistant_model=None,
            captured_generation=CapturedGeneration(account, 0),
        )
        accepted = await acceptor.accept(**options)
        assert (await acceptor.accept(**options)).created is False
    finally:
        await accept_pool.close()
    recovery = InferenceRecovery(pool)
    tasks = TaskStore(pool, enc, MemoryStore(pool, enc), inference_recovery=recovery)
    claim = await tasks.claim(accepted.task_id, worker_id="fixture", lease_s=45)
    assert claim is not None and claim.inference_covered
    identity = AttemptIdentity(
        uuid4(), account, 0, accepted.task_id, claim.epoch, uuid4(), uuid4(), uuid4()
    )
    await tasks.record_compute_scope(
        claim.task_id, claim.epoch, identity.financial_scope_id, binding=claim
    )
    await recovery.register(identity, InferenceInvocation())
    return tasks, recovery, claim, identity


@pytest.mark.asyncio
async def test_mark_atomic_scope_binding_and_irreversible_no_duplicate_permit(db):
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        assert await owner.fetchval(ROLE_PRECONDITION) is False
        async with db.connection("md_acceptor") as acceptor:
            assert await acceptor.fetchval(ROLE_PRECONDITION) is False
        async with pool.acquire() as worker:
            assert await worker.fetchval(ROLE_PRECONDITION) is True
        # Admit the healthy hold before the negative fixture deliberately inserts
        # an unrelated open scope. That fixture otherwise occupies the sole slot.
        # Use actual attempt-qualified admission, not copied reserve SQL.
        reservation = await EntitlementService(pool).reserve_for_attempt(
            identity, 100, operation="chat"
        )
        wrong = await db.reservation(owner, identity.account_id, uuid4())
        async with pool.acquire() as worker:
            with pytest.raises(asyncpg.PostgresError):
                async with worker.transaction():
                    await worker.fetchval(
                        "SELECT public.task_inference_dispatch_begin($1::jsonb,$2,$3,$4)",
                        identity.wire(),
                        wrong,
                        uuid4(),
                        uuid4(),
                    )
        assert not await owner.fetchval(
            "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
            claim.task_id,
        )
        await recovery.mark(identity, reservation.id, InferenceInvocation())
        mark = await owner.fetchrow(
            "SELECT * FROM task_inference_recovery WHERE task_id=$1", claim.task_id
        )
        assert mark["may_have_dispatched"] and mark["first_reservation_id"] == reservation.id
        binding = await owner.fetchrow(
            "SELECT * FROM compute_reservation_metadata WHERE reservation_id=$1", reservation.id
        )
        async with pool.acquire() as worker:
            observation = json.loads(
                await worker.fetchval(
                    "SELECT public.task_inference_dispatch_readback($1::jsonb,$2,$3,$4)",
                    identity.wire(),
                    reservation.id,
                    binding["runtime_identity"],
                    binding["dispatch_nonce"],
                )
            )
            assert observation["may_have_dispatched"] and "created" not in observation
            with pytest.raises(asyncpg.PostgresError):
                await worker.fetchval(
                    "SELECT public.task_inference_dispatch_begin($1::jsonb,$2,$3,$4)",
                    identity.wire(),
                    reservation.id,
                    binding["runtime_identity"],
                    binding["dispatch_nonce"],
                )
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute(
                "UPDATE task_inference_recovery SET may_have_dispatched=false WHERE task_id=$1",
                claim.task_id,
            )
        assert (
            await owner.fetchrow(
                "SELECT * FROM task_inference_recovery WHERE task_id=$1", claim.task_id
            )
            == mark
        )
        # A later healthy distinct reservation is allowed; the first tuple stays fixed.
        second = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        await recovery.mark(identity, second, InferenceInvocation())
        assert (
            await owner.fetchrow(
                "SELECT * FROM task_inference_recovery WHERE task_id=$1", claim.task_id
            )
            == mark
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["fail", "preparation", "defer_claim", "takeover"])
async def test_possible_dispatch_never_requeues_archives_or_resets(db, path):
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        await tasks.write_partial(
            claim.task_id, claim.epoch, content="saved incomplete", delta_seq=1, binding=claim
        )
        reservation = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        await recovery.mark(identity, reservation, InferenceInvocation())
        before = await owner.fetchval(
            "SELECT content FROM messages WHERE id=$1", claim.result_message_id
        )
        if path == "fail":
            assert (
                await tasks.fail_attempt(
                    claim.task_id,
                    claim.epoch,
                    cause=RetryCause.RETRYABLE_ERROR,
                    error_code="internal_error",
                    binding=claim,
                )
                is TaskStatus.NEEDS_ATTENTION
            )
        elif path == "preparation":
            assert (
                await tasks.defer_execution(
                    claim.task_id,
                    claim.epoch,
                    delay_s=1,
                    reason="preparation_failed",
                    max_wait_s=900,
                    before_dispatch=True,
                    binding=claim,
                )
                is TaskStatus.NEEDS_ATTENTION
            )
        elif path == "defer_claim":
            assert (
                await tasks.defer_claim(
                    claim.task_id, claim.epoch, delay_s=1, reason="recovery_pending", binding=claim
                )
                is TaskStatus.NEEDS_ATTENTION
            )
        else:
            await owner.execute(
                "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                claim.task_id,
            )
            with pytest.raises(TaskControlled):
                await tasks.claim(claim.task_id, worker_id="replacement", lease_s=45)
        task = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)
        assert (task["status"], task["terminal_code"], task["lease_epoch"]) == (
            "needs_attention",
            CODE,
            claim.epoch,
        )
        assert (
            await owner.fetchval(
                "SELECT content FROM messages WHERE id=$1", claim.result_message_id
            )
            == before
        )
        assert (
            await owner.fetchval(
                "SELECT partial_ciphertext FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                claim.task_id,
                claim.epoch,
            )
            is None
        )
        assert await owner.fetchval(
            "SELECT closed_at IS NOT NULL FROM attempt_lifecycle_control WHERE id=$1",
            identity.lifecycle_id,
        )


@pytest.mark.asyncio
async def test_stop_precedence_and_missing_collaborator_refusal(db):
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        reservation = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        await recovery.mark(identity, reservation, InferenceInvocation())
        await tasks.request_cancel(identity.account_id, claim.task_id)
        assert (
            await tasks.acknowledge_cancel(claim.task_id, claim.epoch, binding=claim)
            is TaskStatus.CANCELLED
        )
        assert await owner.fetchval(
            "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
            claim.task_id,
        )
        from orchestrator.services.account_control.transaction import ObservationBlocked

        missing = TaskStore(pool, tasks._enc, tasks._memory)
        with pytest.raises(ObservationBlocked):
            await missing.claim(claim.task_id, worker_id="wrong-composition", lease_s=45)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "release"])
async def test_real_mark_driver_fault_latches_before_any_nested_or_finally_effect(db, phase):
    from tests.test_task_generation_publication import FaultPool
    from orchestrator.services.account_control.transaction import ObservationBlocked

    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        reservation = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        fault = FaultPool(pool, phase)
        state = InferenceInvocation()
        token = invocation.set(state)
        try:
            with pytest.raises(ObservationBlocked) as caught:
                await InferenceRecovery(fault).mark(identity, reservation, state)
            assert getattr(caught.value, "inference_identity") is identity
            assert getattr(caught.value, "inference_reservation_id") == reservation
            assert state.uncertainty is not None and fault.commits == 1
            if phase == "release":
                assert fault.terminates == 0 and fault.releases == 1
            with pytest.raises(ObservationBlocked):
                await tasks.fail_attempt(
                    claim.task_id,
                    claim.epoch,
                    cause=RetryCause.RETRYABLE_ERROR,
                    error_code="never",
                    binding=claim,
                )
            service = SimpleNamespace(settle=AsyncMock())
            scope = runtime.ComputeScope(
                identity.account_id,
                cast(EntitlementService, service),
                inference_identity=identity,
                inference_recovery=recovery,
                inference_invocation=state,
            )
            hold = SimpleNamespace(id=reservation)
            scope.outstanding[reservation] = runtime.ReservationHold(hold, 100)
            with pytest.raises(ObservationBlocked):
                await scope.settle(hold, 100)
            service.settle.assert_not_awaited()
        finally:
            invocation.reset(token)
        # Independent fixture observer, not mutation/readback in the closed invocation.
        assert await owner.fetchval(
            "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
            claim.task_id,
        ) is (phase != "before_commit")
        assert (
            await owner.fetchval("SELECT status FROM tasks WHERE id=$1", claim.task_id) == "running"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["eof", "close", "error"])
async def test_actual_compute_path_upstream_continues_but_recovery_never_sends_again(
    db, monkeypatch, end
):
    from tests.test_compute_runtime import _qualified_policy, _route

    route = _route()
    _qualified_policy(monkeypatch, route=route)
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            request_timeout_s=10,
            openrouter_api_key="synthetic-no-network",
            get_provider_config=lambda provider: SimpleNamespace(extra_headers={}),
        ),
    )
    sends = 0
    continued = asyncio.Event()
    tick = asyncio.Event()
    producers = []

    async def producer():
        await tick.wait()
        continued.set()  # separate synthetic remote owner survives local stream close

    class Stream:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return {"choices": [{"delta": {"content": "partial"}}]}
            if end == "error":
                raise RuntimeError("synthetic continuing upstream transport loss")
            raise StopAsyncIteration

        async def aclose(self):
            pass  # deliberately NOT a remote producer-stop certificate

    async def send(**kwargs):
        nonlocal sends
        sends += 1
        assert kwargs["num_retries"] == 0
        producers.append(asyncio.create_task(producer()))
        return Stream()

    monkeypatch.setattr(runtime.litellm, "acompletion", send)
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        state = InferenceInvocation()
        token = invocation.set(state)
        try:
            async with runtime.account_compute(
                pool,
                identity.account_id,
                auto_route=True,
                inference_identity=identity,
                inference_recovery=recovery,
            ):
                stream = await runtime.guarded_completion(
                    messages=[{"role": "user", "content": "synthetic"}], max_tokens=2, stream=True
                )
                await anext(stream)
                await tasks.write_partial(
                    claim.task_id,
                    claim.epoch,
                    content="saved incomplete",
                    delta_seq=1,
                    binding=claim,
                )
                if end == "close":
                    await stream.aclose()
                elif end == "error":
                    with pytest.raises(runtime.ComputeUnavailable):
                        await anext(stream)
                else:
                    with pytest.raises(StopAsyncIteration):
                        await anext(stream)
                if end != "eof":
                    with pytest.raises(InferenceInterrupted):
                        await runtime.guarded_completion(
                            messages=[{"role": "user", "content": "not a new logical call"}],
                            max_tokens=2,
                        )
            tick.set()
            await asyncio.wait_for(continued.wait(), 1)
            assert sends == 1
            await owner.execute(
                "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                claim.task_id,
            )
            with pytest.raises(TaskControlled):
                await tasks.claim(claim.task_id, worker_id="replacement", lease_s=45)
            assert sends == 1 and continued.is_set()
            assert (
                await owner.fetchval("SELECT lease_epoch FROM tasks WHERE id=$1", claim.task_id)
                == claim.epoch
            )
            assert (
                await owner.fetchval(
                    "SELECT partial_ciphertext FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                    claim.task_id,
                    claim.epoch,
                )
                is None
            )
            snapshot = await tasks.snapshot(identity.account_id, claim.task_id)
            assert snapshot is not None and snapshot.content == "saved incomplete"
        finally:
            invocation.reset(token)
            tick.set()
            await asyncio.gather(*producers)


@pytest.mark.asyncio
async def test_abandoned_pass_skips_live_and_closes_only_ended_or_expired(db):
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        assert await recovery.abandoned_pass() == (0, False)
        assert (
            await owner.fetchval(
                "SELECT closed_at FROM attempt_lifecycle_control WHERE id=$1", identity.lifecycle_id
            )
            is None
        )
        # Exhaust discovery then wrap; no age/release interpretation.
        assert await recovery.abandoned_pass() == (0, False)
        await owner.execute(
            "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
            claim.task_id,
        )
        assert await recovery.abandoned_pass() == (1, False)
        assert await owner.fetchval(
            "SELECT closed_at IS NOT NULL FROM attempt_lifecycle_control WHERE id=$1",
            identity.lifecycle_id,
        )
        assert (
            await owner.fetchval(
                "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
                claim.task_id,
            )
            is False
        )


@pytest.mark.asyncio
async def test_known_failed_call_cannot_publish_a_late_final_or_partial(db):
    async with db.connection() as owner, db.pool() as pool:
        tasks, recovery, claim, identity = await seed(db, owner, pool)
        await tasks.write_partial(
            claim.task_id, claim.epoch, content="admitted partial", delta_seq=1, binding=claim
        )
        reservation = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        await recovery.mark(identity, reservation, InferenceInvocation())
        state = InferenceInvocation()
        state.call_failed = True
        token = invocation.set(state)
        try:
            with pytest.raises(InferenceInterrupted):
                await tasks.write_partial(
                    claim.task_id,
                    claim.epoch,
                    content="late unadmitted answer",
                    delta_seq=2,
                    binding=claim,
                )
            assert state.uncertainty is None  # known rollback, not a fabricated DB ambiguity
            assert (
                await tasks.complete(
                    claim.task_id, claim.epoch, content="late unadmitted final", binding=claim
                )
                is TaskStatus.NEEDS_ATTENTION
            )
        finally:
            invocation.reset(token)
        snapshot = await tasks.snapshot(identity.account_id, claim.task_id)
        assert (
            snapshot is not None
            and snapshot.content == "admitted partial"
            and snapshot.terminal_code == CODE
        )
        assert (
            await owner.fetchval(
                "SELECT partial_ciphertext FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                claim.task_id,
                claim.epoch,
            )
            is None
        )


@pytest.mark.asyncio
async def test_acceptance_insert_failure_is_atomic_and_replay_never_backfills(db):
    from orchestrator.services.account_control.transaction import ObservationBlocked

    async with db.connection() as owner, db.pool() as pool:
        tasks, _, claim, identity = await seed(db, owner, pool)
        saved = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)
        await owner.execute("DELETE FROM task_inference_recovery WHERE task_id=$1", claim.task_id)
        async with asyncpg.create_pool(
            host=str(db.host),
            user="md_acceptor",
            password="",
            database=db.name,
            ssl=False,
            min_size=0,
            max_size=1,
        ) as ap:
            acceptor = TaskStore(
                ap, tasks._enc, MemoryStore(ap, tasks._enc), cover_new_inference=True
            )
            options: dict[str, Any] = dict(
                user_id=identity.account_id,
                conversation_id=None,
                new_conversation_title="fixture",
                pipeline="cloud",
                user_message="synthetic",
                task_input={},
                request_hash=saved["request_hash"],
                idempotency_key=saved["idempotency_key"],
                assistant_model=None,
            )
            assert (await acceptor.accept(**options)).created is False
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM task_inference_recovery WHERE task_id=$1", claim.task_id
                )
                == 0
            )
            with pytest.raises(ObservationBlocked):
                await tasks.claim(claim.task_id, worker_id="no-backfill", lease_s=45)
            before = await owner.fetchrow(
                "SELECT (SELECT count(*) FROM tasks) AS tasks,(SELECT count(*) FROM messages) AS messages,(SELECT count(*) FROM conversations) AS conversations"
            )
            await owner.execute(
                # Revoke AFTER the acquired-connection preflight, at the real
                # INSERT boundary. Keep proving rollback of staged content,
                # rather than merely testing the new early capability refusal.
                "CREATE FUNCTION revoke_coverage_on_accept() RETURNS trigger "
                "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ "
                "BEGIN REVOKE INSERT(task_id,account_id,account_generation,protocol_version) "
                "ON public.task_inference_recovery FROM md_acceptor; RETURN NEW; END $$; "
                "REVOKE ALL ON FUNCTION revoke_coverage_on_accept() FROM PUBLIC; "
                "CREATE TRIGGER revoke_coverage_on_accept AFTER INSERT ON tasks "
                "FOR EACH ROW EXECUTE FUNCTION revoke_coverage_on_accept()"
            )
            options.update(
                idempotency_key=str(uuid4()),
                captured_generation=CapturedGeneration(identity.account_id, 0),
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await acceptor.accept(**options)
            assert (
                await owner.fetchrow(
                    "SELECT (SELECT count(*) FROM tasks) AS tasks,(SELECT count(*) FROM messages) AS messages,(SELECT count(*) FROM conversations) AS conversations"
                )
                == before
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_kind", ["decode", "cancel_after_commit"])
async def test_real_mark_decode_and_original_cancellation_retained(db, fault_kind):
    from tests.test_task_generation_publication import FaultPool, FaultConnection
    from orchestrator.services.account_control.transaction import ObservationBlocked

    original = asyncio.CancelledError("synthetic original cancellation")

    class Connection(FaultConnection):
        async def fetchval(self, sql, *args):
            raw = await self.real.fetchval(sql, *args)
            if fault_kind == "decode" and "dispatch_begin" in sql:
                return "malformed-synthetic-json"
            return raw

        def transaction(self, **kwargs) -> Any:
            real = super().transaction(**kwargs)
            if fault_kind != "cancel_after_commit":
                return real

            class Transaction:
                async def start(self):
                    await real.start()

                async def rollback(self):
                    await real.rollback()

                async def commit(self):
                    await real.commit()
                    raise original

            return Transaction()

    class Pool(FaultPool):
        async def acquire(self):
            return Connection(self, await self.pool.acquire())

    async with db.connection() as owner, db.pool() as pool:
        _, _, claim, identity = await seed(db, owner, pool)
        rid = await db.reservation(owner, identity.account_id, identity.financial_scope_id)
        state = InferenceInvocation()
        fault = Pool(pool, "none")
        expected = (
            asyncio.CancelledError if fault_kind == "cancel_after_commit" else ObservationBlocked
        )
        with pytest.raises(expected) as caught:
            await InferenceRecovery(fault).mark(identity, rid, state)
        assert state.uncertainty is not None
        assert getattr(caught.value, "inference_identity") is identity
        if fault_kind == "cancel_after_commit":
            assert caught.value is original and fault.commits == 1
        assert await owner.fetchval(
            "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
            claim.task_id,
        ) is (fault_kind == "cancel_after_commit")
