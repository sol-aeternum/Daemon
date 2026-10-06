"""Durable task states and retry rules (docs/DURABLE_REQUEST_DESIGN.md §4).

Pure functions only: the SQL in ``orchestrator.tasks.store`` implements these
rules, and the property tests exercise them without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


ACTIVE_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})
TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.NEEDS_ATTENTION,
    }
)

_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.NEEDS_ATTENTION}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            # Re-queue after a retryable error, or a claim that takes over an
            # expired lease (running -> running with a new epoch).
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_ATTENTION,
        }
    ),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Whether ``current -> target`` is a permitted task transition."""
    return target in _TRANSITIONS.get(current, frozenset())


class RetryCause(StrEnum):
    #: The attempt's lease expired: crash, partition or a stalled worker.
    LOST = "lost"
    #: The attempt reported an error the compute layer marks retryable.
    RETRYABLE_ERROR = "retryable_error"
    #: The attempt reported a non-retryable error (policy, route, validation).
    TERMINAL_ERROR = "terminal_error"
    #: The attempt was denied capacity or budget. Slice 1 makes this terminal;
    #: DEC09 pause and auto-resume arrive in slice 4.
    CAPACITY = "capacity"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    status: TaskStatus
    terminal_code: str | None


def decide_after_attempt(
    *,
    cause: RetryCause,
    attempt_count: int,
    max_attempts: int,
    material_started: bool,
    error_code: str | None = None,
) -> RetryDecision:
    """Decide what an ended, uncompleted attempt leads to.

    The material-operation check guards every whole-attempt retry path, not
    only crash recovery: once a material tool may have run, regenerating from
    the original input could repeat it, so the task stops at
    ``needs_attention`` with its evidence preserved.
    """
    if attempt_count < 0 or max_attempts < 1:
        raise ValueError("attempt counters must be non-negative with a positive cap")
    if material_started:
        return RetryDecision(TaskStatus.NEEDS_ATTENTION, "uncertain_effect")
    if cause is RetryCause.TERMINAL_ERROR:
        return RetryDecision(TaskStatus.FAILED, error_code or "failed")
    if cause is RetryCause.CAPACITY:
        return RetryDecision(TaskStatus.FAILED, error_code or "capacity_unavailable")
    if attempt_count >= max_attempts:
        return RetryDecision(TaskStatus.FAILED, "interrupted")
    return RetryDecision(TaskStatus.QUEUED, None)


def retry_backoff_s(attempt_count: int, *, base_s: float = 5.0, cap_s: float = 120.0) -> float:
    """Delay before the next attempt after ``attempt_count`` attempts."""
    if attempt_count < 1:
        return 0.0
    return min(cap_s, base_s * (2 ** (attempt_count - 1)))
