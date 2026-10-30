"""Actual isolated A/W composition and authenticated HTTP/worker/sweep paths.

Uses the existing owned network-none cluster, never a user/application DSN.
Only synthetic principals, identities, providers and journals are provisioned.
"""

from contextlib import asynccontextmanager, AsyncExitStack
import asyncio
import contextlib
import sys
import traceback
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator.config import Settings
from orchestrator import main, compute_runtime
from orchestrator.auth_tokens import hash_token
from orchestrator.entitlements.service import EntitlementService
from orchestrator.db import AppState
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.routes.tasks import task_acceptor, task_store
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks import runner
from orchestrator.tasks.composition import qualify_task_composition
from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_compute_runtime import _qualified_policy, _route


@asynccontextmanager
async def pools(db, *, a_role="md_acceptor", w_role="md_app"):
    # Both pools are created by this host from the SAME existing owned endpoint.
    a = await asyncpg.create_pool(
        host=str(db.host),
        database=db.name,
        user=a_role,
        password="",
        ssl=False,
        min_size=0,
        max_size=2,
        timeout=2,
    )
    try:
        async with AsyncExitStack() as owned:
            w = await asyncpg.create_pool(
                host=str(db.host),
                database=db.name,
                user=w_role,
                password="",
                ssl=False,
                min_size=0,
                max_size=3,
                timeout=2,
            )
            owned.push_async_callback(w.close)
            yield a, w
    finally:
        await a.close()


def settings():
    return cast(Any, Settings)(
        _env_file=None,
        durable_chat_enabled=True,
        redis_url=None,
        daemon_encryption_key=Fernet.generate_key().decode(),
        daemon_auth_pepper="p" * 64,
        openrouter_api_key="synthetic-no-network",
    )


@pytest_asyncio.fixture
async def host(db):
    db.sql(
        "INSERT INTO public._migrations(filename) VALUES('049_attempt_lifecycle.sql'),"
        "('050_task_inference_recovery.sql')"
    )
    value = settings()
    enc = ContentEncryption(value.daemon_encryption_key)
    async with pools(db) as (a, w):
        bundle = await qualify_task_composition(
            enabled=True, acceptor_pool=a, worker_pool=w, encryption=enc, settings=value
        )
        assert bundle is not None
        state = AppState(
            value, db_pool=w, memory_store=bundle.tasks._memory, task_composition=bundle
        )
        ctx = dict(
            task_store=bundle.tasks,
            store=bundle.tasks._memory,
            db_pool=w,
            settings=value,
            task_composition=bundle.worker,
        )
        async with db.connection() as owner:
            uid = await db.account(owner)
        yield SimpleNamespace(
            db=db,
            a=a,
            w=w,
            bundle=bundle,
            state=state,
            ctx=ctx,
            uid=uid,
            settings=value,
            encryption=enc,
        )


async def accept(host, *, key=None):
    return await host.bundle.acceptor.accept(
        user_id=host.uid,
        conversation_id=None,
        new_conversation_title="Cloud question",
        pipeline="cloud",
        user_message="What time is it?",
        task_input={"message": "question"},
        request_hash="a" * 64,
        idempotency_key=key,
        assistant_model=None,
        captured_generation=CapturedGeneration(host.uid, 0),
    )


@pytest.mark.asyncio
async def test_separated_factory_real_atomic_acceptance_and_duplicate(host):
    bundle = host.bundle
    assert task_acceptor(host.state) is bundle.acceptor
    assert task_store(host.state) is bundle.tasks
    assert not hasattr(bundle.worker, "acceptor")
    key = str(uuid4())
    first = await accept(host, key=key)
    assert first.created and not (await accept(host, key=key)).created
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 1
        assert (
            await owner.fetchval("SELECT may_have_dispatched FROM task_inference_recovery") is False
        )
        row = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", first.task_id)
        sealed = bundle.acceptor._open(row["input_ciphertext"])
        assert sealed["origin_account_id"] == str(host.uid)
        assert sealed["origin_account_generation"] == 0
        assert sealed["_inference_recovery_version"] == 1
    assert await runner.sweep_tasks(host.ctx) == 0  # Accounting works without Redis/key transport.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["task_store", "db_pool", "store", "settings", "task_composition"]
)
async def test_partial_worker_context_refuses_before_wakeup(host, field):
    await accept(host)
    ctx = {**host.ctx, "redis": object()}
    ctx.pop(field)
    with pytest.raises(ObservationBlocked):
        await runner.sweep_tasks(ctx)
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT wake_seq FROM tasks") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["db_pool", "memory_store", "task_composition"])
async def test_partial_http_binding_refuses_before_acceptance(host, field):
    setattr(host.state, field, None)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        task_acceptor(host.state)
    assert refused.value.status_code == 503
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM tasks") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "a_role,w_role",
    [
        ("md_app", "md_app"),
        ("md_acceptor", "md_acceptor"),
        ("md_app", "md_acceptor"),
        ("md_owner", "md_app"),
        ("md_acceptor", "md_owner"),
    ],
)
async def test_wrong_principal_refuses_without_binding(db, a_role, w_role):
    value = settings()
    async with pools(db, a_role=a_role, w_role=w_role) as (a, w):
        with pytest.raises(ObservationBlocked):
            await qualify_task_composition(
                enabled=True,
                acceptor_pool=a,
                worker_pool=w,
                encryption=ContentEncryption(value.daemon_encryption_key),
                settings=value,
            )
    async with db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM deletion_journal_binding") == 0


