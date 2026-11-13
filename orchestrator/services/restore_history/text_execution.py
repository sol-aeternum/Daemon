"""Explicit text pilot composition; original worker jobs, no queue or startup selector."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import asyncpg

from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks.composition import WorkerComposition, qualify_worker_composition
from orchestrator.tasks.runner import run_chat_task
from orchestrator.tasks._invocation import InferenceInvocation

from .coordination import HistoryCoordinator
from .effects import HistoryEffects
from .model import HistoryError
from .read_supervisor import _observe, qualify_read_history
from .store import _check_time
from .terminal_projection import TerminalHistoryProjection


class HistoryTextExecution:
    """Borrows W only. Constructor is inert; open qualifies before native recovery."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        encryption: ContentEncryption,
        settings: Settings,
        history: HistoryCoordinator,
        projection: TerminalHistoryProjection,
    ):
        if (
            type(pool) is not asyncpg.Pool
            or type(history) is not HistoryCoordinator
            or type(projection) is not TerminalHistoryProjection
            or history.profile != "terminal-v3"
            or history._expected != projection.expected
        ):
            raise HistoryError("text_execution_refused")
        self.pool, self.encryption, self.settings = pool, encryption, settings
        self.history, self.projection = history, projection
        self.worker: WorkerComposition | None = None
        self.effects: HistoryEffects | None = None
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = self._started = self._closing = self._closed = self._blocked = False
        self._jobs: set[asyncio.Task[Any]] = set()
        self._invocations: list[InferenceInvocation] = []
        self._scheduled: set[Any] = set()
        self._outcomes: dict[Any, str] = {}
        self._job_tasks: dict[asyncio.Task[Any], Any] = {}
        self._shutdown_task: asyncio.Task[None] | None = None

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if os.getpid() != self._pid or (self._loop is not None and self._loop is not loop):
            raise HistoryError("text_execution_owner_refused")
        self._loop = loop

    async def open(self, *, deadline: float) -> None:
        self._bind()
        if self._started:
            raise HistoryError("text_execution_already_started")
        self._started = True
        try:
            await self.history.open(deadline=deadline)
            async with self.history.operation(deadline=deadline) as operation:
                await qualify_read_history(operation, self.pool, self.projection, deadline=deadline)
                async with retain_pool_cleanup():
                    async with pool_connection(self.pool) as conn:
                        async with conn.transaction(isolation="read_committed"):
                            # This is W qualification, never acceptor admission.
                            await self.projection.qualify(conn, writing=False, deadline=deadline)
                            # Positive ordinary reads precede J1/native worker qualification.
                            await conn.fetch("SELECT id,settings FROM users LIMIT 0")
                            await conn.fetch("SELECT id FROM messages LIMIT 0")
                    worker = await qualify_worker_composition(
                        enabled=True,
                        worker_pool=self.pool,
                        encryption=self.encryption,
                        settings=self.settings,
                        acceptor_role=self.projection.acceptor,
                        worker_role=self.projection.worker,
                        requires_history=True,
                    )
                if worker is None:
                    raise HistoryError("text_execution_refused")
                self.worker = worker
                self.effects = HistoryEffects(worker.tasks, self.history, self.projection)
                _check_time(deadline)
            self._ready = True
        except BaseException as original:
            self.block()
            try:
                await self.aclose(deadline=deadline)
            except BaseException as cleanup:
                original.add_note("Text qualification cleanup: " + type(cleanup).__name__)
            raise

    def validate_worker(self, ctx: dict[str, Any], worker: WorkerComposition) -> None:
        self._bind()
        if (
            not self._ready
            or self._closing
            or self._closed
            or self._blocked
            or worker is not self.worker
            or not worker.requires_history
            or type(self.effects) is not HistoryEffects
            or self.effects._tasks is not worker.tasks
            or self.effects._history is not self.history
            or self.effects._projection is not self.projection
            or self.effects._blocked
            or self.history._blocked
            or ctx.get("history_text_execution") is not self
            or ctx.get("redis") is not None
        ):
            raise HistoryError("text_execution_unavailable")
        self.effects._check()

    def context(self) -> dict[str, Any]:
        worker = self.worker
        if worker is None:
            raise HistoryError("text_execution_unavailable")
        return dict(
            task_store=worker.tasks,
            store=worker.memory,
            db_pool=self.pool,
            settings=self.settings,
            task_composition=worker,
            history_text_execution=self,
            redis=None,
        )

    def track_invocation(self, shared: InferenceInvocation) -> None:
        # Strong identity references; no clearing of failed/uncertain invocation state.
        if not any(value is shared for value in self._invocations):
            self._invocations.append(shared)

    def block(self) -> None:
        self._blocked = True
        self.history._blocked = True
        if self.effects is not None:
            self.effects._blocked = True
        for shared in self._invocations:
            shared.block("history_execution_unavailable")

    def schedule(self, task: Any) -> None:
        ctx = self.context()
        assert self.worker is not None
        self.validate_worker(ctx, self.worker)
        if task in self._scheduled:
            raise HistoryError("text_execution_duplicate")
        self._scheduled.add(task)
        call = run_chat_task(ctx, str(task))
        try:
            child = asyncio.create_task(call)
        except BaseException:
            call.close()
            self.block()
            raise
        self._jobs.add(child)
        self._job_tasks[child] = task
        child.add_done_callback(self._finished)

    def _finished(self, child: asyncio.Task[Any]) -> None:
        task = self._job_tasks[child]
        if child.cancelled():
            self._outcomes[task] = "cancelled_owner"
            self.block()
        elif child.exception() is not None:
            self._outcomes[task] = "failed_owner"
            self.block()
        else:
            outcome = child.result()
            self._outcomes[task] = outcome
            if outcome not in {
                "completed",
                "cancelled",
                "failed",
                "needs_attention",
                "queued",
                "skipped",
            }:
                self.block()
        # Retain every original job and its receipt through explicit shutdown.

    async def aclose(self, *, deadline: float) -> None:
        self._bind()
        self._closing = True
        self._ready = False
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._close_jobs())
        await _observe(self._shutdown_task, deadline, cancel_body=False)

    async def _close_jobs(self) -> None:
        children = tuple(self._jobs)
        for child in children:
            if not child.done():
                child.cancel()
        first: BaseException | None = None
        for child in children:
            try:
                while not child.done():
                    try:
                        await asyncio.shield(child)
                    except asyncio.CancelledError:
                        continue
                if not child.cancelled():
                    child.result()
            except asyncio.CancelledError:
                pass
            except BaseException as error:
                first = first or error
        # All producers are physically stopped before coordinator close starts.
        await self.history.aclose(deadline=time.monotonic() + 2)
        self._closed = True
        if first is not None:
            raise first
