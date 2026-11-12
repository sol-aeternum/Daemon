"""Explicit isolated host; actual bearer/SQL/POSIX, zero execution or live DSNs."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
from asyncpg.transaction import Transaction
from cryptography.fernet import Fernet
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator.auth_runtime_state import lock_auth_runtime_state
from orchestrator.session_cleanup import lock_session_cleanup
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.models import ChatRequest
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_acceptance_app,
)
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator, HistoryOperation
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import AcceptPrepare, HistoryError
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.tasks.store import TaskStore
from tests.history_cleanup_support import held_reset
from tests.restore_history_support import KEY
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_composition_postgres import deadline, entries, submission
from tests.test_history_coordination import available
from tests.test_history_effects_postgres import install
from tests.test_history_observation_boundary_postgres import device
from tests.test_restore_history_store import owned


@pytest_asyncio.fixture
async def isolated(projection_db, tmp_path, monkeypatch):
    database = projection_db
    install(database)
    async with database.connection() as conn:
        account = await database.account(conn)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    encryption = ContentEncryption(Fernet.generate_key().decode())
    calls = [AsyncMock(side_effect=AssertionError("isolated host executed work")) for _ in range(3)]
    monkeypatch.setattr("litellm.acompletion", calls[0])
    monkeypatch.setattr("litellm.aembedding", calls[1])
    monkeypatch.setattr("arq.connections.ArqRedis.enqueue_job", calls[2])
    s = SimpleNamespace(
        db=database, account=account, path=path, identity=identity, ownership=ownership
    )

    @asynccontextmanager
    async def make():
        async with database.pool() as w:
            a = await asyncpg.create_pool(
                host=str(database.host),
                user="md_acceptor",
                password="",
                database=database.name,
                ssl=False,
                min_size=0,
                max_size=2,
            )
            tasks = TaskStore(a, encryption, MemoryStore(a, encryption), cover_new_inference=True)
            worker = TaskStore(w, encryption, MemoryStore(w, encryption))
            projection = HistoryProjection(identity, acceptor="md_acceptor", worker="md_app")
            acceptance = HistoryAcceptance(
                tasks, HistoryCoordinator(path, identity, ownership), projection, KEY
            )
            reader = HistoryObservation(
                worker, HistoryCoordinator(path, identity, ownership), projection, KEY
            )
            host = IsolatedAcceptanceHost(
                acceptance,
                reader,
                fingerprint_key="synthetic-pepper",
                max_body_bytes=4096,
                keepalive_s=1,
            )
            value = SimpleNamespace(
                host=host,
                app=create_isolated_acceptance_app(host),
                a=a,
                w=w,
                tasks=tasks,
                worker=worker,
                acceptance=acceptance,
                reader=reader,
                projection=projection,
            )
            try:
                await host.open(deadline=deadline())
                yield value
            finally:
                await host.aclose(deadline=deadline())
                await asyncio.wait_for(a.close(), 5)

    s.make = make
    yield s
    for call in calls:
        call.assert_not_called()
    assert database.sql("SELECT count(*) FROM task_attempts") == "0"
    assert database.sql("SELECT count(*) FROM tasks WHERE status<>'queued'") == "0"
    assert (
        database.sql("SELECT count(*) FROM task_inference_recovery WHERE may_have_dispatched")
        == "0"
    )


def payload(**options):
    return ChatRequest(message="synthetic text", **options)


async def submit(v, headers, key="stable", **options):
    return await v.host.submit(
        headers.get("Authorization"), key, payload(**options), deadline=deadline()
    )


async def post(app, headers, body, *, key="stable", disconnect=None, received=None):
    """Consume real ASGI SSE prefix, then disconnect without cancelling the task."""
    messages, disconnected = [], disconnect if disconnect is not None else asyncio.Event()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and (
            b"event: conversation" in message.get("body", b"")
            or not message.get("more_body", False)
        ):
            if received is not None:
                received.set()
            if disconnect is None or not message.get("more_body", False):
                disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("test", 1),
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in {
                **headers,
                "Idempotency-Key": key,
                "Content-Type": "application/json",
            }.items()
        ],
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), 15)
    finally:
        disconnected.set()
    start = next(m for m in messages if m["type"] == "http.response.start")
    return SimpleNamespace(
        status=start["status"],
        headers=dict(start["headers"]),
        body=b"".join(m.get("body", b"") for m in messages),
    )


@pytest.mark.asyncio
async def test_real_chat_ack_then_second_device_after_physical_host_and_pool_restart(isolated):
    s = isolated
    _, a1 = await device(s, s.account)
    _, a2 = await device(s, s.account)
    async with s.make() as first:
        old_pools = first.a, first.w
        result = await post(first.app, a1, {"message": "synthetic text", "answer_detail": "brief"})
        assert result.status == 200 and b"event: task" in result.body
        assert b"event: done" not in result.body and b"event: token" not in result.body
        assert result.headers[b"cache-control"] == b"no-store"
        task_id = result.headers[b"x-daemon-task-id"].decode()
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM messages") == "2"
        recorded = entries(s)
        assert len(recorded) == 1
        prepared = recorded[0].command.data
        assert isinstance(prepared, AcceptPrepare)
        assert str(prepared.task_id) == task_id
    assert first.host._closed and all(pool._closed for pool in old_pools)
    async with s.make() as second:
        assert second.a not in old_pools and second.w not in old_pools
        async with AsyncClient(transport=ASGITransport(second.app), base_url="http://test") as http:
            found = await http.get("/tasks/by-key/stable", headers=a2)
            assert found.status_code == 200 and found.json()["id"] == task_id
            assert found.json()["status"] == "queued" and found.json()["attempt_count"] == 0
            assert found.headers["cache-control"] == "no-store"
            assert (await http.post(f"/tasks/{task_id}/cancel", headers=a2)).status_code == 404
            assert (await http.post("/v1/chat/completions", headers=a2, json={})).status_code == 404
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await submit(second, a2, answer_detail="brief")
        assert second.acceptance._blocked
        assert (
            await second.host.lookup(a2["Authorization"], "stable", deadline=deadline())
        ).kind == "task"
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_concurrent_same_key_is_one_original_acceptance_never_replay(isolated, conflicting):
    s = isolated
    _, a = await device(s, s.account)
    _, b = await device(s, s.account)
    async with s.make() as v:
        results = await asyncio.gather(
            submit(v, a),
            v.host.submit(
                b["Authorization"],
                "stable",
                ChatRequest(message="different" if conflicting else "synthetic text"),
                deadline=deadline(),
            ),
            return_exceptions=True,
        )
        assert any(isinstance(r, Exception) for r in results)
        assert sum(not isinstance(r, BaseException) for r in results) <= 1
        found = await v.host.lookup(b["Authorization"], "stable", deadline=deadline())
        assert found.kind == "task" and found.snapshot is not None
        prepared = entries(s)[0].command.data
        assert isinstance(prepared, AcceptPrepare)
        assert found.snapshot.task_id == prepared.task_id
    assert s.db.sql("SELECT count(*) FROM tasks") == "1" and len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "bad",
        "revoked_session",
        "revoked_device",
        "expired",
        "device_owner",
        "suspended",
        "missing_account",
    ],
)
async def test_real_auth_and_account_refusals_have_no_acceptance(isolated, kind):
    s = isolated
    ident, headers = await device(s, s.account)
    async with s.db.connection() as conn:
        if kind == "revoked_session":
            await conn.execute("UPDATE sessions SET revoked_at=now() WHERE device_id=$1", ident)
        elif kind == "revoked_device":
            await conn.execute("UPDATE devices SET revoked_at=now() WHERE id=$1", ident)
        elif kind == "expired":
            await conn.execute(
                "UPDATE sessions SET access_expires_at=now()-interval '1 second' WHERE device_id=$1",
                ident,
            )
        elif kind == "device_owner":
            other = await s.db.account(conn)
            await conn.execute("UPDATE devices SET user_id=$2 WHERE id=$1", ident, other)
        elif kind == "suspended":
            await conn.execute(
                "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", s.account
            )
        elif kind == "missing_account":
            await conn.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", s.account)
    if kind == "missing":
        headers = {}
    elif kind == "bad":
        headers = {"Authorization": "Bearer synthetic-unknown"}
    async with s.make() as v:
        async with AsyncClient(transport=ASGITransport(v.app), base_url="http://test") as http:
            response = await http.post(
                "/chat",
                headers={**headers, "Idempotency-Key": "stable"},
                json={"message": "synthetic text"},
            )
            assert response.status_code == (
                503 if kind in ("suspended", "missing_account") else 401
            )
            assert response.headers["cache-control"] == "no-store"
        assert not v.acceptance._blocked  # Controlled auth negatives do not replace/renew a writer.
    assert len(entries(s)) == 0 and s.db.sql("SELECT count(*) FROM tasks") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "suspension"])
async def test_original_capture_is_not_recaptured_after_concurrent_account_change(
    isolated, monkeypatch, change
):
    s = isolated
    _, headers = await device(s, s.account)
    from orchestrator.services.restore_history import acceptance_host

    original = acceptance_host.capture_generation
    captures = []

    async def capture(conn, account):
        value = await original(conn, account)
        captures.append(value)
        async with s.db.connection() as control, control.transaction():
            await control.execute(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", account
            )
            await control.execute(
                "UPDATE entitlement_accounts SET "
                + (
                    "content_generation=content_generation+1"
                    if change == "generation"
                    else "status='suspended'"
                )
                + " WHERE user_id=$1",
                account,
            )
        return value

    async with s.make() as v:
        monkeypatch.setattr(acceptance_host, "capture_generation", capture)
        with pytest.raises(Exception):
            await submit(v, headers)
        assert len(captures) == 1 and captures[0].generation == 0
    assert len(entries(s)) == 0 and s.db.sql("SELECT count(*) FROM tasks") == "0"


@pytest.mark.asyncio
async def test_foreign_owner_unknown_key_and_unsupported_inputs_are_no_store(isolated):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.db.connection() as conn:
        foreign = await s.db.account(conn)
    _, other = await device(s, foreign)
    async with s.make() as v:
        accepted = await submit(v, headers)
        async with AsyncClient(transport=ASGITransport(v.app), base_url="http://test") as http:
            for h, key in ((other, "stable"), (headers, "unknown"), (headers, "bad$key")):
                result = await http.get("/tasks/by-key/" + key, headers=h)
                assert result.status_code == 404 and result.headers["cache-control"] == "no-store"
                assert str(accepted.task.task_id) not in result.text
            denied = await http.get(
                "/tasks/by-key/bad$key", headers={"Authorization": "Bearer synthetic-unknown"}
            )
            assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
            for body, code in (
                ({"message": "x", "attachments": [{"name": "unsupported"}]}, 503),
                (
                    {
                        "message": "x",
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
                    },
                    503,
                ),
                ({"message": "x", "user_id": str(foreign)}, 503),
                ({"message": "x" * 5000}, 413),
                ({"message": "   "}, 422),
            ):
                result = await http.post(
                    "/chat", headers={**headers, "Idempotency-Key": "other"}, json=body
                )
                assert result.status_code == code and result.headers["cache-control"] == "no-store"
            for key in (None, "bad$key"):
                result = await http.post(
                    "/chat",
                    headers={**headers, **({"Idempotency-Key": key} if key else {})},
                    json={"message": "x"},
                )
                assert result.status_code == 422 and result.headers["cache-control"] == "no-store"
    assert len(entries(s)) == 1 and s.db.sql("SELECT count(*) FROM tasks") == "1"


@pytest.mark.asyncio
async def test_sql_only_historical_key_never_enters_native_replay_or_refreshes_digest(isolated):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        # Deliberately seed the existing uncomposed native gap. This is not an
        # alternate runtime route, a host acknowledgement or replay authority.
        original = await v.tasks.accept(
            **submission(s.account, key="stable", message="synthetic text")
        )
        s.db.sql("UPDATE tasks SET request_hash=repeat('0',64)")
        with pytest.raises(HistoryError, match="native_acceptance_not_fresh"):
            await submit(v, headers)
        assert v.acceptance._blocked
        async with AsyncClient(transport=ASGITransport(v.app), base_url="http://test") as http:
            found = await http.get("/tasks/by-key/stable", headers=headers)
            assert found.status_code == 409 and str(original.task_id) not in found.text
            assert found.json()["detail"]["retry_safe"] is False
    assert s.db.sql("SELECT request_hash FROM tasks") == "0" * 64
    assert s.db.sql("SELECT count(*) FROM tasks") == "1" and len(entries(s)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["before_append", "after_append", "commit_before", "commit_after"]
)
async def test_uncertain_acceptance_is_never_replayed_after_restart(isolated, monkeypatch, fault):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        original_stage, original_exit = v.projection.stage, Transaction.__aexit__
        armed = False

        async def stage(conn, entry, *, deadline):
            nonlocal armed
            if fault == "after_append":
                raise ConnectionError("synthetic rollback after H append")
            value = await original_stage(conn, entry, deadline=deadline)
            armed = True
            return value

        async def lost(tx, kind, value, traceback):
            nonlocal armed
            if not armed or kind is not None:
                return await original_exit(tx, kind, value, traceback)
            armed = False
            error = ConnectionError("synthetic lost native commit acknowledgement")
            await original_exit(
                tx,
                None if fault == "commit_after" else type(error),
                None if fault == "commit_after" else error,
                None,
            )
            raise error

        async def no_append(*args):
            raise ConnectionError("synthetic refusal before append")

        with monkeypatch.context() as patch:
            if fault == "before_append":
                patch.setattr(HistoryOperation, "append", no_append)
            patch.setattr(v.projection, "stage", stage)
            if fault.startswith("commit"):
                patch.setattr(Transaction, "__aexit__", lost)
            with pytest.raises((HistoryError, ConnectionError)):
                await submit(v, headers)
        assert v.acceptance._blocked
        # An independent reader may observe, but that never repairs/retries A.
        found = await v.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        assert found.kind == (
            "unknown"
            if fault == "before_append"
            else "task"
            if fault == "commit_after"
            else "recovery"
        )
    count = 1 if fault == "commit_after" else 0
    assert s.db.sql("SELECT count(*) FROM tasks") == str(count)
    # A full restarted host refuses mismatched H/SQL rather than installing a
    # writer. Independent historical reader behavior is covered above.
    if fault == "before_append" or fault == "commit_after":
        async with s.make() as restarted:
            if fault == "commit_after":
                with pytest.raises(HistoryError, match="historical_acceptance"):
                    await submit(restarted, headers)
    else:
        with pytest.raises(HistoryError, match="reader_reconciliation_required"):
            async with s.make():
                pytest.fail("mismatched host opened")
    assert s.db.sql("SELECT count(*) FROM tasks") == str(count)


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["session", "device"])
async def test_revocation_wins_during_h_wait_before_real_auth(isolated, monkeypatch, which):
    s = isolated
    ident, headers = await device(s, s.account)
    entered, proceed = asyncio.Event(), asyncio.Event()
    from orchestrator.services.restore_history import acceptance_host

    auth = acceptance_host.IsolatedAcceptanceHost._auth

    async def held(host, conn, token, budget):
        entered.set()
        await proceed.wait()
        return await auth(host, conn, token, budget)

    async with s.make() as v:
        monkeypatch.setattr(acceptance_host.IsolatedAcceptanceHost, "_auth", held)
        task = asyncio.create_task(submit(v, headers))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with s.db.connection() as conn, conn.transaction():
                await lock_auth_runtime_state(conn)
                await conn.execute(
                    "UPDATE "
                    + (
                        "sessions SET revoked_at=now() WHERE device_id=$1"
                        if which == "session"
                        else "devices SET revoked_at=now() WHERE id=$1"
                    ),
                    ident,
                )
            proceed.set()
            with pytest.raises(HTTPException) as caught:
                await task
            assert caught.value.status_code == 401
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)
    assert len(entries(s)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["session", "device"])
async def test_auth_row_locks_cover_native_acceptance_until_commit(isolated, monkeypatch, which):
    s = isolated
    ident, headers = await device(s, s.account)
    entered, proceed = asyncio.Event(), asyncio.Event()
    async with s.make() as v:
        original = v.projection.stage

        async def held(conn, entry, *, deadline):
            result = await original(conn, entry, deadline=deadline)
            entered.set()
            await proceed.wait()
            return result

        monkeypatch.setattr(v.projection, "stage", held)
        task = asyncio.create_task(submit(v, headers))
        async with s.db.connection() as control:
            pid = await control.fetchval("SELECT pg_backend_pid()")

            async def revoke():
                # Deliberately no auth advisory: this is the logout-style writer.
                await control.execute(
                    "UPDATE "
                    + (
                        "sessions SET revoked_at=now() WHERE device_id=$1"
                        if which == "session"
                        else "devices SET revoked_at=now() WHERE id=$1"
                    ),
                    ident,
                )

            revoked = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                revoked = asyncio.create_task(revoke())
                async with s.db.connection() as admin:
                    await s.db.wait_lock(admin, pid)
                assert not revoked.done()
                proceed.set()
                accepted = await task
                await revoked
                assert accepted.task.created
                assert (
                    await v.host.lookup(headers["Authorization"], "stable", deadline=deadline())
                ).kind == "denied"
            finally:
                proceed.set()
                await asyncio.gather(task, *([revoked] if revoked else []), return_exceptions=True)
    assert len(entries(s)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["a", "w"])
async def test_repeated_cancel_retains_both_real_holder_resets_and_h(isolated, monkeypatch, side):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        # A releases parity, then native key lookup, then acceptance. W releases
        # its single auth lease after the complete native acceptance has settled.
        async with held_reset(getattr(v, side), monkeypatch, 3 if side == "a" else 1, False) as (
            entered,
            proceed,
        ):
            task = asyncio.create_task(submit(v, headers))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel("original host cancellation")
                await asyncio.sleep(0.02)
                task.cancel("repeated host cancellation")
                await asyncio.sleep(0.02)
                assert not task.done() and not available(s.path, "OP_LOCK")
                proceed.set()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                assert caught.value.args == ("original host cancellation",)
                assert v.acceptance._blocked and available(s.path, "OP_LOCK")
            finally:
                proceed.set()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_fences_admission_and_drains_an_active_auth_lease(isolated, monkeypatch):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        async with held_reset(v.w, monkeypatch, 1, False) as (entered, proceed):
            task = asyncio.create_task(submit(v, headers))
            close = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                close = asyncio.create_task(v.host.aclose(deadline=deadline()))
                await asyncio.sleep(0.03)
                assert v.host._closing and not close.done()
                with pytest.raises(HistoryError, match="closed"):
                    await submit(v, headers, key="new")
                assert not available(s.path, "OP_LOCK")
                proceed.set()
                await close
                assert v.host._closed
            finally:
                proceed.set()
                await asyncio.gather(task, *([close] if close else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_closes_the_actual_open_acceptance_stream_before_h_owners(isolated):
    s = isolated
    _, headers = await device(s, s.account)
    received, disconnect = asyncio.Event(), asyncio.Event()
    async with s.make() as v:
        task = asyncio.create_task(
            post(
                v.app,
                headers,
                {"message": "synthetic text"},
                disconnect=disconnect,
                received=received,
            )
        )
        try:
            await asyncio.wait_for(received.wait(), 5)
            assert v.host._streams and not task.done()
            await asyncio.wait_for(v.host.aclose(deadline=deadline()), 5)
            assert v.host._closed and not v.host._streams and not v.host._active
            assert v.acceptance._history._closed and v.reader._history._closed
        finally:
            disconnect.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_expiry_is_rechecked_after_a_real_session_row_lock_wait(isolated, monkeypatch):
    s = isolated
    ident, headers = await device(s, s.account)
    entered = asyncio.Event()
    pids = []
    original = IsolatedAcceptanceHost._auth

    async def auth(host, conn, token, budget):
        pids.append(conn.get_server_pid())
        entered.set()
        return await original(host, conn, token, budget)

    async with s.make() as v:
        monkeypatch.setattr(IsolatedAcceptanceHost, "_auth", auth)
        async with s.db.connection() as control:
            task = None
            try:
                async with control.transaction():
                    await control.execute(
                        "UPDATE sessions SET access_expires_at=clock_timestamp()+interval '100 milliseconds' WHERE device_id=$1",
                        ident,
                    )
                    task = asyncio.create_task(submit(v, headers))
                    await asyncio.wait_for(entered.wait(), 5)
                    # Synthetic cluster owner only: ordinary md_owner cannot
                    # inspect another principal's pg_stat_activity wait fields.
                    async with s.db.connection("postgres") as admin:
                        await s.db.wait_lock(admin, pids[0])
                    await asyncio.sleep(0.11)
                with pytest.raises(HTTPException) as caught:
                    await task
                assert caught.value.status_code == 401
            finally:
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
        assert not v.acceptance._blocked
    assert len(entries(s)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["auth", "cleanup"])
async def test_w_auth_wait_has_its_own_real_local_limit(isolated, which):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        async with s.db.connection() as control, control.transaction():
            await (
                lock_auth_runtime_state(control)
                if which == "auth"
                else lock_session_cleanup(control)
            )
            with pytest.raises(asyncpg.LockNotAvailableError):
                await submit(v, headers)
        assert v.acceptance._blocked and available(s.path, "OP_LOCK")
    assert len(entries(s)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["a", "w"])
async def test_failed_actual_holder_reset_cannot_acknowledge_acceptance(
    isolated, monkeypatch, side
):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        async with held_reset(getattr(v, side), monkeypatch, 3 if side == "a" else 1, True) as (
            entered,
            proceed,
        ):
            task = asyncio.create_task(submit(v, headers))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert not task.done() and not available(s.path, "OP_LOCK")
                proceed.set()
                with pytest.raises(Exception):
                    await task
                assert v.acceptance._blocked and available(s.path, "OP_LOCK")
            finally:
                proceed.set()
                await asyncio.gather(task, return_exceptions=True)
        assert (
            await v.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        ).kind == "task"


@pytest.mark.asyncio
async def test_lost_w_auth_commit_after_known_a_commit_is_not_acknowledgement(
    isolated, monkeypatch
):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.make() as v:
        original = Transaction.__aexit__
        armed = False
        stage = v.projection.stage

        async def staged(conn, entry, *, deadline):
            nonlocal armed
            result = await stage(conn, entry, deadline=deadline)
            armed = True
            return result

        async def lost(tx, kind, value, traceback):
            nonlocal armed
            if (
                armed
                and kind is None
                and await tx._connection.fetchval("SELECT CURRENT_USER::text") == "md_app"
            ):
                armed = False
                await original(tx, kind, value, traceback)
                raise ConnectionError("synthetic lost W auth commit acknowledgement")
            return await original(tx, kind, value, traceback)

        with monkeypatch.context() as patch:
            patch.setattr(v.projection, "stage", staged)
            patch.setattr(Transaction, "__aexit__", lost)
            with pytest.raises(ConnectionError, match="lost W"):
                await submit(v, headers)
        assert v.acceptance._blocked
        found = await v.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        assert found.kind == "task" and found.snapshot is not None
        prepared = entries(s)[0].command.data
        assert isinstance(prepared, AcceptPrepare)
        assert found.snapshot.task_id == prepared.task_id
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"


@pytest.mark.asyncio
async def test_foreign_conversation_never_discloses_or_writes_other_owners_content(isolated):
    s = isolated
    _, headers = await device(s, s.account)
    async with s.db.connection() as conn:
        other = await s.db.account(conn)
    _, foreign = await device(s, other)
    async with s.make() as v:
        accepted = await submit(v, foreign)
        result = await post(
            v.app,
            headers,
            {"message": "synthetic text", "conversation_id": str(accepted.task.conversation_id)},
            key="other",
        )
        assert result.status == 404 and str(accepted.task.task_id).encode() not in result.body
    assert len(entries(s)) == 1 and s.db.sql("SELECT count(*) FROM tasks") == "1"