@pytest_asyncio.fixture
async def http(host, monkeypatch, owned_search_redis):
    from arq.connections import ArqRedis

    owned_search_redis.verify_socket()
    redis = ArqRedis(unix_socket_path=str(owned_search_redis.socket))
    assert (await redis.info("server"))["run_id"] == owned_search_redis.run_id
    host.state.redis = redis
    host.ctx["redis"] = redis
    host.settings.mock_llm = False
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", host.settings.daemon_encryption_key)
    # Only provider/routing and optional external rate admission are scripted.
    # Authentication, capture, acceptance, financial SQL and task execution are real.
    route = _route(input_price=1000)
    _qualified_policy(monkeypatch, route=route)
    monkeypatch.setattr(main, "load_inference_policy", compute_runtime.load_inference_policy)
    monkeypatch.setattr(main, "get_settings", lambda: host.settings)
    monkeypatch.setattr(compute_runtime, "get_settings", lambda: host.settings)

    async def unthrottled(**kwargs):
        pass

    monkeypatch.setattr(main, "_enforce_chat_rate_limit", unthrottled)
    from orchestrator import daemon

    host.streaming_errors = []
    # Synthetic-only diagnostics: production intentionally logs no raw exception.
    monkeypatch.setattr(
        daemon.logger,
        "error",
        lambda *args, **kwargs: host.streaming_errors.append(
            "".join(traceback.format_exception(sys.exception()))
        ),
    )
    app = FastAPI()
    app.state.app_state = host.state
    app.dependency_overrides[main.get_settings] = lambda: host.settings
    # chat's Depends captured the original function before monkeypatching.
    from orchestrator.config import get_settings

    app.dependency_overrides[get_settings] = lambda: host.settings
    app.add_api_route("/chat", main.chat, methods=["POST"])
    from orchestrator.routes.tasks import router

    app.include_router(router)
    tokens = []
    async with host.db.connection() as owner:
        for uid in (host.uid, await host.db.account(owner)):
            token, device = "synthetic-" + uuid4().hex, uuid4()
            await owner.execute(
                "INSERT INTO devices(id,user_id,display_name) VALUES($1,$2,'phone')", device, uid
            )
            await owner.execute(
                "INSERT INTO sessions(user_id,device_id,client_kind,access_token_hash,"
                "access_expires_at,refresh_token_hash,refresh_expires_at) VALUES($1,$2,'native',$3,"
                "now()+interval '1 hour',$4,now()+interval '1 day')",
                uid,
                device,
                hash_token(token),
                hash_token("refresh-" + token),
            )
            tokens.append(token)
    host.headers = {
        "Authorization": "Bearer " + tokens[0],
        "X-Daemon-Client-Features": "task-cancel, task-reset",
    }
    host.foreign_headers = {"Authorization": "Bearer " + tokens[1]}
    host.route = route
    host.owned_redis = owned_search_redis
    host.pacers = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            host.client = client
            yield host
    finally:
        for pacer in host.pacers:
            await pacer.aclose()
        await redis.aclose()


