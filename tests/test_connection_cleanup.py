"""Synthetic release ownership; no actual pool or running application."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.services import connection_cleanup as cleanup


async def query(pool, text, *args):
    async with cleanup.pool_queries(pool) as source:
        return await source.fetchrow(text, *args)


@pytest.mark.asyncio
async def test_unscoped_connection_and_convenience_call_keep_native_behavior():
    conn = object()
    visits = []

    @asynccontextmanager
    async def acquire():
        visits.append("enter")
        try:
            yield conn
        finally:
            visits.append("exit")

    pool = SimpleNamespace(acquire=acquire, release=AsyncMock(), fetchrow=AsyncMock(return_value=7))
    async with cleanup.pool_connection(pool) as actual:
        assert actual is conn
    assert visits == ["enter", "exit"]
    assert await query(pool, "synthetic", 1) == 7
    pool.fetchrow.assert_awaited_once_with("synthetic", 1)
    pool.release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_body_exception_survives_repeated_cancellation_and_release_failure(failure):
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = ValueError("first body failure")
    finished = []

    async def release(conn):
        entered.set()
        await proceed.wait()
        finished.append(True)
        if failure:
            raise RuntimeError("synthetic cleanup failure")

    pool = SimpleNamespace(acquire=AsyncMock(return_value=object()), release=release)

    async def owner():
        async with cleanup.retain_pool_cleanup(), cleanup.pool_connection(pool):
            raise original

    task = asyncio.create_task(owner())
    try:
        await entered.wait()
        task.cancel("later cancellation")
        await asyncio.sleep(0)
        task.cancel("another cancellation")
        await asyncio.sleep(0)
        assert not task.done() and finished == []
        proceed.set()
        with pytest.raises(ValueError) as caught:
            await task
        assert caught.value is original and finished == [True]
        assert any("Native connection cleanup failed" in note for note in original.__notes__)
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_pool_acquisition_is_directly_cancellable():
    entered = asyncio.Event()

    async def acquire():
        entered.set()
        await asyncio.Event().wait()

    pool = SimpleNamespace(acquire=acquire, release=AsyncMock())

    async def owner():
        async with cleanup.retain_pool_cleanup(), cleanup.pool_connection(pool):
            pytest.fail("queued acquisition entered body")

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel("queued acquisition")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("queued acquisition",)
    pool.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_inherited_scope_and_nested_scope_refuse_without_io():
    pool = SimpleNamespace(acquire=AsyncMock(), release=AsyncMock(), fetchrow=AsyncMock())
    async with cleanup.retain_pool_cleanup():
        child = asyncio.create_task(query(pool, "synthetic"))
        with pytest.raises(cleanup.CleanupUnavailable):
            await child
        with pytest.raises(cleanup.CleanupUnavailable, match="nested"):
            async with cleanup.retain_pool_cleanup():
                pytest.fail("nested scope entered")
    pool.acquire.assert_not_awaited()
    pool.release.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()
    await query(pool, "outside")
    pool.fetchrow.assert_awaited_once_with("outside")


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_be_caught_to_resume_or_report_success():
    pool = SimpleNamespace(
        acquire=AsyncMock(return_value=object()),
        release=AsyncMock(side_effect=RuntimeError("reset failed")),
        fetchrow=AsyncMock(),
    )
    with pytest.raises(cleanup.CleanupUnavailable):
        async with cleanup.retain_pool_cleanup():
            with pytest.raises(RuntimeError, match="reset failed"):
                async with cleanup.pool_connection(pool):
                    pass
            with pytest.raises(cleanup.CleanupUnavailable):
                await query(pool, "no readback")
    pool.acquire.assert_awaited_once()
    pool.release.assert_awaited_once()
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_scheduling_failure_terminates_exact_owned_connection(monkeypatch):
    original = RuntimeError("cannot schedule release")
    conn = SimpleNamespace(terminate=Mock())
    pool = SimpleNamespace(acquire=AsyncMock(return_value=conn), release=AsyncMock())

    def unavailable(call):
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(cleanup.asyncio, "create_task", unavailable)
        with pytest.raises(RuntimeError) as caught:
            async with cleanup.retain_pool_cleanup(), cleanup.pool_connection(pool):
                pass
    assert caught.value is original
    conn.terminate.assert_called_once_with()
