"""Identity services for hosted identity routes.

Reusable primitives that back the hosted identity endpoints
(/v1/auth/email/*, /v1/auth/google/*, /v1/auth/setup,
/v1/auth/enroll/*, /v1/auth/refresh). The rate limiter (TODO 7)
and the account/tenant/invite/provider service (TODO 8) are the
two services implemented today; future TODOs add email/Google
challenge storage here.
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

__all__ = [
    "AccountService",
    "AccountServiceError",
    "ClaimResult",
    "EmailNotVerified",
    "InviteInvalidOrExpired",
    "InviteOnlyRejection",
    "ProviderCollision",
    "ProviderLink",
    "RateLimitDecision",
    "RateLimitPolicy",
    "RateLimiter",
    "RateLimitUnavailableError",
    "ScopeKind",
    "SignupDisabled",
    "SupportsIdentityQueries",
    "TenantRow",
    "UserRow",
    "client_ip_for_key",
    "enforce_rate_limit",
    "get_rate_limiter",
    "hash_key_material",
    "normalize_email",
]
