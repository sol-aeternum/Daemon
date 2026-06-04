"""Identity-aware device session issuance boundary.

This module is the single chokepoint through which hosted identity
completion routes (TODO 11 email, TODO 13 Google) issue a Daemon device
session after a successful account/tenant claim. It accepts the
resolved `ClaimResult` from `AccountService` (TODO 8) plus per-request
transport metadata and produces a new device + session row in the
database with a fresh access/refresh token pair.

The helper is a backend-only service boundary. No HTTP route is
added in this TODO because identity session issuance MUST be
gated on a verified identity proof (email code consumption or
Google ID-token verification), and those routes are TODO 11 and
TODO 13. A route that accepts caller-supplied user_id/tenant_id
and mints Daemon tokens would bypass the proof-of-identity
invariant and is explicitly out of scope for TODO 9. The thin
test boundary is helper-level only.

Architecture decisions followed:

  - TODO 0 decision lock: provider tokens, email codes, and Google
    credentials are NOT accepted as Daemon API auth. This helper
    exchanges a verified identity claim for a Daemon-issued
    access/refresh pair; the device/session substrate is the only
    thing the protected API trust boundary still recognizes.
  - TODO 5 schema: `devices.tenant_id` and `sessions.tenant_id` are
    nullable FKs to `tenants(id)`. The helper populates them with the
    ClaimResult's tenant.id so the backfilled singleton and the
    identity-claimed tenants share a single tenant-link column. The
    helper does not enforce NOT NULL; the route layer is expected to
    pass a tenant from a successful `ClaimResult`.
  - TODO 8 service: the helper accepts the resolved `user_id` and
    `tenant_id` directly. It never re-resolves account/tenant. The
    route layer is expected to call `AccountService.claim_email_identity`
    or `claim_google_identity` first, then pass the result here.
  - Decision 16 (web cookie): refresh cookie is `__Host-daemon_refresh`
    in production, `daemon_refresh` in development. The helper
    advertises the cookie policy in `IssuedSession.refresh_transport` /
    `IssuedSession.refresh_max_age_seconds` so the route layer uses
    the right `auth_cookies.build_refresh_cookie` call.
  - TODO 0 decision lock (web/native): mixed cookie+body refresh
    transport is rejected before any token burn. The helper enforces
    this by validating the inputs before touching the database and by
    computing a single `refresh_transport` value the caller must
    honor. The route layer is responsible for the HTTP-level
    "no cookie in body" / "no body refresh in cookie" guard at the
    request surface; the helper provides the contractual decision.

Refresh transport semantics (v1):

  - `client_kind="web"` + `device_persistence="private"`:
    HttpOnly refresh cookie, Max-Age = `private_refresh_ttl_days`
    (default 90), refresh token NEVER returned in the response body.
  - `client_kind="web"` + `device_persistence="temporary"`:
    HttpOnly refresh cookie with `Max-Age=None` (browser-session
    cookie, cleared on close) when
    `temporary_refresh_ttl_seconds == 0`, or a short-lived cookie
    when the operator configures a positive value. refresh token
    NEVER returned in the response body.
  - `client_kind="native"` (any persistence): refresh token returned
    in the response body only, NEVER as a Set-Cookie header. The
    client persists the refresh in the OS keychain/keystore.

Persistence is encoded in the cookie Max-Age, not in the database
schema. Migration 031 constrains `sessions.client_kind` to
`('web', 'native')`; the audit recommended against introducing a
`temporary` `client_kind` value (see TODO 2 audit decisions). A
later TODO may add a `device_persistence` column to `sessions` to
preserve the original persistence across refresh rotations; v1
rotates to the default private TTL because the DB does not remember
the originating persistence.

This module never:

  - creates a new database pool (it consumes the caller-supplied
    `asyncpg.Connection` so concurrent issuance is transaction-safe);
  - sets refresh cookies (cookie emission is the route layer's
    responsibility; the helper exposes the transport decision in
    `IssuedSession` so the caller can pick the right shape);
  - logs raw tokens, raw refresh tokens, or raw user ids (only the
    device/session UUIDs are safe to log);
  - weakens refresh rotation/reuse detection (the helper stores only
    SHA-256 token hashes, the same shape the existing
    setup/enrollment/refresh routes use);
  - bypasses the existing web/native isolation invariant (a native
    caller can never receive a refresh cookie; a web caller never
    receives a refresh token in the body).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from orchestrator.auth_tokens import generate_token, hash_token

logger = logging.getLogger(__name__)


# Module-level configuration. These constants are the v1 defaults;
# the route layer reads the live values from `Settings` and passes
# them through `IssueSessionRequest`. The defaults are kept here so
# the helper is testable in isolation without a Settings singleton.
ACCESS_TOKEN_TTL_MINUTES = 30
DEFAULT_PRIVATE_REFRESH_TTL_DAYS = 90
# 0 = session cookie (cleared on browser close). The Settings field
# `daemon_temporary_refresh_ttl_seconds` overrides this at runtime.
# When 0, the DB-side refresh_expires_at falls back to a defensive
# fixed window (1 hour) so the server-side check is finite even
# though the browser-side cookie is session-cookie (no Max-Age).
DEFAULT_TEMPORARY_REFRESH_TTL_SECONDS = 0
# Defensive cap when `temporary_refresh_ttl_seconds == 0` so the
# session row has a finite refresh_expires_at. This is the worst-
# case window a leaked temporary refresh could be replayed for in
# the unlikely case an attacker exfiltrates the cookie from a
# session-cookie browser. 1 hour matches the research recommendation
# for "short/session cookie" abuse-tolerance.
TEMPORARY_DB_FALLBACK_TTL_SECONDS = 3600


# Type alias for the persistence field. Constrained to two values
# for v1; future "ephemeral" / "scoped" semantics can extend the
# literal. The migration 031 client_kind CHECK is preserved by
# encoding persistence in cookie TTL, not in a new client_kind.
DevicePersistence = Literal["private", "temporary"]


# Transport decision the route layer consumes. The helper picks
# `cookie` for any `client_kind == "web"` request and `body` for
# `client_kind == "native"`. The route layer never overrides this.
SessionRefreshTransport = Literal["cookie", "body"]


class SessionIssuanceError(Exception):
    """Base class for session-issuance errors. The route layer is
    expected to translate these into 4xx responses that do not leak
    internal state.

    Subclasses are deliberately narrow so the route layer can map
    each to a user-visible response shape (e.g. 400 invalid_kind).
    """


class InvalidClientKind(SessionIssuanceError):
    """`client_kind` was not 'web' or 'native'."""


class InvalidDevicePersistence(SessionIssuanceError):
    """`device_persistence` was not 'private' or 'temporary'."""


@dataclass(frozen=True)
class IssueSessionRequest:
    """Per-request inputs for the session issuance helper.

    `user_id` is the resolved Daemon user from a successful
    `ClaimResult` (TODO 8). `tenant_id` is the user's personal
    tenant; passing None is allowed for the singleton backfill path
    where the migration has already populated tenant_id, but
    identity-claimed tenants should always pass a real tenant id.

    `client_kind` selects the refresh transport. `device_persistence`
    selects the cookie Max-Age for `client_kind == "web"` and is
    ignored for `client_kind == "native"` (native refresh is
    persisted in the OS keychain; the persistence flag is
    cookie-only). The private/temporary refresh TTL knobs are
    plumbed through as explicit fields so the route layer can pass
    the live `Settings` values without this module importing them
    directly.

    `device_name` is the operator-visible display name shown in
    the device list. `platform` is the optional client platform
    string (e.g. "macos", "ios", "android", "linux", "windows");
    it is stored on the device row for triage and has no
    authorization role.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    client_kind: Literal["web", "native"]
    device_persistence: DevicePersistence
    device_name: str
    platform: str | None = None
    private_refresh_ttl_days: int = DEFAULT_PRIVATE_REFRESH_TTL_DAYS
    temporary_refresh_ttl_seconds: int = DEFAULT_TEMPORARY_REFRESH_TTL_SECONDS


