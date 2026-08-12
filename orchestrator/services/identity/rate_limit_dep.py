"""FastAPI integration for the hosted identity rate limiter.

This module is the only place the route layer (auth_setup.py) talks
to the rate limiter. It exposes:

  - `get_rate_limiter(request)`: a FastAPI dependency that constructs
    a `RateLimiter` from the shared `AppState.redis` + the validated
    `daemon_auth_pepper` (via `validate_and_get_pepper`, so dev mode
    with no pepper still works through the ephemeral-pepper path).
  - `enforce_rate_limit(...)`: a helper that runs one or two
    `check()` calls and returns a generic 429/503 response when the
    limit is hit or Redis is unavailable. It also returns the
    `Retry-After` value for 429s.

Fail-closed policy (TODO 0 decision lock): when hosted identity is
enabled and `daemon_hosted_identity_require_redis` is true, a missing
or unavailable Redis raises a 503 with `Retry-After: 5` rather than
silently letting the request through. In self-hosted / development
mode the helper returns a `not_enforced` marker so the route can
proceed (preserving the existing self-hosted setup-first default).

The helper never logs raw IPs, emails, user agents, codes, or
nonces — only the endpoint tag and the scope kind.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import threading
import time
from collections.abc import Sequence
from typing import Literal

from fastapi import HTTPException, Request

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.config import get_settings
from orchestrator.services.identity.rate_limiter import (
    RateLimitDecision,
    RateLimiter,
    RateLimitPolicy,
    RateLimitUnavailableError,
)

logger = logging.getLogger(__name__)

_PROXY_SIGNATURE_MAX_AGE_SECONDS = 60
_CHAT_REJECTION_WARNING_THRESHOLD = 0.10
_CHAT_REJECTION_WARNING_MIN_REQUESTS = 10
_chat_metrics_lock = threading.Lock()
_chat_metrics: dict[str, dict[str, int]] = {}


ScopeKind = Literal["ip", "email", "user_id", "session_id"]
PolicySpec = tuple[ScopeKind, str, RateLimitPolicy] | tuple[ScopeKind, str, RateLimitPolicy, str]


def get_rate_limiter(request: Request) -> RateLimiter:
    """FastAPI dependency that returns a `RateLimiter` bound to the
    shared `AppState.redis`. If the host has no Redis wired, the
    returned limiter has `is_redis_available == False` and `check()`
    will raise `RateLimitUnavailableError` (which the route layer
    translates per the fail-closed policy).

    The HMAC secret for key derivation is the validated/normalized
    auth pepper. In development where `DAEMON_AUTH_PEPPER` is
    intentionally absent, `validate_and_get_pepper` returns the
    process-ephemeral pepper (with a warning) so this helper never
    raises on missing-config in self-hosted/dev flows.
    """
    app_state = request.app.state.app_state
    redis = getattr(app_state, "redis", None)
    settings = get_settings()
    pepper = validate_and_get_pepper(settings)
    return RateLimiter(redis, hmac_secret=pepper)


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction.

    Default-safe posture: use the immediate socket address only.
    When `daemon_trust_proxy_forwarded_client_ip=true`, the helper will
    honor only the internal `X-Daemon-Client-IP` header, and only when the
    immediate socket hop is loopback/private (the expected Next.js proxy
    path). Direct/self-hosted callers keep the immediate-socket behavior,
    so arbitrary caller-controlled forwarded headers are not trusted.

    Returns "unknown" when the address is unavailable so the key
    namespace still has a stable, non-empty value to hash.
    """
    immediate = request.client.host if request.client is not None and request.client.host else None
    if immediate is None:
        return "unknown"

    settings = get_settings()
    if settings.daemon_trust_proxy_forwarded_client_ip and _is_trusted_proxy_hop(immediate):
        forwarded_ip = _daemon_client_ip_header(
            request,
            secret=settings.daemon_internal_proxy_hmac_secret.strip(),
        )
        if forwarded_ip is not None:
            return forwarded_ip

    return immediate


def _is_trusted_proxy_hop(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "backend"}
    return ip.is_loopback or ip.is_private


def _daemon_client_ip_header(request: Request, *, secret: str) -> str | None:
    asserted_ip = request.headers.get("x-daemon-client-ip")
    forwarded_ip = _normalize_forwarded_ip(asserted_ip)
    timestamp = request.headers.get("x-daemon-client-ip-timestamp")
    signature = request.headers.get("x-daemon-client-ip-signature")
    if forwarded_ip is None or not secret or timestamp is None or signature is None:
        return None
    try:
        issued_at = int(timestamp)
    except ValueError:
        return None
    if abs(int(time.time()) - issued_at) > _PROXY_SIGNATURE_MAX_AGE_SECONDS:
        return None

    signed_ip = asserted_ip.strip() if asserted_ip is not None else ""
    payload = f"v1\n{timestamp}\n{request.method.upper()}\n{signed_ip}".encode()
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    supplied = signature.strip().lower()
    if len(supplied) != len(expected) or any(char not in "0123456789abcdef" for char in supplied):
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    return forwarded_ip


