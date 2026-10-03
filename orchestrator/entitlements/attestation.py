"""ZDR attestation for monitored inference route approvals.

A ``monitored`` route (``approval_mode`` in the inference policy) has no calendar
expiry. It stays approved while this module confirms that what it was approved
against still holds, and is revoked when that changes:

* the exact model and pinned provider endpoint must stay in OpenRouter's public ZDR
  endpoint listing (revocation reason ``left_zdr_listing``);
* the pinned provider's published data policy must keep the values recorded in
  the route's ``zdr_baseline`` (revocation reason ``provider_policy_changed:<key>``).

Backend and worker each run the check themselves (at start and every
:data:`CHECK_INTERVAL`), so neither depends on the other to enforce a revocation.
An observed revocation applies to that process's admission immediately, before any
database I/O, and is kept for the life of the process even if it cannot be
persisted. Each check also appends one row per monitored route to
``inference_route_attestations`` (migration 043), which carries sticky revocations
across restarts and to the other process. Admission reads an in-process snapshot:

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

from orchestrator.entitlements.policy import (
    MONITORED_ENDPOINT,
    MONITORED_PROVIDER,
    PolicyRequirements,
    RoutePolicy,
)

logger = logging.getLogger(__name__)

ZDR_LISTING_URL: Final[str] = "https://openrouter.ai/api/v1/endpoints/zdr"
PROVIDERS_URL: Final[str] = "https://openrouter.ai/api/frontend/v1/all-providers"

#: Operator decision (3 October 2026): a route with no confirmation for this long
#: fails closed until a check succeeds again.
STALE_AFTER: Final[timedelta] = timedelta(hours=72)

#: How often each process reloads the attestation snapshot from the database.
REFRESH_INTERVAL_S: Final[float] = 60.0

#: How often each process re-checks the public metadata itself.
CHECK_INTERVAL: Final[timedelta] = timedelta(hours=6)

#: Attempts to persist one check's results before the check is reported failed.
RECORD_ATTEMPTS: Final[int] = 3

FETCH_TIMEOUT_S: Final[float] = 30.0

OUTCOMES: Final[frozenset[str]] = frozenset({"attested", "revoked", "check_failed"})


def is_monitored(route: RoutePolicy) -> bool:
    return route.approval_mode == "monitored" and route.zdr_baseline is not None


def baseline_fingerprint(route: RoutePolicy) -> str:
    """Identity of what was approved. Any change makes a new, unattested baseline.

    Covers the route, gateway endpoint, exact model, pinned provider, the baseline
    data policy and the operator review date, so re-approval after a revocation is an explicit config
    change, never a silent recovery.
    """
    baseline = route.zdr_baseline
    payload = {
        "route_id": route.route_id,
        "endpoint": route.endpoint,
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
        # Any unreadable entry makes the whole listing unreadable: skipping it could
        # turn missing metadata into a sticky "left the listing" revocation.
        if not isinstance(item, dict):
            return None
        model_id, tag = item.get("model_id"), item.get("tag")
        if not isinstance(model_id, str) or not isinstance(tag, str) or not model_id or not tag:
            return None
        entries.append((model_id, tag))
    return entries


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
    """The exact requested model id at the exact pinned provider tag.

    Neither a sibling model (``<model>-pro``) nor a dated revision listed without the
    requested id is evidence that the requested model is still served ZDR there.
    """
    return (model, tag) in set(entries)


def _same_value(observed: object, approved: object) -> bool:
    """Type-strict equality: ``False`` is not ``0`` and ``True`` is not ``1``."""
    return type(observed) is type(approved) and observed == approved


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
        if route.provider != MONITORED_PROVIDER or route.endpoint != MONITORED_ENDPOINT:
            # Defence in depth: the parser already refuses this. OpenRouter's listings
            # are no evidence about any other host.
            checks.append(
                RouteCheck(
                    route.route_id, fingerprint, "check_failed", ("endpoint_not_attestable",)
                )
            )
            continue
        pins = route.transport.provider_only or ()
        if len(pins) != 1:
            checks.append(
                RouteCheck(route.route_id, fingerprint, "check_failed", ("provider_pin_invalid",))
            )
            continue
        missing = sorted(key for key in baseline.data_policy if key not in policy)
        if missing:
            # A pinned field the provider no longer publishes is unreadable evidence,
            # not an unchanged value: never attest it, and never read it as None.
            checks.append(
                RouteCheck(
                    route.route_id,
                    fingerprint,
                    "check_failed",
                    tuple(f"provider_policy_field_missing:{key}" for key in missing),
                )
            )
            continue
        model = route.model.removeprefix("openrouter/")
        tag = pins[0]
        listed = _is_listed(entries, model, tag)
        observed = {key: policy[key] for key in baseline.data_policy}
        reasons: list[str] = [] if listed else ["left_zdr_listing"]
        reasons.extend(
            f"provider_policy_changed:{key}"
            for key, approved in sorted(baseline.data_policy.items())
            if not _same_value(policy[key], approved)
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
#: Revocations this process has observed. Monotonic for the life of the process and
#: merged into every snapshot, so a failed write or refresh never re-admits a route.
_local_revoked: set[tuple[str, str]] = set()


def snapshot() -> AttestationSnapshot:
    return _snapshot


def set_snapshot(value: AttestationSnapshot) -> None:
    """Replace the admission snapshot atomically, keeping local revocations."""
    global _snapshot
    if _local_revoked - value.revoked:
        value = AttestationSnapshot(
            loaded=value.loaded,
            attested_at=value.attested_at,
            revoked=value.revoked | frozenset(_local_revoked),
        )
    _snapshot = value


def reset_local_revocations() -> None:
    """Forget locally observed revocations (tests only; a process never does this)."""
    _local_revoked.clear()


def apply_observed(checks: Iterable[RouteCheck]) -> None:
    """Apply a check's revocations to this process's admission before any I/O.

    Only negative observations apply here, immediately and permanently for this
    process. A confirmation is published only by a successful :func:`refresh` after
    it has been recorded: confirming locally could renew a baseline another process
    has already revoked while this process cannot read the shared history.
    """
    revoked = {
        (check.route_id, check.baseline_sha256) for check in checks if check.outcome == "revoked"
    }
    if revoked:
        _local_revoked.update(revoked)
        set_snapshot(_snapshot)


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


def _monitored_routes() -> list[RoutePolicy]:
    from orchestrator.entitlements.policy import load_inference_policy

    return [route for route in load_inference_policy().routes.values() if is_monitored(route)]


async def check_once(pool: Any) -> dict[str, int]:
    """One scheduled check of the configured monitored routes. Never raises."""
    import httpx

    try:
        routes = _monitored_routes()
    except Exception:
        logger.warning("Route attestation check skipped: inference policy unavailable")
        return {"routes": 0}
    if not routes:
        return {"routes": 0}
    try:
        async with httpx.AsyncClient() as client:
            return await run_check(pool, client, routes)
    except Exception:
        logger.exception("Route attestation check failed")
        return {"routes": len(routes), "error": 1}


async def _check_loop(pool: Any, interval_s: float) -> None:
    while True:
        await check_once(pool)
        await asyncio.sleep(interval_s)


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def stop() -> None:
    """Cancel and await this process's refresh and check tasks.

    Call before closing their database pool, so no task keeps polling a closed pool
    or overwrites the snapshot after a restart in the same event loop.
    """
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()


async def start(
    pool: Any,
    *,
    interval_s: float = REFRESH_INTERVAL_S,
    check_interval_s: float = CHECK_INTERVAL.total_seconds(),
) -> None:
    """Load the snapshot now, then refresh it and run this process's own checks."""
    await stop()
    # A restart must not keep admitting from a previous pool's history: start
    # unloaded (fail closed) until this pool's history is read. Local revocations
    # are kept by set_snapshot().
    set_snapshot(AttestationSnapshot())
    try:
        await refresh(pool)
    except Exception:
        logger.warning("Route attestation snapshot unavailable; monitored routes fail closed")
    _spawn(_refresh_loop(pool, interval_s))
    _spawn(_check_loop(pool, check_interval_s))


