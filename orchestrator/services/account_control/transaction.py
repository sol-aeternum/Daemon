"""Connection ownership for generation-fenced task/control transactions.

No retries or reconciliation writes. A lost COMMIT response is unknown; native
release transfers ownership before its first await (#540).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from orchestrator.tasks._invocation import block, require_effect


class ObservationBlocked(Exception):
    """An internal observation, never a terminal reason or retry authority."""

    def __init__(self, code: str = "task_observation_indeterminate") -> None:
        super().__init__(code)
        self.code = code


class TransactionUnavailable(ObservationBlocked):
    def __init__(self, *, attempted: bool, acknowledged: bool) -> None:
        super().__init__(
            "task_committed_result_unavailable"
            if acknowledged
            else "task_transition_indeterminate"
            if attempted
            else "task_observation_indeterminate"
        )
        self.commit_attempted = attempted
        self.commit_acknowledged = acknowledged


@asynccontextmanager
async def control_transaction(
    pool: Any, *, refusals: tuple[type[Exception], ...] = ()
) -> AsyncIterator[Any]:
    """Retain phase evidence through commit, cancellation and native release.

    Body refusals propagate only after acknowledged rollback and release. Cleanup
    failure is not proof of rollback and cannot authorize a second transaction.
    This adds no deadline/grace to the enclosing caller's budget.
    """
    conn = None
    tx = None
    attempted = acknowledged = False
    try:
        require_effect()
        conn = await pool.acquire()
        tx = conn.transaction(isolation="read_committed")
        await tx.start()
        require_effect()
        yield conn
        require_effect()
        attempted = True
        await tx.commit()
        acknowledged = True
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        release_conn, conn = conn, None
        await pool.release(release_conn)
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
    except BaseException as original:
        cleaned = False
        if conn is not None:
            if (
                not attempted
                and tx is not None
                and not isinstance(original, asyncio.CancelledError)
            ):
                try:
                    await tx.rollback()
                    release_conn, conn = conn, None
                    await pool.release(release_conn)
                    cleaned = True
                except asyncio.CancelledError as cancel:
                    original = cancel
                except Exception:
                    pass
            if conn is not None:
                # terminate is only local cleanup, never rollback evidence. No
                # reclaim after entering the driver's shielded release above.
                owned, conn = conn, None
                try:
                    owned.terminate()
                except Exception:
                    pass
        if isinstance(original, asyncio.CancelledError):
            block("task_transition_indeterminate")
            original.task_commit_attempted = attempted  # type: ignore[attr-defined]
            original.task_commit_acknowledged = acknowledged  # type: ignore[attr-defined]
            raise original
        if not isinstance(original, Exception):
            raise original
        if cleaned and isinstance(original, (ObservationBlocked, *refusals)):
            raise original
        block(
            "task_committed_result_unavailable" if acknowledged else "task_transition_indeterminate"
        )
        raise TransactionUnavailable(attempted=attempted, acknowledged=acknowledged) from original
