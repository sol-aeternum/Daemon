"""Hosted Google ID-token verification service.

This module is the single chokepoint through which the TODO 13
`/v1/auth/google/complete` route verifies a Google Identity Services
ID token against a server-issued nonce challenge. It owns:

  1. Nonce challenge issuance (CSPRNG nonce, stored as an HMAC
     verifier in `google_nonce_challenges`, with `expires_at`,
     `ip_hash`, `user_agent_hash`, and optional
     `user_id_proposed`).
  2. Nonce challenge consumption (look up by HMAC of the
     presented nonce, reject expired/consumed/missing, atomically
     set `consumed_at`).
  3. Google ID-token verification through a narrow
     `google-auth` boundary: the library validates the JWT
     signature + algorithm pin + `iss` + `aud` + `exp`; this
     module enforces the app-level `sub` / `email` /
     `email_verified` / `nonce` / `azp` checks and returns a
     typed `VerifiedGoogleIdentity` projection.

Architecture decisions followed:

  - TODO 0 decision lock: Google `sub` is the durable
    provider identity; email is a mutable attribute. The
    verifier never keys anything on email alone and never
    inserts a user, tenant, session, device, cookie, or
    provider-link row. The verifier's only side effects
    are (a) the nonce INSERT/UPDATE on `google_nonce_challenges`
    and (b) the delegate call to the `google-auth` library
    boundary (which is also side-effect free beyond the
    JWK cache).
  - TODO 3 research: the 12-item verification checklist is
    enforced here. The library handles signature, JWK cache,
    algorithm pin, `iss`, `aud`, and `exp`; the app layer
    handles `sub` presence, `email` presence, `email_verified`
    truth, `nonce` claim cross-binding, and conditional `azp`.
  - TODO 5 schema: the verifier is the single writer of
    `google_nonce_challenges`. The service issues exactly one
    INSERT per `issue_nonce` call and exactly one UPDATE per
    `consume_nonce` call. The service never UPDATEs any
    other table; it does not own `users`, `tenants`,
    `identity_providers`, etc.
  - TODO 6 settings: client ID and audience allowlist come
    from `Settings.daemon_google_client_id` /
    `Settings.daemon_google_audience_allowlist`. The service
    does not default these; the route layer (TODO 13) is
    expected to pass the live values so config drift is
    explicit at the call site. The TTL is passed through
    `GoogleNonceIssueRequest.ttl_seconds` from the
    `daemon_google_nonce_ttl_seconds` field.
  - TODO 8: `VerifiedGoogleIdentity` exposes only the
    `provider_subject` (the durable Google `sub`), the
    normalized email, the original email, and the verification
    timestamp. The TODO 13 route hands this to
    `AccountService.claim_google_identity` to resolve the
    Daemon account; the verifier itself never touches
    account tables.
  - TODO 9: no HTTP route is added in this TODO. The
    service is backend-only; routes are TODO 13.

This module never:

  - creates a new database pool or Redis pool;
  - calls Google network endpoints in tests (the library
    boundary is injected via a Protocol so unit tests can
    substitute a hand-rolled stub that returns pre-built
    claim dicts);
  - logs plaintext nonces, full ID tokens, full email
    addresses, or full provider subjects (only the
    truncated subject prefix is safe to log);
  - stores the plaintext nonce in any column of any
    table (the schema has no column for it; the
    `nonce_verifier_hash` is the only durable artifact);
  - creates a user, tenant, session, device, cookie, or
    provider link on a successful verification (those
    are TODO 13 / TODO 8 / TODO 9 concerns);
  - silently downgrades an expired/consumed/missing
    nonce to a success (each of those raises the typed
    error; the route layer maps to a generic 4xx).
"""

from __future__ import annotations

import hashlib
import hmac as _stdlib_hmac
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.config import Settings
from orchestrator.services.identity.account_service import normalize_email

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================


# Nonce length in bytes; 32 bytes = 256 bits of CSPRNG entropy, the
# research-recommended minimum for server-issued nonces bound to an
# ID-token `nonce` claim. The base64url-encoded form is ~43 chars.
NONCE_NUM_BYTES = 32

# Default TTL fallback ONLY for tests that construct a service
# without going through the Settings accessor. Production callers
# MUST pass the live `daemon_google_nonce_ttl_seconds` value.
DEFAULT_TTL_SECONDS = 600

# Issuer values accepted by the verifier. Google has documented
# `https://accounts.google.com` as the current issuer; the older
# `accounts.google.com` (no scheme) appears in some historical
# tokens. Both are accepted; any other value is rejected.
# See TODO 3 research §1.4 item 2.
GOOGLE_ISSUER_CANONICAL = "https://accounts.google.com"
GOOGLE_ISSUER_LEGACY = "accounts.google.com"

