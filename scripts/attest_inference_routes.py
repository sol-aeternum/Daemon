"""Run one ZDR attestation check for monitored inference routes now.

Use at deploy time, before restarting the backend and worker onto a policy with
monitored approvals, so routes do not fail closed while waiting for the worker's
first scheduled check. Reads only public OpenRouter listings and the configured
inference policy; writes one row per monitored route to
``inference_route_attestations``.

Usage (inside the backend or worker container, or with DATABASE_URL and
DAEMON_INFERENCE_POLICY exported):

    python scripts/attest_inference_routes.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from orchestrator.config import get_settings  # noqa: E402
from orchestrator.database_url import apply_resolved_database_url  # noqa: E402
from orchestrator.entitlements import attestation  # noqa: E402
from orchestrator.entitlements.policy import load_inference_policy  # noqa: E402


async def main() -> int:
    settings = get_settings()
    apply_resolved_database_url(settings)
    if not settings.database_url:
        print("DATABASE_URL is not configured", file=sys.stderr)
        return 2
    routes = list(load_inference_policy().routes.values())
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=1)
    try:
        async with httpx.AsyncClient() as client:
            counts = await attestation.run_check(pool, client, routes)
        now = attestation.utcnow()
        status = {
            route.route_id: attestation.attestation_reasons(route, now=now) or "attested"
            for route in routes
            if attestation.is_monitored(route)
        }
    finally:
        await pool.close()
    print(json.dumps({"counts": counts, "routes": status}, indent=2, default=list))
    return 0 if counts.get("check_failed", 0) == 0 and counts.get("revoked", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