def _normalize_forwarded_ip(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    if not value or value.lower() == "unknown":
        return None
    if "," in value:
        return None
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if value.count(":") == 1 and "." in value:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            value = host
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _raise_429(
    decision: RateLimitDecision,
    scope_kind: ScopeKind,
    endpoint: str,
) -> None:
    logger.info(
        "rate_limit_rejected endpoint=%s scope=%s retry_after_seconds=%s",
        endpoint,
        scope_kind,
        decision.retry_after_seconds,
    )
    raise HTTPException(
        status_code=429,
        detail="rate_limited",
        headers={
            "Retry-After": str(decision.retry_after_seconds),
            "X-Daemon-Rate-Limit-Scope": scope_kind,
        },
    )


def _raise_503() -> None:
    raise HTTPException(
        status_code=503,
        detail="service_unavailable",
        headers={"Retry-After": "5"},
    )


async def enforce_rate_limit(
    *,
    request: Request,
    limiter: RateLimiter,
    endpoint: str,
    policies: Sequence[PolicySpec],
) -> None:
    """Run one or more rate-limit checks and translate the result.

    Each policy is either a `(scope_kind, raw_value, policy)` tuple or a
    `(scope_kind, raw_value, policy, endpoint_override)` tuple. A request
    passes only if every check returns `allowed=True`. If any check
    returns `allowed=False`, the function raises a 429 immediately and
    does not charge later scopes on the already-rejected request.

    Fail-closed behavior in hosted production:
      - If `daemon_hosted_identity_enabled` is true and
        `daemon_hosted_identity_require_redis` is true (the default),
        any `RateLimitUnavailableError` raises 503 with `Retry-After: 5`.
      - In self-hosted / development mode (hosted identity disabled
        or Redis gate off), the limit is simply not enforced when
        Redis is unavailable, so the existing self-hosted setup-first
        flow is preserved.
    """
    settings = get_settings()
    fail_closed = bool(
        settings.daemon_hosted_identity_enabled and settings.daemon_hosted_identity_require_redis
    )

    if not limiter.is_redis_available:
        if fail_closed:
            logger.warning(
                "Rate limiter unavailable for endpoint=%s; failing closed (hosted)",
                endpoint,
            )
            _raise_503()
        return

    for policy_spec in policies:
        if len(policy_spec) == 3:
            scope_kind, raw_value, policy = policy_spec
            policy_endpoint = endpoint
        else:
            scope_kind, raw_value, policy, policy_endpoint = policy_spec
        try:
            decision = await limiter.check(
                endpoint=policy_endpoint,
                scope_kind=scope_kind,
                raw_value=raw_value,
                policy=policy,
            )
        except RateLimitUnavailableError as exc:
            if fail_closed:
                logger.warning(
                    "Rate limiter unavailable for endpoint=%s scope=%s: %s; failing closed",
                    policy_endpoint,
                    scope_kind,
                    type(exc).__name__,
                )
                _raise_503()
            return

        if not decision.allowed:
            _raise_429(decision, scope_kind, policy_endpoint)


def record_chat_rate_limit_request(endpoint: str) -> None:
    """Record one chat request before any parsing, auth, or limit decision."""
    with _chat_metrics_lock:
        metrics = _chat_metrics.setdefault(
            endpoint,
            {"requests_total": 0, "rejections_total": 0, "last_warning_total": 0},
        )
        metrics["requests_total"] += 1


def record_chat_rate_limit_rejection(endpoint: str, scope: str) -> None:
    """Record one rejected chat request and emit a rate-bounded threshold warning."""
    with _chat_metrics_lock:
        metrics = _chat_metrics.setdefault(
            endpoint,
            {"requests_total": 0, "rejections_total": 0, "last_warning_total": 0},
        )
        metrics["rejections_total"] += 1
        total = metrics["requests_total"]
        rejected = metrics["rejections_total"]
        ratio = rejected / total if total else 0.0
        should_warn = (
            total >= _CHAT_REJECTION_WARNING_MIN_REQUESTS
            and ratio > _CHAT_REJECTION_WARNING_THRESHOLD
            and total - metrics["last_warning_total"] >= _CHAT_REJECTION_WARNING_MIN_REQUESTS
        )
        if should_warn:
            metrics["last_warning_total"] = total

    if should_warn:
        logger.warning(
            "chat_rate_limit_rejection_threshold_exceeded endpoint=%s scope=%s "
            "requests_total=%s rejections_total=%s rejection_ratio=%.4f threshold=%.2f",
            endpoint,
            scope,
            total,
            rejected,
            ratio,
            _CHAT_REJECTION_WARNING_THRESHOLD,
        )


def get_chat_rate_limit_metrics() -> dict[str, object]:
    """Return a process-local snapshot suitable for the authenticated status API."""
    with _chat_metrics_lock:
        by_endpoint = {
            endpoint: {
                "requests_total": values["requests_total"],
                "rejections_total": values["rejections_total"],
                "rejection_ratio": (
                    values["rejections_total"] / values["requests_total"]
                    if values["requests_total"]
                    else 0.0
                ),
            }
            for endpoint, values in sorted(_chat_metrics.items())
        }
    requests_total = sum(int(values["requests_total"]) for values in by_endpoint.values())
    rejections_total = sum(int(values["rejections_total"]) for values in by_endpoint.values())
    rejection_ratio = rejections_total / requests_total if requests_total else 0.0
    return {
        "requests_total": requests_total,
        "rejections_total": rejections_total,
        "rejection_ratio": rejection_ratio,
        "warning_threshold": _CHAT_REJECTION_WARNING_THRESHOLD,
        "warning_active": (
            requests_total >= _CHAT_REJECTION_WARNING_MIN_REQUESTS
            and rejection_ratio > _CHAT_REJECTION_WARNING_THRESHOLD
        ),
        "by_endpoint": by_endpoint,
    }


def reset_chat_rate_limit_metrics() -> None:
    """Clear process-local counters for deterministic tests."""
    with _chat_metrics_lock:
        _chat_metrics.clear()


def client_ip_for_key(request: Request) -> str:
    """Public helper used by route code to build a rate-limit policy
    tuple for the per-IP scope. Returns the immediate client IP or
    the sentinel `"unknown"` string when the address is unavailable.
    """
    return _client_ip(request)
