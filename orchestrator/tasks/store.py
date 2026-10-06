"""PostgreSQL authority for durable tasks (docs/DURABLE_REQUEST_DESIGN.md §3–§6, §11).

Every worker write locks the task row and checks ``lease_epoch`` in the same
transaction, so an attempt that lost its lease can never publish over a newer
one. Lease arithmetic uses the database clock only. Owner-facing reads always
filter on ``user_id`` in SQL; a task owned by another account is reported
exactly like a missing one.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.tasks.inputs import CHAT_INPUT_VERSION, canonical_json
from orchestrator.tasks.states import (
    ACTIVE_STATUSES,
    RetryCause,
    TaskStatus,
    decide_after_attempt,
    retry_backoff_s,
)

#: Account suspension stops running work (§11); checked at heartbeat, before
#: material effects and before publication.
_SUSPENDED = (
    "EXISTS (SELECT 1 FROM entitlement_accounts a "
    "WHERE a.user_id = tasks.user_id AND a.status = 'suspended')"
)

IDEMPOTENCY_INDEX = "uq_tasks_user_idempotency"
CONVERSATION_ACTIVE_INDEX = "uq_tasks_conversation_active"

#: Message status mirrored onto the result message for each terminal task status.
_MESSAGE_STATUS = {
    TaskStatus.COMPLETED: "complete",
    TaskStatus.FAILED: "error",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.NEEDS_ATTENTION: "error",
}

_ATTEMPT_OUTCOME = {
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED: "failed_terminal",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.NEEDS_ATTENTION: "needs_attention",
}


class TaskError(Exception):
    """Base class for task store refusals."""


class TaskNotFound(TaskError):
    """No such task or conversation for this account (never distinguishes the two)."""


class IdempotencyConflict(TaskError):
    """The idempotency key was already used for a different request."""


class ConversationBusy(TaskError):
    """The conversation already has a non-terminal task."""

    def __init__(self, active_task_id: uuid.UUID) -> None:
        super().__init__("conversation already has an active task")
        self.active_task_id = active_task_id


class LeaseLost(TaskError):
    """This attempt no longer holds the task's lease; it must stop writing."""


class EffectRefused(TaskError):
    """A material operation may not start: the task was cancelled, its account
    suspended, or too little lease time remains."""


@dataclass(frozen=True, slots=True)
class AcceptedTask:
    task_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    result_message_id: uuid.UUID
    status: TaskStatus
    #: False when an idempotent replay returned an existing task.
    created: bool


@dataclass(frozen=True, slots=True)
class Claim:
    task_id: uuid.UUID
    epoch: int
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    result_message_id: uuid.UUID
    attempt_count: int
    max_attempts: int
    task_input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    cancel_requested: bool
    account_suspended: bool = False


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: uuid.UUID
    conversation_id: uuid.UUID
    result_message_id: uuid.UUID
    status: TaskStatus
    terminal_code: str | None
    attempt_count: int
    content_generation: int
    content_delta_seq: int
    content: str
    event_seq: int
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class TaskEvent:
    seq: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    #: Status after the request: ``cancelled`` for a queued task, ``running``
    #: (cancel requested) for an executing one, or the existing terminal status.
    status: TaskStatus
    #: Whether this request changed anything.
    accepted: bool


