"""Archive.org Wayback Machine fetch strategy implementation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import cast
from urllib.parse import urlparse, urlunparse

import httpx

from orchestrator.services.fetch.extract import html_to_markdown
from orchestrator.services.fetch.models import FetchResult, FetchPolicy
from orchestrator.tools.ssrf_guard import SsrfViolation, socket_guard, validate_url

logger = logging.getLogger(__name__)


# Wayback Machine hosts that legitimately serve snapshot content. The
# availability API sometimes returns ``closest.url`` in legacy ``http://``
# form even though the canonical URL is ``https://``. We upgrade the
# scheme→https and host→web.archive.org (collapsing ``www.web.archive.org``
# to its apex) before the SSRF pre-flight so legitimate snapshot URLs are
# not falsely rejected by the https-only ``ALLOWED_SCHEMES`` policy. The
# upgrade is host-scoped — it cannot widen the SSRF policy for any other
# host.
_LEGACY_WAYBACK_HOSTS = frozenset({"web.archive.org", "www.web.archive.org"})


def _upgrade_legacy_wayback_url(url: str) -> str:
    """Return ``url`` with the Wayback host normalised to ``https://web.archive.org``.

    Pass-through for any URL whose host is not a known Wayback host — the
    SSRF pre-flight is the authoritative gate, not this helper.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.hostname is None or parsed.hostname.lower() not in _LEGACY_WAYBACK_HOSTS:
        return url
    host = "web.archive.org"
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(scheme="https", netloc=netloc))


