"""Hosted email-code challenge service.

This module is the single chokepoint through which hosted identity
completion routes (TODO 11 `/v1/auth/email/start` and
`/v1/auth/email/complete`) issue, verify, and consume email-code
challenges. It accepts a normalized email, an opaque `ip_hash` and
`user_agent_hash` (already HMAC-truncated by the route layer per
the TODO 3 research recommendation), and the live `daemon_auth_pepper`
(through `validate_and_get_pepper`), and produces a row in the
`email_challenges` table keyed by an HMAC verifier of a 6-digit
CSPRNG code.

Architecture decisions followed:

  - TODO 0 decision lock: `code_verifier_hash` is the ONLY durable
    representation of a challenge code. The plaintext code is
    returned to the caller ONLY through a `DevSink`-shaped object
    that is constructed only by `create_challenge_for_delivery`
    in dev/test mode; the production path (`issue_challenge`)
    returns only a typed `EmailChallengeRow` with no plaintext
    code in any field. Logs never receive the plaintext code.
  - TODO 3 research: codes are 6 decimal digits (~19.9 bits of
    entropy), generated with `secrets.SystemRandom` (the same
    primitive used by `auth_tokens.py:54`). 6 digits is the
    lowest-entropy the research recommended while still being
    usable on mobile keyboards; rate limiting (TODO 7) is what
    caps the brute-force surface, not the code entropy.
  - TODO 3 research: verifier = HMAC-SHA256(code, pepper), keyed by
    the validated `daemon_auth_pepper` from
    `validate_and_get_pepper(settings)`. The code never appears in
    the database, in logs, in audit metadata, or in API responses.
  - TODO 5 schema: the table is the source of truth for the
    challenge row shape. The service issues exactly one INSERT per
    `issue_challenge` call; lifecycle mutations (consume, lock) are
    one UPDATE each, guarded by `attempts_remaining > 0`,
    `consumed_at IS NULL`, `locked_at IS NULL`, and
    `expires_at > NOW()`. This is the canonical pattern for
    single-use, expiring, attempt-limited challenges.
  - TODO 6 settings: TTL and max attempts come from the route layer
    via `EmailChallengeIssueRequest`; the service does NOT default
    them. This forces the caller to acknowledge the live
    `daemon_email_challenge_ttl_seconds` /
    `daemon_email_challenge_max_attempts` values so a config drift
    is explicit at the call site.
  - TODO 7: rate limiting is NOT implemented in this module. The
    rate limiter (`orchestrator.services.identity.rate_limiter`)
    lives in a separate Redis-backed layer and is consumed by the
    route layer BEFORE `issue_challenge` is called. This module
    only enforces the per-row `attempts_remaining` cap.
  - TODO 8: `issue_challenge` does NOT touch `users`,
    `identity_providers`, or `signup_invites`. The challenge is a
    pure inbox-control proof; the route layer (TODO 11) calls
    `AccountService.claim_email_identity` AFTER a successful
    `consume_challenge` to resolve the Daemon account.
  - TODO 9: no HTTP route is added in this TODO. The service is
    backend-only; routes are TODO 11.

This module never:

  - creates a new database pool or Redis pool;
  - logs plaintext codes, raw IPs, raw User-Agent strings, or raw
    pepper values (only the challenge UUID, the normalized email,
    and the attempt count are safe to log);
  - stores the plaintext code in any column of any table (the
    table does not have a column for it; the verifier is the
    only durable artifact);
  - returns the plaintext code through any field of any
    production-path return type (the dev/test sink is a separate
    opt-in object);
  - silently downgrades an expired/locked/consumed challenge to a
    success (each of those raises the typed error, the route
    layer maps to a generic 4xx).
"""

from __future__ import annotations

import hashlib
import hmac as _stdlib_hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.config import Settings

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================


# Code length. 6 decimal digits is the v1 default; ~19.9 bits of
# entropy. The per-IP/per-email rate limiter (TODO 7) is the
# brute-force defense; the code entropy is the minimum that's still
# human-typeable on a mobile keyboard. The TODO 3 research
# recommendation explicitly chose 6 over 8 to keep the UX acceptable.
EMAIL_CODE_NUM_DIGITS = 6

# Default TTL fallback ONLY for tests that construct a service
# without going through the Settings accessor. Production callers
# must pass the live `daemon_email_challenge_ttl_seconds` value.
DEFAULT_TTL_SECONDS = 600

