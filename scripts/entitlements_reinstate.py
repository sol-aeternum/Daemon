#!/usr/bin/env python3
"""Reinstate an entitlement account suspended by a quote overrun.

A settlement above its reservation's quote suspends the account for operator
reconciliation (docs/SUBSCRIPTION_ARCHITECTURE.md). Investigate the overrun in
``entitlement_reservations`` first, then run:

    python scripts/entitlements_reinstate.py <user-uuid> --reason "<why>"
"""

import argparse
import asyncio
import sys
import uuid

import asyncpg

from orchestrator.database_url import resolve_database_url
from orchestrator.entitlements import EntitlementService


async def reinstate(user_id: uuid.UUID, reason: str) -> int:
    database_url = resolve_database_url()
    if not database_url:
        print("DATABASE_URL or complete POSTGRES_* settings are required")
        return 1
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    try:
        changed = await EntitlementService(pool).reinstate(user_id, reason=reason)
    finally:
        await pool.close()
    print("reinstated" if changed else "account already active")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reinstate a suspended entitlement account.")
    parser.add_argument("user_id", type=uuid.UUID)
    parser.add_argument("--reason", required=True, help="reconciliation note for the log")
    args = parser.parse_args()
    return asyncio.run(reinstate(args.user_id, args.reason))


if __name__ == "__main__":
    sys.exit(main())