async def submit_http(host, *, key=None):
    pending = asyncio.create_task(
        host.client.post(
            "/chat",
            headers={**host.headers, "Idempotency-Key": key or uuid4().hex},
            json={
                "message": "What time is it?",
                "model": host.route.model,
                "disable_memory_write": True,
            },
        )
    )
    try:
        async with asyncio.timeout(5):
            while not pending.done():
                task = await host.w.fetchval(
                    "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
                )
                if task is not None:
                    return pending, task
                await asyncio.sleep(0.01)
        response = await pending
        raise AssertionError(
            f"HTTP acceptance did not create task: {response.status_code} {response.text}"
        )
    except BaseException:
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize("use_tool", [False, True, "search"])
async def test_authenticated_http_real_worker_and_independent_sweep(http, monkeypatch, use_tool):
    search_requests = []
    if use_tool == "search":
        import httpx
        from orchestrator.tools import web_search
        from orchestrator.services.search_pacing import SearchPacer
        from orchestrator.entitlements.policy import parse_inference_policy
        from tests.test_metered_web_search import _approved_document, BRAVE_PAYLOAD

        old_policy = compute_runtime.load_inference_policy()
        services = parse_inference_policy(_approved_document())
        policy = SimpleNamespace(
            routes=old_policy.routes,
            requirements=services.requirements,
            tool_service=services.tool_service,
        )
        monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: policy)
        monkeypatch.setattr(main, "load_inference_policy", lambda: policy)
        http.settings.brave_api_key = "synthetic-search-no-network"
        pacer = SearchPacer(
            client_factory=http.owned_redis.connect, namespace="composition:" + uuid4().hex
        )
        http.pacers.append(pacer)

        async def search(request):
            search_requests.append(request)
            assert request.url.host == "api.search.brave.com"
            assert compute_runtime.current_scope().service._pool is http.w
            async with http.db.connection() as owner:
                # Metered tools share the attempt's financial scope/send authority;
                # only SDK inference dispatch creates these metadata bindings.
                assert (
                    await owner.fetchval("SELECT count(*) FROM compute_reservation_metadata") == 1
                )
                hold = await owner.fetchrow(
                    "SELECT * FROM entitlement_reservations WHERE user_id=$1 AND provider='brave'",
                    http.uid,
                )
                assert hold is not None and hold["status"] == "open"
                assert hold["reserved_microusd"] == 7000
                assert hold["scope_id"] == compute_runtime.current_scope().scope_id
                assert not await owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM compute_reservation_metadata WHERE reservation_id=$1)",
                    hold["id"],
                )
            return httpx.Response(200, json=BRAVE_PAYLOAD)

        transport = httpx.MockTransport(search)
        native_tool = web_search.WebSearchTool
        monkeypatch.setattr(
            web_search,
            "WebSearchTool",
            lambda **kwargs: native_tool(**kwargs, _transport=transport, pacer=pacer),
        )
    sends = []

    async def provider(**kwargs):
        scope = compute_runtime.current_scope()
        sends.append(scope.inference_identity)
        assert scope.service._pool is http.w
        assert scope.inference_recovery is http.bundle.recovery
        async with http.db.connection() as owner:
            assert (
                await owner.fetchval("SELECT may_have_dispatched FROM task_inference_recovery")
                is True
            )
            assert await owner.fetchval("SELECT count(*) FROM compute_reservation_metadata") == len(
                sends
            )

        async def stream():
            delta = {"content": "The cloud answer is saved."}
            if use_tool and len(sends) == 1:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "time-1",
                            "type": "function",
                            "function": {
                                "name": "web_search" if use_tool == "search" else "get_time",
                                "arguments": '{"query":"cloud research","num_results":1}'
                                if use_tool == "search"
                                else "{}",
                            },
                        }
                    ]
                }
            yield {"choices": [{"delta": delta}]}
            yield {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "tool_calls" if use_tool and len(sends) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        return stream()

    monkeypatch.setattr(compute_runtime.litellm, "acompletion", provider)
    key = uuid4().hex
    pending, task = await submit_http(http, key=key)
    if use_tool is False:
        # Already accepted work, Stop/observation and sweep survive switching
        # NEW acceptance off; the flag does not certify remote cessation.
        http.settings.durable_chat_enabled = False
    try:
        assert await runner.run_chat_task(http.ctx, str(task)) == "completed", http.streaming_errors
        response = await asyncio.wait_for(pending, 5)
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
    assert response.status_code == 200 and "The cloud answer is saved." in response.text
    assert len(sends) == (2 if use_tool else 1) and len(set(sends)) == 1
    assert await runner.sweep_tasks(http.ctx) == 0
    assert await runner.run_chat_task(http.ctx, str(task)) == "task_already_terminal"
    snapshot = await http.client.get(f"/tasks/{task}", headers=http.headers)
    assert snapshot.status_code == 200 and snapshot.json()["status"] == "completed"
    async with http.db.connection() as owner:
        before = await owner.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", task)
        await owner.execute(
            "UPDATE entitlement_accounts SET status='suspended',content_generation=7 WHERE user_id=$1",
            http.uid,
        )
    replay = await http.client.post(
        "/chat",
        headers={**http.headers, "Idempotency-Key": key},
        json={
            "message": "What time is it?",
            "model": http.route.model,
            "disable_memory_write": True,
        },
    )
    assert replay.status_code == 200 and replay.headers["X-Daemon-Task-Id"] == str(task)
    assert len(sends) == (2 if use_tool else 1)
    async with http.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 1
        assert (
            await owner.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", task) == before
        )
    for suffix in ("", "/events"):
        assert (
            await http.client.get(f"/tasks/{task}{suffix}", headers=http.foreign_headers)
        ).status_code == 404
    assert (
        await http.client.post(f"/tasks/{task}/cancel", headers=http.foreign_headers)
    ).status_code == 404
    async with http.db.connection() as owner:
        holds = await owner.fetch(
            "SELECT * FROM entitlement_reservations WHERE user_id=$1", http.uid
        )
        assert len(holds) == len(sends) + (1 if use_tool == "search" else 0)
        assert all(r["status"] == "settled" for r in holds)
        assert {r["scope_id"] for r in holds} == {sends[0].financial_scope_id}
        if use_tool == "search":
            assert len(search_requests) == 1
            assert next(r for r in holds if r["provider"] == "brave")["actual_microusd"] == 5000


@pytest.mark.asyncio
async def test_authenticated_http_stop_before_worker_never_dispatches(http, monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail("stopped task dispatched")

    monkeypatch.setattr(compute_runtime.litellm, "acompletion", forbidden)
    pending, task = await submit_http(http)
    try:
        stopped = await http.client.post(f"/tasks/{task}/cancel", headers=http.headers)
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        assert await runner.run_chat_task(http.ctx, str(task)) == "task_already_terminal"
        assert await runner.sweep_tasks(http.ctx) == 0
        assert (await asyncio.wait_for(pending, 5)).status_code == 200
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
    async with http.db.connection() as owner:
        assert (
            await owner.fetchval("SELECT may_have_dispatched FROM task_inference_recovery") is False
        )
        assert await owner.fetchval("SELECT count(*) FROM entitlement_reservations") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "GRANT INSERT ON task_inference_recovery TO md_acceptor",
        "GRANT INSERT(may_have_dispatched) ON task_inference_recovery TO md_acceptor",
        "GRANT INSERT(task_id) ON task_inference_recovery TO md_acceptor WITH GRANT OPTION",
        "REVOKE INSERT(protocol_version) ON task_inference_recovery FROM md_acceptor",
        "GRANT SELECT(task_id) ON task_inference_recovery TO md_app",
        "GRANT EXECUTE ON FUNCTION attempt_lifecycle_identity(jsonb) TO md_app",
        "REVOKE EXECUTE ON FUNCTION task_inference_recovery_lock(uuid,bigint,uuid,bigint,uuid) FROM md_app",
    ],
)
async def test_current_acquired_pair_refuses_before_new_acceptance(host, sql):
    host.db.sql(sql)
    with pytest.raises(ObservationBlocked):
        await accept(host)
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM tasks") == 0
        assert await owner.fetchval("SELECT count(*) FROM messages") == 0
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 0


