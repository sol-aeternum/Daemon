"""Uncalled H1 lock/async ownership boundary; no native transaction integration.

Constructor is inert. Explicit open/operation/close own one serial filesystem
worker. Logical failure drains physical work before propagating; kernel cleanup
has no promised upper bound. History and operation results are never permits.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, asynccontextmanager
from pathlib import Path
from typing import TypeVar

from .model import Command, HistoryError, HistoryState, Identity
from .store import AppendResult, HistoryStore, Ownership, _check_time

T = TypeVar("T")


class HistoryOperation:
    """Task-affine, finite scope; no DB connection, identity generation or retry."""

    def __init__(self, coordinator: HistoryCoordinator, deadline: float) -> None:
        self._coordinator = coordinator
        self._task = asyncio.current_task()
        self._deadline = deadline
        self._active = True

    def _check(self) -> None:
        owner = self._coordinator
        owner._bind()
        if (
            not self._active
            or owner._operation is not self
            or asyncio.current_task() is not self._task
        ):
            owner._blocked = True
            raise HistoryError("invalid_operation")
        if owner._blocked:
            raise HistoryError("coordinator_blocked")

    async def verify(self) -> HistoryState:
        self._check()
        return await self._coordinator._work(
            lambda: self._coordinator._store_required().verify(deadline=self._deadline),
            self._deadline,
        )

    async def append(self, command: Command) -> AppendResult:
        self._check()
        return await self._coordinator._work(
            lambda: self._coordinator._store_required().append(command, deadline=self._deadline),
            self._deadline,
        )


class HistoryCoordinator:
    """One process/loop-owned supervisor, explicitly opened and explicitly closed.

    RUNTIME(shared) -> OP(exclusive) -> future native guards -> APPEND(short).
    Required OP ownership precedes any native transaction; this module neither
    observes nor opens one. No provider/transport call belongs inside the scope.
    A failed invocation blocks this object permanently; close is the only exit.
    """

    def __init__(self, path: Path, expected: Identity, ownership: Ownership) -> None:
        self._pid = os.getpid()
        self._path, self._expected, self._ownership = path, expected, ownership
        self._loop: asyncio.AbstractEventLoop | None = None
        self._gate = asyncio.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._store: HistoryStore | None = None
        self._context: AbstractContextManager[None] | None = None
        self._operation: HistoryOperation | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._started = self._closing = self._closed = self._blocked = False

    def _bind(self) -> None:
        # Never touch inherited executor/mutex/flock state after a fork.
        if os.getpid() != self._pid:
            raise HistoryError("forked_coordinator")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise HistoryError("wrong_loop")

    def _usable(self) -> None:
        if self._blocked:
            raise HistoryError("coordinator_blocked")
        if not self._started or self._closing or self._closed:
            raise HistoryError("coordinator_closed")

    def _store_required(self) -> HistoryStore:
        if self._store is None:
            raise HistoryError("coordinator_closed")
        return self._store

    async def _observe(self, future: asyncio.Future[T], deadline: float | None) -> T:
        try:
            if deadline is not None:
                _check_time(deadline)
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            async with asyncio.timeout(remaining):
                result = await asyncio.shield(future)
            if deadline is not None:
                _check_time(deadline)
            return result
        except BaseException as original:
            self._blocked = True
            # Do not cancel the physical owner, release locks, close descriptors
            # or let repeated caller cancellation detach it. Retain the first
            # exception, including CancelledError, until physical completion.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                future.result()
            except BaseException as secondary:
                if secondary is not original:
                    original.add_note(
                        "History owner completion failed: " + type(secondary).__name__
                    )
            original.add_note("History invocation blocked; no retry or late-result promotion")
            raise

    async def _work(self, call: Callable[[], T], deadline: float | None) -> T:
        try:
            if deadline is not None:
                _check_time(deadline)
            if self._executor is None:
                raise HistoryError("coordinator_closed")
            future = asyncio.get_running_loop().run_in_executor(self._executor, call)
            return await self._observe(future, deadline)
        except BaseException:
            self._blocked = True
            raise

    async def open(self, *, deadline: float) -> None:
        self._bind()
        if self._started or self._closing or self._closed or self._blocked:
            raise HistoryError("coordinator_closed")
        _check_time(deadline)
        self._started = True
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h1-owner")

        def initialize() -> None:
            # Retain ownership even if the completed result cannot be delivered.
            self._store = HistoryStore(
                self._path, self._expected, self._ownership, deadline=deadline
            )

        try:
            await self._work(initialize, deadline)
            self._usable()
        except BaseException as original:
            try:
                await self.aclose(deadline=deadline)
            except BaseException:
                original.add_note("History open cleanup failed or exceeded its logical deadline")
            raise

    def _enter(self, deadline: float) -> None:
        context = self._store_required().operation(deadline=deadline)
        context.__enter__()
        self._context = context

    def _exit(self, original: BaseException | None) -> None:
        context, self._context = self._context, None
        if context is not None:
            context.__exit__(
                type(original) if original is not None else None,
                original,
                original.__traceback__ if original is not None else None,
            )

    @asynccontextmanager
    async def operation(self, *, deadline: float) -> AsyncIterator[HistoryOperation]:
        self._bind()
        self._usable()
        if self._operation is not None and self._operation._task is asyncio.current_task():
            raise HistoryError("operation_active")
        _check_time(deadline)
        waiting = asyncio.create_task(self._gate.acquire())
        entered = False
        operation = None
        original: BaseException | None = None
        try:
            await self._observe(waiting, deadline)
            self._usable()
            entered = True
            await self._work(lambda: self._enter(deadline), deadline)
            operation = HistoryOperation(self, deadline)
            self._operation = operation
            yield operation
            if self._blocked:
                raise HistoryError("coordinator_blocked")
        except BaseException as exc:
            original = exc
            self._blocked = True
            raise
        finally:
            if operation is not None:
                operation._active = False
                self._operation = None
            try:
                if entered:
                    # Expired admission does not skip ownership cleanup.
                    await self._work(lambda: self._exit(original), None)
            except BaseException:
                self._blocked = True
                if original is not None:
                    original.add_note("History operation cleanup uncertain")
                else:
                    raise
            finally:
                if waiting.done() and not waiting.cancelled() and waiting.result():
                    self._gate.release()

    async def _shutdown(self) -> None:
        async with self._gate:
            try:
                if self._executor is not None:
                    # Initialization may still be running. Inspect/close only
                    # on its serial worker after it has published ownership.
                    def close_store() -> None:
                        if self._store is not None:
                            self._store.close()

                    await self._work(close_store, None)
            finally:
                try:
                    if self._executor is not None:
                        # Join off-loop; observe retains this cleanup owner too.
                        join = asyncio.create_task(
                            asyncio.to_thread(self._executor.shutdown, wait=True)
                        )
                        await self._observe(join, None)
                        self._executor = None
                finally:
                    self._closed = True

    async def aclose(self, *, deadline: float) -> None:
        self._bind()
        if self._operation is not None and self._operation._task is asyncio.current_task():
            raise HistoryError("operation_active")
        self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        await self._observe(self._shutdown_task, deadline)