# Reject any token whose `exp - iat` exceeds this window. Google
# ID tokens are short-lived (~1h); longer windows are suspicious
# (research §1.4 item 11). This is a soft guard against replay of
# unusually long-lived tokens that still pass `exp` validation.
MAX_TOKEN_AGE_SECONDS = 3600

# IP-hash / UA-hash length used by the schema columns. Mirrors
# `IP_HASH_LENGTH` / `USER_AGENT_HASH_LENGTH` in
# `email_challenge.py`. The route layer is expected to produce
# this same shape (HMAC-SHA256 hex digest). The service does not
# re-hash: doing so would be a second HMAC operation on
# already-hashed material and the schema column is just `TEXT`.
IP_HASH_LENGTH = 64
USER_AGENT_HASH_LENGTH = 64


# ============================================================================
# Errors
# ============================================================================


class GoogleVerifierError(Exception):
    """Base class for Google verifier errors. The route layer
    (TODO 13) translates every subclass into a generic 4xx
    response that does not leak which check failed. The exception
    MESSAGE is what the route may log (no PII, no token, no
    nonce); the body returned to the client is a single opaque
    string.
    """


class GoogleNonceInvalid(GoogleVerifierError):
    """Generic nonce failure: missing, expired, or already
    consumed. All three conditions map to the same typed
    error so the route layer can map them to a single
    generic 4xx with the same timing floor. Distinguishing
    these in the response would let an attacker enumerate
    which nonces are alive (decision lock: enumeration
    resistance).
    """


class GoogleTokenInvalid(GoogleVerifierError):
    """Generic ID-token failure: signature/library failure,
    wrong `iss`, wrong `aud`, expired, missing `sub`,
    missing `email`, `email_verified != True`, wrong
    `nonce`, present-and-wrong `azp`. All app-level
    verification failures collapse here so the route
    layer can map them to a single generic 4xx without
    leaking which check tripped.
    """


class GoogleVerifierUnavailable(GoogleVerifierError):
    """Raised when the service cannot reach its backing
    store, the pepper is invalid, or the verifier
    callable is not configured. The route layer maps
    this to a 503 with a `Retry-After` header. The
    route layer's fail-closed policy lives in TODO 7 /
    TODO 13 and is NOT enforced here; the helper only
    signals the underlying capability.
    """


# ============================================================================
# Result types
# ============================================================================


@dataclass(frozen=True)
class GoogleNonceRow:
    """Minimal projection of `google_nonce_challenges` consumed
    by the service layer.

    The `id` is the row's primary key. `nonce_verifier_hash` is
    the HMAC verifier; the plaintext nonce is never a row
    field. `expires_at` is the row-level TTL; `consumed_at` is
    a terminal set on successful cross-binding verification.
    `user_id_proposed` is optional context for the TODO 13
    linking path (the route may set it when the nonce is
    issued in the context of an explicit linking intent to
    prevent re-binding a captured Google credential to a
    different user).
    """

    id: UUID
    nonce_verifier_hash: str
    user_id_proposed: UUID | None
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= _now_utc()

    @property
    def is_terminal(self) -> bool:
        return self.is_consumed or self.is_expired


@dataclass(frozen=True)
class VerifiedGoogleIdentity:
    """Verified Google identity returned by
    `GoogleVerifierService.verify_id_token`. This is the
    typed result the TODO 13 route hands to
    `AccountService.claim_google_identity` (TODO 8).

    `provider_subject` is the durable Google `sub`. The
    service NEVER uses email as a durable key (TODO 0
    decision lock); the route layer must thread
    `provider_subject` into the durable-link path.

    `normalized_email` is the LOWER+TRIM form of the
    verified email (the only normalization v1 supports;
    see `decisions.md` and the TODO 8 service). It is
    intended for invite matching and audit only.

    `original_email` is the email exactly as it appears
    in the verified ID token. The route layer MUST NOT
    use it as a durable key; it is exposed for display
    and audit purposes.

    `verified_at` is the server-side wall-clock at the
    moment the verifier cleared all app-level checks.
    """

    provider_subject: str
    normalized_email: str
    original_email: str
    verified_at: datetime


# ============================================================================
# Connection protocol
# ============================================================================


class SupportsGoogleNonceQueries(Protocol):
    """The minimal asyncpg connection surface this service uses.

    Defined as a Protocol so the test layer can build a
    hand-rolled `MockConn` that satisfies the structural
    contract without inheriting from asyncpg. The real
    implementation is any `asyncpg.Connection` acquired
    from `AppState.db_pool`. The service issues exactly
    three statements: one INSERT on `issue_nonce`, one
    SELECT on `consume_nonce` lookup, and one UPDATE on
    `consume_nonce` to atomically set `consumed_at`. The
    service does not begin a transaction itself; the
    caller is expected to wrap the call in
    `async with conn.transaction():` if surrounding
    work is to be atomic with the nonce mutation.
    """

    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...
    def transaction(self: Any) -> Any: ...