class TaskStore:
    def __init__(
        self, pool: asyncpg.Pool, encryption: ContentEncryption, memory: MemoryStore
    ) -> None:
        self._pool = pool
        self._enc = encryption
        self._memory = memory

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _seal(self, value: dict[str, Any]) -> str:
        return self._enc.encrypt(canonical_json(value))

    def _open(self, ciphertext: str | None) -> dict[str, Any]:
        if ciphertext is None:
            return {}
        value = json.loads(self._enc.decrypt(ciphertext))
        return value if isinstance(value, dict) else {}

    async def _append_event(
        self, conn: Any, task_id: uuid.UUID, kind: str, payload: dict[str, Any] | None = None
    ) -> int:
        seq = await conn.fetchval(
            "UPDATE tasks SET event_seq = event_seq + 1, updated_at = now() "
            "WHERE id = $1 RETURNING event_seq",
            task_id,
        )
        await conn.execute(
            "INSERT INTO task_events (task_id, seq, kind, payload_ciphertext) VALUES ($1, $2, $3, $4)",
            task_id,
            seq,
            kind,
            self._seal(payload) if payload else None,
        )
        return int(seq)

    async def _material_started(self, conn: Any, task_id: uuid.UUID) -> bool:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM task_operations WHERE task_id = $1)", task_id
            )
        )

    async def _end_attempt(
        self,
        conn: Any,
        task_id: uuid.UUID,
        epoch: int,
        outcome: str,
        terminal_code: str | None,
        result_message_id: uuid.UUID,
    ) -> None:
        # The result message's content column is already application-layer
        # ciphertext; copying it keeps this attempt's output for disclosure
        # after a later attempt overwrites the message.
        await conn.execute(
            """
            UPDATE task_attempts
            SET outcome = $3, terminal_code = $4, ended_at = now(),
                partial_ciphertext = (SELECT content FROM messages WHERE id = $5)
            WHERE task_id = $1 AND epoch = $2 AND outcome = 'running'
            """,
            task_id,
            epoch,
            outcome,
            terminal_code,
            result_message_id,
        )

    async def _finish(
        self,
        conn: Any,
        row: Any,
        status: TaskStatus,
        terminal_code: str | None,
    ) -> None:
        """Move a locked task to a terminal status and mirror it onto the message."""
        await conn.execute(
            """
            UPDATE tasks
            SET status = $2, terminal_code = $3, finished_at = now(), updated_at = now(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = $1
            """,
            row["id"],
            status.value,
            terminal_code,
        )
        metadata: dict[str, Any] = {"terminal_status": _MESSAGE_STATUS[status]}
        if terminal_code:
            metadata["terminal_reason"] = terminal_code
        await self._memory.update_message(
            row["result_message_id"],
            status=_MESSAGE_STATUS[status],
            metadata=metadata,
            conn=conn,
        )
        await self._append_event(
            conn, row["id"], "finished", {"status": status.value, "code": terminal_code}
        )

    async def _locked(self, conn: Any, task_id: uuid.UUID) -> Any:
        return await conn.fetchrow(
            """
            SELECT t.*,
                   (t.status = 'queued' AND t.next_wakeup_at <= now()) AS due,
                   (t.status = 'running' AND t.lease_expires_at < now()) AS lease_expired
            FROM tasks t WHERE t.id = $1 FOR UPDATE
            """,
            task_id,
        )

    async def _locked_for_epoch(self, conn: Any, task_id: uuid.UUID, epoch: int) -> Any:
        return await conn.fetchrow(
            "SELECT * FROM tasks WHERE id = $1 AND lease_epoch = $2 AND status = 'running' "
            "FOR UPDATE",
            task_id,
            epoch,
        )

    # ------------------------------------------------------------------ #
    # Acceptance
    # ------------------------------------------------------------------ #

    async def accept(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        new_conversation_title: str | None,
        pipeline: str,
        user_message: str,
        task_input: dict[str, Any],
        request_hash: str,
        idempotency_key: str | None,
        assistant_model: str | None,
        max_attempts: int = 2,
    ) -> AcceptedTask:
        """Atomically record the conversation turn and its task.

        Nothing is acknowledged before this commits. A replay with the same key
        and request returns the existing task; the same key with a different
        request raises :class:`IdempotencyConflict`.
        """
        if idempotency_key is not None:
            existing = await self._by_key(user_id, idempotency_key, request_hash)
            if existing is not None:
                return existing
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                if conversation_id is None:
                    conversation = await self._memory.create_conversation(
                        user_id=user_id, pipeline=pipeline, title=new_conversation_title, conn=conn
                    )
                    conversation_id = conversation["id"]
                else:
                    owner = await conn.fetchval(
                        "SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE",
                        conversation_id,
                    )
                    if owner != user_id:
                        raise TaskNotFound("conversation not found")
                assert conversation_id is not None
                user_row = await self._memory.insert_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role="user",
                    content=user_message,
                    status="complete",
                    conn=conn,
                )
                result_row = await self._memory.insert_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role="assistant",
                    content="",
                    model=assistant_model,
                    status="streaming",
                    conn=conn,
                )
                # Both rows default to the transaction's now(); give the
                # placeholder a strictly later timestamp so history ordering
                # never ties the answer with its question.
                await conn.execute(
                    "UPDATE messages SET created_at = clock_timestamp() WHERE id = $1",
                    result_row["id"],
                )
                task_id = await conn.fetchval(
                    """
                    INSERT INTO tasks
                        (user_id, conversation_id, user_message_id, result_message_id,
                         input_ciphertext, input_version, idempotency_key, request_hash,
                         max_attempts)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    user_id,
                    conversation_id,
                    user_row["id"],
                    result_row["id"],
                    self._seal(task_input),
                    CHAT_INPUT_VERSION,
                    idempotency_key,
                    request_hash,
                    max_attempts,
                )
                await self._append_event(conn, task_id, "accepted")
        except asyncpg.UniqueViolationError as exc:
            # The transaction rolled back entirely, including the messages.
            if idempotency_key is not None:
                existing = await self._by_key(user_id, idempotency_key, request_hash)
                if existing is not None:
                    return existing
            constraint = getattr(exc, "constraint_name", None)
            if constraint == CONVERSATION_ACTIVE_INDEX and conversation_id is not None:
                active = await self._pool.fetchval(
                    "SELECT id FROM tasks WHERE conversation_id = $1 AND user_id = $2 "
                    "AND status IN ('queued', 'running')",
                    conversation_id,
                    user_id,
                )
                if active is not None:
                    raise ConversationBusy(active) from None
            raise
        return AcceptedTask(
            task_id=task_id,
            conversation_id=conversation_id,
            user_message_id=user_row["id"],
            result_message_id=result_row["id"],
            status=TaskStatus.QUEUED,
            created=True,
        )

    async def find_by_key(
        self, user_id: uuid.UUID, idempotency_key: str, request_hash: str
    ) -> AcceptedTask | None:
        """The task this account's key created, if any (conflict if the request differs)."""
        return await self._by_key(user_id, idempotency_key, request_hash)

    async def _by_key(
        self, user_id: uuid.UUID, idempotency_key: str, request_hash: str
    ) -> AcceptedTask | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM tasks WHERE user_id = $1 AND idempotency_key = $2",
            user_id,
            idempotency_key,
        )
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflict("idempotency key reused for a different request")
        return AcceptedTask(
            task_id=row["id"],
            conversation_id=row["conversation_id"],
            user_message_id=row["user_message_id"],
            result_message_id=row["result_message_id"],
            status=TaskStatus(row["status"]),
            created=False,
        )

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #

    async def mark_woken(self, task_id: uuid.UUID) -> int:
        """Record a wake-up about to be enqueued; returns its sequence for the job id."""
        return int(
            await self._pool.fetchval(
                "UPDATE tasks SET wake_seq = wake_seq + 1, last_wake_at = now() "
                "WHERE id = $1 RETURNING wake_seq",
                task_id,
            )
        )

    async def due_for_wakeup(
        self, *, limit: int = 50, rewake_after_s: float = 60.0
    ) -> list[tuple[uuid.UUID, int]]:
        """Find queued or expired-lease tasks that need a wake-up, and record it.

        A task woken within ``rewake_after_s`` is skipped so a queue backlog is
        not flooded with duplicates; duplicates would be harmless, since the
        claim decides ownership.
        """
        rows = await self._pool.fetch(
            """
            WITH due AS (
                SELECT id FROM tasks
                WHERE ((status = 'queued' AND next_wakeup_at <= now())
                    OR (status = 'running' AND lease_expires_at < now()))
                  AND (last_wake_at IS NULL
                       OR last_wake_at < now() - make_interval(secs => $2))
                ORDER BY COALESCE(lease_expires_at, next_wakeup_at)
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE tasks t SET wake_seq = t.wake_seq + 1, last_wake_at = now()
            FROM due WHERE t.id = due.id
            RETURNING t.id, t.wake_seq
            """,
            limit,
            rewake_after_s,
        )
        return [(row["id"], int(row["wake_seq"])) for row in rows]

    # ------------------------------------------------------------------ #
    # Worker lifecycle
    # ------------------------------------------------------------------ #

    async def claim(self, task_id: uuid.UUID, *, worker_id: str, lease_s: float) -> Claim | None:
        """Claim a due task, taking over an expired lease if needed.

        Returns ``None`` for a duplicate delivery, a task that is not due, or a
        task this claim resolved instead of running (cancelled, exhausted, or
        stopped at ``needs_attention`` because a lost attempt may have caused a
        material effect).
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked(conn, task_id)
            if row is None or not (row["due"] or row["lease_expired"]):
                return None
            if row["lease_expired"]:
                epoch = int(row["lease_epoch"])
                if row["cancel_requested_at"] is not None:
                    await self._end_attempt(
                        conn, task_id, epoch, "cancelled", None, row["result_message_id"]
                    )
                    await self._finish(conn, row, TaskStatus.CANCELLED, "cancelled")
                    return None
                decision = decide_after_attempt(
                    cause=RetryCause.LOST,
                    attempt_count=int(row["attempt_count"]),
                    max_attempts=int(row["max_attempts"]),
                    material_started=await self._material_started(conn, task_id),
                )
                await self._end_attempt(
                    conn,
                    task_id,
                    epoch,
                    "lost"
                    if decision.status is TaskStatus.QUEUED
                    else _ATTEMPT_OUTCOME[decision.status],
                    decision.terminal_code,
                    row["result_message_id"],
                )
                await self._append_event(conn, task_id, "attempt_lost", {"epoch": epoch})
                if decision.status is not TaskStatus.QUEUED:
                    await self._finish(conn, row, decision.status, decision.terminal_code)
                    return None
            elif row["cancel_requested_at"] is not None:
                # Defensive: cancelling a queued task terminalises it directly,
                # so a pending cancel must never start an attempt.
                await self._finish(conn, row, TaskStatus.CANCELLED, "cancelled")
                return None
            elif await self._material_started(conn, task_id):
                # Defensive: a queued task never carries operations, because
                # every retry path checks them first.
                await self._finish(conn, row, TaskStatus.NEEDS_ATTENTION, "uncertain_effect")
                return None
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                await self._finish(conn, row, TaskStatus.FAILED, "interrupted")
                return None

            claimed = await conn.fetchrow(
                """
                UPDATE tasks
                SET status = 'running', lease_epoch = lease_epoch + 1,
                    lease_owner = $2, lease_expires_at = now() + make_interval(secs => $3),
                    attempt_count = attempt_count + 1,
                    content_generation = lease_epoch + 1, content_delta_seq = 0,
                    updated_at = now()
                WHERE id = $1
                RETURNING lease_epoch, attempt_count
                """,
                task_id,
                worker_id,
                lease_s,
            )
            epoch = int(claimed["lease_epoch"])
            await conn.execute(
                "INSERT INTO task_attempts (task_id, epoch, worker_id) VALUES ($1, $2, $3)",
                task_id,
                epoch,
                worker_id,
            )
            # A new attempt is a new content generation: observers replace,
            # never append to, what an earlier attempt wrote.
            await self._memory.update_message(
                row["result_message_id"], content="", status="streaming", conn=conn
            )
            await self._append_event(
                conn,
                task_id,
                "attempt_started",
                {"epoch": epoch, "attempt": int(claimed["attempt_count"])},
            )
            return Claim(
                task_id=task_id,
                epoch=epoch,
                user_id=row["user_id"],
                conversation_id=row["conversation_id"],
                user_message_id=row["user_message_id"],
                result_message_id=row["result_message_id"],
                attempt_count=int(claimed["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                task_input=self._open(row["input_ciphertext"]),
            )

    async def heartbeat(self, task_id: uuid.UUID, epoch: int, *, lease_s: float) -> Heartbeat:
        """Extend this attempt's lease; raises :class:`LeaseLost` when fenced out."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"""
                UPDATE tasks SET lease_expires_at = now() + make_interval(secs => $3),
                                 updated_at = now()
                WHERE id = $1 AND lease_epoch = $2 AND status = 'running'
                RETURNING cancel_requested_at, {_SUSPENDED} AS account_suspended
                """,
                task_id,
                epoch,
                lease_s,
            )
            if row is None:
                raise LeaseLost("lease lost")
            await conn.execute(
                "UPDATE task_attempts SET heartbeat_at = now() WHERE task_id = $1 AND epoch = $2",
                task_id,
                epoch,
            )
            return Heartbeat(
                cancel_requested=row["cancel_requested_at"] is not None,
                account_suspended=bool(row["account_suspended"]),
            )

    async def write_partial(
        self, task_id: uuid.UUID, epoch: int, *, content: str, delta_seq: int
    ) -> None:
        """Persist streamed content of this attempt's generation under the fence."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            await self._memory.update_message(row["result_message_id"], content=content, conn=conn)
            await conn.execute(
                "UPDATE tasks SET content_delta_seq = $2, updated_at = now() WHERE id = $1",
                task_id,
                delta_seq,
            )

    async def begin_operation(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        tool_name: str,
        target: dict[str, Any] | None,
        min_lease_margin_s: float,
    ) -> uuid.UUID:
        """Record a material operation before it runs (the effect fence).

        Refuses unless this attempt holds the lease with at least
        ``min_lease_margin_s`` left, no cancel has been requested and the
        account is not suspended. The gap between this commit and the actual
        external call can be narrowed, not closed.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"""
                SELECT id, cancel_requested_at, {_SUSPENDED} AS account_suspended,
                       lease_expires_at > now() + make_interval(secs => $3) AS lease_margin_ok
                FROM tasks WHERE id = $1 AND lease_epoch = $2 AND status = 'running'
                FOR UPDATE
                """,
                task_id,
                epoch,
                min_lease_margin_s,
            )
            if row is None:
                raise LeaseLost("lease lost")
            if row["cancel_requested_at"] is not None:
                raise EffectRefused("task cancel requested")
            if row["account_suspended"]:
                raise EffectRefused("account suspended")
            if not row["lease_margin_ok"]:
                raise EffectRefused("lease too close to expiry")
            operation_id = await conn.fetchval(
                """
                INSERT INTO task_operations (task_id, epoch, tool_name, effect_class, target_ciphertext)
                VALUES ($1, $2, $3, 'material', $4) RETURNING id
                """,
                task_id,
                epoch,
                tool_name,
                self._seal(target) if target else None,
            )
            await self._append_event(conn, task_id, "operation_started", {"tool": tool_name})
            return operation_id

    async def record_compute_scope(
        self, task_id: uuid.UUID, epoch: int, scope_id: uuid.UUID
    ) -> None:
        """Remember this attempt's account compute scope, under the fence."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            await conn.execute(
                "UPDATE task_attempts SET compute_scope_id = $3 WHERE task_id = $1 AND epoch = $2",
                task_id,
                epoch,
                scope_id,
            )

    async def ended_compute_scopes(self, task_id: uuid.UUID) -> list[uuid.UUID]:
        """Compute scopes of this task's attempts that have ended without completing.

        Their reservations may still be open (a crashed worker never settled
        them). Never includes a running attempt's scope.
        """
        rows = await self._pool.fetch(
            "SELECT compute_scope_id FROM task_attempts "
            "WHERE task_id = $1 AND outcome NOT IN ('running', 'completed') "
            "AND compute_scope_id IS NOT NULL",
            task_id,
        )
        return [row["compute_scope_id"] for row in rows]

    async def record_event(
        self, task_id: uuid.UUID, epoch: int, kind: str, payload: dict[str, Any] | None = None
    ) -> int:
        """Append a lifecycle event from the running attempt, under the fence."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            return await self._append_event(conn, task_id, kind, payload)

    async def finish_operation(self, operation_id: uuid.UUID, *, outcome: str) -> None:
        """Record what a material operation did.

        Deliberately not fenced: a stale attempt's evidence that an effect
        happened is still evidence.
        """
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError(f"invalid operation outcome {outcome!r}")
        await self._pool.execute(
            "UPDATE task_operations SET outcome = $2, completed_at = now() "
            "WHERE id = $1 AND outcome = 'started'",
            operation_id,
            outcome,
        )

    async def complete(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        content: str,
        message_fields: dict[str, Any] | None = None,
    ) -> TaskStatus:
        """Publish the result and complete the task in one transaction.

        A cancel requested before this commit wins: the content is kept on the
        message but the task ends ``cancelled``. A suspended account's result is
        not published: the task ends ``failed`` (``account_suspended``).
        """
        fields = dict(message_fields or {})
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            if row["cancel_requested_at"] is None and await conn.fetchval(
                "SELECT status = 'suspended' FROM entitlement_accounts WHERE user_id = $1",
                row["user_id"],
            ):
                await self._end_attempt(
                    conn,
                    task_id,
                    epoch,
                    "failed_terminal",
                    "account_suspended",
                    row["result_message_id"],
                )
                await self._finish(conn, row, TaskStatus.FAILED, "account_suspended")
                return TaskStatus.FAILED
            status = (
                TaskStatus.CANCELLED
                if row["cancel_requested_at"] is not None
                else TaskStatus.COMPLETED
            )
            metadata = dict(fields.pop("metadata", None) or {})
            metadata["terminal_status"] = _MESSAGE_STATUS[status]
            await self._memory.update_message(
                row["result_message_id"],
                content=content,
                status=_MESSAGE_STATUS[status],
                metadata=metadata,
                conn=conn,
                **fields,
            )
            code = "cancelled" if status is TaskStatus.CANCELLED else None
            await self._end_attempt(
                conn, task_id, epoch, _ATTEMPT_OUTCOME[status], code, row["result_message_id"]
            )
            await conn.execute(
                """
                UPDATE tasks
                SET status = $2, terminal_code = $3, finished_at = now(), updated_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE id = $1
                """,
                task_id,
                status.value,
                code,
            )
            await self._append_event(
                conn, task_id, "finished", {"status": status.value, "code": code}
            )
            return status

    async def fail_attempt(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        cause: RetryCause,
        error_code: str | None,
    ) -> TaskStatus:
        """End this attempt unsuccessfully and apply the retry rules."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            if row["cancel_requested_at"] is not None:
                await self._end_attempt(
                    conn, task_id, epoch, "cancelled", "cancelled", row["result_message_id"]
                )
                await self._finish(conn, row, TaskStatus.CANCELLED, "cancelled")
                return TaskStatus.CANCELLED
            decision = decide_after_attempt(
                cause=cause,
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                material_started=await self._material_started(conn, task_id),
                error_code=error_code,
            )
            if decision.status is TaskStatus.QUEUED:
                await self._end_attempt(
                    conn, task_id, epoch, "failed_retryable", error_code, row["result_message_id"]
                )
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                        next_wakeup_at = now() + make_interval(secs => $2),
                        last_wake_at = NULL, updated_at = now()
                    WHERE id = $1
                    """,
                    task_id,
                    retry_backoff_s(int(row["attempt_count"])),
                )
                await self._append_event(
                    conn, task_id, "attempt_failed", {"epoch": epoch, "code": error_code}
                )
                return TaskStatus.QUEUED
            await self._end_attempt(
                conn,
                task_id,
                epoch,
                _ATTEMPT_OUTCOME[decision.status],
                decision.terminal_code,
                row["result_message_id"],
            )
            await self._finish(conn, row, decision.status, decision.terminal_code)
            return decision.status

    async def defer_claim(
        self, task_id: uuid.UUID, epoch: int, *, delay_s: float, reason: str
    ) -> None:
        """Hand a claimed task back to the queue without consuming its attempt.

        Used when a precondition for safe admission (such as settling a lost
        attempt's holds) cannot be established yet: no provider call has been
        made, so the attempt does not count against ``max_attempts``.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            await self._end_attempt(
                conn, task_id, epoch, "deferred", reason, row["result_message_id"]
            )
            await conn.execute(
                """
                UPDATE tasks
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    attempt_count = attempt_count - 1,
                    next_wakeup_at = now() + make_interval(secs => $2),
                    last_wake_at = NULL, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                delay_s,
            )
            await self._append_event(conn, task_id, "attempt_deferred", {"epoch": epoch})

    async def operations(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[dict[str, Any]]:
        """Owner-scoped evidence of material operations, for deciding on a retry.

        Returns the tool, outcome, timing and the non-content target summary
        recorded before the call (never bodies or credentials).
        """
        rows = await self._pool.fetch(
            """
            SELECT o.tool_name, o.outcome, o.started_at, o.completed_at, o.target_ciphertext
            FROM task_operations o JOIN tasks t ON t.id = o.task_id
            WHERE o.task_id = $1 AND t.user_id = $2
            ORDER BY o.started_at
            LIMIT 50
            """,
            task_id,
            user_id,
        )
        return [
            {
                "tool": row["tool_name"],
                "outcome": row["outcome"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "target": self._open(row["target_ciphertext"]),
            }
            for row in rows
        ]

    async def acknowledge_cancel(self, task_id: uuid.UUID, epoch: int) -> None:
        """The running attempt observed a cancel request and stopped."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._locked_for_epoch(conn, task_id, epoch)
            if row is None:
                raise LeaseLost("lease lost")
            await self._end_attempt(
                conn, task_id, epoch, "cancelled", "cancelled", row["result_message_id"]
            )
            await self._finish(conn, row, TaskStatus.CANCELLED, "cancelled")

    # ------------------------------------------------------------------ #
    # Owner-facing reads and control
    # ------------------------------------------------------------------ #

    async def request_cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> CancelOutcome:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1 AND user_id = $2 FOR UPDATE", task_id, user_id
            )
            if row is None:
                raise TaskNotFound("task not found")
            status = TaskStatus(row["status"])
            if status is TaskStatus.QUEUED:
                await self._finish(conn, row, TaskStatus.CANCELLED, "cancelled")
                return CancelOutcome(TaskStatus.CANCELLED, accepted=True)
            if status is TaskStatus.RUNNING:
                if row["cancel_requested_at"] is None:
                    await conn.execute(
                        "UPDATE tasks SET cancel_requested_at = now(), updated_at = now() "
                        "WHERE id = $1",
                        task_id,
                    )
                    await self._append_event(conn, task_id, "cancel_requested")
                return CancelOutcome(TaskStatus.RUNNING, accepted=True)
            return CancelOutcome(status, accepted=False)

    async def delete_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> tuple[bool, uuid.UUID | None]:
        """Delete a conversation unless a task in it is still running.

        One transaction locks the conversation (as acceptance does) and its
        active task (as claims do), so neither can slip in between. A queued
        task is cancelled and deleted with the conversation; a running task is
        asked to cancel and the conversation is kept, so an in-flight external
        effect never loses its evidence. Returns ``(deleted, running_task_id)``.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            owner = await conn.fetchval(
                "SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE", conversation_id
            )
            if owner != user_id:
                raise TaskNotFound("conversation not found")
            active = await conn.fetchrow(
                "SELECT * FROM tasks WHERE conversation_id = $1 AND status IN ('queued', 'running') "
                "FOR UPDATE",
                conversation_id,
            )
            if active is not None and active["status"] == TaskStatus.RUNNING.value:
                if active["cancel_requested_at"] is None:
                    await conn.execute(
                        "UPDATE tasks SET cancel_requested_at = now(), updated_at = now() "
                        "WHERE id = $1",
                        active["id"],
                    )
                    await self._append_event(conn, active["id"], "cancel_requested")
                return False, active["id"]
            await conn.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
            return True, None

    async def snapshot(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskSnapshot | None:
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content
            FROM tasks t JOIN messages m ON m.id = t.result_message_id
            WHERE t.id = $1 AND t.user_id = $2
            """,
            task_id,
            user_id,
        )
        return self._snapshot_from(row) if row is not None else None

    async def active_for_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> TaskSnapshot | None:
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content
            FROM tasks t JOIN messages m ON m.id = t.result_message_id
            WHERE t.conversation_id = $1 AND t.user_id = $2
              AND t.status IN ('queued', 'running')
            """,
            conversation_id,
            user_id,
        )
        return self._snapshot_from(row) if row is not None else None

    def _snapshot_from(self, row: Any) -> TaskSnapshot:
        return TaskSnapshot(
            task_id=row["id"],
            conversation_id=row["conversation_id"],
            result_message_id=row["result_message_id"],
            status=TaskStatus(row["status"]),
            terminal_code=row["terminal_code"],
            attempt_count=int(row["attempt_count"]),
            content_generation=int(row["content_generation"]),
            content_delta_seq=int(row["content_delta_seq"]),
            content=self._enc.decrypt(row["result_content"]),
            event_seq=int(row["event_seq"]),
            cancel_requested=row["cancel_requested_at"] is not None
            and TaskStatus(row["status"]) in ACTIVE_STATUSES,
        )

    async def latest_for_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> TaskSnapshot | None:
        """The conversation's most recent task, active or finished."""
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content
            FROM tasks t JOIN messages m ON m.id = t.result_message_id
            WHERE t.conversation_id = $1 AND t.user_id = $2
            ORDER BY t.created_at DESC LIMIT 1
            """,
            conversation_id,
            user_id,
        )
        return self._snapshot_from(row) if row is not None else None

    async def events_since(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, after_seq: int, limit: int = 200
    ) -> list[TaskEvent]:
        rows = await self._pool.fetch(
            """
            SELECT e.seq, e.kind, e.payload_ciphertext
            FROM task_events e JOIN tasks t ON t.id = e.task_id
            WHERE e.task_id = $1 AND t.user_id = $2 AND e.seq > $3
            ORDER BY e.seq LIMIT $4
            """,
            task_id,
            user_id,
            after_seq,
            limit,
        )
        return [
            TaskEvent(int(row["seq"]), row["kind"], self._open(row["payload_ciphertext"]))
            for row in rows
        ]
