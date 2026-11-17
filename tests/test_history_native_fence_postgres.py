"""#731 native fence: binding is serialized against unselected native authority.

Real PostgreSQL (owned synthetic databases, ``--compute-metadata-postgres``),
migrated by the real runner through the latest file (073 included). The host
side connects as ``md_app`` (no H privilege); the binder is the table owner.
Every interleaving is driven by explicit ordering or catalog conditions, never
by sleeps. No live binding, migration or deployment.
"""

import asyncio
import os
import signal
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import asyncpg
import pytest
from fastapi import HTTPException, Response

from orchestrator.routes import tasks as routes
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.native_fence import FENCE_KEY, NativeFence
from tests.compute_metadata_support import ROOT
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_history_acceptance_upgrade_postgres import upgrade_through
from tests.test_history_unbound_start_postgres import LATEST

BIND = (
    "INSERT INTO task_history_binding(history_id,version,key_id,key_check,request_schema) "
    "VALUES (gen_random_uuid(),1,1,repeat('a',64),'chat-canonical-3715-v1')"
)
HOLDERS = (
    "SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype='advisory' AND granted "
    "AND classid=$1::oid AND objid=$2::oid AND objsubid=2 AND database="
    "(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())"
)
CHILD = """
import asyncio, sys
from orchestrator.services.restore_history.native_fence import NativeFence
async def main():
    fence = NativeFence(on_lost=lambda: None)
    await fence.open(sys.argv[1])
    print("ready", flush=True)
    await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
    await fence.close()
asyncio.run(main())
"""


def dsn(db, role="md_app") -> str:
    return f"postgresql://{role}@/{db.name}?host={db.host}"


async def migrated(db):
    async with db.connection() as owner:
        await upgrade_through(owner, LATEST)


async def bind(db) -> None:
    async with db.connection() as owner:
        await owner.execute(BIND)


async def bound(db) -> int:
    async with db.connection() as owner:
        return await owner.fetchval("SELECT count(*) FROM task_history_binding")


async def refused_bind(db) -> None:
    with pytest.raises(asyncpg.PostgresError, match="history_native_hosts_active"):
        await bind(db)
    assert await bound(db) == 0


async def holders(db) -> int:
    async with db.connection("postgres") as admin:
        return await admin.fetchval(HOLDERS, *FENCE_KEY)


async def until_holders(db, expected: int) -> None:
    """Wait for the server to observe session ends (a condition, not a sleep)."""
    async with asyncio.timeout(10):
        while await holders(db) != expected:
            await asyncio.sleep(0.05)


class _PresenceOnly(NativeFence):
    """L2 in isolation: reports L1 alive without holding the presence lock."""

    @property
    def alive(self) -> bool:
        return True


async def opened(db, **kwargs) -> NativeFence:
    fence = NativeFence(**({"on_lost": lambda: None} | kwargs))
    await fence.open(dsn(db))
    return fence


# 1. Binding committed before qualification.
@pytest.mark.asyncio
async def test_binding_before_qualification_refuses_startup_and_holds_nothing(db):
    await migrated(db)
    await bind(db)
    with pytest.raises(HistoryError, match="history_configuration_required"):
        await opened(db)
    await until_holders(db, 0)


# 2. Binder transaction open with its insert done (K held exclusively).
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rollback", "commit"])
async def test_open_binding_transaction_blocks_startup_then_decides_it(db, outcome):
    await migrated(db)
    async with db.connection() as binder:
        tx = binder.transaction()
        await tx.start()
        await binder.execute(BIND)
        with pytest.raises(HistoryError, match="history_fence_unavailable"):
            await opened(db)
        await (tx.rollback() if outcome == "rollback" else tx.commit())
    if outcome == "rollback":
        # The aborted insert left a heap page: A1 fails closed until VACUUM
        # truncates it, then startup qualifies again.
        with pytest.raises(HistoryError, match="history_configuration_required"):
            await opened(db)
        async with db.connection() as owner:
            await owner.execute("VACUUM task_history_binding")
        fence = await opened(db)
        await fence.close()
    else:
        with pytest.raises(HistoryError, match="history_configuration_required"):
            await opened(db)


# 3. After qualification: L1 alone refuses the binder.
@pytest.mark.asyncio
async def test_qualified_host_refuses_binding_until_it_closes(db):
    await migrated(db)
    fence = await opened(db)
    await refused_bind(db)
    await fence.close()
    await until_holders(db, 0)
    await bind(db)
    with pytest.raises(HistoryError, match="history_configuration_required"):
        await opened(db)


