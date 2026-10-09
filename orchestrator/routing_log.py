"""Server-side routing telemetry: one allowlisted JSON line per routing event.

Records let an operator reconstruct a turn's classification, candidate walk,
attempts, fallbacks, settlements and cancellation, and join them to the ledger by
``scope_id`` and ``reservation_id``. They are written only to the server log; no
client contract (SSE events or the routing ``reason`` string) changes.

Privacy: fields are identifiers, enumerations, counts and amounts from a fixed
allowlist. Message or tool content, reasoning text, credentials, endpoints, headers,
email and raw user ids are never recorded. A record that fails validation is
dropped, and emitting never raises into dispatch or settlement.

The supported runtime launcher manages output sinks; this logger also uses a
safe sink for direct imports. Arbitrary caller text is never a log vocabulary.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from typing import Final, cast

from orchestrator import safe_logging

LOGGER_NAME: Final[str] = "daemon.routing"
PREFIX: Final[str] = "routing_event"
MAX_STRING_LENGTH: Final[int] = 200
MAX_LIST_LENGTH: Final[int] = 64

#: Fields each event may carry. Anything else is a programming error.
EVENT_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    "decision": frozenset(
        {
            "request_id",
            "endpoint",
            "auto",
            "explicit_model",
            "profile",
            "tier",
            "classifier_version",
            "complexity_signals",
            "research_signals",
            "empty_text",
            "attachment_count",
            "admission",
        }
    ),
    "scope_open": frozenset(
        {
            "request_id",
            "scope_id",
            "operation",
            "profile",
            "auto_route",
            "account_allow_premium",
            "background",
            "extended",
        }
    ),
    "candidates": frozenset(
        {
            "scope_id",
            "completion_seq",
            "profile",
            "plan",
            "explicit",
            "pinned",
            "stream",
            "route_ids",
            "candidate_count",
            "exclusions",
        }
    ),
    "attempt": frozenset(
        {
            "scope_id",
            "completion_seq",
            "attempt_index",
            "route_id",
            "model",
            "group",
            "premium",
            "explicit",
            "pinned",
            "requested_effort",
            "preset_effort",
            "sent_effort",
            "include_reasoning",
            "max_tokens",
            "hold_bound",
            "budget_fitted",
            "reservation_id",
            "stream",
        }
    ),
    "attempt_outcome": frozenset(
        {
            "scope_id",
            "completion_seq",
            "attempt_index",
            "reservation_id",
            "outcome",
            "failure_category",
            "status_code",
            "retryable",
            "output_released",
            "next_action",
        }
    ),
    "settlement": frozenset(
        {
            "scope_id",
            "reservation_id",
            "path",
            "status",
            "actual",
            "hold_bound",
            "estimated",
            "input_tokens",
            "output_tokens",
            "overage",
        }
    ),
    "profile_fallback": frozenset({"scope_id", "from_profile", "to_profile", "cause"}),
    "scope_close": frozenset(
        {
            "request_id",
            "scope_id",
            "operation",
            "exit",
            "completions",
            "attempts",
            "settled_total",
            "first_output_ms",
            "duration_ms",
        }
    ),
}


class RoutingLogError(ValueError):
    """A routing record that would break the allowlist contract."""


_BOOLEANS = frozenset(
    {
        "auto",
        "empty_text",
        "auto_route",
        "account_allow_premium",
        "background",
        "extended",
        "explicit",
        "pinned",
        "stream",
        "premium",
        "include_reasoning",
        "budget_fitted",
        "retryable",
        "output_released",
        "estimated",
    }
)
_NUMBERS = frozenset(
    {
        "attachment_count",
        "completion_seq",
        "candidate_count",
        "attempt_index",
        "max_tokens",
        "hold_bound",
        "status_code",
        "actual",
        "input_tokens",
        "output_tokens",
        "overage",
        "completions",
        "attempts",
        "settled_total",
        "first_output_ms",
        "duration_ms",
    }
)
_ENUMS = {
    "operation": {"chat", "agent"},
    "endpoint": {"chat", "openai", "chat:openai", "chat:daemon"},
    "tier": {"simple", "routine", "reasoning", "research", "explicit"},
    "plan": {"free", "pro", "power", "trial"},
    "requested_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "preset_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "sent_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "outcome": {"failed", "completed", "cancelled", "denied", "revoked", "refused"},
    "next_action": {"fallback", "raise", "none"},
    "failure_category": {
        "rate_limited",
        "upstream_unavailable",
        "connection_failed",
        "timeout",
        "deadline_exceeded",
        "authentication_failed",
        "payment_failed",
        "invalid_request",
        "settlement_failed",
        "unspecified",
        "entitlements",
    },
    "path": {
        "scope_cleanup",
        "tool_not_dispatched",
        "embedding_not_dispatched",
        "embedding_call",
        "tool_call",
        "dispatch_failure",
        "completed",
        "stream_end",
        "receipt_reconciliation",
        "expiry_recovery",
    },
    "status": {"open", "settled", "released"},
    "exit": {"normal", "cancelled", "closed", "error", "error:entitlements"},
    "admission": {
        "admitted",
        "route_unavailable",
        "capacity_unavailable",
        "profile_unavailable",
        "capability_unavailable",
        "account_unavailable",
    },
    "cause": {
        "capability_unavailable",
        "budget_exceeded",
        "capacity_unavailable",
        "route_unavailable",
        "profile_unavailable",
        "reasoning_unavailable",
    },
}
_COMPUTE_CODES = frozenset(
    {
        "rate_limited",
        "concurrency_exceeded",
        "trial_exhausted",
        "trial_extended_agents_exhausted",
        "extended_agents_exceeded",
        "extended_budget_exceeded",
        "limit_exceeded",
        "account_suspended",
        "account_unavailable",
        "budget_exceeded",
        "capability_unavailable",
        "capacity_unavailable",
        "context_limit",
        "embedding_price_exceeded",
        "embedding_unavailable",
        "extended_agents_exhausted",
        "modality_unavailable",
        "profile_unavailable",
        "reservation_outcome_unresolved",
        "route_unavailable",
        "settlement_conflict",
        "settlement_failed",
        "tool_price_exceeded",
        "tool_service_unavailable",
    }
)
_ENUMS["admission"].update(_COMPUTE_CODES | {"denied"})
_ENUMS["exit"].update(f"error:{code}" for code in _COMPUTE_CODES)
_EXCLUSIONS = frozenset(
    {
        "premium_not_allowed",
        "effort",
        "sampling",
        "route_capabilities",
        "output_floor",
        "budget",
        "qualification",
        "capability",
        "context",
        "output",
        "reasoning",
        "premium",
        "excluded",
        "policy",
        "unavailable",
    }
)


_dynamic_vocabulary: dict[str, frozenset[str]] = {}


def initialize_vocabulary() -> None:
    """Snapshot trusted diagnostic labels before work; never load from a sink.

    A failed initialization leaves dynamic labels denied. Diagnostic snapshots
    follow the process lifetime, not caller-supplied labels or runtime reloads.
    """
    global _dynamic_vocabulary
    _dynamic_vocabulary = {}
    from orchestrator import model_routing
    from orchestrator import model_router
    from orchestrator.entitlements.policy import load_inference_policy

    config = model_routing.load_model_routing()
    policy = load_inference_policy()
    profiles = frozenset(config.profiles)
    models = frozenset(config.models) | frozenset(route.model for route in policy.routes.values())
    routes = frozenset(policy.routes)
    _dynamic_vocabulary = {
        "profile": profiles,
        "from_profile": profiles,
        "to_profile": profiles,
        "group": frozenset(
            group.name for profile in config.profiles.values() for group in profile.groups
        ),
        "model": models,
        "explicit_model": models,
        "route_id": routes,
        "route_ids": routes,
        "classifier_version": frozenset({model_router.CLASSIFIER_VERSION}),
        "research_signals": frozenset(model_router.RESEARCH_SIGNALS),
        "complexity_signals": frozenset(
            model_router.COMPLEXITY_SIGNALS | model_router.COMPLEXITY_SIGNAL_FORMS
        ),
    }


def _vocabulary(field: str) -> set[str] | frozenset[str]:
    return _ENUMS[field] if field in _ENUMS else _dynamic_vocabulary.get(field, frozenset())


def _clean(value: object, *, field: str) -> object:
    if value is None:
        return None
    if field in _BOOLEANS and type(value) is bool:
        return value
    if field in _NUMBERS and type(value) is int:
        if 0 <= value <= 10**18:
            return value
        raise RoutingLogError("invalid diagnostic number")
    # Scope/reservation ids have server-generated provenance at the inspected
    # compute/ledger emit sites, not the account/user id. Request ids are omitted
    # below: the generic account_compute argument does not prove their origin.
    if field in {"scope_id", "reservation_id"} and type(value) is str:
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            return None
        return str(parsed) if parsed.version == 4 else None
    if field == "exclusions" and type(value) is dict:
        counts: dict[str, int] = {}
        for key, count in value.items():
            if type(key) is not str or type(count) is not int or not 0 <= count <= 10**18:
                raise RoutingLogError("invalid exclusion count")
            if key in _EXCLUSIONS:
                counts[key] = count
        return counts
    if field in {"route_ids", "complexity_signals", "research_signals"} and type(value) in {
        list,
        tuple,
    }:
        items = cast(list[object] | tuple[object, ...], value)
        permitted = _vocabulary(field)
        if any(type(item) is not str for item in items):
            raise RoutingLogError("invalid diagnostic list")
        return [
            item for item in items[:MAX_LIST_LENGTH] if isinstance(item, str) and item in permitted
        ]
    if type(value) is str and field not in _BOOLEANS | _NUMBERS:
        return (
            value
            if len(value) <= MAX_STRING_LENGTH
            and (value in _vocabulary(field) or value == "unrecognized")
            else "unrecognized"
        )
    raise RoutingLogError("invalid diagnostic type")


def build_record(event: str, fields: Mapping[str, object]) -> dict[str, object]:
    """Validate ``fields`` against ``event``'s allowlist and return the record."""
    if (
        type(event) is not str
        or type(fields) is not dict
        or any(type(key) is not str for key in fields)
    ):
        raise RoutingLogError("invalid routing record")
    allowed = EVENT_FIELDS.get(event)
    if allowed is None:
        raise RoutingLogError("unknown routing event")
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise RoutingLogError("unknown routing fields")
    record: dict[str, object] = {"event": event}
    for name in sorted(fields):
        if name == "request_id":
            continue
        record[name] = _clean(fields[name], field=name)
    return record


def _configure() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(getattr(handler, "_daemon_routing", False) for handler in logger.handlers):
        handler = safe_logging.handler()
        handler._daemon_routing = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = _configure()
safe_logging.register_routing_validator(build_record)


def emit(event: str, **fields: object) -> None:
    """Write one routing record. Never raises; an invalid record is dropped."""
    try:
        record = build_record(event, fields)
        logger.info(
            "%s %s",
            PREFIX,
            json.dumps(record, separators=(",", ":"), sort_keys=True, allow_nan=False),
            extra={"_daemon_event": {"kind": "routing", "fields": record}},
        )
    except Exception:
        # Telemetry must never affect dispatch or settlement.
        with contextlib.suppress(Exception):
            logging.getLogger(__name__).warning("Dropped an invalid routing record")


def vocabulary_version(*vocabularies: frozenset[str] | set[str]) -> str:
    """A short, stable digest of classifier vocabularies, so records name the rules."""
    digest = hashlib.sha256()
    for vocabulary in vocabularies:
        digest.update("\x1f".join(sorted(vocabulary)).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:12]
