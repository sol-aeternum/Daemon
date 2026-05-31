"""Data models for the fetch service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    blocked_domains: list[str] = []
    allowed_content_types: list[str] = []
    max_depth: int | None = None
    min_content_length: int | None = None
    error_signatures: list[str] = []

    blocked_domains_env = os.getenv("FETCH_BLOCKED_DOMAINS")
    if blocked_domains_env:
        blocked_domains = [domain.strip() for domain in blocked_domains_env.split(",")]

    allowed_content_types_env = os.getenv("FETCH_ALLOWED_CONTENT_TYPES")
    if allowed_content_types_env:
        allowed_content_types = [ct.strip() for ct in allowed_content_types_env.split(",")]

    max_depth_env = os.getenv("FETCH_MAX_DEPTH")
    if max_depth_env:
        try:
            max_depth = int(max_depth_env)
        except ValueError:
            pass

    min_content_length_env = os.getenv("FETCH_MIN_CONTENT_LENGTH")
    if min_content_length_env:
        try:
            min_content_length = int(min_content_length_env)
        except ValueError:
            pass

    error_signatures_env = os.getenv("FETCH_ERROR_SIGNATURES")
    if error_signatures_env:
        error_signatures = [sig.strip() for sig in error_signatures_env.split(",")]

    # Build kwargs for FetchPolicy, only including set values
    kwargs: dict[str, Any] = {}
    if blocked_domains:
        kwargs["blocked_domains"] = blocked_domains
    if allowed_content_types:
        kwargs["allowed_content_types"] = allowed_content_types
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    if min_content_length is not None:
        kwargs["min_content_length"] = min_content_length
    if error_signatures:
        kwargs["error_signatures"] = error_signatures

    return FetchPolicy(**kwargs)
