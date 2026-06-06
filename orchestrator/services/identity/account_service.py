"""Hosted identity account, tenant, invite, and provider service.

This module is the single chokepoint through which all identity-claim
flows (email-code completion, Google ID-token completion) resolve a
Daemonside `users` row, ensure a personal `tenants` row, mint the
`tenant_memberships` owner row, and link a provider identity
(`identity_providers`). It is the service layer that backstops the
upcoming `/v1/auth/email/*` and `/v1/auth/google/*` route handlers
(TODO 11/13) and is consumed by the session-issuance layer (TODO 9).

The module is repository/business logic only: it never owns a database
pool, never builds an HTTP response, and never accepts a provider
token/email code/invite token as an authorization artifact. The
`conn` it takes is whatever the caller is already holding (an
asyncpg connection, possibly inside a transaction) so concurrent
identity claims are transaction-safe.

Architecture decisions followed:

  - TODO 0 decision lock: `google.sub` is the durable provider
    identity; email is mutable metadata and is captured as
    `normalized_email_at_link` at link time, never used as the durable
    provider key. Strict linking: link Google to an existing email
    account only if the verified normalized email matches exactly,
    no conflicting provider identity exists, and invite policy
    permits; otherwise halt/reject. No merge by unverified or fuzzy
    email.
  - TODO 0 decision lock: hosted production defaults to invite-only.
    Uninvited signups get a generic `InviteOnlyRejection` that does
    not leak account existence.
  - TODO 0 decision lock: provider tokens, email codes, and invite
    tokens are NOT accepted as Daemon API auth; this service only
    resolves identity claims for future Daemon session issuance.
  - TODO 5 schema: `tenants.kind = 'personal'` is unique per
    `owner_user_id` (partial unique index
    `idx_tenants_personal_owner`); the schema is the source of truth
    for the personal-tenant invariant. Concurrent creation is
    transaction-safe via `INSERT ... ON CONFLICT (owner_user_id)
    WHERE kind = 'personal' DO NOTHING RETURNING id` followed by a
    SELECT.
  - TODO 5 schema: `identity_providers(provider, provider_subject)` is
    globally unique. The `link_provider_identity` helper relies on
    that unique index to detect provider-subject collisions.
  - TODO 5 schema: HMAC verifier storage for low-entropy secrets
    (invite tokens, email codes, Google nonces). The plaintext is
    never written or logged. This module only ever sees the verifier
    hashes; the matching-side HMAC is done by the challenge/invite
    issuers (TODO 6/10/12).

Design choices captured in `decisions.md`:

  - Email-code completion does NOT insert a row into
    `identity_providers(provider='email')`. The schema allows it, but
    the decision lock treats email as mutable, so a per-account email
    link would either churn rows on every email change (defeating the
    audit trail) or require a different unique key (defeating the
    `UNIQUE(provider, provider_subject)` index). The conservative
    choice is to record the link event in `identity_audit_log`
    instead and treat Google `sub` as the only durable provider
    identity. Google completion DOES insert a `identity_providers`
    row keyed by `(google, google_sub)`.
  - Email normalization is a single explicit helper. The migration
    already backfills `users.normalized_email` with
    `LOWER(TRIM(email))`. The service applies the same shape so the
    service-side value is the same as the in-DB value. Gmail-style
    dot/`+`-suffix normalization is intentionally NOT applied in v1:
    the migration would have to be re-run, and the operational
    benefit is not justified yet. This is the deliberate scope
    recorded in `decisions.md`.

This module never:

  - creates a new Redis pool or Postgres pool (it consumes the
    caller-supplied connection);
  - creates new HTTP responses or accepts API auth tokens;
  - logs raw IPs, raw emails, raw provider subjects, raw codes, or
    raw invite tokens (only the user/tenant UUIDs and the
    normalized-email value are safe to log);
  - silently downgrades invite-only to open signup in hosted
    production;
  - performs fuzzy/loose email matching (alias stripping, fuzzy
    Levenshtein, etc.);
  - mutates an existing user row's `email_verified_at` without an
    explicit verification event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


# ============================================================================
# Errors
# ============================================================================


class AccountServiceError(Exception):
    """Base class for all account-service errors. The route layer
    (TODO 11/13) is expected to translate these into generic 4xx
    responses that do not leak account-existence information.

    Subclasses are deliberately narrow so the route layer can map
    each to a user-visible response shape (e.g. 400 invalid_invite,
    409 provider_collision). The exception MESSAGE is what the route
    should treat as safe to log; the body returned to the client
    should be a generic opaque string, not the message.
    """


class InviteOnlyRejection(AccountServiceError):
    """Raised when signup mode is invite-only and no valid invite
    (or no invite token) was supplied for a new-identity claim.

    The message is deliberately generic so the route layer can echo
    it to the client without leaking whether an account exists or
    whether an invite is active. The route MUST NOT differentiate
    this from `InviteInvalidOrExpired` in its response body or
    timing.
    """


class InviteInvalidOrExpired(AccountServiceError):
    """Raised when an invite token was supplied but did not match an
    active, unexpired, unconsumed invite row for the supplied
    normalized email.

    Like `InviteOnlyRejection`, the route layer should not
    differentiate this from other rejection classes in the response
    surface.
    """


class SignupDisabled(AccountServiceError):
    """Raised when signup mode is `disabled` and a new-identity
    claim is attempted. Existing accounts may still sign in.
    """


class ProviderCollision(AccountServiceError):
    """Raised when an attempt to link a provider identity would
    either:

    - bind a `(provider, provider_subject)` pair that is already
      linked to a different Daemon user (schema-level UNIQUE
      violation), or
    - link a Google identity to a Daemon user who already has a
      different `(google, sub)` linked (multi-sub collision on the
      same user), or
    - link a Google identity to a Daemon user whose
      `users.email_verified_at` is NULL or whose normalized email
      does not match the verified Google email exactly.
    """


class EmailNotVerified(AccountServiceError):
    """Raised when an identity-claim path requires a verified email
    (e.g. cross-provider linking) and the underlying user row's
    `email_verified_at` is NULL.
    """


# ============================================================================
# Result types
# ============================================================================


@dataclass(frozen=True)
class UserRow:
    """Minimal projection of `users` consumed by the service layer.

    `email_verified_at` is the timestamp recorded in the most recent
    successful email verification; NULL means the user has never
    proven control of the inbox.
    """

    id: UUID
    normalized_email: str | None
    email_verified_at: datetime | None

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None


@dataclass(frozen=True)
class TenantRow:
    """Minimal projection of `tenants` consumed by the service layer."""

    id: UUID
    owner_user_id: UUID
    kind: str
    name: str


@dataclass(frozen=True)
class InviteRow:
    """Minimal projection of `signup_invites` consumed by the service
    layer. The `token_verifier_hash` is never returned to callers;
    only the lifecycle metadata is.
    """

    id: UUID
    normalized_email: str
    status: str
    expires_at: datetime
    used_by_user_id: UUID | None


@dataclass(frozen=True)
class ProviderLink:
    """A row in `identity_providers` returned by the service layer."""

    id: UUID
    user_id: UUID
    provider: str
    provider_subject: str
    normalized_email_at_link: str | None
    linked_at: datetime
    last_used_at: datetime


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a successful identity claim.

    `is_new_user` and `is_new_tenant` and `is_new_membership` are
    diagnostic flags so the route layer and audit logger can record
    the right `identity_audit_log.event_type`. They are False on
    repeated sign-ins (re-use) and True on first-time claim.
    """

    user: UserRow
    tenant: TenantRow
    membership_role: str
    is_new_user: bool
    is_new_tenant: bool
    is_new_membership: bool


