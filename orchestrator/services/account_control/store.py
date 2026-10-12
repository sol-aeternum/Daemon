"""J1b request-fact persistence, deliberately NOT a completed request fence.

All functions require caller-owned transactions. There is no runtime producer,
status writer, phase/closure API, applied-marker writer or readiness path here.
Only an actual durable journal scan under account locks can supply request facts.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeAlias
from uuid import UUID

import asyncpg
import asyncpg.pool

from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.model import Cause, Entry, JournalState, RequestFields
from orchestrator.services.deletion_journal.store import Journal

Connection: TypeAlias = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
_DOMAIN = b"daemon:account-control:v1\0"
_COLUMNS = (
    "id,account_id,kind,journal_id,request_seq,request_event_id,request_digest,"
    "command_digest,requested_at,prior_status,prior_reason,fence_generation,"
    "drain_deadline,revocation_deadline,billing_deadline,no_grace,reason,closed_at"
)


def account_lock_key(account_id: UUID) -> int:
    """One bigint namespace, separate from J1a's two-int recovery lock."""
    return int.from_bytes(
        hashlib.sha256(_DOMAIN + account_id.bytes).digest()[:8], "big", signed=True
    )


@dataclass(frozen=True, slots=True)
class OperationRecord:
    id: UUID
    account_id: UUID
    kind: str
    journal_id: UUID
    request_seq: int
    request_event_id: UUID
    request_digest: bytes
    command_digest: bytes
    requested_at: datetime
    prior_status: str
    prior_reason: str | None
    fence_generation: int
    drain_deadline: datetime
    revocation_deadline: datetime
    billing_deadline: datetime
    no_grace: bool
    reason: str | None
    closed_at: datetime | None


def _record(row: Mapping[str, Any]) -> OperationRecord:
    return OperationRecord(**{name: row[name] for name in _COLUMNS.split(",")})


def _request_record(entry: Entry) -> OperationRecord:
    command, fields = entry.command, entry.command.fields
    if command.event_type not in {"requested", "reset_requested"} or not isinstance(
        fields, RequestFields
    ):
        raise JournalError("invalid_entry")
    return OperationRecord(
        command.operation_id,
        command.account_id,
        "deletion" if command.event_type == "requested" else "reset",
        entry.journal_id,
        entry.seq,
        entry.event_id,
        entry.digest,
        entry.command_digest,
        command.decision_at,
        fields.prior_status,
        fields.prior_reason,
        fields.generation,
        fields.drain_deadline,
        fields.revocation_deadline,
        fields.billing_deadline,
        fields.no_grace,
        fields.reason,
        None,
    )


async def _scan(journal: Journal) -> JournalState:
    # Retain the executor future directly, rather than a cancellable child task:
    # all-task shutdown must not cancel our cleanup waiter and spin on that task.
    # Shield caller cancellation until live I/O releases its file locks/FDs.
    future = asyncio.get_running_loop().run_in_executor(
        None, contextvars.copy_context().run, journal.verify
    )
    cancelled = False
    while True:
        try:
            view = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if (
                future.done()
                and not future.cancelled()
                and isinstance(future.exception(), asyncio.CancelledError)
            ):
                # The call itself raised CancelledError: its thread is done,
                # and re-awaiting would re-raise at once, forever (#516).
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return view


class VerifiedRequest:
    """A concrete request scanned in this live account/transaction scope.

    No public constructor. This is provenance for persistence ONLY, never a
    certificate that cancellation, sessions, writers or namespaces are fenced.
    """

    __slots__ = ("_scope", "_entry")

    _scope: LockedAccount
    _entry: Entry

    def __init__(self) -> None:
        raise TypeError("use the locked account's verify_request")


