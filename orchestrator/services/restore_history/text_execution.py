"""Explicit text pilot composition; original worker jobs, no queue or startup selector."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from orchestrator.config import Settings
from orchestrator.entitlements.models import ReservationStatus, Settlement
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks.composition import WorkerComposition, qualify_worker_composition
from orchestrator.tasks.runner import AttemptState, run_chat_task
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks._history_binding import ComputeHistoryBinding
from orchestrator.services.search_pacing import SearchPacerLike
from orchestrator.tools.web_search import BRAVE_FIXED_MICROUSD, WebSearchTool

from .coordination import HistoryCoordinator
from .effects import HistoryEffects
from .model import HistoryError
from .read_supervisor import _observe, qualify_read_history
from .store import _check_time
from .terminal_projection import (
    TERMINAL_PROFILES,
    InboxOwnedIndexHistoryProjection,
    InboxResearchHistoryProjection,
    InboxSourcesHistoryProjection,
    TerminalHistoryProjection,
)


@dataclass(frozen=True, slots=True)
class OriginalCleanup:
    binding: ComputeHistoryBinding
    send_record: UUID
    settlement: Settlement


def _successful(owner: asyncio.Task[Any] | None) -> bool:
    return (
        owner is not None and owner.done() and not owner.cancelled() and owner.exception() is None
    )


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
            or pool.get_max_size() < 2
            or type(history) is not HistoryCoordinator
            or type(projection) not in TERMINAL_PROFILES
            or projection.profile != TERMINAL_PROFILES.get(type(projection))
            or history.profile != projection.profile
            or history._expected != projection.expected
            or (type(projection) is InboxResearchHistoryProjection)
            != (type(self) is HistoryResearchExecution)
            or (type(projection) is InboxSourcesHistoryProjection)
            != (type(self) is HistorySourcesExecution)
            or (type(projection) is InboxOwnedIndexHistoryProjection)
            != (type(self) is HistoryOwnedIndexExecution)
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
        self._original_attempts: dict[asyncio.Task[Any], AttemptState] = {}
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
            # Stop retains auth ownership while acquiring a separate native
            # transaction. Capacity is a prerequisite, not an availability promise.
            if self.pool.get_max_size() < 2:
                raise HistoryError("text_execution_refused")
            await self.history.open(deadline=deadline)
            async with self.history.operation(deadline=deadline) as operation:
                await qualify_read_history(operation, self.pool, self.projection, deadline=deadline)
                if (
                    type(self.projection) is InboxOwnedIndexHistoryProjection
                    and (await operation.verify()).entries
                ):
                    raise HistoryError("owned_index_original_exists")
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

    def retain_original_attempt(self, state: AttemptState) -> None:
        child = asyncio.current_task()
        if (
            self.projection.profile
            not in ("inbox-retired-v1", "inbox-research-v1", "inbox-sources-v1")
            or child is None
            or self._job_tasks.get(child) != state.claim.task_id
            or child in self._original_attempts
        ):
            raise HistoryError("original_attempt_refused")
        self._original_attempts[child] = state

    def research_cleanup_confirmed(self, task: UUID) -> bool:
        """Same-host proof only; no recovery or settlement from saved rows."""
        if self.projection.profile not in ("inbox-research-v1", "inbox-sources-v1"):
            return False
        child = next((j for j, ident in self._job_tasks.items() if ident == task), None)
        if child is None or not _successful(child) or child.result() != "completed":
            return False
        state = self._original_attempts.get(child)
        if state is None or state.compute_scope is None or state.history_binding is None:
            return False
        scope, binding = state.compute_scope, state.history_binding
        if self.projection.profile == "inbox-sources-v1":
            source = binding.source_run
            if (
                source is None
                or source.claim is not state.claim
                or source.effects is not self.effects
                or source.pending is not None
                or not source.transport_entered
                or not source.synthesis_checked
                or source.report is None
                or not 1 <= len(source.pins) == len(source.selected) <= 2
                or (
                    type(self.projection) is InboxOwnedIndexHistoryProjection
                    and len(source.pins) != 2
                )
            ):
                return False
        beat = state.heartbeat
        if not (
            state.result is TaskStatus.COMPLETED
            and state.own_completion_acknowledged
            and not state.stopping
            and not state.interrupted
            and state.error is None
            and state.requested_terminal is None
            and _successful(state.execution)
            and beat is not None
            and (_successful(beat) or (beat.cancelled() and state.heartbeat_stop_requested))
            and not binding.state.call_failed
            and binding.state.uncertainty is None
            and binding.effects is self.effects
            and binding.composition is self.worker
            and any(shared is binding.state for shared in self._invocations)
            and scope.history_binding is binding
            and scope.inference_identity is binding.identity
            and scope.closed
            and not scope.outstanding
            and not scope.streams
            and len(scope.settled) == (4 if self.projection.profile == "inbox-sources-v1" else 3)
            and all(_successful(owner) for owner in scope.cleanup_owners)
            and scope.retirement_holds is not None
            and len(scope.retirement_holds) == len(scope.settled)
        ):
            return False
        assert self.effects is not None
        owned = self.effects._owned_sends.get(id(binding.state))
        if owned is None or owned[0] is not binding.state or len(owned[1]) != len(scope.settled):
            return False
        for index, hold in enumerate(scope.retirement_holds):
            if not _successful(hold.settlement) or hold.settlement not in scope.cleanup_owners:
                return False
            assert hold.settlement is not None
            result = hold.settlement.result()
            if (
                type(result) is not Settlement
                or not result.applied
                or result.status is not ReservationStatus.SETTLED
                or result.overage_microusd
                or result.reservation.id != hold.reservation.id
                or result.reservation.user_id != binding.identity.account_id
                or result.period_key != hold.reservation.period_key
                or scope.settled.get(result.reservation.id) != result.actual_microusd
            ):
                return False
            stream = hold.stream
            if index == 1:
                owned_index = type(self.projection) is InboxOwnedIndexHistoryProjection
                if (
                    stream is not None
                    or result.actual_microusd != (0 if owned_index else BRAVE_FIXED_MICROUSD)
                    or (owned_index and hold.reservation.reserved_microusd != 0)
                ):
                    return False
            elif (
                stream is None
                or stream.invocation is not binding.state
                or stream.state != "eof"
                or stream.cleanup_blocked
                or stream.cleanup_outcome != "close_returned"
                or not _successful(stream.closer)
                or not _successful(stream.closing)
                or stream.reading is None
                or not stream.reading.done()
                or stream.reading.cancelled()
                or not isinstance(stream.reading.exception(), StopAsyncIteration)
            ):
                return False
        return True

    def original_cleanup(self, task: UUID) -> OriginalCleanup:
        """Inspect the original physical owners. Never initiate or finish cleanup here."""
        self._bind()
        if (
            self.worker is None
            or self.effects is None
            or self.projection.profile != "inbox-retired-v1"
            or self._scheduled != {task}
            or len(self._original_attempts) != 1
            or self._outcomes != {task: "completed"}
        ):
            raise HistoryError("original_cleanup_unavailable")
        self.validate_worker(self.context(), self.worker)
        ((child, state),) = self._original_attempts.items()
        binding = state.history_binding
        scope = state.compute_scope
        beat = state.heartbeat
        if (
            self._jobs != {child}
            or self._job_tasks != {child: task}
            or not _successful(child)
            or child.result() != "completed"
            or not _successful(state.execution)
            or beat is None
            or not (_successful(beat) or (beat.cancelled() and state.heartbeat_stop_requested))
            or state.claim.task_id != task
            or state.result is not TaskStatus.COMPLETED
            or not state.own_completion_acknowledged
            or state.stopping
            or state.interrupted
            or state.error is not None
            or state.requested_terminal is not None
            or binding is None
            or binding.effects is not self.effects
            or binding.composition is not self.worker
            or len(self._invocations) != 1
            or binding.state is not self._invocations[0]
            or binding.state.call_failed
            or binding.state.uncertainty is not None
            or scope is None
            or scope.history_binding is not binding
            or scope.inference_identity is not binding.identity
            or not scope.closed
            or scope.outstanding
            or scope.streams
            or not all(_successful(owner) for owner in scope.cleanup_owners)
            or len(scope.retirement_holds or ()) != 1
        ):
            raise HistoryError("original_cleanup_unavailable")
        (hold,) = scope.retirement_holds
        stream = hold.stream
        if (
            hold.settlement not in scope.cleanup_owners
            or not _successful(hold.settlement)
            or stream is None
            or stream.invocation is not binding.state
            or stream.state != "eof"
            or stream.cleanup_blocked
            or stream.cleanup_outcome != "close_returned"
            or not _successful(stream.closer)
            or not _successful(stream.closing)
            or stream.reading is None
            or not stream.reading.done()
            or stream.reading.cancelled()
            or not isinstance(stream.reading.exception(), StopAsyncIteration)
        ):
            raise HistoryError("original_cleanup_unavailable")
        result = hold.settlement.result()
        owned = self.effects._owned_sends.get(id(binding.state))
        if (
            type(result) is not Settlement
            or not result.applied
            or result.status is not ReservationStatus.SETTLED
            or result.overage_microusd
            or result.reservation.id != hold.reservation.id
            or result.reservation.user_id != binding.identity.account_id
            or result.period_key != hold.reservation.period_key
            or scope.settled != {result.reservation.id: result.actual_microusd}
            or owned is None
            or owned[0] is not binding.state
            or len(owned[1]) != 1
        ):
            raise HistoryError("original_cleanup_unavailable")
        return OriginalCleanup(binding, next(iter(owned[1])), result)

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
        if type(self.projection) is InboxOwnedIndexHistoryProjection and self._scheduled:
            raise HistoryError("owned_index_original_exists")
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


class HistoryResearchExecution(HistoryTextExecution):
    """Only the explicit disposable host can supply the native search adapter."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        encryption: ContentEncryption,
        settings: Settings,
        history: HistoryCoordinator,
        projection: InboxResearchHistoryProjection | InboxSourcesHistoryProjection,
        *,
        model_transport: Callable[..., Awaitable[Any]],
        search_transport: httpx.MockTransport,
        search_pacer: SearchPacerLike,
    ) -> None:
        if (
            type(search_transport) is not httpx.MockTransport
            or search_pacer is None
            or not inspect.iscoroutinefunction(model_transport)
            or type(projection)
            not in (InboxResearchHistoryProjection, InboxSourcesHistoryProjection)
            or settings.mock_llm
        ):
            raise HistoryError("research_execution_refused")
        super().__init__(pool, encryption, settings, history, projection)
        self.model_transport = model_transport
        self.search_tool = WebSearchTool(
            "synthetic-inbox-search",
            provider="brave",
            _transport=search_transport,
            pacer=search_pacer,
        )


