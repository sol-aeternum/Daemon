"""Installed asyncpg holder/proxy tests with SYNTHETIC SQL/physical connections.

These execute both real adapter call paths and native Pool.release, not PostgreSQL.
"""

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from orchestrator import attempt_lifecycle as lc
from orchestrator import compute_metadata as md


class ReleaseRig:
    def __init__(self, kind, write):
        self.kind, self.write = kind, write
        self.module = md if kind == "metadata" else lc
        self.identity = (
            md.new_scope_metadata_identity(account_id=uuid4())
            if kind == "metadata"
            else lc.AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
        )
        self.command = uuid4()
        self.pool = MagicMock(spec=asyncpg.Pool)
        self.pool._loop = asyncio.get_running_loop()
        self.pool._generation = 0
        self.pool._queue = asyncio.Queue()
        self.pool._reset = None
        self.holder = asyncpg.pool.PoolConnectionHolder(
            self.pool, max_queries=50000, setup=None, max_inactive_time=0
        )
        self.store = (
            md.ComputeMetadataStore(self.pool)
            if kind == "metadata"
            else lc.AttemptLifecycleStore(self.pool)
        )
        self.commit_delay = 0.0
        self.fault = None
        self.calls = []
        self.cancelled = None
        self.on_release_error: BaseException | None = None

    def row(self):
        timestamp = datetime.now(timezone.utc)
        if isinstance(self.identity, md.ScopeMetadataIdentity):
            return {
                "id": self.identity.metadata_scope_id,
                "account_id": self.identity.account_id,
                "created_at": None if self.fault == "decode" else timestamp,
            }
        if self.fault == "decode":
            return "{}"
        wire = json.loads(self.identity.wire())
        return json.dumps(
            {
                "control": dict(
                    {
                        k: wire[k]
                        for k in (
                            "account_id",
                            "task_id",
                            "epoch",
                            "financial_scope_id",
                            "metadata_scope_id",
                        )
                    },
                    id=wire["lifecycle_id"],
                    identity=wire,
                    revision=1,
                    closed_at=None,
                    attempt_started_at=timestamp.isoformat(),
                    created_at=timestamp.isoformat(),
                ),
                "receipt": {
                    "command_id": str(self.command),
                    "lifecycle_id": wire["lifecycle_id"],
                    "opcode": "register",
                    "reason": "",
                    "revision": 1,
                    "committed_at": timestamp.isoformat(),
                },
                "account_present": True,
                "references_current": True,
            }
        )

    def physical(self):
        rig = self

        class Physical:
            def __init__(self):
                self.closed = False
                self.termination_calls = 0
                self._protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)

            def _set_proxy(self, proxy):
                pass

            def _on_release(self):
                if rig.on_release_error is not None:
                    raise rig.on_release_error

            def is_closed(self):
                return self.closed

            def terminate(self):
                self.termination_calls += 1
                self.closed = True
                rig.holder._release_on_close()

            def transaction(self, *, isolation):
                assert isolation == "read_committed"
                return self

            async def start(self):
                rig.calls.append("begin")

            async def execute(self, sql):
                assert "lock_timeout='250ms'" in sql and "statement_timeout='1000ms'" in sql
                rig.calls.append("settings")

            async def fetchval(self, sql, *args):
                if sql == rig.module.ROLE_PRECONDITION:
                    return rig.fault != "preflight"
                rig.calls.append("statement")
                return rig.row()

            async def fetchrow(self, sql, *args):
                rig.calls.append("statement")
                return rig.row()

            async def commit(self):
                rig.calls.append("commit")
                if rig.fault == "commit":
                    raise ConnectionError("synthetic unknown COMMIT")
                if rig.commit_delay:
                    await asyncio.sleep(rig.commit_delay)

            async def _reset(self):
                pass

            async def reset(self, *, timeout):
                pass

        return Physical()

    async def start(self):
        self.old = self.physical()
        self.pool._get_new_connection = AsyncMock(return_value=self.old)
        self.proxy = await self.holder.acquire()
        self.pool.acquire = AsyncMock(return_value=self.proxy)

        async def release(conn, *, timeout):
            assert conn is self.proxy and timeout == 0.5
            try:
                await asyncpg.Pool.release(self.pool, conn, timeout=timeout)
            except asyncio.CancelledError as exc:
                self.cancelled = exc
                raise

        self.pool.release = AsyncMock(side_effect=release)

    async def call(self):
        if isinstance(self.store, md.ComputeMetadataStore):
            assert isinstance(self.identity, md.ScopeMetadataIdentity)
            if self.write:
                return await self.store.create_scope(self.identity)
            return await self.store.read_scope(
                account_id=self.identity.account_id,
                metadata_scope_id=self.identity.metadata_scope_id,
            )
        assert isinstance(self.identity, lc.AttemptIdentity)
        if self.write:
            return await self.store.register(self.identity, self.command)
        return await self.store.readback(self.identity, self.command)

    def check_error(self, exc, *, acknowledged, cancellation=False, phase="release"):
        identity = self.identity if self.write or self.kind == "lifecycle" else None
        if cancellation:
            prefix = "metadata" if self.kind == "metadata" else "lifecycle"
            assert getattr(exc, prefix + "_identity") is identity
            assert getattr(exc, prefix + "_outcome") == (
                "committed" if acknowledged else "not_issued"
            )
            assert not getattr(exc, prefix + "_termination_failed")
            if self.kind == "lifecycle":
                assert exc.lifecycle_command_id is self.command
        else:
            assert exc.identity is identity
            assert exc.commit_acknowledged == acknowledged
            if phase == "release":
                assert exc.code == (
                    "committed_result_unavailable" if acknowledged else "unavailable"
                )
            assert not exc.termination_failed
            if self.kind == "lifecycle":
                assert exc.command_id is self.command


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["metadata", "lifecycle"])
@pytest.mark.parametrize("write", [True, False])
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
async def test_native_release_ownership_survives_interruption(kind, write, interrupt, monkeypatch):
    rig = ReleaseRig(kind, write)
    await rig.start()
    entered, finish = asyncio.Event(), asyncio.Event()
    resets = []

    async def paused_reset(conn):
        assert conn is rig.old
        resets.append(asyncio.current_task())
        entered.set()
        await finish.wait()
        raise RuntimeError("synthetic old reset failure")

    rig.pool._reset = paused_reset
    # Exercise the ADAPTER'S absolute timeout, with budget already spent on COMMIT.
    monkeypatch.setattr(rig.module, "OPERATION_S", 0.3 if interrupt == "deadline" else 2)
    rig.commit_delay = 0.18 if interrupt == "deadline" else 0
    task = asyncio.create_task(rig.call())
    try:
        async with asyncio.timeout(1):
            await entered.wait()
        release_entered = asyncio.get_running_loop().time()
        if interrupt == "cancel":
            task.cancel("original caller cancellation")
        with pytest.raises(
            asyncio.CancelledError if interrupt == "cancel" else Exception
        ) as caught:
            async with asyncio.timeout(1):
                await task
        if interrupt == "deadline":
            # Restarting a 0.3s operation allowance at release would violate this.
            assert asyncio.get_running_loop().time() - release_entered < 0.24
        else:
            assert caught.value is rig.cancelled
            assert caught.value.args == ("original caller cancellation",)
        rig.check_error(caught.value, acknowledged=write, cancellation=interrupt == "cancel")
        assert rig.calls == ["begin", "settings", "statement", "commit"]
        rig.pool.acquire.assert_awaited_once_with(timeout=0.5)
        rig.pool.release.assert_awaited_once()
        # Within native release's 0.5s allowance the OLD reset still owns the
        # holder, not the adapter or a borrower. The pause does not disable its cap.
        assert rig.pool._queue.empty()
        assert rig.proxy._con is rig.old and rig.old.termination_calls == 0
    finally:
        finish.set()
        async with asyncio.timeout(2):
            outcomes = await asyncio.gather(*resets, return_exceptions=True)
            await asyncio.gather(task, return_exceptions=True)
    assert len(outcomes) == 1 and isinstance(outcomes[0], RuntimeError)
    assert rig.pool._queue.get_nowait() is rig.holder
    new = rig.physical()
    rig.pool._get_new_connection = AsyncMock(return_value=new)
    new_proxy = await rig.holder.acquire()
    try:
        assert rig.old.termination_calls == 1
        assert new_proxy._con is new and new.termination_calls == 0
        assert rig.proxy._con is None
    finally:
        new.terminate()  # Test-owned synthetic holder teardown, not adapter cleanup.


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["metadata", "lifecycle"])
@pytest.mark.parametrize("write", [True, False])
@pytest.mark.parametrize("boundary", ["before_shield", "detached"])
@pytest.mark.parametrize("cancellation", [False, True])
async def test_failed_release_never_reclaims_proxy(kind, write, boundary, cancellation):
    rig = ReleaseRig(kind, write)
    await rig.start()
    original = (
        asyncio.CancelledError("original release cancellation")
        if cancellation
        else ConnectionError()
    )
    if boundary == "before_shield":
        rig.on_release_error = original

    async def failed_release(conn, *, timeout):
        # Both boundaries enter the installed driver; _on_release raises before
        # Pool.release creates its shield in the first case.
        await asyncpg.Pool.release(rig.pool, conn, timeout=timeout)
        if boundary == "detached":
            assert conn._con is None
        raise original

    rig.pool.release = AsyncMock(side_effect=failed_release)
    try:
        with pytest.raises(asyncio.CancelledError if cancellation else Exception) as caught:
            await rig.call()
        if cancellation:
            assert caught.value is original
        rig.check_error(caught.value, acknowledged=write, cancellation=cancellation)
        assert rig.old.termination_calls == 0
        if isinstance(rig.store, md.ComputeMetadataStore):
            assert rig.store.cleanup_events == []
        if boundary == "before_shield":
            # Unresolved driver cleanup is deliberately NOT application reclaim.
            assert rig.proxy._con is rig.old and rig.pool._queue.empty()
        else:
            assert rig.proxy._con is None
        rig.pool.release.assert_awaited_once_with(rig.proxy, timeout=0.5)
        assert rig.calls.count("commit") == rig.calls.count("statement") == 1
    finally:
        if rig.proxy._con is not None:
            rig.old.terminate()  # Explicit test cleanup of unresolved synthetic ownership.


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["metadata", "lifecycle"])
@pytest.mark.parametrize("phase", ["preflight", "commit", "decode"])
async def test_pretransfer_failure_still_owns_cleanup(kind, phase):
    rig = ReleaseRig(kind, True)
    await rig.start()
    rig.fault = phase
    with pytest.raises(Exception) as caught:
        await rig.call()
    rig.check_error(caught.value, acknowledged=phase == "decode", phase=phase)
    assert rig.old.termination_calls == 1
    assert rig.proxy._con is None
    rig.pool.release.assert_not_awaited()
    if phase == "commit":
        assert isinstance(caught.value, (md.MetadataError, lc.LifecycleError))
        assert caught.value.code == "write_uncertain"
