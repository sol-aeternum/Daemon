"""Actual startup and readonly renewal on owned SQL/POSIX, not live rollout.

OS service membership/key loading is qualified by separate file/profile tests.
This fixture supplies synthetic material and does not provision host identities.
"""

import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator import db as database
from orchestrator import main
from orchestrator.services.restore_history import runtime, read_supervisor
from orchestrator.services.restore_history.configuration import (
    HistoryConfiguration,
    HistoryMaterial,
)
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.read_supervisor import HistoryReadSupervisor
from orchestrator.services.deletion_journal.store import initialize
from tests.restore_history_support import KEY
from tests.test_history_coordination import available
from tests.test_task_composition_postgres import settings
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_effects_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    effects as effects,
    deadline,
    running,
    mark,
)
from tests.history_cleanup_support import held_reset

worker = importlib.import_module("orchestrator.worker.worker")


def dsn(s, principal):
    return f"postgresql://{principal}@/{s.db.name}?{urlencode({'host': str(s.db.host)})}"


@pytest_asyncio.fixture
async def host(effects, tmp_path, monkeypatch, request):
    s = effects
    revision = getattr(request, "param", "ready")
    assert revision in ("ready", "missing", "disabled")
    # The shared history fixture intentionally stops at 057. This startup lane
    # needs the real current memory prerequisite, not a mocked/relaxed guard.
    if revision != "missing":
        migration = Path(__file__).resolve().parents[1] / "migrations/066_memory_bitemporal.sql"
        s.db.sql("BEGIN;" + migration.read_text() + "COMMIT;")
        if revision == "disabled":
            s.db.sql("ALTER TABLE memories DISABLE TRIGGER memory_revision_capture")
    value = settings()
    value.database_url = dsn(s, "md_app")
    value.daemon_task_acceptor_database_url = dsn(s, "md_acceptor")
    value.daemon_task_acceptor_role = "md_acceptor"
    value.daemon_task_worker_role = "md_app"
    value.redis_url = None
    journal = tmp_path / "j1"
    jid = uuid4()
    initialize(journal, jid)
    value.daemon_deletion_journal_dir = str(journal)
    value.daemon_deletion_journal_id = str(jid)
    values = {
        "dir": str(s.path),
        "expected_file": str(tmp_path / "operator" / "expected"),
        "operator_uid": "0",
        "backend_uid": str(os.getuid()),
        "worker_uid": str(os.getuid() + 1),
        "group_gid": str(os.getgid()),
        "key_gid": str(os.getgid() + 1),
        "key_file": str(tmp_path / "keys" / "k1"),
    }
    for name, value_part in values.items():
        setattr(value, "daemon_restore_history_" + name, value_part)
    loads = []

    def material(config, service, *, deadline):
        assert service == "http" or config.key_file is None
        loads.append(service)
        return HistoryMaterial(s.identity, KEY if service == "http" else None)

    # The profile validates a distinct operator; this single-UID fixture owns
    # its temporary POSIX files as the test user. Map only the store ownership
    # to that known fixture, without relaxing configuration UID validation.
    # Real operator/service filesystem separation remains a deployment gate.
    monkeypatch.setattr(HistoryConfiguration, "ownership", property(lambda _: s.ownership))
    monkeypatch.setattr(runtime, "load_material", material)
    monkeypatch.setattr(database, "ContentEncryption", lambda _: s.tasks._enc)
    return SimpleNamespace(s=s, settings=value, loads=loads)


