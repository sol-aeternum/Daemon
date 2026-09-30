"""Shared, cross-process admission pacing for one paid search provider.

Search calls are billed on success, and the provider enforces its published
window limits (Brave documents a 1-second sliding window) across every caller
of the same credential. A per-process pacer cannot see other backend/worker
processes, so admission for one provider credential is coordinated through the
deployment's existing Redis using atomic Lua scripts:

* **Shared and atomic.** Two processes racing to admit run one Lua script each;
  the script re-checks every constraint and records the slot, so parallel
  pacers cannot overshoot the allowance. Keys are scoped to
  ``provider`` plus a SHA-256 digest of the credential, so credentials and
  providers never share a gate and no key, log or diagnostic ever carries the
  credential, the query or a URL.
* **Two admission regimes.** Until valid provider window metadata has been
  observed for the credential (``X-RateLimit-Policy`` on a real
  response), admission uses a deliberately conservative fallback spacing. Once
  learned, each window is enforced as a plain count over its allowance, so up
  to a window's full allowance may be admitted as one concurrent burst —
  nothing is artificially spaced, and no window length serializes calls. The
  fallback spacing is a **temporary bootstrap**, not a production throughput
  target: operator-configured capacity is a separate, unbuilt design decision.
* **Bounded and cancel-safe.** There is no queue and no reserved future slot:
  waiters sleep locally and re-run the atomic gate, so cancellation cannot leak
  a reservation and the recorded schedule cannot grow without bound (entries
  are pruned and TTL'd). A wait is bounded by the caller's remaining operation
  budget; a wait longer than the budget refuses immediately.
* **Fail closed.** When Redis is not configured or is unreachable, admission
  fails closed before provider dispatch rather than bursting through un-paced.
  Search's dispatch-aware metering settles its temporary account hold at zero.
* **Cooldown, never a long sleep.** Rate-limit responses (and their bounded,
  strictly parsed headers) can only *extend* a shared cooldown. Valid long reset
  evidence is retained until reset, while a caller whose wait budget is shorter
  receives an immediate refusal.

This module never issues HTTP requests and never retries one; it only gates
admission and records what responses reported.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from arq.connections import RedisSettings, create_pool as arq_create_pool
from orchestrator.config import get_settings

logger = logging.getLogger(__name__)

#: Prefix for every pacing key. The namespace is opaque; credential material
#: never appears in it (see :func:`_scope_prefix`).
DEFAULT_NAMESPACE: Final[str] = "daemon:search-pacing:v1"

#: Conservative fallback spacing between two admitted dispatches while no valid
#: provider window metadata has been observed for the credential. This is a
#: deliberate bootstrap under uncertainty (the active key's real allowance is
#: unknown), documented as temporary in ``docs/SEARCH_SERVICE_APPROVALS.md``.
#: It is NOT the production throughput model: once windows are learned, bursts
#: within a window's allowance are admitted concurrently.
FALLBACK_MIN_INTERVAL_S: Final[float] = 1.1

#: Sliding-window tracking bounds. Only learned windows with at most this many
#: entries (their limit) and at most this window length are tracked on the
#: per-credential ZSET, which caps that ZSET's memory regardless of TTL. Larger
#: or longer windows (monthly quotas) use an O(1) conservative two-bucket hash
#: instead of retaining per-admission history.
ZSET_MAX_ENTRIES: Final[int] = 20_000
ZSET_MAX_WINDOW_S: Final[float] = 3_000_000.0

#: No-evidence billing/quota cooldown. Valid reset evidence is retained up to
#: MAX_RESET_S instead; storage lifetime and a caller's wait budget are separate.
COOLDOWN_CAP_S: Final[float] = 60.0

#: Minimum learned window metadata TTL. Retain long windows for at least twice
#: their length, so metadata cannot expire while counted usage remains relevant.
CAPACITY_TTL_S: Final[float] = 6 * 3600.0

#: Strict parse bounds for provider headers. Anything outside them (or not a
#: plain non-negative integer) is discarded entirely — headers are untrusted.
MAX_WINDOWS: Final[int] = 8
MIN_WINDOW_S: Final[float] = 1.0
MAX_WINDOW_S: Final[float] = 10_000_000.0
MAX_LIMIT: Final[int] = 1_000_000
MAX_RESET_S: Final[float] = 10_000_000.0

#: Small no-evidence cooldown after a 429: the provider documents a 1-second
#: sliding window, so this is the honest minimum before probing again.
RATE_COOLDOWN_NO_EVIDENCE_S: Final[float] = 1.0

#: After a Redis failure, refuse (fail closed) this long before retrying the
#: client, so an outage does not turn every search into a fresh connection
#: storm.
UNAVAILABLE_LEASH_S: Final[float] = 2.0

#: Hard cap on admission attempts inside one ``admit`` call (each attempt is a
#: bounded Redis round trip). The wall-clock budget binds first in practice.
MAX_ADMISSION_ATTEMPTS: Final[int] = 25

#: Digest length (hex characters) for the credential part of the scope prefix:
#: 192 bits, opaque and never reversible from logs or keys.
_DIGEST_HEX_CHARS: Final[int] = 48

_ADMIT_SCRIPT: Final[str] = """\
-- One atomic grant, with bounded state even at the largest accepted allowance.
local tm = redis.call('TIME')
local now_ms = tm[1] * 1000 + math.floor(tm[2] / 1000)
local spacing_ms = tonumber(ARGV[1])
local sliding_max_ms = tonumber(ARGV[3])
local sliding_max_entries = tonumber(ARGV[4])
local function integer(n) return n and n == math.floor(n) end
local earliest = now_ms
local cd_raw = redis.call('GET', KEYS[2])
local cd = tonumber(cd_raw or '0')
if not integer(cd) or cd < 0 or cd > now_ms + 10000000000 then return {-1, 0} end
if cd > earliest then earliest = cd end
-- Check cardinality BEFORE fetching: corrupt metadata cannot allocate an
-- unbounded reply or authorize an unsupported window.
if redis.call('HLEN', KEYS[3]) > 8 or redis.call('HLEN', KEYS[4]) > 8 then
    return {-1, 0}
