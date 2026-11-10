"""
Tests-only reset completeness helper.

Provides extended reset operations that supplement the production
reset_canonical_benchmark() with additional table coverage and
state verification for the benchmark harness.

This module is tests/harness ONLY — no production code under
orchestrator/memory/ is modified.

Identified gaps (from wave0_state_reset_audit_v2.md):
- skill_consolidation_log: not cleared by production reset
- skill_nudge_user_state: not cleared by production reset
- Redis keys: not cleaned when cleanup_redis=False in triple-run
- conversations.last_retrieved_memory_ids: column-level state not reset

These gaps cause:
- 10x row accumulation between runs (skill_* tables)
- FK violations from stale state
- Supersede failures from residual active memories
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from orchestrator.eval.bench_db import RETAINED_PROTECTION_TABLES


# Fixed test user UUID used across benchmark harness scripts
TEST_USER_ID: uuid.UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")

# Additional payload tables beyond the canonical content reset in
# cleanup_canonical_benchmark_state(). These are identified in
# wave0_state_reset_audit_v2.md Section 2.2 as missing from
# the production reset scope.
EXTENDED_RESET_QUERIES = {
    "skill_consolidation_log": ("DELETE FROM skill_consolidation_log WHERE user_id = $1"),
    "skill_nudge_user_state": "DELETE FROM skill_nudge_user_state WHERE user_id = $1",
}
EXTENDED_RESET_TABLES = list(EXTENDED_RESET_QUERIES)

# Payload tables must reach zero. Clocks, mutations, tombstones and erased
# source spans deliberately do not; they are reported separately below.
ALL_RESET_COUNT_QUERIES = {
    "conversations": "SELECT COUNT(*) FROM conversations WHERE user_id = $1",
    "messages": "SELECT COUNT(*) FROM messages WHERE user_id = $1",
    "memories": "SELECT COUNT(*) FROM memories WHERE user_id = $1",
    "memory_extraction_log": ("SELECT COUNT(*) FROM memory_extraction_log WHERE user_id = $1"),
    "retrieval_log": "SELECT COUNT(*) FROM retrieval_log WHERE user_id = $1",
    "dream_log": "SELECT COUNT(*) FROM dream_log WHERE user_id = $1",
    "entities": "SELECT COUNT(*) FROM entities WHERE user_id = $1",
    "skill_consolidation_log": ("SELECT COUNT(*) FROM skill_consolidation_log WHERE user_id = $1"),
    "skill_nudge_user_state": ("SELECT COUNT(*) FROM skill_nudge_user_state WHERE user_id = $1"),
    "memory_revisions": "SELECT COUNT(*) FROM memory_revisions WHERE user_id = $1",
    "memory_revision_segments": (
        "SELECT COUNT(*) FROM memory_revision_segments s JOIN memory_revisions r "
        "ON r.id=s.revision_id WHERE r.user_id=$1"
    ),
    "memory_dependencies": "SELECT COUNT(*) FROM memory_dependencies WHERE user_id = $1",
    "memory_sources": "SELECT COUNT(*) FROM memory_sources WHERE user_id = $1",
}
ALL_RESET_TABLES = list(ALL_RESET_COUNT_QUERIES)

# Redis key patterns that should be cleaned
REDIS_EXTRACT_PATTERNS = (
    "extract:*",
    "arq:job:extract:*",
    "arq:result:extract:*",
    "arq:retry:extract:*",
)


@dataclass
class ExtendedResetResult:
    """Result of an extended reset operation."""

    success: bool
    tables_cleared: dict[str, int] = field(default_factory=dict)
    extended_tables_cleared: dict[str, int] = field(default_factory=dict)
    redis_keys_deleted: int = 0
    total_rows_deleted: int = 0
    row_counts_after_reset: dict[str, int] = field(default_factory=dict)
    all_zero: bool = False
    retained_protection: dict[str, int] = field(default_factory=dict)
    owner_outcomes: dict[str, str] = field(default_factory=dict)
    error: str | None = None


async def extended_cleanup_tables(pool: asyncpg.Pool) -> dict[str, int]:
    """
    Delete all TEST_USER_ID rows from the extended tables that are
    not covered by cleanup_canonical_benchmark_state().

    Returns dict of table_name → deleted_count.
    """
    deleted: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table, delete_query in EXTENDED_RESET_QUERIES.items():
            result = await conn.execute(delete_query, TEST_USER_ID)
            count_str = result.split()[-1] if result else "0"
            deleted[table] = int(count_str)
    return deleted


async def get_table_row_counts(pool: asyncpg.Pool) -> dict[str, int]:
    """
    Return current row counts for all benchmark tables.

    Returns dict of table_name → row_count for TEST_USER_ID rows.
    """
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table, count_query in ALL_RESET_COUNT_QUERIES.items():
            count = await conn.fetchval(count_query, TEST_USER_ID)
            counts[table] = int(count)
    return counts


async def verify_zero_row_state(pool: asyncpg.Pool) -> tuple[bool, dict[str, int]]:
    """
    Check zero payload rows for TEST_USER_ID, not deletion-protection rows.

    Returns (all_zero, counts) where all_zero is True only when every
    table has 0 rows.
    """
    counts = await get_table_row_counts(pool)
    all_zero = all(v == 0 for v in counts.values())
    return all_zero, counts


async def retained_protection_counts(pool: asyncpg.Pool) -> dict[str, int]:
    """These intentionally nonzero rows are deletion protection, not reset debt."""
    async with pool.acquire() as conn:
        return {
            table: int(
                await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE user_id=$1",
                    TEST_USER_ID,
                )
            )
            for table in sorted(RETAINED_PROTECTION_TABLES)
        }


async def cleanup_runner_redis() -> dict[str, Any]:
    """
    Delete Redis keys used by the extraction pipeline.

    Clears keys matching the ARQ extraction job patterns so that no
    stale queued-extraction state can bleed into the next run.
    """
    try:
        import redis
    except Exception as exc:
        return {"keys_deleted": 0, "error": f"redis unavailable: {exc}"}

    def _scan_delete(client: Any, pattern: str) -> int:
        count = 0
        cursor = 0
        while True:
            scan_result = client.scan(cursor=cursor, match=pattern, count=500)
            if isinstance(scan_result, tuple) and len(scan_result) == 2:
                cursor, keys = scan_result
                if keys:
                    count += client.delete(*keys)
            else:
                break
            if cursor == 0:
                break
        return count

    try:
        from orchestrator.config import get_settings

        settings = get_settings()
        redis_url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        total = sum(_scan_delete(client, p) for p in REDIS_EXTRACT_PATTERNS)
        return {"keys_deleted": total, "error": None}
    except Exception as exc:
        return {"keys_deleted": 0, "error": str(exc)}


async def full_reset_with_verification(
    pool: asyncpg.Pool,
    checkpoint_path: Path,
    *,
    cleanup_redis: bool = True,
) -> ExtendedResetResult:
    """
    Perform a full extended reset with post-reset verification.

    This function supplements the production reset_canonical_benchmark()
    with:
    1. Extended table cleanup (skill_consolidation_log, skill_nudge_user_state)
    2. Optional Redis key cleanup
    3. Post-reset zero-payload verification and explicit retained-protection counts

    Parameters
    ----------
    pool
        An open asyncpg.Pool connected to the Daemon database.
    checkpoint_path
        Path to the runner checkpoint file. Will be deleted.
    cleanup_redis
        When True, delete Redis extraction/ARQ keys.

    Returns
    -------
    ExtendedResetResult
        Structured result with tables_cleared, row_counts_after_reset,
        all_zero flag, and any error.
    """
    result = ExtendedResetResult(success=True)

    # Step 1: Run production reset (import lazily to avoid patching issues)
    try:
        from orchestrator.eval.fact_harness import reset_canonical_benchmark

        summary = await reset_canonical_benchmark(
            pool,
            checkpoint_path,
            cleanup_redis=False,  # We handle redis below
        )
        result.tables_cleared = dict(summary.tables_cleared)
        result.owner_outcomes = dict(summary.owner_outcomes)
        result.total_rows_deleted = summary.total_rows_deleted
        if not summary.success:
            result.success = False
            result.error = f"production reset failed: {summary.error}"
            return result
    except Exception as exc:
        result.success = False
        result.error = f"production reset failed: {exc}"
        return result

    # Step 2: Extended table cleanup (skill_consolidation_log, skill_nudge_user_state)
    try:
        result.extended_tables_cleared = await extended_cleanup_tables(pool)
        result.total_rows_deleted += sum(result.extended_tables_cleared.values())
    except Exception as exc:
        result.success = False
        result.error = f"extended cleanup failed: {exc}"
        return result

    # Step 3: Redis cleanup (if requested)
    if cleanup_redis:
        try:
            redis_result = await cleanup_runner_redis()
            result.redis_keys_deleted = redis_result.get("keys_deleted", 0)
        except Exception:
            # Redis cleanup is best-effort; continue even if it fails
            pass

    # Step 4: Verify zero-row state
    try:
        all_zero, counts = await verify_zero_row_state(pool)
        result.row_counts_after_reset = counts
        result.all_zero = all_zero
        result.retained_protection = await retained_protection_counts(pool)
    except Exception as exc:
        result.success = False
        result.error = f"verification query failed: {exc}"
        return result

    return result


async def double_reset_for_confirmation(
    pool: asyncpg.Pool,
    checkpoint_path: Path,
) -> dict[str, Any]:
    """
    Perform two consecutive resets to confirm state reaches zero.

    This is useful for R5 empirical verification: if residual state
    survives a single reset, running reset twice should clear any
    late-arriving async writes.

    Returns a dict with:
    - first_result: ExtendedResetResult from first reset
    - second_result: ExtendedResetResult from second reset
    - confirmed_clean: True if second reset shows all_zero
    """
    first_result = await full_reset_with_verification(pool, checkpoint_path, cleanup_redis=True)

    if first_result.success and first_result.all_zero:
        # Explicit confirmation only after acknowledged, verified completion.
        await asyncio.sleep(0.5)
        second_result = await full_reset_with_verification(
            pool, checkpoint_path, cleanup_redis=True
        )
    else:
        # Do not replay a failed/uncertain purge or erase unexplained leftovers.
        second_result = ExtendedResetResult(success=False, error="confirmation_not_attempted")

    return {
        "first_result": {
            "success": first_result.success,
            "tables_cleared": first_result.tables_cleared,
            "extended_tables_cleared": first_result.extended_tables_cleared,
            "total_rows_deleted": first_result.total_rows_deleted,
            "row_counts_after_reset": first_result.row_counts_after_reset,
            "all_zero": first_result.all_zero,
            "retained_protection": first_result.retained_protection,
            "owner_outcomes": first_result.owner_outcomes,
            "error": first_result.error,
        },
        "second_result": {
            "success": second_result.success,
            "tables_cleared": second_result.tables_cleared,
            "extended_tables_cleared": second_result.extended_tables_cleared,
            "total_rows_deleted": second_result.total_rows_deleted,
            "row_counts_after_reset": second_result.row_counts_after_reset,
            "all_zero": second_result.all_zero,
            "retained_protection": second_result.retained_protection,
            "owner_outcomes": second_result.owner_outcomes,
            "error": second_result.error,
        },
        "confirmed_clean": second_result.all_zero,
    }