class HistorySourcesExecution(HistoryResearchExecution):
    """Explicit source profile, with no live fetch transport or startup selector."""

    def __init__(
        self,
        pool,
        encryption,
        settings,
        history,
        projection,
        *,
        model_transport,
        search_transport,
        search_pacer,
        source_transport,
        source_key,
    ):
        if (
            type(projection) is not InboxSourcesHistoryProjection
            or type(source_transport) is not httpx.MockTransport
        ):
            raise HistoryError("source_execution_refused")
        projection.expected.verify_key(source_key)
        from .source_execution import source_key_material

        super().__init__(
            pool,
            encryption,
            settings,
            history,
            projection,
            model_transport=model_transport,
            search_transport=search_transport,
            search_pacer=search_pacer,
        )
        self.source_transport = source_transport
        self.source_key = source_key_material(source_key, projection.expected.history_id)

    def original_source(self, claim):
        from .source_execution import SourceRun

        assert self.effects is not None
        return SourceRun(claim, self.effects, self.source_key, self.source_transport, self.settings)


class HistoryOwnedIndexExecution(HistoryTextExecution):
    """Explicit one-original composition over the existing v6 native source runner."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        encryption: ContentEncryption,
        settings: Settings,
        history: HistoryCoordinator,
        projection: InboxOwnedIndexHistoryProjection,
        *,
        model_transport: Callable[..., Awaitable[Any]],
        captures: tuple[bytes, bytes],
        source_key: bytes,
    ) -> None:
        from .owned_index import OwnedIndexCorpus, OwnedIndexSearchTool
        from .source_execution import source_key_material

        if (
            type(projection) is not InboxOwnedIndexHistoryProjection
            or not inspect.iscoroutinefunction(model_transport)
            or settings.mock_llm
        ):
            raise HistoryError("owned_index_execution_refused")
        projection.expected.verify_key(source_key)
        super().__init__(pool, encryption, settings, history, projection)
        corpus = OwnedIndexCorpus(captures)
        self.model_transport = model_transport
        self.search_tool = OwnedIndexSearchTool(projection)
        self.source_transport = httpx.MockTransport(corpus.respond)
        self.source_key = source_key_material(
            source_key, projection.expected.history_id, owned_index=True
        )

    def original_source(self, claim):
        from .source_execution import SourceRun

        assert self.effects is not None
        return SourceRun(claim, self.effects, self.source_key, self.source_transport, self.settings)