end
local cap = redis.call('HGETALL', KEYS[3])
local horizon = 0
local counter_horizon = 0
local counters = {}
local counter_fields = {}
local i = 1
while i < #cap do
    local w = tonumber(cap[i])
    local limit = tonumber(cap[i + 1])
    if not integer(w) or w < 1000 or w > 10000000000 or
       not integer(limit) or limit < 1 or limit > 1000000 then return {-1, 0} end
    i = i + 2
    if w <= sliding_max_ms and limit <= sliding_max_entries then
        if w > horizon then horizon = w end
    else
        if w > counter_horizon then counter_horizon = w end
        counter_fields[cap[i - 2]] = true
    end
end
for _, field in ipairs(redis.call('HKEYS', KEYS[4])) do
    if not counter_fields[field] then redis.call('HDEL', KEYS[4], field) end
end
if #cap == 0 then horizon = spacing_ms end
if #cap == 0 then
    local last = redis.call('ZRANGE', KEYS[1], -1, -1, 'WITHSCORES')
    if last[2] ~= nil then
        local ready = tonumber(last[2]) + spacing_ms
        if ready > earliest then earliest = ready end
    end
else
    i = 1
    while i < #cap do
        local field = cap[i]
        local w = tonumber(cap[i])
        local limit = tonumber(cap[i + 1])
        i = i + 2
        if w <= sliding_max_ms and limit <= sliding_max_entries then
            local count = redis.call('ZCOUNT', KEYS[1], '(' .. tostring(now_ms - w), '+inf')
            if count >= limit then
                local oldest = redis.call(
                    'ZRANGE', KEYS[1], '(' .. tostring(now_ms - w), '+inf',
                    'BYSCORE', 'LIMIT', 0, 1, 'WITHSCORES'
                )
                if oldest[2] ~= nil then
                    local ready = tonumber(oldest[2]) + w
                    if ready > earliest then earliest = ready end
                end
            end
        else
            -- Count both whole buckets. This conservatively includes every
            -- admission in the rolling window, including across a boundary.
            local bucket = math.floor(now_ms / w)
            local previous, current = 0, 0
            local raw = redis.call('HGET', KEYS[4], field)
            if raw then
                local b, p, c = string.match(raw, '^(%d+):(%d+):(%d+)$')
                b, p, c = tonumber(b), tonumber(p), tonumber(c)
                if not integer(b) or not integer(p) or not integer(c) or
                   b > bucket or p > 1000000 or c > 1000000 then return {-1, 0} end
                if b == bucket then previous, current = p, c
                elseif b == bucket - 1 then previous = c end
            else
                -- Preserve retained bootstrap/sliding grants when first
                -- learning a counter window. These are past admissions, not
                -- future reservations, so persist them even on refusal.
                previous = redis.call('ZCOUNT', KEYS[1], (bucket - 1) * w,
                    '(' .. tostring(bucket * w))
                current = redis.call('ZCOUNT', KEYS[1], bucket * w, '+inf')
                if previous + current > 0 then
                    redis.call('HSET', KEYS[4], field,
                        string.format('%.0f:%.0f:%.0f', bucket, previous, current))
                    redis.call('PEXPIRE', KEYS[4], counter_horizon * 2 + 60000)
                end
            end
            counters[field] = {bucket, previous, current}
            if previous + current >= limit then
                local ready = (bucket + 1) * w
                if ready > earliest then earliest = ready end
            end
        end
    end
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - horizon)
if earliest > now_ms then
    return {0, earliest - now_ms}
