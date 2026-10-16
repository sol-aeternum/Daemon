"""Uncalled Crawl4AI 0.9.5 service adapter; NOT a qualified browser boundary.

Local admission does not pin the remote browser's DNS, redirects or subresources.
See docs/CRAWL4AI_ADAPTER.md before considering any activation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

import httpx

from orchestrator.config import get_settings
from orchestrator.services.fetch.bounds import max_response_bytes
from orchestrator.services.fetch.extract import extract_html_title
from orchestrator.services.fetch.models import (
    FetchContentError,
    FetchPageTooLargeError,
    FetchPolicy,
    FetchResult,
)
from orchestrator.services.fetch.strategies.direct import _is_blocked_domain, _read_bounded_body
from orchestrator.tools.ssrf_guard import (
    SsrfPolicyViolation,
    SsrfUnreachable,
    SsrfViolation,
    validate_url_and_resolve_async,
)

logger = logging.getLogger(__name__)

# Client-side concurrency only: releasing a slot cannot terminate a remote job.
SEM = asyncio.Semaphore(1)
_DEADLINE_SECONDS = 30.0
_TARGET_SCHEMES = frozenset({"http", "https"})
_TARGET_PORTS = frozenset({80, 443})


def _service_endpoint(base: str) -> str:
    """Capture an operator-owned service origin, never a caller URL/config."""
    try:
        parts = urlsplit(base)
        valid = (
            parts.scheme in _TARGET_SCHEMES
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and parts.path in {"", "/"}
            and (parts.port is None or 1 <= parts.port <= 65535)
            and not any(char.isspace() or ord(char) < 32 for char in base)
            and "?" not in base
            and "#" not in base
        )
        if valid:
            return str(httpx.URL(base).join("/crawl"))
    except (ValueError, httpx.InvalidURL):
        pass
    raise ValueError("Invalid Crawl4AI service origin") from None


def _request_payload(url: str, user_agent: str) -> dict[str, object]:
    # Bare dictionaries are supported by 0.9.5 BrowserConfig.load and
    # CrawlerRunConfig.load, including their UNTRUSTED field gates. TLS and
    # sandbox knobs are NOT request-admitted fields: enforcement is server-owned.
    return {
        "urls": [url],
        "browser_config": {
            "browser_type": "chromium",
            "headless": True,
            "accept_downloads": False,
            "enable_stealth": False,
            "user_agent": user_agent,
            "verbose": False,
        },
        "crawler_config": {
            "cache_mode": "disabled",
            "stream": False,
            "max_retries": 0,
            "page_timeout": 15000,
            "wait_until": "domcontentloaded",
            "screenshot": False,
            "pdf": False,
            "capture_mhtml": False,
            "fetch_ssl_certificate": False,
            "scan_full_page": False,
            "process_iframes": False,
            "log_console": False,
            "capture_network_requests": False,
            "capture_console_messages": False,
            "verbose": False,
        },
    }


def _valid_markdown(value: object) -> bool:
    # Optional upstream output is checked, never used as Daemon's extraction.
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    required = {"raw_markdown", "markdown_with_citations", "references_markdown"}
    optional = {"fit_markdown", "fit_html"}
    return (
        required <= value.keys() <= required | optional
        and all(isinstance(value[key], str) for key in required)
        and all(value.get(key) is None or isinstance(value[key], str) for key in optional)
    )


def _remaining(deadline_at: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


class Crawl4AIStrategy:
    """Bounded client only. FetchService deliberately never constructs this."""

    def __init__(self, policy: FetchPolicy) -> None:
        self.policy = policy
        settings = get_settings()
        self._endpoint = _service_endpoint(settings.crawl4ai_url)
        self._user_agent = settings.daemon_fetch_user_agent
        # FetchPolicy is frozen but its lists aren't. Capture the admission list.
        self._blocked_domains = tuple(policy.blocked_domains)

    async def _admit(self, url: str, deadline_at: float) -> None:
        if _is_blocked_domain(url, list(self._blocked_domains)):
            raise SsrfPolicyViolation("Crawl4AI target blocked by fetch policy")
        validated = await validate_url_and_resolve_async(
            url,
            allowed_schemes=_TARGET_SCHEMES,
            allowed_ports=_TARGET_PORTS,
            timeout=_remaining(deadline_at),
        )
        if not validated.addresses:
            raise SsrfUnreachable("Crawl4AI target unavailable")

    async def fetch(self, url: str) -> FetchResult | None:
        deadline_at = time.monotonic() + _DEADLINE_SECONDS
        try:
            async with asyncio.timeout(_remaining(deadline_at)), SEM:
                await self._admit(url, deadline_at)
                async with httpx.AsyncClient(
                    timeout=_remaining(deadline_at),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        self._endpoint,
                        json=_request_payload(url, self._user_agent),
                        headers={"Accept-Encoding": "identity, gzip, deflate"},
                    ) as response:
                        # This status belongs to the service/API, NOT the page.
                        # Redirect/error bodies are not consumed, even for logs.
                        if response.status_code != 200:
                            logger.info("Crawl4AI service_http_error")
                            return None
                        body = await _read_bounded_body(response, deadline_at=deadline_at)
                data = json.loads(body)
                _remaining(deadline_at)
                return await self._decode_result(data, url, deadline_at)
        except SsrfUnreachable:
            logger.info("Crawl4AI target_unavailable")
            return None
        except (SsrfViolation, FetchContentError):
            raise
        except TimeoutError:
            logger.info("Crawl4AI client_deadline")
            return None
        except Exception:
            # Never log the URL, body, headers, raw provider error or traceback.
            logger.info("Crawl4AI service_error")
            return None

    async def _decode_result(
        self, data: object, url: str, deadline_at: float
    ) -> FetchResult | None:
        if not isinstance(data, dict) or type(data.get("success")) is not bool:
            logger.info("Crawl4AI invalid_envelope")
            return None
        if data["success"] is not True:
            logger.info("Crawl4AI service_api_error")
            return None
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 1:
            logger.info("Crawl4AI invalid_results")
            return None
        item = results[0]
        if not isinstance(item, dict) or type(item.get("success")) is not bool:
            logger.info("Crawl4AI invalid_result")
            return None
        if not isinstance(item.get("url"), str) or item["url"] != url:
            logger.info("Crawl4AI result_identity_mismatch")
            return None
        headers = item.get("response_headers")
        if headers is not None and (
            not isinstance(headers, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
        ):
            logger.info("Crawl4AI invalid_target_headers")
            return None
        # Explicit protocol evidence only, never generic 403/body heuristics or
        # the upstream detector's broad "Blocked by anti-bot" error message.
        if headers and any(
            k.lower() == "cf-mitigated" and v.strip().lower() == "challenge"
            for k, v in headers.items()
        ):
            logger.info("Crawl4AI challenge_evidence")
            return None
        if item["success"] is not True:
            logger.info("Crawl4AI target_result_error")
            return None
        status = item.get("status_code")
        final_status = item.get("redirected_status_code")
        final_url = item.get("redirected_url")
        # 0.9.5 exposes the FIRST HTTP hop as status_code and the navigation
        # response as redirected_status_code. Do not reject valid HTTP redirects
        # or accept a failed final page merely because the first hop was 200.
        if (
            type(status) is not int
            or (final_status is not None and type(final_status) is not int)
            or (final_status is not None and not 200 <= final_status < 300)
            or (final_url is not None and final_url != url and final_status is None)
            or (
                not 200 <= status < 300
                and not (
                    status in {301, 302, 303, 307, 308}
                    and final_status is not None
                    and isinstance(final_url, str)
                    and bool(final_url)
                )
            )
        ):
            logger.info("Crawl4AI target_http_error")
            return None
        if not _valid_markdown(item.get("markdown")):
            logger.info("Crawl4AI invalid_markdown")
            return None
        html = item.get("html")
        if not isinstance(html, str) or not html:
            logger.info("Crawl4AI invalid_html")
            return None
        if len(html.encode("utf-8")) > max_response_bytes():
            raise FetchPageTooLargeError()
        if final_url is not None:
            if not isinstance(final_url, str) or not final_url:
                logger.info("Crawl4AI invalid_redirect")
                return None
            await self._admit(final_url, deadline_at)
        if not self.policy.content_is_valid(html, "text/html"):
            return None
        title = extract_html_title(html) or ""
        _remaining(deadline_at)
        return FetchResult(
            url=url,
            content=html,
            title=title,
            strategy_used="crawl4ai",
            cached=False,
            fetch_time_ms=0.0,
            content_length=len(html),
            source_url=url,
            final_url=final_url or url,
            content_type="text/html",
        )