# ============================================================================
# Library boundary protocol
# ============================================================================


class GoogleIdTokenVerifier(Protocol):
    """Narrow boundary over the `google-auth` library.

    The TODO 3 research recommendation is to call
    `google.oauth2.id_token.verify_token` (or
    `verify_oauth2_token`). The function implements JWK
    fetch + cache, RS256 signature verification with
    algorithm pin, `iss` + `aud` + `exp` checks, and
    clock-skew tolerance. It returns the decoded claims
    after a successful verification.

    The `audience` parameter accepts either a single
    client ID string or a list of acceptable client IDs;
    the library treats the list form as "the `aud` claim
    must equal one of these". Passing `[client_id] +
    allowlist` lets the library enforce the full audience
    set at the boundary, not just the primary client ID.

    The `request` parameter accepts either a
    `google.auth.transport.requests.Request` (or
    compatible) instance or `None`. The default verifier
    (`default_google_id_token_verifier`) constructs a
    `Request()` when the caller passes `None` so the
    library can perform its JWK fetch.

    The boundary is defined as a Protocol so the test
    layer can substitute a hand-rolled stub that returns
    pre-built claim dicts. This is the ONLY Google
    network call the verifier makes in production;
    tests must inject a stub and never call the live
    endpoint.
    """

    def __call__(
        self,
        id_token: str,
        request: Any,
        audience: str | list[str],
        *,
        certs_url: str | None = None,
    ) -> Mapping[str, Any]: ...


def default_google_id_token_verifier() -> GoogleIdTokenVerifier:
    """Build the production verifier callable.

    Lazy-imports the `google-auth` package so test
    environments without `google-auth` installed (e.g. a
    minimal CI matrix) can still import this module. The
    package is listed as a project dependency in
    `pyproject.toml:22` (`google-auth>=2.53.0`).

    The returned callable:
      - forwards `request` to `verify_token` as-is when
        the caller supplies one (e.g. an authenticated
        `google.auth.transport.requests.Request` that
        carries the user's `Authorization` header for
        `verify_oauth2_token`);
      - constructs a fresh `Request()` when the caller
        passes `None`, so the lower-level `verify_token`
        can still perform its JWK fetch (the library
        requires a transport to issue the certs HTTP
        request, even when no Authorization header is
        needed);
      - forwards the `audience` argument as-is, supporting
        both the `str` and `list[str]` shapes (the
        service passes `[client_id] + allowlist` so the
        library enforces the full audience set at the
        boundary);
      - forwards `certs_url` only when non-None so the
        library's default JWK URL is honored in the
        common case.
    """
    try:
        from google.oauth2 import id_token as _id_token  # type: ignore[import-not-found]
        from google.auth.transport import requests as _requests  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - explicit guard
        raise GoogleVerifierUnavailable(
            "google-auth is not installed; cannot construct the default "
            "Google ID-token verifier. Install google-auth>=2.53.0 or "
            "inject a custom verifier callable."
        ) from exc

    def _verify(
        id_token: str,
        request: Any,
        audience: str | list[str],
        *,
        certs_url: str | None = None,
    ) -> Mapping[str, Any]:
        # `verify_token` requires a transport Request to
        # fetch the JWKs; if the caller passed None,
        # construct a default Request so the library can
        # still issue its certs HTTP GET. The Request is
        # short-lived (one call per verification) and
        # carries no Authorization header; the lower-level
        # `verify_token` does not need one. A caller that
        # already has a Request (e.g. from an HTTP layer
        # for `verify_oauth2_token`) can pass it through
        # unchanged.
        if request is None:
            request = _requests.Request()

        # The `google-auth` library returns the verified
        # claims as a Mapping after RS256 signature,
        # algorithm pin, `iss`, `aud`, and `exp` checks.
        # `audience` is forwarded as-is (str or list[str])
        # so the library enforces the full audience set
        # at the boundary, not just the primary client ID.
        # `certs_url` is forwarded only when non-None so
        # the library default JWK URL is honored in the
        # common case.
        if certs_url is None:
            return _id_token.verify_token(id_token, request, audience=audience)
        return _id_token.verify_token(id_token, request, audience=audience, certs_url=certs_url)

    return _verify


# ============================================================================
# Module-level helpers
# ============================================================================


def _now_utc() -> datetime:
    """Single source of truth for the service-side `now()` value.

    Exposed as a module-level function (not a free
    `datetime.now()`) so tests can monkeypatch the clock in
    one place if they need to.
    """
    return datetime.now(timezone.utc)