@pytest.mark.asyncio
async def test_current_worker_privileges_checked_even_with_no_quarantine_candidates(host):
    task = await accept(host)
    host.db.sql("GRANT SELECT(task_id) ON task_inference_recovery TO md_app")
    with pytest.raises(ObservationBlocked):
        await runner.sweep_tasks(host.ctx)
    with pytest.raises(ObservationBlocked):
        await host.bundle.tasks.mark_woken(task.task_id)
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT wake_seq FROM tasks") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "GRANT SELECT ON task_inference_recovery TO md_third",
        "GRANT EXECUTE ON FUNCTION task_inference_recovery_immutable() TO PUBLIC",
        "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner IN SCHEMA public GRANT SELECT ON TABLES TO md_third",
        "ALTER FUNCTION task_inference_dispatch_begin(jsonb,uuid,uuid,uuid) SET search_path=public",
        "ALTER FUNCTION compute_metadata_check_account(uuid) RESET search_path",
        "DROP FUNCTION compute_metadata_check_account(uuid); CREATE FUNCTION compute_metadata_check_account(uuid) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT true'; REVOKE ALL ON FUNCTION compute_metadata_check_account(uuid) FROM PUBLIC",
        "ALTER TABLE task_inference_recovery DISABLE TRIGGER task_inference_recovery_immutable",
        "ALTER TABLE task_inference_recovery ADD COLUMN unexpected boolean",
        "ALTER TABLE task_inference_recovery DROP CONSTRAINT task_inference_recovery_account_generation_check",
        "ALTER TABLE task_inference_recovery DROP CONSTRAINT task_inference_recovery_account_generation_check; ALTER TABLE task_inference_recovery ADD CONSTRAINT task_inference_recovery_account_generation_check CHECK (true)",
        "ALTER TABLE task_inference_recovery ALTER COLUMN may_have_dispatched SET DEFAULT true",
        "ALTER TABLE task_inference_recovery ALTER COLUMN account_generation DROP NOT NULL",
        "REVOKE UPDATE ON entitlement_usage_periods FROM md_app",
    ],
)
async def test_unsafe_schema_acl_refuses_before_j1_initial_binding(db, tmp_path, damage):
    from orchestrator.services.deletion_journal.store import initialize

    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    value = settings()
    value.daemon_deletion_journal_dir = str(path)
    value.daemon_deletion_journal_id = str(identity)
    db.sql(damage)
    async with pools(db) as (a, w):
        with pytest.raises(ObservationBlocked):
            await qualify_task_composition(
                enabled=True,
                acceptor_pool=a,
                worker_pool=w,
                encryption=ContentEncryption(value.daemon_encryption_key),
                settings=value,
            )
    async with db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM deletion_journal_binding") == 0
        assert await owner.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
        assert await owner.fetchval("SELECT count(*) FROM tasks") == 0


@pytest.mark.asyncio
async def test_configured_j1_initial_binding_and_pending_request_refusal(db, tmp_path):
    from orchestrator.services.deletion_journal.store import initialize, Journal
    from orchestrator.services.deletion_journal.errors import JournalError
    from tests.deletion_journal_support import request

    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    value = settings()
    value.daemon_deletion_journal_dir = str(path)
    value.daemon_deletion_journal_id = str(identity)
    enc = ContentEncryption(value.daemon_encryption_key)
    async with pools(db) as (a, w):
        bundle = await qualify_task_composition(
            enabled=True, acceptor_pool=a, worker_pool=w, encryption=enc, settings=value
        )
        assert bundle is not None
        async with db.connection() as owner:
            assert (
                await owner.fetchval("SELECT journal_id FROM deletion_journal_binding") == identity
            )
            uid = await db.account(owner)
        with Journal(path, identity) as journal:
            journal.append(request(uid), uuid4(), None, None)
        with pytest.raises(JournalError, match="recovery_required"):
            await qualify_task_composition(
                enabled=True, acceptor_pool=a, worker_pool=w, encryption=enc, settings=value
            )
        async with db.connection() as owner:
            assert await owner.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0