@dataclass(frozen=True)
class IssuedSession:
    """Result of a successful device-session issuance.

    The helper returns the (still-private) tokens and a
    transport-decision hint. The route layer is responsible for
    deciding whether to set a cookie, return the refresh in the
    JSON body, or both. The transport decision is pre-computed here
    so the route layer never has to re-derive it.

    `refresh_max_age_seconds` is the cookie Max-Age value the route
    layer should pass to `auth_cookies.build_refresh_cookie`. None
    means "session cookie" (no Max-Age attribute) — the browser
    keeps the cookie until the user closes the browser.
    """

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    session_id: uuid.UUID
    device_id: uuid.UUID
    client_kind: str
    refresh_transport: SessionRefreshTransport
    refresh_max_age_seconds: int | None


class SupportsSessionIssuanceQueries(Protocol):
    """The minimal asyncpg connection surface this helper uses.

    Defined as a Protocol so the test layer can build a hand-rolled
    `MockConn` that satisfies the structural contract without
    inheriting from asyncpg. The real implementation is any
    `asyncpg.Connection` acquired from `AppState.db_pool`. The
    caller is expected to wrap the helper call in
    `async with conn.transaction():` so the device + session
    inserts are atomic with the surrounding claim resolution
    (TODO 8 / TODO 11 / TODO 13).

    `fetchval` is used for the INSERT ... RETURNING id shape; the
    helper never issues a SELECT.
    """

    async def fetchval(self, query: str, *args: Any) -> Any: ...


