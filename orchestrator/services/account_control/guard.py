"""Uncalled J1c-1a content-writer prerequisite, NOT a completed account fence.

Capture before deriving inputs. Acquire on the caller's READ COMMITTED transaction
before content locks; revalidate after rollback to a savepoint predating the lock.
The helper cannot intercept raw SQL or enforce caller ownership/lock ordering.
Cooperating control writers must lock the same account FOR UPDATE before changing
operations/causes; a subordinate FK lock alone does not serialize this guard.
No mutation, journal access, privileged bypass, commit or readiness release.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias
from uuid import UUID

import asyncpg
import asyncpg.pool

Connection: TypeAlias = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
RefusalCode: TypeAlias = Literal[
    "invalid_capture",
    "missing_account",
    "account_restricted",
    "stale_generation",
    "open_operation",
    "inconsistent_state",
    "transaction_required",
    "unsupported_isolation",
    "scope_mismatch",
    "scope_expired",
]


class WriteGuardRefusal(Exception):
    """Closed validation codes; database/cancellation errors also deny permission."""

    def __init__(self, code: RefusalCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CapturedGeneration:
    """Immutable originating account/generation value, not write authority."""

    account_id: UUID
    generation: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.account_id, UUID)
            or type(self.generation) is not int
            or not 0 <= self.generation <= 2**63 - 1
        ):
            raise WriteGuardRefusal("invalid_capture")


def _account_row(row: Mapping[str, Any] | None) -> int:
    if row is None:
        raise WriteGuardRefusal("missing_account")
    if row["status"] == "suspended":
        # Includes legacy status-only suspension: do not invent cause provenance.
        raise WriteGuardRefusal("account_restricted")
    generation = row["content_generation"]
    if row["status"] != "active" or type(generation) is not int or generation < 0:
        raise WriteGuardRefusal("inconsistent_state")
    return generation


async def capture_generation(conn: Connection, account_id: UUID) -> CapturedGeneration:
    """Read BEFORE inputs/provider work; never recapture to cure a stale commit.

    No lock or transaction authority is retained. The later guard must check all
    state again. Caller-supplied identity is not authentication or owner proof.
    """
    if not isinstance(account_id, UUID):
        raise WriteGuardRefusal("invalid_capture")
    row = await conn.fetchrow(
        "SELECT status, content_generation FROM entitlement_accounts WHERE user_id=$1",
        account_id,
    )
    return CapturedGeneration(account_id, _account_row(row))


class ContentWriteScope:
    """Same-connection, outer-transaction authority for cooperating content callers.

    No public constructor. Every revalidation reacquires SHARE, including after a
    savepoint rollback. The caller MUST revalidate before further raw SQL after
    such a rollback; the Python context cannot detect/intercept arbitrary SQL.
    Locks live through the caller's commit, even after this scope exits.
    """

    __slots__ = ("_conn", "_expected", "_xid", "_active")

    _conn: Connection
    _expected: CapturedGeneration
    _xid: int
    _active: bool

    def __init__(self) -> None:
        raise TypeError("use guard_content_write")

    async def revalidate(self, conn: Connection, expected: CapturedGeneration) -> None:
        """Reacquire/recheck immediately before caller writes after rollback."""
        if not self._active:
            raise WriteGuardRefusal("scope_expired")
        if conn is not self._conn or expected != self._expected:
            raise WriteGuardRefusal("scope_mismatch")
        if not conn.is_in_transaction():
            raise WriteGuardRefusal("transaction_required")
        if await conn.fetchval("SELECT txid_current()") != self._xid:
            raise WriteGuardRefusal("scope_expired")
        if await conn.fetchval("SHOW transaction_isolation") != "read committed":
            # A stale RR snapshot can miss cause-only/operation-only commits;
            # tuple-lock serialization failure is not a sufficient guarantee.
            raise WriteGuardRefusal("unsupported_isolation")
        row = await conn.fetchrow(
            "SELECT status, content_generation FROM entitlement_accounts "
            "WHERE user_id=$1 FOR SHARE",
            expected.account_id,
        )
        if _account_row(row) != expected.generation:
            raise WriteGuardRefusal("stale_generation")
        # MUST be a separate statement after the possibly waiting row lock:
        # READ COMMITTED obtains a fresh snapshot for subordinate-only changes.
        state = await conn.fetchrow(
            "SELECT EXISTS(SELECT 1 FROM account_deletions "
            "WHERE account_id=$1 AND closed_at IS NULL) AS operation, "
            "EXISTS(SELECT 1 FROM account_suspension_causes WHERE account_id=$1) AS cause",
            expected.account_id,
        )
        if state is None:
            raise WriteGuardRefusal("inconsistent_state")
        if state["operation"]:
            raise WriteGuardRefusal("open_operation")
        if state["cause"]:
            raise WriteGuardRefusal("inconsistent_state")


@asynccontextmanager
async def guard_content_write(
    conn: Connection, expected: CapturedGeneration
) -> AsyncIterator[ContentWriteScope]:
    """Acquire account SHARE before content locks, without ending caller's txn.

    The caller owns rollback on refusal/cancellation and may not continue writes
    without renewed validation. Control/settlement/evidence need separate APIs.
    """
    if not isinstance(expected, CapturedGeneration):
        raise WriteGuardRefusal("invalid_capture")
    if not conn.is_in_transaction():
        raise WriteGuardRefusal("transaction_required")
    scope = object.__new__(ContentWriteScope)
    scope._conn = conn
    scope._expected = expected
    xid = await conn.fetchval("SELECT txid_current()")
    if type(xid) is not int:
        raise WriteGuardRefusal("transaction_required")
    scope._xid = xid
    scope._active = True
    try:
        await scope.revalidate(conn, expected)
        yield scope
    finally:
        scope._active = False
