#!/usr/bin/env python3
"""Evaluation-only same-model endpoint reliability runner.

This runner is the *only* place failover enablement exists for the approved
experiment in ``docs/MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md``. It drives the
existing runtime one dispatch at a time: every provider attempt is a separate
``compute_runtime.guarded_completion`` invocation pinned with the internal
``_route_id``/``_dispatch_timeout_s`` seam, so one invocation equals exactly one
durable dispatch row. It never delegates a two-attempt operation to a single
runtime call, and it never rewrites the original single-endpoint pilot.

Design rules this file enforces (all of them observable in the state file):

* Separate, versioned, immutable run identity. Original pilot attempt IDs and
  scores are never reused or reinterpreted.
* Three predeclared arms over the same exact model, two explicit route IDs with
  distinct provider pins, and a fixed recorded interleaving schedule.
* At most two sequential dispatches per logical call, 90s overall / 45s per
  endpoint, clipped by the remaining deadline. A matched 45s single-endpoint arm
  exists so redundancy is not confused with a deadline change.
* Every dispatch is fsynced to durable state *before* it is sent, so an
  interrupted call is never replayed and its conservative intent bound is
  retained against the incremental ceiling.
* The incremental ceiling (USD 1) is enforced from durable reservations and
  charges, including unknown costs and smoke calls, and cross-checked against the
  authoritative PostgreSQL ledger delta from a frozen baseline. The ledger
  post-delta is evidence; the durable state is the accounting truth.
* Structured, sanitized failure metadata only. Raw exception strings and secret
  material are never stored or classified; a failure is eligible for a backup
  dispatch only through the runtime's typed metadata and only for the approved
  pre-output transport class.

No streaming, no tools, no external effects, no paid calls during ``--dry-run``.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import asyncpg

from orchestrator import compute_runtime, model_routing
from orchestrator.config import Settings, get_settings
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import BudgetExceeded, EntitlementsError
from orchestrator.entitlements.ledger import next_period_key
from orchestrator.entitlements.plans import Capability
from orchestrator.entitlements.policy import RoutePolicy, load_inference_policy, load_policy
from scripts import model_roster_live as live
from scripts import model_roster_pilot as pilot

# ---------------------------------------------------------------------------
# Approved experiment shape (docs/MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md)
# ---------------------------------------------------------------------------

#: This file format is versioned independently of the original pilot's state
#: and result artifacts. It is a local file format, not a public API.
STATE_VERSION: Final[str] = "model-endpoint-reliability-state/1"
RESULTS_ARTIFACT_VERSION: Final[str] = "model-endpoint-reliability-results/1"

#: The original funded period. There is no month rollover, trial money or grant
#: for this experiment: approvals expire at the original period end.
ORIGINAL_FUNDED_PERIOD: Final[str] = "2026-09"

#: Shared aggregate account ceiling (USD 25) and this experiment's incremental
#: ceiling (USD 1) *inside* that cap. The incremental ceiling is a stricter local
#: bound, never new funding.
AGGREGATE_CAP_MICROUSD: Final[int] = live.CAP_MICROUSD
INCREMENTAL_CAP_MICROUSD: Final[int] = 1_000_000

UTILITY_CASE_IDS: Final[tuple[str, ...]] = ("U01", "U03", "U05", "U08")
REPEATS: Final[int] = 2
ARMS: Final[tuple[str, ...]] = ("primary_only", "alternate_only", "primary_then_alternate")
ROUTE_ROLES: Final[tuple[str, ...]] = ("primary", "alternate")

#: Route roles attempted, in order, by each arm. Fixed: a cooldown never
#: replaces an arm, it only skips a scheduled dispatch inside it.
ARM_ROUTE_ROLES: Final[Mapping[str, tuple[str, ...]]] = {
    "primary_only": ("primary",),
    "alternate_only": ("alternate",),
    "primary_then_alternate": ("primary", "alternate"),
}

#: Hard dispatch limits. The case budget counts every actual dispatch, and the
#: redundancy arm may use at most two of them.
MAX_DISPATCHES_PER_LOGICAL: Final[Mapping[str, int]] = {
    arm: len(roles) for arm, roles in ARM_ROUTE_ROLES.items()
}
CASE_MAX_DISPATCHES: Final[int] = pilot.MAX_CALLS_PER_ATTEMPT
LOGICAL_DISPATCH_BOUND: Final[int] = (
    len(UTILITY_CASE_IDS) * REPEATS * sum(MAX_DISPATCHES_PER_LOGICAL[arm] for arm in ARMS)
)
SMOKE_DISPATCH_COUNT: Final[int] = len(ROUTE_ROLES)
TOTAL_DISPATCH_BOUND: Final[int] = LOGICAL_DISPATCH_BOUND + SMOKE_DISPATCH_COUNT

#: Bounds from the review resolutions: 45s per endpoint, 90s overall only for the
#: redundancy arm. Pacing and settlement consume the same logical deadline.
ENDPOINT_TIMEOUT_S: Final[float] = 45.0
SINGLE_ARM_DEADLINE_S: Final[float] = 45.0
REDUNDANT_DEADLINE_S: Final[float] = 90.0
#: Minimum spacing between dispatch starts when the provider gave no retry
#: guidance. A rate-limit cooldown is honoured instead of waited out.
MIN_PACING_S: Final[float] = 2.0
#: Used only when a rate-limited failure carried no usable retry guidance.
DEFAULT_COOLDOWN_S: Final[float] = 60.0

COMMON_MAX_OUTPUT_TOKENS: Final[int] = 4096
SMOKE_MAX_OUTPUT_TOKENS: Final[int] = 64
SMOKE_PROMPT: Final[str] = "Reply with the single word: ok."
SMOKE_DEADLINE_S: Final[float] = ENDPOINT_TIMEOUT_S
RECHECK_SMOKE_ID: Final[str] = "smoke-primary-recheck-4096-v1"

# ---------------------------------------------------------------------------
# Failure classes (structured metadata only, never message substrings)
# ---------------------------------------------------------------------------

#: HTTP statuses that may earn a backup dispatch. Mirrors the runtime's own
#: approved set, so the runner never widens it.
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset(compute_runtime.RETRY_ELIGIBLE_STATUS)
#: Runtime failure categories that may earn a backup dispatch, pre-output only.
#: These are the runtime's own closed vocabulary, not runner-invented names.
RETRYABLE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        compute_runtime.FAILURE_RATE_LIMITED,
        compute_runtime.FAILURE_UPSTREAM_UNAVAILABLE,
        compute_runtime.FAILURE_CONNECTION_FAILED,
        compute_runtime.FAILURE_TIMEOUT,
    }
)
#: Cancellation, settlement/accounting and policy failures abort the whole batch:
#: continuing would hide a broken experiment rather than measure an endpoint.
ABORT_CATEGORIES: Final[frozenset[str]] = frozenset(
    {compute_runtime.FAILURE_SETTLEMENT_FAILED, "cancellation", "cancelled", "accounting", "policy"}
)
ABORT_CODES: Final[frozenset[str]] = frozenset(
    {
        "settlement_conflict",
        "policy_error",
        "access_denied",
        "capability_unavailable",
        "capability_denied",
        "account_unavailable",
        "account_error",
        "route_unavailable",
    }
)
#: A truthful capacity stop, recorded separately from provider unavailability.
CAPACITY_CATEGORIES: Final[frozenset[str]] = frozenset({"budget", "capacity"})
CAPACITY_CODES: Final[frozenset[str]] = frozenset({"budget_exceeded", "limit_exceeded"})

JsonObject = dict[str, Any]


class ReliabilityError(Exception):
    """Preflight, accounting, cancellation or persistent-state failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReliabilityError(message)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def monotonic() -> float:
    """Indirection over :func:`time.monotonic` so deadlines stay testable."""
    return time.monotonic()


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def period_end(period: str) -> datetime:
    """The instant the original funded period ends (UTC)."""
    return datetime.fromisoformat(next_period_key(period) + "-01").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Durable, private, versioned state
