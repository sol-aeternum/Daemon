"""Opt-in task-owned pool release draining; no pool replacement or authority.

Only the uncalled history join enters this scope. Outside it the helpers use
the original pool operations. Acquisition remains directly cancellable; once a
connection is returned its actual release task must settle before scope exit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


class CleanupUnavailable(RuntimeError):
    """An ownership failure, never permission to retry or acknowledge."""


@dataclass
class _Scope:
    task: asyncio.Task[Any] | None
    failed: bool = False

    def check(self) -> None:
        if self.failed or self.task is None or asyncio.current_task() is not self.task:
            raise CleanupUnavailable("native_cleanup_owner_unavailable")


_scope: ContextVar[_Scope | None] = ContextVar("native_connection_cleanup", default=None)


@asynccontextmanager
async def retain_pool_cleanup() -> AsyncIterator[None]:
    """Enter only inside the H owner; inherited tasks cannot borrow this scope."""
    if _scope.get() is not None:
        raise CleanupUnavailable("native_cleanup_scope_nested")
    owner = _Scope(asyncio.current_task())
    owner.check()
    token = _scope.set(owner)
    try:
        yield
        owner.check()
    finally:
        _scope.reset(token)


async def _release(pool: Any, conn: Any, owner: _Scope) -> None:
    call = pool.release(conn)
    try:
        release = asyncio.create_task(call)
    except BaseException as original:
        owner.failed = True
        call.close()
        try:
            conn.terminate()
        except BaseException:
            original.add_note("Native connection termination also failed")
        raise
    try:
        await asyncio.shield(release)
    except BaseException as original:
        owner.failed = True
        # asyncpg itself shields holder.reset/release. Never cancel our retained
        # Pool.release task: joining a cancelled wrapper would lose that owner.
        while not release.done():
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            release.result()
        except BaseException as secondary:
            if secondary is not original:
                original.add_note("Native cleanup completion: " + type(secondary).__name__)
        raise


@asynccontextmanager
async def pool_connection(pool: Any) -> AsyncIterator[Any]:
    owner = _scope.get()
    if owner is None:
        async with pool.acquire() as conn:
            yield conn
        return
    owner.check()
    conn = await pool.acquire()  # A queued acquisition owns no release task.
    original: BaseException | None = None
    try:
        yield conn
    except BaseException as exc:
        original = exc
        raise
    finally:
        try:
            await _release(pool, conn, owner)
        except BaseException as cleanup:
            owner.failed = True
            if original is None:
                raise
            original.add_note("Native connection cleanup failed: " + type(cleanup).__name__)


@asynccontextmanager
async def pool_queries(pool: Any) -> AsyncIterator[Any]:
    """Preserve native convenience calls by default; retain their scoped lease."""
    if _scope.get() is None:
        yield pool
        return
    async with pool_connection(pool) as conn:
        yield conn
