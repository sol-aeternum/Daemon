"""Identity services for hosted identity routes.

Reusable primitives that back the hosted identity endpoints
(/v1/auth/email/*, /v1/auth/google/*, /v1/auth/setup,
/v1/auth/enroll/*, /v1/auth/refresh). The rate limiter (TODO 7),
the account/tenant/invite/provider service (TODO 8), and the
identity-aware device-session issuance helper (TODO 9) are the
three services implemented today; future TODOs add email/Google
challenge storage here. The TODO 9 helper is a backend-only
boundary — no HTTP route is added at this TODO because identity
session issuance MUST be gated on a verified identity proof
(email code consumption or Google ID-token verification), and
those routes are TODO 11 and TODO 13.
"""

from __future__ import annotations

from orchestrator.services.identity.rate_limiter import (
    RateLimitDecision,
    RateLimiter,
    RateLimitPolicy,
    RateLimitUnavailableError,
    hash_key_material,
)
from orchestrator.services.identity.rate_limit_dep import (
    ScopeKind,
    client_ip_for_key,
    enforce_rate_limit,
    get_rate_limiter,
)
from orchestrator.services.identity.account_service import (
    AccountService,
    AccountServiceError,
    ClaimResult,
    EmailNotVerified,
    InviteInvalidOrExpired,
    InviteOnlyRejection,
    ProviderCollision,
    ProviderLink,
    SignupDisabled,
    SupportsIdentityQueries,
    TenantRow,
    UserRow,
    normalize_email,
)
from orchestrator.services.identity.session_issuance import (
    DEFAULT_PRIVATE_REFRESH_TTL_DAYS,
    DEFAULT_TEMPORARY_REFRESH_TTL_SECONDS,
    DevicePersistence,
    InvalidClientKind,
    InvalidDevicePersistence,
    IssueSessionRequest,
    IssuedSession,
    SessionIssuanceError,
    SessionRefreshTransport,
    SupportsSessionIssuanceQueries,
    TEMPORARY_DB_FALLBACK_TTL_SECONDS,
    issue_device_session,
)

__all__ = [
    "AccountService",
    "AccountServiceError",
    "ClaimResult",
    "DEFAULT_PRIVATE_REFRESH_TTL_DAYS",
    "DEFAULT_TEMPORARY_REFRESH_TTL_SECONDS",
    "DevicePersistence",
    "EmailNotVerified",
    "InvalidClientKind",
    "InvalidDevicePersistence",
    "InviteInvalidOrExpired",
    "InviteOnlyRejection",
    "IssueSessionRequest",
    "IssuedSession",
    "ProviderCollision",
    "ProviderLink",
    "RateLimitDecision",
    "RateLimitPolicy",
    "RateLimiter",
    "RateLimitUnavailableError",
    "ScopeKind",
    "SessionIssuanceError",
    "SessionRefreshTransport",
    "SignupDisabled",
    "SupportsIdentityQueries",
    "SupportsSessionIssuanceQueries",
    "TEMPORARY_DB_FALLBACK_TTL_SECONDS",
    "TenantRow",
    "UserRow",
    "client_ip_for_key",
    "enforce_rate_limit",
    "get_rate_limiter",
    "hash_key_material",
    "issue_device_session",
    "normalize_email",
]
