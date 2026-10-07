"""Worker execution of durable chat tasks (DURABLE_REQUEST_DESIGN §4–§5, §8–§10).

One arq job runs one attempt: claim the task under a fresh lease epoch, keep
the lease alive with a heartbeat that also carries cancellation, stream the
turn through the existing chat engine inside the account compute scope, and
route every write through the lease fence. Live token deltas are published
over Redis tagged with ``(content_generation, delta_seq)``; they are an
optimisation for attached observers, never the record.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from typing import Any

from orchestrator.compute_runtime import (
    RETRYABLE_COMPUTE_CODES,
    account_compute,
    compute_error,
    current_scope,
)
from orchestrator.tasks.fence import guard_registry
from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.prompts import DAEMON_PROMPT_VERSION
from orchestrator.tasks.store import Claim, ExecutionRefused, LeaseLost, TaskStore

logger = logging.getLogger(__name__)

#: Lease length and heartbeat cadence (database clock; §5).
LEASE_S = 45.0
HEARTBEAT_S = 10.0
#: Margin kept between the local lease estimate and the database's lease.
LEASE_SAFETY_S = 5.0
#: How long a cancelled or suspended attempt may take to stop at its next
#: frame before its execution is interrupted (a provider can stall between
#: frames, and Stop must not wait for it).
STOP_GRACE_S = 2.0
#: Delay before retrying a claim whose admission precondition failed.
DEFER_S = 15.0
#: Account admission refusals that clear on their own (a busy slot, a full
#: rate window). Refused before any provider work, they defer the task
#: without consuming an attempt.
ADMISSION_CODES = frozenset({"concurrency_exceeded", "rate_limited"})
#: How long after acceptance a task may keep waiting for admission.
ADMISSION_WAIT_S = 900.0
#: Upper bound for one whole attempt (all tool rounds), enforced by arq.
ATTEMPT_TIMEOUT_S = 600
#: Budget-period refusals: terminal in slice 1 (DEC09 pause arrives in slice 4).
CAPACITY_CODES = frozenset(
    {
        "budget_exceeded",
        "extended_budget_exceeded",
        "extended_agents_exceeded",
        "trial_exhausted",
        "trial_extended_agents_exhausted",
    }
)
#: Frames forwarded to attached observers as-is (never persisted).
_PASSTHROUGH_FRAMES = frozenset({"routing", "thinking", "tool_call", "tool_result", "metadata"})
#: Message status the result row carries for each status ``complete`` commits.
_COMMITTED_MESSAGE_STATUS = {
    TaskStatus.COMPLETED: "complete",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.FAILED: "error",
}
#: Message states that never feed back into a model's history.
_HISTORY_EXCLUDED = ["streaming", "error", "cancelled"]

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def live_channel(task_id: uuid.UUID) -> str:
    return f"daemon:task:{task_id}:live"


@dataclass
class AttemptState:
    claim: Claim
    #: Local, conservative lease expiry (event-loop clock).
    lease_deadline: float = 0.0
    delta_seq: int = 0
    cancel_requested: bool = False
    account_suspended: bool = False
    fenced: bool = False
    #: Execution was cancelled by this attempt after a cancel or suspension.
    interrupted: bool = False
    #: The task running the chat engine for this attempt.
    execution: asyncio.Task[Any] | None = None
    result: TaskStatus | None = None
    requested_terminal: str | None = None
    error: BaseException | None = None
    publish_failures: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def stopping(self) -> bool:
        return self.cancel_requested or self.account_suspended or self.fenced


class AttemptSink:
    """Receives the chat engine's assistant-row writes and fences them."""

    def __init__(self, store: TaskStore, state: AttemptState) -> None:
        self._store = store
        self._state = state

    def _row(self) -> dict[str, Any]:
        return {"id": self._state.claim.result_message_id}

    async def insert_message(self, **_kwargs: Any) -> dict[str, Any]:
        # The task's result row was created at acceptance; reuse it.
        return self._row()

    async def update_message(
        self,
        message_id: Any = None,
        *,
        content: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        claim = self._state.claim
        try:
            if status == "complete":
                message_fields = {key: value for key, value in fields.items() if value is not None}
                if metadata:
                    message_fields["metadata"] = metadata
                self._state.result = await self._store.complete(
                    claim.task_id,
                    claim.epoch,
                    content=content or "",
                    message_fields=message_fields,
                )
                # The status actually committed: a cancel or suspension may
                # have won, and the engine then skips success-only work.
                return {**self._row(), "status": _COMMITTED_MESSAGE_STATUS[self._state.result]}
            if content is not None:
                await self._store.write_partial(
                    claim.task_id, claim.epoch, content=content, delta_seq=self._state.delta_seq
                )
            if status is not None:
                # cancelled / error: the runner applies the task-level outcome.
                self._state.requested_terminal = status
            return self._row()
        except LeaseLost:
            # Stop the whole attempt now: a provider call between frames would
            # otherwise run on beside the recovery attempt.
            _fence_lost(self._state)
            self._state.fenced = True
            return None


def _interrupt(state: AttemptState, execution: asyncio.Task[Any]) -> None:
    # The engine stops at its next frame once ``stopping`` is set; a provider
    # call that produces no frame is interrupted here instead.
    if execution.done() or state.result is not None or state.fenced:
        return
    state.interrupted = True
    execution.cancel()


def _fence_lost(state: AttemptState) -> None:
    # The lease is gone (seen by the effect fence or a fenced write): stop
    # the whole attempt now, once.
    if state.execution is not None and state.result is None and not state.fenced:
        _stop_fenced(state, state.execution)


def _stop_fenced(state: AttemptState, execution: asyncio.Task[Any]) -> None:
    # Another attempt may own the task: abort the provider stream now rather
    # than at the next event, to bound duplicate spend.
    state.fenced = True
    execution.cancel()


async def _heartbeat(store: TaskStore, state: AttemptState, execution: asyncio.Task[Any]) -> None:
    """Renew the lease and stop execution the moment it can no longer be trusted.

    The local deadline is conservative: a lease is granted at most LEASE_S
    from the moment a claim or renewal was *requested*, minus a safety
    margin. Every sleep and every renewal is bounded by the remaining time, so
    execution stops at the deadline even while a renewal is still pending
    (§5: no writes or provider work after the lease could have passed on).
    """
    claim = state.claim
    loop = asyncio.get_running_loop()
    while not state.fenced:
        remaining = state.lease_deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(HEARTBEAT_S, remaining))
        if state.result is not None:
            # This attempt committed its outcome; the lease ended with it and
            # post-completion work must not be cancelled as if fenced.
            return
        remaining = state.lease_deadline - loop.time()
        if remaining <= 0:
            break
        requested_at = loop.time()
        try:
            beat = await asyncio.wait_for(
                store.heartbeat(claim.task_id, claim.epoch, lease_s=LEASE_S),
                timeout=min(HEARTBEAT_S, remaining),
            )
        except LeaseLost:
            if state.result is not None:
                return
            _stop_fenced(state, execution)
            return
        except Exception:
            # A missed beat is tolerable while the lease is still ours; the
            # loop re-checks the deadline before waiting again.
            logger.warning("Task heartbeat failed (task_id=%s)", claim.task_id, exc_info=True)
            continue
        state.lease_deadline = requested_at + LEASE_S - LEASE_SAFETY_S
        was_stopping = state.cancel_requested or state.account_suspended
        if beat.cancel_requested:
            state.cancel_requested = True
        if beat.account_suspended:
            state.account_suspended = True
        if not was_stopping and (state.cancel_requested or state.account_suspended):
            loop.call_later(STOP_GRACE_S, _interrupt, state, execution)
    if state.result is None and not state.fenced:
        logger.warning("Task lease could not be renewed in time (task_id=%s)", claim.task_id)
        _stop_fenced(state, execution)