end
-- Never add history when only large counters are needed. The longest sliding
-- window's allowance bounds the shared ZSET; this also enforces a hard cap.
if horizon > 0 then
    if redis.call('ZCARD', KEYS[1]) >= sliding_max_entries then return {-1, 0} end
    redis.call('ZADD', KEYS[1], now_ms, ARGV[2])
    redis.call('PEXPIRE', KEYS[1], horizon + 60000)
end
-- Remove obsolete counter fields when learned policies change.
redis.call('DEL', KEYS[4])
if counter_horizon > 0 then
    for field, counts in pairs(counters) do
        redis.call('HSET', KEYS[4], field,
            string.format('%.0f:%.0f:%.0f', counts[1], counts[2], counts[3] + 1))
    end
    redis.call('PEXPIRE', KEYS[4], counter_horizon * 2 + 60000)
end
return {1, 0}
"""

_COOLDOWN_SCRIPT: Final[str] = """\
-- KEYS[1] cooldown until-ms; ARGV[1] span-ms from now. Monotonic: only ever
-- extends an existing cooldown, never shortens it.
local tm = redis.call('TIME')
local now_ms = tm[1] * 1000 + math.floor(tm[2] / 1000)
local span = tonumber(ARGV[1])
if span == nil or span <= 0 then return 0 end
local proposed = now_ms + span
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current == nil then current = 0 end
if proposed > now_ms and proposed > current then
    redis.call('SET', KEYS[1], tostring(proposed), 'PX', proposed - now_ms)
    return 1
end
return 0
"""

_CAPACITY_SCRIPT: Final[str] = """\
-- KEYS[1] capacity hash; ARGV[1] TTL ms; ARGV[2..] window_ms, limit pairs.
redis.call('DEL', KEYS[1])
local i = 2
while i < #ARGV do
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
    i = i + 2
end
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]))
return 1
"""


class PacingUnavailable(Exception):
    """Redis is unconfigured or unreachable: admission fails closed."""


class PacingRefused(Exception):
    """No slot within the caller's bounded wait budget: refuse before dispatch."""


class SearchPacerLike(Protocol):
    """What the search tool needs from a pacer; :class:`SearchPacer` conforms.

    Exposed so callers (and tests) can substitute an alternate implementation
    without importing the concrete Redis-backed class.
    """

    async def admit(self, provider: str, credential: str, *, wait_budget_s: float) -> None:
        """Grant one dispatch slot or raise :class:`PacingRefused`."""
        ...

    async def observe(
        self,
        provider: str,
        credential: str,
        *,
        status: int,
        headers: Mapping[str, str],
        wait_budget_s: float = 1.0,
    ) -> None:
        """Record what one provider response reported."""
        ...


class RedisLike(Protocol):
    """The slice of a Redis client the pacer needs (ArqRedis satisfies it)."""

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | bytes | int | float
    ) -> Any: ...


ClientFactory = Callable[[], Awaitable[RedisLike]]


@dataclass(frozen=True, slots=True)
class _RateEvidence:
    """Strictly parsed, bounded evidence from one response's headers.

    ``windows`` is the provider-declared allowance list; ``cooldown_s`` is a
    bounded reset duration. Both may be None: absence of evidence is normal.
    """

    windows: tuple[tuple[int, float], ...] | None = None
    cooldown_s: float | None = None
    invalid_windows: bool = False


