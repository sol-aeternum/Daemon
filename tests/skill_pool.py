"""AsyncMock pool whose ``acquire()``/``transaction()`` work as async context managers.

The connection delegates to the pool mock, so tests keep configuring and
asserting on ``pool.fetchrow`` / ``pool.execute`` as before.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock


def lockable_pool() -> AsyncMock:
    pool = AsyncMock()

    @asynccontextmanager
    async def _transaction():
        yield

    @asynccontextmanager
    async def _acquire():
        conn = MagicMock()
        conn.fetchrow = pool.fetchrow
        conn.execute = pool.execute
        conn.transaction = _transaction
        yield conn

    pool.acquire = MagicMock(side_effect=_acquire)
    return pool