@pytest.mark.asyncio
async def test_transitive_noinherit_acceptor_membership_is_not_worker_separation(db):
    # Role membership is cluster-wide, so restore this exact synthetic mutation.
    db.cluster.sql(
        "ALTER ROLE md_app NOINHERIT; GRANT md_acceptor TO md_mid; GRANT md_mid TO md_app"
    )
    try:
        value = settings()
        async with pools(db) as (a, w):
            with pytest.raises(ObservationBlocked):
                await qualify_task_composition(
                    enabled=True,
                    acceptor_pool=a,
                    worker_pool=w,
                    encryption=ContentEncryption(value.daemon_encryption_key),
                    settings=value,
                )
    finally:
        db.cluster.sql(
            "REVOKE md_mid FROM md_app; REVOKE md_acceptor FROM md_mid; ALTER ROLE md_app INHERIT"
        )


@pytest.mark.asyncio
async def test_same_pool_and_cross_database_refuse_without_j1(db):
    value = settings()
    enc = ContentEncryption(value.daemon_encryption_key)
    async with pools(db) as (a, w):
        with pytest.raises(ObservationBlocked):
            await qualify_task_composition(
                enabled=True, acceptor_pool=a, worker_pool=a, encryption=enc, settings=value
            )
        other = await asyncpg.create_pool(
            host=str(db.host),
            database="postgres",
            user="md_app",
            password="",
            ssl=False,
            min_size=0,
            max_size=1,
        )
        try:
            with pytest.raises(ObservationBlocked, match="inference_database_refused"):
                await qualify_task_composition(
                    enabled=True, acceptor_pool=a, worker_pool=other, encryption=enc, settings=value
                )
        finally:
            await other.close()