def _parse_frame(frame: str) -> tuple[str | None, dict[str, Any]]:
    event: str | None = None
    data: dict[str, Any] = {}
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            try:
                parsed = json.loads(line[6:])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                data = parsed
    return event, data


async def _publish(redis: Any, state: AttemptState, message: dict[str, Any]) -> None:
    if redis is None:
        return
    try:
        await redis.publish(live_channel(state.claim.task_id), json.dumps(message))
    except Exception:
        state.publish_failures += 1
        if state.publish_failures == 1:
            logger.warning("Live task updates unavailable (task_id=%s)", state.claim.task_id)


async def _history(store: Any, claim: Claim, limit: int, prepared: Any) -> list[dict[str, Any]]:
    """History as of acceptance: up to and including the accepted user turn.

    Request-bound paths (such as council) may still write to the same
    conversation after acceptance, so later rows are cut off rather than
    assumed absent. The task's own placeholder is a streaming row and is
    excluded.
    """
    rows = await store.get_recent_messages(
        claim.conversation_id,
        limit=limit,
        exclude_status=_HISTORY_EXCLUDED,
        until_message_id=claim.user_message_id,
    )
    accepted_ids = [
        index for index, row in enumerate(rows) if row.get("id") == claim.user_message_id
    ]
    if not accepted_ids:
        raise RuntimeError("accepted user turn is not in the recent history window")
    rows = rows[: accepted_ids[-1] + 1]
    history: list[dict[str, Any]] = []
    for row in rows:
        if row.get("role") in (None, "system") or row.get("content") is None:
            continue
        mapped: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        if row.get("id") == claim.user_message_id and prepared is not None:
            mapped["content"] = prepared
        if row.get("reasoning_text"):
            mapped["reasoning"] = row["reasoning_text"]
        if row.get("reasoning_duration_secs") is not None:
            mapped["reasoning_duration_secs"] = row["reasoning_duration_secs"]
        if row.get("reasoning_model"):
            mapped["reasoning_model"] = row["reasoning_model"]
        history.append(mapped)
    return history


