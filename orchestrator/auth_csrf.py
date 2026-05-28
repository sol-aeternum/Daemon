"""CSRF and origin guard for cookie-backed endpoints.

Architecture decisions followed:
  - Decision 18: Cookie-backed endpoints require CSRF/origin checks

CSRF guard logic:
  1. Reject Sec-Fetch-Site: cross-site
  2. Allow same-origin and user-initiated none where appropriate
  3. If Sec-Fetch-Site absent, validate Origin against allowed origins
  4. Treat Origin: null as hostile
  5. Use Referer only as a fallback origin signal
  6. Fail closed for sensitive cookie-backed requests with missing origin metadata
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OriginStatus(Enum):
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    NULL_ORIGIN = "null_origin"


@dataclass(frozen=True)
class CSRFGuardResult:
    allowed: bool
    reason: str
    status: OriginStatus


def check_csrf_origin(
    request_origin: str | None,
    sec_fetch_site: str | None,
    referer: str | None,
    allowed_origins: list[str],
    public_origin: str | None,
    has_cookie: bool,
) -> CSRFGuardResult:
    if sec_fetch_site == "cross-site":
        return CSRFGuardResult(
            allowed=False,
            reason="cross-site request rejected by Sec-Fetch-Site",
            status=OriginStatus.INVALID,
        )

    if request_origin is None:
        if sec_fetch_site == "same-origin":
            return CSRFGuardResult(
                allowed=True,
                reason="same-origin request",
                status=OriginStatus.VALID,
            )

        if sec_fetch_site == "none":
            return CSRFGuardResult(
                allowed=True,
                reason="user-initiated same-site request",
                status=OriginStatus.VALID,
            )

        if referer:
            referer_origin = _extract_origin_from_url(referer)
            if referer_origin and _origin_matches(referer_origin, allowed_origins, public_origin):
                return CSRFGuardResult(
                    allowed=True,
                    reason="origin validated via Referer fallback",
                    status=OriginStatus.VALID,
                )

        if has_cookie:
            return CSRFGuardResult(
                allowed=False,
                reason="cookie-backed request without verifiable origin signal",
                status=OriginStatus.MISSING,
            )
        else:
            return CSRFGuardResult(
                allowed=True,
                reason="no cookie present, CSRF guard not required",
                status=OriginStatus.MISSING,
            )

    if request_origin == "null":
        return CSRFGuardResult(
            allowed=False,
            reason="Origin: null is hostile",
            status=OriginStatus.NULL_ORIGIN,
        )

    if _origin_matches(request_origin, allowed_origins, public_origin):
        return CSRFGuardResult(
            allowed=True,
            reason="origin matches allowed origins",
            status=OriginStatus.VALID,
        )

    return CSRFGuardResult(
        allowed=False,
        reason="origin not in allowed list",
        status=OriginStatus.INVALID,
    )


def _extract_origin_from_url(url: str) -> str | None:
    if url.startswith("https://"):
        rest = url[8:]
        slash_idx = rest.find("/")
        if slash_idx == -1:
            return f"https://{rest}"
        return f"https://{rest[:slash_idx]}"
    if url.startswith("http://"):
        rest = url[7:]
        slash_idx = rest.find("/")
        if slash_idx == -1:
            return f"http://{rest}"
        return f"http://{rest[:slash_idx]}"
    return None


def _origin_matches(origin: str, allowed_origins: list[str], public_origin: str | None) -> bool:
    if public_origin and origin == public_origin:
        return True

    if not allowed_origins:
        return False

    for allowed in allowed_origins:
        if allowed == origin:
            return True
        if allowed.endswith("/"):
            if origin.startswith(allowed):
                return True

    return False