# Default max-attempts fallback ONLY for tests. Production callers
# must pass the live `daemon_email_challenge_max_attempts` value.
DEFAULT_MAX_ATTEMPTS = 5

# IP-hash / UA-hash length used by the schema columns. The route
# layer is expected to produce this same shape (HMAC-SHA256
# truncated to a fixed hex length). The service does not re-truncate
# because that would be a second HMAC operation on already-hashed
# material and the schema column is just `TEXT`.
IP_HASH_LENGTH = 64
USER_AGENT_HASH_LENGTH = 64


# ============================================================================
# Errors
# ============================================================================


class EmailChallengeServiceError(Exception):
    """Base class for email-challenge service errors. The route layer
    (TODO 11) translates every subclass into a generic 4xx response
    that does not leak whether the challenge existed, was expired,
    was locked, or was already consumed. The exception MESSAGE is
    what the route may log (no PII, no plaintext code); the body
    returned to the client is a single opaque string.
    """


class EmailChallengeInvalid(EmailChallengeServiceError):
    """Generic failure: wrong code, expired, locked, consumed, or
    missing challenge. All five conditions map to the same typed
    error so the route layer can map them to a single generic
    `invalid_code` 4xx with the same timing floor. Distinguishing
    these in the response would let an attacker enumerate which
    challenges are alive (decision lock: enumeration resistance).
    """


class EmailChallengeLocked(EmailChallengeInvalid):
    """Attempts exhausted on this row. Subclass of
    `EmailChallengeInvalid` so the route layer can catch
    `EmailChallengeInvalid` (parent) and render a single generic
    4xx response for ALL failure modes (wrong code, expired,
    locked, consumed, missing). The distinct subclass exists
    for the route's internal log: a "too many attempts" event
    may want a different breadcrumb than a "wrong code" event,
    even though the body returned to the client is identical.

    Raised on the consume path that decrements
    `attempts_remaining` to zero.
    """


class EmailChallengeUnavailable(EmailChallengeServiceError):
    """Raised when the service cannot reach its backing store or
    the pepper is invalid. The route layer maps this to a 503 with
    a `Retry-After` header. The route layer's fail-closed policy
    lives in TODO 7 / TODO 11 and is NOT enforced here; the helper
    only signals the underlying capability.
    """


# ============================================================================
# Result types
# ============================================================================


@dataclass(frozen=True)
class EmailChallengeRow:
    """Minimal projection of `email_challenges` consumed by the
    service layer.

    The `id` is the row's primary key, returned to the route layer
    as the public challenge identifier. `normalized_email` is the
    lowercased, trimmed form (same shape as the route layer's
    `normalize_email`). `attempts_remaining` is the live counter;
    `expires_at` is the row-level TTL; `consumed_at` and
    `locked_at` are mutually exclusive terminals.
    """

    id: UUID
    normalized_email: str
    attempts_remaining: int
    expires_at: datetime
    consumed_at: datetime | None
    locked_at: datetime | None
    created_at: datetime

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= _now_utc()

    @property
    def is_terminal(self) -> bool:
        return self.is_consumed or self.is_locked or self.is_expired


@dataclass(frozen=True)
class DevSink:
    """In-process test/dev sink that exposes the plaintext code to
    the caller for a single challenge. THIS TYPE EXISTS ONLY FOR
    DEV/TEST USE. Production code MUST NOT construct or accept
    `DevSink`; the production path returns `EmailChallengeRow`
    only.

    The sink is the only legitimate place a plaintext code can
    appear in the system after `create_challenge_for_delivery`
    returns. The route layer's mail-sender call (TODO 11) reads
    `dev_sink.plaintext_code` to render the email body in
    dev/test environments. In production the mail sender reads
    from an out-of-band channel (e.g. an arq job that owns the
    plaintext for the duration of the request) and this sink is
    never constructed.

    The shape is intentionally minimal: a single optional
    `plaintext_code` per challenge id. The sink stores the code
    in a dict keyed by challenge id so multiple in-flight
    challenges can coexist in a test.
    """

    _store: dict[UUID, str]

    def get(self, challenge_id: UUID) -> str | None:
        """Return the plaintext code for the given challenge id, or
        None if the challenge is not in the sink. The method is
        read-only; the dev/test path does not need a delete API
        because the sink is process-scoped and short-lived.
        """
        return self._store.get(challenge_id)

    def __len__(self) -> int:
        return len(self._store)