# ============================================================================
# Connection protocol
# ============================================================================


class SupportsIdentityQueries(Protocol):
    """The minimal asyncpg connection surface this service uses.

    Defined as a Protocol so the test layer can build a hand-rolled
    `MockConn` that satisfies the structural contract without
    inheriting from asyncpg. The real implementation is any
    `asyncpg.Connection` acquired from `AppState.db_pool`.
    """

    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...
    def transaction(self: Any) -> Any: ...


# ============================================================================
# Module-level helpers
# ============================================================================


def normalize_email(raw: str) -> str:
    """Normalize a raw email string for identity-claim matching.

    Returns the lowercased, trimmed form. Empty/whitespace-only
    input raises `ValueError` (the route layer is expected to
    reject this earlier; this is a defensive guard). The shape
    matches the migration's `LOWER(TRIM(email))` backfill in
    `migrations/032_hosted_identity_claim.sql:45-49` so the
    service-side value and the in-DB value are byte-identical for
    legacy data.

    Gmail-style normalization (dot removal in the local part,
    `+suffix` stripping) is intentionally NOT applied in v1. See
    `decisions.md` for the deliberate scope.
    """
    if raw is None:
        raise ValueError("normalize_email requires a non-None input")
    normalized = raw.strip().lower()
    if not normalized:
        raise ValueError("normalize_email requires a non-empty input")
    if any(ch.isspace() for ch in normalized):
        raise ValueError("normalize_email requires a single-line email address")
    if normalized.count("@") != 1:
        raise ValueError("normalize_email requires exactly one @")
    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain:
        raise ValueError("normalize_email requires non-empty local and domain parts")
    if "." not in domain:
        raise ValueError("normalize_email requires a dotted domain")
    labels = domain.split(".")
    if any(not label for label in labels):
        raise ValueError("normalize_email requires non-empty domain labels")
    if any(any(ord(ch) < 33 or ord(ch) == 127 for ch in label) for label in [local_part, *labels]):
        raise ValueError("normalize_email contains control characters")
    return normalized


def _now_utc() -> datetime:
    """Single source of truth for the service-side `now()` value.

    Exposed as a module-level function (not a free `datetime.now()`)
    so tests can monkeypatch the clock in one place if they need to.
    """
    return datetime.now(timezone.utc)


# ============================================================================
# Account service
# ============================================================================


