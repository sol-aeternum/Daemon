"""Data models for the fetch service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.config import get_settings


@dataclass
class FetchResult:
    """Result of a fetch operation."""

    url: str
    content: str
    title: str
    strategy_used: str
    cached: bool
    fetch_time_ms: float
    content_length: int


class FetchPolicy(BaseModel):
    """Policy configuration for fetch operations."""

    model_config = ConfigDict(frozen=True)

    blocked_domains: list[str] = Field(
        default_factory=list,
        description="List of domains that are blocked from fetching",
    )

    allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "text/plain",
            "application/json",
            "application/xml",
            "text/xml",
        ],
        description="List of allowed content types for fetching",
    )

    max_depth: int = Field(
        default=3, ge=1, le=10, description="Maximum depth for recursive fetching"
    )

    min_content_length: int = Field(
        default=100, ge=0, description="Minimum content length in bytes"
    )

    error_signatures: list[str] = Field(
        default_factory=lambda: [
            "404",
            "403",
            "500",
            "502",
            "503",
            "access denied",
            "not found",
            "error",
        ],
        description="Signatures that indicate an error response",
    )

    error_signature_max_length: int = Field(
        default=1000,
        ge=100,
        description="Maximum content length to check for error signatures",
    )

    @field_validator("blocked_domains", "allowed_content_types", "error_signatures")
    @classmethod
    def validate_non_empty_strings(cls, v: list[str]) -> list[str]:
        return [item for item in v if item]

    def content_is_valid(self, content: str, content_type: str | None = None) -> bool:
        # Check content length
        if len(content) < self.min_content_length:
            return False

        # Check content type if provided
        if content_type and self.allowed_content_types:
            if not any(allowed_type in content_type for allowed_type in self.allowed_content_types):
                return False

        # Check for error signatures in short content
        if len(content) <= self.error_signature_max_length:
            content_lower = content.lower()
            for signature in self.error_signatures:
                if signature.lower() in content_lower:
                    return False

        return True


def load_policy_from_env() -> FetchPolicy:
    """Build a `FetchPolicy` from the canonical `Settings` fields.

    All `FETCH_*` environment variables were migrated to `Settings` fields
    on `orchestrator.config.Settings` (the same fields any caller using
    `Depends(get_settings)` sees). This function exists for the
    service-level bootstrap path (`FetchService.__init__` falls back to it
    when no explicit policy is supplied); the values are now equivalent to
    reading `get_settings()` directly.
    """
    settings = get_settings()

    blocked_domains = [
        domain.strip() for domain in settings.fetch_blocked_domains.split(",") if domain.strip()
    ]
    allowed_content_types = [
        ct.strip() for ct in settings.fetch_allowed_content_types.split(",") if ct.strip()
    ]
    max_depth = settings.fetch_max_depth
    min_content_length = settings.fetch_min_content_length
    error_signatures = [
        sig.strip() for sig in settings.fetch_error_signatures.split(",") if sig.strip()
    ]

    # Build kwargs for FetchPolicy, only including set values so the
    # caller-visible defaults (e.g. FetchPolicy.allowed_content_types'
    # HTML/JSON allowlist) survive when the operator has not configured
    # an override via env / .env.
    kwargs: dict[str, Any] = {}
    if settings.fetch_blocked_domains.strip():
        kwargs["blocked_domains"] = blocked_domains
    if settings.fetch_allowed_content_types.strip():
        kwargs["allowed_content_types"] = allowed_content_types
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    if min_content_length is not None:
        kwargs["min_content_length"] = min_content_length
    if settings.fetch_error_signatures.strip():
        kwargs["error_signatures"] = error_signatures

    return FetchPolicy(**kwargs)