# ============================================================================
# Helpers
# ============================================================================


def _now_utc() -> datetime:
    """Single source of truth for the helper-side `now()` value.

    Exposed as a module-level function (not a free `datetime.now()`)
    so tests can monkeypatch the clock in one place if they need to.
    """
    return datetime.now(timezone.utc)


def _compute_refresh_ttl_seconds(
    *,
    device_persistence: DevicePersistence,
    private_refresh_ttl_days: int,
    temporary_refresh_ttl_seconds: int,
) -> tuple[int | None, int]:
    """Return `(cookie_max_age_seconds, db_refresh_ttl_seconds)`.

    `cookie_max_age_seconds` is the value to pass to
    `auth_cookies.build_refresh_cookie(..., max_age=...)`; None means
    "no Max-Age attribute" (session cookie, browser-managed).

    `db_refresh_ttl_seconds` is the value to use for
    `sessions.refresh_expires_at` — i.e. the server-side cap for
    refresh validation. For the temporary + session-cookie case
    (`temporary_refresh_ttl_seconds == 0`) the DB-side cap falls
    back to `TEMPORARY_DB_FALLBACK_TTL_SECONDS` so the server check
    is finite even though the browser-side cookie is
    session-managed.

    The two are decoupled on purpose: the cookie TTL is the
    user-facing lifetime, the DB TTL is the server-side cap. They
    coincide for the long-lived private case and the
    short-lived-explicit-temporary case; they diverge only for the
    session-cookie temporary case.
    """
    if device_persistence == "private":
        cookie_max_age = int(timedelta(days=private_refresh_ttl_days).total_seconds())
        db_ttl = cookie_max_age
        return cookie_max_age, db_ttl

    # device_persistence == "temporary"
    if temporary_refresh_ttl_seconds <= 0:
        return None, TEMPORARY_DB_FALLBACK_TTL_SECONDS

    return int(temporary_refresh_ttl_seconds), int(temporary_refresh_ttl_seconds)


# ============================================================================
# Public helper
# ============================================================================