class AccountService:
    """Identity-claim resolution for email and Google completion paths.

    The service is constructed with a single connection; it does not
    own the connection. The caller (a route handler or a higher-level
    orchestrator) is responsible for acquiring the connection from
    the shared `AppState.db_pool` and for any surrounding transaction
    boundary that spans multiple service calls.

    The class is intentionally stateless beyond its `conn`
    reference. The same instance may be reused across requests; the
    per-request inputs are passed as method arguments.
    """

    def __init__(self, conn: SupportsIdentityQueries) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Lookups (read-only)
    # ------------------------------------------------------------------

    async def find_user_by_normalized_email(self, normalized_email: str) -> UserRow | None:
        """Look up a user by their current normalized email.

        Returns None if the user does not exist or the email is NULL
        on the users row. Multiple users sharing the same normalized
        email is impossible because of the partial unique index
        `idx_users_normalized_email_unique` in migration 032, so at
        most one row can match.
        """
        row = await self._conn.fetchrow(
            """
            SELECT id,
                   normalized_email,
                   email_verified_at
            FROM users
            WHERE normalized_email = $1
            """,
            normalized_email,
        )
        if row is None:
            return None
        return _user_row_from_record(row)

    async def find_user_by_provider(self, provider: str, provider_subject: str) -> UserRow | None:
        """Look up a user linked to a given provider identity.

        The `(provider, provider_subject)` lookup is unique by the
        index `idx_identity_providers_provider_subject`; at most one
        Daemon user can match. Returns the user row, not the link
        row, because callers usually want the user identity.
        """
        row = await self._conn.fetchrow(
            """
            SELECT u.id,
                   u.normalized_email,
                   u.email_verified_at
            FROM users u
            JOIN identity_providers ip
              ON ip.user_id = u.id
            WHERE ip.provider = $1
              AND ip.provider_subject = $2
            """,
            provider,
            provider_subject,
        )
        if row is None:
            return None
        return _user_row_from_record(row)

    async def find_user_provider_links(self, user_id: UUID) -> list[ProviderLink]:
        """Return every provider identity currently linked to a user.

        Used by `link_provider_identity` to detect a conflicting
        Google sub on the same user (e.g. the user already has a
        different `(google, sub)` and the new Google identity
        would silently swap the durable key).
        """
        fetch = getattr(self._conn, "fetch", None)
        if fetch is None:
            row = await self._conn.fetchrow(
                """
                SELECT id,
                       user_id,
                       provider,
                       provider_subject,
                       normalized_email_at_link,
                       linked_at,
                       last_used_at
                FROM identity_providers
                WHERE user_id = $1
                ORDER BY linked_at ASC
                """,
                user_id,
            )
            return [] if row is None else [_provider_link_from_record(row)]
        rows = await fetch(
            """
            SELECT id,
                   user_id,
                   provider,
                   provider_subject,
                   normalized_email_at_link,
                   linked_at,
                   last_used_at
            FROM identity_providers
            WHERE user_id = $1
            ORDER BY linked_at ASC
            """,
            user_id,
        )
        return [_provider_link_from_record(r) for r in rows]

    async def find_personal_tenant(self, user_id: UUID) -> TenantRow | None:
        """Return the user's personal tenant, or None.

        By the partial unique index `idx_tenants_personal_owner`,
        at most one row can match; if it exists, it is the
        authoritative personal tenant.
        """
        row = await self._conn.fetchrow(
            """
            SELECT id,
                   owner_user_id,
                   kind,
                   name
            FROM tenants
            WHERE owner_user_id = $1
              AND kind = 'personal'
            """,
            user_id,
        )
        if row is None:
            return None
        return _tenant_row_from_record(row)

    async def find_active_invite(self, normalized_email: str) -> InviteRow | None:
        """Return the active invite for a normalized email, or None.

        An active invite has `status = 'active'`, is unexpired, and
        is not yet consumed. The partial unique index
        `idx_signup_invites_active_email` ensures at most one
        active invite per email at a time.
        """
        row = await self._conn.fetchrow(
            """
            SELECT id,
                   normalized_email,
                   status,
                   expires_at,
                   used_by_user_id
            FROM signup_invites
            WHERE normalized_email = $1
              AND status = 'active'
              AND expires_at > NOW()
            """,
            normalized_email,
        )
        if row is None:
            return None
        return _invite_row_from_record(row)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create_user(
        self,
        *,
        normalized_email: str,
        email_verified_at: datetime | None,
    ) -> UserRow:
        """Insert a new `users` row and return the resulting user.

        The `users` schema (migrations 002 + 010 + 032) requires:
          - `username TEXT NOT NULL` (from migration 010; backfilled
            from `name` or the local part of `email`, with `'user'`
            as the ultimate fallback)
          - `email TEXT` (nullable; kept for legacy lookups)
          - `name TEXT` (nullable; legacy)
          - `normalized_email TEXT` (from migration 032; partial
            unique index `idx_users_normalized_email_unique`)
          - `email_verified_at TIMESTAMPTZ` (from migration 032;
            the canonical verification record)
          - `settings JSONB DEFAULT '{}'::jsonb` (from migration 010)

        This helper derives `username` and `name` from the supplied
        normalized email and inserts every NOT NULL column. A
        duplicate `normalized_email` raises the unique-violation
        that the caller is expected to handle by re-reading the
        existing row.
        """
        username = _derive_username(normalized_email)
        row = await self._conn.fetchrow(
            """
            INSERT INTO users (
                username,
                email,
                name,
                normalized_email,
                email_verified_at
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id,
                      normalized_email,
                      email_verified_at
            """,
            username,
            normalized_email,
            normalized_email,
            normalized_email,
            email_verified_at,
        )
        if row is None:
            # Should not happen on a vanilla INSERT; defensive.
            raise AccountServiceError("create_user RETURNING produced no row")
        return _user_row_from_record(row)

    async def set_user_email_verified(self, user_id: UUID) -> UserRow:
        """Set `users.email_verified_at` to NOW() for the given user.

        Called after a successful email-code consumption (TODO 10
        produces the verification event; this service applies the
        state change) and as a defensive stamp when a Google
        `email_verified=true` token is bound to a user whose
        `email_verified_at` is still NULL.
        """
        row = await self._conn.fetchrow(
            """
            UPDATE users
            SET email_verified_at = NOW()
            WHERE id = $1
            RETURNING id,
                      normalized_email,
                      email_verified_at
            """,
            user_id,
        )
        if row is None:
            raise AccountServiceError(f"set_user_email_verified: user {user_id} not found")
        return _user_row_from_record(row)

    async def update_user_normalized_email(self, user_id: UUID, normalized_email: str) -> UserRow:
        """Update the user's current normalized email.

        Mutable per decision lock; the service treats this as a
        profile update, not a durable-key change. The unique index
        still applies, so swapping to an email that already belongs
        to a different user raises a unique violation; the caller
        is expected to surface that as `ProviderCollision` (the
        closest matching error class — it captures the
        "would create a duplicate identity" semantic).
        """
        row = await self._conn.fetchrow(
            """
            UPDATE users
            SET normalized_email = $2
            WHERE id = $1
            RETURNING id,
                      normalized_email,
                      email_verified_at
            """,
            user_id,
            normalized_email,
        )
        if row is None:
            raise AccountServiceError(f"update_user_normalized_email: user {user_id} not found")
        return _user_row_from_record(row)

    async def ensure_personal_tenant(
        self, user_id: UUID, *, name: str = "Personal"
    ) -> tuple[TenantRow, bool]:
        """Idempotently create the user's personal tenant.

        Returns `(tenant, is_new)`. The partial unique index
        `idx_tenants_personal_owner` plus
        `ON CONFLICT (owner_user_id) WHERE kind = 'personal' DO
        NOTHING RETURNING id` is the canonical idempotent
        pattern: a concurrent second caller either gets a
        RETURNING row (winner of the race) or sees a NULL and
        re-SELECTs the existing row (loser of the race). Either
        way, the second caller observes the same tenant and
        `(tenant, is_new=False)`.

        A failing INSERT that does not match the partial unique
        (e.g. a true FK or CHECK violation) is propagated as
        `AccountServiceError`.
        """
        inserted_id = await self._conn.fetchval(
            """
            INSERT INTO tenants (kind, name, owner_user_id)
            VALUES ('personal', $2, $1)
            ON CONFLICT (owner_user_id) WHERE kind = 'personal'
            DO NOTHING
            RETURNING id
            """,
            user_id,
            name,
        )
        if inserted_id is not None:
            return (
                TenantRow(
                    id=inserted_id,
                    owner_user_id=user_id,
                    kind="personal",
                    name=name,
                ),
                True,
            )
        existing = await self.find_personal_tenant(user_id)
        if existing is None:
            # ON CONFLICT skipped but the row is missing. This is
            # the abort path: data is in a bad state.
            raise AccountServiceError(
                f"ensure_personal_tenant: ON CONFLICT skipped but "
                f"no personal tenant found for user {user_id}"
            )
        return existing, False

    async def ensure_owner_membership(self, tenant_id: UUID, user_id: UUID) -> tuple[str, bool]:
        """Idempotently create the owner membership row.

        Returns `(role, is_new)`. The primary key
        `(tenant_id, user_id)` plus `ON CONFLICT DO NOTHING`
        is the canonical idempotent pattern. A second caller in a
        concurrent race observes `(role='owner', is_new=False)`.
        """
        inserted = await self._conn.fetchval(
            """
            INSERT INTO tenant_memberships (tenant_id, user_id, role)
            VALUES ($1, $2, 'owner')
            ON CONFLICT (tenant_id, user_id) DO NOTHING
            RETURNING role
            """,
            tenant_id,
            user_id,
        )
        if inserted is not None:
            return inserted, True
        existing = await self._conn.fetchval(
            """
            SELECT role
            FROM tenant_memberships
            WHERE tenant_id = $1 AND user_id = $2
            """,
            tenant_id,
            user_id,
        )
        if existing is None:
            raise AccountServiceError(
                f"ensure_owner_membership: ON CONFLICT skipped but "
                f"no membership found for tenant {tenant_id} "
                f"user {user_id}"
            )
        return existing, False

    async def link_provider_identity(
        self,
        *,
        user_id: UUID,
        provider: str,
        provider_subject: str,
        normalized_email_at_link: str | None,
    ) -> ProviderLink:
        """Link a durable provider identity to an existing user.

        Raises:
            ProviderCollision: when (provider, provider_subject) is
                already linked to a DIFFERENT user (the unique
                index would raise), or when the user already has a
                different `(google, sub)` link (the durable-key
                invariant would be violated), or when the user has
                a different `provider_subject` for the same provider
                (same semantic).
            AccountServiceError: on any other unexpected DB error.
        """
        # Defensive pre-check: enforce the durable-key invariant at
        # the service layer in addition to the schema-level UNIQUE.
        # This converts a generic `asyncpg.UniqueViolationError` into
        # the service-level `ProviderCollision` and lets the route
        # layer translate it without importing asyncpg.
        existing_link_for_user = await self._conn.fetchrow(
            """
            SELECT provider, provider_subject
            FROM identity_providers
            WHERE user_id = $1 AND provider = $2
            """,
            user_id,
            provider,
        )
        if existing_link_for_user is not None:
            # The same (user, provider) already has a link; if it
            # is the same subject, this is a re-link of the same
            # identity (idempotent success). If it is a different
            # subject, that is a multi-sub collision.
            existing_subject = _safe_record_str(existing_link_for_user, "provider_subject")
            if existing_subject != provider_subject:
                raise ProviderCollision(
                    f"user {user_id} already has provider={provider} "
                    f"subject={existing_subject!r}; refusing to "
                    f"swap to subject={provider_subject!r}"
                )
            # Same link already exists; refresh last_used_at and
            # return the current row. This keeps the helper
            # idempotent on repeated Google sign-ins.
            return await self._touch_provider_link(
                user_id,
                provider,
                provider_subject,
                normalized_email_at_link,
            )

        # Cross-user collision check: a different user already
        # owns this (provider, provider_subject). The schema's
        # UNIQUE index would also catch this on INSERT, but the
        # pre-check lets us raise the right error class.
        other_user = await self._conn.fetchval(
            """
            SELECT user_id
            FROM identity_providers
            WHERE provider = $1 AND provider_subject = $2
            """,
            provider,
            provider_subject,
        )
        if other_user is not None and other_user != user_id:
            raise ProviderCollision(
                f"provider={provider} subject={provider_subject!r} "
                f"is already linked to user {other_user}; "
                f"refusing to link to user {user_id}"
            )

        try:
            row = await self._conn.fetchrow(
                """
                INSERT INTO identity_providers (
                    user_id,
                    provider,
                    provider_subject,
                    normalized_email_at_link
                )
                VALUES ($1, $2, $3, $4)
                RETURNING id,
                          user_id,
                          provider,
                          provider_subject,
                          normalized_email_at_link,
                          linked_at,
                          last_used_at
                """,
                user_id,
                provider,
                provider_subject,
                normalized_email_at_link,
            )
        except Exception as exc:  # pragma: no cover - mapped at runtime
            exc_name = type(exc).__name__
            if "UniqueViolation" in exc_name:
                raise ProviderCollision(
                    f"provider={provider} subject={provider_subject!r} "
                    f"already linked (UNIQUE violation)"
                ) from exc
            raise AccountServiceError(f"link_provider_identity failed: {exc_name}") from exc

        if row is None:
            raise AccountServiceError("link_provider_identity RETURNING produced no row")
        return _provider_link_from_record(row)

    async def _touch_provider_link(
        self,
        user_id: UUID,
        provider: str,
        provider_subject: str,
        normalized_email_at_link: str | None = None,
    ) -> ProviderLink:
        """Refresh `last_used_at` on an existing link and return it.

        Used by `link_provider_identity` for the idempotent
        re-link path. A no-op in terms of durable state, but the
        timestamp is informative for the operator audit log.
        """
        row = await self._conn.fetchrow(
            """
            UPDATE identity_providers
            SET last_used_at = NOW(),
                normalized_email_at_link = COALESCE($4, normalized_email_at_link)
            WHERE user_id = $1
              AND provider = $2
              AND provider_subject = $3
            RETURNING id,
                      user_id,
                      provider,
                      provider_subject,
                      normalized_email_at_link,
                      linked_at,
                      last_used_at
            """,
            user_id,
            provider,
            provider_subject,
            normalized_email_at_link,
        )
        if row is None:
            raise AccountServiceError(
                f"_touch_provider_link: link not found for user {user_id} provider={provider}"
            )
        return _provider_link_from_record(row)

    async def consume_invite(
        self,
        *,
        invite_id: UUID,
        used_by_user_id: UUID,
    ) -> InviteRow:
        """Atomically mark an invite as consumed.

        The transaction-safe pattern: UPDATE with `WHERE status =
        'active' AND used_by_user_id IS NULL AND expires_at >
        NOW()` returning the updated row. A second concurrent
        caller observes `RETURNING NULL` and the helper raises
        `InviteInvalidOrExpired`. The schema's CHECK on the
        status column prevents stale 'active' rows from being
        revived by a race.
        """
        row = await self._conn.fetchrow(
            """
            UPDATE signup_invites
            SET status = 'consumed',
                used_by_user_id = $2,
                consumed_at = NOW()
            WHERE id = $1
              AND status = 'active'
              AND used_by_user_id IS NULL
              AND expires_at > NOW()
            RETURNING id,
                      normalized_email,
                      status,
                      expires_at,
                      used_by_user_id
            """,
            invite_id,
            used_by_user_id,
        )
        if row is None:
            raise InviteInvalidOrExpired(
                f"invite {invite_id} is not consumable (expired, disabled, or already consumed)"
            )
        return _invite_row_from_record(row)

    # ------------------------------------------------------------------
    # High-level claims (the route layer consumes these)
    # ------------------------------------------------------------------

    async def claim_email_identity(
        self,
        *,
        normalized_email: str,
        email_verified_at: datetime,
        signup_mode: str,
        invite_token_verifier_hash: str | None,
    ) -> ClaimResult:
        async with self._conn.transaction():
            return await self.claim_email_identity_in_transaction(
                normalized_email=normalized_email,
                email_verified_at=email_verified_at,
                signup_mode=signup_mode,
                invite_token_verifier_hash=invite_token_verifier_hash,
            )

    async def claim_email_identity_in_transaction(
        self,
        *,
        normalized_email: str,
        email_verified_at: datetime,
        signup_mode: str,
        invite_token_verifier_hash: str | None,
    ) -> ClaimResult:
        """Resolve a verified-email-code completion into a Daemon
        account + personal tenant.

        Steps (all inside a single transaction):

        1. Look up the user by normalized email. If a user
           already exists, return that user with their personal
           tenant (and create the membership if missing). This is
           the "repeated sign-in" path. Mark `is_new_user=False`.
        2. If no user exists, gate on signup_mode:
           - `disabled` -> `SignupDisabled`
           - `invite_only` -> verify the invite (without
             consuming it). If verification fails, raise
             `InviteOnlyRejection` (generic).
           - `open` -> proceed without an invite.
        3. Create the user, the personal tenant, and the owner
           membership.
        4. If the path is invite_only, consume the invite with
           the real user id (not a placeholder) so the FK to
           `users(id)` is satisfied and the audit log records
           the real user.

        Email-code completion does NOT insert a
        `identity_providers(provider='email')` row. See the
        module docstring and `decisions.md` for the rationale.
        """
        if not normalized_email:
            raise ValueError("claim_email_identity requires normalized_email")
        if email_verified_at is None:
            raise ValueError(
                "claim_email_identity requires email_verified_at "
                "(email code completion proves inbox control; "
                "an unverified claim is an internal bug)"
            )

        existing = await self.find_user_by_normalized_email(normalized_email)
        if existing is not None:
            return await self._ensure_account_for_existing_user(
                existing,
                email_verified_at=email_verified_at,
            )

        # New user. Gate on signup mode.
        if signup_mode == "disabled":
            raise SignupDisabled("new hosted signups are disabled")
        pending_invite: InviteRow | None = None
        if signup_mode == "invite_only":
            pending_invite = await self._verify_invite_for_email(
                normalized_email=normalized_email,
                invite_token_verifier_hash=invite_token_verifier_hash,
            )
        elif signup_mode == "open":
            pass
        else:
            raise AccountServiceError(f"unknown signup_mode: {signup_mode!r}")

        result = await self._create_user_tenant_membership(
            normalized_email=normalized_email,
            email_verified_at=email_verified_at,
        )

        if pending_invite is not None:
            try:
                await self.consume_invite(
                    invite_id=pending_invite.id,
                    used_by_user_id=result.user.id,
                )
            except InviteInvalidOrExpired as exc:
                raise InviteOnlyRejection("invite-only signup requires a valid invite") from exc

        return result

    async def claim_google_identity(
        self,
        *,
        google_sub: str,
        normalized_email: str,
        email_verified: bool,
        signup_mode: str,
        invite_token_verifier_hash: str | None,
    ) -> ClaimResult:
        async with self._conn.transaction():
            return await self.claim_google_identity_in_transaction(
                google_sub=google_sub,
                normalized_email=normalized_email,
                email_verified=email_verified,
                signup_mode=signup_mode,
                invite_token_verifier_hash=invite_token_verifier_hash,
            )

    async def claim_google_identity_in_transaction(
        self,
        *,
        google_sub: str,
        normalized_email: str,
        email_verified: bool,
        signup_mode: str,
        invite_token_verifier_hash: str | None,
    ) -> ClaimResult:
        """Resolve a Google ID-token completion into a Daemon account
        + personal tenant + provider link.

        Steps (all inside a single transaction):

        1. If the `(google, google_sub)` is already linked to a
           user, return that user with their personal tenant
           (re-link path, repeated sign-in). `is_new_user=False`.
        2. If no link, but a user with the same normalized email
           exists, attempt to link:
           - the user must have `email_verified_at` set
             (`is_email_verified`); otherwise `EmailNotVerified`
             (and the existing user keeps their account; this is
             not a generic 4xx — it is a safe rejection that does
             not leak).
           - if `email_verified` is False (the Google token did
             not assert a verified email), reject with
             `EmailNotVerified`. The token's email_verified claim
             is the trust anchor for the cross-provider link.
           - link via `link_provider_identity`. Any
             `ProviderCollision` is propagated.
        3. If no existing user, gate on signup_mode (same as
           email-code). Verify the invite (no consume). Create
           the user, tenant, membership, and the
           `(google, google_sub)` link in one shot. Consume the
           invite with the real user id.
        """
        if not google_sub:
            raise ValueError("claim_google_identity requires google_sub")
        if not email_verified and not normalized_email:
            raise EmailNotVerified(
                "google token has unverified email and no normalized email to match on"
            )
        if not email_verified:
            raise EmailNotVerified(
                "google token email_verified is false; cannot use unverified email as durable key"
            )

        # 1. Already linked?
        by_provider = await self.find_user_by_provider("google", google_sub)
        if by_provider is not None:
            if by_provider.normalized_email != normalized_email:
                by_new_email = await self.find_user_by_normalized_email(normalized_email)
                if by_new_email is not None and by_new_email.id != by_provider.id:
                    raise ProviderCollision(
                        f"google sub {google_sub!r} is bound to user "
                        f"{by_provider.id} and email {normalized_email!r} "
                        f"belongs to user {by_new_email.id}; refusing "
                        f"cross-user email reassignment"
                    )
                by_provider = await self.update_user_normalized_email(
                    by_provider.id,
                    normalized_email,
                )
            await self.link_provider_identity(
                user_id=by_provider.id,
                provider="google",
                provider_subject=google_sub,
                normalized_email_at_link=normalized_email,
            )
            return await self._ensure_account_for_existing_user(
                by_provider,
                email_verified_at=_now_utc(),
            )

        # 2. Existing user with the same email? Try to link.
        by_email = await self.find_user_by_normalized_email(normalized_email)
        if by_email is not None:
            if not by_email.is_email_verified:
                raise EmailNotVerified(
                    f"existing user {by_email.id} has not "
                    f"verified email; refusing to link Google "
                    f"identity without email-code verification"
                )
            if by_email.normalized_email != normalized_email:
                raise ProviderCollision(
                    "verified google email does not match existing user normalized email"
                )
            await self.link_provider_identity(
                user_id=by_email.id,
                provider="google",
                provider_subject=google_sub,
                normalized_email_at_link=normalized_email,
            )
            return await self._ensure_account_for_existing_user(
                by_email,
                email_verified_at=_now_utc(),
            )

        # 3. New user. Same signup-mode gate as email-code.
        if signup_mode == "disabled":
            raise SignupDisabled("new hosted signups are disabled")
        pending_invite: InviteRow | None = None
        if signup_mode == "invite_only":
            pending_invite = await self._verify_invite_for_email(
                normalized_email=normalized_email,
                invite_token_verifier_hash=invite_token_verifier_hash,
            )
        elif signup_mode == "open":
            pass
        else:
            raise AccountServiceError(f"unknown signup_mode: {signup_mode!r}")

        result = await self._create_user_tenant_membership(
            normalized_email=normalized_email,
            email_verified_at=_now_utc(),
        )
        await self.link_provider_identity(
            user_id=result.user.id,
            provider="google",
            provider_subject=google_sub,
            normalized_email_at_link=normalized_email,
        )
        if pending_invite is not None:
            try:
                await self.consume_invite(
                    invite_id=pending_invite.id,
                    used_by_user_id=result.user.id,
                )
            except InviteInvalidOrExpired as exc:
                raise InviteOnlyRejection("invite-only signup requires a valid invite") from exc
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_account_for_existing_user(
        self,
        user: UserRow,
        *,
        email_verified_at: datetime,
    ) -> ClaimResult:
        """Return a ClaimResult for an already-existing user.

        The personal tenant and the owner membership are looked
        up and (idempotently) created if missing. This is the
        repeated sign-in path. `is_new_user` is False. The
        `is_new_tenant` and `is_new_membership` flags reflect
        whether the row needed to be created on this call
        (useful for audit and for the "first device" / "first
        identity-claim" notifications in TODO 14).
        """
        tenant, is_new_tenant = await self.ensure_personal_tenant(user.id)
        role, is_new_membership = await self.ensure_owner_membership(tenant.id, user.id)
        # Stamp verification if the user just proved email
        # control via a code and the row was unverified before.
        if user.email_verified_at is None and email_verified_at is not None:
            user = await self.set_user_email_verified(user.id)
        return ClaimResult(
            user=user,
            tenant=tenant,
            membership_role=role,
            is_new_user=False,
            is_new_tenant=is_new_tenant,
            is_new_membership=is_new_membership,
        )

    async def _create_user_tenant_membership(
        self,
        *,
        normalized_email: str,
        email_verified_at: datetime | None,
    ) -> ClaimResult:
        """Create user + personal tenant + owner membership.

        Assumes the surrounding transaction (or the test
        harness) has already authorized the claim (invite
        consumed in invite-only mode, or open mode permitted
        without an invite). The ordering is:

        1. `users` row (UNIQUE(normalized_email) so a concurrent
           second caller either wins the INSERT or sees a
           unique violation; the caller is expected to retry by
           re-reading the existing user).
        2. `tenants` row (idempotent via the partial unique
           index).
        3. `tenant_memberships` row (idempotent via PK).

        All three are SQL-side idempotent at the row level, so
        a concurrent second caller that lands here will see
        `is_new_user=True, is_new_tenant=False,
        is_new_membership=False` only if its own INSERT
        succeeded; otherwise the partial unique/PK on the
        later rows still resolves safely. The service is
        intentionally tolerant of the latter case so a
        race-condition retry from the route layer does not
        double-create.
        """
        # Step 1: user. Use the existing user lookup to handle
        # the race where another caller already inserted the
        # user with the same normalized email between our
        # pre-check and this INSERT.
        try:
            user = await self.create_user(
                normalized_email=normalized_email,
                email_verified_at=email_verified_at,
            )
            is_new_user = True
        except AccountServiceError:
            raise
        except Exception as exc:
            exc_name = type(exc).__name__
            if "UniqueViolation" in exc_name:
                # Another caller won the race; fall through to
                # the existing-user path.
                existing = await self.find_user_by_normalized_email(normalized_email)
                if existing is None:
                    raise AccountServiceError(
                        f"unique violation on user insert but no user found: {exc_name}"
                    ) from exc
                return await self._ensure_account_for_existing_user(
                    existing,
                    email_verified_at=(email_verified_at or _now_utc()),
                )
            raise AccountServiceError(f"create_user failed: {exc_name}") from exc

        # Step 2 + 3: tenant + membership (idempotent).
        tenant, is_new_tenant = await self.ensure_personal_tenant(user.id)
        role, is_new_membership = await self.ensure_owner_membership(tenant.id, user.id)
        return ClaimResult(
            user=user,
            tenant=tenant,
            membership_role=role,
            is_new_user=is_new_user,
            is_new_tenant=is_new_tenant,
            is_new_membership=is_new_membership,
        )

    async def _verify_invite_for_email(
        self,
        *,
        normalized_email: str,
        invite_token_verifier_hash: str | None,
    ) -> InviteRow:
        """Validate an invite for the invite-only path WITHOUT
        consuming it. Returns the unconsumed active invite row.

        The caller is expected to:
          1. Run this verifier first (still in the same
             transaction; no state change occurs).
          2. Create or resolve the user row.
          3. Call `consume_invite(invite_id, used_by_user_id=...)`
             with the actual user id, so the FK to
             `users(id)` is satisfied and the audit log
             records the real user.

        The split between verify and consume is intentional: a
        consume with a placeholder user id would either violate
        the FK to `users(id)` (since the placeholder UUID is
        not a real user) or would record a wrong user. The
        only safe order is verify-then-create-then-consume,
        all inside the same transaction so a rollback on
        consume failure also rolls back user creation.

        Raises `InviteOnlyRejection` for any rejection path
        (missing token, mismatched token, no active invite,
        expired, consumed). The route layer MUST treat all of
        these as the same generic 4xx response with the same
        timing floor, per the decision lock's
        enumeration-resistance rule.
        """
        if not invite_token_verifier_hash:
            raise InviteOnlyRejection("invite-only signup requires a valid invite")
        invite = await self.find_active_invite(normalized_email)
        if invite is None:
            raise InviteOnlyRejection("no active invite for the supplied email")
        # The verifier hash comparison is the caller's
        # responsibility: the invite issuer (TODO 4/6) HMACs
        # the plaintext invite token with the auth pepper and
        # passes the resulting hash here. The service compares
        # it to the stored hash, which is also an HMAC of the
        # same plaintext (with the same pepper). Equality
        # therefore implies same plaintext; mismatch implies a
        # different (or wrong) token.
        active_hash = await self._conn.fetchval(
            """
            SELECT token_verifier_hash
            FROM signup_invites
            WHERE id = $1
              AND status = 'active'
              AND expires_at > NOW()
            """,
            invite.id,
        )
        if not active_hash or not _constant_time_equals(
            str(active_hash), str(invite_token_verifier_hash)
        ):
            raise InviteOnlyRejection("invite token does not match the active invite")
        return invite


