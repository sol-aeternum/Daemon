"""Startup evidence barrier. J1a deliberately has NO pending-entry applier."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import UUID

import asyncpg

from orchestrator.config import Settings

from .errors import JournalError
from .model import JournalState
from .store import Journal

# Two-int PostgreSQL advisory-lock namespace (independent of bigint account
# locks). Shared by every backend/worker; never use advisory_shared_lock here.
RECOVERY_KEY = (0x4A314A52, 1)
STARTUP_BUDGET_S = 300.0
T = TypeVar("T")


def configured_journal(settings: Settings) -> tuple[Path, UUID] | None:
    directory = settings.daemon_deletion_journal_dir
    identity = settings.daemon_deletion_journal_id
    if not directory and not identity:
        return None
    if not directory or not identity:
        raise JournalError("invalid_config")
    try:
        path = Path(directory)
        uid = UUID(identity)
        if not path.is_absolute() or str(uid) != identity:
            raise JournalError("invalid_config")
        return path, uid
    except (ValueError, TypeError):
        raise JournalError("invalid_config") from None


async def _io(call: Callable[[], T]) -> T:
    """Cancellation never outlives an I/O thread still owning file descriptors.

    A hung filesystem can delay process exit; it cannot make the barrier ready.
    asyncio cancellation does not terminate threads, so wait for lock release
    rather than close/reuse descriptors while the thread still writes.
    """
    # Retain the executor future, not a child task that all-task shutdown can
    # cancel independently while its filesystem thread is still running.
    future = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, call)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if future.done():
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
    return result


async def verify_applied(
    connection: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, view: JournalState
) -> None:
    """Compare every marker, including ids; never infer application from time."""
    entries = {entry.seq: entry for entry in view.entries}
    rows = await connection.fetch(
        "SELECT journal_id,seq,event_id,account_id,operation_id,entry_digest "
        "FROM deletion_journal_applied ORDER BY seq"
    )
    for row in rows:
        entry = entries.pop(row["seq"], None)
        if (
            entry is None
            or row["journal_id"] != entry.journal_id
            or row["event_id"] != entry.event_id
            or row["account_id"] != entry.command.account_id
            or row["operation_id"] != entry.command.operation_id
            or bytes(row["entry_digest"]) != entry.digest
        ):
            raise JournalError("marker_mismatch")
    if entries:
        # No callback/dispatch, and most importantly no INSERT of fake applied
        # markers: the real fence/cancel/purge handlers have not shipped.
        raise JournalError("recovery_required")


async def _verify_schema(
    connection: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, configured: bool
) -> bool:
    row = await connection.fetchrow(
        "SELECT to_regclass('deletion_journal_binding') AS binding, "
        "to_regclass('deletion_journal_applied') AS applied"
    )
    if row is None:
        raise JournalError("schema_required")
    if row["binding"] is None and row["applied"] is None and not configured:
        # Positive catalog evidence for a wholly pre-J1 schema; never swallow
        # an SQL/permission/connection error and reinterpret it as no markers.
        return False
    if row["binding"] is None or row["applied"] is None:
        raise JournalError("schema_required")
    for table, expected in (
        ("deletion_journal_binding", {"singleton": "bool", "journal_id": "uuid"}),
        (
            "deletion_journal_applied",
            {
                "journal_id": "uuid",
                "seq": "int8",
                "event_id": "uuid",
                "account_id": "uuid",
                "operation_id": "uuid",
                "entry_digest": "bytea",
            },
        ),
    ):
        columns = await connection.fetch(
            "SELECT a.attname,t.typname,c.relkind FROM pg_attribute a "
            "JOIN pg_type t ON t.oid=a.atttypid JOIN pg_class c ON c.oid=a.attrelid "
            "WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped",
            table,
        )
        if {col["attname"]: col["typname"] for col in columns} != expected or any(
            col["relkind"] != b"r" and col["relkind"] != "r" for col in columns
        ):
            raise JournalError("schema_required")
    # Validate readable columns even for empty tables. Missing/ill-typed objects
    # are not evidence of an unbound deployment; source migrations own constraints.
    await connection.fetch("SELECT singleton,journal_id FROM deletion_journal_binding")
    await connection.fetch(
        "SELECT journal_id,seq,event_id,account_id,operation_id,entry_digest "
        "FROM deletion_journal_applied LIMIT 0"
    )
    return True


async def _barrier(pool: asyncpg.Pool, settings: Settings) -> None:
    config = configured_journal(settings)
    async with pool.acquire() as connection:
        locked = False
        try:
            # A cancelled lock query may have succeeded before its response was
            # observed. On ANY uncertain acquisition/cleanup, terminate rather
            # than return a session-level lock to the pool.
            await connection.execute("SELECT pg_advisory_lock($1,$2)", *RECOVERY_KEY)
            locked = True
            async with connection.transaction():
                schema = await _verify_schema(connection, config is not None)
                if not schema:
                    return
                rows = await connection.fetch(
                    "SELECT singleton,journal_id FROM deletion_journal_binding"
                )
                if config is None:
                    if rows or await connection.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM deletion_journal_applied)"
                    ):
                        raise JournalError("invalid_config")
                    return
                directory, identity = config
                if rows and (
                    len(rows) != 1
                    or rows[0]["singleton"] is not True
                    or rows[0]["journal_id"] != identity
                ):
                    raise JournalError("marker_mismatch")

                def verify() -> JournalState:
                    with Journal(directory, identity) as journal:
                        return journal.verify()

                view = await _io(verify)
                await verify_applied(connection, view)
                if not rows:
                    await connection.execute(
                        "INSERT INTO deletion_journal_binding(singleton,journal_id) VALUES(true,$1)",
                        identity,
                    )
        except BaseException:
            locked = False  # Never query/unlock a proxy after termination.
            connection.terminate()
            raise
        finally:
            if locked:
                try:
                    released = await connection.fetchval(
                        "SELECT pg_advisory_unlock($1,$2)", *RECOVERY_KEY
                    )
                    if released is not True:
                        connection.terminate()
                        raise JournalError("unavailable")
                except BaseException:
                    connection.terminate()
                    raise


async def require_recovery_barrier(pool: asyncpg.Pool | None, settings: Settings) -> None:
    """All startup modes require positive DB evidence (owner-approved change)."""
    configured_journal(settings)  # Reject half/malformed settings even without DB.
    if pool is None:
        raise JournalError("database_required")
    try:
        async with asyncio.timeout(STARTUP_BUDGET_S):
            await _barrier(pool, settings)
    except JournalError:
        raise
    except (TimeoutError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
        raise JournalError("unavailable") from None