@dataclass(frozen=True)
class EmailChallengeIssueRequest:
    """Per-request inputs for `EmailChallengeService.issue_challenge`.

    The `ttl_seconds` and `max_attempts` fields are explicit
    passthroughs from the route layer's read of the live
    `daemon_email_challenge_ttl_seconds` /
    `daemon_email_challenge_max_attempts` Settings fields. The
    service does NOT default them; the route layer MUST pass the
    live values so config drift is explicit at the call site.

    `ip_hash` and `user_agent_hash` are the HMAC-SHA256 hex
    digests (truncated to 64 chars) of the source IP and the
    User-Agent header, keyed by `daemon_auth_pepper`. The route
    layer is responsible for computing these (the same pattern as
    the rate-limiter key material in TODO 7). The service does
    NOT re-hash; doing so would be a second HMAC on already-hashed
    material and the schema column is just `TEXT`.
    """

    normalized_email: str
    ip_hash: str | None
    user_agent_hash: str | None
    ttl_seconds: int
    max_attempts: int


@dataclass(frozen=True)
class EmailChallengeConsumeRequest:
    """Per-request inputs for `EmailChallengeService.consume_challenge`.

    `plaintext_code` is the user-supplied 6-digit code from the
    request body. The service computes the HMAC verifier
    internally and compares it to the stored value with
    `hmac.compare_digest`. The plaintext is never logged, never
    written to the database, never returned in any error
    message, and never exposed in any return type.
    """

    challenge_id: UUID
    plaintext_code: str


# ============================================================================
# Connection protocol
# ============================================================================


class SupportsEmailChallengeQueries(Protocol):
    """The minimal asyncpg connection surface this service uses.

    Defined as a Protocol so the test layer can build a hand-rolled
    `MockConn` that satisfies the structural contract without
    inheriting from asyncpg. The real implementation is any
    `asyncpg.Connection` acquired from `AppState.db_pool`. The
    service issues exactly three statements: an INSERT on
    `issue_challenge` and two UPDATEs on `consume_challenge` (one
    to decrement attempts, one to mark consumed). The service
    does not begin a transaction itself; the caller is expected
    to wrap the call in `async with conn.transaction():` if
    surrounding work is to be atomic with the challenge mutation.
    """

    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...
    def transaction(self: Any) -> Any: ...


# ============================================================================
# Module-level helpers
# ============================================================================


def _now_utc() -> datetime:
    """Single source of truth for the service-side `now()` value.

    Exposed as a module-level function (not a free `datetime.now()`)
    so tests can monkeypatch the clock in one place if they need to.
    """
    return datetime.now(timezone.utc)


def generate_email_code(*, num_digits: int = EMAIL_CODE_NUM_DIGITS) -> str:
    """Generate a CSPRNG numeric email code.

    Uses `secrets.SystemRandom` to pick each digit independently
    from `string.digits`. This is the same primitive
    `auth_tokens.generate_enrollment_code` uses (TODO 0 decision
    lock and TODO 3 research). The output is exactly `num_digits`
    decimal digits with no separator (the route layer is
    responsible for any UI-side formatting; the stored verifier
    is computed on the digit-only string).

    Args:
        num_digits: Number of decimal digits to generate. Defaults
            to 6 (research-recommended). Must be in `[4, 10]` to
            keep the entropy and usability in a sensible range.

    Returns:
        A string of exactly `num_digits` decimal digits.
    """
    if not 4 <= num_digits <= 10:
        raise ValueError(f"num_digits must be in [4, 10], got: {num_digits!r}")
    rng = secrets.SystemRandom()
    return "".join(rng.choice("0123456789") for _ in range(num_digits))