async def _system_prompt(memory: Any, db_pool: Any, claim: Claim) -> tuple[str, str | None]:
    from orchestrator.memory.embedding import raise_if_embedding_accounting_error
    from orchestrator.memory.injection import (
        assemble_system_prompt,
        build_memory_context,
        format_preferences_block,
    )
    from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
    from orchestrator.skills_store import build_skill_index
    from orchestrator.timezones import extract_timezone_name

    try:
        skills_block = await build_skill_index(db_pool=db_pool)
    except Exception:
        logger.warning("Skills injection failed, continuing without skills", exc_info=True)
        skills_block = ""
    preferences_block = ""
    user_timezone = None
    try:
        user_settings = await memory.get_user_settings(claim.user_id)
        user_timezone = extract_timezone_name(user_settings)
        preferences_block = format_preferences_block(user_settings)
    except Exception:
        logger.warning("Preference injection failed, using base prompt", exc_info=True)
    prompt = DAEMON_SYSTEM_PROMPT
    try:
        context = await build_memory_context(memory, claim.conversation_id)
        prompt = await assemble_system_prompt(
            memory_context=context,
            preferences_block=preferences_block,
            conversation_id=claim.conversation_id,
        )
    except Exception as error:
        raise_if_embedding_accounting_error(error)
        logger.warning("Memory injection failed, using base prompt", exc_info=True)
    if skills_block and skills_block not in prompt:
        prompt = f"{prompt.rstrip()}\n\n{skills_block}"
    return prompt, user_timezone


async def settle_lost_attempt_holds(store: TaskStore, db_pool: Any, task_id: uuid.UUID) -> int:
    """Settle reservations a lost attempt left open, at their full hold.

    A crashed worker never settles its holds, and an open foreground hold
    counts against the account's concurrency ceiling until the generic
    recovery (2 x request_timeout_s) runs. Settling them as soon as the
    lease is taken over frees the slot for the recovery attempt. Unknown
    provider work is never assumed free; a fenced worker that is still alive
    gets a settlement conflict later and is never charged twice.
    """
    from orchestrator.entitlements.errors import ReservationNotFound, SettlementConflict
    from orchestrator.entitlements.service import EntitlementService

    scopes = await store.ended_compute_scopes(task_id)
    if not scopes:
        return 0
    rows = await db_pool.fetch(
        "SELECT id, reserved_microusd FROM entitlement_reservations "
        "WHERE scope_id = ANY($1::uuid[]) AND status = 'open'",
        scopes,
    )
    service = EntitlementService(db_pool)
    settled = 0
    for row in rows:
        try:
            await service.settle(
                row["id"],
                int(row["reserved_microusd"]),
                usage={"estimated_cost": True, "recovered_after_timeout": True},
            )
            settled += 1
        except (SettlementConflict, ReservationNotFound):
            continue  # settled by its own worker or recovery meanwhile
    return settled