@pytest.mark.asyncio
async def test_actual_connection_session_user_not_hidden_by_set_role(db):
    db.cluster.sql("GRANT md_app TO md_controller")
    value = settings()

    async def setup(conn):
        await conn.execute("SET ROLE md_app")

    try:
        async with pools(db) as (a, _w):
            w = await asyncpg.create_pool(
                host=str(db.host),
                database=db.name,
                user="md_controller",
                password="",
                ssl=False,
                min_size=0,
                max_size=1,
                setup=setup,
            )
            try:
                with pytest.raises(ObservationBlocked):
                    await qualify_task_composition(
                        enabled=True,
                        acceptor_pool=a,
                        worker_pool=w,
                        encryption=ContentEncryption(value.daemon_encryption_key),
                        settings=value,
                    )
            finally:
                await w.close()
    finally:
        db.cluster.sql("REVOKE md_app FROM md_controller")


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_worker", [False, True])
async def test_http_dispatch_uncertainty_restart_quarantine_and_original_hold_sweep(
    http, monkeypatch, tmp_path, separate_worker
):
    import importlib
    from unittest.mock import AsyncMock
    from urllib.parse import urlencode
    from orchestrator.entitlements import attestation
    from orchestrator.services.deletion_journal.store import initialize

    worker = importlib.import_module("orchestrator.worker.worker")
    recovered_ctx = None
    if separate_worker:
        path, journal_id = tmp_path / "startup-journal", uuid4()
        initialize(path, journal_id)
        http.settings.daemon_deletion_journal_dir = str(path)
        http.settings.daemon_deletion_journal_id = str(journal_id)
        http.settings.daemon_task_acceptor_role = "md_acceptor"
        http.settings.daemon_task_worker_role = "md_app"
        http.db.sql("REVOKE INSERT,UPDATE,DELETE ON public.deletion_journal_binding FROM md_app")
        bundle = await qualify_task_composition(
            enabled=True,
            acceptor_pool=http.a,
            worker_pool=http.w,
            encryption=http.encryption,
            settings=http.settings,
            acceptor_role="md_acceptor",
            worker_role="md_app",
        )
        assert bundle is not None
        http.bundle = bundle
        http.state.task_composition = bundle
        http.state.memory_store = bundle.tasks._memory
        http.ctx.update(
            task_composition=bundle.worker, task_store=bundle.tasks, store=bundle.tasks._memory
        )
    sends = 0
    remote_tick, remote_continued = asyncio.Event(), asyncio.Event()
    producers = []

    async def remote():
        await remote_tick.wait()
        remote_continued.set()

    async def provider(**kwargs):
        nonlocal sends
        sends += 1
        producers.append(asyncio.create_task(remote()))

        async def stream():
            yield {"choices": [{"delta": {"content": "An incomplete cloud answer."}}]}
            raise ConnectionError("synthetic local transport lost, remote continues")

        return stream()

    monkeypatch.setattr(compute_runtime.litellm, "acompletion", provider)
    pending, task = await submit_http(http)
    try:

        async def settlement_unavailable(*args, **kwargs):
            raise ConnectionError("synthetic settlement unavailable")

        with monkeypatch.context() as fault:
            fault.setattr(EntitlementService, "settle", settlement_unavailable)
            await runner.run_chat_task(http.ctx, str(task))
        remote_tick.set()
        await asyncio.wait_for(remote_continued.wait(), 1)
        async with http.db.connection() as owner:
            row = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", task)
            assert row["status"] == "running"
            hold = await owner.fetchrow(
                "SELECT * FROM entitlement_reservations WHERE user_id=$1", http.uid
            )
            assert hold["status"] == "open"
            original = dict(hold)
            assert (
                await owner.fetchval("SELECT may_have_dispatched FROM task_inference_recovery")
                is True
            )
            await owner.execute(
                "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                task,
            )
        if separate_worker:
            # A fresh process owns only W and requalifies J1 after NEW is disabled.
            http.settings.durable_chat_enabled = False
            worker_settings = http.settings.model_copy(
                update={
                    "database_url": f"postgresql://md_app@/{http.db.name}?{urlencode({'host': str(http.db.host)})}",
                    "daemon_task_acceptor_database_url": None,
                    "daemon_environment": "development",
                }
            )
            monkeypatch.setattr(worker, "get_settings", lambda: worker_settings)
            monkeypatch.setattr(attestation, "start", AsyncMock())
            monkeypatch.setattr(attestation, "stop", AsyncMock())
            recovered_ctx = {"redis": http.ctx["redis"]}
            await worker.on_startup(recovered_ctx)
            assert not hasattr(recovered_ctx["task_composition"], "acceptor_pool")
            assert recovered_ctx["db_pool"] is not http.w
        active_ctx = recovered_ctx if recovered_ctx is not None else http.ctx
        # Fresh invocation/owner observes the old mark. Fail AFTER terminal COMMIT
        # so only the independent sweep can recover the original funded obligation.
        with monkeypatch.context() as fault:
            fault.setattr(EntitlementService, "settle", settlement_unavailable)
            with pytest.raises(ConnectionError, match="settlement unavailable"):
                await runner.run_chat_task(active_ctx, str(task))
        snapshot = await http.client.get(f"/tasks/{task}", headers=http.headers)
        assert snapshot.json()["status"] == "needs_attention"
        assert snapshot.json()["terminal_code"] == "inference_dispatch_unresolved"
        before = snapshot.json()
        ctx = {**active_ctx, "redis": None}
        assert await runner.sweep_tasks(ctx) == 0
        assert await runner.sweep_tasks(ctx) == 0
        assert (await http.client.get(f"/tasks/{task}", headers=http.headers)).json() == before
        assert sends == 1 and remote_continued.is_set()
        async with http.db.connection() as owner:
            current = await owner.fetchrow(
                "SELECT * FROM entitlement_reservations WHERE id=$1", original["id"]
            )
            assert current["status"] == "settled"
            assert current["actual_microusd"] == original["reserved_microusd"]
            for field in (
                "user_id",
                "scope_id",
                "period_key",
                "operation",
                "provider",
                "model",
                "route_id",
                "reserved_microusd",
            ):
                assert current[field] == original[field]
            assert (
                await owner.fetchval("SELECT lease_epoch FROM tasks WHERE id=$1", task)
                == row["lease_epoch"]
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM entitlement_reservations WHERE user_id=$1", http.uid
                )
                == 1
            )
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        remote_tick.set()
        await asyncio.gather(*producers)
        if recovered_ctx is not None and "db_pool" in recovered_ctx:
            await worker.on_shutdown(recovered_ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "piece",
    [
        "worker_pool",
        "acceptor_pool",
        "worker_memory",
        "acceptor_memory",
        "recovery_pool",
        "worker_role",
        "worker_accepts",
    ],
)
async def test_crossed_mutable_collaborators_refuse_at_their_own_boundary(host, piece):
    from fastapi import HTTPException
    from orchestrator.memory.store import MemoryStore

    bundle = host.bundle
    if piece == "worker_pool":
        bundle.tasks._pool = host.a
    elif piece == "acceptor_pool":
        bundle.acceptor._pool = host.w
    elif piece == "worker_memory":
        bundle.tasks._memory = MemoryStore(host.w, host.encryption)
    elif piece == "acceptor_memory":
        bundle.acceptor._memory = MemoryStore(host.a, host.encryption)
    elif piece == "recovery_pool":
        bundle.recovery.pool = host.a
    elif piece == "worker_role":
        bundle.recovery.principals = None
    else:
        bundle.tasks._cover_new_inference = True
    with pytest.raises(HTTPException):
        task_acceptor(host.state)
    if piece.startswith("acceptor_"):
        # W deliberately carries no A collaborator. Independent recovery must
        # not acquire an acceptance dependency merely to reject broken HTTP A.
        assert await runner.sweep_tasks(host.ctx) == 0
    else:
        with pytest.raises(ObservationBlocked):
            await runner.sweep_tasks(host.ctx)
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM tasks") == 0
        assert await owner.fetchval("SELECT count(*) FROM entitlement_reservations") == 0


