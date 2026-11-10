"""Explicit synthetic observations for memory business-logic test doubles.

These are not transaction-fence evidence; registered writers and actual lock,
commit, erasure and startup behavior are qualified by real PostgreSQL suites.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager
from datetime import UTC, datetime


def install_capture_pool(store: Any, *, generation: int = 0) -> None:
    account = {"status": "active", "content_generation": generation}
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=account)
    pool.fetchval = AsyncMock(return_value=0)
    # HTTP import units also exercise the real post-commit observer. Model an
    # empty synthetic clock with the actual account/transaction guard protocol;
    # do not monkeypatch observation, accept writes, or claim SQL qualification.
    conn = MagicMock()
    active = False
    conn.is_in_transaction.side_effect = lambda: active

    @asynccontextmanager
    async def transaction(*, isolation):
        nonlocal active
        assert isolation == "read_committed" and not active
        active = True
        try:
            yield
        finally:
            active = False

    @asynccontextmanager
    async def acquire():
        yield conn

    async def fetchrow(sql, *args):
        assert active
        if "FROM entitlement_accounts" in sql:
            return account
        if "AS operation" in sql:
            return {"operation": False, "cause": False}
        assert "FROM memory_knowledge_clock" in sql, sql
        return None

    async def fetchval(sql, *args):
        assert active
        if sql == "SELECT txid_current()":
            return 1
        if sql == "SHOW transaction_isolation":
            return "read committed"
        if "pg_try_advisory_xact_lock" in sql:
            return True
        assert sql == "SELECT clock_timestamp()", sql
        return datetime.now(UTC)

    conn.transaction = transaction
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.fetchval = AsyncMock(side_effect=fetchval)
    pool.acquire = acquire
    store._database_pool = pool


def memory_store_mock() -> AsyncMock:
    store = AsyncMock()
    install_capture_pool(store)
    return store
