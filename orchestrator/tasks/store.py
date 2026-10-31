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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    WriteGuardRefusal,
    guard_content_write,
)
from orchestrator.services.account_control.store import account_lock_key
from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    control_transaction,
)
from orchestrator.tasks._origin import (
    OriginalTaskOrigin,
    TaskOriginContext,
    TaskOriginRefusal,
    _classify_task_origin,
)
from orchestrator.tasks.inputs import CHAT_INPUT_VERSION, canonical_json
from orchestrator.tasks._invocation import InferenceInterrupted, invocation, require_effect
from orchestrator.tasks.inference import (
    CODE as INFERENCE_UNRESOLVED,
    InferenceRecovery,
    RecoveryObservation,
    acceptor_preflight,
)
from orchestrator.tasks.states import (
    ACTIVE_STATUSES,
    RetryCause,
    TaskStatus,
    decide_after_attempt,
    retry_backoff_s,
)

if TYPE_CHECKING:
    from orchestrator.resource_inputs import ResourceInputs
    from orchestrator.tasks.composition import TaskComposition, WorkerComposition

#: Account suspension stops running work (§11); checked at heartbeat, before
#: material effects and before publication.
_SUSPENDED = (
    "EXISTS (SELECT 1 FROM entitlement_accounts a "
    "WHERE a.user_id = tasks.user_id AND a.status = 'suspended')"
)
#: The attempt's lease is still current. An expired lease is never renewed
#: or written through, whether or not another worker has taken it over yet.
_LEASE_HELD = "tasks.lease_expires_at > clock_timestamp()"

IDEMPOTENCY_INDEX = "uq_tasks_user_idempotency"
CONVERSATION_ACTIVE_INDEX = "uq_tasks_conversation_active"

# Original OPEN ledger rows are the pending queue, never a new financial intent.
# Only ordinary application tables here; protected provenance is observed through
# InferenceRecovery.lock, not direct joins under the separated worker principal.
_QUARANTINE_HOLDS = """
    SELECT t.id AS task_id, r.id AS reservation_id, r.user_id, r.scope_id,
           r.reserved_microusd
    FROM tasks t
    JOIN task_attempts a ON a.task_id=t.id AND a.epoch=t.lease_epoch
    JOIN entitlement_reservations r ON r.scope_id=a.compute_scope_id AND r.user_id=t.user_id
    WHERE t.status='needs_attention' AND t.terminal_code='inference_dispatch_unresolved'
      AND t.finished_at IS NOT NULL AND t.lease_owner IS NULL AND t.lease_expires_at IS NULL
      AND a.outcome='needs_attention' AND a.terminal_code='inference_dispatch_unresolved'
      AND a.ended_at IS NOT NULL AND r.status='open'
"""

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


async def _disclose_regeneration(conn: Any, metadata: dict[str, Any], row: Any) -> None:
    """Mark a result that a later attempt regenerated after an interruption.

    §4: an interrupted attempt is disclosed, not hidden. Only the number of
    earlier attempts that executed is recorded (their partial text stays on
    the attempt records), counted from the attempts themselves, so it does
    not depend on whether the current attempt reached inference.
    """
    earlier = await conn.fetchval(
        "SELECT count(*) FROM task_attempts "
        "WHERE task_id = $1 AND epoch < $2 AND execution_started_at IS NOT NULL "
        "AND outcome IN ('lost', 'failed_retryable')",
        row["id"],
        int(row["lease_epoch"]),
    )
    if earlier:
        metadata["regenerated_after_interruption"] = int(earlier)


class _ActiveTaskEnded(Exception):
    """The conflicting active task finished before it could be named."""

    def __init__(self, violation: BaseException) -> None:
        super().__init__("active task ended")
        self.violation = violation


class ConversationBusy(TaskError):
    """The conversation already has a non-terminal task."""

    def __init__(self, active_task_id: uuid.UUID) -> None:
        super().__init__("conversation already has an active task")
        self.active_task_id = active_task_id


class LeaseLost(TaskError):
    """This attempt no longer holds the task's lease; it must stop writing."""


class _PermissionRefused(ObservationBlocked):
    """Latched definite refusal; only acknowledged rollback permits control exit."""


class TaskControlled(ObservationBlocked):
    """An acknowledged control-only exit, never a financial recovery grant."""

    def __init__(self, status: TaskStatus) -> None:
        super().__init__("task_controlled")
        self.status = status


class TaskQuarantined(TaskControlled):
    """Own ACKed quarantine; permits existing ended-hold accounting, never a send."""


class EffectRefused(TaskError):
    """A material operation may not start: the task was cancelled, its account
    suspended, or too little lease time remains. ``reason`` is
    ``cancel_requested``, ``account_suspended`` or ``lease_margin``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExecutionRefused(TaskError):
    """An attempt may not start inference: ``reason`` is ``cancel_requested``
    or ``account_suspended``. Nothing was counted or started."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AcceptedTask:
    task_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    result_message_id: uuid.UUID
    status: TaskStatus
    #: False when an idempotent replay returned an existing task.
    created: bool


_AcceptanceStage = Callable[[Any, AcceptedTask], Awaitable[None]]


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
    worker_id: str
    origin: OriginalTaskOrigin
    inference_covered: bool = False


@dataclass(frozen=True, slots=True)
class ControlledClaim:
    """Acknowledged controller exit, with no settlement or execution authority."""

    status: TaskStatus
    inference_quarantine: bool = False


