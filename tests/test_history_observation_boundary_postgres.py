"""Explicit HTTP historical observation on owned SQL/POSIX, not rollout proof."""

from contextlib import asynccontextmanager
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from asyncpg.transaction import Transaction
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from cryptography.fernet import Fernet

from orchestrator.auth_tokens import hash_token
from orchestrator.auth_runtime_state import lock_auth_runtime_state
from orchestrator.services.account_control.transaction import TransactionUnavailable
from orchestrator.routes.tasks import router
from orchestrator.services.restore_history.model import AcceptPrepare, HistoryError
from orchestrator.services.restore_history.coordination import HistoryCoordinator, HistoryOperation
from orchestrator.services.restore_history.observation import HistoryObservation
from tests.restore_history_support import KEY
from tests.history_cleanup_support import held_reset
from tests.test_history_coordination import available
from orchestrator.tasks.store import TaskControlled
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from tests.test_restore_history_store import owned
from tests.test_history_composition_postgres import submission
from tests.test_history_effects_postgres import (
    db as db,
    effects as effects,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    running,
    mark,
    deadline,
    entries,
    install,
)


async def device(s, account):
    """Synthetic distinct active device/session, with actual access-token checks."""
    ident, session = uuid4(), uuid4()
    token = "synthetic-observation-" + uuid4().hex
    async with s.db.connection() as conn:
        await conn.execute(
            "INSERT INTO devices(id,user_id,display_name) VALUES($1,$2,'synthetic')",
            ident,
            account,
        )
        await conn.execute(
            "INSERT INTO sessions(id,user_id,device_id,client_kind,access_token_hash,"
            "access_expires_at,refresh_token_hash,refresh_expires_at) "
            "VALUES($1,$2,$3,'native',$4,now()+interval '1 hour',$5,now()+interval '1 day')",
            session,
            account,
            ident,
            hash_token(token),
            hash_token("synthetic-refresh-" + uuid4().hex),
        )
    return ident, {"Authorization": "Bearer " + token}


@asynccontextmanager
async def client(s, *, installed=True, tasks=None, selected=None):
    history = HistoryCoordinator(s.path, s.identity, s.ownership)
    await history.open(deadline=deadline())
    app = FastAPI()
    app.state.app_state = SimpleNamespace(
        db_pool=(tasks or s.worker)._pool,
        memory_store=(tasks or s.worker)._memory,
        task_composition=selected,
        history_observation=HistoryObservation(tasks or s.worker, history, s.projection, KEY)
        if installed
        else None,
    )
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://fixture.test"
        ) as http:
            yield http
    finally:
        await history.aclose(deadline=deadline())


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_native_reconnect_on_two_authenticated_devices_preserves_task_and_result(
    effects, completed
):
    s = effects
    await running(s)
    await s.worker.write_partial(
        s.claim.task_id, s.claim.epoch, content="synthetic partial", delta_seq=1, binding=s.claim
    )
    if completed:
        assert (
            await s.effects.complete(
                s.claim.task_id,
                s.claim.epoch,
                binding=s.claim,
                content="synthetic completed",
                deadline=deadline(),
            )
            is TaskStatus.COMPLETED
        )
    first, a = await device(s, s.account)
    second, b = await device(s, s.account)
    assert first != second
    async with client(s) as http:
        results = [await http.get("/tasks/by-key/stable-client-key", headers=h) for h in (a, b)]
        assert all(r.status_code == 200 for r in results)
        assert results[0].json() == results[1].json()
        result = results[1].json()
        assert result["id"] == str(s.accepted.task_id)
        assert result["status"] == ("completed" if completed else "running")
        assert result["content"] == ("synthetic completed" if completed else "synthetic partial")
        assert len(entries(s)) == 1
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"