def _scope_prefix(namespace: str, provider: str, credential: str) -> str:
    """An opaque, per-credential scope prefix: provider plus a credential digest.

    This is a deterministic namespace fingerprint of a provider-issued API key,
    not a password verifier. An observer could test guessed credentials against
    it; it does not add entropy or protect low-entropy passwords. No raw
    credential, query or URL fragment enters the Redis key.
    """
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:_DIGEST_HEX_CHARS]
    return f"{namespace}:{provider}:{digest}"


def _positive_int(value: str) -> int | None:
    """A strict bounded non-negative integer token, or None."""
    token = value.strip()
    if not token.isascii() or not token.isdigit() or len(token) > 10:
        return None
    parsed = int(token)
    return parsed


def _parse_policy_entry(entry: str) -> tuple[int, float] | None:
    """One ``X-RateLimit-Policy`` entry: ``limit;w=window`` — or None."""
    parts = entry.split(";")
    if len(parts) != 2:
        return None
    limit = _positive_int(parts[0])
    if limit is None or limit < 1 or limit > MAX_LIMIT:
        return None
    window_part = parts[1].strip().lower()
    if not window_part.startswith("w="):
        return None
    window = _positive_int(window_part[2:])
    if window is None:
        return None
    window_s = float(window)
    if window_s < MIN_WINDOW_S or window_s > MAX_WINDOW_S:
        return None
    return limit, window_s


def _split_list(value: str) -> list[str] | None:
    if len(value) > 512:
        return None
    entries = [part.strip() for part in value.split(",")]
    if not entries or len(entries) > MAX_WINDOWS or any(not part for part in entries):
        return None
    return entries


def parse_rate_headers(headers: Mapping[str, str]) -> _RateEvidence:
    """Strictly parse ``X-RateLimit-*`` / ``Retry-After`` response headers.

    Headers are untrusted: every numeric token must be a small plain integer,
    the window lists must be internally consistent, and any malformed or
    out-of-bounds value discards the whole header. Nothing here can produce a
    unbounded or fractional wait; valid reset evidence is retained up to MAX_RESET_S.
    """
    lower: dict[str, str] = {}
    for key, value in headers.items():
        lower[str(key).lower()] = value

    policy_raw = lower.get("x-ratelimit-policy")
    windows: tuple[tuple[int, float], ...] | None = None
    if policy_raw is not None:
        entries = _split_list(policy_raw)
        parsed: list[tuple[int, float]] = []
        if entries is not None and len(entries) <= MAX_WINDOWS:
            for entry in entries:
                item = _parse_policy_entry(entry)
                if item is None:
                    parsed = []
                    break
                parsed.append(item)
        if parsed:
            windows = tuple(parsed)

    cooldown_s: float | None = None

    retry_after = lower.get("retry-after")
    if retry_after is not None:
        seconds = _positive_int(retry_after)
        if seconds is not None:
            if seconds <= MAX_RESET_S:
                cooldown_s = float(seconds)

    remaining_raw = lower.get("x-ratelimit-remaining")
    reset_raw = lower.get("x-ratelimit-reset")
    if remaining_raw is not None and reset_raw is not None and windows is not None:
        remaining = _split_list(remaining_raw)
        resets = _split_list(reset_raw)
        if (
            remaining is not None
            and resets is not None
            and (len(remaining) == len(resets) == len(windows))
        ):
            for index, (limit, _window_s) in enumerate(windows):
                left = _positive_int(remaining[index])
                reset = _positive_int(resets[index])
                if left is None or reset is None or left > limit or reset > MAX_RESET_S:
                    cooldown_s = None
                    break
                # Only an exhausted window (nothing left) means "wait for the
                # reset"; a partly-used quota is normal and never a cooldown.
                if left == 0:
                    candidate = float(reset)
                    if cooldown_s is None or candidate > cooldown_s:
                        cooldown_s = candidate
    if windows is not None and len({window for _limit, window in windows}) != len(windows):
        windows = None
    return _RateEvidence(
        windows=windows,
        cooldown_s=cooldown_s,
        invalid_windows=policy_raw is not None and windows is None,
    )


