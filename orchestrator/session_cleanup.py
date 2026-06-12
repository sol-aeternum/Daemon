"""Session cleanup background job.

Deletes sessions where:
  - refresh_expires_at < NOW() - grace  (stale expired)
  - revoked_at IS NOT NULL AND revoked_at < NOW() - grace  (stale revoked)

Active, unexpired, recently expired, and recently revoked sessions are preserved.
Consumed-but-not-expired rows are preserved until expiry+grace to support reuse detection
(Decision 20) because the expiry check is on refresh_expires_at, not refresh_consumed_at.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_MAX_DELETE_FRACTION = 0.5
SAFETY_MIN_TOTAL_SESSIONS = 20
SESSION_CLEANUP_ADVISORY_LOCK_ID = 987654321


def validate_session_cleanup_inputs(
    grace_days: int,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
) -> None:
    if grace_days <= 0:
        raise ValueError("session cleanup grace_days must be a positive integer")
    if max_delete_fraction <= 0 or max_delete_fraction > 1:
        raise ValueError("session cleanup max_delete_fraction must be > 0 and <= 1")


async def lock_session_cleanup(conn: Any) -> None:
    """Serialize session cleanup with refresh-token rotation in one transaction."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1)",
        SESSION_CLEANUP_ADVISORY_LOCK_ID,
    )


async def cleanup_stale_sessions(
    db_pool: asyncpg.Pool | None,
    grace_days: int,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
) -> int:
    """Delete stale sessions from the database.

    Args:
        db_pool: PostgreSQL connection pool.
        grace_days: Number of days after expiry/revocation before deletion.
        max_delete_fraction: Abort when a cleanup run would delete more than
            this fraction of the sessions table, once the table has enough rows
            for the guard to be meaningful.

    Returns:
        Number of sessions deleted.
    """
    validate_session_cleanup_inputs(grace_days, max_delete_fraction)

    if db_pool is None:
        return 0

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await lock_session_cleanup(conn)
            counts = await conn.fetchrow(
                """
                WITH candidates AS (
                    SELECT id
                    FROM sessions
                    WHERE
                        refresh_expires_at < NOW() - ($1 * INTERVAL '1 day')
                        OR (
                            revoked_at IS NOT NULL
                            AND revoked_at < NOW() - ($1 * INTERVAL '1 day')
                        )
                )
                SELECT
                    (SELECT COUNT(*) FROM candidates) AS candidate_count,
                    (SELECT COUNT(*) FROM sessions) AS total_count
                """,
                grace_days,
            )
            candidate_count = int(counts["candidate_count"]) if counts is not None else 0
            total_count = int(counts["total_count"]) if counts is not None else 0

            if (
                total_count >= SAFETY_MIN_TOTAL_SESSIONS
                and candidate_count / total_count > max_delete_fraction
            ):
                logger.critical(
                    "Session cleanup aborted: candidate_count=%d total_count=%d max_delete_fraction=%.3f",
                    candidate_count,
                    total_count,
                    max_delete_fraction,
                )
                raise RuntimeError(
                    "session cleanup aborted: candidate delete fraction exceeds safety limit"
                )

            deleted: int = await conn.fetchval(
                """
                WITH deleted AS (
                    DELETE FROM sessions
                    WHERE
                        refresh_expires_at < NOW() - ($1 * INTERVAL '1 day')
                        OR (
                            revoked_at IS NOT NULL
                            AND revoked_at < NOW() - ($1 * INTERVAL '1 day')
                        )
                    RETURNING id
                )
                SELECT COUNT(*) FROM deleted
                """,
                grace_days,
            )

    return deleted or 0


async def _session_cleanup_loop(
    db_pool: asyncpg.Pool,
    grace_days: int,
    interval_seconds: int,
    shutdown_event: asyncio.Event,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
) -> None:
    validate_session_cleanup_inputs(grace_days, max_delete_fraction)
    try:
        logger.info(
            "Session cleanup task starting: grace_days=%d, interval_seconds=%d",
            grace_days,
            interval_seconds,
        )

        while True:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                deleted = await cleanup_stale_sessions(
                    db_pool,
                    grace_days,
                    max_delete_fraction,
                )
                if deleted > 0:
                    logger.info("Session cleanup completed: %d stale sessions deleted", deleted)
                else:
                    logger.debug("Session cleanup completed: no stale sessions found")
            except Exception:
                logger.warning("Session cleanup run failed", exc_info=True)

    except asyncio.CancelledError:
        logger.debug("Session cleanup loop cancelled")
        raise
    finally:
        logger.info("Session cleanup task stopped")


async def start_session_cleanup_task(
    db_pool: asyncpg.Pool,
    grace_days: int,
    interval_seconds: int,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
) -> tuple[asyncio.Task[None], asyncio.Event]:
    """Start the periodic session cleanup background task.

    Args:
        db_pool: PostgreSQL connection pool.
        grace_days: Grace period in days after expiry/revocation before deletion.
        interval_seconds: Seconds between cleanup runs.
        max_delete_fraction: Safety threshold for one cleanup run.

    Returns:
        Tuple of (task, shutdown_event). The caller must set shutdown_event
        and await task when the application is shutting down.
    """
    validate_session_cleanup_inputs(grace_days, max_delete_fraction)
    shutdown_event = asyncio.Event()

    async def _run() -> None:
        await _session_cleanup_loop(
            db_pool,
            grace_days,
            interval_seconds,
            shutdown_event,
            max_delete_fraction,
        )

    task = asyncio.create_task(_run())
    return task, shutdown_event