# ---------------------------------------------------------------------------


def write_state(path: Path, state: JsonObject) -> None:
    """Replace and fsync a private checkpoint before any billable call."""
    raw = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


def empty_state(identity: JsonObject) -> JsonObject:
    return {
        "version": STATE_VERSION,
        "identity": identity,
        "account_ledger_baseline": None,
        "preflight": None,
        "cooldowns": {},
        "smokes": {},
        "logicals": {},
        "pacing": {"last_dispatch_started_at": None},
    }


def amendment_payload(plan_sha256: str) -> JsonObject:
    """The sole approved extension; the original plan fingerprint is untouched."""
    return {
        "original_plan_sha256": plan_sha256,
        "smoke_id": RECHECK_SMOKE_ID,
        "role": "primary",
        "max_output_tokens": COMMON_MAX_OUTPUT_TOKENS,
        "endpoint_timeout_s": ENDPOINT_TIMEOUT_S,
        "additional_dispatches": 1,
        "total_dispatch_bound": TOTAL_DISPATCH_BOUND + 1,
        "incremental_cap_microusd": INCREMENTAL_CAP_MICROUSD,
    }


def amended(state: JsonObject) -> bool:
    record = state.get("plan_amendment")
    if record is None:
        require(RECHECK_SMOKE_ID not in state["smokes"], "recheck without amendment")
        return False
    payload = amendment_payload(state["identity"]["plan_sha256"])
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    require(record == {**payload, "amendment_sha256": digest}, "plan amendment drift")
    require(recheck_eligible(state), "original smoke evidence drift")
    return True


def recheck_eligible(state: JsonObject) -> bool:
    primary = state["smokes"].get("smoke-primary")
    alternate = state["smokes"].get("smoke-alternate")
    return (
        isinstance(primary, dict)
        and primary.get("outcome") == "quality:truncated_response"
        and primary.get("status") == "quality_failed"
        and len(primary.get("dispatches", [])) == 1
        and isinstance(alternate, dict)
        and alternate.get("outcome") == "completed"
        and alternate.get("status") == "completed"
        and len(alternate.get("dispatches", [])) == 1
    )


@contextmanager
def locked_state(path: Path, identity: JsonObject):
    """One private state file under one exclusive lock, with a frozen identity."""
    require(path.parent.is_dir() and not path.is_symlink(), "state directory/file invalid")
    require(path.parent.stat().st_mode & 0o077 == 0, "state directory must be private (0700)")
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReliabilityError("another reliability runner holds the state lock") from exc
        # A torn checkpoint is rejected, never repaired: the previous writer may
        # have been interrupted between the intent write and the response.
        require(not path.with_name(path.name + ".tmp").exists(), "incomplete state checkpoint")
        if path.exists():
            require(path.is_file() and not path.is_symlink(), "state path invalid")
            state: JsonObject = json.loads(path.read_text(encoding="utf-8"))
            require(state.get("version") == STATE_VERSION, "state version changed")
            require(state.get("identity") == identity, "run identity drift")
            for key in ("cooldowns", "smokes", "logicals"):
                require(isinstance(state.get(key), dict), "state sections invalid")
            require(isinstance(state.get("pacing"), dict), "state pacing invalid")
            amended(state)
        else:
            state = empty_state(identity)
            write_state(path, state)
        yield state
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Cooldowns: durable UTC earliest-retry evidence, consulted for every route
# ---------------------------------------------------------------------------


def validate_retry_delay(raw: object) -> tuple[float | None, str]:
    """Accept only a finite, non-negative delay. Returns ``(delay, source)``.

    Malformed or missing guidance is never trusted as a shorter wait, and never
    permits an immediate retry: the caller falls back to the conservative default
    and still records a cooldown.
    """
    if raw is None:
        return None, "absent"
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, "malformed"
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        return None, "malformed"
    return value, "provider"


def active_cooldown(state: JsonObject, route_id: str, now: datetime) -> datetime | None:
    """The route's earliest-retry instant when it is still cooling down."""
    record = state["cooldowns"].get(route_id)
    if not isinstance(record, dict):
        return None
    until = parse_iso(record.get("until"))
    return until if until is not None and until > now else None


def record_cooldown(
    state: JsonObject,
    route_id: str,
    *,
    status_code: int | None,
    retry_after: object,
    now: datetime,
    ends_at: datetime,
) -> JsonObject | None:
    """Record a durable cooldown; never shorten an existing one.

    The stored deadline is capped at the original funded period end, so a long
    provider hint cannot extend authority past the period, and "no shorter than
    what we already promised" keeps a resume from inventing fresh availability.
    """
    delay, source = validate_retry_delay(retry_after)
    rate_limited = status_code == 429
    if delay is None and not rate_limited:
        return None
    if delay is None:
        delay, source = DEFAULT_COOLDOWN_S, f"default:{source}"
    deadline = min(now + timedelta(seconds=delay), ends_at)
    existing = state["cooldowns"].get(route_id)
    if isinstance(existing, dict):
        previous = parse_iso(existing.get("until"))
        if previous is not None and previous > deadline:
            return None
    record: JsonObject = {
        "route_id": route_id,
        "until": iso(deadline),
        "source": source,
        "observed_status_code": status_code,
        "recorded_at": iso(now),
        "capped_at_period_end": deadline >= ends_at,
    }
    state["cooldowns"][route_id] = record
    return record


# ---------------------------------------------------------------------------
# Preflight: same exact model, two explicit route IDs, no inherited helper
# ---------------------------------------------------------------------------


def qualified_routes(
    inference: Any, primary_id: str, alternate_id: str
) -> Mapping[str, RoutePolicy]:
    """Route-specific qualification for exactly two explicitly named routes.

    The original pilot's ``pinned_routes``/``verify_candidate`` helpers assert a
    *single* eligible route per model and are deliberately not reused: two
    qualified routes for one exact model is the precondition of this experiment.
    """
    require(primary_id != alternate_id, "primary and alternate route IDs must be distinct")
    require(bool(primary_id) and bool(alternate_id), "explicit route IDs required")
    routes: dict[str, RoutePolicy] = {}
    for role, route_id in (("primary", primary_id), ("alternate", alternate_id)):
        route = inference.route(route_id)
        require(route is not None, f"unqualified route for {role}")
        require(route.is_approved(inference.requirements), f"unqualified route for {role}")
        require(
            route.provider == "openrouter" and route.model.startswith("openrouter/"),
            "non-OpenRouter route",
        )
        require(
            route.route_class in {"routine", "premium"} and route.price_ceiling is not None,
            "unfunded route class",
        )
        # Raises for an unqualified route and is the only transport builder used.
        route.transport_payload(inference.requirements)
        provider_pins = tuple(route.transport.provider_only or ())
        require(bool(provider_pins), f"provider pin missing for {role}")
        routes[role] = route
    require(
        routes["primary"].model == routes["alternate"].model,
        "primary and alternate must serve the same exact model",
    )
    return routes


def completion_params(
    case: pilot.CaseFixture | None, route: RoutePolicy, max_tokens: int
) -> JsonObject:
    """The single, non-streaming, tool-free request for one dispatch.

    Both arms send byte-identical parameters apart from the model string, so a
    reliability difference cannot be explained by a different ask.
    """
    prompt = SMOKE_PROMPT if case is None else case.prompt
    ceiling = min(max_tokens, route.max_output_tokens)
    params: JsonObject = {
        "model": route.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": ceiling,
    }
    if case is not None and case.requires_schema:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "reliability_" + case.case_id,
                "strict": True,
                "schema": case.schema,
            },
        }
    # Explicit, permanent guard for this evaluation: no tools and no streaming.
    require(not params.get("tools") and not params.get("stream"), "no tools or streaming in eval")
    require(
        "_route_id" not in params and "_dispatch_timeout_s" not in params,
        "internal seam arguments are never part of a priced request",
    )
    return params