class LockedAccount:
    """Caller-transaction scope; only constructed by lock_account_control."""

    def __init__(self, conn: Connection, account_id: UUID, operation_id: UUID | None) -> None:
        self._conn = conn
        self._account_id = account_id
        self._operation_id = operation_id
        self._xid: int | None = None
        self._active = False
        self._verified: list[tuple[VerifiedRequest, Entry]] = []

    async def _check(self) -> None:
        if (
            not self._active
            or not self._conn.is_in_transaction()
            or await self._conn.fetchval("SELECT txid_current()") != self._xid
        ):
            raise JournalError("conflict")

    async def _relock(self) -> Mapping[str, Any]:
        await self._check()
        # Reacquire even after a caller rolls back a savepoint that held locks.
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock($1)", account_lock_key(self._account_id)
        )
        row: Mapping[str, Any] | None = await self._conn.fetchrow(
            "SELECT user_id,status,content_generation FROM entitlement_accounts "
            "WHERE user_id=$1 FOR UPDATE",
            self._account_id,
        )
        if row is None:
            raise JournalError("conflict")
        if self._operation_id is not None:
            operation = await self._conn.fetchrow(
                "SELECT account_id FROM account_deletions WHERE id=$1 FOR UPDATE",
                self._operation_id,
            )
            if operation is not None and operation["account_id"] != self._account_id:
                raise JournalError("conflict")
        await self._conn.fetch(
            "SELECT id FROM account_deletions WHERE account_id=$1 AND closed_at IS NULL FOR UPDATE",
            self._account_id,
        )
        return row

    async def verify_request(self, journal: Journal, event_id: UUID) -> VerifiedRequest:
        """Resolve immutable retry identity by an actual durable scan, not a view supplied by a caller."""
        await self._relock()
        view = await _scan(journal)
        await self._relock()  # I/O must not turn a committed/replaced transaction into authority.
        binding = await self._conn.fetchval(
            "SELECT journal_id FROM deletion_journal_binding WHERE singleton=true"
        )
        if binding != journal.journal_id:
            raise JournalError("marker_mismatch")
        entry = view.events.get(event_id)
        if entry is None:
            raise JournalError("invalid_entry")
        _request_record(entry)
        if entry.command.account_id != self._account_id or (
            self._operation_id is not None and entry.command.operation_id != self._operation_id
        ):
            raise JournalError("conflict")
        # Verify markers themselves even on an idempotent fact-record retry.
        marked = await _markers(self._conn, view)
        if any(
            previous.command.account_id == self._account_id
            and previous.seq < entry.seq
            and previous.seq not in marked
            for previous in view.entries
        ):
            raise JournalError("recovery_required")
        result = object.__new__(VerifiedRequest)
        result._scope = self
        result._entry = entry
        self._verified.append((result, entry))
        return result

    async def _list_causes(self) -> tuple[Cause, ...]:
        await self._relock()
        rows = await self._conn.fetch(
            "SELECT cause_code,cause_id FROM account_suspension_causes "
            "WHERE account_id=$1 ORDER BY cause_code,cause_id",
            self._account_id,
        )
        return tuple(Cause(row["cause_code"], row["cause_id"]) for row in rows)

    async def _insert_cause(self, cause: Cause) -> None:
        """Private SQL primitive, not a journaled suspension transition."""
        await self._relock()
        existing = await self._conn.fetchval(
            "SELECT cause_code FROM account_suspension_causes WHERE account_id=$1 AND cause_id=$2",
            self._account_id,
            cause.id,
        )
        if existing is not None and existing != cause.code:
            raise JournalError("conflict")
        await self._conn.execute(
            "INSERT INTO account_suspension_causes(account_id,cause_id,cause_code) "
            "VALUES($1,$2,$3) ON CONFLICT(account_id,cause_id) DO NOTHING",
            self._account_id,
            cause.id,
            cause.code,
        )

    async def _remove_cause_exact(self, cause: Cause) -> None:
        """Remove only the named pair; never activate the entitlement account."""
        await self._relock()
        await self._conn.execute(
            "DELETE FROM account_suspension_causes WHERE account_id=$1 AND cause_id=$2 AND cause_code=$3",
            self._account_id,
            cause.id,
            cause.code,
        )


