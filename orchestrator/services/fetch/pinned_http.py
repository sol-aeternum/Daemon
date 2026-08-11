"""HTTP helpers for connecting only to SSRF-validated addresses."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from orchestrator.tools.ssrf_guard import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    SsrfPolicyViolation,
    SsrfUnreachable,
    ValidatedUrl,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

_PER_ADDRESS_TIMEOUT_SECONDS = 5.0
_MAX_ADDRESS_ATTEMPTS = 4
_monotonic = time.monotonic


def encode_idna_hostname(host: str) -> str:
    """Return the ASCII IDNA form required by HTTP headers and TLS SNI."""
    if host.isascii():
        return host
    return host.encode("idna").decode("ascii")


def build_host_header(url: str) -> str:
    """Build an ASCII Host header, preserving IPv6 brackets and explicit ports."""
    parsed = urlsplit(url)
    host = encode_idna_hostname(parsed.hostname or "")
    authority_host = f"[{host}]" if ":" in host else host
    return f"{authority_host}:{parsed.port}" if parsed.port is not None else authority_host


def pin_url_to_address(url: str, address: str) -> str:
    """Replace only the request URL's hostname with an approved IP address."""
    parsed = urlsplit(url)
    address_authority = f"[{address}]" if ":" in address else address
    if parsed.port is not None:
        address_authority = f"{address_authority}:{parsed.port}"
    return urlunsplit(parsed._replace(netloc=address_authority))


def _effective_port(parsed: SplitResult) -> int:
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError as exc:
        raise SsrfPolicyViolation(f"malformed host/port in request URL: {exc}") from exc
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    raise SsrfPolicyViolation(f"unsupported request scheme {parsed.scheme!r}")


def _canonicalize_hostname(host: str) -> str:
    try:
        return host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SsrfPolicyViolation(f"hostname {host!r} is not a valid DNS name: {exc}") from exc


def _assert_validated_origin(request_url: str, validated: ValidatedUrl) -> None:
    """Ensure a request URL cannot escape the origin that was resolved.

    ``validation_url`` may intentionally be shorter than ``request_url``. Jina,
    for example, validates its fixed origin before appending an encoded user URL
    that can exceed the SSRF guard's input-length limit. Only the path/query may
    differ; scheme, hostname, and effective port must remain identical.
    """
    request = urlsplit(request_url)
    validation = urlsplit(validated.url)
    if request.username is not None or request.password is not None:
        raise SsrfPolicyViolation("request URLs containing userinfo are not allowed")
    request_host = request.hostname
    if request_host is None:
        raise SsrfPolicyViolation("request URL is missing a hostname")
    if request.scheme != validation.scheme:
        raise SsrfPolicyViolation("request URL scheme differs from its validated origin")
    if _canonicalize_hostname(request_host) != validated.host:
        raise SsrfPolicyViolation("request URL hostname differs from its validated origin")
    if _effective_port(request) != validated.port:
        raise SsrfPolicyViolation("request URL port differs from its validated origin")


def _request_headers(url: str, headers: Mapping[str, str] | None) -> dict[str, str]:
    # A caller-provided Host header must never override the logical authority.
    result = {name: value for name, value in (headers or {}).items() if name.lower() != "host"}
    result["Host"] = build_host_header(url)
    return result


async def pinned_get(
    request_url: str,
    *,
    validation_url: str | None = None,
    headers: Mapping[str, str] | None = None,
    allowed_schemes: frozenset[str] = ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
    timeout: float = 10.0,
) -> httpx.Response | None:
    """GET ``request_url`` through an IP approved by the SSRF resolver.

    The DNS result is pinned into the transport URL while the original Host
    header and TLS SNI are preserved. No process-global resolver state is
    changed, so unrelated Redis/Postgres/internal DNS lookups remain isolated.
    ``trust_env=False`` prevents a proxy from bypassing the pinned connection.

    ``validation_url`` defaults to the full request URL. Callers may supply a
    same-origin shorter URL when an encoded path can legitimately exceed the
    validator's maximum input length; ``_assert_validated_origin`` prevents
    that escape hatch from changing scheme, host, or port.
    """
    if timeout <= 0:
        return None

    deadline_at = _monotonic() + timeout
    target_to_validate = validation_url or request_url
    try:
        validated = await validate_url_and_resolve_async(
            target_to_validate,
            allowed_schemes=allowed_schemes,
            allowed_ports=allowed_ports,
            timeout=timeout,
        )
    except SsrfUnreachable as exc:
        # DNS/capacity failure means there is no approved address to connect
        # to, but it is not a policy violation. Report ordinary unavailability
        # so a strategy chain may try a different, independently validated
        # upstream (for example Archive after Jina).
        logger.info("Pinned GET could not resolve %s: %s", target_to_validate, exc)
        return None
    _assert_validated_origin(request_url, validated)

    try:
        sni_hostname = encode_idna_hostname(validated.host)
        request_headers = _request_headers(request_url, headers)
    except (ValueError, UnicodeError) as exc:
        raise SsrfPolicyViolation(f"request hostname cannot be encoded: {exc}") from exc

    addresses = validated.addresses[:_MAX_ADDRESS_ATTEMPTS]
    if len(addresses) < len(validated.addresses):
        logger.info(
            "Pinned GET capped address list at %d of %d for %s",
            len(addresses),
            len(validated.addresses),
            request_url,
        )

    last_error: Exception | None = None
    for address in addresses:
        remaining = deadline_at - _monotonic()
        if remaining <= 0:
            break
        attempt_timeout = min(_PER_ADDRESS_TIMEOUT_SECONDS, remaining)
        client_kwargs: dict[str, Any] = {
            "timeout": attempt_timeout,
            "follow_redirects": False,
            "trust_env": False,
        }
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                return await asyncio.wait_for(
                    client.get(
                        pin_url_to_address(request_url, address),
                        headers=request_headers,
                        extensions={"sni_hostname": sni_hostname},
                    ),
                    timeout=remaining,
                )
        except (httpx.RequestError, TimeoutError) as exc:
            last_error = exc
            logger.debug("Pinned GET address %s for %s failed: %s", address, request_url, exc)

    if last_error is not None:
        logger.info(
            "Pinned GET exhausted %d addresses for %s; last error: %s",
            len(addresses),
            request_url,
            last_error,
        )
    return None