def worker_startup(host, monkeypatch):
    wsettings = host.settings.model_copy(
        update={
            "daemon_task_acceptor_database_url": None,
            "daemon_restore_history_key_file": None,
        }
    )
    monkeypatch.setattr(worker, "get_settings", lambda: wsettings)
    # Execute actual AuditedWorker/ARQ startup; only Redis transport/logging is
    # synthetic. Track the real pool so every refusal must physically close it.
    ctx = {"settings": wsettings}
    monkeypatch.setattr(worker.worker, "ctx", ctx)
    monkeypatch.setattr(worker.worker, "_pool", object())
    monkeypatch.setattr("arq.worker.log_redis_info", AsyncMock())
    poll = AsyncMock(side_effect=AssertionError("job loop bypassed startup barrier"))
    monkeypatch.setattr(worker.worker, "_poll_iteration", poll)
    pools = []
    native = worker.asyncpg.create_pool

    def create_pool(*args, **kwargs):
        pool = native(*args, **kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(worker.asyncpg, "create_pool", create_pool)
    return ctx, poll, pools


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["missing", "disabled"], indirect=True)
async def test_worker_revision_schema_guard_precedes_h_barrier_and_closes_pool(host, monkeypatch):
    ctx, poll, pools = worker_startup(host, monkeypatch)
    with pytest.raises(RuntimeError, match="Memory revisions require migration 066"):
        await worker.worker.main()
    poll.assert_not_awaited()
    assert pools and all(pool._closed for pool in pools)
    assert not any(ctx.get(key) for key in ("task_store", "store", "db_pool", "task_composition"))
    assert host.loads == ["worker"]
    assert host.s.db.sql("SELECT count(*) FROM deletion_journal_binding") == "0"


@pytest.mark.asyncio
async def test_real_backend_installs_renewable_authenticated_reader_and_worker_never_runs_jobs(
    host, monkeypatch
):
    state = await database.init_app_state(host.settings)
    assert state.history_runtime is not None
    assert state.history_runtime.history is not None
    assert isinstance(state.history_observation, HistoryReadSupervisor)
    assert state.task_composition is not None
    try:
        assert state.history_runtime.ready(state)
        assert state.history_observation._tasks is state.task_composition.tasks
        assert state.db_pool is not state.task_acceptor_pool
        assert not available(host.s.path, "RUNTIME_LOCK")
        assert available(host.s.path, "OP_LOCK")
        _, headers = await device(host.s, host.s.account)
        monkeypatch.setattr(main.app.state, "app_state", state, raising=False)
        async with AsyncClient(transport=ASGITransport(main.app), base_url="http://test") as client:
            response = await client.get("/tasks/by-key/stable-client-key", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["id"] == str(host.s.accepted.task_id)
            assert response.headers["cache-control"] == "no-store"
            assert (await client.post("/chat", headers=headers, json={})).status_code == 503
            assert (
                await client.post(f"/tasks/{host.s.accepted.task_id}/cancel", headers=headers)
            ).status_code == 503
        ctx, poll, pools = worker_startup(host, monkeypatch)
        with pytest.raises(HistoryError, match="history_observation_only"):
            await worker.worker.main()
        poll.assert_not_awaited()
        assert pools and all(pool._closed for pool in pools)
        assert not any(
            ctx.get(key) for key in ("task_store", "store", "db_pool", "task_composition")
        )
        assert host.loads == ["http", "worker"]
    finally:
        await database.close_app_state(state)
    assert state.history_runtime.history._closed
    assert state.history_observation._closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["accept", "send", "stop"])