def compute_code_verifier(plaintext_code: str, pepper: str) -> str:
    """Compute the HMAC-SHA256 verifier for a plaintext code.

    The verifier is `HMAC-SHA256(plaintext_code, pepper)`. The
    pepper is the validated `daemon_auth_pepper` from
    `validate_and_get_pepper(settings)`. The output is a 64-char
    lowercase hex digest (the same shape as the existing
    `hash_token` / `hash_enrollment_code` helpers in
    `orchestrator/auth_tokens.py`).

    The function is intentionally not memoized; the cost is one
    HMAC per call and memoization would be a security regression
    (cache pollution attack).

    Args:
        plaintext_code: The 6-digit decimal code (or any
            user-supplied string; the function does not validate
            format). Empty input raises `ValueError` defensively.
        pepper: The validated `daemon_auth_pepper`. Empty input
            raises `ValueError` defensively.

    Returns:
        A 64-character lowercase hex digest.
    """
    if not plaintext_code:
        raise ValueError("compute_code_verifier requires a non-empty plaintext_code")
    if not pepper:
        raise ValueError("compute_code_verifier requires a non-empty pepper")
    return _stdlib_hmac.new(
        pepper.encode("utf-8"),
        plaintext_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def normalize_code(plaintext_code: str) -> str:
    """Normalize a user-supplied code to digits-only.

    Accepts common pasted forms:
      - '123456'
      - '123 456'
      - '  123456  '

    Returns exactly 6 decimal digits (or whatever the configured
    length is; the function takes the length as a parameter).
    Raises `ValueError` if the input cannot be normalized.
    """
    if plaintext_code is None:
        raise ValueError("normalize_code requires a non-None input")
    stripped = plaintext_code.strip()
    digits_only = stripped.replace(" ", "").replace("-", "")
    if not digits_only.isdecimal():
        raise ValueError(f"code must contain only digits, got: {plaintext_code!r}")
    return digits_only


def hash_ip_for_storage(raw_ip: str, pepper: str) -> str:
    """HMAC-SHA256 truncate a raw IP to a fixed-length hex digest.

    Convenience helper used by the route layer (TODO 11) to build
    the `ip_hash` column value. Mirrors the existing
    `hash_key_material` helper in `rate_limiter.py:108-125` but
    returns the full 64-char digest instead of a 16-char
    truncation (the schema column is `TEXT`, so the full digest
    is the canonical value).

    The function is exported for the route layer; the service
    does not call it directly (the route layer is expected to
    produce the `ip_hash` and pass it in via
    `EmailChallengeIssueRequest`).
    """
    if not raw_ip:
        raise ValueError("hash_ip_for_storage requires a non-empty raw_ip")
    if not pepper:
        raise ValueError("hash_ip_for_storage requires a non-empty pepper")
    return _stdlib_hmac.new(
        pepper.encode("utf-8"),
        raw_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_user_agent_for_storage(raw_user_agent: str, pepper: str) -> str:
    """HMAC-SHA256 truncate a raw User-Agent to a fixed-length hex
    digest. Same shape as `hash_ip_for_storage`; exposed as a
    parallel helper so the route layer has both call sites
    documented.
    """
    if not raw_user_agent:
        raise ValueError("hash_user_agent_for_storage requires a non-empty raw_user_agent")
    if not pepper:
        raise ValueError("hash_user_agent_for_storage requires a non-empty pepper")
    return _stdlib_hmac.new(
        pepper.encode("utf-8"),
        raw_user_agent.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ============================================================================
# Service
# ============================================================================


class EmailChallengeService:
    """Email-code challenge lifecycle service.

    Connection-scoped: the service holds a single connection
    reference and is otherwise stateless. The caller (route layer)
    acquires a connection from the shared `AppState.db_pool` and
    passes it in. This is the same pattern as `AccountService`
    (TODO 8) and `issue_device_session` (TODO 9).

    The service is constructed with a `Settings` instance and an
    optional `dev_sink`. The `dev_sink` is `None` in production;
    a `DevSink(_store={})` instance is supplied by dev/test code
    that wants to read the plaintext code back later (e.g. a
    test that simulates a mail-sender sink reading from the
    DevSink). The DevSink is NEVER required for production
    delivery — `create_challenge_for_delivery` works without
    one and returns the plaintext directly to the caller for
    immediate dispatch via the mail sender.
    """

    def __init__(
        self,
        conn: SupportsEmailChallengeQueries,
        settings: Settings,
        *,
        dev_sink: DevSink | None = None,
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._dev_sink = dev_sink

    # ------------------------------------------------------------------
    # Issuance
    # ------------------------------------------------------------------

    async def _create_challenge(
        self,
        request: EmailChallengeIssueRequest,
    ) -> tuple[EmailChallengeRow, str]:
        """Insert a new `email_challenges` row with a fresh code.

        Private helper that performs ONE INSERT and returns both
        the persisted `EmailChallengeRow` and the plaintext code
        that produces the stored HMAC verifier. The function is
        the single source of truth for "issue a challenge": every
        public issuance method delegates to it.

        The plaintext is generated with `generate_email_code`
        (CSPRNG, `secrets.SystemRandom`) and stored as an HMAC
        verifier via `compute_code_verifier(plaintext,
        validate_and_get_pepper(settings))`. The verifier is
        computed BEFORE the INSERT so a single round-trip
        persists the durable state — there is no INSERT-then-
        UPDATE pattern. The plaintext is returned to the caller
        ONLY in memory; the database stores the HMAC verifier,
        never the plaintext.

        If a `DevSink` is configured (dev/test only), the
        plaintext is also stashed under the challenge id so
        later test code can read it via `dev_sink.get(row.id)`.
        The sink is a process-local side-effect; production
        callers that pass `dev_sink=None` get the plaintext in
        the return value and dispatch it via the mail sender
        without any in-process persistence beyond the dispatch.

        Args:
            request: Per-request inputs (see
                `EmailChallengeIssueRequest`).

        Returns:
            `(row, plaintext_code)` where `plaintext_code` is
            the 6-digit decimal code whose HMAC verifier is
            stored in `code_verifier_hash`. The caller is
            responsible for delivering the plaintext (e.g. via
            the mail sender) and MUST NOT retain it beyond
            the dispatch.

        Raises:
            ValueError: invalid request inputs.
            EmailChallengeUnavailable: pepper validation failed
                (propagates from `validate_and_get_pepper`) or
                the INSERT returned no row.
        """
        if not request.normalized_email:
            raise ValueError("issue_challenge requires normalized_email")
        if request.ttl_seconds < 30 or request.ttl_seconds > 3600:
            raise ValueError(f"ttl_seconds must be in [30, 3600], got: {request.ttl_seconds!r}")
        if request.max_attempts < 1 or request.max_attempts > 10:
            raise ValueError(f"max_attempts must be in [1, 10], got: {request.max_attempts!r}")

        # Obtain the pepper through the canonical accessor so
        # the production gate and dev-ephemeral generation are
        # honored. NEVER read `settings.daemon_auth_pepper`
        # directly for cryptographic operations (the inherited
        # TODO 7 lesson).
        pepper = validate_and_get_pepper(self._settings)

        plaintext_code = generate_email_code()
        verifier = compute_code_verifier(plaintext_code, pepper)
        now = _now_utc()
        expires_at = now + timedelta(seconds=request.ttl_seconds)

        row = await self._conn.fetchrow(
            """
            INSERT INTO email_challenges (
                normalized_email,
                code_verifier_hash,
                attempts_remaining,
                expires_at,
                ip_hash,
                user_agent_hash
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id,
                      normalized_email,
                      attempts_remaining,
                      expires_at,
                      consumed_at,
                      locked_at,
                      created_at
            """,
            request.normalized_email,
            verifier,
            request.max_attempts,
            expires_at,
            request.ip_hash,
            request.user_agent_hash,
        )
        if row is None:
            raise EmailChallengeUnavailable("issue_challenge INSERT RETURNING produced no row")
        typed_row = _row_from_record(row)

        if self._dev_sink is not None:
            # Dev/test side-effect: stash the plaintext in the
            # in-memory sink keyed by challenge id. The sink is
            # a process-scoped test helper; production code
            # never sees a DevSink.
            self._dev_sink._store[typed_row.id] = plaintext_code

        return typed_row, plaintext_code

    async def issue_challenge(
        self,
        request: EmailChallengeIssueRequest,
    ) -> EmailChallengeRow:
        """Insert a new `email_challenges` row and discard the
        plaintext code.

        This is the public issue entry point for callers that
        do NOT need to deliver the code (operator-triage
        workflows, tests that exercise the row shape without
        the consume path). The plaintext is generated, HMAC'd,
        stored, and then dropped — the durable artifact is
        `code_verifier_hash`.

        Production `/v1/auth/email/start` (TODO 11) MUST use
        `create_challenge_for_delivery` instead, so the route
        can hand the plaintext to the mail sender.

        Args:
            request: Per-request inputs (see
                `EmailChallengeIssueRequest`).

        Returns:
            The persisted `EmailChallengeRow` (no plaintext code
            in any field).

        Raises:
            ValueError: invalid request inputs.
            EmailChallengeUnavailable: pepper validation failed
                (propagates from `validate_and_get_pepper`).
        """
        row, _plaintext = await self._create_challenge(request)
        return row

    async def create_challenge_for_delivery(
        self,
        request: EmailChallengeIssueRequest,
    ) -> tuple[EmailChallengeRow, str]:
        """Insert a new `email_challenges` row and return the
        plaintext code for immediate dispatch.

        This is the public issue entry point for the route
        layer (TODO 11) — both production and dev/test. The
        function works with or without a configured
        `DevSink`:

          - **Without a DevSink** (production): the function
            returns `(row, plaintext_code)`. The caller MUST
            hand the plaintext to the mail sender
            (TODO 10 `mail_sender.py`) and MUST NOT retain
            it in request memory beyond the dispatch. The
            row is the durable handle; the plaintext is the
            in-flight delivery artifact.

          - **With a DevSink** (dev/test only): the function
            returns `(row, plaintext_code)` AND stashes the
            plaintext under `row.id` in the sink. The sink
            is a process-scoped test helper that lets test
            code read the plaintext back via
            `dev_sink.get(row.id)` for assertion. Production
            deployments never construct a DevSink.

        The implementation is the same single-INSERT path as
        `issue_challenge`; the only difference is that the
        plaintext is returned to the caller rather than
        discarded. There is no INSERT-then-UPDATE pattern: one
        plaintext, one HMAC verifier, one round-trip.

        Returns:
            `(row, plaintext_code)`. The plaintext is the
            6-digit decimal code whose HMAC verifier is stored
            in `code_verifier_hash`.

        Raises:
            ValueError: invalid request inputs.
            EmailChallengeUnavailable: pepper validation failed
                or the INSERT returned no row.
        """
        return await self._create_challenge(request)

    # ------------------------------------------------------------------
    # Verification / consumption
    # ------------------------------------------------------------------

    async def consume_challenge(
        self,
        request: EmailChallengeConsumeRequest,
    ) -> EmailChallengeRow:
        """Verify the presented code against the stored verifier
        and atomically mark the row consumed on success.

        The verification is HMAC-SHA256(plaintext_code, pepper)
        compared to `code_verifier_hash` with
        `hmac.compare_digest`. The plaintext is never written
        to the database, never logged, and never returned in
        the result.

        On success the row is updated with `consumed_at = NOW()`
        and a fresh fetch returns the updated `EmailChallengeRow`.
        On any failure (wrong code, missing row, expired,
        locked, already consumed, attempts exhausted) the row
        is left in its pre-call state EXCEPT for the
        `attempts_remaining` decrement + lockout-on-exhaustion
        path, which is intentional: a wrong attempt still
        consumes one of the row's attempts and may soft-lock
        the row if `attempts_remaining` hits zero.

        The decrement + lock + verifier check is one UPDATE
        with a guarded WHERE clause:

          WHERE id = $1
            AND consumed_at IS NULL
            AND locked_at IS NULL
            AND expires_at > NOW()
            AND code_verifier_hash = $2

        A wrong code means the WHERE does not match; the row
        is left untouched. A right code means the WHERE matches
        and the row is consumed. After the consume, a separate
        UPDATE decrements `attempts_remaining` for the
        wrong-attempt path. This split keeps the
        consume-vs-decrement decision clean and lets the
        caller see `attempts_remaining` for the next attempt.

        Args:
            request: Per-request inputs (see
                `EmailChallengeConsumeRequest`).

        Returns:
            The consumed `EmailChallengeRow` with
            `consumed_at` set to NOW() and `attempts_remaining`
            unchanged (or 0 if it was the final attempt and we
            consumed the last one).

        Raises:
            EmailChallengeInvalid: wrong code, missing row,
                expired, or already consumed. All four map to
                the same typed error so the route layer can
                surface a generic 4xx without an oracle.
            EmailChallengeLocked: attempts exhausted on this
                row (still inside the TTL window).
            EmailChallengeUnavailable: pepper validation
                failed.
        """
        if not request.plaintext_code:
            raise ValueError("consume_challenge requires plaintext_code")

        pepper = validate_and_get_pepper(self._settings)
        presented_verifier = compute_code_verifier(request.plaintext_code, pepper)

        # Step 1: read the row to distinguish "wrong code" from
        # "expired/locked/consumed/missing". The row read is a
        # non-mutating SELECT; the consume happens in step 2.
        # Doing the read first is the only way to return
        # `EmailChallengeLocked` distinctly from
        # `EmailChallengeInvalid` on the attempts-exhausted path
        # without an oracle in the body.
        existing = await self._conn.fetchrow(
            """
            SELECT id,
                   normalized_email,
                   attempts_remaining,
                   expires_at,
                   consumed_at,
                   locked_at,
                   created_at
            FROM email_challenges
            WHERE id = $1
            """,
            request.challenge_id,
        )
        if existing is None:
            raise EmailChallengeInvalid("challenge not found")

        # Map the record to a typed row so the gates below read
        # the same shape the service exposes to the caller.
        typed_existing = _row_from_record(existing)

        if typed_existing.is_consumed:
            raise EmailChallengeInvalid("challenge already consumed")
        if typed_existing.is_locked:
            # Soft-locked: a wrong-attempts-exhausted row that
            # has not yet expired. We map this to the generic
            # invalid path so the route does not differentiate
            # locked from wrong in the body; the typed error is
            # the hook for the route's internal log.
            raise EmailChallengeInvalid("challenge is locked")
        if typed_existing.is_expired:
            raise EmailChallengeInvalid("challenge expired")

        # Step 2: verifier check + atomic consume. The WHERE
        # guard is the authoritative gate: the row is only
        # consumed if the verifier matches AND the row is still
        # in the (active, unconsumed, unlocked, unexpired)
        # state. A concurrent second caller that lands here
        # with the right code will see the row already consumed
        # (RETURNING NULL) and raise `EmailChallengeInvalid`.
        consumed = await self._conn.fetchrow(
            """
            UPDATE email_challenges
            SET consumed_at = NOW()
            WHERE id = $1
              AND consumed_at IS NULL
              AND locked_at IS NULL
              AND expires_at > NOW()
              AND code_verifier_hash = $2
            RETURNING id,
                      normalized_email,
                      attempts_remaining,
                      expires_at,
                      consumed_at,
                      locked_at,
                      created_at
            """,
            request.challenge_id,
            presented_verifier,
        )
        if consumed is not None:
            return _row_from_record(consumed)

        # The UPDATE did not match. Three reasons:
        #   (a) wrong verifier,
        #   (b) row state changed (consumed/locked/expired)
        #       between the SELECT and the UPDATE (race),
        #   (c) challenge id was deleted (impossible without
        #       a hard delete, but defensive).
        # We re-read to distinguish "wrong verifier" from
        # "row-state-changed" so the wrong-verifier path can
        # decrement attempts and possibly lock the row.
        post = await self._conn.fetchrow(
            """
            SELECT id,
                   normalized_email,
                   attempts_remaining,
                   expires_at,
                   consumed_at,
                   locked_at,
                   created_at
            FROM email_challenges
            WHERE id = $1
            """,
            request.challenge_id,
        )
        if post is None:
            raise EmailChallengeInvalid("challenge not found")
        typed_post = _row_from_record(post)

        if typed_post.is_consumed:
            # Race: another caller consumed first.
            raise EmailChallengeInvalid("challenge already consumed")
        if typed_post.is_locked:
            raise EmailChallengeInvalid("challenge is locked")
        if typed_post.is_expired:
            raise EmailChallengeInvalid("challenge expired")

        # Wrong verifier on an active row. Decrement
        # `attempts_remaining` atomically; if the decrement
        # brings it to zero, set `locked_at` to lock the row
        # for the remainder of its TTL. The decrement + lock
        # is one UPDATE with a guarded WHERE.
        decremented = await self._conn.execute(
            """
            UPDATE email_challenges
            SET attempts_remaining = attempts_remaining - 1,
                locked_at = CASE
                    WHEN attempts_remaining - 1 = 0 THEN NOW()
                    ELSE locked_at
                END
            WHERE id = $1
              AND consumed_at IS NULL
              AND locked_at IS NULL
              AND expires_at > NOW()
            """,
            request.challenge_id,
        )
        # `execute` returns a status string ("UPDATE n"); we
        # don't branch on the count, but log a warning if the
        # status indicates no rows were affected (a race we
        # already mapped to typed errors above).
        if not decremented or "0" in str(decremented).split():
            logger.warning(
                "consume_challenge: decrement UPDATE affected 0 rows for id=%s; "
                "concurrent state change",
                request.challenge_id,
            )

        # Re-read to determine the post-decrement state.
        post_decrement = await self._conn.fetchrow(
            """
            SELECT id,
                   normalized_email,
                   attempts_remaining,
                   expires_at,
                   consumed_at,
                   locked_at,
                   created_at
            FROM email_challenges
            WHERE id = $1
            """,
            request.challenge_id,
        )
        if post_decrement is None:
            raise EmailChallengeInvalid("challenge not found")
        typed_post_decrement = _row_from_record(post_decrement)

        if typed_post_decrement.is_locked:
            # Attempts exhausted on this consume attempt; the
            # route may surface a "too many attempts" hint, but
            # the body is still generic.
            raise EmailChallengeLocked("challenge locked: attempts remaining exhausted")

        # Wrong code on an active row with attempts still
        # remaining. Generic invalid; the route may include
        # `attempts_remaining` in its internal log for
        # debugging but the body is opaque.
        raise EmailChallengeInvalid("invalid code")

    # ------------------------------------------------------------------
    # Lockout / inspection
    # ------------------------------------------------------------------

    async def lock_challenge(self, challenge_id: UUID) -> EmailChallengeRow:
        """Force-lock a challenge (e.g. on operator review).

        Sets `locked_at = NOW()` on the row, regardless of
        attempts remaining. Idempotent: re-locking a locked row
        is a no-op (the WHERE guard skips). This is the
        operator/abuse-triage hook; the route layer (TODO 11)
        does NOT call this on the normal user-facing path.

        Returns:
            The (now-locked) `EmailChallengeRow`.

        Raises:
            EmailChallengeInvalid: the row does not exist.
        """
        updated = await self._conn.fetchrow(
            """
            UPDATE email_challenges
            SET locked_at = NOW()
            WHERE id = $1
              AND consumed_at IS NULL
              AND locked_at IS NULL
            RETURNING id,
                      normalized_email,
                      attempts_remaining,
                      expires_at,
                      consumed_at,
                      locked_at,
                      created_at
            """,
            challenge_id,
        )
        if updated is None:
            # Either the row does not exist, or it is already
            # in a terminal state. Re-read to distinguish.
            existing = await self._conn.fetchrow(
                """
                SELECT id,
                       normalized_email,
                       attempts_remaining,
                       expires_at,
                       consumed_at,
                       locked_at,
                       created_at
                FROM email_challenges
                WHERE id = $1
                """,
                challenge_id,
            )
            if existing is None:
                raise EmailChallengeInvalid("challenge not found")
            return _row_from_record(existing)
        return _row_from_record(updated)


# ============================================================================
# Record helpers
# ============================================================================


def _row_from_record(record: Any) -> EmailChallengeRow:
    """Build an `EmailChallengeRow` from an asyncpg record (or
    compatible).
    """
    return EmailChallengeRow(
        id=_record_uuid(record, "id"),
        normalized_email=_record_str(record, "normalized_email"),
        attempts_remaining=_record_int(record, "attempts_remaining"),
        expires_at=_record_dt(record, "expires_at"),
        consumed_at=_record_dt_or_none(record, "consumed_at"),
        locked_at=_record_dt_or_none(record, "locked_at"),
        created_at=_record_dt(record, "created_at"),
    )


def _record_uuid(record: Any, column: str) -> UUID:
    value = record[column]
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _record_str(record: Any, column: str) -> str:
    value = record[column]
    if value is None:
        raise EmailChallengeServiceError(f"record column {column!r} is unexpectedly None")
    return str(value)


def _record_int(record: Any, column: str) -> int:
    value = record[column]
    if value is None:
        raise EmailChallengeServiceError(f"record column {column!r} is unexpectedly None")
    return int(value)


def _record_dt(record: Any, column: str) -> datetime:
    value = record[column]
    if value is None:
        raise EmailChallengeServiceError(f"record column {column!r} is unexpectedly None")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.fromisoformat(str(value))


def _record_dt_or_none(record: Any, column: str) -> datetime | None:
    value = record[column]
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.fromisoformat(str(value))