async def issue_device_session(
    conn: SupportsSessionIssuanceQueries,
    request: IssueSessionRequest,
) -> IssuedSession:
    """Create a new device + session and return the issued tokens.

    The helper is connection-scoped: it consumes a single
    `asyncpg.Connection`-shaped object and issues exactly two SQL
    statements (one INSERT for `devices`, one INSERT for `sessions`).
    The caller is responsible for wrapping the call in
    `async with conn.transaction():` so the high-level claim
    (TODO 8) and the session issuance are atomic together. The
    helper itself does not begin a transaction so the route layer
    can compose it with surrounding work without nested
    transactions (asyncpg does not support nested transactions).

    Args:
        conn: An asyncpg-compatible connection. Must already be
            inside a `conn.transaction()` block managed by the
            caller.
        request: The per-request inputs (see `IssueSessionRequest`).

    Returns:
        `IssuedSession` with the (private) access + refresh tokens,
        the session/device UUIDs, and the cookie-transport decision
        the route layer must honor.

    Raises:
        InvalidClientKind: `client_kind` was not 'web' or 'native'.
        InvalidDevicePersistence: `device_persistence` was not
            'private' or 'temporary'.
        Exception: any DB-level error (FK violation, etc.) is
            propagated unchanged. The route layer is expected to
            catch and translate to a 4xx/5xx response.
    """
    if request.client_kind not in ("web", "native"):
        raise InvalidClientKind(
            f"client_kind must be 'web' or 'native', got {request.client_kind!r}"
        )
    if request.device_persistence not in ("private", "temporary"):
        raise InvalidDevicePersistence(
            f"device_persistence must be 'private' or 'temporary', "
            f"got {request.device_persistence!r}"
        )

    cookie_max_age, db_refresh_ttl_seconds = _compute_refresh_ttl_seconds(
        device_persistence=request.device_persistence,
        private_refresh_ttl_days=request.private_refresh_ttl_days,
        temporary_refresh_ttl_seconds=request.temporary_refresh_ttl_seconds,
    )

    now = _now_utc()
    access_expires_at = now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
    refresh_expires_at = now + timedelta(seconds=db_refresh_ttl_seconds)

    # Step 1: device row. tenant_id is nullable (migration 032
    # leaves it NULL for non-singleton users until they claim a
    # personal tenant; the service layer TODO 8 stamps it on
    # identity-claim). The display_name + platform are required by
    # the schema (display_name NOT NULL; platform NULLABLE).
    device_id = await conn.fetchval(
        """
        INSERT INTO devices (user_id, tenant_id, display_name, platform)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        request.user_id,
        request.tenant_id,
        request.device_name,
        request.platform,
    )

    # Step 2: tokens. generate_token() yields secrets.token_urlsafe(32)
    # — 256 bits of entropy. hash_token() is the canonical SHA-256
    # hex digest used by every other auth route. The DB never sees
    # the plaintext after this function returns.
    access_token = generate_token()
    refresh_token = generate_token()

    # Step 3: session row. client_kind is constrained by migration
    # 031 to ('web', 'native'); we store 'web' for both private and
    # temporary web persistence. tenant_id is the same value the
    # device row received; future multi-tenant migrations may
    # differentiate, but v1 keeps the device/session tenant in sync.
    session_id = await conn.fetchval(
        """
        INSERT INTO sessions (
            user_id, device_id, client_kind, tenant_id,
            access_token_hash, access_expires_at,
            refresh_token_hash, refresh_expires_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        request.user_id,
        device_id,
        request.client_kind,
        request.tenant_id,
        hash_token(access_token),
        access_expires_at,
        hash_token(refresh_token),
        refresh_expires_at,
        now,
    )

    # Step 4: transport decision. The route layer reads this and
    # either sets a cookie (web) or returns the refresh in the JSON
    # body (native). The helper does not set the cookie itself.
    refresh_transport: SessionRefreshTransport = (
        "cookie" if request.client_kind == "web" else "body"
    )

    return IssuedSession(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        session_id=session_id,
        device_id=device_id,
        client_kind=request.client_kind,
        refresh_transport=refresh_transport,
        refresh_max_age_seconds=cookie_max_age,
    )
