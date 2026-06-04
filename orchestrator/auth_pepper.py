"""Auth pepper validation and policy.

Architecture decisions followed:
  - Decision 10: DAEMON_AUTH_PEPPER is mandatory in production

Pepper policy:
  - Production: requires ≥32 random bytes (≥43 base64url chars). Missing/weak = fails startup.
  - Development: if absent, generates process-ephemeral pepper with warning.
"""

from __future__ import annotations

import logging
import secrets

from orchestrator.config import Settings


logger = logging.getLogger(__name__)

MIN_PEPPER_BYTES = 32
MIN_PEPPER_CHARS = 43

_development_pepper_cache: str | None = None


class PepperValidationError(Exception):
    pass


def validate_and_get_pepper(settings: Settings) -> str:
    global _development_pepper_cache

    environment = settings.daemon_environment.lower().strip()

    if environment not in ("production", "development"):
        raise PepperValidationError(
            f"daemon_environment must be 'production' or 'development', got: {settings.daemon_environment!r}"
        )

    pepper = settings.daemon_auth_pepper

    if environment == "production":
        if not pepper:
            raise PepperValidationError(
                "daemon_auth_pepper is required in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if len(pepper) < MIN_PEPPER_CHARS:
            raise PepperValidationError(
                f"daemon_auth_pepper is too weak in production. "
                f"Got {len(pepper)} chars, need at least {MIN_PEPPER_CHARS}. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return pepper

    if not pepper:
        if _development_pepper_cache is None:
            _development_pepper_cache = secrets.token_urlsafe(32)
            logger.warning(
                "daemon_auth_pepper not set in development. "
                "Using process-ephemeral pepper. "
                "Pending enrollments created with this pepper will become invalid after restart."
            )
        return _development_pepper_cache

    return pepper


def is_production_environment(settings: Settings) -> bool:
    return settings.daemon_environment.lower().strip() == "production"


def is_development_environment(settings: Settings) -> bool:
    return settings.daemon_environment.lower().strip() == "development"
