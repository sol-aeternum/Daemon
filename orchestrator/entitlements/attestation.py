"""ZDR attestation for monitored inference route approvals.

A ``monitored`` route (``approval_mode`` in the inference policy) has no calendar
expiry. It stays approved while this module confirms that what it was approved
against still holds, and is revoked when that changes:

* the exact model and pinned provider endpoint must stay in OpenRouter's public ZDR
  endpoint listing (revocation reason ``left_zdr_listing``);
* the pinned provider's published data policy must keep the values recorded in
  the route's ``zdr_baseline`` (revocation reason ``provider_policy_changed:<key>``).

Each check appends one row per monitored route to ``inference_route_attestations``
(migration 043). Admission reads an in-process snapshot of those rows:

* a revocation is sticky for the approved baseline. Re-approval is an operator act
  that changes the baseline (for example the review date), never an automatic
  recovery;
* a route whose last confirmation is older than :data:`STALE_AFTER` fails closed,
  so an unmonitored route is an unapproved route;
* before the snapshot has loaded at all, every monitored route fails closed.

A failed or unreadable check (network error, changed metadata format, missing
provider) records ``check_failed`` and revokes nothing; only staleness can then
close the route. The check sends no credentials and no route-specific data: it
fetches two public listings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from orchestrator.entitlements.policy import RoutePolicy

logger = logging.getLogger(__name__)

ZDR_LISTING_URL: Final[str] = "https://openrouter.ai/api/v1/endpoints/zdr"
PROVIDERS_URL: Final[str] = "https://openrouter.ai/api/frontend/v1/all-providers"

#: Operator decision (3 October 2026): a route with no confirmation for this long
#: fails closed until a check succeeds again.
STALE_AFTER: Final[timedelta] = timedelta(hours=72)

#: How often each process reloads the attestation snapshot from the database.
REFRESH_INTERVAL_S: Final[float] = 60.0

FETCH_TIMEOUT_S: Final[float] = 30.0

OUTCOMES: Final[frozenset[str]] = frozenset({"attested", "revoked", "check_failed"})


def is_monitored(route: RoutePolicy) -> bool:
    return route.approval_mode == "monitored" and route.zdr_baseline is not None


def baseline_fingerprint(route: RoutePolicy) -> str:
    """Identity of what was approved. Any change makes a new, unattested baseline.

    Covers the route, exact model, pinned provider, the baseline data policy and the
    operator review date, so re-approval after a revocation is an explicit config
    change, never a silent recovery.
    """
    baseline = route.zdr_baseline
    payload = {
        "route_id": route.route_id,
        "model": route.model,
        "provider_only": list(route.transport.provider_only or ()),
        "provider_slug": baseline.provider_slug if baseline else None,
        "data_policy": dict(sorted(baseline.data_policy.items())) if baseline else None,
        "reviewed_at": route.review.reviewed_at.isoformat() if route.review.reviewed_at else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Evaluation (pure)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RouteCheck:
    route_id: str
    baseline_sha256: str
    outcome: str
    reasons: tuple[str, ...]
    observed: Mapping[str, Any] = field(default_factory=dict)


def _listing_entries(payload: object) -> list[tuple[str, str]] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    entries: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        model_id, tag = item.get("model_id"), item.get("tag")
        if isinstance(model_id, str) and isinstance(tag, str):
            entries.append((model_id, tag))
    return entries or None


def _provider_policies(payload: object) -> dict[str, Mapping[str, Any]] | None:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or not data:
        return None
    policies: dict[str, Mapping[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("slug"), str):
            policy = item.get("dataPolicy")
            if isinstance(policy, dict):
                policies[item["slug"]] = policy
    return policies or None


def _is_listed(entries: Iterable[tuple[str, str]], model: str, tag: str) -> bool:
    """Exact model id, or a dated revision of it (``<model>-<date>``), at the exact tag."""
    return any(
        entry_tag == tag and (entry_model == model or entry_model.startswith(model + "-"))
        for entry_model, entry_tag in entries
    )


def evaluate(
    routes: Iterable[RoutePolicy], zdr_payload: object, providers_payload: object
) -> list[RouteCheck]:
    """Compare every monitored route with its approved baseline. Never raises."""
    monitored = [route for route in routes if is_monitored(route)]
    entries = _listing_entries(zdr_payload)
    policies = _provider_policies(providers_payload)
    checks: list[RouteCheck] = []
    for route in monitored:
        baseline = route.zdr_baseline
        assert baseline is not None
        fingerprint = baseline_fingerprint(route)
        if entries is None or policies is None:
            checks.append(
                RouteCheck(route.route_id, fingerprint, "check_failed", ("metadata_malformed",))
            )
            continue
        policy = policies.get(baseline.provider_slug)
        if policy is None:
            checks.append(
                RouteCheck(
                    route.route_id, fingerprint, "check_failed", ("provider_policy_missing",)
                )
            )
            continue
        pins = route.transport.provider_only or ()
        if len(pins) != 1:
            checks.append(
                RouteCheck(route.route_id, fingerprint, "check_failed", ("provider_pin_invalid",))
            )
            continue
        model = route.model.removeprefix("openrouter/")
        tag = pins[0]
        listed = _is_listed(entries, model, tag)
        observed = {key: policy.get(key) for key in baseline.data_policy}
        reasons: list[str] = [] if listed else ["left_zdr_listing"]
        reasons.extend(
            f"provider_policy_changed:{key}"
            for key, approved in sorted(baseline.data_policy.items())
            if policy.get(key) != approved
        )
        checks.append(
            RouteCheck(
                route.route_id,
                fingerprint,
                "revoked" if reasons else "attested",
                tuple(reasons),
                {"zdr_listed": listed, "data_policy": observed},
            )
        )
    return checks


def failed_checks(routes: Iterable[RoutePolicy], reason: str) -> list[RouteCheck]:
    return [
        RouteCheck(route.route_id, baseline_fingerprint(route), "check_failed", (reason,))
        for route in routes
        if is_monitored(route)
    ]


# --------------------------------------------------------------------------- #
# Admission snapshot
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AttestationSnapshot:
    loaded: bool = False
    #: (route_id, baseline_sha256) -> latest confirmation time.
    attested_at: Mapping[tuple[str, str], datetime] = field(default_factory=dict)
    #: (route_id, baseline_sha256) pairs that have ever been revoked.
    revoked: frozenset[tuple[str, str]] = frozenset()


_snapshot = AttestationSnapshot()
_tasks: set[asyncio.Task[None]] = set()


def snapshot() -> AttestationSnapshot:
    return _snapshot


def set_snapshot(value: AttestationSnapshot) -> None:
    """Replace the admission snapshot atomically (also used by tests)."""
    global _snapshot
    _snapshot = value


def attestation_reasons(route: RoutePolicy, *, now: datetime) -> tuple[str, ...]:
    """Why a monitored route is not currently attested. Empty means attested."""
    current = _snapshot
    if not current.loaded:
        return ("zdr_attestation_unknown",)
    key = (route.route_id, baseline_fingerprint(route))
    if key in current.revoked:
        return ("zdr_attestation_revoked",)
    confirmed = current.attested_at.get(key)
    if confirmed is None or now - confirmed > STALE_AFTER:
        return ("zdr_attestation_stale",)
    return ()


_LATEST_SQL: Final[str] = """
    SELECT route_id, baseline_sha256, outcome, max(checked_at) AS checked_at
    FROM inference_route_attestations
    WHERE outcome IN ('attested', 'revoked')
    GROUP BY route_id, baseline_sha256, outcome
