"""Server-side routing telemetry: one allowlisted JSON line per routing event.

Records let an operator reconstruct a turn's classification, candidate walk,
attempts, fallbacks, settlements and cancellation, and join them to the ledger by
``scope_id`` and ``reservation_id``. They are written only to the server log; no
client contract (SSE events or the routing ``reason`` string) changes.

Privacy: fields are identifiers, enumerations, counts and amounts from a fixed
allowlist. Message or tool content, reasoning text, credentials, endpoints, headers,
email and raw user ids are never recorded. A record that fails validation is
dropped, and emitting never raises into dispatch or settlement.

The application does not configure logging, and uvicorn's defaults leave the root
logger at WARNING with no handler, so this logger owns its handler explicitly.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sys
from collections.abc import Mapping
from typing import Final

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


def _clean(value: object, *, field: str) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_LIST_LENGTH:
            value = value[:MAX_LIST_LENGTH]
        cleaned: list[object] = []
        for item in value:
            if isinstance(item, str):
                cleaned.append(item[:MAX_STRING_LENGTH])
            elif isinstance(item, (bool, int)):
                cleaned.append(item)
            else:
                raise RoutingLogError(f"{field} holds a non-scalar item")
        return cleaned
    if isinstance(value, Mapping):
        counts: dict[str, int] = {}
        for key, count in value.items():
            if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int):
                raise RoutingLogError(f"{field} must map strings to counts")
            counts[key[:MAX_STRING_LENGTH]] = count
        return counts
    raise RoutingLogError(f"{field} has unsupported type {type(value).__name__}")


def build_record(event: str, fields: Mapping[str, object]) -> dict[str, object]:
    """Validate ``fields`` against ``event``'s allowlist and return the record."""
    allowed = EVENT_FIELDS.get(event)
    if allowed is None:
        raise RoutingLogError(f"unknown routing event {event!r}")
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise RoutingLogError(f"{event} does not allow {', '.join(unknown)}")
    record: dict[str, object] = {"event": event}
    for name in sorted(fields):
        record[name] = _clean(fields[name], field=name)
    return record


def _configure() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(getattr(handler, "_daemon_routing", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._daemon_routing = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = _configure()


def emit(event: str, **fields: object) -> None:
    """Write one routing record. Never raises; an invalid record is dropped."""
    try:
        record = build_record(event, fields)
        logger.info("%s %s", PREFIX, json.dumps(record, separators=(",", ":"), sort_keys=True))
    except Exception:
        # Telemetry must never affect dispatch or settlement.
        with contextlib.suppress(Exception):
            logging.getLogger(__name__).warning("Dropped an invalid routing record (%s)", event)


def vocabulary_version(*vocabularies: frozenset[str] | set[str]) -> str:
    """A short, stable digest of classifier vocabularies, so records name the rules."""
    digest = hashlib.sha256()
    for vocabulary in vocabularies:
        digest.update("\x1f".join(sorted(vocabulary)).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:12]