# 4. In-flight work under L2 alone (no L1), both orders.
@pytest.mark.asyncio
async def test_borrowed_connection_refuses_binding_and_later_acquires_see_it(db):
    await migrated(db)
    fence = _PresenceOnly(on_lost=lambda: None)
    async with db.pool(setup=fence.guard) as pool:
        async with pool.acquire() as conn:  # an in-flight native operation
            await refused_bind(db)
            assert await conn.fetchval("SELECT 1") == 1  # it completes natively
        await until_holders(db, 0)
        await bind(db)  # nothing borrowed: the binder may win
        with pytest.raises(HistoryError, match="history_configuration_required"):
            async with pool.acquire():
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["during_read", "between_check_and_read"])
async def test_by_key_read_is_either_before_binding_or_refused(db, monkeypatch, binding):
    await migrated(db)
    fence = _PresenceOnly(on_lost=lambda: None)
    async with db.pool(setup=fence.guard) as pool:

        async def task_for_key(*_args):
            if binding == "between_check_and_read":
                await bind(db)  # wins after the route's own check returned
            async with pool.acquire() as conn:
                if binding == "during_read":
                    await refused_bind(db)
                await conn.fetchval("SELECT 1")
            return None

        monkeypatch.setattr(
            routes, "task_store", Mock(return_value=SimpleNamespace(task_for_key=task_for_key))
        )
        state = cast(Any, SimpleNamespace(db_pool=pool, history_observation=None))
        auth = cast(Any, SimpleNamespace(user_id=None))
        with pytest.raises(HTTPException) as raised:
            await routes.get_task_by_key("stable", Response(), app_state=state, auth=auth)
        # 404: answered natively before any binding. 503: refused after it.
        assert raised.value.status_code == (404 if binding == "during_read" else 503)
        assert await bound(db) == (0 if binding == "during_read" else 1)


# 5. Presence terminated: on_lost fires, L2 refuses, only then can the binder win.
@pytest.mark.asyncio
async def test_terminated_presence_stops_native_work_before_binding(db):
    await migrated(db)
    lost = asyncio.Event()
    fence = await opened(db, on_lost=lost.set)
    async with db.pool(setup=fence.guard) as pool:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT 1") == 1
        await refused_bind(db)
        presence = fence._conn
        assert presence is not None
        async with db.connection("postgres") as admin:
            assert await admin.fetchval(
                "SELECT pg_terminate_backend($1)", presence.get_server_pid()
            )
        async with asyncio.timeout(5):
            await lost.wait()
        assert not fence.alive
        with pytest.raises(HistoryError, match="history_fence_lost"):
            async with pool.acquire():
                pass
        await until_holders(db, 0)
        await bind(db)
    await fence.close()


async def child(db):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        dsn(db),
        cwd=ROOT,
        env={k: v for k, v in os.environ.items() if not k.startswith("PG")}
        | {"PYTHONPATH": str(ROOT)},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    async with asyncio.timeout(30):
        assert (await process.stdout.readline()).strip() == b"ready"
    return process


# 6. Several processes: binding waits for every one of them.
@pytest.mark.asyncio
async def test_binding_refused_until_every_host_process_is_gone(db):
    await migrated(db)
    first, second = await child(db), await child(db)
    try:
        await until_holders(db, 2)
        await refused_bind(db)
        first.kill()
        await first.wait()
        await until_holders(db, 1)
        await refused_bind(db)
        assert second.stdin is not None
        second.stdin.close()
        assert await second.wait() == 0
        await until_holders(db, 0)
        await bind(db)
    finally:
        for process in (first, second):
            if process.returncode is None:
                process.kill()
                await process.wait()


# 7. A stopped (half-alive) host still holds the fence: fail closed.
@pytest.mark.asyncio
async def test_stopped_host_keeps_binding_refused_until_killed(db):
    await migrated(db)
    process = await child(db)
    try:
        process.send_signal(signal.SIGSTOP)
        await refused_bind(db)
        process.kill()  # SIGKILL also ends a stopped process
        await process.wait()
        await until_holders(db, 0)
        await bind(db)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


# 8. Replica mode does not skip the ALWAYS trigger.
@pytest.mark.asyncio
async def test_replication_role_replica_cannot_bypass_the_fence(db):
    await migrated(db)
    fence = await opened(db)
    try:
        async with db.connection("postgres") as admin:
            await admin.execute("SET session_replication_role = replica")
            with pytest.raises(asyncpg.PostgresError, match="history_native_hosts_active"):
                await admin.execute(BIND)
        assert await bound(db) == 0
    finally:
        await fence.close()


# 9. A removed or altered fence makes unselected hosts refuse (fail closed).
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "ALTER TABLE task_history_binding DISABLE TRIGGER task_history_binding_native_fence",
        "ALTER TABLE task_history_binding ENABLE TRIGGER task_history_binding_native_fence",
        "DROP TRIGGER task_history_binding_native_fence ON task_history_binding",
        "CREATE OR REPLACE FUNCTION public.history_native_fence() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RETURN NULL; END $$",
        "CREATE TRIGGER task_history_acceptance_native_fence BEFORE INSERT ON "
        "task_history_acceptance FOR EACH STATEMENT EXECUTE FUNCTION public.history_native_fence()",
    ],
    ids=["disabled", "origin-only", "dropped", "body-replaced", "second-use"],
)
async def test_altered_fence_refuses_l1_and_l2(db, damage):
    await migrated(db)
    healthy = await opened(db)
    async with db.pool(setup=healthy.guard) as pool:
        async with db.connection() as owner:
            await owner.execute(damage)
        with pytest.raises(HistoryError, match="history_configuration_required"):
            async with pool.acquire():
                pass
    await healthy.close()
    with pytest.raises(HistoryError, match="history_configuration_required"):
        await opened(db)