# --------------------------------------------------------------------------- #
# Check job
# --------------------------------------------------------------------------- #

_INSERT_SQL: Final[str] = """
    INSERT INTO inference_route_attestations
        (route_id, baseline_sha256, outcome, reasons, observed, evidence_sha256)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
"""


async def record(pool: Any, checks: Iterable[RouteCheck], evidence_sha256: str | None) -> None:
    checks = list(checks)
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
    # Enforce revocations before any fallible I/O; confirmations wait for the
    # recorded history to be read back by refresh().
    apply_observed(checks)
    revoked = [check for check in checks if check.outcome == "revoked"]
    for check in revoked:
        logger.warning(
            "Inference route %s revoked by ZDR attestation: %s",
            check.route_id,
            ", ".join(check.reasons),
        )
    for attempt in range(1, RECORD_ATTEMPTS + 1):
        try:
            await record(pool, checks, evidence)
            break
        except Exception:
            if attempt == RECORD_ATTEMPTS:
                if revoked:
                    logger.critical(
                        "Route revocation enforced in this process but not persisted: %s",
                        ", ".join(check.route_id for check in revoked),
                    )
                raise
            await asyncio.sleep(attempt)
    await refresh(pool)
    counts = {"routes": len(checks)}
    for outcome in sorted(OUTCOMES):
        counts[outcome] = sum(1 for check in checks if check.outcome == outcome)
    return counts


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Exit codes for the deploy-time bootstrap (scripts/attest_inference_routes.py).
BOOTSTRAP_ADMITTED: Final[int] = 0
BOOTSTRAP_NOT_ADMITTED: Final[int] = 1
BOOTSTRAP_NO_MONITORED_ROUTES: Final[int] = 3


def bootstrap_status(
    routes: Iterable[RoutePolicy], requirements: PolicyRequirements, *, now: datetime
) -> tuple[int, dict[str, tuple[str, ...]]]:
    """Effective admission of every monitored route, as the deploy gate.

    Uses each route's complete rejection reasons under the policy's requirements, so
    success means every monitored route would be admitted now: including routes a
    clean latest check cannot re-admit because their baseline was revoked earlier,
    and routes unusable for any other reason (not approved, a future review date, a
    missing account assertion).
    """
    status = {
        route.route_id: route.rejection_reasons(requirements, now=now)
        for route in routes
        if is_monitored(route)
    }
    if not status:
        return BOOTSTRAP_NO_MONITORED_ROUTES, status
    if any(status.values()):
        return BOOTSTRAP_NOT_ADMITTED, status
    return BOOTSTRAP_ADMITTED, status