@pytest.mark.asyncio
async def test_native_reconnect_denies_revoked_device_and_foreign_owner(effects):
    s = effects
    ident, a = await device(s, s.account)
    _, b = await device(s, s.account)
    async with s.db.connection() as conn:
        other = await s.db.account(conn)
    _, foreign = await device(s, other)
    async with client(s) as http:
        assert (await http.get("/tasks/by-key/stable-client-key", headers=a)).status_code == 200
        assert (
            await http.get("/tasks/by-key/stable-client-key", headers=foreign)
        ).status_code == 404
        assert (
            await http.get("/tasks/" + str(s.accepted.task_id), headers=foreign)
        ).status_code == 404
        async with s.db.connection() as conn:
            await conn.execute("UPDATE devices SET revoked_at=now() WHERE id=$1", ident)
        assert (await http.get("/tasks/by-key/stable-client-key", headers=a)).status_code == 401
        surviving = await http.get("/tasks/by-key/stable-client-key", headers=b)
        assert surviving.status_code == 200 and surviving.json()["id"] == str(s.accepted.task_id)


@pytest.mark.asyncio
async def test_native_uncertain_send_reconnect_preserves_partial_and_needs_attention(effects):
    s = effects
    await running(s)
    await s.worker.write_partial(
        s.claim.task_id, s.claim.epoch, content="synthetic partial", delta_seq=1, binding=s.claim
    )
    await mark(s)  # No transport/provider is called.
    async with s.db.connection() as conn:
        await conn.execute(
            "UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=$1",
            s.claim.task_id,
        )
    with pytest.raises(TaskControlled):
        await s.worker.claim(s.claim.task_id, worker_id="synthetic-replacement", lease_s=45)
    _, headers = await device(s, s.account)
    async with client(s) as http:
        result = await http.get("/tasks/by-key/stable-client-key", headers=headers)
    assert result.status_code == 200
    row = result.json()
    assert row["id"] == str(s.accepted.task_id) and row["status"] == "needs_attention"
    assert row["terminal_code"] == "inference_dispatch_unresolved"
    assert row["content"] == "synthetic partial"
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"
    assert len(entries(s)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_lost_accept_commit_ack_reopens_original_or_returns_recovery_without_retry(
    effects, monkeypatch, committed
):
    s = effects
    options = submission(s.account, key="lost-ack", conversation=None)
    original_exit, original_stage = Transaction.__aexit__, s.projection.stage
    armed = False

    async def stage(conn, entry, *, deadline):
        nonlocal armed
        result = await original_stage(conn, entry, deadline=deadline)
        armed = True
        return result

    async def lost_ack(tx, kind, value, traceback):
        nonlocal armed
        if not armed or kind is not None:
            return await original_exit(tx, kind, value, traceback)
        armed = False
        error = ConnectionError("synthetic lost acceptance commit acknowledgement")
        await original_exit(
            tx, None if committed else type(error), None if committed else error, None
        )
        raise error

    # A different conversation avoids the native per-conversation active guard.
    with monkeypatch.context() as patch:
        patch.setattr(s.projection, "stage", stage)
        patch.setattr(Transaction, "__aexit__", lost_ack)
        with pytest.raises((HistoryError, ConnectionError)):
            await s.accept.accept(deadline=deadline(), **options)
    recorded = entries(s)
    assert len(recorded) == 2
    prepared = recorded[-1].command.data
    assert isinstance(prepared, AcceptPrepare)
    original_task = prepared.task_id
    _, headers = await device(s, s.account)
    async with client(s) as http:
        result = await http.get("/tasks/by-key/lost-ack", headers=headers)
    if committed:
        assert result.status_code == 200 and result.json()["id"] == str(original_task)
    else:
        assert result.status_code == 409
        assert result.json() == {
            "detail": {
                "code": "restore_recovery_required",
                "task_id": str(original_task),
                "retry_safe": False,
            }
        }
    assert result.headers["cache-control"] == "no-store"
    assert s.db.sql("SELECT count(*) FROM tasks") == ("2" if committed else "1")


def recovery(response, status, task=None):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "detail": {
            "code": "restore_recovery_required" if status == 409 else "history_unavailable",
            "task_id": str(task) if task else None,
            "retry_safe": False,
        }
    }


@pytest.mark.asyncio
async def test_verified_unknown_key_is_non_cached_and_does_not_mutate_history(effects):
    s = effects
    _, headers = await device(s, s.account)
    before = (s.path / "LOG").read_bytes()
    async with client(s) as http:
        result = await http.get("/tasks/by-key/genuinely-unknown", headers=headers)
    assert result.status_code == 404 and result.headers["cache-control"] == "no-store"
    assert (s.path / "LOG").read_bytes() == before
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "suspension"])
async def test_recovery_discloses_no_prior_id_or_content_after_account_fence(effects, change):
    s = effects
    _, headers = await device(s, s.account)
    s.db.sql(
        "UPDATE entitlement_accounts SET "
        + (
            "content_generation=content_generation+1"
            if change == "generation"
            else "status='suspended'"
        )
    )
    async with client(s) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 409)