def _status_cooldown_s(status: int) -> float | None:
    """A bounded no-evidence cooldown for one refused status family.

    Only statuses with a real basis get a cooldown: 429 is documented as the
    provider's rate-limit response over a 1-second window; 402 is billing;
    432/433 are the provider's rate-or-quota family whose semantics its public
    documentation does not define, so they get the same bounded conservative
    treatment without invented distinctions. Everything else is untouched.
    """
    if status == 429:
        return RATE_COOLDOWN_NO_EVIDENCE_S
    if status in (402, 432, 433):
        return COOLDOWN_CAP_S
    return None


class SearchPacer:
    """One provider credential's shared admission gate, backed by Redis."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None,
        namespace: str = DEFAULT_NAMESPACE,
        fallback_min_interval_s: float = FALLBACK_MIN_INTERVAL_S,
        capacity_ttl_s: float = CAPACITY_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client_factory = client_factory
        self._namespace = namespace
        self._fallback_ms = int(fallback_min_interval_s * 1000)
        self._capacity_ttl_ms = int(capacity_ttl_s * 1000)
        self._clock = clock
        self._sleep = sleep
        self._client: RedisLike | None = None
        self._client_lock = asyncio.Lock()
        self._unavailable_until = 0.0

    @classmethod
    def from_settings(cls) -> SearchPacer:
        """The production pacer: the existing ``settings.redis_url`` seam."""
        return cls(client_factory=create_settings_client)

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    # ------------------------------------------------------------ admission
    async def admit(self, provider: str, credential: str, *, wait_budget_s: float) -> None:
        """Grant one dispatch slot for this credential, or refuse.

        Waits only within ``wait_budget_s`` (the caller's remaining operation
        budget). A wait that does not fit the budget refuses immediately, so a
        long-window exhaustion is a fast sanitized refusal, not a sleep.
        Cancellation propagates. Sleeping waiters have no future reservation;
        cancellation of an in-flight Redis command may consume a conservative
        slot if the server processed it, but never authorizes a caller to send.
        """
        budget_end = self._clock() + wait_budget_s
        if wait_budget_s <= 0:
            raise PacingRefused
        prefix = _scope_prefix(self._namespace, provider, credential)
        member = uuid.uuid4().hex
        try:
            async with asyncio.timeout(wait_budget_s):
                for _attempt in range(MAX_ADMISSION_ATTEMPTS):
                    remaining = budget_end - self._clock()
                    if remaining <= 0:
                        raise PacingRefused
                    wait_ms = await self._attempt(prefix, member, remaining=remaining)
                    if self._clock() >= budget_end:
                        raise PacingRefused
                    if wait_ms == 0:
                        return
                    wait_s = wait_ms / 1000.0
                    remaining = budget_end - self._clock()
                    if wait_s >= remaining or remaining <= 0:
                        raise PacingRefused
                    await self._sleep(wait_s)
        except TimeoutError:
            raise PacingRefused from None
        raise PacingRefused

    async def _attempt(self, prefix: str, member: str, *, remaining: float) -> int:
        """One atomic gate evaluation: 0 to dispatch, else the wait in ms."""
        try:
            async with asyncio.timeout(remaining):
                client = await self._redis()
                result = await client.eval(
                    _ADMIT_SCRIPT,
                    4,
                    f"{prefix}:admit",
                    f"{prefix}:cooldown",
                    f"{prefix}:cap",
                    f"{prefix}:count",
                    self._fallback_ms,
                    member,
                    int(ZSET_MAX_WINDOW_S * 1000),
                    ZSET_MAX_ENTRIES,
                )
            granted, wait_ms = self._script_pair(result)
        except Exception as exc:
            self._note_unavailable(exc)
            raise PacingUnavailable from None
        return 0 if granted else wait_ms

    # ------------------------------------------------------------- observe
    async def observe(
        self,
        provider: str,
        credential: str,
        *,
        status: int,
        headers: Mapping[str, str],
        wait_budget_s: float = 1.0,
    ) -> None:
        """Record what one provider response reported. Never raises.

        Learning is opportunistic: a Redis failure here must not disturb a
        response that was already delivered or already refused by the provider.
        """
        if status != 200 and _status_cooldown_s(status) is None:
            return
        evidence = parse_rate_headers(headers)
        cooldown_s = (
            evidence.cooldown_s if evidence.cooldown_s is not None else _status_cooldown_s(status)
        )
        prefix = _scope_prefix(self._namespace, provider, credential)
        try:
            async with asyncio.timeout(max(0.0, wait_budget_s)):
                if cooldown_s is not None:
                    await self._set_cooldown(prefix, cooldown_s)
                # Unsupported metadata poisons the gate, rather than silently
                # reverting a known allowance to bootstrap spacing.
                if evidence.invalid_windows:
                    await self._learn(prefix, ((0, 0.0),))
                elif evidence.windows is not None:
                    await self._learn(prefix, evidence.windows)
        except Exception as exc:
            self._note_unavailable(exc)

    async def _learn(self, prefix: str, windows: tuple[tuple[int, float], ...]) -> None:
        client = await self._redis()
        ttl_ms = max(self._capacity_ttl_ms, int(max(w for _limit, w in windows) * 2000))
        args: list[str] = [str(ttl_ms)]
        for limit, window_s in windows:
            args.extend([str(int(window_s * 1000)), str(limit)])
        result = await client.eval(_CAPACITY_SCRIPT, 1, f"{prefix}:cap", *args)
        if type(result) is not int or result != 1:
            raise PacingUnavailable

    async def _set_cooldown(self, prefix: str, cooldown_s: float) -> None:
        capped = min(max(cooldown_s, 0.0), MAX_RESET_S)
        if capped <= 0:
            return
        client = await self._redis()
        result = await client.eval(
            _COOLDOWN_SCRIPT, 1, f"{prefix}:cooldown", str(int(capped * 1000))
        )
        if type(result) is not int or result not in (0, 1):
            raise PacingUnavailable

    # --------------------------------------------------------------- redis
    async def _redis(self) -> RedisLike:
        if self._client is not None:
            return self._client
        if self._client_factory is None or self._clock() < self._unavailable_until:
            raise PacingUnavailable
        async with self._client_lock:
            if self._client is None:
                if self._clock() < self._unavailable_until:
                    raise PacingUnavailable
                factory = self._client_factory
                if factory is None:
                    raise PacingUnavailable
                try:
                    self._client = await factory()
                except Exception as exc:
                    self._note_unavailable(exc)
                    raise PacingUnavailable from None
        client = self._client
        if client is None:
            raise PacingUnavailable
        return client

    def _note_unavailable(self, exc: Exception) -> None:
        """A short leash on failures, logged without exception text or keys."""
        self._client = None
        self._unavailable_until = self._clock() + UNAVAILABLE_LEASH_S
        logger.warning("Search pacing backend unavailable: error_type=%s", type(exc).__name__)

    @staticmethod
    def _script_pair(result: Any) -> tuple[int, int]:
        if isinstance(result, (list, tuple)) and len(result) == 2:
            first = result[0]
            second = result[1]
            if (
                type(first) is int
                and type(second) is int
                and (
                    (first == 1 and second == 0)
                    or (first == 0 and 0 < second <= int(MAX_RESET_S * 1000))
                )
            ):
                return first, second
        raise PacingUnavailable


async def create_settings_client() -> RedisLike:
    """A Redis client from the deployment's existing ``redis_url`` setting.

    Same factory and settings seam the app lifecycle uses; no new daemon or
    background service. A missing URL fails closed (the pacer refuses).
    """
    settings = get_settings()
    url = settings.redis_url
    if not url:
        raise PacingUnavailable
    # ArqRedis satisfies the runtime surface (eval) but its stubbed overloads
    # do not structurally match the narrow protocol; the cast is the seam.
    return cast(RedisLike, await arq_create_pool(RedisSettings.from_dsn(url)))


_shared_pacer: SearchPacer | None = None
_shared_lock: asyncio.Lock | None = None


async def shared_pacer() -> SearchPacer:
    """The process-wide shared pacer, built lazily from settings."""
    global _shared_pacer, _shared_lock
    if _shared_pacer is None:
        if _shared_lock is None:
            _shared_lock = asyncio.Lock()
        async with _shared_lock:
            if _shared_pacer is None:
                _shared_pacer = SearchPacer.from_settings()
    return _shared_pacer


async def reset_shared_pacer() -> None:
    """Test hook: forget the process-wide pacer."""
    global _shared_pacer
    _shared_pacer = None