"""


def snapshot_from_rows(rows: Iterable[Mapping[str, Any]]) -> AttestationSnapshot:
    attested: dict[tuple[str, str], datetime] = {}
    revoked: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row["route_id"]), str(row["baseline_sha256"]))
        if row["outcome"] == "revoked":
            revoked.add(key)
        elif row["outcome"] == "attested":
            attested[key] = row["checked_at"]
    return AttestationSnapshot(loaded=True, attested_at=attested, revoked=frozenset(revoked))


async def refresh(pool: Any) -> AttestationSnapshot:
    """Reload the admission snapshot. On error the previous snapshot is kept."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LATEST_SQL)
    current = snapshot_from_rows(rows)
    set_snapshot(current)
    return current


async def _refresh_loop(pool: Any, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await refresh(pool)
        except Exception:
            logger.warning("Route attestation refresh failed; keeping previous snapshot")


async def start(pool: Any, *, interval_s: float = REFRESH_INTERVAL_S) -> None:
    """Load the snapshot now, then keep it current in the background."""
    try:
        await refresh(pool)
    except Exception:
        logger.warning("Route attestation snapshot unavailable; monitored routes fail closed")
    task = asyncio.create_task(_refresh_loop(pool, interval_s))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --------------------------------------------------------------------------- #
# Check job
# --------------------------------------------------------------------------- #

_INSERT_SQL: Final[str] = """
    INSERT INTO inference_route_attestations
        (route_id, baseline_sha256, outcome, reasons, observed, evidence_sha256)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
"""


async def record(pool: Any, checks: Iterable[RouteCheck], evidence_sha256: str | None) -> None:
    async with pool.acquire() as conn, conn.transaction():
        for check in checks:
            await conn.execute(
                _INSERT_SQL,
                check.route_id,
                check.baseline_sha256,
                check.outcome,
                list(check.reasons),
                json.dumps(dict(check.observed), sort_keys=True),
                evidence_sha256,
            )


async def _fetch_json(client: Any, url: str) -> tuple[object, bytes]:
    response = await client.get(url, timeout=FETCH_TIMEOUT_S)
    response.raise_for_status()
    body = response.content
    return json.loads(body), body


async def run_check(pool: Any, client: Any, routes: Iterable[RoutePolicy]) -> dict[str, int]:
    """Fetch the public listings, evaluate every monitored route and record the result."""
    candidates = [route for route in routes if is_monitored(route)]
    if not candidates:
        return {"routes": 0}
    evidence: str | None = None
    try:
        zdr_payload, zdr_body = await _fetch_json(client, ZDR_LISTING_URL)
        providers_payload, providers_body = await _fetch_json(client, PROVIDERS_URL)
        evidence = hashlib.sha256(zdr_body + b"\n" + providers_body).hexdigest()
        checks = evaluate(candidates, zdr_payload, providers_payload)
    except Exception as exc:
        logger.warning("Route attestation check could not read metadata: %s", type(exc).__name__)
        checks = failed_checks(candidates, "metadata_unavailable")
    for check in checks:
        if check.outcome == "revoked":
            logger.warning(
                "Inference route %s revoked by ZDR attestation: %s",
                check.route_id,
                ", ".join(check.reasons),
            )
    await record(pool, checks, evidence)
    await refresh(pool)
    counts = {"routes": len(checks)}
    for outcome in sorted(OUTCOMES):
        counts[outcome] = sum(1 for check in checks if check.outcome == outcome)
    return counts


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