class ArchiveOrgStrategy:
    """Archive.org Wayback Machine fetch strategy."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy: FetchPolicy = policy

    async def fetch(self, url: str) -> FetchResult | None:
        """
        Fetch content from Archive.org Wayback Machine.

        Args:
            url: URL to fetch from archive

        Returns:
            FetchResult with archived content or None if no suitable snapshot found
        """
        try:
            # Check Wayback availability API. This client honours
            # ``HTTPS_PROXY``/``ALL_PROXY`` (i.e. ``trust_env=True``, the
            # ``httpx`` default) so a deployment that needs an operator
            # proxy for outbound internet access — whether for compliance
            # routing or for outbound firewall reasons — can still reach
            # the fixed, trusted ``availability_url``. The
            # ``archive.org/wayback/available`` URL itself is a hard-coded
            # configuration value (not user-controlled) and so does not
            # need the SSRF guard; the second-hop ``closest.url`` it
            # returns, by contrast, IS attacker-influenceable through
            # response poisoning and is fetched through a separate client
            # below with ``trust_env=False``.
            availability_url = f"https://archive.org/wayback/available?url={url}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(availability_url)
                _ = response.raise_for_status()

                data: dict[str, object] = cast(dict[str, object], response.json())

                # Check if snapshot exists
                snapshots: dict[str, object] = cast(
                    dict[str, object], data.get("archived_snapshots", {})
                )
                closest: dict[str, object] = cast(dict[str, object], snapshots.get("closest", {}))

                if not closest or not cast(bool, closest.get("available", False)):
                    logger.debug(f"No archive snapshot available for {url}")
                    return None

                # Check snapshot timestamp (must be within 90 days)
                timestamp_str: str = cast(str, closest.get("timestamp", ""))
                if len(timestamp_str) >= 14:
                    try:
                        # Parse timestamp format: YYYYMMDDHHMMSS
                        snapshot_time = datetime(
                            year=int(timestamp_str[0:4]),
                            month=int(timestamp_str[4:6]),
                            day=int(timestamp_str[6:8]),
                            hour=int(timestamp_str[8:10]),
                            minute=int(timestamp_str[10:12]),
                            second=int(timestamp_str[12:14]),
                            tzinfo=timezone.utc,
                        )

                        # Check if snapshot is within 90 days
                        if datetime.now(timezone.utc) - snapshot_time > timedelta(days=90):
                            logger.debug(f"Archive snapshot too old for {url}")
                            return None
                    except (ValueError, IndexError):
                        logger.warning(f"Invalid timestamp format for {url}: {timestamp_str}")
                        return None
                else:
                    logger.warning(f"Invalid timestamp for {url}: {timestamp_str}")
                    return None

                # Fetch archived HTML
                archive_url: str = cast(str, closest.get("url", ""))
                if not archive_url:
                    logger.warning(f"No archive URL in response for {url}")
                    return None

                # SSRF guard the second-hop URL before fetching. archive.org
                # is trusted as a query source but the ``closest.url`` field
                # it returns is attacker-influenceable through poisoning of
                # the JSON response (e.g. a Jina-style proxy that forwards
                # unverified upstream data, or a compromised intermediate
                # cache). Mirror the SSRF contract documented in
                # ``orchestrator/tools/ssrf_guard.py``: a pre-flight
                # ``validate_url`` rejects disallowed schemes, ports,
                # userinfo, and resolved IPs, and ``socket_guard`` wraps the
                # actual HTTP call to re-validate at connect time (so a
                # DNS-rebinding response between pre-flight and connect
                # cannot bypass the policy).
                #
                # Wayback availability commonly returns ``closest.url`` in
                # ``http://web.archive.org/...`` form even though the
                # ``https://`` form is canonical. Upgrade scheme→https and
                # host→web.archive.org before the SSRF pre-flight so a
                # legitimate legacy URL is not falsely rejected by the
                # https-only ``ALLOWED_SCHEMES`` policy. The upgrade is
                # host-scoped to ``web.archive.org`` (and the explicit
                # ``www.web.archive.org`` alias) so it cannot widen the
                # policy for attacker-controlled hosts.
                archive_url = _upgrade_legacy_wayback_url(archive_url)
                try:
                    validated_url = await asyncio.wait_for(
                        asyncio.to_thread(validate_url, archive_url), timeout=10.0
                    )
                except SsrfViolation as exc:
                    logger.warning(
                        "Archive snapshot URL %s violates SSRF policy: %s; refusing to fetch",
                        archive_url,
                        exc,
                    )
                    raise

                # ``socket_guard`` is intentionally process-global for the
                # duration of the awaited request — that's the design: every
                # DNS lookup during the second-hop HTTP call (initial
                # validation, connect-time resolution, retries) must be
                # forced through the public-IP policy or the rebinding
                # window between pre-flight and connect opens. The guard is
                # reference-counted under a lock so concurrent callers are
                # safe, but overlapping coroutines that issue unrelated DNS
                # lookups during this window will inherit the policy. That
                # is acceptable because the SSRF policy is a
                # whole-process invariant — unrelated callers that needed
                # private-IP resolution would be a separate configuration
                # bug, not a side effect of this strategy.
                #
                # The guarded fetch uses a SEPARATE ``httpx.AsyncClient``
                # with ``trust_env=False`` so ``HTTPS_PROXY`` / ``ALL_PROXY``
                # cannot route the SSRF-guarded request through an
                # operator proxy that resolves only the public hostname
                # but forwards traffic elsewhere (defeating the
                # connect-time DNS guard). The availability lookup above
                # uses the default ``trust_env=True`` client so a
                # deployment that needs an operator proxy for outbound
                # internet access can still reach the hard-coded
                # ``https://archive.org/wayback/available`` URL.
                with socket_guard():
                    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as guarded_client:
                        html_response = await guarded_client.get(validated_url)
                _ = html_response.raise_for_status()

                html_content = html_response.text
                content_type: str = html_response.headers.get("content-type", "") or ""

                # Validate HTML content
                if not self.policy.content_is_valid(html_content, content_type):
                    logger.debug(f"Archived content validation failed for {url}")
                    return None

                # Convert HTML to markdown
                markdown_content = html_to_markdown(html_content)
                if not markdown_content:
                    logger.warning(f"HTML to markdown conversion failed for {url}")
                    return None

                return FetchResult(
                    url=url,
                    content=markdown_content,
                    title="",  # Will be populated by caller
                    strategy_used="archive",
                    cached=False,
                    fetch_time_ms=0.0,  # Will be populated by caller
                    content_length=len(markdown_content),
                )

        except SsrfViolation:
            # SSRF violations must propagate so the strategy chain cannot
            # fall back to a strategy that bypasses policy. The inner
            # ``raise`` already logged the violation with the URL; here
            # we only re-raise after not also logging it as a generic
            # Archive.org fetch failure (which would confuse the
            # operator's audit trail).
            raise
        except Exception as e:
            logger.warning(f"Archive.org fetch failed for {url}: {e}")
            return None
