"""Data models for the fetch service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)

# Stable identifier of the fetch-side textual representation produced for
# `article` extraction (trafilatura markdown, links/tables retained) and
# for bounded text/metadata passthrough. Cache keys bind this version so
# a future extraction change cannot serve stale representations to
# snapshot offsets. Bump when extraction semantics change.
EXTRACTION_VERSION_V1: Final[str] = "web-reader-v1"

# Bounded error messages. These are the entire ``str()`` of the error so
# tool-facing error results can never leak raw exception detail, URLs, or
# response bodies. Consumers may match on the stable ``code`` attribute.
_ERROR_MESSAGES: Final[dict[str, str]] = {
    "page_too_large": ("page_too_large: content exceeds the configured maximum response size"),
    "extraction_failed": ("extraction_failed: requested extraction unavailable for this source"),
}


class FetchContentError(Exception):
    """A bounded, categorized fetch failure surfaced to tool consumers.

    Raised instead of a partial/placeholder success. ``str(exc)`` is a
    single fixed, secret-free message for the tool error path; ``code``
    is a stable machine-readable category (``page_too_large`` or
    ``extraction_failed``). Programs should branch on ``code``, not on
    exception text.
    """

    code: ClassVar[str]

    def __init__(self) -> None:
        self.message = _ERROR_MESSAGES.get(self.code, "fetch_error")
        super().__init__(self.message)


class FetchPageTooLargeError(FetchContentError):
    """The fetched content exceeds a configured size bound.

    Applies both to the streamed decoded HTTP response bound and to the
    extracted UTF-8 content bound. Never carries the oversized content.
    """

    code: ClassVar[str] = "page_too_large"


class FetchExtractionError(FetchContentError):
    """The requested representation could not be produced honestly.

    Covers readable-article extraction failure and requested modes that
    this source cannot legitimately support (for example transcript on a
    non-transcript HTML page). Raw HTML is never injected as a fallback.
    """

    code: ClassVar[str] = "extraction_failed"


def error_code(exc: BaseException) -> str | None:
    """Stable error category for tool-result mapping (None for other errors)."""
    return getattr(exc, "code", None)


@dataclass
class FetchResult:
    """Result of a fetch operation.

    ``url`` remains the cache-identity form the service normalizes to
    (historical consumer contract). Provenance is carried by the optional
    fields: ``source_url`` is the original validated request URL and
    ``final_url`` the final *logical* redirect target — never a pinned
    IP transport form. ``content_type`` carries the response MIME type;
    ``extraction_version`` identifies the textual representation so
    snapshot offsets always refer to one immutable version.
    """

    url: str
    content: str
    title: str
    strategy_used: str
    cached: bool
    fetch_time_ms: float
    content_length: int
    source_url: str | None = None
    final_url: str | None = None
    content_type: str | None = None
    extraction_version: str = EXTRACTION_VERSION_V1


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
    # Preserve the legacy loader's effective default: when the env var was
    # absent it omitted this keyword and `FetchPolicy` supplied 100. The
    # Settings field's built-in 200 is only an override when configuration
    # explicitly supplied it.
    if "fetch_min_content_length" in settings.model_fields_set:
        kwargs["min_content_length"] = min_content_length
    if settings.fetch_error_signatures.strip():
        kwargs["error_signatures"] = error_signatures

    return FetchPolicy(**kwargs)