@dataclass(frozen=True, slots=True)
class Heartbeat:
    cancel_requested: bool
    account_suspended: bool = False
    refusal: str | None = None


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
    regenerated_after_interruption: int = 0


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
        self,
        pool: asyncpg.Pool,
        encryption: ContentEncryption,
        memory: MemoryStore,
        *,
        inference_recovery: InferenceRecovery | None = None,
        cover_new_inference: bool = False,
    ) -> None:
        self._pool = pool
        self._enc = encryption
        self._memory = memory
        # Fixture-only explicit composition. Neither HTTP nor worker startup supplies
        # these collaborators. Acceptor INSERT and worker EXECUTE use different roles.
        self._inference = inference_recovery
        self._cover_new_inference = cover_new_inference
        self._composition: TaskComposition | WorkerComposition | None = None
        self._resource_inputs: ResourceInputs | None = None
        # Fairness only, independent of abandoned-pass lifecycle observation.
        self._quarantine_hold_after: tuple[uuid.UUID, uuid.UUID] | None = None

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

    def _origin(self, row: Any) -> OriginalTaskOrigin:
        # Authentication/key failures propagate as operational uncertainty. Never
        # classify invented plaintext, or substitute summary/decoded input fields.
        owner = row["user_id"]
        if not isinstance(owner, uuid.UUID):
            raise ObservationBlocked("task_not_owned")
        # asyncpg's UUID codec returns a UUID subclass. Normalize only the
        # independently established persisted identity, never the saved origin.
        origin = _classify_task_origin(
            self._enc.decrypt(row["input_ciphertext"]),
            input_version=row["input_version"],
            context=TaskOriginContext(uuid.UUID(bytes=owner.bytes)),
        )
        if isinstance(origin, TaskOriginRefusal):
            raise ObservationBlocked(origin.code)
        return origin

    def _covered(self, row: Any) -> bool:
        value = self._open(row["input_ciphertext"]).get("_inference_recovery_version")
        covered = type(value) is int and value == 1
        if (
            (value is not None and not covered)
            or (covered and self._inference is None)
            or (self._inference is not None and not covered)
        ):
            raise ObservationBlocked("inference_coverage_unavailable")
        return covered

    async def _inference_order(self, conn: Any, row: Any) -> RecoveryObservation | None:
        require_effect()
        if not self._covered(row):
            return None
        origin = self._origin(row)
        assert self._inference is not None
        return await self._inference.lock(
            conn, row["user_id"], origin.generation, row["id"], int(row["lease_epoch"])
        )

    async def _inference_before_task(self, conn: Any, task_id: uuid.UUID) -> None:
        row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
        if row is None:
            raise ObservationBlocked("task_not_owned")
        await self._inference_order(conn, row)

    async def _close_inference(self, conn: Any, row: Any, reason: str) -> None:
        observation = await self._inference_order(conn, row)
        if observation is not None:
            assert self._inference is not None
            if (
                reason != "stale"
                and row["status"] == "running"
                and not row.get("lease_expired", False)
            ):
                if not await conn.fetchval(
                    f"SELECT {_LEASE_HELD} FROM tasks WHERE id=$1", row["id"]
                ):
                    raise LeaseLost("lease lost")
            await self._inference.close(conn, observation, reason)

    async def _quarantine(self, conn: Any, row: Any) -> bool:
        observation = await self._inference_order(conn, row)
        if observation is None or not observation.possible:
            return False
        # _worker/claim authenticate the original and recheck ownership/lease before
        # this call. No _end_attempt: that helper archives ciphertext.
        await self._control_end(
            conn,
            row,
            TaskStatus.NEEDS_ATTENTION,
            INFERENCE_UNRESOLVED,
            end_attempt=row["status"] == "running",
        )
        return True

    async def _account(self, conn: Any, owner: uuid.UUID) -> Any:
        if await conn.fetchval("SHOW transaction_isolation") != "read committed":
            raise ObservationBlocked()
        await conn.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(owner))
        account = await conn.fetchrow(
            "SELECT status,content_generation FROM entitlement_accounts WHERE user_id=$1 FOR SHARE",
            owner,
        )
        # Separate post-lock statement: subordinate-only changes must be visible.
        state = await conn.fetchrow(
            "SELECT EXISTS(SELECT 1 FROM account_deletions "
            "WHERE account_id=$1 AND closed_at IS NULL) AS operation, "
            "EXISTS(SELECT 1 FROM account_suspension_causes WHERE account_id=$1) AS cause",
            owner,
        )
        if state is None:
            raise ObservationBlocked()
        return account, state

    @staticmethod
    def _refusal(account: Any, state: Any, origin: OriginalTaskOrigin) -> str | None:
        if account is None:
            return "account_unavailable"
        generation = account["content_generation"]
        if type(generation) is not int or not 0 <= generation <= 2**63 - 1:
            return "task_account_inconsistent"
        # A restored/contradictory earlier generation is NOT revocation proof,
        # even if the restored status currently says suspended.
        if generation < origin.generation:
            raise ObservationBlocked()
        if account["status"] == "suspended":
            return "account_suspended"
        if account["status"] != "active":
            return "task_account_inconsistent"
        if generation > origin.generation:
            return "task_generation_stale"
        if state["operation"]:
            return "task_account_operation"
        if state["cause"]:
            return "task_account_inconsistent"
        return None

    async def _references(self, conn: Any, row: Any) -> None:
        owner = row["user_id"]
        if not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM conversations WHERE id=$1 AND user_id=$2)",
            row["conversation_id"],
            owner,
        ):
            raise ObservationBlocked("task_not_owned")
        messages = await conn.fetch(
            "SELECT id,role FROM messages WHERE id IN ($1::uuid,$2::uuid) "
            "AND conversation_id=$3 AND user_id=$4 FOR UPDATE",
            row["user_message_id"],
            row["result_message_id"],
            row["conversation_id"],
            owner,
        )
        roles = {r["id"]: r["role"] for r in messages}
        if roles != {row["user_message_id"]: "user", row["result_message_id"]: "assistant"}:
            raise ObservationBlocked("task_not_owned")

    async def _attempt(self, conn: Any, row: Any, binding: Claim | None = None) -> Any:
        attempt = await conn.fetchrow(
            "SELECT task_id,epoch,worker_id,outcome,ended_at FROM task_attempts "
            "WHERE task_id=$1 AND epoch=$2 FOR UPDATE",
            row["id"],
            row["lease_epoch"],
        )
        if (
            attempt is None
            or attempt["worker_id"] != row["lease_owner"]
            or attempt["outcome"] != "running"
            or attempt["ended_at"] is not None
            or (binding is not None and attempt["worker_id"] != binding.worker_id)
        ):
            raise ObservationBlocked("task_attempt_not_current")
        return attempt

    async def _worker_row(self, conn: Any, task_id: uuid.UUID, epoch: int, binding: Claim) -> Any:
        if not isinstance(binding, Claim) or (task_id, epoch) != (binding.task_id, binding.epoch):
            raise ObservationBlocked("task_attempt_not_current")
        row = await self._locked_for_epoch(conn, task_id, epoch)
        if row is None:
            raise LeaseLost("lease lost")
        if (
            row["user_id"] != binding.user_id
            or row["conversation_id"] != binding.conversation_id
            or row["user_message_id"] != binding.user_message_id
            or row["result_message_id"] != binding.result_message_id
        ):
            raise ObservationBlocked("task_not_owned")
        await self._attempt(conn, row, binding)
        await self._references(conn, row)
        if self._origin(row) != binding.origin:
            raise ObservationBlocked()
        if not await conn.fetchval(f"SELECT {_LEASE_HELD} FROM tasks WHERE id=$1", task_id):
            raise LeaseLost("lease lost")
        return row

    @asynccontextmanager
    async def _worker(
        self,
        task_id: uuid.UUID,
        epoch: int,
        binding: Claim,
        *,
        allow_interrupted: bool = False,
    ) -> AsyncIterator[tuple[Any, Any]]:
        """Actual content/requeue authority, followed by a distinct control exit."""
        try:
            async with control_transaction(
                self._pool, refusals=(TaskError, InferenceInterrupted)
            ) as conn:
                await self._inference_before_task(conn, task_id)
                account, state = await self._account(conn, binding.user_id)
                reason = self._refusal(account, state, binding.origin)
                if reason:
                    raise _PermissionRefused(reason)  # latch before another await
                try:
                    async with guard_content_write(
                        conn, CapturedGeneration(binding.user_id, binding.origin.generation)
                    ):
                        row = await self._worker_row(conn, task_id, epoch, binding)
                        if row["cancel_requested_at"] is not None:
                            raise _PermissionRefused("cancelled")
                        require_effect()
                        shared = invocation.get()
                        if not allow_interrupted and shared is not None and shared.call_failed:
                            raise InferenceInterrupted(INFERENCE_UNRESOLVED)
                        yield conn, row
                        require_effect()
                        if not allow_interrupted and shared is not None and shared.call_failed:
                            raise InferenceInterrupted(INFERENCE_UNRESOLVED)
                        if not await conn.fetchval(
                            "SELECT clock_timestamp() < $1::timestamptz", row["lease_expires_at"]
                        ):
                            raise LeaseLost("lease lost")
                except WriteGuardRefusal:
                    # An invariant/protocol failure is not a semantic account refusal.
                    raise ObservationBlocked() from None
        except _PermissionRefused as refusal:
            status = await self.finish_refusal(task_id, epoch, binding=binding, reason=refusal.code)
            raise TaskControlled(status) from None

    async def _material_possible(self, conn: Any, row: Any) -> bool:
        """M=YES/UNKNOWN -> True; read failures propagate, never become UNKNOWN."""
        attempts = await conn.fetch(
            "SELECT a.epoch,a.worker_id,a.outcome,a.ended_at,a.started_at,a.execution_started_at "
            "FROM task_attempts a JOIN tasks t ON t.id=a.task_id "
            "WHERE t.id=$1 AND t.user_id=$2 ORDER BY a.epoch",
            row["id"],
            row["user_id"],
        )
        operations = await conn.fetch(
            "SELECT o.epoch,o.effect_class,o.outcome,o.started_at,o.completed_at "
            "FROM task_operations o JOIN tasks t ON t.id=o.task_id "
            "WHERE t.id=$1 AND t.user_id=$2",
            row["id"],
            row["user_id"],
        )
        markers = await conn.fetch(
            "SELECT e.kind FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE t.id=$1 AND t.user_id=$2 "
            "AND e.kind IN ('execution_started','operation_started','operation_finished')",
            row["id"],
            row["user_id"],
        )
        # All material outcomes count. Inconsistent associations, nonmaterial rows,
        # dangling markers and gaps also mean possible, never evidence of absence.
        if operations or markers or row["attempt_count"] != 0:
            return True
        if len(attempts) != row["lease_epoch"] or any(
            a["epoch"] != index for index, a in enumerate(attempts, start=1)
        ):
            return True
        for attempt in attempts:
            current_running = row["status"] == "running" and attempt["epoch"] == row["lease_epoch"]
            if (
                attempt["execution_started_at"] is not None
                or not isinstance(attempt["started_at"], datetime)
                or (attempt["outcome"] == "running") != current_running
                or (attempt["ended_at"] is None) != current_running
                or (current_running and attempt["worker_id"] != row["lease_owner"])
            ):
                return True
        return False

    async def _control_end(
        self, conn: Any, row: Any, status: TaskStatus, code: str, *, end_attempt: bool
    ) -> None:
        """T: no message decryption, body/trace copy or attempt archival."""
        allowed = {
            INFERENCE_UNRESOLVED,
            "cancelled",
            "account_suspended",
            "account_unavailable",
            "task_generation_stale",
            "task_account_operation",
            "task_account_inconsistent",
            "uncertain_effect",
        }
        if code not in allowed or status not in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_ATTENTION,
        }:
            raise ObservationBlocked()
        require_effect()
        await self._close_inference(
            conn, row, "cancelled" if status is TaskStatus.CANCELLED else "requested"
        )
        possible = await self._material_possible(conn, row)
        metadata: dict[str, Any] = {
            "terminal_status": _MESSAGE_STATUS[status],
            "terminal_reason": code,
        }
        inference = await self._inference_order(conn, row)
        if inference is not None and inference.possible:
            metadata["inference_dispatch_possible"] = True
        if possible:
            metadata["material_effect_possible"] = True
        now = await conn.fetchval("SELECT clock_timestamp()")
        if (
            await conn.fetchval(
                "UPDATE messages SET status=$4, metadata=metadata || $5::jsonb "
                "WHERE id=$1 AND user_id=$2 AND conversation_id=$3 "
                "AND jsonb_typeof(metadata)='object' RETURNING id",
                row["result_message_id"],
                row["user_id"],
                row["conversation_id"],
                _MESSAGE_STATUS[status],
                json.dumps(metadata),
            )
            != row["result_message_id"]
        ):
            raise ObservationBlocked()
        if end_attempt:
            if (
                await conn.fetchval(
                    "UPDATE task_attempts SET outcome=$4,terminal_code=$5,ended_at=$6 "
                    "WHERE task_id=$1 AND epoch=$2 AND worker_id=$3 "
                    "AND outcome='running' AND ended_at IS NULL RETURNING id",
                    row["id"],
                    row["lease_epoch"],
                    row["lease_owner"],
                    _ATTEMPT_OUTCOME[status],
                    code,
                    now,
                )
                is None
            ):
                raise ObservationBlocked()
        payload = self._seal({"status": status.value, "code": code})
        seq = await conn.fetchval(
            "UPDATE tasks SET status=$2,terminal_code=$3,finished_at=$4,updated_at=$4, "
            "lease_owner=NULL,lease_expires_at=NULL,event_seq=event_seq+1 "
            "WHERE id=$1 AND user_id=$5 AND status=$6 RETURNING event_seq",
            row["id"],
            status.value,
            code,
            now,
            row["user_id"],
            row["status"],
        )
        if seq is None:
            raise ObservationBlocked()
        if (
            await conn.fetchval(
                "INSERT INTO task_events(task_id,seq,kind,payload_ciphertext,created_at) "
                "VALUES($1,$2,'finished',$3,$4) RETURNING seq",
                row["id"],
                seq,
                payload,
                now,
            )
            != seq
        ):
            raise ObservationBlocked()

    async def finish_refusal(
        self, task_id: uuid.UUID, epoch: int, *, binding: Claim, reason: str
    ) -> TaskStatus:
        """Fresh W/O/control authority; the latch is evidence, not write permission."""
        if reason not in {
            "account_suspended",
            "cancelled",
            "account_unavailable",
            "task_generation_stale",
            "task_account_operation",
            "task_account_inconsistent",
        }:
            raise ObservationBlocked()
        async with control_transaction(self._pool, refusals=(LeaseLost,)) as conn:
            await self._inference_before_task(conn, task_id)
            account, state = await self._account(conn, binding.user_id)
            row = await self._worker_row(conn, task_id, epoch, binding)
            current = self._refusal(account, state, binding.origin)
            status, code = await self._disposition(conn, row, current, reason)
            await self._control_end(conn, row, status, code, end_attempt=True)
            if not await conn.fetchval(
                "SELECT clock_timestamp() < $1::timestamptz", row["lease_expires_at"]
            ):
                raise LeaseLost("lease lost")
            return status

    async def _disposition(
        self, conn: Any, row: Any, current: str | None, latched: str | None = None
    ) -> tuple[TaskStatus, str]:
        if current == "account_suspended" or latched == "account_suspended":
            return TaskStatus.FAILED, "account_suspended"
        if row["cancel_requested_at"] is not None:
            return TaskStatus.CANCELLED, "cancelled"
        reason = current or latched
        if reason in (None, "cancelled"):
            raise ObservationBlocked()
        if await self._material_possible(conn, row):
            return TaskStatus.NEEDS_ATTENTION, "uncertain_effect"
        return TaskStatus.FAILED, reason

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
        require_effect()
        row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
        if row is None:
            raise ObservationBlocked()
        await self._close_inference(conn, row, "stale" if outcome == "lost" else "requested")
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
        await _disclose_regeneration(conn, metadata, row)
        if status is not TaskStatus.COMPLETED:
            # Tool names only (no targets): later turns are told which action
            # may already have happened, whether the task needs attention,
            # was cancelled or failed after starting it (uncertain_effect_note).
            started = await self._started_tools(conn, row["id"])
            if started or status is TaskStatus.NEEDS_ATTENTION:
                metadata["uncertain_tools"] = started
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

    async def _account_suspended(self, conn: Any, user_id: uuid.UUID) -> bool:
        return bool(
            await conn.fetchval(
                "SELECT status = 'suspended' FROM entitlement_accounts WHERE user_id = $1",
                user_id,
            )
        )

    async def _started_tools(self, conn: Any, task_id: uuid.UUID) -> list[str]:
        """Material tools this task started (names only), oldest first."""
        return [
            r["tool_name"]
            for r in await conn.fetch(
                "SELECT tool_name FROM task_operations WHERE task_id = $1 "
                "GROUP BY tool_name ORDER BY min(started_at)",
                task_id,
            )
        ]

    async def _end_suspended(self, conn: Any, row: Any, epoch: int) -> None:
        await self._end_attempt(
            conn, row["id"], epoch, "failed_terminal", "account_suspended", row["result_message_id"]
        )
        await self._finish(conn, row, TaskStatus.FAILED, "account_suspended")

    async def _locked_for_epoch(self, conn: Any, task_id: uuid.UUID, epoch: int) -> Any:
        """Lock the task for this attempt, or return None if it no longer owns it.

        The epoch alone is not ownership: a worker paused past its lease must
        not write even if no takeover has happened yet. The expiry is checked
        by a separate statement once the lock is held, because a locking
        query's own predicates are evaluated before any wait for the lock.
        """
        row = await conn.fetchrow(
            "SELECT * FROM tasks WHERE id = $1 AND lease_epoch = $2 AND status = 'running' "
            "FOR UPDATE",
            task_id,
            epoch,
        )
        if row is None or not await conn.fetchval(
            f"SELECT {_LEASE_HELD} FROM tasks WHERE id = $1", task_id
        ):
            return None
        return row

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
        request_canonical: str | None = None,
        captured_generation: CapturedGeneration | None = None,
    ) -> AcceptedTask:
        """Atomically record the conversation turn and its task.

        If the conversation's previous task ended between this transaction's
        conflict and the follow-up lookup, acceptance is retried once rather
        than surfacing the stale uniqueness violation.
        NEW work requires the original server capture; a replay needs no capture.
        The same capture is retained through the bounded busy-conflict retry.
        """
        return await self._accept_with_stage(
            stage=None,
            user_id=user_id,
            conversation_id=conversation_id,
            new_conversation_title=new_conversation_title,
            pipeline=pipeline,
            user_message=user_message,
            task_input=task_input,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            assistant_model=assistant_model,
            max_attempts=max_attempts,
            request_canonical=request_canonical,
            captured_generation=captured_generation,
        )

    async def _accept_with_stage(
        self,
        *,
        stage: _AcceptanceStage | None,
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
        request_canonical: str | None = None,
        captured_generation: CapturedGeneration | None = None,
    ) -> AcceptedTask:
        """Atomically record the conversation turn and its task.

        If the conversation's previous task ended between this transaction's
        conflict and the follow-up lookup, acceptance is retried once rather
        than surfacing the stale uniqueness violation.
        NEW work requires the original server capture; a replay needs no capture.
        The same capture is retained through the bounded busy-conflict retry.
        """
        if self._resource_inputs is not getattr(self._memory, "_resource_inputs", None):
            raise ObservationBlocked("resource_composition_refused")
        if self._resource_inputs is not None:
            if request_canonical is None:
                raise IdempotencyConflict("canonical request required by resource composition")
            return await self._resource_inputs._accept_with_stage(
                stage=stage,
                user_id=user_id,
                conversation_id=conversation_id,
                new_conversation_title=new_conversation_title,
                pipeline=pipeline,
                user_message=user_message,
                task_input=task_input,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
                assistant_model=assistant_model,
                max_attempts=max_attempts,
                request_canonical=request_canonical,
                captured_generation=captured_generation,
            )
        for retry in (False, True):
            try:
                return await self._accept_once(
                    stage=stage,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    new_conversation_title=new_conversation_title,
                    pipeline=pipeline,
                    user_message=user_message,
                    task_input=task_input,
                    request_hash=request_hash,
                    idempotency_key=idempotency_key,
                    assistant_model=assistant_model,
                    max_attempts=max_attempts,
                    request_canonical=request_canonical,
                    captured_generation=captured_generation,
                )
            except _ActiveTaskEnded as ended:
                if retry:
                    raise ended.violation from None
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def _accept_connection(self, *, staging: bool) -> AsyncIterator[Any]:
        async with self._pool.acquire() as conn:
            # A staged acceptance must own the top-level COMMIT. Releasing a
            # savepoint inside a pool setup/caller transaction is not durability.
            if staging and conn.is_in_transaction():
                raise ObservationBlocked("acceptance_transaction_owner_refused")
            yield conn

    async def _accept_once(
        self,
        *,
        stage: _AcceptanceStage | None = None,
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
        request_canonical: str | None = None,
        captured_generation: CapturedGeneration | None = None,
    ) -> AcceptedTask:
        """One acceptance transaction (see :meth:`accept`).

        Nothing is acknowledged before this commits. A replay with the same key
        and request returns the existing task; the same key with a different
        request raises :class:`IdempotencyConflict`.
        """
        if idempotency_key is not None:
            existing = await self._by_key(user_id, idempotency_key, request_hash, request_canonical)
            if existing is not None:
                return existing
        stage_attempted = False
        try:
            sealed_input = await self._accept_input(
                user_id, task_input, request_canonical, captured_generation
            )
            assert captured_generation is not None
            generation = captured_generation.generation
            # Account SHARE precedes every content lock/write on this connection,
            # and survives guard scope exit through the actual transaction commit.
            async with (
                self._accept_connection(staging=stage is not None) as conn,
                conn.transaction(isolation="read_committed"),
                guard_content_write(conn, captured_generation),
            ):
                pending = await self._insert_accepted(
                    conn,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    new_conversation_title=new_conversation_title,
                    pipeline=pipeline,
                    user_message=user_message,
                    sealed_input=sealed_input,
                    request_hash=request_hash,
                    idempotency_key=idempotency_key,
                    assistant_model=assistant_model,
                    max_attempts=max_attempts,
                    generation=generation,
                )
                if stage is not None:
                    stage_attempted = True
                    await stage(conn, pending)
        except WriteGuardRefusal:
            if stage_attempted:
                raise
            # A concurrent matching acceptance may have committed after our first
            # lookup but before a fence refused NEW work. Resolve it only after
            # rollback; never refresh the capture or authorize another turn.
            if idempotency_key is not None:
                existing = await self._by_key(
                    user_id, idempotency_key, request_hash, request_canonical
                )
                if existing is not None:
                    return existing
            raise
        except asyncpg.UniqueViolationError as exc:
            if stage_attempted:
                raise
            # The transaction rolled back entirely, including the messages.
            if idempotency_key is not None:
                existing = await self._by_key(
                    user_id, idempotency_key, request_hash, request_canonical
                )
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
                raise _ActiveTaskEnded(exc) from None
            raise
        return pending

    async def _accept_input(
        self,
        user_id: uuid.UUID,
        task_input: dict[str, Any],
        request_canonical: str | None,
        captured_generation: CapturedGeneration | None,
    ) -> dict[str, Any]:
        # Validate after replay, not before it; even mutated captures must fail.
        account_id = getattr(captured_generation, "account_id", None)
        generation = getattr(captured_generation, "generation", None)
        if (
            type(captured_generation) is not CapturedGeneration
            or not isinstance(account_id, uuid.UUID)
            or type(generation) is not int
            or not 0 <= generation <= 2**63 - 1
            or account_id != user_id
        ):
            raise WriteGuardRefusal("invalid_capture")
        if self._composition is not None:
            from orchestrator.tasks.composition import TaskComposition

            if not self._cover_new_inference or type(self._composition) is not TaskComposition:
                raise ObservationBlocked("inference_acceptor_refused")
            await self._composition.preflight()
        value = {
            **task_input,
            "origin_account_id": str(account_id),
            "origin_account_generation": generation,
            "summary_origin_account_id": str(account_id),
            "summary_origin_account_generation": generation,
        }
        value.pop("_inference_recovery_version", None)
        if self._cover_new_inference:
            value["_inference_recovery_version"] = 1
        if request_canonical is not None:
            value["_request"] = request_canonical
        return value

    async def _insert_accepted(
        self,
        conn: Any,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        new_conversation_title: str | None,
        pipeline: str,
        user_message: str,
        sealed_input: dict[str, Any],
        request_hash: str,
        idempotency_key: str | None,
        assistant_model: str | None,
        max_attempts: int,
        generation: int,
        task_id: uuid.UUID | None = None,
    ) -> AcceptedTask:
        """Private transaction join; result is PENDING until caller's known COMMIT.

        Caller owns original capture, account guard, sealed origins and preflight.
        No transaction/pool/replay/commit handling here. The optional ID is allocated
        by the trusted input-batch writer, never an HTTP parameter.
        """
        if not conn.is_in_transaction():
            raise WriteGuardRefusal("transaction_required")
        if self._cover_new_inference:
            binding = self._composition
            if binding is not None:
                binding.validate()
            await acceptor_preflight(
                conn,
                expected=binding.acceptor_role if binding else None,
                other=binding.worker_role if binding else None,
            )
        if conversation_id is None:
            conversation = await self._memory.create_conversation(
                user_id=user_id, pipeline=pipeline, title=new_conversation_title, conn=conn
            )
            conversation_id = conversation["id"]
        else:
            owner = await conn.fetchval(
                "SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE", conversation_id
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
        # Strictly later placeholder preserves history ordering in one clock tick.
        await conn.execute(
            "UPDATE messages SET created_at = GREATEST(clock_timestamp(),"
            "(SELECT created_at FROM messages WHERE id=$2)+interval '1 microsecond') WHERE id=$1",
            result_row["id"],
            user_row["id"],
        )
        task_id = await conn.fetchval(
            "INSERT INTO tasks(id,user_id,conversation_id,user_message_id,result_message_id,"
            "input_ciphertext,input_version,idempotency_key,request_hash,max_attempts) "
            "VALUES(COALESCE($1,gen_random_uuid()),$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
            task_id,
            user_id,
            conversation_id,
            user_row["id"],
            result_row["id"],
            self._seal(sealed_input),
            CHAT_INPUT_VERSION,
            idempotency_key,
            request_hash,
            max_attempts,
        )
        if not isinstance(task_id, uuid.UUID):
            raise TaskError("invalid_task_identity")
        if self._cover_new_inference:
            await conn.execute(
                "INSERT INTO public.task_inference_recovery(task_id,account_id,account_generation,protocol_version) VALUES($1,$2,$3,1)",
                task_id,
                user_id,
                generation,
            )
        await self._append_event(conn, task_id, "accepted")
        return AcceptedTask(
            task_id, conversation_id, user_row["id"], result_row["id"], TaskStatus.QUEUED, True
        )

    async def task_for_key(self, user_id: uuid.UUID, idempotency_key: str) -> TaskSnapshot | None:
        """The task this account's key created, if any (no payload comparison)."""
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content,
                   (SELECT count(*) FROM task_attempts a WHERE a.task_id = t.id
                    AND a.epoch < t.lease_epoch AND a.execution_started_at IS NOT NULL
                    AND a.outcome IN ('lost', 'failed_retryable')) AS regeneration_count
            FROM tasks t JOIN messages m ON m.id = t.result_message_id
            WHERE t.user_id = $1 AND t.idempotency_key = $2
            """,
            user_id,
            idempotency_key,
        )
        return self._snapshot_from(row) if row is not None else None

    async def find_by_key(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
        request_canonical: str | None = None,
    ) -> AcceptedTask | None:
        """The task this account's key created, if any (conflict if the request differs)."""
        return await self._by_key(user_id, idempotency_key, request_hash, request_canonical)

    async def _by_key(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
        request_canonical: str | None = None,
    ) -> AcceptedTask | None:
        # Canonical replay can refresh a rotated digest below. Validate wiring
        # before that possible write, without imposing NEW origin/admission.
        if self._composition is not getattr(self._memory, "_task_composition", None):
            raise ObservationBlocked("inference_composition_refused")
        if self._composition is not None:
            self._composition.validate()
        if self._resource_inputs is not getattr(self._memory, "_resource_inputs", None):
            raise ObservationBlocked("resource_composition_refused")
        if self._resource_inputs is not None:
            return await self._resource_inputs.find_by_key(
                user_id, idempotency_key, request_hash, request_canonical
            )
        row = await self._pool.fetchrow(
            "SELECT * FROM tasks WHERE user_id = $1 AND idempotency_key = $2",
            user_id,
            idempotency_key,
        )
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            # The digest key (auth pepper) may have been rotated since this
            # task was accepted: compare the canonical request kept in the
            # encrypted input before declaring a conflict.
            stored = self._open(row["input_ciphertext"]).get("_request")
            if request_canonical is None or stored != request_canonical:
                raise IdempotencyConflict("idempotency key reused for a different request")
            await self._pool.execute(
                "UPDATE tasks SET request_hash = $2 WHERE id = $1", row["id"], request_hash
            )
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

    @asynccontextmanager
    async def _composed_writer(self) -> AsyncIterator[Any]:
        if self._composition is None:
            yield self._pool
            return
        self._composition.validate()
        if self._inference is None:
            raise ObservationBlocked("inference_principal_refused")
        async with control_transaction(self._pool) as conn:
            await self._inference.preflight(conn)
            yield conn

    async def mark_woken(self, task_id: uuid.UUID) -> int:
        """Record a wake-up about to be enqueued; returns its sequence for the job id."""
        async with self._composed_writer() as conn:
            return int(
                await conn.fetchval(
                    "UPDATE tasks SET wake_seq = wake_seq + 1, last_wake_at = now() "
                    "WHERE id = $1 RETURNING wake_seq",
                    task_id,
                )
            )

    async def _quarantine_hold_candidates(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Bounded circular page; no locks retained across financial settlement."""
        require_effect()
        after = self._quarantine_hold_after
        async with control_transaction(self._pool) as conn:
            if self._composition is not None:
                self._composition.validate()
                assert self._inference is not None
                await self._inference.preflight(conn)
            await conn.execute(
                "SET LOCAL lock_timeout='250ms'; SET LOCAL statement_timeout='1000ms'"
            )
            rows = await conn.fetch(
                _QUARANTINE_HOLDS + " ORDER BY CASE WHEN $1::uuid IS NULL THEN false "
                "ELSE (t.id,r.id)<=($1,$2::uuid) END, t.id,r.id LIMIT 50",
                None if after is None else after[0],
                None if after is None else after[1],
            )
        return [(r["task_id"], r["reservation_id"]) for r in rows]

    async def _observe_quarantine_hold(
        self, task_id: uuid.UUID, reservation_id: uuid.UUID
    ) -> int | None:
        """Authenticate original closed obligation; release ALL locks before settle."""
        require_effect()
        if self._inference is None or self._inference.pool is not self._pool:
            raise ObservationBlocked("inference_coverage_unavailable")
        async with control_transaction(self._pool) as conn:
            await conn.execute(
                "SET LOCAL lock_timeout='250ms'; SET LOCAL statement_timeout='1000ms'"
            )
            row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            if row is None or not self._covered(row):
                raise ObservationBlocked("task_not_owned")
            origin = self._origin(row)
            observation = await self._inference.lock(
                conn, row["user_id"], origin.generation, task_id, int(row["lease_epoch"])
            )
            # READ COMMITTED statements AFTER waits; do not trust pre-lock row data.
            current = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            if (
                current is None
                or not self._covered(current)
                or self._origin(current) != origin
                or current["lease_epoch"] != row["lease_epoch"]
                or current["user_id"] != row["user_id"]
            ):
                raise ObservationBlocked("inference_origin_unavailable")
            hold = await conn.fetchrow(
                _QUARANTINE_HOLDS + " AND t.id=$1 AND r.id=$2", task_id, reservation_id
            )
            # A concurrent pass's acknowledged settlement simply removes pending work.
            if hold is None:
                return None
            identity = observation.identity
            if (
                not observation.possible
                or observation.closed is not True
                or identity is None
                or identity.financial_scope_id != hold["scope_id"]
                or identity.account_id != hold["user_id"]
                or not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM entitlement_accounts WHERE user_id=$1)",
                    hold["user_id"],
                )
            ):
                raise ObservationBlocked("inference_obligation_unavailable")
            require_effect()
            amount = int(hold["reserved_microusd"])
        require_effect()
        return amount

    async def due_for_wakeup(
        self, *, limit: int = 50, rewake_after_s: float = 60.0
    ) -> list[tuple[uuid.UUID, int, uuid.UUID]]:
        """Find queued or expired-lease tasks that need a wake-up, and record it.

        A task woken within ``rewake_after_s`` is skipped so a queue backlog is
        not flooded with duplicates; duplicates would be harmless, since the
        claim decides ownership.
        """
        async with self._composed_writer() as conn:
            rows = await conn.fetch(
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
            RETURNING t.id, t.wake_seq, t.user_id
            """,
                limit,
                rewake_after_s,
            )
        return [(row["id"], int(row["wake_seq"]), row["user_id"]) for row in rows]

    # ------------------------------------------------------------------ #
    # Worker lifecycle
    # ------------------------------------------------------------------ #

    async def claim(self, task_id: uuid.UUID, *, worker_id: str, lease_s: float) -> Claim | None:
        result = await self._claim(task_id, worker_id=worker_id, lease_s=lease_s)
        if isinstance(result, ControlledClaim):
            if result.inference_quarantine:
                raise TaskQuarantined(result.status)
            raise TaskControlled(result.status)
        return result

    async def _claim(
        self, task_id: uuid.UUID, *, worker_id: str, lease_s: float
    ) -> Claim | ControlledClaim | None:
        """Claim a due task, taking over an expired lease if needed.

        Returns ``None`` for a duplicate delivery, a task that is not due, or a
        task this claim resolved instead of running (cancelled, exhausted, or
        stopped at ``needs_attention`` because a lost attempt may have caused a
        material effect).
        """
        async with control_transaction(self._pool, refusals=(LeaseLost,)) as conn:
            owner = await conn.fetchval("SELECT user_id FROM tasks WHERE id=$1", task_id)
            if owner is None:
                raise ObservationBlocked("task_not_owned")
            await self._inference_before_task(conn, task_id)
            account, state = await self._account(conn, owner)
            row = await self._locked(conn, task_id)
            if row is None or row["user_id"] != owner:
                raise ObservationBlocked("task_not_owned")
            if row["status"] not in {s.value for s in ACTIVE_STATUSES}:
                raise ObservationBlocked("task_already_terminal")
            origin = self._origin(row)
            refusal = self._refusal(account, state, origin)
            # Recompute due/expiry AFTER any lock wait, not against query snapshot.
            due = await conn.fetchval(
                "SELECT (status='queued' AND next_wakeup_at<=clock_timestamp()) OR "
                "(status='running' AND lease_expires_at<clock_timestamp()) FROM tasks WHERE id=$1",
                task_id,
            )
            if not due:
                if refusal:
                    # A live foreign writer stays untouched, but a known account
                    # refusal is not ordinary None/lost-hold settlement authority.
                    raise ObservationBlocked(refusal)
                return None
            row = dict(row)
            row["lease_expired"] = row["status"] == "running"
            await self._references(conn, row)
            if row["status"] == "running":
                await self._attempt(conn, row)
            newer = account is not None and account["content_generation"] > origin.generation
            if newer:
                status, code = await self._disposition(conn, row, refusal, "task_generation_stale")
                await self._control_end(
                    conn, row, status, code, end_attempt=row["status"] == "running"
                )
                # Signal only AFTER commit/release, below; not ordinary None/settlement.
                controlled = status
            else:
                controlled = None
                if refusal:
                    raise ObservationBlocked(refusal)
            if controlled is not None:
                # Returning a closed result cannot be mistaken for a Claim or None.
                return ControlledClaim(controlled)
            async with guard_content_write(conn, CapturedGeneration(owner, origin.generation)):
                pass  # account locks survive this scope through COMMIT
            if row["cancel_requested_at"] is None and await self._quarantine(conn, row):
                return ControlledClaim(TaskStatus.NEEDS_ATTENTION, inference_quarantine=True)
            if row["lease_expired"]:
                epoch = int(row["lease_epoch"])
                # Suspension wins over a cancel or a retry, here as at
                # publication: a suspended account's task ends failed.
                if row["cancel_requested_at"] is not None:
                    await self._control_end(
                        conn, row, TaskStatus.CANCELLED, "cancelled", end_attempt=True
                    )
                    return ControlledClaim(TaskStatus.CANCELLED)
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
                await self._control_end(
                    conn, row, TaskStatus.CANCELLED, "cancelled", end_attempt=False
                )
                return ControlledClaim(TaskStatus.CANCELLED)
            elif await self._material_started(conn, task_id):
                # Defensive: a queued task never carries operations, because
                # every retry path checks them first.
                await self._finish(conn, row, TaskStatus.NEEDS_ATTENTION, "uncertain_effect")
                return None
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                await self._finish(conn, row, TaskStatus.FAILED, "interrupted")
                return None

            # Also fence a queued ended pre-dispatch attempt whose control was
            # left open. Due/origin/owner checks above precede this staged close.
            await self._close_inference(conn, row, "stale")

            claimed = await conn.fetchrow(
                """
                UPDATE tasks
                SET status = 'running', lease_epoch = lease_epoch + 1,
                    lease_owner = $2, lease_expires_at = now() + make_interval(secs => $3),
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
            await self._append_event(conn, task_id, "attempt_started", {"epoch": epoch})
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
                worker_id=worker_id,
                origin=origin,
                inference_covered=self._covered(row),
            )

    async def heartbeat(
        self, task_id: uuid.UUID, epoch: int, *, lease_s: float, binding: Claim
    ) -> Heartbeat:
        """Extend this attempt's lease; raises :class:`LeaseLost` when fenced out."""
        async with control_transaction(self._pool, refusals=(LeaseLost,)) as conn:
            account, state = await self._account(conn, binding.user_id)
            row = await self._worker_row(conn, task_id, epoch, binding)
            refusal = self._refusal(account, state, binding.origin)
            if refusal or row["cancel_requested_at"] is not None:
                # Observation-only: stop the engine, do not renew a refused lease.
                return Heartbeat(
                    cancel_requested=row["cancel_requested_at"] is not None,
                    account_suspended=refusal == "account_suspended",
                    refusal=refusal,
                )
            row = await conn.fetchrow(
                f"""
                UPDATE tasks
                SET lease_expires_at = clock_timestamp() + make_interval(secs => $2),
                    updated_at = now()
                WHERE id = $1
                RETURNING cancel_requested_at, {_SUSPENDED} AS account_suspended
                """,
                task_id,
                lease_s,
            )
            await conn.execute(
                "UPDATE task_attempts SET heartbeat_at = now() WHERE task_id = $1 AND epoch = $2",
                task_id,
                epoch,
            )
            return Heartbeat(
                cancel_requested=row["cancel_requested_at"] is not None,
                account_suspended=bool(row["account_suspended"]),
            )

    async def observe_attempt(self, binding: Claim) -> Heartbeat:
        """One coherent read-only snapshot. Never a lease or mutation grant.

        In particular no account/task row lock can wait behind this invocation's
        completion. Terminal state may belong to another caller; report only loss
        of the observed running lease, not a completion receipt.
        """
        row = await self._pool.fetchrow(
            "SELECT t.status,t.lease_epoch,t.lease_owner,"
            "t.lease_expires_at>clock_timestamp() AS live,t.cancel_requested_at,"
            "a.worker_id,a.outcome,a.ended_at,ea.user_id AS account_id,"
            "ea.status AS account_status,ea.content_generation,"
            "EXISTS(SELECT 1 FROM account_deletions WHERE account_id=t.user_id "
            "AND closed_at IS NULL) AS operation,"
            "EXISTS(SELECT 1 FROM account_suspension_causes WHERE account_id=t.user_id) AS cause "
            "FROM tasks t JOIN conversations c ON c.id=t.conversation_id AND c.user_id=t.user_id "
            "JOIN messages u ON u.id=t.user_message_id AND u.user_id=t.user_id "
            "AND u.conversation_id=t.conversation_id AND u.role='user' "
            "JOIN messages m ON m.id=t.result_message_id AND m.user_id=t.user_id "
            "AND m.conversation_id=t.conversation_id AND m.role='assistant' "
            "LEFT JOIN task_attempts a ON a.task_id=t.id AND a.epoch=t.lease_epoch "
            "LEFT JOIN entitlement_accounts ea ON ea.user_id=t.user_id "
            "WHERE t.id=$1 AND t.user_id=$2 AND t.conversation_id=$3 "
            "AND t.user_message_id=$4 AND t.result_message_id=$5",
            binding.task_id,
            binding.user_id,
            binding.conversation_id,
            binding.user_message_id,
            binding.result_message_id,
        )
        if row is None:
            raise ObservationBlocked("task_not_owned")
        if (
            row["status"] != "running"
            or row["lease_epoch"] != binding.epoch
            or row["lease_owner"] != binding.worker_id
            or not row["live"]
        ):
            raise LeaseLost("lease lost")
        if (
            row["worker_id"] != binding.worker_id
            or row["outcome"] != "running"
            or row["ended_at"] is not None
        ):
            raise ObservationBlocked("task_attempt_not_current")
        account = (
            None
            if row["account_id"] is None
            else {
                "status": row["account_status"],
                "content_generation": row["content_generation"],
            }
        )
        reason = self._refusal(account, row, binding.origin)
        return Heartbeat(
            cancel_requested=row["cancel_requested_at"] is not None,
            account_suspended=reason == "account_suspended",
            refusal=reason,
        )

    async def write_partial(
        self, task_id: uuid.UUID, epoch: int, *, content: str, delta_seq: int, binding: Claim
    ) -> None:
        """Persist streamed content of this attempt's generation under the fence."""
        async with self._worker(task_id, epoch, binding) as (conn, row):
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
        binding: Claim,
    ) -> uuid.UUID:
        """Record a material operation before it runs (the effect fence).

        Refuses unless this attempt holds the lease with at least
        ``min_lease_margin_s`` left, no cancel has been requested and the
        account is not suspended. The gap between this commit and the actual
        external call can be narrowed, not closed.
        """
        async with self._worker(task_id, epoch, binding) as (conn, _):
            # Read under the lock, so a cancel, suspension or lapse that
            # committed while this waited is seen.
            row = await conn.fetchrow(
                f"""
                SELECT cancel_requested_at, {_SUSPENDED} AS account_suspended,
                       lease_expires_at > clock_timestamp() + make_interval(secs => $2)
                           AS lease_margin_ok
                FROM tasks WHERE id = $1
                """,
                task_id,
                min_lease_margin_s,
            )
            if row["cancel_requested_at"] is not None:
                raise EffectRefused("cancel_requested")
            if row["account_suspended"]:
                raise EffectRefused("account_suspended")
            if not row["lease_margin_ok"]:
                raise EffectRefused("lease_margin")
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
            await self._append_event(
                conn,
                task_id,
                "operation_started",
                {"tool": tool_name, "epoch": epoch, "operation_id": str(operation_id)},
            )
            return operation_id

    async def record_compute_scope(
        self, task_id: uuid.UUID, epoch: int, scope_id: uuid.UUID, *, binding: Claim
    ) -> None:
        """Link this attempt's account compute scope before any reservation.

        Preparation (memory retrieval) can reserve under the scope before the
        attempt is counted; a lost attempt's holds are found through it.
        """
        async with self._worker(task_id, epoch, binding) as (conn, row):
            # Preparation can call the embedding provider: a cancel or
            # suspension that committed since the claim stops it first.
            if await self._account_suspended(conn, row["user_id"]):
                raise ExecutionRefused("account_suspended")
            if row["cancel_requested_at"] is not None:
                raise ExecutionRefused("cancel_requested")
            await conn.execute(
                "UPDATE task_attempts SET compute_scope_id = $3 WHERE task_id = $1 AND epoch = $2",
                task_id,
                epoch,
                scope_id,
            )

    async def begin_execution(
        self,
        task_id: uuid.UUID,
        epoch: int,
        scope_id: uuid.UUID,
        *,
        prompt_version: str | None = None,
        binding: Claim,
    ) -> int:
        """Mark this attempt as executing; only now does it count as an attempt.

        Records the attempt's account compute scope (for settling its holds if
        it is lost) and the system prompt version it runs with, and returns the
        number of attempts that have executed. Claiming, preparing or
        reconciling consume no retry, so a database outage during preparation
        cannot exhaust the task. A cancel or suspension that committed since
        the claim raises :class:`ExecutionRefused` before any inference.
        """
        async with self._worker(task_id, epoch, binding) as (conn, row):
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                raise LeaseLost("no attempts left")  # defensive: the claim checks this
            if await conn.fetchval(f"SELECT {_SUSPENDED} FROM tasks WHERE id = $1", task_id):
                raise ExecutionRefused("account_suspended")
            if row["cancel_requested_at"] is not None:
                raise ExecutionRefused("cancel_requested")
            count = await conn.fetchval(
                "UPDATE tasks SET attempt_count = attempt_count + 1, updated_at = now() "
                "WHERE id = $1 RETURNING attempt_count",
                task_id,
            )
            await conn.execute(
                "UPDATE task_attempts SET compute_scope_id = $3, execution_started_at = now(), "
                "prompt_version = $4 WHERE task_id = $1 AND epoch = $2",
                task_id,
                epoch,
                scope_id,
                prompt_version,
            )
            await self._append_event(
                conn, task_id, "execution_started", {"epoch": epoch, "attempt": int(count)}
            )
            return int(count)

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
        self,
        task_id: uuid.UUID,
        epoch: int,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        binding: Claim,
    ) -> int:
        """Append a lifecycle event from the running attempt, under the fence."""
        async with self._worker(task_id, epoch, binding) as (conn, _):
            return await self._append_event(conn, task_id, kind, payload)

    async def refreshed_pages(self, task_id: uuid.UUID) -> set[str]:
        """Keys of the pages this task has refreshed in any attempt (#475)."""
        rows = await self._pool.fetch(
            "SELECT payload_ciphertext FROM task_events "
            "WHERE task_id = $1 AND kind = 'page_refreshed'",
            task_id,
        )
        keys: set[str] = set()
        for row in rows:
            payload = self._open(row["payload_ciphertext"])
            if isinstance(payload, dict) and isinstance(payload.get("key"), str):
                keys.add(payload["key"])
        return keys

    async def pinned_page(
        self, claim: Claim, key: str, legacy_key: str | None = None, *, conn: Any = None
    ) -> uuid.UUID | None:
        """Exact task-bound snapshot; legacy identity-less markers fail closed."""
        rows = await (conn if conn is not None else self._pool).fetch(
            "SELECT e.payload_ciphertext FROM task_events e JOIN tasks t ON t.id = e.task_id "
            "WHERE e.task_id = $1 AND t.user_id = $2 AND t.conversation_id = $3 "
            "AND e.kind = 'page_refreshed' ORDER BY e.seq",
            claim.task_id,
            claim.user_id,
            claim.conversation_id,
        )
        for row in rows:
            payload = self._open(row["payload_ciphertext"])
            if payload.get("key") == key:
                if not payload.get("snapshot_id"):
                    raise ExecutionRefused("snapshot_unavailable")
                return uuid.UUID(payload["snapshot_id"])
            if legacy_key is not None and payload.get("key") == legacy_key:
                raise ExecutionRefused("snapshot_unavailable")
        return None

    async def pin_page(self, conn: Any, claim: Claim, key: str, snapshot_id: uuid.UUID) -> None:
        """Same-connection snapshot publication, after account/conversation locks."""
        # Caller already owns its account-first content transaction. This adds
        # original task currency; it is not a generation recapture or a commit.
        async with guard_content_write(
            conn, CapturedGeneration(claim.user_id, claim.origin.generation)
        ):
            row = await self._worker_row(conn, claim.task_id, claim.epoch, claim)
        if row["user_id"] != claim.user_id or row["conversation_id"] != claim.conversation_id:
            raise ExecutionRefused("snapshot_owner_mismatch")
        if row["cancel_requested_at"] is not None:
            raise ExecutionRefused("cancel_requested")
        if await self._account_suspended(conn, claim.user_id):
            raise ExecutionRefused("account_suspended")
        if await self.pinned_page(claim, key, conn=conn) is not None:
            raise ExecutionRefused("snapshot_already_pinned")
        await self._append_event(
            conn,
            claim.task_id,
            "page_refreshed",
            {"key": key, "snapshot_id": str(snapshot_id), "epoch": claim.epoch},
        )
        # Encryption/event work must not carry a formerly valid lease past
        # expiry. Cancellation cannot change while this task lock is held.
        if not await conn.fetchval(f"SELECT {_LEASE_HELD} FROM tasks WHERE id = $1", claim.task_id):
            raise LeaseLost("lease lost")

    async def finish_operation(self, operation_id: uuid.UUID, *, outcome: str) -> None:
        """Record what a material operation did.

        Deliberately not fenced: a stale attempt's evidence that an effect
        happened is still evidence.
        """
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError(f"invalid operation outcome {outcome!r}")
        async with self._pool.acquire() as conn, conn.transaction():
            task_id = await conn.fetchval(
                "SELECT task_id FROM task_operations WHERE id = $1", operation_id
            )
            if task_id is None:
                return
            # Same order as begin_operation: task before operation. Intentionally
            # no lease check: even stale workers may contribute truthful evidence.
            if await self._locked(conn, task_id) is None:
                return
            row = await conn.fetchrow(
                "UPDATE task_operations SET outcome = $2, completed_at = now() "
                "WHERE id = $1 AND outcome = 'started' RETURNING epoch, tool_name",
                operation_id,
                outcome,
            )
            if row is not None:
                await self._append_event(
                    conn,
                    task_id,
                    "operation_finished",
                    {
                        "operation_id": str(operation_id),
                        "tool": row["tool_name"],
                        "epoch": row["epoch"],
                        "outcome": outcome,
                    },
                )

    async def operation_matches(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        epoch: int,
        operation_id: Any,
        name: Any,
        outcome: Any,
    ) -> bool:
        """Owner/current-generation operation evidence, not a live UUID assertion."""
        if not all(isinstance(value, str) for value in (operation_id, name, outcome)):
            return False
        try:
            identity = uuid.UUID(operation_id)
        except ValueError:
            return False
        return bool(
            await self._pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM task_operations o JOIN tasks t ON t.id = o.task_id "
                "WHERE t.id = $1 AND t.user_id = $2 AND t.content_generation = $3 "
                "AND o.id = $4 AND o.epoch = $3 AND o.tool_name = $5 AND o.outcome = $6 "
                "AND o.completed_at IS NOT NULL AND NOT "
                + _SUSPENDED.replace("tasks.", "t.")
                + ")",
                task_id,
                user_id,
                epoch,
                identity,
                name,
                outcome,
            )
        )

    async def operation_progress(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        epoch: int,
        operation_id: str,
        name: str,
        outcome: str,
        seq: int,
        *,
        after_seq: int,
    ) -> TaskEvent | None:
        """Exact owner-scoped durable progress proof, including consumed sequences."""
        if not await self.operation_matches(user_id, task_id, epoch, operation_id, name, outcome):
            return None
        row = await self._pool.fetchrow(
            "SELECT e.seq, e.kind, e.payload_ciphertext, e.created_at "
            "FROM task_events e JOIN tasks t ON t.id = e.task_id "
            "WHERE e.task_id = $1 AND t.user_id = $2 AND t.content_generation = $3 "
            "AND e.seq = $4 AND e.kind = 'tool_result' AND NOT "
            + _SUSPENDED.replace("tasks.", "t."),
            task_id,
            user_id,
            epoch,
            seq,
        )
        if row is None:
            return None
        # Available-before projection may look across a page boundary, never
        # across a damaged durable gap between operation and result progress.
        count = await self._pool.fetchval(
            "SELECT count(*) FROM task_events WHERE task_id = $1 AND seq > $2 AND seq <= $3",
            task_id,
            after_seq,
            seq,
        )
        if count != seq - after_seq:
            return None
        payload = self._open(row["payload_ciphertext"])
        if any(
            payload.get(key) != value
            for key, value in (
                ("operation_id", operation_id),
                ("epoch", epoch),
                ("name", name),
                ("outcome", outcome),
                ("payload_state", "full"),
            )
        ):
            return None
        return TaskEvent(seq=int(row["seq"]), kind=row["kind"], payload=payload)

    async def complete(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        content: str,
        message_fields: dict[str, Any] | None = None,
        binding: Claim,
    ) -> TaskStatus:
        """Return only an acknowledged guarded result or control-only exit."""
        try:
            return await self._complete(
                task_id, epoch, content=content, message_fields=message_fields, binding=binding
            )
        except TaskControlled as controlled:
            return controlled.status

    async def _complete(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        content: str,
        message_fields: dict[str, Any] | None,
        binding: Claim,
    ) -> TaskStatus:
        fields = dict(message_fields or {})
        async with self._worker(task_id, epoch, binding, allow_interrupted=True) as (conn, row):
            shared = invocation.get()
            if shared is not None and shared.call_failed:
                if not await self._quarantine(conn, row):
                    raise ObservationBlocked("inference_coverage_unavailable")
                return TaskStatus.NEEDS_ATTENTION
            status = TaskStatus.COMPLETED
            metadata = dict(fields.pop("metadata", None) or {})
            metadata["terminal_status"] = _MESSAGE_STATUS[status]
            await _disclose_regeneration(conn, metadata, row)
            if status is TaskStatus.CANCELLED:
                # Cancelled rows leave history; keep any started action visible.
                started = await self._started_tools(conn, task_id)
                if started:
                    metadata["uncertain_tools"] = started
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
        binding: Claim,
    ) -> TaskStatus:
        """End this attempt unsuccessfully and apply the retry rules."""
        async with self._worker(task_id, epoch, binding, allow_interrupted=True) as (conn, row):
            if await self._quarantine(conn, row):
                return TaskStatus.NEEDS_ATTENTION
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
        self, task_id: uuid.UUID, epoch: int, *, delay_s: float, reason: str, binding: Claim
    ) -> TaskStatus | None:
        """Hand a claimed task back to the queue without consuming its attempt.

        Used when a precondition for safe admission (such as settling a lost
        attempt's holds) cannot be established yet. No execution began, so
        nothing counts against ``max_attempts``; if this write also fails, the
        lease simply expires and the next claim finds no executed attempt.
        """
        async with self._worker(task_id, epoch, binding, allow_interrupted=True) as (conn, row):
            if await self._quarantine(conn, row):
                return TaskStatus.NEEDS_ATTENTION
            await self._end_attempt(
                conn, task_id, epoch, "deferred", reason, row["result_message_id"]
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
                delay_s,
            )
            await self._append_event(conn, task_id, "attempt_deferred", {"epoch": epoch})

    async def defer_execution(
        self,
        task_id: uuid.UUID,
        epoch: int,
        *,
        delay_s: float,
        reason: str,
        max_wait_s: float,
        before_dispatch: bool = False,
        binding: Claim,
    ) -> bool | TaskStatus:
        """Re-queue an attempt the account refused admission, returning its attempt.

        A concurrency or rate refusal of the attempt's first reservation means
        no provider work started: the attempt is ended ``deferred``, its count
        is given back and the task waits for the slot instead of exhausting
        ``max_attempts``. Returns False, changing nothing, if any reservation
        was granted in the attempt's compute scope, a material operation was
        recorded, or the task has already waited ``max_wait_s`` since
        acceptance; the caller then applies the ordinary retry rules.

        ``before_dispatch``: the attempt failed while preparing, before it was
        counted, so no provider call can have been made; reservations from
        preparation (memory retrieval) do not prevent the deferral.
        """
        async with self._worker(task_id, epoch, binding, allow_interrupted=True) as (conn, row):
            if await self._quarantine(conn, row):
                return TaskStatus.NEEDS_ATTENTION
            check = await conn.fetchrow(
                """
                SELECT t.created_at > now() - make_interval(secs => $3)
                       AND NOT EXISTS (
                           SELECT 1 FROM task_operations o WHERE o.task_id = $1 AND o.epoch = $2)
                       AND ($4 OR NOT EXISTS (
                           SELECT 1 FROM entitlement_reservations r
                           WHERE r.scope_id = a.compute_scope_id)) AS admissible,
                       a.execution_started_at IS NOT NULL AS counted
                FROM tasks t JOIN task_attempts a ON a.task_id = t.id AND a.epoch = $2
                WHERE t.id = $1
                """,
                task_id,
                epoch,
                max_wait_s,
                before_dispatch,
            )
            if check is None or not check["admissible"]:
                return False
            await self._end_attempt(
                conn, task_id, epoch, "deferred", reason, row["result_message_id"]
            )
            await conn.execute(
                """
                UPDATE tasks
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    attempt_count = attempt_count - $3::int,
                    next_wakeup_at = now() + make_interval(secs => $2),
                    last_wake_at = NULL, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                delay_s,
                1 if check["counted"] else 0,
            )
            await self._append_event(
                conn, task_id, "attempt_deferred", {"epoch": epoch, "code": reason}
            )
            return True

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

    async def acknowledge_cancel(
        self, task_id: uuid.UUID, epoch: int, *, binding: Claim
    ) -> TaskStatus:
        """The running attempt observed a cancel request and stopped.

        A suspended account ends ``failed`` (``account_suspended``) instead.
        """
        return await self.finish_refusal(task_id, epoch, binding=binding, reason="cancelled")

    # ------------------------------------------------------------------ #
    # Owner-facing reads and control
    # ------------------------------------------------------------------ #

    async def request_cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> CancelOutcome:
        async with control_transaction(self._pool, refusals=(TaskNotFound,)) as conn:
            preliminary = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id=$1 AND user_id=$2", task_id, user_id
            )
            if preliminary is None:
                raise TaskNotFound("task not found")
            await self._inference_order(conn, preliminary)
            await self._account(conn, user_id)
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1 AND user_id = $2 FOR UPDATE", task_id, user_id
            )
            if row is None:
                raise TaskNotFound("task not found")
            status = TaskStatus(row["status"])
            if status is TaskStatus.QUEUED:
                await self._references(conn, row)
                await self._control_end(
                    conn, row, TaskStatus.CANCELLED, "cancelled", end_attempt=False
                )
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
        if self._resource_inputs is not getattr(self._memory, "_resource_inputs", None):
            raise ObservationBlocked("resource_composition_refused")
        if self._resource_inputs is not None:
            return await self._resource_inputs.delete_conversation(user_id, conversation_id)
        async with self._pool.acquire() as conn, conn.transaction():
            return await self._delete_conversation_rows(conn, user_id, conversation_id)

    async def _delete_conversation_rows(
        self, conn: Any, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> tuple[bool, uuid.UUID | None]:
        """Caller owns transaction; composed caller also owns account/journal guard."""
        owner = await conn.fetchval(
            "SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE", conversation_id
        )
        if owner != user_id:
            raise TaskNotFound("conversation not found")
        active = await conn.fetchrow(
            "SELECT * FROM tasks WHERE conversation_id = $1 AND status IN ('queued', 'running') FOR UPDATE",
            conversation_id,
        )
        if active is not None and active["status"] == TaskStatus.RUNNING.value:
            if active["cancel_requested_at"] is None:
                await conn.execute(
                    "UPDATE tasks SET cancel_requested_at = now(), updated_at = now() WHERE id = $1",
                    active["id"],
                )
                await self._append_event(conn, active["id"], "cancel_requested")
            return False, active["id"]
        if self._resource_inputs is not None:
            await self._resource_inputs._detach(conn, user_id, conversation_id)
        await conn.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
        return True, None

    async def snapshot(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskSnapshot | None:
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content,
                   (SELECT count(*) FROM task_attempts a WHERE a.task_id = t.id
                    AND a.epoch < t.lease_epoch AND a.execution_started_at IS NOT NULL
                    AND a.outcome IN ('lost', 'failed_retryable')) AS regeneration_count
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
            SELECT t.*, m.content AS result_content,
                   (SELECT count(*) FROM task_attempts a WHERE a.task_id = t.id
                    AND a.epoch < t.lease_epoch AND a.execution_started_at IS NOT NULL
                    AND a.outcome IN ('lost', 'failed_retryable')) AS regeneration_count
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
            regenerated_after_interruption=int(row["regeneration_count"]),
            cancel_requested=row["cancel_requested_at"] is not None
            and TaskStatus(row["status"]) in ACTIVE_STATUSES,
        )

    async def latest_for_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> TaskSnapshot | None:
        """The conversation's most recent task, active or finished."""
        row = await self._pool.fetchrow(
            """
            SELECT t.*, m.content AS result_content,
                   (SELECT count(*) FROM task_attempts a WHERE a.task_id = t.id
                    AND a.epoch < t.lease_epoch AND a.execution_started_at IS NOT NULL
                    AND a.outcome IN ('lost', 'failed_retryable')) AS regeneration_count
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
