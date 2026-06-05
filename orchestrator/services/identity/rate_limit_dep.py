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

import ipaddress
import logging
import re
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


ScopeKind = Literal["ip", "email"]


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
    honor `X-Forwarded-For` / `Forwarded` / `X-Real-IP` only if the
    immediate socket hop is loopback/private (the expected Next.js proxy
    path). Direct/self-hosted callers keep the immediate-socket behavior,
    so arbitrary forwarded headers are not trusted by default.

    Returns "unknown" when the address is unavailable so the key
    namespace still has a stable, non-empty value to hash.
    """
    immediate = request.client.host if request.client is not None and request.client.host else None
    if immediate is None:
        return "unknown"

    settings = get_settings()
    if settings.daemon_trust_proxy_forwarded_client_ip and _is_trusted_proxy_hop(immediate):
        forwarded_ip = _forwarded_client_ip(request)
        if forwarded_ip is not None:
            return forwarded_ip

    return immediate


def _is_trusted_proxy_hop(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "backend"}
    return ip.is_loopback or ip.is_private


def _forwarded_client_ip(request: Request) -> str | None:
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        for candidate in x_forwarded_for.split(","):
            valid = _normalize_forwarded_ip(candidate)
            if valid is not None:
                return valid

    forwarded = request.headers.get("forwarded")
    if forwarded:
        for token in re.split(r"\s*,\s*", forwarded):
            match = re.search(r'for=(?P<value>"?\[[^\]]+\]"?|"?[^;,\"]+"?)', token)
            if not match:
                continue
            valid = _normalize_forwarded_ip(match.group("value"))
            if valid is not None:
                return valid

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return _normalize_forwarded_ip(x_real_ip)

    return None


def _normalize_forwarded_ip(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    if not value or value.lower() == "unknown":
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


def _raise_429(decision: RateLimitDecision) -> None:
    raise HTTPException(
        status_code=429,
        detail="rate_limited",
        headers={"Retry-After": str(decision.retry_after_seconds)},
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
    policies: list[tuple[ScopeKind, str, RateLimitPolicy]],
) -> None:
    """Run one or more rate-limit checks and translate the result.

    Each policy is a (scope_kind, raw_value, policy) tuple. A request
    passes only if every check returns `allowed=True`. If any check
    returns `allowed=False`, the function raises a 429 with the
    `Retry-After` from the most-restrictive failure (the maximum
    `retry_after_seconds` across all failed checks).

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

    worst_retry_after = 0
    blocked = False
    for scope_kind, raw_value, policy in policies:
        try:
            decision = await limiter.check(
                endpoint=endpoint,
                scope_kind=scope_kind,
                raw_value=raw_value,
                policy=policy,
            )
        except RateLimitUnavailableError as exc:
            if fail_closed:
                logger.warning(
                    "Rate limiter unavailable for endpoint=%s scope=%s: %s; failing closed",
                    endpoint,
                    scope_kind,
                    type(exc).__name__,
                )
                _raise_503()
            return

        if not decision.allowed:
            blocked = True
            if decision.retry_after_seconds > worst_retry_after:
                worst_retry_after = decision.retry_after_seconds

    if blocked:
        # Synthetic decision: advertise the worst-case Retry-After
        # without exposing the per-scope count/limit in the response.
        _raise_429(
            RateLimitDecision(
                allowed=False,
                count=0,
                limit=0,
                window_seconds=0,
                retry_after_seconds=worst_retry_after,
            )
        )


def client_ip_for_key(request: Request) -> str:
    """Public helper used by route code to build a rate-limit policy
    tuple for the per-IP scope. Returns the immediate client IP or
    the sentinel `"unknown"` string when the address is unavailable.
    """
    return _client_ip(request)
