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


# Fixed test user UUID used across benchmark harness scripts
TEST_USER_ID: uuid.UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")

# Additional tables to reset beyond the 7 core tables in
# cleanup_canonical_benchmark_state(). These are identified in
# wave0_state_reset_audit_v2.md Section 2.2 as missing from
# the production reset scope.
EXTENDED_RESET_TABLES = [
    "skill_consolidation_log",
    "skill_nudge_user_state",
]

# All tables that should reach zero-row state after a full reset.
# Includes the 7 core tables + 2 extended tables.
ALL_RESET_TABLES = [
    "conversations",
    "messages",
    "memories",
    "memory_extraction_log",
    "retrieval_log",
    "dream_log",
    "entities",
    "skill_consolidation_log",
    "skill_nudge_user_state",
]

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
    error: str | None = None


async def extended_cleanup_tables(pool: asyncpg.Pool) -> dict[str, int]:
    """
    Delete all TEST_USER_ID rows from the extended tables that are
    not covered by cleanup_canonical_benchmark_state().

    Returns dict of table_name → deleted_count.
    """
    deleted: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table in EXTENDED_RESET_TABLES:
            result = await conn.execute(
                f"DELETE FROM {table} WHERE user_id = $1", TEST_USER_ID
            )
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
        for table in ALL_RESET_TABLES:
            count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = $1", TEST_USER_ID
            )
            counts[table] = int(count)
    return counts


async def verify_zero_row_state(pool: asyncpg.Pool) -> tuple[bool, dict[str, int]]:
    """
    Check whether all benchmark tables have zero rows for TEST_USER_ID.

    Returns (all_zero, counts) where all_zero is True only when every
    table has 0 rows.
    """
    counts = await get_table_row_counts(pool)
    all_zero = all(v == 0 for v in counts.values())
    return all_zero, counts


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
    3. Post-reset zero-row verification

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
        from orchestrator.eval.runner import reset_canonical_benchmark
        summary = await reset_canonical_benchmark(
            pool, checkpoint_path, cleanup_redis=False  # We handle redis below
        )
        result.tables_cleared = dict(summary.tables_cleared)
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
    first_result = await full_reset_with_verification(
        pool, checkpoint_path, cleanup_redis=True
    )

    # Small delay to allow any remaining async tasks to settle
    await asyncio.sleep(0.5)

    second_result = await full_reset_with_verification(
        pool, checkpoint_path, cleanup_redis=True
    )

    return {
        "first_result": {
            "success": first_result.success,
            "tables_cleared": first_result.tables_cleared,
            "extended_tables_cleared": first_result.extended_tables_cleared,
            "total_rows_deleted": first_result.total_rows_deleted,
            "row_counts_after_reset": first_result.row_counts_after_reset,
            "all_zero": first_result.all_zero,
            "error": first_result.error,
        },
        "second_result": {
            "success": second_result.success,
            "tables_cleared": second_result.tables_cleared,
            "extended_tables_cleared": second_result.extended_tables_cleared,
            "total_rows_deleted": second_result.total_rows_deleted,
            "row_counts_after_reset": second_result.row_counts_after_reset,
            "all_zero": second_result.all_zero,
            "error": second_result.error,
        },
        "confirmed_clean": second_result.all_zero,
    }
