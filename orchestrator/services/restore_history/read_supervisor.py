"""Dedicated read ownership; renewal never retries a request or unblocks a writer.

A fresh read may obtain a new coordinator only after the old read owner has
demonstrably drained and closed. Uncertain cleanup permanently blocks this
supervisor. The ordinary coordinator's monotonic writer rules are unchanged.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import asyncpg

from orchestrator.auth import AuthenticatedDevice
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks.store import TaskStore

from .coordination import HistoryCoordinator, HistoryOperation
from .effects_projection import HistoryProjection
from .model import MAX_RECORDS, HistoryError
from .observation import HistoricalObservation, HistoryObservation
from .projection import compare_projections
from .store import Ownership, _check_time

T = TypeVar("T")


async def qualify_read_history(
    operation: HistoryOperation,
    pool: asyncpg.Pool,
    projection: HistoryProjection,
    *,
    deadline: float,
) -> None:
    """Complete H/SQL checks on the actual W connection, never an effect permit."""
    state = await operation.verify()
    facts = []
    async with retain_pool_cleanup(), pool_connection(pool) as conn:
        if conn.is_in_transaction():
            raise HistoryError("reader_transaction_owner_refused")
        async with conn.transaction(isolation="read_committed", readonly=True):
            await projection._limits(conn, deadline)
            if (
                await conn.fetchval(
                    "SELECT current_schemas(true)=ARRAY['pg_catalog','public']::name[]"
                )
                is not True
            ):
                raise HistoryError("reader_namespace_refused")
            await projection.qualify(conn, writing=False, deadline=deadline)
            if await conn.fetchval("SELECT CURRENT_USER::text") != projection.worker:
                raise HistoryError("reader_principal_refused")
            cursor = 0
            while True:
                page = await projection.page(conn, after_seq=cursor, deadline=deadline)
                facts.extend(page)
                if len(facts) > MAX_RECORDS:
                    raise HistoryError("reader_history_unavailable")
                if len(page) < 32:
                    break
                cursor = page[-1].seq
    report = compare_projections(projection.expected.history_id, state, tuple(facts))
    if report.missing or report.extra or report.mismatched or report.restore_required:
        raise HistoryError("reader_reconciliation_required", uncertain=True)
    _check_time(deadline)


async def _observe(task: asyncio.Task[T], deadline: float, *, cancel_body: bool) -> T:
    """Drain an owned child; physical close is never cancelled or detached."""
    try:
        _check_time(deadline)
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            value = await asyncio.shield(task)
        _check_time(deadline)
        return value
    except BaseException as original:
        if cancel_body and not task.done():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            task.result()
        except BaseException as secondary:
            if secondary is not original:
                original.add_note("Read owner completion: " + type(secondary).__name__)
        raise


@dataclass(slots=True)
class _ReadOwner:
    history: HistoryCoordinator
    reader: HistoryObservation
    close_task: asyncio.Task[None] | None = None


class HistoryReadSupervisor:
    """One bounded read owner, independent of every mutation coordinator.

    Construction is inert. Startup explicitly opens and qualifies the first
    owner. Failed reads retire that owner once, without retrying the failed read.
    Only a later request may create and fully qualify another read-only owner.
    """

    def __init__(
        self,
        tasks: TaskStore,
        path: Path,
        projection: HistoryProjection,
        ownership: Ownership,
        key: bytes,
    ) -> None:
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks, self._path, self._projection = tasks, path, projection
        self._ownership, self._key = ownership, key
        self._gate = asyncio.Lock()
        self._started = self._closing = self._closed = self._blocked = False
        self._active_child: asyncio.Task[Any] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._owner: _ReadOwner | None = self._make_owner()

    def _make_owner(self) -> _ReadOwner:
        history = HistoryCoordinator(self._path, self._projection.expected, self._ownership)
        reader = HistoryObservation(self._tasks, history, self._projection, self._key)
        return _ReadOwner(history, reader)

    def _bind(self) -> None:
        if os.getpid() != self._pid:
            raise HistoryError("forked_reader")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise HistoryError("wrong_loop")

    @asynccontextmanager
    async def _admit(self, deadline: float) -> AsyncIterator[None]:
        self._bind()
        _check_time(deadline)
        if self._closing or self._closed or self._blocked:
            raise HistoryError("reader_closed")
        # No physical work belongs to a queued logical admission waiter.
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            await self._gate.acquire()
        original: BaseException | None = None
        try:
            _check_time(deadline)
            if self._closing or self._closed or self._blocked:
                raise HistoryError("reader_closed")
            yield
        except BaseException as error:
            original = error
            raise
        finally:
            try:
                self._gate.release()
            except BaseException as cleanup:
                self._blocked = True
                if original is None:
                    raise
                original.add_note("Read admission cleanup: " + type(cleanup).__name__)

    async def _prepare(self, deadline: float) -> HistoryObservation:
        if self._owner is None:
            self._owner = self._make_owner()
        owner = self._owner
        await owner.history.open(deadline=deadline)
        async with owner.history.operation(deadline=deadline) as operation:
            await qualify_read_history(
                operation, self._tasks._pool, self._projection, deadline=deadline
            )
        return owner.reader

    async def _retire(self, deadline: float) -> None:
        owner = self._owner
        if owner is None:
            return
        if owner.close_task is None:
            call = owner.history.aclose(deadline=deadline)
            try:
                owner.close_task = asyncio.create_task(call)
            except BaseException as original:
                self._blocked = True
                try:
                    call.close()
                except BaseException as cleanup:
                    original.add_note("Unstarted shutdown cleanup: " + type(cleanup).__name__)
                raise
        close_error: BaseException | None = None
        try:
            await _observe(owner.close_task, deadline, cancel_body=False)
        except BaseException as error:
            close_error = error
        # A logical close timeout/cancellation is not its physical outcome.
        # Inspect only the retained, settled shutdown task, never retry close or
        # infer completion from a boolean/descriptor number. This is cleanup
        # evidence for another READ, never mutation/ACK/dispatch authority.
        shutdown = owner.history._shutdown_task
        known_closed = (
            shutdown is not None
            and shutdown.done()
            and not shutdown.cancelled()
            and shutdown.exception() is None
            and owner.history._closed
            and owner.history._executor is None
        )
        if not known_closed:
            self._blocked = True
            raise HistoryError("reader_cleanup_uncertain", uncertain=True) from close_error
        self._owner = None

    async def _execute(self, call: Coroutine[Any, Any, T], deadline: float) -> T:
        try:
            child = asyncio.create_task(call)
        except BaseException as original:
            self._blocked = True
            try:
                call.close()
            except BaseException as cleanup:
                original.add_note("Unstarted read cleanup: " + type(cleanup).__name__)
            try:
                await self._retire(deadline)
            except BaseException as cleanup:
                original.add_note("Read retirement failed: " + type(cleanup).__name__)
            raise
        self._active_child = child
        try:
            return await _observe(child, deadline, cancel_body=True)
        except BaseException as original:
            try:
                await self._retire(deadline)
            except BaseException as cleanup:
                self._blocked = True
                original.add_note("Read retirement failed: " + type(cleanup).__name__)
            raise
        finally:
            self._active_child = None

    async def open(self, *, deadline: float) -> None:
        self._bind()
        if self._started:
            raise HistoryError("reader_already_started")
        self._started = True
        try:
            async with self._admit(deadline):
                await self._execute(self._prepare(deadline), deadline)
        except BaseException:
            # Startup is not a renewable observation request.
            self._blocked = True
            raise

    async def lookup(
        self, auth: AuthenticatedDevice, client_key: str, *, deadline: float
    ) -> HistoricalObservation:
        self._bind()
        if not self._started:
            raise HistoryError("reader_not_started")
        if type(auth) is not AuthenticatedDevice:
            raise HistoryError("observation_composition_refused")
        captured = AuthenticatedDevice(auth.user_id, auth.device_id, auth.session_id)
        async with self._admit(deadline):

            async def read() -> HistoricalObservation:
                reader = (
                    self._owner.reader if self._owner is not None else await self._prepare(deadline)
                )
                return await reader.lookup(captured, client_key, deadline=deadline)

            return await self._execute(read(), deadline)

    async def _shutdown(self, deadline: float) -> None:
        async with self._gate:
            try:
                await self._retire(deadline)
            finally:
                self._closed = True

    def _begin_shutdown(self) -> None:
        self._bind()
        self._closing = True

    async def aclose(self, *, deadline: float) -> None:
        self._bind()
        if asyncio.current_task() is self._active_child:
            raise HistoryError("reader_operation_active")
        self._begin_shutdown()
        if self._shutdown_task is None:
            call = self._shutdown(deadline)
            try:
                self._shutdown_task = asyncio.create_task(call)
            except BaseException as original:
                try:
                    call.close()
                except BaseException as cleanup:
                    original.add_note("Unstarted shutdown cleanup: " + type(cleanup).__name__)
                raise
        await _observe(self._shutdown_task, deadline, cancel_body=False)
