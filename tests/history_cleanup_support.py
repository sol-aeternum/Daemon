"""Hold the actual asyncpg holder reset, not a replacement pool/release stub."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from orchestrator.services.restore_history.model import HistoryError
from tests.test_history_coordination import available


@asynccontextmanager
async def held_reset(pool, monkeypatch, target, failure):
    entered, proceed = asyncio.Event(), asyncio.Event()
    native = pool._reset
    calls = 0
    workers = []

    async def reset(conn):
        nonlocal calls
        calls += 1
        if calls == target:
            workers.append(asyncio.current_task())
            entered.set()
            await proceed.wait()
            if failure:
                raise RuntimeError("synthetic held holder reset failure")
        if native is not None:
            await native(conn)
        else:
            await conn.execute(conn.get_reset_query())

    monkeypatch.setattr(pool, "_reset", reset)
    try:
        yield entered, proceed
    finally:
        proceed.set()
        await asyncio.gather(*(w for w in workers if w is not None), return_exceptions=True)


async def interrupted_reset(s, options, entered, proceed, mode):
    limit = asyncio.get_running_loop().time() + 3
    task = asyncio.create_task(s.accept.accept(deadline=limit, **options))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if mode == "cancel":
            task.cancel("original held-reset cancellation")
            await asyncio.sleep(0.03)
            task.cancel("repeated held-reset cancellation")
        else:
            await asyncio.sleep(max(0, limit - asyncio.get_running_loop().time()) + 0.03)
            task.cancel("repeated cancellation after deadline")
        await asyncio.sleep(0.03)
        assert not task.done(), "acceptance response escaped while actual holder reset was pending"
        assert not available(s.path, "OP_LOCK"), "history OP released before native reset settled"
        assert not available(s.path, "RUNTIME_LOCK")
        proceed.set()
        error = asyncio.CancelledError if mode == "cancel" else TimeoutError
        with pytest.raises(error) as caught:
            await task
        if mode == "cancel":
            assert caught.value.args == ("original held-reset cancellation",)
        assert available(s.path, "OP_LOCK")
        with pytest.raises(HistoryError, match="blocked"):
            await s.accept.accept(deadline=limit + 10, **options)
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


async def failed_reset(s, options, entered, proceed):
    limit = asyncio.get_running_loop().time() + 10
    task = asyncio.create_task(s.accept.accept(deadline=limit, **options))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done() and not available(s.path, "OP_LOCK")
        proceed.set()
        with pytest.raises(RuntimeError, match="holder reset failure"):
            await task
        assert available(s.path, "OP_LOCK")
        with pytest.raises(HistoryError, match="blocked"):
            await s.accept.accept(deadline=limit, **options)
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)
