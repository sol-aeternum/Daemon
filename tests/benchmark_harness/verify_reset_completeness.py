#!/usr/bin/env python3
"""
R4 targeted verification script for reset completeness.

Run this script to verify that the extended reset helper
correctly clears all benchmark tables including the previously
missing skill_consolidation_log and skill_nudge_user_state.

Usage:
    python -m tests.benchmark_harness.verify_reset_completeness

This script:
1. Performs a double-reset to catch any async write residue
2. Verifies all 9 target tables reach zero rows
3. Reports per-table counts and confirms all_zero state
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import dotenv  # noqa: E402

dotenv.load_dotenv()
os.environ["DATABASE_URL"] = "postgresql://daemon:daemon@127.0.0.1:5432/daemon"
os.environ["BENCHMARK_MODE"] = "1"

import asyncpg  # noqa: E402
from orchestrator.config import get_settings  # noqa: E402
from tests.benchmark_harness.reset_verify_helper import (  # noqa: E402
    double_reset_for_confirmation,
    get_table_row_counts,
    TEST_USER_ID,
)


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=3)
    checkpoint_path = (
        PROJECT_ROOT / "tests" / "benchmark_results" / "_r4_verify" / "checkpoint.json"
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("R4 Reset Completeness Verification")
    print("=" * 60)
    print(f"TEST_USER_ID: {TEST_USER_ID}")
    print(f"Database: {settings.database_url}")
    print()

    counts_before = await get_table_row_counts(pool)
    print("Row counts BEFORE reset:")
    for table, count in counts_before.items():
        marker = " <-- STILL HAS DATA" if count > 0 else ""
        print(f"  {table}: {count}{marker}")
    print()

    print("Running double-reset with verification...")
    result = await double_reset_for_confirmation(pool, checkpoint_path)

    print()
    print("First reset:")
    first = result["first_result"]
    print(f"  success: {first['success']}")
    print(f"  total_rows_deleted: {first['total_rows_deleted']}")
    print(f"  all_zero: {first['all_zero']}")
    print(f"  tables_cleared: {first['tables_cleared']}")
    print(f"  extended_tables_cleared: {first['extended_tables_cleared']}")
    print(f"  row_counts_after_reset: {first['row_counts_after_reset']}")
    if first["error"]:
        print(f"  error: {first['error']}")
    print()

    print("Second reset:")
    second = result["second_result"]
    print(f"  success: {second['success']}")
    print(f"  total_rows_deleted: {second['total_rows_deleted']}")
    print(f"  all_zero: {second['all_zero']}")
    print(f"  row_counts_after_reset: {second['row_counts_after_reset']}")
    if second["error"]:
        print(f"  error: {second['error']}")
    print()

    print("=" * 60)
    print("VERIFICATION RESULT")
    print("=" * 60)
    print(f"confirmed_clean: {result['confirmed_clean']}")
    print()

    if result["confirmed_clean"]:
        print("PASS: All tables reach zero after double-reset.")
        print()
        print("Key findings:")
        print(
            f"  - skill_consolidation_log cleared: {first['extended_tables_cleared'].get('skill_consolidation_log', 0)} rows deleted"
        )
        print(
            f"  - skill_nudge_user_state cleared: {first['extended_tables_cleared'].get('skill_nudge_user_state', 0)} rows deleted"
        )
        print(
            f"  - Total rows deleted across both resets: {first['total_rows_deleted'] + second['total_rows_deleted']}"
        )
    else:
        print("FAIL: Non-zero tables remain after double-reset.")
        print()
        non_zero = {k: v for k, v in second["row_counts_after_reset"].items() if v > 0}
        for table, count in non_zero.items():
            print(f"  {table}: {count} rows")
        print()
        print("This indicates residual async writes or incomplete reset coverage.")

    output_path = checkpoint_path.parent / "verification_result.json"
    output_data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "test_user_id": str(TEST_USER_ID),
        "counts_before": counts_before,
        "first_result": first,
        "second_result": second,
        "confirmed_clean": result["confirmed_clean"],
    }
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, default=str)
    print(f"\nFull result saved to: {output_path}")

    await pool.close()

    return 0 if result["confirmed_clean"] else 1


if __name__ == "__main__":
    import os

    sys.exit(asyncio.run(main()))
