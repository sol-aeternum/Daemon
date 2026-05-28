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

import asyncpg

logger = logging.getLogger(__name__)


async def cleanup_stale_sessions(
    db_pool: asyncpg.Pool | None,
    grace_days: int,
) -> int:
    """Delete stale sessions from the database.

    Args:
        db_pool: PostgreSQL connection pool.
        grace_days: Number of days after expiry/revocation before deletion.

    Returns:
        Number of sessions deleted.
    """
    if db_pool is None:
        return 0

    deleted: int = await db_pool.fetchval(
        """
        WITH deleted AS (
            DELETE FROM sessions
            WHERE
                refresh_expires_at < NOW() - ($1 || ' days')::interval
                OR (revoked_at IS NOT NULL AND revoked_at < NOW() - ($1 || ' days')::interval)
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
) -> None:
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
                deleted = await cleanup_stale_sessions(db_pool, grace_days)
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
) -> tuple[asyncio.Task[None], asyncio.Event]:
    """Start the periodic session cleanup background task.

    Args:
        db_pool: PostgreSQL connection pool.
        grace_days: Grace period in days after expiry/revocation before deletion.
        interval_seconds: Seconds between cleanup runs.

    Returns:
        Tuple of (task, shutdown_event). The caller must set shutdown_event
        and await task when the application is shutting down.
    """
    shutdown_event = asyncio.Event()

    async def _run() -> None:
        await _session_cleanup_loop(db_pool, grace_days, interval_seconds, shutdown_event)

    task = asyncio.create_task(_run())
    return task, shutdown_event
