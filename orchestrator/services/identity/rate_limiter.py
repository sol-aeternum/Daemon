"""Reusable Redis-backed rate limiter for hosted identity endpoints.

Architecture decisions followed:
  - TODO 0 decision lock: hosted identity uses shared `AppState.redis`; no
    new Redis pool; hosted production fails closed for
    nonce/challenge/rate-limit enforcement.
  - TODO 2 audit: `AppState.redis` is the only Redis client the app process
    owns for identity. `FetchCache` has its own pool and must not be
    borrowed.
  - TODO 3 research: atomic `INCR` + `EXPIRE` via Lua/`EVALSHA` is the
    authoritative pattern. All keys namespaced and HMAC-truncated. 429
    responses include `Retry-After`. Body is generic — never leaks the
    count, the IP, or any identifier.

The helper exposes a single `RateLimiter` class that route code uses
without needing to touch Lua, key construction, or TTL handling. Each
"policy" is a (limit, window-seconds) pair keyed by an
endpoint-scoped key built from HMAC-truncated components. The first
increment atomically sets the TTL via Lua; subsequent increments only
update the counter. A single Lua call returns both the new counter and
the remaining TTL so route code can build a deterministic
`Retry-After`.

This module never:
  - creates a new Redis pool/connection (it consumes the caller-supplied
    `ArqRedis` instance);
  - writes to Postgres or any other store;
  - logs raw IPs, emails, user agents, codes, or nonces;
  - silently falls open when Redis is unavailable in hosted production.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from arq.connections import ArqRedis

logger = logging.getLogger(__name__)


# Lua: single round-trip INCR with PEXPIRE on the first hit. Returns
# {count, ttl_ms}. Atomic — closes the INCR-then-EXPIRE crash gap.
_LUA_INCR_WITH_TTL = """
local n = redis.call("INCR", KEYS[1])
if n == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("PTTL", KEYS[1])
return {n, ttl}
"""


# PEXPIRE returns -1 (no expiry) or -2 (no key). The latter cannot
# happen post-INCR; the former is an operator-investigation trigger.
# We return the full window so the caller can still advertise a sane
# Retry-After.
_TTL_FALLBACK_MS = 0


@dataclass(frozen=True)
class RateLimitPolicy:
    """One limit rule: a maximum count of hits per window seconds."""

    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("RateLimitPolicy.limit must be >= 1")
        if self.window_seconds < 1:
            raise ValueError("RateLimitPolicy.window_seconds must be >= 1")


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a `check` call.

    `allowed` is True when the request may proceed. When False,
    `retry_after_seconds` carries the recommended `Retry-After` value
    (rounded up to the nearest second, with a 1-second floor).
    `count` and `limit` are exposed for diagnostic logs only — they
    MUST NOT be echoed back to the caller in the response body.
    """

    allowed: bool
    count: int
    limit: int
    window_seconds: int
    retry_after_seconds: int

    @property
    def remaining(self) -> int:
        """Number of requests remaining in the current window (>= 0)."""
        return max(0, self.limit - self.count)


class RateLimitUnavailableError(RuntimeError):
    """Raised when a rate-limit check cannot be evaluated because the
    Redis backend is required but unavailable. The route layer is
    expected to translate this into an HTTP 503 with a generic body
    and a `Retry-After: 5` header — see TODO 0 decision lock.
    """


def hash_key_material(secret: str, raw_value: str) -> str:
    """HMAC-SHA256 truncate a key component so raw PII never lands in
    a Redis key.

    The output is 16 lowercase hex chars (64 bits) — enough collision
    resistance for a rate-limit namespace and short enough to keep
    keys greppable in `redis-cli KEYS rl:*` output.
    """
    if not secret:
        raise ValueError("hash_key_material requires a non-empty secret")
    if raw_value is None or raw_value == "":
        raise ValueError("hash_key_material requires a non-empty value")
    digest = hmac.new(
        secret.encode("utf-8"),
        raw_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:16]