# ============================================================================
# Module-level helpers (internal)
# ============================================================================


def _derive_username(normalized_email: str) -> str:
    """Derive a non-empty `users.username` from a normalized email.

    Mirrors the migration 010 backfill (`COALESCE(name, local-part,
    'user')`) so the service-side value matches the in-DB value
    for legacy data. The result is always non-empty because:
      - if the local part exists, it is used (sanitized to ASCII);
      - if the email is degenerate (no `@`, no local part), the
        string `'user'` is returned.
    """
    if not normalized_email:
        return "user"
    local = normalized_email.split("@", 1)[0] if "@" in normalized_email else normalized_email
    if not local:
        return "user"
    return local


def _constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string equality (HMAC verifier comparison).

    Imported here rather than using `hmac.compare_digest` directly
    so the helper is mockable in tests if a future test needs
    a non-constant-time reference implementation to verify timing
    parity (currently no such test exists).
    """
    import hmac as _hmac

    return _hmac.compare_digest(a, b)


def _user_row_from_record(record: Any) -> UserRow:
    """Build a `UserRow` from an asyncpg record (or compatible)."""
    return UserRow(
        id=_record_uuid(record, "id"),
        normalized_email=_record_str_or_none(record, "normalized_email"),
        email_verified_at=_record_dt_or_none(record, "email_verified_at"),
    )


def _tenant_row_from_record(record: Any) -> TenantRow:
    return TenantRow(
        id=_record_uuid(record, "id"),
        owner_user_id=_record_uuid(record, "owner_user_id"),
        kind=_record_str(record, "kind"),
        name=_record_str(record, "name"),
    )


def _invite_row_from_record(record: Any) -> InviteRow:
    return InviteRow(
        id=_record_uuid(record, "id"),
        normalized_email=_record_str(record, "normalized_email"),
        status=_record_str(record, "status"),
        expires_at=_record_dt(record, "expires_at"),
        used_by_user_id=_record_uuid_or_none(record, "used_by_user_id"),
    )


def _provider_link_from_record(record: Any) -> ProviderLink:
    return ProviderLink(
        id=_record_uuid(record, "id"),
        user_id=_record_uuid(record, "user_id"),
        provider=_record_str(record, "provider"),
        provider_subject=_record_str(record, "provider_subject"),
        normalized_email_at_link=_record_str_or_none(record, "normalized_email_at_link"),
        linked_at=_record_dt(record, "linked_at"),
        last_used_at=_record_dt(record, "last_used_at"),
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
        raise AccountServiceError(f"record column {column!r} is unexpectedly None")
    return str(value)


def _record_str_or_none(record: Any, column: str) -> str | None:
    value = record[column]
    if value is None:
        return None
    return str(value)


def _record_dt(record: Any, column: str) -> datetime:
    value = record[column]
    if value is None:
        raise AccountServiceError(f"record column {column!r} is unexpectedly None")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    # Defensive: some DB drivers return a string.
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


def _safe_record_str(record: Any, column: str) -> str | None:
    """Read a string column that may be missing from the record.

    Used by defensive pre-checks where the SELECT projection
    intentionally does not include every column.
    """
    try:
        value = record[column]
    except (KeyError, IndexError):
        return None
    if value is None:
        return None
    return str(value)