def route_preflight(
    resolved: Any, route: RoutePolicy, params: JsonObject, case: pilot.CaseFixture | None
) -> int:
    """Route-specific admission for one exact request, and its worst-case bound.

    Uses the runtime's own pricing/bound helpers so the number the runner
    accounts against the ceiling is the number the runtime would reserve, and
    selects this route out of the *model's* candidate list rather than requiring
    the list to contain a single entry.
    """
    bound = compute_runtime._request_bound(params)
    candidates = compute_runtime._priced_candidates(
        resolved, bound, params, route.model, check_budget=False
    )
    priced = [entry for entry in candidates if entry[2].route_id == route.route_id]
    require(
        len(priced) == 1,
        f"pinned route not eligible for {'smoke' if case is None else case.case_id}",
    )
    return int(priced[0][0])


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Logical:
    logical_id: str
    arm: str
    case_id: str
    repeat: int
    route_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Smoke:
    smoke_id: str
    role: str


def build_logicals() -> tuple[Logical, ...]:
    """The fixed recorded interleaving schedule, identical on every run.

    Arms are interleaved case-by-case and repeat-by-repeat so any shared-pool
    degradation is felt by all three arms in the same time window.
    """
    planned: list[Logical] = []
    for repeat in range(1, REPEATS + 1):
        for case_id in UTILITY_CASE_IDS:
            for arm in ARMS:
                planned.append(
                    Logical(
                        # Deliberately not a pilot attempt ID: this experiment has
                        # its own identity space and its own artifact version.
                        logical_id=f"rel-{case_id}-{arm}-r{repeat}",
                        arm=arm,
                        case_id=case_id,
                        repeat=repeat,
                        route_roles=ARM_ROUTE_ROLES[arm],
                    )
                )
    ids = [item.logical_id for item in planned]
    require(len(set(ids)) == len(ids), "internal: duplicate logical ids")
    return tuple(planned)


def build_smokes() -> tuple[Smoke, ...]:
    return tuple(Smoke(smoke_id=f"smoke-{role}", role=role) for role in ROUTE_ROLES)