@pytest.mark.asyncio
async def test_history_schema_without_installed_reader_never_falls_back_to_sql(effects):
    s = effects
    _, headers = await device(s, s.account)
    async with client(s, installed=False) as http:
        for key in ("stable-client-key", "unknown"):
            recovery(await http.get("/tasks/by-key/" + key, headers=headers), 503)


@pytest.mark.asyncio
async def test_unavailable_sql_evidence_is_not_unknown_or_original_id(effects, monkeypatch):
    s = effects
    _, headers = await device(s, s.account)

    async def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic unavailable projection")

    monkeypatch.setattr(s.projection, "page", unavailable)
    async with client(s) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
async def test_corrupt_history_does_not_leak_sql_content_or_original_id(effects):
    s = effects
    _, headers = await device(s, s.account)
    async with client(s) as http:
        with (s.path / "LOG").open("ab") as file:
            file.write(b"synthetic corrupt tail")
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
async def test_acceptor_connection_cannot_become_runtime_read_host(effects):
    s = effects
    _, headers = await device(s, s.account)
    async with client(s, tasks=s.tasks) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
async def test_temporary_namespace_cannot_shadow_the_native_generation_guard(effects, monkeypatch):
    s = effects
    _, headers = await device(s, s.account)
    s.db.sql("UPDATE entitlement_accounts SET content_generation=1")

    async def temporary(conn):
        await conn.execute(
            "CREATE TEMP TABLE entitlement_accounts AS SELECT user_id,status,0::bigint AS content_generation FROM public.entitlement_accounts"
        )

    monkeypatch.setattr(s.worker_pool, "_init", temporary)
    await s.worker_pool.expire_connections()
    async with client(s) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["key", "canonical"])
async def test_native_key_or_encrypted_request_drift_is_not_a_verified_original_task(
    effects, change
):
    s = effects
    _, headers = await device(s, s.account)
    async with s.db.connection() as conn:
        if change == "key":
            await conn.execute(
                "UPDATE tasks SET idempotency_key='different' WHERE id=$1", s.accepted.task_id
            )
        else:
            encrypted = await conn.fetchval(
                "SELECT input_ciphertext FROM tasks WHERE id=$1", s.accepted.task_id
            )
            value = s.worker._open(encrypted)
            value["_request"] = "{}"
            await conn.execute(
                "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1",
                s.accepted.task_id,
                s.worker._seal(value),
            )
    async with client(s) as http:
        recovery(
            await http.get("/tasks/by-key/stable-client-key", headers=headers),
            409,
            s.accepted.task_id,
        )


@pytest.mark.asyncio
async def test_observation_does_not_refresh_a_rotated_native_request_digest(effects):
    s = effects
    _, headers = await device(s, s.account)
    s.db.sql("UPDATE tasks SET request_hash=repeat('0',64)")
    async with client(s) as http:
        assert (
            await http.get("/tasks/by-key/stable-client-key", headers=headers)
        ).status_code == 200
    assert s.db.sql("SELECT request_hash FROM tasks") == "0" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["app", "store", "inputs"])
async def test_observation_cannot_bypass_a_refused_selected_native_composition(
    effects, monkeypatch, selection
):
    s = effects
    _, headers = await device(s, s.account)
    if selection != "app":
        monkeypatch.setattr(
            s.worker, "_composition" if selection == "store" else "_resource_inputs", object()
        )
    async with client(s, selected=object() if selection == "app" else None) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_db", [False], indirect=True)