async def _execute(ctx: dict[str, Any], store: TaskStore, state: AttemptState) -> None:
    from orchestrator.daemon import stream_sse_chat

    claim = state.claim
    settings = ctx["settings"]
    memory = ctx["store"]
    db_pool = ctx["db_pool"]
    redis = ctx.get("redis")
    task_input = claim.task_input
    provider_config = settings.get_provider_config(task_input.get("provider"))
    request_id = str(task_input.get("request_id") or f"req_{uuid.uuid4().hex}")

    async def is_disconnected() -> bool:
        # The chat engine's stop signal: an explicit cancel or a lost lease,
        # never a client disconnect.
        return state.stopping

    async with account_compute(
        db_pool,
        claim.user_id,
        auto_route=bool(task_input.get("auto_route", True)),
        profile=str(task_input.get("profile") or "routine"),
        request_id=request_id,
    ):
        try:
            await store.begin_execution(
                claim.task_id,
                claim.epoch,
                current_scope().scope_id,
                prompt_version=str(DAEMON_PROMPT_VERSION),
            )
        except ExecutionRefused as refused:
            # Stopped between the claim and inference: no provider call.
            if refused.reason == "account_suspended":
                state.account_suspended = True
            else:
                state.cancel_requested = True
            return
        system_prompt, user_timezone = await _system_prompt(memory, db_pool, claim)
        history = await _history(
            memory, claim, settings.chat_history_limit, task_input.get("prepared_content")
        )
        frames = stream_sse_chat(
            settings=settings,
            provider_config=provider_config,
            system_prompt=system_prompt,
            user_message=str(task_input.get("message") or ""),
            request_id=request_id,
            conversation_id=f"conv_{claim.conversation_id}",
            is_disconnected=is_disconnected,
            actual_model=task_input.get("actual_model"),
            reported_model=task_input.get("reported_model"),
            routing_info=task_input.get("routing_info"),
            history_messages=history,
            memory_store=memory,
            user_id=claim.user_id,
            conversation_uuid=claim.conversation_id,
            queue=redis,
            db_pool=db_pool,
            trusted_spawn_context=task_input.get("trusted_spawn_context"),
            disable_memory_write=bool(task_input.get("disable_memory_write")),
            user_timezone=user_timezone,
            message_sink=AttemptSink(store, state),
            tool_guard=lambda registry: guard_registry(
                registry, store, claim.task_id, claim.epoch, lambda: _fence_lost(state)
            ),
        )
        async for frame in frames:
            event, envelope = _parse_frame(frame)
            raw_data = envelope.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            if event == "token":
                text = data.get("text")
                if isinstance(text, str) and text:
                    state.delta_seq += 1
                    await _publish(
                        redis,
                        state,
                        {"t": "delta", "gen": claim.epoch, "seq": state.delta_seq, "text": text},
                    )
            elif event in _PASSTHROUGH_FRAMES:
                await _publish(redis, state, {"t": "frame", "gen": claim.epoch, "frame": frame})
                if event in {"tool_call", "tool_result"}:
                    with contextlib.suppress(LeaseLost):
                        await store.record_event(
                            claim.task_id, claim.epoch, event, {"name": data.get("name")}
                        )
            elif event == "error":
                state.requested_terminal = state.requested_terminal or "error"


def _classify(exc: BaseException) -> tuple[RetryCause, str]:
    refusal = compute_error(exc)
    if refusal is None:
        return RetryCause.RETRYABLE_ERROR, "internal_error"
    if refusal.code in RETRYABLE_COMPUTE_CODES:
        return RetryCause.RETRYABLE_ERROR, refusal.code
    if refusal.code in CAPACITY_CODES:
        return RetryCause.CAPACITY, refusal.code
    return RetryCause.TERMINAL_ERROR, refusal.code