async def test_startup_refuses_restored_sql_missing_any_required_history_kind_before_native_recovery(
    host, monkeypatch, kind
):
    from tests.test_history_effects_postgres import entries

    if kind != "accept":
        await running(host.s)
        if kind == "send":
            await mark(host.s)
        else:
            await host.s.effects.request_cancel(
                host.s.account, host.s.accepted.task_id, key=KEY, deadline=deadline()
            )
    # Owner-only synthetic restore image. No production data/privilege operation.
    async with host.s.db.connection() as conn:
        await conn.execute("ALTER TABLE task_history_projection DISABLE TRIGGER USER")
        await conn.execute("ALTER TABLE task_history_acceptance DISABLE TRIGGER USER")
        if kind == "accept":
            await conn.execute("DELETE FROM task_history_acceptance")
        await conn.execute("DELETE FROM task_history_projection WHERE seq=$1", len(entries(host.s)))
        await conn.execute("ALTER TABLE task_history_projection ENABLE TRIGGER USER")
        await conn.execute("ALTER TABLE task_history_acceptance ENABLE TRIGGER USER")
    calls = []

    async def forbidden(**kw):
        calls.append("native recovery")
        pytest.fail("native recovery preceded independent parity")

    monkeypatch.setattr(database, "qualify_task_composition", forbidden)
    with pytest.raises(HistoryError, match="reader_reconciliation_required"):
        await database.init_app_state(host.settings)
    assert calls == []
    assert host.s.db.sql("SELECT count(*) FROM deletion_journal_binding") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_startup_cancel_drains_actual_sql_reset_before_h_cleanup(host, monkeypatch, failure):
    owners, entered, proceed = [], asyncio.Event(), asyncio.Event()
    original = read_supervisor.qualify_read_history

    async def qualify(operation, pool, projection, *, deadline):
        owners.append(operation._coordinator)
        async with held_reset(pool, monkeypatch, 1, failure) as (seen, release):

            async def release_when_allowed():
                await seen.wait()
                entered.set()
                await proceed.wait()
                release.set()

            relay = asyncio.create_task(release_when_allowed())
            try:
                await original(operation, pool, projection, deadline=deadline)
            finally:
                await relay

    monkeypatch.setattr(runtime, "qualify_read_history", qualify)
    task = asyncio.create_task(database.init_app_state(host.settings))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original startup cancellation")
        await asyncio.sleep(0)
        task.cancel("again")
        await asyncio.sleep(0.02)
        assert not task.done() and not owners[0]._closed
        assert not available(host.s.path, "OP_LOCK")
        proceed.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original startup cancellation",)
        assert owners[0]._closed and owners[0]._executor is None
        assert available(host.s.path, "OP_LOCK")
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_read_renewal_serializes_with_independent_writer_and_fully_revalidates(
    host, monkeypatch
):
    state = await database.init_app_state(host.settings)
    assert state.history_runtime is not None
    assert state.history_runtime.history is not None
    assert isinstance(state.history_observation, HistoryReadSupervisor)
    assert state.task_composition is not None
    reader = state.history_observation
    try:
        _, headers = await device(host.s, host.s.account)
        from orchestrator.auth import AuthenticatedDevice

        async with host.s.db.connection() as conn:
            row = await conn.fetchrow(
                "SELECT id,device_id FROM sessions WHERE user_id=$1", host.s.account
            )
        auth = AuthenticatedDevice(host.s.account, row["device_id"], row["id"])
        # First failed read retires only the reader. Writer/startup owner is not
        # cleared or replaced; the next explicit request must obtain OP and scan.
        from orchestrator.services.restore_history.observation import HistoryObservation

        native = HistoryObservation.lookup

        async def fail(*a, **kw):
            raise RuntimeError("synthetic read fault")

        monkeypatch.setattr(HistoryObservation, "lookup", fail)
        assert reader._owner is not None
        old = reader._owner.history
        with pytest.raises(RuntimeError):
            await reader.lookup(auth, "stable-client-key", deadline=deadline())
        assert old._closed and reader._owner is None
        monkeypatch.setattr(HistoryObservation, "lookup", native)
        async with host.s.history.operation(deadline=deadline()):
            task = asyncio.create_task(
                reader.lookup(auth, "stable-client-key", deadline=deadline())
            )
            await asyncio.sleep(0.03)
            assert not task.done()
        result = await task
        assert result.kind == "task"
        assert reader._owner is not None
        assert reader._owner.history is not old
        assert state.history_runtime.history is not reader._owner.history
    finally:
        await database.close_app_state(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_shutdown_h_lifetime_survives_actual_pool_close_until_settled(
    host, monkeypatch, failure
):
    import asyncpg

    state = await database.init_app_state(host.settings)
    assert state.history_runtime is not None and state.history_runtime.history is not None
    assert isinstance(state.history_observation, HistoryReadSupervisor)
    entered, release = asyncio.Event(), asyncio.Event()
    native = asyncpg.Pool.close
    attempts = []

    async def close(pool):
        if pool is state.db_pool:
            attempts.append(pool)
            entered.set()
            await release.wait()
            await native(pool)
            if failure:
                raise OSError("synthetic close acknowledgement loss")
        else:
            await native(pool)

    monkeypatch.setattr(asyncpg.Pool, "close", close)
    task = asyncio.create_task(database.close_app_state(state))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original pool-close cancellation")
        await asyncio.sleep(0)
        task.cancel("again")
        await asyncio.sleep(0.02)
        assert not task.done()
        assert state.history_observation._closed
        assert not state.history_runtime.history._closed
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original pool-close cancellation",)
        assert state.history_runtime.history._closed
        assert not state.history_runtime.ready(state)
        if failure:
            from orchestrator.services.deletion_journal.errors import JournalError

            with pytest.raises(JournalError):
                await database.close_app_state(state)
        else:
            await database.close_app_state(state)
        assert len(attempts) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