def generate_google_nonce(*, num_bytes: int = NONCE_NUM_BYTES) -> str:
    """Generate a CSPRNG nonce for the Google ID-token flow.

    Uses `secrets.token_urlsafe` (the same primitive
    `orchestrator/auth_tokens.py` uses for setup tokens) to
    produce `num_bytes` of entropy rendered as URL-safe
    base64. The output is the plaintext nonce that the
    client passes to `google.accounts.id.initialize({nonce})`
    and that the server stores only as an HMAC verifier.

    Args:
        num_bytes: Number of CSPRNG bytes; the
            `NONCE_NUM_BYTES` default yields 256 bits, the
            research-recommended minimum. Must be in
            `[16, 64]` to keep the entropy and the
            base64url-encoded length in a sensible range.

    Returns:
        A URL-safe base64 string of length
        `4 * ceil(num_bytes / 3)`.
    """
    if not 16 <= num_bytes <= 64:
        raise ValueError(f"num_bytes must be in [16, 64], got: {num_bytes!r}")
    return secrets.token_urlsafe(num_bytes)


def compute_nonce_verifier(nonce: str, pepper: str) -> str:
    """Compute the HMAC-SHA256 verifier for a plaintext nonce.

    The verifier is `HMAC-SHA256(nonce, pepper)`. The
    pepper is the validated `daemon_auth_pepper` from
    `validate_and_get_pepper(settings)`. The output is a
    64-char lowercase hex digest (the same shape as the
    `email_challenges.code_verifier_hash` /
    `signup_invites.token_verifier_hash` columns and the
    `compute_code_verifier` helper in `email_challenge.py`).

    The function is intentionally not memoized; the cost
    is one HMAC per call and memoization would be a
    security regression (cache pollution attack).

    Args:
        nonce: The plaintext nonce (or any caller-supplied
            string; the function does not validate format).
            Empty input raises `ValueError` defensively.
        pepper: The validated `daemon_auth_pepper`. Empty
            input raises `ValueError` defensively.

    Returns:
        A 64-character lowercase hex digest.
    """
    if not nonce:
        raise ValueError("compute_nonce_verifier requires a non-empty nonce")
    if not pepper:
        raise ValueError("compute_nonce_verifier requires a non-empty pepper")
    return _stdlib_hmac.new(
        pepper.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_ip_for_storage(raw_ip: str, pepper: str) -> str:
    """HMAC-SHA256 a raw IP to a fixed-length hex digest.

    Convenience helper used by the route layer (TODO 13)
    to build the `ip_hash` column value. Mirrors the
    `hash_ip_for_storage` helper in `email_challenge.py`;
    both share the same column shape (`TEXT`, full 64-char
    digest).
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
    """HMAC-SHA256 a raw User-Agent to a fixed-length hex
    digest. Same shape as `hash_ip_for_storage`; exposed
    as a parallel helper so the route layer has both call
    sites documented.
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


def parse_audience_allowlist(raw: str | None) -> list[str]:
    """Parse `Settings.daemon_google_audience_allowlist` into a
    list of non-empty, stripped client IDs.

    The Settings field is a comma-separated string (per
    `orchestrator/config.py:351-354`). Empty entries are
    rejected by the config validator; this helper
    defensively strips whitespace and discards empty
    fragments so a permissive config still produces a
    clean allowlist.
    """
    if not raw:
        return []
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def audience_allowed(claims_aud: Any, *, client_id: str, allowlist: list[str]) -> bool:
    """Check whether the `aud` claim matches the configured
    client ID or one of the allowlist entries.

    The Google library already enforces that `claims_aud`
    is a string equal to the audience it was given
    (matching `client_id` here). The allowlist is an
    additional safety net for environments that share a
    code path between dev and prod client IDs.
    """
    if not isinstance(claims_aud, str) or not claims_aud:
        return False
    if claims_aud == client_id:
        return True
    return claims_aud in allowlist


def issuer_allowed(claims_iss: Any) -> bool:
    """Check whether the `iss` claim is a Google issuer.

    Accepts `https://accounts.google.com` (current) and
    `accounts.google.com` (legacy, no scheme). Any other
    value is rejected. The Google library already
    enforces this when given a Request object, but the
    verifier double-checks to harden against a
    misconfigured library boundary.
    """
    if not isinstance(claims_iss, str):
        return False
    return claims_iss in (GOOGLE_ISSUER_CANONICAL, GOOGLE_ISSUER_LEGACY)


# ============================================================================
# Request types
# ============================================================================


@dataclass(frozen=True)
class GoogleNonceIssueRequest:
    """Per-request inputs for
    `GoogleVerifierService.issue_nonce`.

    `ttl_seconds` is an explicit passthrough from the
    route layer's read of the live
    `daemon_google_nonce_ttl_seconds` Settings field. The
    service does NOT default it; the route layer MUST
    pass the live value so config drift is explicit at
    the call site.

    `ip_hash` and `user_agent_hash` are the HMAC-SHA256
    hex digests of the source IP and the User-Agent
    header, keyed by `daemon_auth_pepper`. The route
    layer is responsible for computing these (the same
    pattern as the rate-limiter key material in TODO 7).
    The service does NOT re-hash.

    `user_id_proposed` is OPTIONAL context: the Daemon
    user the client intended to link to. The TODO 13
    route sets this when the nonce is issued in the
    context of an explicit linking intent so that a
    captured Google credential cannot be re-bound to a
    different user on `complete`. The service does not
    use this field for verification; it is stored for
    audit and consumed by the linking path.
    """

    ip_hash: str | None
    user_agent_hash: str | None
    ttl_seconds: int
    user_id_proposed: UUID | None = None


@dataclass(frozen=True)
class GoogleNonceConsumeRequest:
    """Per-request inputs for
    `GoogleVerifierService.consume_nonce`.

    `challenge_id` is the server-issued nonce row id the
    client received from `issue_nonce` and echoed back on the
    completion request.

    `plaintext_nonce` is the nonce the client received
    from `issue_nonce` and presented back on the
    `complete` call. The service computes the HMAC
    verifier internally via
    `compute_nonce_verifier(plaintext, pepper)` and
    matches it against the stored `nonce_verifier_hash`
    through guarded SQL equality on the consume UPDATE.
    The plaintext is never logged, never written to the
    database, and never returned in any error message.
    """

    challenge_id: UUID
    plaintext_nonce: str


@dataclass(frozen=True)
class GoogleIdTokenVerifyRequest:
    """Per-request inputs for
    `GoogleVerifierService.verify_id_token`.

    `id_token_str` is the Google ID token from the
    client. `plaintext_nonce` is the nonce the client
    received from `issue_nonce` and presented back on
    the `complete` call. The verifier (a) calls the
    library boundary to validate signature + `iss` +
    `aud` + `exp`; (b) cross-checks the ID-token
    `nonce` claim against `plaintext_nonce` AFTER the
    nonce row is atomically consumed; (c) enforces
    `sub` present, `email` present, `email_verified`
    true, and (when present) `azp` allowed.

    The verifier itself does NOT call `consume_nonce`;
    the route layer is expected to call `consume_nonce`
    first and pass the consumed row into this request
    so the verifier can re-check the nonce value. This
    ordering is documented at the verify_id_token
    boundary.
    """

    id_token_str: str
    plaintext_nonce: str
    consumed_nonce: GoogleNonceRow


# ============================================================================
# Service
# ============================================================================


class GoogleVerifierService:
    """Google ID-token verification service.

    Connection-scoped: the service holds a single
    connection reference and a `GoogleIdTokenVerifier`
    callable. The caller (route layer) acquires a
    connection from the shared `AppState.db_pool` and
    passes it in; the caller also injects the verifier
    callable (the production caller passes
    `default_google_id_token_verifier()`; tests pass a
    hand-rolled stub).

    The service is constructed with a `Settings` instance,
    a `SupportsGoogleNonceQueries` connection, and a
    `GoogleIdTokenVerifier` callable. The verifier is
    stored as an instance attribute and re-used for
    every call.
    """

    def __init__(
        self,
        conn: SupportsGoogleNonceQueries,
        settings: Settings,
        verifier: GoogleIdTokenVerifier,
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._verifier = verifier

    # ------------------------------------------------------------------
    # Nonce issuance
    # ------------------------------------------------------------------

    async def issue_nonce(
        self,
        request: GoogleNonceIssueRequest,
    ) -> tuple[GoogleNonceRow, str]:
        """Insert a new `google_nonce_challenges` row with a
        fresh CSPRNG nonce.

        The plaintext nonce is generated with
        `generate_google_nonce` (CSPRNG, 32 bytes,
        `secrets.token_urlsafe`) and stored as an HMAC
        verifier via
        `compute_nonce_verifier(plaintext, pepper)`. The
        verifier is computed BEFORE the INSERT so a
        single round-trip persists the durable state.

        The plaintext nonce is returned to the caller
        ONLY in memory. The database stores the HMAC
        verifier; never the plaintext. The route layer
        (TODO 13) is expected to hand the plaintext
        back to the client in the `/v1/auth/google/start`
        response and MUST discard it from request memory
        once the response is sent.

        Args:
            request: Per-request inputs (see
                `GoogleNonceIssueRequest`).

        Returns:
            `(row, plaintext_nonce)` where
            `plaintext_nonce` is the URL-safe base64
            string whose HMAC verifier is stored in
            `nonce_verifier_hash`.

        Raises:
            ValueError: invalid request inputs.
            GoogleVerifierUnavailable: pepper
                validation failed (propagates from
                `validate_and_get_pepper`) or the INSERT
                returned no row.
        """
        if request.ttl_seconds < 30 or request.ttl_seconds > 3600:
            raise ValueError(f"ttl_seconds must be in [30, 3600], got: {request.ttl_seconds!r}")

        # Obtain the pepper through the canonical
        # accessor so the production gate and
        # dev-ephemeral generation are honored. NEVER
        # read `settings.daemon_auth_pepper` directly
        # for cryptographic operations.
        pepper = validate_and_get_pepper(self._settings)

        plaintext_nonce = generate_google_nonce()
        verifier = compute_nonce_verifier(plaintext_nonce, pepper)
        now = _now_utc()
        expires_at = now + timedelta(seconds=request.ttl_seconds)

        row = await self._conn.fetchrow(
            """
            INSERT INTO google_nonce_challenges (
                nonce_verifier_hash,
                user_id_proposed,
                expires_at,
                ip_hash,
                user_agent_hash
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id,
                      nonce_verifier_hash,
                      user_id_proposed,
                      expires_at,
                      consumed_at,
                      created_at
            """,
            verifier,
            request.user_id_proposed,
            expires_at,
            request.ip_hash,
            request.user_agent_hash,
        )
        if row is None:
            raise GoogleVerifierUnavailable("issue_nonce INSERT RETURNING produced no row")
        typed_row = _row_from_record(row)
        return typed_row, plaintext_nonce

    # ------------------------------------------------------------------
    # Nonce consumption
    # ------------------------------------------------------------------

    async def consume_nonce(
        self,
        request: GoogleNonceConsumeRequest,
    ) -> GoogleNonceRow:
        """Look up the nonce row by HMAC of the presented
        nonce and atomically mark it consumed on success.

        The verification is
        `HMAC-SHA256(plaintext_nonce, pepper)` compared
        to `nonce_verifier_hash` via guarded SQL
        equality on the consume UPDATE, and it is bound
        to the server-issued row id from
        `request.challenge_id`. The verifier for the
        presented nonce is computed locally with
        `compute_nonce_verifier` and then matched inside
        the `WHERE id = $1 AND nonce_verifier_hash = $2
        AND consumed_at IS NULL AND expires_at > NOW()`
        clause; a wrong challenge id or wrong nonce
        leaves the UPDATE with `RETURNING NULL` and the
        helper raises the typed error. There is no in-Python
        `hmac.compare_digest` call: the SQL equality is
        the authoritative match, and the same WHERE
        guard is what makes the consume atomic with the
        row-state check (concurrent second callers see
        the row already consumed and fail).

        The plaintext is never written to the database,
        never logged, and never returned in the result.
        On success the row is updated with
        `consumed_at = NOW()` and a fresh fetch returns
        the updated `GoogleNonceRow`. On any failure
        (wrong nonce, missing row, expired, already
        consumed) the row is left in its pre-call state
        and the helper raises the typed error.

        Args:
            request: Per-request inputs (see
                `GoogleNonceConsumeRequest`).

        Returns:
            The consumed `GoogleNonceRow` with
            `consumed_at` set to NOW().

        Raises:
            ValueError: invalid request inputs.
            GoogleNonceInvalid: wrong nonce, missing
                row, expired, or already consumed. All
                four map to the same typed error so the
                route layer can surface a generic 4xx
                without an oracle.
            GoogleVerifierUnavailable: pepper
                validation failed.
        """
        if not request.plaintext_nonce:
            raise ValueError("consume_nonce requires plaintext_nonce")

        pepper = validate_and_get_pepper(self._settings)
        presented_verifier = compute_nonce_verifier(request.plaintext_nonce, pepper)

        # Step 1: read the row to distinguish "wrong
        # nonce" from "expired/consumed/missing" for
        # the route's internal log. The row read is a
        # non-mutating SELECT; the consume happens in
        # step 2.
        existing = await self._conn.fetchrow(
            """
            SELECT id,
                   nonce_verifier_hash,
                   user_id_proposed,
                   expires_at,
                   consumed_at,
                   created_at
            FROM google_nonce_challenges
            WHERE id = $1
              AND nonce_verifier_hash = $2
            """,
            request.challenge_id,
            presented_verifier,
        )
        if existing is None:
            raise GoogleNonceInvalid("nonce not found")
        typed_existing = _row_from_record(existing)

        if typed_existing.is_consumed:
            raise GoogleNonceInvalid("nonce already consumed")
        if typed_existing.is_expired:
            raise GoogleNonceInvalid("nonce expired")

        # Step 2: verifier check + atomic consume. The
        # WHERE guard is the authoritative gate: the
        # row is only consumed if the verifier matches
        # AND the row is still in the (active,
        # unconsumed, unexpired) state. A concurrent
        # second caller that lands here with the right
        # nonce will see the row already consumed
        # (RETURNING NULL) and raise the typed error.
        consumed = await self._conn.fetchrow(
            """
            UPDATE google_nonce_challenges
            SET consumed_at = NOW()
            WHERE id = $1
              AND nonce_verifier_hash = $2
              AND consumed_at IS NULL
              AND expires_at > NOW()
            RETURNING id,
                      nonce_verifier_hash,
                      user_id_proposed,
                      expires_at,
                      consumed_at,
                      created_at
            """,
            request.challenge_id,
            presented_verifier,
        )
        if consumed is None:
            # Re-read to distinguish "wrong verifier"
            # from "row-state-changed" (race).
            post = await self._conn.fetchrow(
                """
                SELECT id,
                       nonce_verifier_hash,
                       user_id_proposed,
                       expires_at,
                       consumed_at,
                       created_at
                FROM google_nonce_challenges
                WHERE id = $1
                  AND nonce_verifier_hash = $2
                """,
                request.challenge_id,
                presented_verifier,
            )
            if post is None:
                raise GoogleNonceInvalid("nonce not found")
            typed_post = _row_from_record(post)
            if typed_post.is_consumed:
                raise GoogleNonceInvalid("nonce already consumed")
            if typed_post.is_expired:
                raise GoogleNonceInvalid("nonce expired")
            # Conservative: treat any other post-state
            # (e.g. truncated row) as invalid.
            raise GoogleNonceInvalid("nonce not consumable")
        return _row_from_record(consumed)

    # ------------------------------------------------------------------
    # ID-token verification
    # ------------------------------------------------------------------

    async def verify_id_token(
        self,
        request: GoogleIdTokenVerifyRequest,
    ) -> VerifiedGoogleIdentity:
        """Verify a Google ID token's app-level claims and
        return a typed `VerifiedGoogleIdentity`.

        The library boundary (injected via the
        `GoogleIdTokenVerifier` callable) handles the
        signature verification, JWK cache, algorithm
        pin, `iss` / `aud` / `exp` checks, and clock-skew
        tolerance. This helper enforces the app-level
        checks: `sub` present, `email` present,
        `email_verified` true, `nonce` claim equals the
        plaintext nonce from the consumed challenge
        row, and (when present) `azp` is in the
        audience allowlist.

        The function is side-effect-free beyond the
        library call: it does NOT create a user,
        tenant, session, device, cookie, or provider
        link. The TODO 13 route hands the returned
        `VerifiedGoogleIdentity` to
        `AccountService.claim_google_identity` (TODO 8)
        to resolve the Daemon account; the verifier
        itself never touches account tables.

        Args:
            request: Per-request inputs (see
                `GoogleIdTokenVerifyRequest`). The
                `consumed_nonce` field is the
                already-consumed `GoogleNonceRow`
                returned by `consume_nonce`. The route
                layer is expected to call
                `consume_nonce` first, then pass the
                row in here, so the verifier can
                re-check the nonce value.

        Returns:
            A `VerifiedGoogleIdentity` projection
            (`provider_subject`, `normalized_email`,
            `original_email`, `verified_at`).

        Raises:
            ValueError: invalid request inputs.
            GoogleTokenInvalid: any app-level
                verification failure (signature,
                `iss`, `aud`, `exp`, `sub`, `email`,
                `email_verified`, `nonce`, `azp`).
                All paths collapse to the same typed
                error so the route layer can surface
                a generic 4xx without an oracle.
            GoogleVerifierUnavailable: pepper
                validation failed, the audience is
                not configured, or the verifier
                callable raised an unexpected error.
        """
        if not request.id_token_str:
            raise ValueError("verify_id_token requires id_token_str")
        if not request.plaintext_nonce:
            raise ValueError("verify_id_token requires plaintext_nonce")
        if request.consumed_nonce is None:
            raise ValueError("verify_id_token requires a consumed_nonce row")

        # Audience: the configured Google client ID is
        # the primary audience, and the allowlist
        # extends the set of acceptable client IDs for
        # dev/staging. We pass the FULL list
        # `[client_id] + allowlist` to the library so
        # the library enforces the complete audience
        # set at the boundary (the `google-auth`
        # library treats the list form as "the `aud`
        # claim must equal one of these"). The
        # post-decode `audience_allowed(...)` check
        # below remains as defense-in-depth in case a
        # future library version regresses the list
        # form.
        client_id = self._settings.daemon_google_client_id
        if not client_id:
            raise GoogleVerifierUnavailable(
                "daemon_google_client_id is not configured; "
                "the verifier cannot validate the audience."
            )
        allowlist = parse_audience_allowlist(self._settings.daemon_google_audience_allowlist)
        audience: str | list[str] = [client_id, *allowlist] if allowlist else client_id

        # Library call. The Google library returns the
        # verified claims as a dict after a successful
        # signature + algorithm-pin + iss + aud + exp
        # validation. Any library-side failure (bad
        # signature, wrong issuer, wrong audience,
        # expired token) raises a google.auth exception
        # subclass; we collapse every library failure
        # to `GoogleTokenInvalid` so the route layer
        # can map to a single generic 4xx. The default
        # verifier (`default_google_id_token_verifier`)
        # constructs a `Request()` when `request is None`
        # so the library can perform its JWK fetch.
        try:
            claims = self._verifier(
                request.id_token_str,
                None,
                audience,
            )
        except GoogleTokenInvalid:
            raise
        except GoogleVerifierUnavailable:
            raise
        except Exception as exc:
            logger.info(
                "google verifier library boundary raised: %s",
                type(exc).__name__,
            )
            raise GoogleTokenInvalid("google id token verification failed") from exc

        if not isinstance(claims, dict):
            raise GoogleTokenInvalid("google id token claims are not a dict")

        # Re-check issuer. The library already enforces
        # this when given a Request, but we double-check
        # to harden against a misconfigured boundary.
        if not issuer_allowed(claims.get("iss")):
            raise GoogleTokenInvalid("google id token issuer rejected")

        # Re-check audience. The library already
        # enforces `aud == client_id`; the allowlist
        # is an additional safety net.
        if not audience_allowed(claims.get("aud"), client_id=client_id, allowlist=allowlist):
            raise GoogleTokenInvalid("google id token audience rejected")

        # Token age: reject if `exp - iat > 1h` to
        # harden against replay of unusually long-lived
        # tokens that still pass `exp` validation.
        iat_value = claims.get("iat")
        exp_value = claims.get("exp")
        if isinstance(iat_value, (int, float)) and isinstance(exp_value, (int, float)):
            if exp_value - iat_value > MAX_TOKEN_AGE_SECONDS:
                raise GoogleTokenInvalid("google id token age exceeds limit")

        # `sub` must be present and a non-empty
        # string. This is the durable Google account
        # identifier; the TODO 0 decision lock
        # mandates it as the provider key.
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise GoogleTokenInvalid("google id token missing sub claim")

        # `email` must be present and a non-empty
        # string. Mutable per decision lock; used for
        # invite matching only, never as a durable
        # account key.
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise GoogleTokenInvalid("google id token missing email claim")

        # `email_verified` must be true. False
        # indicates the user has not proven control
        # of the email; the link path requires a
        # verified email per the TODO 0 decision lock.
        email_verified = claims.get("email_verified")
        if email_verified is not True:
            raise GoogleTokenInvalid("google id token email is not verified")

        # `nonce` claim must equal the plaintext
        # nonce from the consumed challenge row. This
        # is the cross-binding check that proves the
        # ID token was minted for the same challenge
        # the server issued. Mismatch -> 401.
        claim_nonce = claims.get("nonce")
        if not isinstance(claim_nonce, str) or not claim_nonce:
            raise GoogleTokenInvalid("google id token missing nonce claim")
        if not _stdlib_hmac.compare_digest(claim_nonce, request.plaintext_nonce):
            raise GoogleTokenInvalid("google id token nonce does not match")

        # `azp` is conditional: if present, it must be
        # in the audience allowlist (it may differ from
        # `aud` when the same user is using a
        # different client). If absent, we do not
        # fail. See TODO 3 research §1.4 item 9.
        azp = claims.get("azp")
        if azp is not None:
            if not isinstance(azp, str) or not azp:
                raise GoogleTokenInvalid("google id token azp is not a string")
            if azp != client_id and azp not in allowlist:
                raise GoogleTokenInvalid("google id token azp rejected")

        # All app-level checks cleared. Build the
        # typed identity projection. Email
        # normalization reuses the TODO 8 helper
        # (LOWER+TRIM only; no Gmail-style
        # normalization in v1 per the decision lock).
        try:
            normalized = normalize_email(email)
        except ValueError as exc:
            raise GoogleTokenInvalid("google id token email is not normalizable") from exc

        return VerifiedGoogleIdentity(
            provider_subject=sub,
            normalized_email=normalized,
            original_email=email,
            verified_at=_now_utc(),
        )


# ============================================================================
# Record helpers
# ============================================================================


def _row_from_record(record: Any) -> GoogleNonceRow:
    """Build a `GoogleNonceRow` from an asyncpg record (or
    compatible).
    """
    return GoogleNonceRow(
        id=_record_uuid(record, "id"),
        nonce_verifier_hash=_record_str(record, "nonce_verifier_hash"),
        user_id_proposed=_record_uuid_or_none(record, "user_id_proposed"),
        expires_at=_record_dt(record, "expires_at"),
        consumed_at=_record_dt_or_none(record, "consumed_at"),
        created_at=_record_dt(record, "created_at"),
    )


def _record_uuid(record: Any, column: str) -> UUID:
    value = record[column]
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _record_uuid_or_none(record: Any, column: str) -> UUID | None:
    value = record[column]
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _record_str(record: Any, column: str) -> str:
    value = record[column]
    if value is None:
        raise GoogleVerifierError(f"record column {column!r} is unexpectedly None")
    return str(value)


def _record_dt(record: Any, column: str) -> datetime:
    value = record[column]
    if value is None:
        raise GoogleVerifierError(f"record column {column!r} is unexpectedly None")
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