async def _resolve(store: TaskStore, state: AttemptState) -> str:
    claim = state.claim
    if state.result is not None:
        return state.result.value
    if state.fenced:
        return "fenced"
    try:
        if state.cancel_requested:
            await store.acknowledge_cancel(claim.task_id, claim.epoch)
            return TaskStatus.CANCELLED.value
        if state.account_suspended:
            cause, code = RetryCause.TERMINAL_ERROR, "account_suspended"
        elif state.error is not None:
            cause, code = _classify(state.error)
            if code in ADMISSION_CODES and await store.defer_execution(
                claim.task_id,
                claim.epoch,
                delay_s=DEFER_S,
                reason=code,
                max_wait_s=ADMISSION_WAIT_S,
            ):
                return TaskStatus.QUEUED.value
        else:
            # The engine ended without publishing a result (an internal error
            # it reported in-stream, or an unexpected early stop).
            cause, code = RetryCause.RETRYABLE_ERROR, "internal_error"
        status = await store.fail_attempt(claim.task_id, claim.epoch, cause=cause, error_code=code)
        return status.value
    except LeaseLost:
        return "fenced"


async def run_chat_task(ctx: dict[str, Any], task_id: str) -> str:
    """arq job: run one attempt of a durable chat task."""
    store: TaskStore | None = ctx.get("task_store")
    if store is None:
        logger.warning("Durable task store unavailable; leaving task for the sweep")
        return "unavailable"
    loop = asyncio.get_running_loop()
    # The lease runs from when the claim was requested, not from when this
    # worker got round to executing.
    lease_deadline = loop.time() + LEASE_S - LEASE_SAFETY_S
    claim = await store.claim(uuid.UUID(task_id), worker_id=WORKER_ID, lease_s=LEASE_S)
    if claim is None:
        # The claim may have ended the task (a lost attempt that needs
        # attention, exhausted its attempts or was cancelled): free the dead
        # attempt's open holds now rather than at generic recovery. Only
        # ended attempts' scopes are touched, so this is safe when skipped.
        try:
            await settle_lost_attempt_holds(store, ctx["db_pool"], uuid.UUID(task_id))
        except Exception:
            logger.warning("Could not settle a lost attempt's holds", exc_info=True)
        return "skipped"
    try:
        await settle_lost_attempt_holds(store, ctx["db_pool"], claim.task_id)
    except Exception:
        # Admission must not race a lost attempt's still-open hold (a one-slot
        # account would refuse this attempt and burn it). Hand the task back
        # without consuming the attempt; the sweep retries later.
        logger.warning("Could not settle a lost attempt's holds; deferring", exc_info=True)
        try:
            await store.defer_claim(
                claim.task_id, claim.epoch, delay_s=DEFER_S, reason="recovery_pending"
            )
        except Exception:
            # The database is unavailable for this write too. Nothing executed,
            # so the lease simply lapses and a later claim starts afresh
            # without having consumed an attempt.
            logger.warning("Could not defer the claim; letting the lease lapse", exc_info=True)
        return "deferred"
    if loop.time() >= lease_deadline:
        # Reconciliation outlasted the lease: another worker may own the task
        # now. Nothing has executed, so nothing counts against the attempts.
        return "fenced"
    state = AttemptState(claim=claim, lease_deadline=lease_deadline)
    execution = asyncio.create_task(_execute(ctx, store, state))
    state.execution = execution
    heartbeat = asyncio.create_task(_heartbeat(store, state, execution))
    try:
        await execution
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if (current is not None and current.cancelling()) or not (
            state.fenced or state.interrupted
        ):
            # Worker shutdown: the lease expires and a later claim recovers.
            raise
    except Exception as exc:
        state.error = exc
        if compute_error(exc) is None:
            logger.exception("Durable chat task attempt failed (task_id=%s)", claim.task_id)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat
    outcome = await _resolve(store, state)
    await _publish(ctx.get("redis"), state, {"t": "terminal", "gen": claim.epoch})
    return outcome


async def sweep_tasks(ctx: dict[str, Any]) -> int:
    """arq cron: wake tasks whose wake-up was lost or whose lease expired."""
    store: TaskStore | None = ctx.get("task_store")
    redis = ctx.get("redis")
    if store is None or redis is None:
        return 0
    woken = 0
    for task_id, wake_seq in await store.due_for_wakeup():
        try:
            await redis.enqueue_job(
                "run_chat_task", str(task_id), _job_id=f"task:{task_id}:{wake_seq}"
            )
            woken += 1
        except Exception:
            logger.warning("Task wake-up enqueue failed (task_id=%s)", task_id, exc_info=True)
    return woken