class RateLimiter:
    """Atomic Redis-backed rate limiter.

    The class is stateless beyond its `redis` reference and the cached
    Lua script handle. A single instance is safe to share across
    requests because the underlying `ArqRedis` (a) is async and (b) is
    safe to use concurrently. Routes should call `check()` per
    request; the helper performs one round trip (the EVALSHA).

    Key shape:
        rl:<endpoint>:<scope_kind>:<scope_hash>

    where `<endpoint>` is the caller-supplied endpoint tag (e.g.
    `auth:enroll:complete`), `<scope_kind>` is `ip` or `email`, and
    `<scope_hash>` is the HMAC-truncated component produced by
    `hash_key_material`. The key never carries raw IP, raw email,
    raw User-Agent, codes, or tokens.
    """

    def __init__(
        self,
        redis: ArqRedis | None,
        *,
        hmac_secret: str,
        namespace: str = "rl",
    ) -> None:
        if not hmac_secret:
            raise ValueError("RateLimiter requires a non-empty hmac_secret")
        self._redis: ArqRedis | None = redis
        self._hmac_secret: str = hmac_secret
        self._namespace: str = namespace
        # `register_script` returns a Script object that uses
        # EVALSHA+fallback-to-EVAL semantics and is safe to call
        # concurrently.
        self._script = None
        if redis is not None:
            self._script = redis.register_script(_LUA_INCR_WITH_TTL)

    @property
    def redis(self) -> ArqRedis | None:
        """The Redis client the helper was constructed with. None means
        the host has no Redis wired (e.g. self-hosted setup-first
        development)."""
        return self._redis

    @property
    def is_redis_available(self) -> bool:
        """True when the helper was constructed with a Redis client.

        Routes use this to decide whether to enforce the limit. The
        fail-closed policy for hosted production lives in the route
        layer (it knows `daemon_hosted_identity_enabled` /
        `daemon_hosted_identity_require_redis`); this helper exposes
        the underlying capability.
        """
        return self._redis is not None

    def build_key(
        self,
        endpoint: str,
        scope_kind: str,
        raw_value: str,
    ) -> str:
        """Construct the namespaced Redis key for a given scope value.

        The HMAC truncation happens here so the route layer never sees
        raw material in the key. The result is deterministic for a
        given (endpoint, scope_kind, raw_value, secret) tuple.
        """
        if not endpoint:
            raise ValueError("endpoint must be a non-empty string")
        if scope_kind not in ("ip", "email", "user_id", "session_id"):
            raise ValueError("scope_kind must be 'ip', 'email', 'user_id', or 'session_id'")
        scope_hash = hash_key_material(self._hmac_secret, raw_value)
        return f"{self._namespace}:{endpoint}:{scope_kind}:{scope_hash}"

    async def check(
        self,
        endpoint: str,
        scope_kind: str,
        raw_value: str,
        policy: RateLimitPolicy,
    ) -> RateLimitDecision:
        """Atomically increment the counter and return the decision.

        Returns `allowed=True` when the new count is within the
        configured limit. Returns `allowed=False` (with a populated
        `retry_after_seconds`) when the limit has been exceeded in
        the current window. Raises `RateLimitUnavailableError` when
        the Redis client is missing or a command fails — the route
        layer decides whether fail-closed applies based on
        `daemon_hosted_identity_enabled` / `daemon_hosted_identity_require_redis`.
        """
        if not self.is_redis_available or self._script is None:
            raise RateLimitUnavailableError("Rate limiter is not wired to a Redis client")

        key = self.build_key(endpoint, scope_kind, raw_value)
        window_ms = int(policy.window_seconds * 1000)

        try:
            result = await self._script(
                keys=[key],
                args=[window_ms],
                client=self._redis,
            )
        except Exception as exc:
            logger.warning(
                "Rate limiter Redis call failed for endpoint=%s scope=%s: %s",
                endpoint,
                scope_kind,
                type(exc).__name__,
            )
            raise RateLimitUnavailableError(
                f"Rate limiter Redis call failed: {type(exc).__name__}"
            ) from exc

        # `result` is a 2-element Lua table (list) {count, pttl_ms}.
        try:
            count_raw, ttl_ms_raw = result  # type: ignore[misc]
            count = int(count_raw)
            ttl_ms = int(ttl_ms_raw)
        except (TypeError, ValueError) as exc:
            raise RateLimitUnavailableError(
                f"Rate limiter received malformed result: {result!r}"
            ) from exc

        if ttl_ms < 0:
            # PEXPIRE -1 (no TTL) or -2 (no key, impossible post-INCR).
            # Defensive: full window so the caller can advertise a
            # sane Retry-After.
            ttl_ms = window_ms if ttl_ms == -1 else _TTL_FALLBACK_MS

        # Round up to nearest second; floor of 1 second on limit hit
        # so the client doesn't poll-storm.
        retry_after_seconds = max(1, -(-ttl_ms // 1000))
        allowed = count <= policy.limit

        return RateLimitDecision(
            allowed=allowed,
            count=count,
            limit=policy.limit,
            window_seconds=policy.window_seconds,
            retry_after_seconds=retry_after_seconds,
        )