def plan_fingerprint(logicals: Sequence[Logical], smokes: Sequence[Smoke]) -> str:
    """Immutable hash of the predeclared plan (never of a monetary result)."""
    payload = {
        "state_version": STATE_VERSION,
        "logicals": [
            {
                "logical_id": item.logical_id,
                "arm": item.arm,
                "case_id": item.case_id,
                "repeat": item.repeat,
                "route_roles": list(item.route_roles),
            }
            for item in logicals
        ],
        "smokes": [{"smoke_id": item.smoke_id, "role": item.role} for item in smokes],
        "limits": {
            "endpoint_timeout_s": ENDPOINT_TIMEOUT_S,
            "single_arm_deadline_s": SINGLE_ARM_DEADLINE_S,
            "redundant_deadline_s": REDUNDANT_DEADLINE_S,
            "min_pacing_s": MIN_PACING_S,
            "common_max_output_tokens": COMMON_MAX_OUTPUT_TOKENS,
            "case_max_dispatches": CASE_MAX_DISPATCHES,
            "total_dispatch_bound": TOTAL_DISPATCH_BOUND,
            "incremental_cap_microusd": INCREMENTAL_CAP_MICROUSD,
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Authoritative ledger reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerExposure:
    spent_microusd: int
    reserved_microusd: int
    open_holds: int

    @property
    def total_microusd(self) -> int:
        return self.spent_microusd + self.reserved_microusd


async def read_ledger_exposure(pool: Any, account: uuid.UUID, period: str) -> LedgerExposure:
    """Settled spend plus open holds, straight from the authoritative tables."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(p.spent_microusd, 0) AS spent, "
            "COALESCE(p.reserved_microusd, 0) AS reserved, "
            "(SELECT count(*) FROM entitlement_reservations r "
            " WHERE r.user_id = $1 AND r.status = 'open') AS open_holds "
            "FROM entitlement_usage_periods p "
            "WHERE p.user_id = $1 AND p.period_key = $2",
            account,
            period,
        )
    require(row is not None, "no authoritative ledger row for this account/period")
    return LedgerExposure(int(row["spent"]), int(row["reserved"]), int(row["open_holds"]))


# ---------------------------------------------------------------------------
# Durable accounting
# ---------------------------------------------------------------------------


def dispatch_rows(state: JsonObject) -> list[JsonObject]:
    """Every dispatch ever recorded, in recorded order, across smokes and cases."""
    rows: list[JsonObject] = []
    for section in ("smokes", "logicals"):
        for entry in state[section].values():
            recorded = entry.get("dispatches")
            if isinstance(recorded, list):
                rows.extend(item for item in recorded if isinstance(item, dict))
    return rows


def accounted_microusd(state: JsonObject) -> int:
    """Conservative experiment charge: known charges plus unknown intent bounds.

    A settled dispatch contributes its charge; an interrupted or unknown dispatch
    keeps its full reservation bound. No refunds, no re-basing on restart.
    """
    total = 0
    for row in dispatch_rows(state):
        charge = row.get("account_charge_microusd")
        if isinstance(charge, int) and not isinstance(charge, bool):
            total += charge
            continue
        bound = row.get("reservation_bound_microusd")
        if isinstance(bound, int) and not isinstance(bound, bool):
            total += bound
    return total


def baseline_exposure(state: JsonObject) -> int:
    baseline = state.get("account_ledger_baseline")
    if not isinstance(baseline, dict):
        raise ReliabilityError("account ledger baseline missing")
    value = baseline.get("exposure_microusd")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReliabilityError("baseline exposure invalid")
    return value


def ledger_delta_microusd(state: JsonObject, exposure: LedgerExposure) -> int:
    """Authoritative ledger movement since the frozen baseline (evidence only)."""
    return exposure.total_microusd - baseline_exposure(state)


def sub_cap_admits(state: JsonObject, bound: int) -> bool:
    return accounted_microusd(state) + bound <= INCREMENTAL_CAP_MICROUSD


# ---------------------------------------------------------------------------
# Failure classification (typed metadata only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Failure:
    """Sanitized failure facts. Never a raw exception string."""

    category: str
    status_code: int | None
    retry_after: object
    outcome: str  # "fallback" | "stop" | "abort" | "capacity_stop"

    def as_record(self) -> JsonObject:
        return {
            "category": self.category,
            "status_code": self.status_code,
            "retry_after_seconds": self.retry_after
            if isinstance(self.retry_after, (int, float)) and not isinstance(self.retry_after, bool)
            else None,
            "outcome": self.outcome,
        }


def _token(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _opt_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def classify_unavailable(exc: compute_runtime.ComputeUnavailable) -> Failure:
    """Map typed ``ComputeUnavailable`` metadata onto an experiment outcome.

    The runtime's ``category``/``status_code``/``retryable`` metadata is the only
    classifier. A missing or false ``retryable`` fails closed, and the approved
    pre-output transport class (429/502/503/504, connect, timeout) is required on
    top of it. The exception message is never inspected.
    """
    category = _token(getattr(exc, "category", None))
    if category == "unknown":
        category = _token(getattr(exc, "code", None))
    status_code = _opt_status(getattr(exc, "status_code", None))
    retry_after = getattr(exc, "retry_after_seconds", None)
    declared_retryable = getattr(exc, "retryable", False) is True
    code = _token(getattr(exc, "code", None))
    if category in ABORT_CATEGORIES or code in ABORT_CODES:
        outcome = "abort"
    elif category in CAPACITY_CATEGORIES or code in CAPACITY_CODES:
        outcome = "capacity_stop"
    elif status_code in RETRYABLE_STATUS_CODES or category in RETRYABLE_CATEGORIES:
        outcome = "fallback" if declared_retryable else "stop"
    else:
        outcome = "stop"
    return Failure(category, status_code, retry_after, outcome)


def classify_entitlements(exc: EntitlementsError) -> Failure:
    """A budget refusal is a capacity stop; any other denial is a policy abort."""
    code = _token(getattr(exc, "code", None)) if not isinstance(exc, BudgetExceeded) else "budget"
    if isinstance(exc, BudgetExceeded) or code in CAPACITY_CODES:
        return Failure(code, None, None, "capacity_stop")
    return Failure(code, None, None, "abort")


def classify_unknown(exc: BaseException) -> Failure:
    """Unknown, unclassified exceptions stop; they are never a retry classifier."""
    if isinstance(exc, asyncio.CancelledError):
        return Failure("cancellation", None, None, "abort")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return Failure(compute_runtime.FAILURE_TIMEOUT, None, None, "stop")
    return Failure(compute_runtime.FAILURE_UNSPECIFIED, None, None, "stop")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunContext:
    pool: Any
    account: uuid.UUID
    service: EntitlementService
    period: str
    identity: JsonObject
    state: JsonObject
    state_path: Path
    routes: Mapping[str, RoutePolicy]
    cases: Mapping[str, pilot.CaseFixture]
    account_ceiling_microusd: int
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchResult:
    response: JsonObject | None = None
    failure: Failure | None = None
    block: str | None = None  # capacity_stop | cooldown | deadline_exceeded


async def route_preflight_bounds(ctx: RunContext, case: pilot.CaseFixture | None, role: str) -> int:
    """The worst-case charge for one role's request to this case (or the smoke)."""
    route = ctx.routes[role]
    params = completion_params(
        case, route, COMMON_MAX_OUTPUT_TOKENS if case is not None else SMOKE_MAX_OUTPUT_TOKENS
    )
    resolved = await ctx.service.resolve(ctx.account)
    return route_preflight(resolved, route, params, case)


async def pace(ctx: RunContext, deadline: float) -> bool:
    """Wait out the minimum inter-dispatch spacing, bounded by the deadline."""
    state = ctx.state
    last = parse_iso(state["pacing"].get("last_dispatch_started_at"))
    now = utcnow()
    if last is not None:
        target = last + timedelta(seconds=MIN_PACING_S)
        remaining = (target - now).total_seconds()
        if remaining > 0:
            budget = deadline - monotonic()
            if remaining >= budget:
                return False
            await asyncio.sleep(remaining)
    return True


async def dispatch_once(
    ctx: RunContext,
    entry: JsonObject,
    *,
    case: pilot.CaseFixture | None,
    role: str,
    deadline: float,
    pending_bounds: Sequence[int],
    max_output_tokens: int | None = None,
) -> DispatchResult:
    """One guarded_completion invocation equals exactly one durable dispatch row.

    ``pending_bounds`` is this dispatch's bound followed by every bound that
    could still be reached in the same arm. It is a conservative eligibility
    check only, never an atomic multi-call hold: the ledger separately admits
    each dispatch and can still refuse the second one.
    """
    route = ctx.routes[role]
    params = completion_params(
        case,
        route,
        max_output_tokens
        if max_output_tokens is not None
        else COMMON_MAX_OUTPUT_TOKENS
        if case is not None
        else SMOKE_MAX_OUTPUT_TOKENS,
    )
    resolved = await ctx.service.resolve(ctx.account)
    bound = route_preflight(resolved, route, params, case)

    before = await read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
    remaining = deadline - monotonic()
    if remaining <= 0:
        return DispatchResult(block="deadline_exceeded")
    timeout = min(ENDPOINT_TIMEOUT_S, remaining)
    if timeout <= 0:
        return DispatchResult(block="deadline_exceeded")
    reachable = bound + sum(pending_bounds)
    if not sub_cap_admits(ctx.state, reachable) or (
        before.total_microusd + reachable > ctx.account_ceiling_microusd
    ):
        return DispatchResult(block="capacity_stop")
    if not await pace(ctx, deadline):
        return DispatchResult(block="deadline_exceeded")
    remaining = deadline - monotonic()
    if remaining <= 0:
        return DispatchResult(block="deadline_exceeded")
    timeout = min(ENDPOINT_TIMEOUT_S, remaining)
    require(
        len(dispatch_rows(ctx.state)) + 1 <= TOTAL_DISPATCH_BOUND + int(amended(ctx.state)),
        "global dispatch bound reached",
    )

    row: JsonObject = {
        "route_id": route.route_id,
        "role": role,
        "status": "in_progress",
        "requested_model": route.model,
        "provider_pin": list(route.transport.provider_only or ()),
        "max_output_tokens": params["max_tokens"],
        "dispatch_timeout_s": round(timeout, 6),
        "reservation_bound_microusd": bound,
        "account_charge_microusd": None,
        "provider_cost_usd": None,
        "ledger_exposure_before_microusd": before.total_microusd,
        "failure": None,
    }
    # Persist intent before sending. A crash between this checkpoint and the
    # response can lose one dispatch, never duplicate a paid call, and never
    # refunds its conservative bound against the incremental ceiling.
    row["started_at"] = iso(utcnow())
    entry["dispatches"].append(row)
    ctx.state["pacing"]["last_dispatch_started_at"] = row["started_at"]
    # Intent is durable before the provider is contacted.
    write_state(ctx.state_path, ctx.state)
    failure: Failure | None = None
    response: JsonObject | None = None
    started = monotonic()
    try:
        async with compute_runtime.account_compute(
            ctx.pool,
            ctx.account,
            operation="chat",
            profile="routine",
            expected_period=ctx.period,
        ):
            response = live.as_dict(
                await compute_runtime.guarded_completion(
                    **params,
                    _route_id=route.route_id,
                    _dispatch_timeout_s=timeout,
                )
            )
            routed = model_routing.current_routing()
            row["runtime_route_id"] = routed.selected_route_id
            row["runtime_model"] = routed.selected_model
            require(
                routed.selected_route_id == route.route_id and routed.selected_model == route.model,
                "runtime route drift",
            )
    except compute_runtime.ComputeUnavailable as exc:
        failure = classify_unavailable(exc)
    except BudgetExceeded:
        failure = Failure("budget", None, None, "capacity_stop")
    except EntitlementsError as exc:
        failure = classify_entitlements(exc)
    except asyncio.CancelledError:
        row["status"] = "interrupted"
        row["elapsed_seconds"] = monotonic() - started
        row["failure"] = Failure("cancellation", None, None, "abort").as_record()
        write_state(ctx.state_path, ctx.state)
        raise
    except Exception as exc:
        failure = classify_unknown(exc)
    row["elapsed_seconds"] = monotonic() - started
    if response is not None:
        row["response"] = response
        usage = response.get("usage")
        row["usage"] = usage
        if isinstance(usage, dict) and type(usage.get("cost")) in (int, float):
            cost = float(usage["cost"])
            if 0 <= cost < float("inf"):
                row["provider_cost_usd"] = cost
    if failure is not None:
        row["status"] = "failed"
        row["failure"] = failure.as_record()
    else:
        row["status"] = "completed"
    # Ledger post-delta is corroborating evidence; the durable state is the
    # accounting truth and keeps the bound when the delta is not attributable.
    after = await read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
    delta = after.total_microusd - int(row["ledger_exposure_before_microusd"])
    row["ledger_exposure_after_microusd"] = after.total_microusd
    row["ledger_delta_microusd"] = delta
    if 0 < delta <= bound:
        row["account_charge_microusd"] = delta
    elif delta > bound:
        # Never trust an exposure movement larger than this dispatch's own bound.
        row["account_charge_microusd"] = bound
        row["charge_capped_at_bound"] = True
    else:
        # No attributable movement: keep the conservative reservation bound and
        # charge it in full against the incremental ceiling. A failed or
        # interrupted call is never refunded on the strength of a report.
        row["account_charge_microusd"] = None
    write_state(ctx.state_path, ctx.state)
    if failure is not None:
        if failure.outcome in {"fallback", "stop"}:
            # Provider retry guidance, when it exists, is honoured for this
            # route and every later dispatch, including a backup. A budget stop
            # is not route health and never opens a cooldown.
            record_cooldown(
                ctx.state,
                route.route_id,
                status_code=failure.status_code,
                retry_after=failure.retry_after,
                now=utcnow(),
                ends_at=ctx.ends_at,
            )
            write_state(ctx.state_path, ctx.state)
        return DispatchResult(failure=failure)
    return DispatchResult(response=response)


def verify_response(ctx: RunContext, response: JsonObject, role: str) -> str | None:
    """Corroborate the pinned route and the exact served model; never infer them.

    Returns a quality note when the answer completed but is not usable evidence
    (schema/semantic problems are quality failures, not retry triggers).
    """
    route = ctx.routes[role]
    expected = route.model.removeprefix("openrouter/")
    require(
        response.get("model") in {route.model, expected},
        "served model drift",
    )
    providers = route.transport.provider_only
    if not providers:
        raise ReliabilityError("provider pin missing")
    require(
        live.provider_echo_matches(response.get("provider"), providers[0]),
        "served provider drift",
    )
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return "invalid_choices"
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return "invalid_message"
    if message.get("tool_calls"):
        return "unexpected_tool_call"
    if choices[0].get("finish_reason") in {"length", "content_filter"}:
        return "truncated_response"
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return "empty_answer"
    return None


async def run_smoke(ctx: RunContext, smoke: Smoke) -> None:
    """One bounded single-dispatch qualification per endpoint (45 seconds)."""
    entry: JsonObject = {
        "smoke_id": smoke.smoke_id,
        "role": smoke.role,
        "status": "in_progress",
        "dispatches": [],
        "outcome": None,
    }
    ctx.state["smokes"][smoke.smoke_id] = entry
    write_state(ctx.state_path, ctx.state)
    cooling = active_cooldown(ctx.state, ctx.routes[smoke.role].route_id, utcnow())
    if cooling is not None:
        entry["outcome"] = "skipped_cooldown"
        entry["status"] = "skipped"
        entry["cooldown_until"] = iso(cooling)
        write_state(ctx.state_path, ctx.state)
        return
    started = monotonic()
    deadline = started + SMOKE_DEADLINE_S
    result = await dispatch_once(
        ctx,
        entry,
        case=None,
        role=smoke.role,
        deadline=deadline,
        pending_bounds=(),
        max_output_tokens=COMMON_MAX_OUTPUT_TOKENS
        if smoke.smoke_id == RECHECK_SMOKE_ID
        else SMOKE_MAX_OUTPUT_TOKENS,
    )
    if result.response is not None:
        note = verify_response(ctx, result.response, smoke.role)
        entry["outcome"] = "completed" if note is None else f"quality:{note}"
        entry["status"] = "completed" if note is None else "quality_failed"
    elif result.failure is not None:
        if result.failure.outcome == "abort":
            entry["outcome"] = "aborted"
            entry["status"] = "aborted"
            write_state(ctx.state_path, ctx.state)
            raise ReliabilityError(f"smoke {smoke.smoke_id} aborted the run")
        entry["outcome"] = (
            "capacity_stop" if result.failure.outcome == "capacity_stop" else "failed"
        )
        entry["status"] = entry["outcome"]
    else:
        entry["outcome"] = result.block or "skipped"
        entry["status"] = "skipped"
    entry["latency_seconds"] = monotonic() - started
    write_state(ctx.state_path, ctx.state)


async def run_logical(ctx: RunContext, logical: Logical) -> None:
    """Run one predeclared logical case through its arm's fixed route order."""
    case = ctx.cases[logical.case_id]
    entry: JsonObject = {
        "logical_id": logical.logical_id,
        "arm": logical.arm,
        "case_id": logical.case_id,
        "repeat": logical.repeat,
        "status": "in_progress",
        "dispatches": [],
        "skipped": [],
        "avoided_dispatch": False,
        "outcome": None,
        "schema_valid": None,
    }
    ctx.state["logicals"][logical.logical_id] = entry
    write_state(ctx.state_path, ctx.state)
    started = monotonic()
    budget_s = (
        REDUNDANT_DEADLINE_S if logical.arm == "primary_then_alternate" else SINGLE_ARM_DEADLINE_S
    )
    deadline = started + budget_s
    require(
        len(logical.route_roles) <= MAX_DISPATCHES_PER_LOGICAL[logical.arm]
        and len(logical.route_roles) <= min(CASE_MAX_DISPATCHES, case.max_calls),
        "planned arm exceeds the case dispatch budget",
    )
    # Bound every dispatch this arm could still reach, so a backup attempt is
    # admitted against both ceilings before the first call is reserved.
    reachable = [await route_preflight_bounds(ctx, case, role) for role in logical.route_roles]
    for index, role in enumerate(logical.route_roles):
        if index >= MAX_DISPATCHES_PER_LOGICAL[logical.arm]:
            entry["outcome"] = "case_dispatch_budget_exhausted"
            break
        now = utcnow()
        cooling = active_cooldown(ctx.state, ctx.routes[role].route_id, now)
        if cooling is not None:
            entry["skipped"].append(
                {
                    "route_id": ctx.routes[role].route_id,
                    "role": role,
                    "reason": "cooldown",
                    "until": iso(cooling),
                }
            )
            write_state(ctx.state_path, ctx.state)
            if index == 0 and len(logical.route_roles) > 1:
                # The arm is unchanged: the primary dispatch is avoided, not
                # replaced, and never counted as a recovered failure.
                entry["avoided_dispatch"] = True
                continue
            entry["outcome"] = "skipped_cooldown" if index == 0 else "backup_cooldown"
            break
        result = await dispatch_once(
            ctx,
            entry,
            case=case,
            role=role,
            deadline=deadline,
            pending_bounds=reachable[index + 1 :],
        )
        if result.block is not None:
            # A capacity stop and a deadline stop are distinct from a provider
            # failure and are reported separately in the arm comparison.
            entry["outcome"] = result.block
            break
        if result.failure is not None:
            failure = result.failure
            if failure.outcome == "abort":
                entry["outcome"] = "aborted"
                entry["status"] = "aborted"
                write_state(ctx.state_path, ctx.state)
                raise ReliabilityError(f"logical {logical.logical_id} aborted the run")
            if failure.outcome == "fallback" and index + 1 < len(logical.route_roles):
                continue
            entry["outcome"] = "capacity_stop" if failure.outcome == "capacity_stop" else "failed"
            break
        if result.response is None:
            raise ReliabilityError("dispatch returned neither response nor failure")
        note = verify_response(ctx, result.response, role)
        if note is None:
            content = result.response["choices"][0]["message"]["content"]
            entry["final_response"] = content
            if case.requires_schema:
                try:
                    entry["schema_valid"] = live.schema_matches(
                        json.loads(content), case.schema or {}
                    )
                except ValueError:
                    entry["schema_valid"] = False
        if note is not None or entry["schema_valid"] is False:
            # A schema-invalid or semantically bad answer is a quality failure,
            # never a hidden provider retry.
            entry["outcome"] = f"quality:{note or 'schema_invalid'}"
            break
        if entry["avoided_dispatch"]:
            entry["outcome"] = "avoided_dispatch"
        elif index == 0:
            entry["outcome"] = "completed"
        else:
            entry["outcome"] = "recovered"
        break
    else:
        entry["outcome"] = "no_dispatch_attempted"
    entry["status"] = entry["outcome"] or "unknown"
    entry["latency_seconds"] = monotonic() - started
    # Actual dispatches attempted, not successful ones: the budget counts every
    # provider call, including the ones that failed or were avoided.
    entry["dispatch_count"] = len(entry["dispatches"])
    write_state(ctx.state_path, ctx.state)


# ---------------------------------------------------------------------------
# Results artifact (deliberately not the offline scorer import format)
# ---------------------------------------------------------------------------


def _arm_summary(rows: Sequence[JsonObject]) -> JsonObject:
    outcomes: dict[str, int] = {}
    for row in rows:
        key = str(row.get("outcome"))
        outcomes[key] = outcomes.get(key, 0) + 1
    latencies = [
        float(row["latency_seconds"])
        for row in rows
        if isinstance(row.get("latency_seconds"), float)
    ]
    known_charge = 0
    provider_cost = 0.0
    for row in rows:
        for dispatch in row.get("dispatches", []):
            charge = dispatch.get("account_charge_microusd")
            if isinstance(charge, int) and not isinstance(charge, bool):
                known_charge += charge
            cost = dispatch.get("provider_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                provider_cost += float(cost)
    return {
        "logicals": len(rows),
        "dispatches": sum(len(row.get("dispatches", [])) for row in rows),
        "outcomes": dict(sorted(outcomes.items())),
        "first_dispatch_failures": sum(
            1
            for row in rows
            if row.get("dispatches") and row["dispatches"][0].get("status") != "completed"
        ),
        "recovered_failures": outcomes.get("recovered", 0),
        "avoided_dispatches": outcomes.get("avoided_dispatch", 0),
        "capacity_stops": outcomes.get("capacity_stop", 0),
        "deadline_exceeded": outcomes.get("deadline_exceeded", 0),
        "backup_cooldown": outcomes.get("backup_cooldown", 0),
        "skipped_cooldown": outcomes.get("skipped_cooldown", 0),
        "schema_valid": sum(1 for row in rows if row.get("schema_valid") is True),
        "latency_seconds_mean": (sum(latencies) / len(latencies)) if latencies else None,
        "known_charge_microusd": known_charge,
        "provider_invoice_cost_usd": provider_cost or None,
    }


def build_results(state: JsonObject, exposure: LedgerExposure | None) -> JsonObject:
    """A private summary artifact. Every semantic verdict stays pending."""
    logicals = list(state["logicals"].values())
    arms = {arm: _arm_summary([row for row in logicals if row.get("arm") == arm]) for arm in ARMS}
    baseline = state.get("account_ledger_baseline")
    return {
        "artifact_version": RESULTS_ARTIFACT_VERSION,
        "scorer_compatible": False,
        "identity": state["identity"],
        "plan_sha256": state["identity"].get("plan_sha256"),
        "plan_amendment": state.get("plan_amendment"),
        "primary_compatibility_evidence": state.get("primary_compatibility_evidence"),
        "bounds": {
            "incremental_cap_microusd": INCREMENTAL_CAP_MICROUSD,
            "aggregate_cap_microusd": AGGREGATE_CAP_MICROUSD,
            "total_dispatch_bound": TOTAL_DISPATCH_BOUND + int(amended(state)),
            "dispatches_recorded": len(dispatch_rows(state)),
        },
        "accounting": {
            "accounted_microusd": accounted_microusd(state),
            "unknown_charge_dispatches": sum(
                1 for row in dispatch_rows(state) if row.get("account_charge_microusd") is None
            ),
            "frozen_baseline_microusd": (
                baseline.get("exposure_microusd") if isinstance(baseline, dict) else None
            ),
            "ledger_exposure_now_microusd": (
                exposure.total_microusd if exposure is not None else None
            ),
            "ledger_delta_microusd": (
                ledger_delta_microusd(state, exposure) if exposure is not None else None
            ),
        },
        "cooldowns": {
            route_id: record.get("until") for route_id, record in sorted(state["cooldowns"].items())
        },
        "smokes": [
            {
                "smoke_id": entry.get("smoke_id"),
                "role": entry.get("role"),
                "outcome": entry.get("outcome"),
                "dispatches": len(entry.get("dispatches", [])),
            }
            for entry in state["smokes"].values()
        ],
        "arms": arms,
        "logicals": [
            {
                "logical_id": row.get("logical_id"),
                "arm": row.get("arm"),
                "case_id": row.get("case_id"),
                "repeat": row.get("repeat"),
                "outcome": row.get("outcome"),
                "schema_valid": row.get("schema_valid"),
                "semantic_verdict": "pending",
                "avoided_dispatch": row.get("avoided_dispatch"),
                "dispatch_count": len(row.get("dispatches", [])),
                "skipped": row.get("skipped", []),
                "latency_seconds": row.get("latency_seconds"),
            }
            for row in logicals
        ],
        "notes": (
            "Same-model endpoint redundancy evaluation. Semantic verdicts are always pending: "
            "this artifact is not the offline scorer import format and contains no human "
            "adjudication. Failed, skipped and capacity-stopped dispatches are included; "
            "avoided primary dispatches are not recovered failures. Cooldowns and health are "
            "local to this evaluation runner, not a distributed circuit breaker."
        ),
    }


def write_results(path: Path, results: JsonObject) -> None:
    require(not path.exists(), "results file already exists")
    require(path.parent.is_dir(), "results directory missing")
    write_state(path, results)


# ---------------------------------------------------------------------------
# Preflight, phases and CLI
# ---------------------------------------------------------------------------


def validate_period(period: str) -> None:
    require(
        len(period) == 7 and datetime.strptime(period, "%Y-%m").strftime("%Y-%m") == period,
        "period must be YYYY-MM",
    )
    require(period == ORIGINAL_FUNDED_PERIOD, "only the original funded period is authorized")


def bound_for(
    resolved: Any,
    routes: Mapping[str, RoutePolicy],
    role: str,
    case: pilot.CaseFixture | None,
    max_output_tokens: int | None = None,
) -> int:
    """Worst-case charge for one role's request to one case (or the smoke)."""
    route = routes[role]
    max_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else COMMON_MAX_OUTPUT_TOKENS
        if case is not None
        else SMOKE_MAX_OUTPUT_TOKENS
    )
    return route_preflight(resolved, route, completion_params(case, route, max_tokens), case)


def worst_case_bounds(
    routes: Mapping[str, RoutePolicy],
    cases: Mapping[str, pilot.CaseFixture],
    resolved: Any,
    logicals: Sequence[Logical],
    *,
    include_recheck: bool = False,
) -> JsonObject:
    """The full 34-dispatch worst case, computed per route and per case.

    Both ceilings are checked up front: the incremental experiment ceiling and
    the remaining shared account allowance. This is an additional safeguard, not
    a substitute for the per-dispatch ledger admission, and it is a proof over
    the *whole* plan rather than a running subtotal.
    """
    worst_case: JsonObject = {"smokes": {}, "logicals": {}}
    total = 0
    for role in ROUTE_ROLES:
        bound = bound_for(resolved, routes, role, None)
        worst_case["smokes"][role] = bound
        total += bound
    if include_recheck:
        recheck_bound = bound_for(resolved, routes, "primary", None, COMMON_MAX_OUTPUT_TOKENS)
        worst_case["recheck"] = {RECHECK_SMOKE_ID: recheck_bound}
        total += recheck_bound
    for logical in logicals:
        case = cases[logical.case_id]
        bounds = {role: bound_for(resolved, routes, role, case) for role in logical.route_roles}
        worst_case["logicals"][logical.logical_id] = bounds
        total += sum(bounds.values())
    dispatch_count = (
        SMOKE_DISPATCH_COUNT
        + int(include_recheck)
        + sum(len(item.route_roles) for item in logicals)
    )
    require(
        dispatch_count == TOTAL_DISPATCH_BOUND + int(include_recheck),
        "planned dispatch count does not match the approved global bound",
    )
    require(len(worst_case["logicals"]) == len(logicals), "schedule is not the recorded plan")
    worst_case["total_microusd"] = total
    return worst_case


def smokes_allow_run(state: JsonObject) -> tuple[bool, str]:
    """The live arms require two successful smokes; a failed smoke is never replayed."""
    for role in ROUTE_ROLES:
        entry = state["smokes"].get(f"smoke-{role}")
        if not isinstance(entry, dict):
            return False, f"smoke for {role} has not run"
        if entry.get("outcome") == "completed":
            continue
        if role == "primary" and amended(state) and recheck_eligible(state):
            recheck = state["smokes"].get(RECHECK_SMOKE_ID)
            if isinstance(recheck, dict) and recheck.get("outcome") == "completed":
                continue
            if prior_compatibility_admits(state):
                continue
        return False, f"pilot blocked: {role} smoke did not complete ({entry.get('outcome')})"
    return True, ""


def prior_compatibility_admits(state: JsonObject) -> bool:
    """One approved compatibility exception; current route qualification still applies."""
    record = state.get("primary_compatibility_evidence")
    if not isinstance(record, dict):
        return False
    payload = {key: value for key, value in record.items() if key != "sha256"}
    require(
        hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        == record.get("sha256"),
        "compatibility evidence drift",
    )
    identity = state["identity"]
    require(
        record.get("rule") == "prior-primary-compatibility/1"
        and record.get("plan_sha256") == identity["plan_sha256"]
        and record.get("model") == identity["exact_model"]
        and record.get("route_id") == identity["primary_route_id"],
        "compatibility evidence identity drift",
    )
    recheck = state["smokes"].get(RECHECK_SMOKE_ID, {})
    rows = recheck.get("dispatches", [])
    return (
        amended(state)
        and recheck.get("outcome") == "failed"
        and len(rows) == 1
        and rows[0].get("failure", {}).get("category") == "rate_limited"
        and rows[0].get("failure", {}).get("status_code") == 429
    )


def admit_prior_compatibility(state: JsonObject, route: RoutePolicy, source_path: Path) -> None:
    """Bind the approved prior successful pinned call without rewriting failed smokes."""
    require(not state["logicals"], "compatibility admission must precede logical calls")
    require(amended(state) and recheck_eligible(state), "recheck amendment required")
    require(source_path.is_file() and not source_path.is_symlink(), "evidence path invalid")
    raw = source_path.read_bytes()
    source = json.loads(raw)
    require(source.get("version") == "model-roster-live-state/1", "evidence version invalid")
    identity = state["identity"]
    for key in (
        "account",
        "period_key",
        "database",
        "database_url_sha256",
        "fixtures_sha256",
        "commercial_sha256",
    ):
        require(source["identity"].get(key) == identity.get(key), "evidence scope mismatch")
    attempt_id = "O01-glm-flash-r2"
    entry = source.get("attempts", {}).get(attempt_id, {})
    calls = entry.get("calls", [])
    require(entry.get("status") == "completed" and len(calls) == 1, "prior success missing")
    call = calls[0]
    expected = route.model.removeprefix("openrouter/")
    providers = route.transport.provider_only
    if not providers:
        raise ReliabilityError("provider pin missing")
    response = call.get("raw_response", {})
    require(
        call.get("status") == "completed"
        and call.get("route_id") == source["identity"].get("pins", {}).get("glm-flash")
        and call.get("requested_model") == route.model
        and call.get("provider_pin") == list(providers)
        and response.get("model") in {expected, route.model}
        and response.get("provider") is not None
        and live.provider_echo_matches(response.get("provider"), providers[0]),
        "prior exact-route evidence mismatch",
    )
    choices = response.get("choices", [])
    require(
        len(choices) == 1
        and choices[0].get("finish_reason") == "stop"
        and bool(choices[0].get("message", {}).get("content"))
        and not choices[0].get("message", {}).get("tool_calls"),
        "prior terminal answer missing",
    )
    payload = {
        "rule": "prior-primary-compatibility/1",
        "plan_sha256": identity["plan_sha256"],
        "model": route.model,
        "route_id": route.route_id,
        "provider_pin": list(providers),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "attempt_id": attempt_id,
        "call_sha256": hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest(),
    }
    record = {
        **payload,
        "sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }
    require(
        state.get("primary_compatibility_evidence") in (None, record), "evidence already differs"
    )
    state["primary_compatibility_evidence"] = record
    require(prior_compatibility_admits(state), "only the recorded 429 recheck is admissible")


def check_worst_case(
    routes: Mapping[str, RoutePolicy],
    cases: Mapping[str, pilot.CaseFixture],
    resolved: Any,
    logicals: Sequence[Logical],
    exposure: LedgerExposure,
    *,
    include_recheck: bool = False,
) -> JsonObject:
    """Prove the whole plan fits both ceilings before any dispatch is admitted."""
    bounds = worst_case_bounds(routes, cases, resolved, logicals, include_recheck=include_recheck)
    total = int(bounds["total_microusd"])
    require(
        total <= INCREMENTAL_CAP_MICROUSD,
        "worst-case dispatch bound exceeds the incremental ceiling",
    )
    require(
        exposure.total_microusd + total <= account_ceiling(resolved),
        "worst-case bound exceeds the remaining shared account allowance",
    )
    return bounds


def account_ceiling(resolved: Any) -> int:
    """The shared aggregate cap, further limited by this account's own budget."""
    return min(AGGREGATE_CAP_MICROUSD, int(resolved.recurring_budget_microusd))


async def run(args: argparse.Namespace) -> int:
    evidence_path = getattr(args, "primary_evidence", None)
    require(evidence_path is None or args.phase == "run", "evidence only applies to run admission")
    if not args.dry_run:
        require(not args.results.exists(), "results file already exists")
    commercial_path, inference_path = live.policy_paths()
    policy = load_policy(commercial_path)
    live.validate_commercial(policy)
    inference = load_inference_policy(inference_path)
    validate_period(args.period)
    fixtures = pilot.load_fixtures(args.fixtures)
    cases = fixtures.by_id()
    require(
        all(case_id in cases for case_id in UTILITY_CASE_IDS),
        "utility fixtures missing from the approved fixture set",
    )
    routes = qualified_routes(inference, args.primary_route, args.alternate_route)
    if any(route.route_class == "premium" for route in routes.values()):
        require(
            Capability.PREMIUM_ROUTING in policy.plan("pro").capabilities,
            "premium route needs evaluation pro-plan capability (no trial funding)",
        )
    logicals = build_logicals()
    smokes = build_smokes()
    identity: JsonObject = {
        "account": str(args.account),
        "period_key": args.period,
        "primary_route_id": args.primary_route,
        "alternate_route_id": args.alternate_route,
        "exact_model": routes["primary"].model,
        "commercial_sha256": live.digest(commercial_path),
        "inference_sha256": live.digest(inference_path),
        "fixtures_sha256": fixtures.sha256,
        "plan_sha256": plan_fingerprint(logicals, smokes),
    }

    if args.dry_run:
        # Read-only preflight: database reads and bound inspection, never an
        # inference call, never a state write, never a reservation.
        require(
            "DATABASE_URL" in Settings.explicit_evaluation_environment()
            and get_settings().database_url
            == Settings.explicit_evaluation_environment()["DATABASE_URL"],
            "explicit evaluation DATABASE_URL required",
        )
        pool = await asyncpg.create_pool(
            dsn=Settings.explicit_evaluation_environment()["DATABASE_URL"], min_size=1, max_size=2
        )
        try:
            service = EntitlementService(pool)
            db_name = await live.validate_database(pool, args.account, service, args.period)
            identity["database"] = db_name
            identity["database_url_sha256"] = hashlib.sha256(
                Settings.explicit_evaluation_environment()["DATABASE_URL"].encode()
            ).hexdigest()
            existing: JsonObject | None = None
            if args.state.exists():
                require(args.state.is_file() and not args.state.is_symlink(), "state path invalid")
                require(
                    args.state.parent.stat().st_mode & 0o077 == 0, "state directory must be private"
                )
                loaded = json.loads(args.state.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ReliabilityError("state must be an object")
                existing = loaded
                require(existing.get("version") == STATE_VERSION, "state version changed")
                require(existing.get("identity") == identity, "run identity drift")
                require(isinstance(existing.get("smokes"), dict), "state smokes invalid")
            if args.phase == "recheck":
                require(existing is not None and recheck_eligible(existing), "recheck not eligible")
            include_recheck = amended(existing) if existing is not None else False
            if evidence_path is not None:
                if existing is None:
                    raise ReliabilityError("compatibility admission requires original state")
                admit_prior_compatibility(existing, routes["primary"], evidence_path)
            if args.phase == "recheck":
                if existing is None:
                    raise ReliabilityError("recheck requires original state")
                require(
                    RECHECK_SMOKE_ID in existing["smokes"] or not existing["logicals"],
                    "logical calls already started",
                )
                require(existing.get("account_ledger_baseline") is not None, "baseline missing")
                include_recheck = True
            resolved = await service.resolve(args.account)
            exposure = await read_ledger_exposure(pool, args.account, args.period)
            bounds = check_worst_case(
                routes, cases, resolved, logicals, exposure, include_recheck=include_recheck
            )
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "identity": identity,
                        "worst_case": bounds,
                        "max_dispatches": TOTAL_DISPATCH_BOUND + int(include_recheck),
                        "logical_calls": len(logicals),
                        "incremental_cap_microusd": INCREMENTAL_CAP_MICROUSD,
                        "account_ceiling_microusd": account_ceiling(resolved),
                        "ledger_exposure_microusd": exposure.total_microusd,
                        "open_holds": exposure.open_holds,
                        "phase": args.phase,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        finally:
            await pool.close()
        return 0

    require(
        "DATABASE_URL" in Settings.explicit_evaluation_environment()
        and get_settings().database_url
        == Settings.explicit_evaluation_environment()["DATABASE_URL"],
        "explicit evaluation DATABASE_URL required",
    )
    require(bool(get_settings().openrouter_api_key), "OpenRouter credential missing")
    pool = await asyncpg.create_pool(
        dsn=Settings.explicit_evaluation_environment()["DATABASE_URL"], min_size=1, max_size=2
    )
    try:
        service = EntitlementService(pool)
        db_name = await live.validate_database(pool, args.account, service, args.period)
        identity["database"] = db_name
        identity["database_url_sha256"] = hashlib.sha256(
            Settings.explicit_evaluation_environment()["DATABASE_URL"].encode()
        ).hexdigest()
        resolved = await service.resolve(args.account)

        if args.phase == "recheck":
            require(args.state.exists(), "recheck requires existing state")
        with locked_state(args.state, identity) as state:
            if args.phase == "recheck":
                require(recheck_eligible(state), "recheck not eligible")
                require(state.get("account_ledger_baseline") is not None, "baseline missing")
                if RECHECK_SMOKE_ID not in state["smokes"]:
                    require(not state["logicals"], "logical calls already started")
                if not amended(state):
                    payload = amendment_payload(identity["plan_sha256"])
                    state["plan_amendment"] = {
                        **payload,
                        "amendment_sha256": hashlib.sha256(
                            json.dumps(payload, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                    }
                    write_state(args.state, state)
            include_recheck = amended(state)
            exposure = await read_ledger_exposure(pool, args.account, args.period)
            if state.get("account_ledger_baseline") is None:
                # Initial start only. A frozen baseline is what makes a ledger
                # delta evidence about this experiment rather than a guess about
                # a scope, an id, or the account's unrelated prior spend.
                require(
                    exposure.open_holds == 0,
                    "no open reservations allowed before freezing the ledger baseline",
                )
                state["account_ledger_baseline"] = {
                    "exposure_microusd": exposure.total_microusd,
                    "open_holds": exposure.open_holds,
                    "recorded_at": iso(utcnow()),
                    "period_key": args.period,
                }
                write_state(args.state, state)
            else:
                # A restart adds no budget: the ceiling is re-derived from the
                # durable rows, not from a fresh or re-based allowance.
                require(
                    accounted_microusd(state) <= INCREMENTAL_CAP_MICROUSD,
                    "durable experiment accounting already exceeds the incremental ceiling",
                )
            if args.phase == "run":
                if evidence_path is not None:
                    admit_prior_compatibility(state, routes["primary"], evidence_path)
                    write_state(args.state, state)
                allowed, reason = smokes_allow_run(state)
                require(allowed, reason or "both smokes must complete before the live arms")
            require(
                args.phase in {"smoke", "recheck"}
                or len(dispatch_rows(state))
                + sum(
                    len(logical.route_roles)
                    for logical in logicals
                    if logical.logical_id not in state["logicals"]
                )
                <= TOTAL_DISPATCH_BOUND + int(include_recheck),
                "recorded dispatches leave no room for the declared arms",
            )
            bounds = check_worst_case(
                routes, cases, resolved, logicals, exposure, include_recheck=include_recheck
            )
            state["preflight"] = {
                "worst_case_microusd": bounds["total_microusd"],
                "dispatch_bound": TOTAL_DISPATCH_BOUND + int(include_recheck),
                "checked_at": iso(utcnow()),
                "phase": args.phase,
            }
            write_state(args.state, state)

            ctx = RunContext(
                pool=pool,
                account=args.account,
                service=service,
                period=args.period,
                identity=identity,
                state=state,
                state_path=args.state,
                routes=routes,
                cases={case_id: cases[case_id] for case_id in UTILITY_CASE_IDS},
                account_ceiling_microusd=account_ceiling(resolved),
                ends_at=period_end(args.period),
            )
            try:
                if args.phase == "smoke":
                    for smoke in smokes:
                        if smoke.smoke_id in state["smokes"]:
                            # Recorded, including in-progress: never replayed.
                            continue
                        await run_smoke(ctx, smoke)
                elif args.phase == "recheck":
                    if RECHECK_SMOKE_ID not in state["smokes"]:
                        await run_smoke(ctx, Smoke(RECHECK_SMOKE_ID, "primary"))
                else:
                    for logical in logicals:
                        if logical.logical_id in state["logicals"]:
                            continue
                        await run_logical(ctx, logical)
            finally:
                final = await read_ledger_exposure(pool, args.account, args.period)
                write_results(args.results, build_results(state, final))
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=pilot.DEFAULT_FIXTURES_PATH)
    parser.add_argument("--account", type=uuid.UUID, required=True)
    parser.add_argument("--period", required=True, help="original funded UTC month, YYYY-MM")
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="private persistent JSON state; parent directory must be private (0700)",
    )
    parser.add_argument(
        "--results", type=Path, required=True, help="new private summary artifact (never exists)"
    )
    parser.add_argument("--primary-route", required=True, help="exact approved route ID")
    parser.add_argument("--alternate-route", required=True, help="different approved route ID")
    parser.add_argument(
        "--primary-evidence",
        type=Path,
        help="Approved prior pinned-success state for admitting the recorded primary 429 recheck",
    )
    parser.add_argument(
        "--phase",
        choices=("smoke", "recheck", "run"),
        required=True,
        help="smoke qualifies endpoints; recheck permits one primary qualification; run executes arms",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only preflight: database reads and bound inspection, no inference",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (
        ReliabilityError,
        pilot.ArtifactError,
        EntitlementsError,
        compute_runtime.ComputeUnavailable,
        OSError,
        ValueError,
        asyncpg.PostgresError,
    ) as exc:
        print(f"reliability run rejected: {sanitize(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("reliability run interrupted", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"reliability run failed: {type(exc).__name__}", file=sys.stderr)
        return 2


def sanitize(text: str) -> str:
    """Redact the live OpenRouter credential from any operator-facing message."""
    try:
        key = get_settings().openrouter_api_key
    except Exception:
        key = ""
    return text.replace(key, "[REDACTED]") if key else text


if __name__ == "__main__":
    raise SystemExit(main())