async def _markers(conn: Connection, view: JournalState) -> set[int]:
    entries = {entry.seq: entry for entry in view.entries}
    marked: set[int] = set()
    for row in await conn.fetch(
        "SELECT journal_id,seq,event_id,account_id,operation_id,entry_digest FROM deletion_journal_applied"
    ):
        entry = entries.get(row["seq"])
        if (
            entry is None
            or row["journal_id"] != entry.journal_id
            or row["event_id"] != entry.event_id
            or row["account_id"] != entry.command.account_id
            or row["operation_id"] != entry.command.operation_id
            or bytes(row["entry_digest"]) != entry.digest
        ):
            raise JournalError("marker_mismatch")
        marked.add(entry.seq)
    return marked


@asynccontextmanager
async def lock_account_control(
    conn: Connection, account_id: UUID, operation_id: UUID | None = None
) -> AsyncIterator[LockedAccount]:
    """Advisory -> entitlement -> operation locks inside a caller-owned transaction."""
    if not conn.is_in_transaction() or not isinstance(account_id, UUID):
        raise JournalError("conflict")
    scope = LockedAccount(conn, account_id, operation_id)
    scope._xid = await conn.fetchval("SELECT txid_current()")
    scope._active = True
    try:
        await scope._relock()
        yield scope
    finally:
        scope._active = False
        scope._verified.clear()


async def record_request_facts(
    conn: Connection, verified_entry: VerifiedRequest
) -> OperationRecord:
    """Record request facts atomically; deliberately DO NOT record the request as applied."""
    if not isinstance(verified_entry, VerifiedRequest):
        raise JournalError("invalid_entry")
    scope = getattr(verified_entry, "_scope", None)
    if not isinstance(scope, LockedAccount):
        raise JournalError("invalid_entry")
    entry = next((entry for item, entry in scope._verified if item is verified_entry), None)
    if conn is not scope._conn or entry is None:
        raise JournalError("conflict")
    account = await scope._relock()
    expected = _request_record(entry)
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM account_deletions "
        "WHERE id=$1 OR (journal_id=$2 AND (request_seq=$3 OR request_event_id=$4)) FOR UPDATE",
        expected.id,
        expected.journal_id,
        expected.request_seq,
        expected.request_event_id,
    )
    if rows:
        if len(rows) != 1:
            raise JournalError("conflict")
        original = _record(rows[0])
        # closed_at is mutable only in future handlers; retries never reopen it.
        if any(
            getattr(original, field) != getattr(expected, field)
            for field in _COLUMNS.split(",")
            if field != "closed_at"
        ):
            raise JournalError("conflict")
        return original
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM deletion_journal_applied WHERE journal_id=$1 AND seq=$2)",
        entry.journal_id,
        entry.seq,
    ):
        raise JournalError("marker_mismatch")  # a marker cannot stand in for a lost operation row
    fields = entry.command.fields
    assert isinstance(fields, RequestFields)
    existing = set(await scope._list_causes())
    recorded = set(fields.causes)
    unrelated = recorded - {Cause(expected.kind, expected.id)}
    if (
        not existing <= recorded
        or (fields.prior_status == "suspended" and not unrelated)
        or (fields.prior_status == "active" and unrelated)
        or (account["status"] == "suspended" and fields.prior_status == "active")
        or int(account["content_generation"]) >= fields.generation
        or await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM account_deletions WHERE account_id=$1 AND closed_at IS NULL)",
            expected.account_id,
        )
    ):
        raise JournalError("conflict")
    # Savepoint makes partial helper failures atomic even if the caller catches
    # an SQL exception and elects to commit its unrelated outer transaction.
    async with conn.transaction():
        await conn.execute(
            f"INSERT INTO account_deletions({_COLUMNS}) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)",
            *(getattr(expected, field) for field in _COLUMNS.split(",")),
        )
        for cause in fields.causes:
            await scope._insert_cause(cause)
        await conn.execute(
            "UPDATE entitlement_accounts SET content_generation=$2 WHERE user_id=$1",
            expected.account_id,
            fields.generation,
        )
    return expected
