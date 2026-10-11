"""Bounded identity retention; no account deletion or address-based ownership inference."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

MAX_BATCH_SIZE = 1000
TERMINAL_RETENTION = timedelta(days=7)
AUDIT_RETENTION = timedelta(days=90)

# Fixed internal SQL identifiers, never request inputs. Explicit terminal guards
# protect active unexpired invites even when legacy consumed metadata is anomalous.
_TABLES = (
    ("identity_audit_log", "created_at", "created_at <= $1", AUDIT_RETENTION),
    (
        "email_challenges",
        "COALESCE(consumed_at, expires_at)",
        "(consumed_at IS NOT NULL OR expires_at <= $1)",
        TERMINAL_RETENTION,
    ),
    (
        "google_nonce_challenges",
        "COALESCE(consumed_at, expires_at)",
        "(consumed_at IS NOT NULL OR expires_at <= $1)",
        TERMINAL_RETENTION,
    ),
    (
        "signup_invites",
        "COALESCE(terminal_at, expires_at)",
        "(status <> 'active' OR expires_at <= $1)",
        TERMINAL_RETENTION,
    ),
)


async def sweep_identity_records(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    *,
    now: datetime | None = None,
    limit: int = MAX_BATCH_SIZE,
) -> dict[str, int]:
    """One <=1000-row batch/table; independent commits make partial failure repeat-safe.

    ``now`` is an internal controlled-clock test seam, not a public/runtime input.
    Production captures one database clock for the invocation. Absolute timedeltas
    avoid session-timezone/DST interval arithmetic. Errors/timeouts propagate.
    """
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError("identity retention batch must be between 1 and 1000")
    if now is None:
        now = await conn.fetchval("SELECT clock_timestamp()")
    if now is None or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("identity retention clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    counts: dict[str, int] = {}
    for table, anchor, terminal, retention in _TABLES:
        # These names/expressions are a closed constant inventory above.
        predicate = f"{terminal} AND {anchor} <= $2"
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            await conn.execute("SET LOCAL lock_timeout = '1s'")
            count = await conn.fetchval(
                f"""
                WITH candidates AS (
                    SELECT id FROM {table}
                    WHERE {predicate}
                    ORDER BY {anchor}, id
                    LIMIT $3 FOR UPDATE SKIP LOCKED
                ), removed AS (
                    DELETE FROM {table} WHERE id IN (SELECT id FROM candidates)
                      AND {predicate}
                    RETURNING id
                )
                SELECT count(*) FROM removed
                """,
                now,
                now - retention,
                limit,
            )
        if not isinstance(count, int):
            raise RuntimeError("identity retention count unavailable")
        counts[table] = count
    return counts