@pytest.mark.asyncio
async def test_legacy_acceptance_is_not_implicitly_covered_or_adopted(host):
    from orchestrator.tasks.store import TaskStore
    from orchestrator.memory.store import MemoryStore

    legacy = TaskStore(host.a, host.encryption, MemoryStore(host.a, host.encryption))
    accepted = await legacy.accept(
        user_id=host.uid,
        conversation_id=None,
        new_conversation_title="Old source",
        pipeline="cloud",
        user_message="old",
        task_input={"message": "old", "_inference_recovery_version": 1},
        request_hash="a" * 64,
        idempotency_key=None,
        assistant_model=None,
        captured_generation=CapturedGeneration(host.uid, 0),
    )
    assert (
        await runner.run_chat_task(host.ctx, str(accepted.task_id))
        == "inference_coverage_unavailable"
    )
    async with host.db.connection() as owner:
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 0
        assert await owner.fetchval("SELECT count(*) FROM task_attempts") == 0
        assert await owner.fetchval("SELECT lease_epoch FROM tasks") == 0


@pytest.mark.asyncio
async def test_isolated_host_owns_first_pool_when_second_pool_creation_fails(db, monkeypatch):
    native = asyncpg.create_pool
    created = []

    async def create(**kwargs):
        if kwargs["user"] == "md_app":
            raise ConnectionError("synthetic second pool unavailable")
        pool = await native(**kwargs)
        created.append(pool)
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(ConnectionError, match="second pool unavailable"):
        async with pools(db):
            pytest.fail("partial host yielded")
    assert len(created) == 1 and created[0]._closed


@pytest.mark.asyncio
async def test_inherited_invocation_cannot_be_replaced_at_composed_worker_entry(host, monkeypatch):
    from orchestrator.tasks._invocation import InferenceInvocation, invocation
    from unittest.mock import AsyncMock

    original = InferenceInvocation()
    original.block("original_uncertainty")
    execute = AsyncMock(return_value="must-not-enter")
    monkeypatch.setattr(runner, "_run_chat_task", execute)
    token = invocation.set(original)
    try:
        with pytest.raises(ObservationBlocked, match="inherited_inference_invocation"):
            await runner.run_chat_task(host.ctx, str(uuid4()))
        execute.assert_not_awaited()
        assert invocation.get() is original
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
async def test_inherited_invocation_refuses_factory_before_acquisition(host, monkeypatch):
    from orchestrator.tasks._invocation import InferenceInvocation, invocation

    def forbidden(*args, **kwargs):
        pytest.fail("factory acquired a connection under an inherited invocation")

    monkeypatch.setattr(asyncpg.Pool, "acquire", forbidden)
    token = invocation.set(InferenceInvocation())
    try:
        with pytest.raises(ObservationBlocked, match="inherited_inference_invocation"):
            await qualify_task_composition(
                enabled=True,
                acceptor_pool=host.a,
                worker_pool=host.w,
                encryption=host.encryption,
                settings=host.settings,
            )
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_acceptor", "md_app"])
async def test_database_owner_is_not_qualified_even_when_public_has_other_owner(db, role):
    db.cluster.sql(f"ALTER DATABASE {db.name} OWNER TO {role}")
    value = settings()
    try:
        async with pools(db) as (a, w):
            with pytest.raises(ObservationBlocked, match="inference_principal_refused"):
                await qualify_task_composition(
                    enabled=True,
                    acceptor_pool=a,
                    worker_pool=w,
                    encryption=ContentEncryption(value.daemon_encryption_key),
                    settings=value,
                )
        async with db.connection() as owner:
            assert await owner.fetchval("SELECT count(*) FROM deletion_journal_binding") == 0
    finally:
        db.cluster.sql(f"ALTER DATABASE {db.name} OWNER TO postgres")


@pytest.mark.asyncio
async def test_new_acceptance_off_requalifies_controls_but_refuses_new_acceptor(host):
    host.settings.durable_chat_enabled = False
    requalified = await qualify_task_composition(
        enabled=True,
        acceptor_pool=host.a,
        worker_pool=host.w,
        encryption=host.encryption,
        settings=host.settings,
    )
    assert requalified is not None
    with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
        await requalified.preflight()
    with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
        await accept(host)
    assert task_store(host.state) is host.bundle.tasks
    assert await runner.sweep_tasks(host.ctx) == 0


@pytest.mark.asyncio
async def test_acceptance_checks_actual_connection_after_pair_preflight(host, monkeypatch):
    from orchestrator.tasks import store as store_module

    original_guard = store_module.guard_content_write

    @asynccontextmanager
    async def drift(conn, captured):
        async with original_guard(conn, captured):
            # The real pair preflight already passed. Mutate only this owned
            # fixture's ACL before the actual transaction-connection check.
            host.db.sql(
                "GRANT INSERT(may_have_dispatched) ON task_inference_recovery TO md_acceptor"
            )
            yield

    monkeypatch.setattr(store_module, "guard_content_write", drift)
    with pytest.raises(ObservationBlocked, match="inference_acceptor_refused"):
        await accept(host)
    async with host.db.connection() as owner:
        for table in (
            "tasks",
            "messages",
            "conversations",
            "task_events",
            "task_inference_recovery",
        ):
            assert await owner.fetchval(f"SELECT count(*) FROM {table}") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "cancellation", "commit"])
