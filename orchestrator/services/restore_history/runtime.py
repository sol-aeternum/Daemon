"""Observation-only host ownership. No acceptance, send, Stop or output install.

The retained lifetime owner precedes native recovery qualification. A separate
read owner can renew only under the read-supervisor contract. No initializer,
default identity, key, grant, schema repair or execution permit is supplied.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
import time
from typing import TypeVar

import asyncpg

from orchestrator.config import Settings
from orchestrator.services.connection_cleanup import retain_pool_cleanup
from orchestrator.tasks.store import TaskStore

from .configuration import HistoryConfiguration, configured_history, load_material
from .coordination import HistoryCoordinator
from .effects_projection import HistoryProjection
from .model import HistoryError
from .read_supervisor import HistoryReadSupervisor, _observe, qualify_read_history
from .store import _check_time

T = TypeVar("T")


async def physical_completion(call: Awaitable[T], *, deadline: float) -> T:
    """Never cancel an owned physical initializer/close and lose its outcome.

    A logical failure still propagates after draining, even for a late success.
    This is not a wall-clock bound on kernel cleanup.
    """
    try:
        future = asyncio.ensure_future(call)
    except BaseException as original:
        try:
            if inspect.iscoroutine(call):
                call.close()
        except BaseException as cleanup:
            original.add_note("Unstarted physical cleanup: " + type(cleanup).__name__)
        raise
    try:
        _check_time(deadline)
        async with asyncio.timeout_at(deadline):
            value = await asyncio.shield(future)
        _check_time(deadline)
        return value
    except BaseException as original:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            future.result()
        except BaseException as cleanup:
            if cleanup is not original:
                original.add_note("Physical completion: " + type(cleanup).__name__)
        raise


async def require_unselected_history(pool: asyncpg.Pool) -> None:
    """Positive catalog absence, before any native recovery write.

    Partial H schema cannot silently become a legacy host by dropping settings.
    """
    if (
        await pool.fetchval("""SELECT
        NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND left(c.relname,13)='task_history_')
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND left(p.proname,13)='task_history_')""")
        is not True
    ):
        raise HistoryError("history_configuration_required")


class HistoryRuntime:
    """Backend observation installation; mutating owners remain uninstalled."""

    def __init__(self, configuration: HistoryConfiguration, settings: Settings) -> None:
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.configuration = configuration
        self.settings = settings
        self.history: HistoryCoordinator | None = None
        self.projection: HistoryProjection | None = None
        self.reader: HistoryReadSupervisor | None = None
        self._key: bytes | None = None
        self._started = self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._reader_close_task: asyncio.Task[None] | None = None

    def _bind(self) -> None:
        if self._pid != os.getpid():
            raise HistoryError("forked_history_host")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise HistoryError("wrong_loop")

    async def open(self, *, deadline: float, service: str = "http") -> None:
        self._bind()
        if self._started or service not in ("http", "worker"):
            raise HistoryError("history_startup_refused")
        self._started = True
        # Capture the physical worker task before awaiting it. Caller cancellation
        # must not detach a key read while its descriptors are still owned.
        material = await physical_completion(
            asyncio.to_thread(load_material, self.configuration, service, deadline=deadline),
            deadline=deadline,
        )
        acceptor, worker = (
            self.settings.daemon_task_acceptor_role,
            self.settings.daemon_task_worker_role,
        )
        if not isinstance(acceptor, str) or not isinstance(worker, str):
            raise HistoryError("history_configuration_required")
        self.projection = HistoryProjection(
            material.expected,
            acceptor=acceptor,
            worker=worker,
        )
        self._key = material.key
        self.history = HistoryCoordinator(
            self.configuration.directory, material.expected, self.configuration.ownership
        )
        await self.history.open(deadline=deadline)

    @asynccontextmanager
    async def qualify_before_recovery(
        self, pool: asyncpg.Pool, *, deadline: float
    ) -> AsyncIterator[None]:
        self._bind()
        if self.history is None or self.projection is None or self._closing:
            raise HistoryError("history_startup_refused")
        async with self.history.operation(deadline=deadline) as operation:
            await qualify_read_history(operation, pool, self.projection, deadline=deadline)
            # Existing paired qualification keeps its native current-connection,
            # principal, journal and barrier checks. Actual resets finish inside H.
            async with retain_pool_cleanup(), asyncio.timeout_at(deadline):
                yield
            _check_time(deadline)

    async def install_reader(self, tasks: TaskStore, *, deadline: float) -> None:
        self._bind()
        if self.projection is None or self._key is None or self.reader is not None:
            raise HistoryError("history_startup_refused")
        self.reader = HistoryReadSupervisor(
            tasks,
            self.configuration.directory,
            self.projection,
            self.configuration.ownership,
            self._key,
        )
        await self.reader.open(deadline=deadline)

    def ready(self, state: object) -> bool:
        self._bind()
        return (
            not self._closing
            and getattr(state, "settings", None) is self.settings
            and self.reader is not None
            and self.reader is getattr(state, "history_observation", None)
            and self.reader._tasks._pool is getattr(state, "db_pool", None)
            and self.reader._tasks._memory is getattr(state, "memory_store", None)
            and self.configuration == configured_history(self.settings, "http")
            and self.history is not None
            and not self.history._blocked
            and not self.history._closed
            and self.history._store is not None
        )

    async def _close(self, deadline: float) -> None:
        first: BaseException | None = None
        for call in (
            self.drain_reader(deadline=deadline),
            *([self.history.aclose(deadline=deadline)] if self.history is not None else []),
        ):
            try:
                await call
            except BaseException as error:
                if first is None:
                    first = error
                else:
                    first.add_note("History shutdown: " + type(error).__name__)
        self._key = None
        if first is not None:
            raise first

    async def drain_reader(self, *, deadline: float) -> None:
        """Stop read admission before host-owned SQL pools are closed.

        The main H lifetime remains held until the host subsequently closes it.
        Joining the same retained reader shutdown is not another close attempt.
        """
        self._begin_shutdown()
        if self.reader is not None:
            if self._reader_close_task is None:
                call = self.reader.aclose(deadline=deadline)
                try:
                    self._reader_close_task = asyncio.create_task(call)
                except BaseException as original:
                    try:
                        call.close()
                    except BaseException as cleanup:
                        original.add_note("Unstarted shutdown cleanup: " + type(cleanup).__name__)
                    raise
            await _observe(self._reader_close_task, deadline, cancel_body=False)

    def _begin_shutdown(self) -> None:
        """Synchronous admission fence, even if cleanup cannot be scheduled."""
        self._bind()
        self._closing = True
        if self.reader is not None:
            self.reader._begin_shutdown()

    async def aclose(self, *, deadline: float) -> None:
        self._begin_shutdown()
        if self._close_task is None:
            call = self._close(deadline)
            try:
                self._close_task = asyncio.create_task(call)
            except BaseException as original:
                try:
                    call.close()
                except BaseException as cleanup:
                    original.add_note("Unstarted shutdown cleanup: " + type(cleanup).__name__)
                raise
        await _observe(self._close_task, deadline, cancel_body=False)


def startup_deadline() -> float:
    return time.monotonic() + 10.0