async def test_empty_history_without_sql_binding_is_unavailable_not_verified_absence(
    projection_db, tmp_path
):
    database = projection_db
    install(database)
    async with database.connection() as conn:
        account = await database.account(conn)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    async with database.pool() as pool:
        encryption = ContentEncryption(Fernet.generate_key().decode())
        tasks = TaskStore(pool, encryption, MemoryStore(pool, encryption))
        s = SimpleNamespace(
            db=database,
            account=account,
            path=path,
            identity=identity,
            ownership=ownership,
            worker_pool=pool,
            worker=tasks,
            projection=HistoryProjection(identity, acceptor="md_acceptor", worker="md_app"),
        )
        _, headers = await device(s, account)
        async with client(s) as http:
            recovery(await http.get("/tasks/by-key/unknown", headers=headers), 503)


@pytest.mark.asyncio
async def test_changed_history_key_is_unavailable_without_disclosing_the_original_id(
    effects, monkeypatch
):
    s = effects
    _, headers = await device(s, s.account)
    lookup = HistoryObservation.lookup

    async def wrong_key(reader, *args, **kwargs):
        reader._key = bytes([255]) * 32
        return await lookup(reader, *args, **kwargs)

    monkeypatch.setattr(HistoryObservation, "lookup", wrong_key)
    async with client(s) as http:
        recovery(await http.get("/tasks/by-key/stable-client-key", headers=headers), 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "stop"])
async def test_missing_effect_projection_returns_recovery_without_backfill(
    effects, monkeypatch, operation
):
    s = effects
    await running(s)
    _, headers = await device(s, s.account)

    async def rollback(*args, **kwargs):
        raise ConnectionError("synthetic rollback after durable effect prepare")

    with monkeypatch.context() as patch:
        patch.setattr(s.projection, "stage_effect", rollback)
        with pytest.raises(TransactionUnavailable) as caught:
            if operation == "send":
                await mark(s)
            else:
                await s.effects.request_cancel(
                    s.account, s.accepted.task_id, key=KEY, deadline=deadline()
                )
        assert caught.value.commit_attempted is False and caught.value.commit_acknowledged is False
    assert len(entries(s)) == 2
    before = (s.path / "LOG").read_bytes()
    async with client(s) as http:
        recovery(
            await http.get("/tasks/by-key/stable-client-key", headers=headers),
            409,
            s.accepted.task_id,
        )
    assert (s.path / "LOG").read_bytes() == before
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
async def test_revocation_while_waiting_for_history_is_rechecked_before_disclosure(
    effects, monkeypatch
):
    s = effects
    ident, headers = await device(s, s.account)
    _, survivor = await device(s, s.account)
    entered, release = asyncio.Event(), asyncio.Event()
    verify = HistoryOperation.verify

    async def held(operation):
        value = await verify(operation)
        entered.set()
        await release.wait()
        return value

    async with client(s) as http:
        with monkeypatch.context() as patch:
            patch.setattr(HistoryOperation, "verify", held)
            task = asyncio.create_task(http.get("/tasks/by-key/stable-client-key", headers=headers))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                async with s.db.connection() as conn, conn.transaction():
                    await lock_auth_runtime_state(conn)
                    await conn.execute("UPDATE devices SET revoked_at=now() WHERE id=$1", ident)
                release.set()
                result = await asyncio.wait_for(task, 3)
                assert result.status_code == 401 and str(s.accepted.task_id) not in result.text
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        assert (
            await http.get("/tasks/by-key/stable-client-key", headers=survivor)
        ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "failure", "deadline"])
async def test_observation_retains_actual_pool_cleanup_before_history_release(
    effects, monkeypatch, mode
):
    s = effects
    _, headers = await device(s, s.account)
    async with (
        client(s) as http,
        held_reset(s.worker_pool, monkeypatch, 2, mode == "failure") as (entered, proceed),
    ):
        # Initial bearer authentication releases once; the next reset belongs to
        # H observation's retained SQL connection.
        task = asyncio.create_task(http.get("/tasks/by-key/stable-client-key", headers=headers))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original observation cancellation")
                await asyncio.sleep(0.02)
                task.cancel("repeated observation cancellation")
                await asyncio.sleep(0.02)
                assert not task.done() and not available(s.path, "OP_LOCK")
            elif mode == "deadline":
                await asyncio.sleep(5.05)
                assert not task.done() and not available(s.path, "OP_LOCK")
            proceed.set()
            if mode == "cancel":
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                assert caught.value.args == ("original observation cancellation",)
            else:
                recovery(await task, 503)
            assert available(s.path, "OP_LOCK")
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)