async def test_http_acceptance_faults_do_not_fall_back_or_leave_partial_coverage(
    http, monkeypatch, failure
):
    entered = asyncio.Event()
    native_append = http.bundle.acceptor._append_event

    async def append(conn, *args, **kwargs):
        await native_append(conn, *args, **kwargs)
        assert await conn.fetchval("SELECT count(*) FROM messages") == 2
        entered.set()
        if failure == "rollback":
            raise RuntimeError("synthetic acceptance rollback")
        if failure == "cancellation":
            await asyncio.Event().wait()

    monkeypatch.setattr(http.bundle.acceptor, "_append_event", append)

    async def forbidden(**kwargs):
        pytest.fail("acceptance failure fell back to request-bound inference")

    monkeypatch.setattr(compute_runtime.litellm, "acompletion", forbidden)
    if failure == "commit":
        http.db.sql(
            "CREATE FUNCTION acceptance_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'synthetic acceptance commit failure'; END $$; "
            "CREATE CONSTRAINT TRIGGER acceptance_commit_failure AFTER INSERT ON task_events "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION acceptance_commit_failure()"
        )
    pending = asyncio.create_task(
        http.client.post(
            "/chat",
            headers={**http.headers, "Idempotency-Key": uuid4().hex},
            json={"message": "Synthetic question", "model": http.route.model},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if failure == "cancellation":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
        else:
            response = await asyncio.wait_for(pending, 5)
            assert (
                response.status_code == 503
                and response.json()["detail"]["code"] == "task_unavailable"
            )
        async with http.db.connection() as owner:
            for table in (
                "tasks",
                "messages",
                "conversations",
                "task_events",
                "task_inference_recovery",
                "entitlement_reservations",
            ):
                assert await owner.fetchval(f"SELECT count(*) FROM {table}") == 0
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "piece",
    ["acceptor_pool", "acceptor_memory", "recovery_pool", "missing_store", "missing_memory"],
)
async def test_rotated_key_replay_rejects_crossed_binding_before_digest_write(host, piece):
    from orchestrator.memory.store import MemoryStore

    options = dict(
        user_id=host.uid,
        conversation_id=None,
        new_conversation_title="Synthetic",
        pipeline="cloud",
        user_message="question",
        task_input={},
        request_hash="a" * 64,
        request_canonical="same canonical request",
        idempotency_key=uuid4().hex,
        assistant_model=None,
        captured_generation=CapturedGeneration(host.uid, 0),
    )
    first = await host.bundle.acceptor.accept(**options)
    if piece == "acceptor_pool":
        host.bundle.acceptor._pool = host.w
    elif piece == "acceptor_memory":
        host.bundle.acceptor._memory = MemoryStore(host.a, host.encryption)
    elif piece == "missing_store":
        host.bundle.acceptor._composition = None
    elif piece == "missing_memory":
        host.bundle.acceptor._memory._task_composition = None
    else:
        host.bundle.recovery.pool = host.a
    options.update(request_hash="b" * 64, captured_generation=None)
    with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
        await host.bundle.acceptor.accept(**options)
    with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
        await host.bundle.acceptor.find_by_key(
            host.uid, options["idempotency_key"], "b" * 64, "same canonical request"
        )
    async with host.db.connection() as owner:
        assert (
            await owner.fetchval("SELECT request_hash FROM tasks WHERE id=$1", first.task_id)
            == "a" * 64
        )
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 1


@pytest.mark.asyncio
async def test_valid_rotated_replay_precedes_new_origin_flag_and_coverage_checks(host):
    options = dict(
        user_id=host.uid,
        conversation_id=None,
        new_conversation_title="Synthetic",
        pipeline="cloud",
        user_message="question",
        task_input={},
        request_hash="a" * 64,
        request_canonical="same canonical request",
        idempotency_key=uuid4().hex,
        assistant_model=None,
        captured_generation=CapturedGeneration(host.uid, 0),
    )
    first = await host.bundle.acceptor.accept(**options)
    host.db.sql(
        "REVOKE INSERT(task_id,account_id,account_generation,protocol_version) ON task_inference_recovery FROM md_acceptor"
    )
    host.settings.durable_chat_enabled = False
    options.update(request_hash="b" * 64, captured_generation=None)
    replay = await host.bundle.acceptor.accept(**options)
    assert replay.task_id == first.task_id and not replay.created
    async with host.db.connection() as owner:
        assert (
            await owner.fetchval("SELECT request_hash FROM tasks WHERE id=$1", first.task_id)
            == "b" * 64
        )
        assert await owner.fetchval("SELECT count(*) FROM task_inference_recovery") == 1
