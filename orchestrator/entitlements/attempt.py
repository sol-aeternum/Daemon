"""Private bounded connection owner for uncalled attempt financial APIs.

No caller transaction, retries, jobs, provisioning or dispatch. The service alone
calls commit after its complete financial body; normal context exit only releases.
Timeouts/termination are cooperative local bounds, never remote rollback proof.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import asyncpg.pool
import asyncpg.transaction

from orchestrator.attempt_lifecycle import AttemptIdentity, LifecycleError, assert_open
from orchestrator.entitlements.errors import (
    AttemptReservationUnavailable,
    EntitlementsError,
    ReservationCommitUncertain,
    ReservationCommittedResultUnavailable,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)

OPERATION_S = 2.0
ACQUIRE_S = 0.5
RELEASE_S = 0.5
ROLLBACK_S = 0.5
LOCK_MS = 250
STATEMENT_MS = 1000


class _AttemptTransaction:
    def __init__(
        self,
        pool: asyncpg.Pool,
        identity: AttemptIdentity | None,
        receipt: ReservationReceipt | None,
        acknowledged: bool,
    ) -> None:
        self.pool = pool
        self.identity = identity
        self.receipt = receipt
        self.recovery = receipt is not None
        # Acknowledgement of the ORIGINAL reservation, not of the read transaction.
        self.original_acknowledged = acknowledged
        self.deadline = asyncio.get_running_loop().time() + OPERATION_S
        self.conn: asyncpg.pool.PoolConnectionProxy | None = None
        self.tx: asyncpg.transaction.Transaction | None = None
        self.phase = "acquire"
        self.commit_attempted = False
        self.commit_acknowledged = False
        self.rollback_acknowledged = False
        self.termination_failed = False

    def remaining(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        # A driver which swallowed external cancellation must not yield success.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        return remaining

    async def open(self) -> None:
        if not isinstance(self.pool, asyncpg.Pool):
            raise AttemptReservationUnavailable("connection_state", identity=self.identity)
        self.conn = await self.pool.acquire(timeout=min(ACQUIRE_S, self.remaining()))
        self.remaining()
        if self.conn.is_in_transaction():
            raise AttemptReservationUnavailable("connection_state", identity=self.identity)
        self.tx = self.conn.transaction(isolation="read_committed")
        self.phase = "begin"
        assert self.tx is not None
        await self.tx.start()
        await self.conn.execute(
            f"SET LOCAL lock_timeout='{LOCK_MS}ms'; SET LOCAL statement_timeout='{STATEMENT_MS}ms'"
        )
        self.phase = "body"
        await self.guard()

    async def guard(self) -> None:
        self.remaining()
        if self.identity is not None:
            assert self.conn is not None
            await assert_open(self.conn, self.identity)
        self.remaining()

    async def commit(self) -> None:
        if self.tx is None or self.commit_attempted or self.receipt is None:
            raise AttemptReservationUnavailable("connection_state", identity=self.identity)
        await self.guard()
        self.phase = "commit"
        self.commit_attempted = True
        await self.tx.commit()
        # No await between acknowledgement and recording it.
        self.commit_acknowledged = True
        self.remaining()

    def terminate(self) -> None:
        if self.conn is None:
            return
        # asyncpg 0.31 invalidates the exact proxy on release. Never retain or
        # terminate its underlying physical connection: it may have a new lease.
        if type(self.conn) is asyncpg.pool.PoolConnectionProxy and self.conn._con is None:
            self.conn = None
            return
        try:
            self.conn.terminate()
        except Exception:
            self.termination_failed = True
        self.conn = None

    async def release(self) -> None:
        assert self.conn is not None
        timeout = min(RELEASE_S, self.remaining())
        self.phase = "release"
        # Relinquish ownership BEFORE entering the driver's shielded release.
        # Terminating this proxy while its old reset still runs can return the
        # holder early, letting that reset later terminate a NEW physical lease.
        # An interrupted/failed release is unresolved driver cleanup, never
        # permission to reclaim the proxy or evidence of remote rollback.
        conn = self.conn
        self.conn = None
        await self.pool.release(conn, timeout=timeout)
        self.remaining()

    async def cleanup(self, original: BaseException) -> None:
        if self.conn is None:
            return
        # Once COMMIT was attempted, rollback cannot settle its outcome. External
        # cancellation also takes the synchronous termination path; no new waiter.
        if self.commit_attempted or isinstance(original, asyncio.CancelledError):
            self.terminate()
            return
        try:
            if self.tx is not None:
                async with asyncio.timeout_at(
                    min(self.deadline, asyncio.get_running_loop().time() + ROLLBACK_S)
                ):
                    self.remaining()
                    await self.tx.rollback()
                    self.rollback_acknowledged = True
            async with asyncio.timeout_at(self.deadline):
                await self.release()
        except asyncio.CancelledError:
            # The first external cancellation wins over a prior refusal. A
            # primary cancellation already took the synchronous path above.
            self.terminate()
            raise
        except BaseException:
            # Cleanup errors/timeouts do not replace the original refusal.
            self.terminate()

    def error(self, exc: BaseException) -> BaseException:
        acknowledged = self.original_acknowledged if self.recovery else self.commit_acknowledged
        if isinstance(exc, asyncio.CancelledError):
            exc.reservation_identity = self.identity  # type: ignore[attr-defined]
            exc.reservation_receipt = self.receipt  # type: ignore[attr-defined]
            exc.reservation_commit_acknowledged = acknowledged  # type: ignore[attr-defined]
            exc.reservation_outcome = (  # type: ignore[attr-defined]
                "committed"
                if acknowledged
                else "uncertain"
                if self.commit_attempted or self.recovery
                else "not_committed"
            )
            exc.reservation_rollback_acknowledged = self.rollback_acknowledged  # type: ignore[attr-defined]
            exc.reservation_termination_failed = self.termination_failed  # type: ignore[attr-defined]
            return exc
        if not isinstance(exc, Exception):
            return exc
        if self.recovery:
            assert self.receipt is not None
            error: Exception = ReservationRecoveryUnresolved(self.receipt)
        elif self.commit_acknowledged:
            assert self.receipt is not None
            error = ReservationCommittedResultUnavailable(self.receipt)
        elif self.commit_attempted:
            assert self.receipt is not None
            error = ReservationCommitUncertain(self.receipt)
        elif isinstance(exc, LifecycleError):
            error = AttemptReservationUnavailable(
                "lifecycle_refused",
                identity=self.identity,
                sqlstate=exc.sqlstate,
                lifecycle_code=exc.code,
            )
        elif isinstance(exc, EntitlementsError):
            error = exc
        else:
            error = AttemptReservationUnavailable(
                "server_refused" if isinstance(exc, asyncpg.PostgresError) else "unavailable",
                identity=self.identity,
                sqlstate=getattr(exc, "sqlstate", None),
            )
        error.reservation_receipt = self.receipt  # type: ignore[attr-defined]
        error.commit_acknowledged = acknowledged  # type: ignore[attr-defined]
        error.rollback_acknowledged = self.rollback_acknowledged  # type: ignore[attr-defined]
        error.termination_failed = self.termination_failed  # type: ignore[attr-defined]
        return error


@asynccontextmanager
async def _attempt_transaction(
    pool: asyncpg.Pool,
    *,
    identity: AttemptIdentity | None = None,
    receipt: ReservationReceipt | None = None,
    acknowledged: bool = False,
) -> AsyncIterator[_AttemptTransaction]:
    state = _AttemptTransaction(pool, identity, receipt, acknowledged)
    try:
        async with asyncio.timeout_at(state.deadline):
            await state.open()
            yield state
            state.remaining()
            if not state.commit_acknowledged:
                raise AttemptReservationUnavailable("connection_state", identity=identity)
            await state.release()
    except BaseException as exc:
        try:
            await state.cleanup(exc)
        except asyncio.CancelledError as cancellation:
            raise state.error(cancellation) from None
        raise state.error(exc) from None
