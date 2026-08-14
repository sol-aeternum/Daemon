import socket
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Conditional import to allow tests to run without optional dependency
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

from orchestrator.config import Settings
from orchestrator.services.fetch import cache as fetch_cache_module
from orchestrator.services.fetch import models as fetch_models
from orchestrator.services.fetch.cache import FetchCache
from orchestrator.services.fetch.models import FetchPolicy, FetchResult
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.fetch.strategies.archive import ArchiveOrgStrategy
from orchestrator.services.fetch.strategies.crawl4ai import Crawl4AIStrategy
from orchestrator.services.fetch.strategies.direct import DirectFetchStrategy
from orchestrator.services.fetch.strategies.direct import (
    _build_cookie_header,
    _extract_cookies_for_logical_url,
)
from orchestrator.services.fetch.strategies.jina import JinaReaderStrategy
from orchestrator.services.fetch.strategies.youtube import YouTubeTranscriptStrategy
from orchestrator.services.fetch.url_extract import extract_urls
from orchestrator.tools.ssrf_guard import (
    SsrfPolicyViolation,
    SsrfUnreachable,
    SsrfViolation,
    ValidatedUrl,
)


def _validated_direct_url(
    url: str,
    *,
    host: str = "example.com",
    addresses: tuple[str, ...] = ("93.184.216.34",),
) -> ValidatedUrl:
    return ValidatedUrl(url=url, host=host, port=443, addresses=addresses)


def _mock_direct_resolution(
    url: str,
    *,
    host: str = "example.com",
    addresses: tuple[str, ...] = ("93.184.216.34",),
) -> AbstractContextManager[AsyncMock]:
    return patch(
        "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
        new_callable=AsyncMock,
        return_value=_validated_direct_url(url, host=host, addresses=addresses),
    )


@pytest.fixture
def fetch_policy():
    # Create a policy with very permissive settings for testing
    return FetchPolicy(
        min_content_length=10,
        allowed_content_types=[
            "text/html",
            "text/plain",
            "application/json",
            "application/xml",
            "text/markdown",
        ],
        error_signatures=[],
    )


@pytest.fixture
def fetch_cache():
    cache = FetchCache()
    # Create an async mock for Redis
    mock_redis = AsyncMock()
    cache.redis = mock_redis
    cache._ensure_connection = AsyncMock(return_value=True)
    return cache


@pytest.fixture
def fetch_service(fetch_policy, fetch_cache):
    service = FetchService(policy=fetch_policy, cache=fetch_cache)
    return service


def test_fetch_policy_preserves_unset_minimum_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An unset Settings field keeps the legacy FetchPolicy default of 100."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FETCH_MIN_CONTENT_LENGTH", raising=False)
    settings = Settings()
    assert "fetch_min_content_length" not in settings.model_fields_set
    monkeypatch.setattr(fetch_models, "get_settings", lambda: settings)

    policy = fetch_models.load_policy_from_env()

    assert policy.min_content_length == FetchPolicy().min_content_length == 100


def test_fetch_policy_applies_explicit_minimum_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An operator-provided env value remains an explicit policy override."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FETCH_MIN_CONTENT_LENGTH", "275")
    settings = Settings()
    assert "fetch_min_content_length" in settings.model_fields_set
    monkeypatch.setattr(fetch_models, "get_settings", lambda: settings)

    policy = fetch_models.load_policy_from_env()

    assert policy.min_content_length == 275


@pytest.mark.asyncio
async def test_fetch_cache_preserves_unset_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path, fetch_cache
) -> None:
    """An unset Settings field keeps the legacy one-hour cache lifetime."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FETCH_CACHE_TTL_SECONDS", raising=False)
    settings = Settings()
    assert "fetch_cache_ttl_seconds" not in settings.model_fields_set
    monkeypatch.setattr(fetch_cache_module, "get_settings", lambda: settings)
    result = FetchResult(
        url="https://example.com",
        content="content",
        title="Example",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1.0,
        content_length=7,
    )

    assert await fetch_cache.set(result.url, result) is True

    fetch_cache.redis.set.assert_awaited_once()
    assert fetch_cache.redis.set.await_args.kwargs["ex"] == 3600


@pytest.mark.asyncio
async def test_fetch_cache_applies_explicit_ttl_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path, fetch_cache
) -> None:
    """An operator-provided cache TTL remains an explicit override."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FETCH_CACHE_TTL_SECONDS", "7200")
    settings = Settings()
    assert "fetch_cache_ttl_seconds" in settings.model_fields_set
    monkeypatch.setattr(fetch_cache_module, "get_settings", lambda: settings)
    result = FetchResult(
        url="https://example.com",
        content="content",
        title="Example",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1.0,
        content_length=7,
    )

    assert await fetch_cache.set(result.url, result) is True

    fetch_cache.redis.set.assert_awaited_once()
    assert fetch_cache.redis.set.await_args.kwargs["ex"] == 7200


class TestDirectStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>This is a sufficiently long content for testing purposes to pass validation</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution("https://example.com"),
            patch("httpx.AsyncClient.get", return_value=mock_response),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long content" in result.content
        assert result.strategy_used == "direct"

    @pytest.mark.asyncio
    async def test_fetch_failure(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)

        with (
            _mock_direct_resolution("https://example.com"),
            patch("httpx.AsyncClient.get", side_effect=Exception("Network error")),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_redirect_to_link_local_is_blocked(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        redirect.text = "This body must not be treated as successful redirected content"
        with (
            patch(
                "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=redirect
            ) as mock_get,
            pytest.raises(SsrfViolation, match="blocked IP"),
        ):
            await strategy.fetch("https://8.8.8.8/start")

        mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nonstandard_port_is_blocked(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)

        with pytest.raises(SsrfViolation, match="port 8443 is not allowed"):
            await strategy.fetch("https://8.8.8.8:8443/private")

    @pytest.mark.asyncio
    async def test_redirect_to_policy_blocked_domain_stops_fetch(self, fetch_policy):
        blocked_policy = fetch_policy.model_copy(update={"blocked_domains": ["example.com"]})
        strategy = DirectFetchStrategy(blocked_policy)
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "https://example.com/private"}
        success = MagicMock()
        success.status_code = 200
        success.headers = {"content-type": "text/plain"}
        success.text = "This content must not be returned from a blocked redirect target"
        success.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution(
                "https://8.8.8.8/start",
                host="8.8.8.8",
                addresses=("8.8.8.8",),
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=[redirect, success],
            ) as mock_get,
            pytest.raises(SsrfViolation, match="blocked by fetch policy"),
        ):
            await strategy.fetch("https://8.8.8.8/start")

        mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_does_not_patch_process_dns(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)
        original_getaddrinfo = socket.getaddrinfo
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "text/plain"}
        response.text = "This is sufficiently long direct content for testing purposes"
        response.raise_for_status = MagicMock()

        async def assert_dns_is_unpatched(*_args, **_kwargs):
            assert socket.getaddrinfo is original_getaddrinfo
            return response

        with (
            _mock_direct_resolution(
                "https://8.8.8.8/article",
                host="8.8.8.8",
                addresses=("8.8.8.8",),
            ),
            patch("httpx.AsyncClient.get", side_effect=assert_dns_is_unpatched),
        ):
            result = await strategy.fetch("https://8.8.8.8/article")

        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_pins_connection_to_validated_ip(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "text/plain"}
        response.text = "This is sufficiently long direct content for testing purposes"
        response.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution("https://example.com/article?q=1"),
            patch(
                "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response
            ) as mock_get,
        ):
            result = await strategy.fetch("https://example.com/article?q=1")

        assert result is not None
        request_call = mock_get.await_args
        assert request_call is not None
        assert request_call.args[0] == "https://93.184.216.34/article?q=1"
        assert request_call.kwargs["headers"]["Host"] == "example.com"
        assert request_call.kwargs["extensions"]["sni_hostname"] == "example.com"

    @pytest.mark.asyncio
    async def test_redirect_to_policy_blocked_domain_with_trailing_dot_is_blocked(
        self, fetch_policy
    ):
        """DNS-equivalent trailing-dot hostnames must not bypass blocked_domains."""
        blocked_policy = fetch_policy.model_copy(update={"blocked_domains": ["example.com"]})
        strategy = DirectFetchStrategy(blocked_policy)
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "https://example.com./private"}
        success = MagicMock()
        success.status_code = 200
        success.headers = {"content-type": "text/plain"}
        success.text = "This content must not be returned from a trailing-dot bypass"
        success.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution(
                "https://8.8.8.8/start",
                host="8.8.8.8",
                addresses=("8.8.8.8",),
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=[redirect, success],
            ) as mock_get,
            pytest.raises(SsrfViolation, match="blocked by fetch policy"),
        ):
            await strategy.fetch("https://8.8.8.8/start")

        mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idn_hostname_is_idna_encoded(self, fetch_policy):
        """Unicode hostnames must be encoded to IDNA before the Host header is set."""
        strategy = DirectFetchStrategy(fetch_policy)
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "text/plain"}
        response.text = "This is sufficiently long content for IDN fetch validation"
        response.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution(
                "https://bücher.example:443/article",
                host="xn--bcher-kva.example",
            ),
            patch(
                "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response
            ) as mock_get,
        ):
            result = await strategy.fetch("https://bücher.example:443/article")

        assert result is not None
        request_call = mock_get.await_args
        assert request_call is not None
        assert request_call.kwargs["headers"]["Host"] == "xn--bcher-kva.example:443"
        assert request_call.kwargs["extensions"]["sni_hostname"] == "xn--bcher-kva.example"

    @pytest.mark.asyncio
    async def test_address_fallback_tries_next_address_when_first_unreachable(self, fetch_policy):
        """When ``addresses[0]`` raises a network error, the next address must be tried."""
        strategy = DirectFetchStrategy(fetch_policy)
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "text/plain"}
        response.text = "This is sufficiently long content reached via fallback address"
        response.raise_for_status = MagicMock()

        with (
            _mock_direct_resolution(
                "https://example.com/article",
                addresses=("2606:4700:4700::1111", "93.184.216.34"),
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=[httpx.ConnectError("IPv6 unreachable"), response],
            ) as mock_get,
        ):
            result = await strategy.fetch("https://example.com/article")

        assert result is not None
        assert mock_get.await_count == 2
        first_call = mock_get.await_args_list[0]
        second_call = mock_get.await_args_list[1]
        assert first_call.args[0] == "https://[2606:4700:4700::1111]/article"
        assert second_call.args[0] == "https://93.184.216.34/article"

    @pytest.mark.asyncio
    async def test_address_fallback_returns_none_when_all_addresses_fail(self, fetch_policy):
        """When every validated address fails to connect, the strategy returns ``None``."""
        strategy = DirectFetchStrategy(fetch_policy)

        with (
            _mock_direct_resolution(
                "https://example.com/article",
                addresses=("2606:4700:4700::1111", "2001:4860:4860::8888"),
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=[
                    httpx.ConnectError("first address unreachable"),
                    httpx.ConnectError("second address unreachable"),
                ],
            ),
        ):
            result = await strategy.fetch("https://example.com/article")

        assert result is None

    @pytest.mark.asyncio
    async def test_cookies_preserved_across_redirect_hops(self, fetch_policy):
        """P2 — Codex round-6 finding: cookies carry across redirect hops.

        Public sites commonly set a cookie on a redirect response and
        require it at the destination (session/consent/anti-bot flows).
        The fix threads a single ``httpx.Cookies()`` jar through every
        per-address client and every redirect hop so Set-Cookie responses
        drive the Cookie header of the next hop. This test fakes a
        redirect chain via ``httpx.MockTransport`` that issues
        ``Set-Cookie: session=secret`` on the first hop and asserts the
        second hop's request includes that cookie in its Cookie header.
        """
        from orchestrator.tools.ssrf_guard import ValidatedUrl

        request_log: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            request_log.append(request)
            # First hop: 302 with Set-Cookie header.
            if len(request_log) == 1:
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://example.com/dest",
                        "set-cookie": "session=secret",
                    },
                    request=request,
                )
            # Second hop: 200 OK.
            return httpx.Response(
                200,
                text="final content body",
                headers={"content-type": "text/html"},
                request=request,
            )

        transport = httpx.MockTransport(handler)

        async def validate(url, *args, **kwargs):
            return ValidatedUrl(
                url=url,
                host="example.com",
                port=443,
                addresses=("93.184.216.34",),
            )

        # Patch ``httpx.AsyncClient`` to inject our MockTransport while
        # still using the real ``cookies`` jar semantics.
        original_async_client = httpx.AsyncClient

        def patched_async_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        with (
            patch(
                "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
                new_callable=AsyncMock,
                side_effect=validate,
            ),
            patch(
                "httpx.AsyncClient",
                side_effect=patched_async_client,
            ),
        ):
            result = await DirectFetchStrategy(fetch_policy).fetch("https://example.com/start")

        assert result is not None
        assert result.content == "final content body"
        # Two HTTP calls: 302 -> 200.
        assert len(request_log) == 2
        first_request = request_log[0]
        second_request = request_log[1]
        # First hop sent no Cookie header.
        assert first_request.headers.get("cookie") is None
        # Second hop includes the cookie set on the first hop's redirect.
        cookie_header = second_request.headers.get("cookie")
        assert cookie_header is not None
        assert "session=secret" in cookie_header

    @pytest.mark.asyncio
    async def test_cookies_scoped_to_logical_host_across_addresses(self, fetch_policy) -> None:
        """P1 — Codex round-7 finding: cookies are scoped to the logical
        hostname, not the pinned-IP URL of the per-address request.

        Codex's reproduction: each per-address ``httpx.AsyncClient`` issues
        ``GET`` against a pinned-IP URL (e.g. ``http://203.0.113.1/...``),
        but the original cookie was set on the *logical* host. With the
        prior ``cookies=`` jar injection, ``httpx.Cookies`` evaluated
        cookie acceptance against the response URL host (the pinned IP),
        so:

        * a host-only cookie leaked across cross-host redirects whose
          hosts resolved to the same pinned IP;
        * a ``Domain=example.com`` cookie was rejected because the
          response URL host was the pinned IP, not ``example.com``;
        * a same-host redirect that used a different validated address
          lost host-only cookies because the jar saw a different IP
          origin.

        This test exercises the third failure mode: two same-host
        redirects whose pinned addresses differ, with a host-only cookie
        set on the first hop. The cookie must ride with the second hop
        even though the second hop's request URL is a *different* pinned
        IP for the same logical hostname.
        """
        from orchestrator.tools.ssrf_guard import ValidatedUrl

        request_log: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            request_log.append(request)
            # First hop: 302 with host-only Set-Cookie on logical host.
            if len(request_log) == 1:
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://example.com/dest",
                        "set-cookie": "session=secret; Path=/; Secure",
                    },
                    request=request,
                )
            # Second hop: 200 OK.
            return httpx.Response(
                200,
                text="final content body",
                headers={"content-type": "text/html"},
                request=request,
            )

        transport = httpx.MockTransport(handler)

        # Two different pinned IPs for the same logical hostname. The
        # The async validation mock returns a different IP per
        # invocation so each redirect hop resolves to a distinct address
        # and ``_pin_url_to_address`` rewrites the request URL host.
        pinned_addresses = iter(("93.184.216.34", "140.82.114.3"))

        async def validate(url, *args, **kwargs):
            return ValidatedUrl(
                url=url,
                host="example.com",
                port=443,
                addresses=(next(pinned_addresses),),
            )

        original_async_client = httpx.AsyncClient

        def patched_async_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_async_client(*args, **kwargs)

        with (
            patch(
                "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
                new_callable=AsyncMock,
                side_effect=validate,
            ),
            patch(
                "httpx.AsyncClient",
                side_effect=patched_async_client,
            ),
        ):
            result = await DirectFetchStrategy(fetch_policy).fetch("https://example.com/start")

        assert result is not None
        assert result.content == "final content body"
        # Two HTTP calls: 302 -> 200.
        assert len(request_log) == 2
        second_request = request_log[1]
        # The second hop's request URL must be the *other* pinned IP
        # (proving the test actually exercises the per-address pin).
        assert "93.184.216.34" not in str(second_request.url)
        assert "140.82.114.3" in str(second_request.url)
        # And the host-only cookie must ride with it because the jar is
        # scoped to ``example.com``, not the pinned-IP URL.
        cookie_header = second_request.headers.get("cookie")
        assert cookie_header is not None, (
            "host-only cookie should ride with the second hop because "
            "the jar is scoped to the logical host, not the pinned IP"
        )
        assert "session=secret" in cookie_header

    def test_idn_cookie_uses_canonical_logical_url(self) -> None:
        """Host-only cookies use the same IDNA host for extraction and sending."""
        jar = httpx.Cookies()
        response = httpx.Response(
            302,
            headers={"set-cookie": "session=secret; Path=/; Secure"},
            request=httpx.Request("GET", "https://93.184.216.34/start"),
        )

        _extract_cookies_for_logical_url(jar, response, "https://bücher.example/start")

        assert (
            _build_cookie_header(jar, "https://bücher.example/destination")
            == "Cookie: session=secret"
        )


class TestYouTubeStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = YouTubeTranscriptStrategy(fetch_policy)

        # Mock transcript data
        mock_transcript = [{"start": 0, "text": "Hello"}, {"start": 5, "text": "World"}]

        # Mock YouTubeTranscriptApi
        with patch.object(YouTubeTranscriptApi, "fetch", return_value=mock_transcript):
            result = await strategy.fetch("https://www.youtube.com/watch?v=abc123")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://www.youtube.com/watch?v=abc123"
        assert "[00:00:00] Hello" in result.content
        assert "[00:00:05] World" in result.content
        assert result.strategy_used == "youtube"

    @pytest.mark.asyncio
    async def test_fetch_invalid_url(self, fetch_policy):
        strategy = YouTubeTranscriptStrategy(fetch_policy)

        result = await strategy.fetch("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_no_transcript(self, fetch_policy):
        strategy = YouTubeTranscriptStrategy(fetch_policy)

        with patch.object(YouTubeTranscriptApi, "fetch", return_value=None):
            result = await strategy.fetch("https://www.youtube.com/watch?v=abc123")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_supports_snippet_objects(self, fetch_policy):
        strategy = YouTubeTranscriptStrategy(fetch_policy)

        @dataclass
        class Snippet:
            start: float
            text: str

        mock_transcript = [
            Snippet(start=0.0, text="Hello"),
            Snippet(start=5.0, text="World"),
        ]

        with patch.object(YouTubeTranscriptApi, "fetch", return_value=mock_transcript):
            result = await strategy.fetch("https://www.youtube.com/watch?v=abc123")

        assert result is not None
        assert "[00:00:00] Hello" in result.content
        assert "[00:00:05] World" in result.content


class TestJinaStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = JinaReaderStrategy(fetch_policy)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.text = "Title: Example\n\nURL Source: https://example.com/\n\nThis is sufficiently long content from Jina Reader for testing purposes"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.status_code = 200

        user_validation = ValidatedUrl(
            url="https://example.com",
            host="example.com",
            port=443,
            addresses=("93.184.216.34",),
        )
        with (
            patch(
                "orchestrator.services.fetch.strategies.jina.validate_url_and_resolve_async",
                new=AsyncMock(return_value=user_validation),
            ) as validate_user_url,
            patch(
                "orchestrator.services.fetch.strategies.jina.pinned_get",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long content" in result.content
        assert result.strategy_used == "jina"
        validate_user_url.assert_awaited_once_with(
            "https://example.com",
            allowed_schemes=frozenset({"http", "https"}),
            allowed_ports=frozenset({80, 443}),
            timeout=15.0,
        )

    @pytest.mark.asyncio
    async def test_fetch_failure(self, fetch_policy):
        strategy = JinaReaderStrategy(fetch_policy)

        # Mock httpx response with error status
        mock_response = MagicMock()
        mock_response.status_code = 404

        with (
            patch(
                "orchestrator.services.fetch.strategies.jina.validate_url_and_resolve_async",
                new=AsyncMock(
                    return_value=ValidatedUrl(
                        url="https://example.com",
                        host="example.com",
                        port=443,
                        addresses=("93.184.216.34",),
                    )
                ),
            ),
            patch(
                "orchestrator.services.fetch.strategies.jina.pinned_get",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_uses_jina_when_user_url_is_locally_unreachable(self, fetch_policy):
        """Local DNS failure retains the approved Jina public-URL boundary."""
        strategy = JinaReaderStrategy(fetch_policy)

        mock_response = MagicMock()
        mock_response.text = (
            "Title: Example\n\nThis is sufficiently long content returned "
            "through Jina while local target DNS is unavailable"
        )
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.status_code = 200

        with (
            patch(
                "orchestrator.services.fetch.strategies.jina.validate_url_and_resolve_async",
                new=AsyncMock(side_effect=SsrfUnreachable("DNS timed out")),
            ),
            patch(
                "orchestrator.services.fetch.strategies.jina.pinned_get",
                new=AsyncMock(return_value=mock_response),
            ) as mock_pinned_get,
        ):
            result = await strategy.fetch("https://temporarily-unresolvable.example")

        assert result is not None
        assert result.strategy_used == "jina"
        mock_pinned_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_rejects_user_url_pointing_at_link_local(self, fetch_policy):
        """An attacker-supplied URL whose host resolves to link-local
        (cloud metadata) must be rejected with SsrfViolation before any
        upstream request is issued.
        """
        from orchestrator.tools.ssrf_guard import SsrfViolation

        strategy = JinaReaderStrategy(fetch_policy)

        with (
            patch("httpx.AsyncClient.get") as mock_get,
            pytest.raises(SsrfViolation),
        ):
            await strategy.fetch("http://169.254.169.254/latest/meta-data/")

        # Confirm no upstream GET was attempted; the user URL failed the
        # pre-flight before any HTTP work was done.
        assert mock_get.call_count == 0

    @pytest.mark.asyncio
    async def test_fetch_rejects_user_url_pointing_at_rfc1918(self, fetch_policy):
        """RFC1918 destinations (10.0.0.0/8) must be rejected with
        SsrfViolation. The validator catches the literal IP before
        forwarding to Jina.
        """
        from orchestrator.tools.ssrf_guard import SsrfViolation

        strategy = JinaReaderStrategy(fetch_policy)

        with (
            patch("httpx.AsyncClient.get") as mock_get,
            pytest.raises(SsrfViolation),
        ):
            await strategy.fetch("https://10.0.0.5/internal-admin")

        assert mock_get.call_count == 0

    @pytest.mark.asyncio
    async def test_fetch_rejects_user_url_with_userinfo(self, fetch_policy):
        """URLs containing ``user:pass@`` must be rejected (userinfo can
        smuggle a real host after a parser misread).
        """
        from orchestrator.tools.ssrf_guard import SsrfViolation

        strategy = JinaReaderStrategy(fetch_policy)

        with (
            patch("httpx.AsyncClient.get") as mock_get,
            pytest.raises(SsrfViolation),
        ):
            await strategy.fetch("https://attacker:pwn@example.com/")

        assert mock_get.call_count == 0

    @pytest.mark.asyncio
    async def test_fetch_pins_upstream_without_patching_process_dns(self, fetch_policy):
        """The Jina request uses an approved IP while unrelated DNS stays local."""
        strategy = JinaReaderStrategy(fetch_policy)

        mock_response = MagicMock()
        mock_response.text = "Title: Example\n\nURL Source: https://example.com/\n\nThis is sufficiently long content from Jina Reader for testing purposes"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.status_code = 200

        observed_internal_dns: list[str] = []

        def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
            if host == "redis":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        async def fake_get(*args, **kwargs):  # type: ignore[no-untyped-def]
            assert socket.getaddrinfo is fake_getaddrinfo
            internal = socket.getaddrinfo("redis", 6379)
            observed_internal_dns.append(str(internal[0][4][0]))
            return mock_response

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=fake_get)
        mock_client_context = MagicMock()
        mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "orchestrator.services.fetch.strategies.jina.validate_url_and_resolve_async",
                new=AsyncMock(
                    return_value=ValidatedUrl(
                        url="https://example.com",
                        host="example.com",
                        port=443,
                        addresses=("93.184.216.34",),
                    )
                ),
            ),
            patch(
                "orchestrator.services.fetch.pinned_http.validate_url_and_resolve_async",
                new=AsyncMock(
                    return_value=ValidatedUrl(
                        url="https://r.jina.ai/",
                        host="r.jina.ai",
                        port=443,
                        addresses=("93.184.216.34",),
                    )
                ),
            ),
            patch(
                "orchestrator.services.fetch.pinned_http.httpx.AsyncClient",
                return_value=mock_client_context,
            ) as mock_client_class,
            patch("socket.getaddrinfo", new=fake_getaddrinfo),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert observed_internal_dns == ["10.0.0.8"]
        assert mock_client_class.call_args.kwargs["trust_env"] is False
        request_call = mock_client.get.await_args
        assert request_call is not None
        assert request_call.args[0].startswith("https://93.184.216.34/")
        assert request_call.kwargs["headers"]["Host"] == "r.jina.ai"
        assert request_call.kwargs["extensions"]["sni_hostname"] == "r.jina.ai"

    @pytest.mark.asyncio
    async def test_fetch_accepts_url_that_grows_during_encoding(self, fetch_policy):
        """A user URL that exceeds ``MAX_URL_LENGTH`` after URL-encoding
        must still be accepted: pinned transport validates only the fixed
        upstream origin, not the composed encoded URL.
        Regression for Codex P2 finding: a user URL just under
        ``MAX_URL_LENGTH`` whose query contains many ``&`` / ``=``
        characters expands past 2,048 chars after ``quote(..., safe="")``
        and a previous implementation rejected it as ``URL exceeds
        2048 characters`` even though the user URL was within the limit.
        """
        strategy = JinaReaderStrategy(fetch_policy)

        # Build a user URL whose unencoded length is < 2048 but whose
        # encoded length is > 2048. Each ``&`` becomes ``%26`` (1 → 3
        # chars). With a 23-char base plus a 2,021-char filler of 1010
        # ``a`` chars separated by 1010 ``&``s we land at 2,044
        # unencoded and 5,064 encoded — comfortably on the right side
        # of 2,048 after ``quote(..., safe="")``.
        base = "https://example.com/?q="
        filler = "&".join("a" for _ in range(1010))
        long_user_url = base + filler
        assert len(long_user_url) < 2048
        from urllib.parse import quote

        assert len(quote(long_user_url, safe="")) > 2048

        mock_response = MagicMock()
        mock_response.text = "Title: Example\n\nURL Source: https://example.com/\n\nThis is sufficiently long content from Jina Reader for testing purposes"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.status_code = 200

        with (
            patch(
                "orchestrator.services.fetch.strategies.jina.validate_url_and_resolve_async",
                new=AsyncMock(
                    return_value=ValidatedUrl(
                        url=long_user_url,
                        host="example.com",
                        port=443,
                        addresses=("93.184.216.34",),
                    )
                ),
            ),
            patch(
                "orchestrator.services.fetch.strategies.jina.pinned_get",
                new=AsyncMock(return_value=mock_response),
            ) as mock_pinned_get,
        ):
            result = await strategy.fetch(long_user_url)

        # The strategy must succeed despite the composed encoded URL
        # exceeding ``MAX_URL_LENGTH`` — only the user URL length gate
        # applies, on the user URL.
        assert result is not None
        assert result.strategy_used == "jina"
        pinned_call = mock_pinned_get.await_args
        assert pinned_call is not None
        assert len(pinned_call.args[0]) > 2048
        assert pinned_call.kwargs["validation_url"] == "https://r.jina.ai/"


class TestCrawl4AIStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = Crawl4AIStrategy(fetch_policy)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": [
                {
                    "markdown": "# Article\n\nThis is sufficiently long content from Crawl4AI for testing purposes"
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long content" in result.content
        assert result.strategy_used == "crawl4ai"

    @pytest.mark.asyncio
    async def test_fetch_no_result(self, fetch_policy):
        strategy = Crawl4AIStrategy(fetch_policy)

        # Mock httpx response with empty result
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is None


class TestArchiveOrgStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = ArchiveOrgStrategy(fetch_policy)

        # Mock availability response with recent timestamp
        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/web/20230101000000/https://example.com",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()

        # Mock archived content response with longer content
        html_content = "<html><body><p>This is sufficiently long archived content for testing purposes. This content needs to be long enough to pass validation. We need to make sure this content is long enough to satisfy the minimum content length requirements and avoid any error signatures that might be present.</p><p>Adding more content to ensure we have enough text for the validation to pass successfully.</p></body></html>"
        mock_content_response = MagicMock()
        mock_content_response.text = html_content
        mock_content_response.headers = {"content-type": "text/html"}
        mock_content_response.raise_for_status = MagicMock()

        # Mock html_to_markdown to return expected content
        markdown_content = "This is sufficiently long archived content for testing purposes. This content needs to be long enough to pass validation. We need to make sure this content is long enough to satisfy the minimum content length requirements."
        with (
            patch("httpx.AsyncClient.get", return_value=mock_availability_response),
            patch(
                "orchestrator.services.fetch.strategies.archive.pinned_get",
                new=AsyncMock(return_value=mock_content_response),
            ) as mock_pinned_get,
            patch(
                "orchestrator.services.fetch.strategies.archive.html_to_markdown",
                return_value=markdown_content,
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long archived content" in result.content
        assert result.strategy_used == "archive"
        mock_pinned_get.assert_awaited_once_with(
            "https://web.archive.org/web/20230101000000/https://example.com",
            timeout=10.0,
        )

    @pytest.mark.asyncio
    async def test_fetch_no_snapshot(self, fetch_policy):
        strategy = ArchiveOrgStrategy(fetch_policy)

        # Mock availability response with no snapshot
        mock_response = MagicMock()
        mock_response.json.return_value = {"archived_snapshots": {}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_ssrf_policy_violation_blocks_chain(self, fetch_policy):
        """An archive URL whose host resolves to a disallowed IP must be
        rejected with SsrfViolation, not silently fetched. The exception
        propagates so the strategy chain cannot fall back to a strategy
        that bypasses policy.
        """
        from orchestrator.tools.ssrf_guard import SsrfViolation

        strategy = ArchiveOrgStrategy(fetch_policy)

        # Mock availability response whose ``closest.url`` points at a
        # link-local metadata IP. The URL uses ``https://`` so the
        # pre-flight reaches the IP-range branch instead of failing
        # earlier on scheme — without ``https``, the SSRF validator
        # rejects the scheme before examining the resolved IP, and the
        # test would only verify the scheme-rejection path (already
        # covered by ``test_fetch_ssrf_non_https_url_rejected``).
        # Archive.org itself does not return such a URL, but a
        # poisoned/relayed JSON response could.
        #
        # Round-3 Codex review (P2, 2026-08-10T17:02:16Z, on
        # ``tests/test_fetch_strategies.py:298``) flagged the
        # scheme-stopped-before-IP shape and asked for the IP-range
        # branch to be exercised directly.
        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", return_value=mock_availability_response):
            with pytest.raises(SsrfViolation):
                await strategy.fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_fetch_ssrf_non_https_url_rejected(self, fetch_policy):
        """Plain http:// archive URLs are rejected by the SSRF policy
        (only ``https`` is in ALLOWED_SCHEMES). This covers a poison
        vector where the archive URL is downgraded to plaintext http
        pointing at a non-Wayback host — Wayback ``http://`` URLs are
        upgraded to ``https://web.archive.org`` before validation (see
        ``_upgrade_legacy_wayback_url``).
        """
        from orchestrator.tools.ssrf_guard import SsrfViolation

        strategy = ArchiveOrgStrategy(fetch_policy)

        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "http://evil.example.com/web/20230101000000/https://example.com",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", return_value=mock_availability_response):
            with pytest.raises(SsrfViolation):
                await strategy.fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_fetch_wayback_legacy_http_upgraded_to_https(self, fetch_policy):
        """Wayback availability commonly returns ``closest.url`` in
        ``http://web.archive.org/...`` form. The strategy upgrades the
        scheme→https before SSRF validation so a legitimate legacy URL
        is not falsely rejected by the https-only ``ALLOWED_SCHEMES``
        policy. Without the upgrade, every Archive.org snapshot would
        fail pre-flight because the Wayback API serves http.
        """
        strategy = ArchiveOrgStrategy(fetch_policy)

        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "http://web.archive.org/web/20230101000000/https://example.com",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()
        mock_content_response = MagicMock()
        mock_content_response.text = "<html><body>sufficiently long archived content for testing the wayback http→https upgrade. We need to make sure this content is long enough to pass content validation across the chain.</body></html>"
        mock_content_response.headers = {"content-type": "text/html"}
        mock_content_response.raise_for_status = MagicMock()

        with (
            patch("httpx.AsyncClient.get", return_value=mock_availability_response),
            patch(
                "orchestrator.services.fetch.strategies.archive.pinned_get",
                new=AsyncMock(return_value=mock_content_response),
            ) as mock_pinned_get,
            patch(
                "orchestrator.services.fetch.strategies.archive.html_to_markdown",
                return_value="sufficiently long archived content for testing the wayback http→https upgrade.",
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        pinned_call = mock_pinned_get.await_args
        assert pinned_call is not None
        second_hop_url = pinned_call.args[0]
        assert second_hop_url.startswith("https://web.archive.org/")

    @pytest.mark.asyncio
    async def test_fetch_uses_pinned_get_for_snapshot(self, fetch_policy):
        """The attacker-influenceable snapshot URL goes through pinned transport."""
        strategy = ArchiveOrgStrategy(fetch_policy)

        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/web/20230101000000/https://example.com",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()
        mock_content_response = MagicMock()
        mock_content_response.text = "<html><body>sufficiently long archived content for testing the socket guard wrap. We need to make sure this content is long enough to pass content validation across the chain.</body></html>"
        mock_content_response.headers = {"content-type": "text/html"}
        mock_content_response.raise_for_status = MagicMock()

        with (
            patch("httpx.AsyncClient.get", return_value=mock_availability_response),
            patch(
                "orchestrator.services.fetch.strategies.archive.pinned_get",
                new=AsyncMock(return_value=mock_content_response),
            ) as mock_pinned_get,
            patch(
                "orchestrator.services.fetch.strategies.archive.html_to_markdown",
                return_value="sufficiently long archived content for testing the pinned fetch.",
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        mock_pinned_get.assert_awaited_once_with(
            "https://web.archive.org/web/20230101000000/https://example.com",
            timeout=10.0,
        )

    @pytest.mark.asyncio
    async def test_availability_lookup_keeps_operator_proxy_while_snapshot_is_pinned(
        self, fetch_policy
    ):
        """Only the fixed availability lookup may use environment proxies."""
        strategy = ArchiveOrgStrategy(fetch_policy)

        mock_availability_response = MagicMock()
        mock_availability_response.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/web/20230101000000/https://example.com",
                    "timestamp": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
                }
            }
        }
        mock_availability_response.raise_for_status = MagicMock()
        mock_content_response = MagicMock()
        mock_content_response.text = "<html><body>sufficiently long archived content for testing the per-client trust_env split. We need to make sure this content is long enough to pass content validation across the chain.</body></html>"
        mock_content_response.headers = {"content-type": "text/html"}
        mock_content_response.raise_for_status = MagicMock()

        availability_client = MagicMock()
        availability_client.get = AsyncMock(return_value=mock_availability_response)
        availability_context = MagicMock()
        availability_context.__aenter__ = AsyncMock(return_value=availability_client)
        availability_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "orchestrator.services.fetch.strategies.archive.httpx.AsyncClient",
                return_value=availability_context,
            ) as mock_client_class,
            patch(
                "orchestrator.services.fetch.strategies.archive.pinned_get",
                new=AsyncMock(return_value=mock_content_response),
            ) as mock_pinned_get,
            patch(
                "orchestrator.services.fetch.strategies.archive.html_to_markdown",
                return_value="sufficiently long archived content for testing the per-client trust_env split.",
            ),
        ):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        mock_client_class.assert_called_once_with(timeout=10.0)
        mock_pinned_get.assert_awaited_once_with(
            "https://web.archive.org/web/20230101000000/https://example.com",
            timeout=10.0,
        )


class TestUrlExtraction:
    def test_url_extraction_accuracy(self):
        # Test http(s) URLs
        text = "Check out https://example.com and http://test.org for more info"
        result = extract_urls(text)
        assert "https://example.com" in result
        assert "http://test.org" in result

        # Test bare domains
        text = "Visit example.com and sub.test.org"
        result = extract_urls(text)
        assert "example.com" in result
        assert "sub.test.org" in result

        # Test false positives prevention
        text = "Version v1.2.3 and path /usr/bin should not be extracted"
        result = extract_urls(text)
        assert "v1.2.3" not in result
        assert "/usr/bin" not in result

        # Test email addresses are not extracted
        text = "Contact user@example.com for support"
        result = extract_urls(text)
        assert "example.com" not in result

        # Test complex text with multiple URLs
        text = """
        I've been reading about machine learning from 
        https://arxiv.org and also checking paperswithcode.com.
        Also found great stuff at example.dev and http://research.ai.
        """
        result = extract_urls(text)
        assert "https://arxiv.org" in result
        assert "paperswithcode.com" in result
        assert "example.dev" in result
        assert "http://research.ai" in result


class TestFetchService:
    @pytest.mark.asyncio
    async def test_strategy_chain_fallback(self, fetch_service):
        # Mock strategies to fail except the last one
        fetch_service.direct_strategy = MagicMock()
        fetch_service.direct_strategy.fetch = AsyncMock(return_value=None)

        fetch_service.jina_strategy = MagicMock()
        fetch_service.jina_strategy.fetch = AsyncMock(return_value=None)

        fetch_service.crawl4ai_strategy = MagicMock()
        fetch_service.crawl4ai_strategy.fetch = AsyncMock(return_value=None)

        # Last strategy succeeds
        fetch_service.archive_strategy = MagicMock()
        fetch_service.archive_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://example.com",
                content="This is sufficiently long archived content for testing purposes",
                title="",
                strategy_used="archive",
                cached=False,
                fetch_time_ms=0.0,
                content_length=100,
            )
        )

        # Mock cache to avoid real Redis calls
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        result = await fetch_service.fetch("https://example.com")

        assert result is not None
        assert result.strategy_used == "archive"
        fetch_service.direct_strategy.fetch.assert_called_once_with("https://example.com")
        fetch_service.jina_strategy.fetch.assert_called_once_with("https://example.com")
        fetch_service.crawl4ai_strategy.fetch.assert_not_called()
        fetch_service.archive_strategy.fetch.assert_called_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_ssrf_violation_stops_fallback_chain(self, fetch_service):
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.direct_strategy = MagicMock()
        fetch_service.direct_strategy.fetch = AsyncMock(
            side_effect=SsrfViolation("redirect resolved to a blocked IP")
        )
        fallback_result = FetchResult(
            url="https://8.8.8.8/start",
            content="This fallback must not run after an SSRF policy violation",
            title="",
            strategy_used="jina",
            cached=False,
            fetch_time_ms=0.0,
            content_length=60,
        )
        fetch_service.jina_strategy = MagicMock()
        fetch_service.jina_strategy.fetch = AsyncMock(return_value=fallback_result)

        result = await fetch_service.fetch("https://8.8.8.8/start")

        assert result is None
        fetch_service.direct_strategy.fetch.assert_awaited_once()
        fetch_service.jina_strategy.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_hit(self, fetch_service):
        # Mock cache to return a result
        cached_result = FetchResult(
            url="https://example.com",
            content="This is sufficiently long cached content for testing purposes",
            title="",
            strategy_used="direct",
            cached=True,
            fetch_time_ms=0.0,
            content_length=100,
        )
        fetch_service.cache.get = AsyncMock(return_value=cached_result)

        result = await fetch_service.fetch("https://example.com")

        assert result is not None
        assert result.cached is True
        assert "sufficiently long cached content" in result.content
        fetch_service.cache.get.assert_called_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_cache_miss_and_store(self, fetch_service):
        # Mock cache to return None (miss) and successfully store
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        # Mock direct strategy to succeed
        fetch_service.direct_strategy = MagicMock()
        fetch_service.direct_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://example.com",
                content="This is sufficiently long fetched content for testing purposes",
                title="",
                strategy_used="direct",
                cached=False,
                fetch_time_ms=100.0,
                content_length=100,
            )
        )

        # Mock cache methods to avoid real Redis calls
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        result = await fetch_service.fetch("https://example.com")

        assert result is not None
        assert result.cached is False
        assert "sufficiently long fetched content" in result.content
        fetch_service.cache.get.assert_called_once_with("https://example.com")
        fetch_service.cache.set.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe_url",
        [
            "https://169.254.169.254/latest/meta-data/",
            "https://100.64.0.1/private",
            "https://[::ffff:169.254.169.254]/private",
        ],
    )
    async def test_private_address_rejected_before_cache_or_strategy(
        self, fetch_service, unsafe_url
    ):
        fetch_service.cache.get = AsyncMock(return_value=None)
        strategies = [
            fetch_service.direct_strategy,
            fetch_service.jina_strategy,
            fetch_service.crawl4ai_strategy,
            fetch_service.archive_strategy,
        ]
        for strategy in strategies:
            assert strategy is not None
            strategy.fetch = AsyncMock(return_value=None)

        result = await fetch_service.fetch(unsafe_url)

        assert result is None
        fetch_service.cache.get.assert_not_called()
        for strategy in strategies:
            strategy.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_public_http_address_is_preserved(self, fetch_service):
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)
        fetch_service.direct_strategy = MagicMock()
        fetch_service.direct_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="http://8.8.8.8/article",
                content="This is sufficiently long public HTTP content for testing purposes",
                title="",
                strategy_used="direct",
                cached=False,
                fetch_time_ms=0.0,
                content_length=68,
            )
        )

        result = await fetch_service.fetch("http://8.8.8.8/article")

        assert result is not None
        assert result.strategy_used == "direct"
        fetch_service.direct_strategy.fetch.assert_awaited_once_with("http://8.8.8.8/article")

    @pytest.mark.asyncio
    async def test_blocked_domain(self, fetch_service):
        # Configure policy with blocked domain
        fetch_service.policy = FetchPolicy(blocked_domains=["blocked.com"])

        result = await fetch_service.fetch("https://blocked.com")

        assert result is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://youtube.com/watch?v=abc123",
            "https://www.youtube.com/watch?v=abc123",
            "https://m.youtube.com/watch?v=abc123",
            "https://youtube.com./watch?v=abc123",
            "https://youtu.be/abc123",
        ],
    )
    def test_youtube_host_detection_accepts_only_youtube_hosts(
        self, fetch_service, url: str
    ) -> None:
        assert fetch_service._is_youtube_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://notyoutube.com/watch?v=abc123",
            "https://youtube.com.evil.example/watch?v=abc123",
            "https://youtube.com@evil.example/watch?v=abc123",
            "https://evil.example/?next=https://youtube.com/watch?v=abc123",
            "https://subdomain.youtu.be/abc123",
        ],
    )
    def test_youtube_host_detection_rejects_substring_matches(
        self, fetch_service, url: str
    ) -> None:
        assert fetch_service._is_youtube_url(url) is False

    @pytest.mark.asyncio
    async def test_youtube_url_shortcircuit(self, fetch_service):
        # Mock YouTube strategy to succeed
        fetch_service.youtube_strategy = MagicMock()
        fetch_service.youtube_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://www.youtube.com/watch?v=abc123",
                content="[00:00:00] YouTube transcript",
                title="YouTube Transcript: abc123",
                strategy_used="youtube",
                cached=False,
                fetch_time_ms=50.0,
                content_length=100,
            )
        )

        # Mock cache to avoid real Redis calls
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        result = await fetch_service.fetch("https://www.youtube.com/watch?v=abc123")

        assert result is not None
        assert result.strategy_used == "youtube"
        # Ensure other strategies were not called (they should be None for YouTube URLs)
        assert fetch_service.direct_strategy is not None
        assert fetch_service.jina_strategy is not None
        assert fetch_service.crawl4ai_strategy is not None
        assert fetch_service.archive_strategy is not None

    @pytest.mark.asyncio
    async def test_youtube_url_shortcircuit_with_text_extract(self, fetch_service):
        fetch_service.youtube_strategy = MagicMock()
        fetch_service.youtube_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://www.youtube.com/watch?v=abc123",
                content="[00:00:00] YouTube transcript",
                title="YouTube Transcript: abc123",
                strategy_used="youtube",
                cached=False,
                fetch_time_ms=50.0,
                content_length=100,
            )
        )

        fetch_service.direct_strategy = MagicMock()
        fetch_service.direct_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.jina_strategy = MagicMock()
        fetch_service.jina_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.crawl4ai_strategy = MagicMock()
        fetch_service.crawl4ai_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.archive_strategy = MagicMock()
        fetch_service.archive_strategy.fetch = AsyncMock(return_value=None)

        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        result = await fetch_service.fetch("https://www.youtube.com/watch?v=abc123", extract="text")

        assert result is not None
        assert result.strategy_used == "youtube"
        fetch_service.youtube_strategy.fetch.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc123"
        )
        fetch_service.direct_strategy.fetch.assert_not_called()
        fetch_service.jina_strategy.fetch.assert_not_called()
        fetch_service.crawl4ai_strategy.fetch.assert_not_called()
        fetch_service.archive_strategy.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_served_without_dns_resolution(self, fetch_service):
        """A cached hit must be served even when DNS resolution fails.

        Mirrors Codex finding #4: cache lookups happen before any DNS-based
        validator runs, so a temporary DNS outage does not invalidate a
        still-valid cached entry. The static gate (``_is_supported_url``)
        still rejects obviously-unsafe URLs up front, so private IP literals
        are not served from cache.
        """
        cached_result = FetchResult(
            url="https://example.com",
            content="This is sufficiently long cached content for testing purposes",
            title="",
            strategy_used="direct",
            cached=True,
            fetch_time_ms=0.0,
            content_length=100,
        )
        fetch_service.cache.get = AsyncMock(return_value=cached_result)

        # Patch the DNS validator on the strategy module (where it is now
        # invoked) to simulate a saturated resolver; a cache hit must return
        # before this coroutine is awaited.
        from orchestrator.services.fetch.strategies import direct as _direct_module

        dns_validator = AsyncMock(side_effect=SsrfViolation("DNS validation timed out"))

        with patch.object(_direct_module, "validate_url_and_resolve_async", dns_validator):
            result = await fetch_service.fetch("https://example.com")

        assert result is not None
        assert result.cached is True
        fetch_service.cache.get.assert_awaited_once()
        dns_validator.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trailing_dot_blocked_domain_is_rejected(self, fetch_service):
        """DNS-equivalent trailing-dot hostnames must not bypass ``blocked_domains``."""
        fetch_service.policy = FetchPolicy(blocked_domains=["blocked.com"])
        strategies = [
            fetch_service.direct_strategy,
            fetch_service.jina_strategy,
            fetch_service.crawl4ai_strategy,
            fetch_service.archive_strategy,
        ]
        for strategy in strategies:
            assert strategy is not None
            strategy.fetch = AsyncMock(return_value=None)
        fetch_service.cache.get = AsyncMock(return_value=None)

        result = await fetch_service.fetch("https://blocked.com./private")

        assert result is None
        fetch_service.cache.get.assert_not_called()
        for strategy in strategies:
            strategy.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_idn_blocked_domain_is_rejected_in_both_forms(self, fetch_service):
        """IDNA-equivalent blocked domains must reject both Unicode and Punycode."""
        fetch_service.policy = FetchPolicy(blocked_domains=["bücher.example"])
        strategies = [
            fetch_service.direct_strategy,
            fetch_service.jina_strategy,
            fetch_service.crawl4ai_strategy,
            fetch_service.archive_strategy,
        ]
        for strategy in strategies:
            assert strategy is not None
            strategy.fetch = AsyncMock(return_value=None)
        fetch_service.cache.get = AsyncMock(return_value=None)

        # Unicode form
        unicode_result = await fetch_service.fetch("https://bücher.example/")
        assert unicode_result is None
        # Punycode form (DNS-equivalent of the Unicode form)
        punycode_result = await fetch_service.fetch("https://xn--bcher-kva.example/")
        assert punycode_result is None

        fetch_service.cache.get.assert_not_called()
        for strategy in strategies:
            strategy.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_punycode_blocked_domain_rejects_unicode_request(self, fetch_service):
        """Punycode-form configured domain must reject a Unicode request.

        Mirrors Codex finding #1 — without IDNA canonicalization in both
        directions, ``bücher.example`` in the request escapes a
        ``xn--bcher-kva.example`` blocked-domain entry.
        """
        fetch_service.policy = FetchPolicy(blocked_domains=["xn--bcher-kva.example"])
        for strategy in (
            fetch_service.direct_strategy,
            fetch_service.jina_strategy,
            fetch_service.crawl4ai_strategy,
            fetch_service.archive_strategy,
        ):
            assert strategy is not None
            strategy.fetch = AsyncMock(return_value=None)
        fetch_service.cache.get = AsyncMock(return_value=None)

        result = await fetch_service.fetch("https://bücher.example/")

        assert result is None
        fetch_service.cache.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_numeric_ip_loopback_rejected_before_cache(self, fetch_service):
        """Legacy numeric IPv4 forms like ``2130706433`` must be rejected.

        Mirrors Codex finding #3 — libc resolves ``2130706433``, ``127.1``,
        and ``0xA9FEA9FE`` as IPv4 literals even though ``ipaddress``
        rejects them. Without canonicalization those forms slip past the
        IP-literal validator and reach the cache layer.
        """
        for host in ("2130706433", "127.1", "0xA9FEA9FE"):
            fetch_service.cache.get = AsyncMock(return_value=None)
            for strategy in (
                fetch_service.direct_strategy,
                fetch_service.jina_strategy,
                fetch_service.crawl4ai_strategy,
                fetch_service.archive_strategy,
            ):
                assert strategy is not None
                strategy.fetch = AsyncMock(return_value=None)
            url = f"http://{host}/"
            result = await fetch_service.fetch(url)
            assert result is None, f"legacy IP literal {host} should be rejected"
            fetch_service.cache.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_libc_numeric_ip_grammar_rejected_before_cache(self, fetch_service):
        """The full ``inet_aton`` grammar must be rejected before cache access.

        Mirrors Codex round-4 finding #5 — the prior parser missed libc
        numeric forms ``017700000001``, ``0x7f.1``, ``127.16777215``, and
        ``0xA9.0xFE.0xA9.0xFE``. All four resolve to loopback /
        link-local addresses on Linux and must be classified as disallowed
        so a retained or concurrently-written pre-hardening cache entry
        cannot be served.
        """
        # (host string, expected IP it resolves to under inet_aton).
        cases = (
            ("017700000001", "127.0.0.1"),
            ("0x7f.1", "127.0.0.1"),
            ("127.16777215", "127.255.255.255"),
            ("0xA9.0xFE.0xA9.0xFE", "169.254.169.254"),
        )
        for host, expected_ip in cases:
            fetch_service.cache.get = AsyncMock(return_value=None)
            for strategy in (
                fetch_service.direct_strategy,
                fetch_service.jina_strategy,
                fetch_service.crawl4ai_strategy,
                fetch_service.archive_strategy,
            ):
                assert strategy is not None
                strategy.fetch = AsyncMock(return_value=None)
            url = f"http://{host}/"
            result = await fetch_service.fetch(url)
            assert result is None, (
                f"libc numeric literal {host} (resolves to {expected_ip}) "
                f"should be rejected as disallowed"
            )
            fetch_service.cache.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_octal_component_ipv4_literals_rejected_before_cache(self, fetch_service):
        """Per-component octal IPv4 forms must be rejected before cache access.

        Mirrors Codex round-5 finding #4 — ``int(part, 0)`` rejects bare
        octal forms like ``0177`` while libc accepts them. Without
        per-component octal grammar support, ``0177.0.0.1`` resolves to
        ``127.0.0.1`` under libc but slips past the parser and reaches the
        cache layer. All four forms below must be classified as disallowed
        before any cache lookup runs.
        """
        # (host string, expected IP it resolves to under inet_aton).
        cases = (
            ("0177.0.0.1", "127.0.0.1"),
            ("0177.1", "127.0.0.1"),
            ("0251.0376.0251.0376", "169.254.169.254"),
        )
        for host, expected_ip in cases:
            fetch_service.cache.get = AsyncMock(return_value=None)
            for strategy in (
                fetch_service.direct_strategy,
                fetch_service.jina_strategy,
                fetch_service.crawl4ai_strategy,
                fetch_service.archive_strategy,
            ):
                assert strategy is not None
                strategy.fetch = AsyncMock(return_value=None)
            url = f"http://{host}/"
            result = await fetch_service.fetch(url)
            assert result is None, (
                f"per-component octal literal {host} (resolves to "
                f"{expected_ip}) should be rejected as disallowed"
            )
            fetch_service.cache.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_redirect_dns_validation_uses_shared_deadline(self, fetch_policy):
        """Each redirect hop's DNS validation must consume the shared budget.

        Mirrors Codex round-5 finding #5 — ``validate_url_and_resolve_async``
        previously granted each hop its own independent timeout, so a
        chain of redirects could keep resolver workers occupied past the
        shared ``_PER_FETCH_DEADLINE_SECONDS`` budget. The fix passes
        ``timeout=remaining`` from the shared ``deadline_at`` into every
        DNS-validation call, so subsequent hops see a strictly smaller
        (or equal) timeout than the first hop.
        """
        from unittest.mock import MagicMock

        from orchestrator.services.fetch.strategies import direct as _direct_module
        from orchestrator.tools.ssrf_guard import ValidatedUrl

        original_deadline = _direct_module._PER_FETCH_DEADLINE_SECONDS
        original_attempt = _direct_module._PER_ADDRESS_TIMEOUT_SECONDS
        try:
            # Short deadline so the test is fast.
            _direct_module._PER_FETCH_DEADLINE_SECONDS = 0.5
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = 0.2

            validate_calls: list[float] = []

            async def fast_validate(url, *args, **kwargs):
                # Record the ``timeout`` argument so we can verify it
                # shrinks across redirect hops.
                timeout = kwargs.get("timeout")
                validate_calls.append(timeout if timeout is not None else -1.0)
                # Tiny sleep so the deadline actually shrinks between hops.
                import asyncio as _asyncio

                await _asyncio.sleep(0.05)
                return ValidatedUrl(
                    url=url, host="example.com", port=443, addresses=("93.184.216.34",)
                )

            def _redirect_response():
                response = MagicMock()
                response.status_code = 302
                response.headers = {"location": "/hop_next"}
                response.raise_for_status = MagicMock()
                return response

            with (
                patch(
                    "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
                    new_callable=AsyncMock,
                    side_effect=fast_validate,
                ) as mock_validate,
                patch(
                    "httpx.AsyncClient.get",
                    new_callable=AsyncMock,
                    return_value=_redirect_response(),
                ),
            ):
                # Redirect chain will hit ``_MAX_REDIRECTS`` (5) and
                # then return None.
                result = await DirectFetchStrategy(fetch_policy).fetch(
                    "https://example.com/article"
                )

            assert result is None
            # Multiple hops reached — at least 3 (loop visits 6 total,
            # minus the early termination once the deadline is gone).
            assert mock_validate.await_count >= 3, (
                f"expected >=3 redirect hops, got {mock_validate.await_count}"
            )
            # Each subsequent hop's timeout must be <= the previous one —
            # if not, the shared deadline isn't actually being threaded
            # into ``validate_url_and_resolve_async``. The first timeout
            # is the full 0.5s budget; later timeouts shrink as the
            # deadline ticks down.
            first_timeout = validate_calls[0]
            assert first_timeout > 0
            for i, t in enumerate(validate_calls):
                assert t <= first_timeout, (
                    f"hop {i + 1} timeout {t} exceeds first-hop timeout "
                    f"{first_timeout}: shared deadline is not being "
                    f"threaded through"
                )
            # And the final timeout must be strictly smaller than the
            # first — the budget shrinks across hops.
            assert validate_calls[-1] < first_timeout, (
                f"final timeout {validate_calls[-1]} did not shrink from "
                f"first timeout {first_timeout}"
            )
        finally:
            _direct_module._PER_FETCH_DEADLINE_SECONDS = original_deadline
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = original_attempt

    @pytest.mark.asyncio
    async def test_slow_drip_response_cannot_outlive_shared_deadline(self, fetch_policy):
        """A slow-drip response is bounded by the shared wall-clock deadline.

        Mirrors Codex round-5 finding #6 — ``httpx.Timeout`` is an
        inactivity timeout, not an overall wall-clock bound. A server
        that sends a byte every few seconds could keep ``client.get()``
        alive indefinitely without tripping the per-attempt timeout.
        The fix wraps ``client.get()`` in ``asyncio.wait_for`` with the
        remaining budget, so the shared deadline actually bounds total
        wall time even against an actively-streaming slow-drip response.
        """
        import asyncio as _asyncio
        import time as _time
        from unittest.mock import MagicMock

        from orchestrator.services.fetch.strategies import direct as _direct_module

        original_deadline = _direct_module._PER_FETCH_DEADLINE_SECONDS
        original_attempt = _direct_module._PER_ADDRESS_TIMEOUT_SECONDS
        try:
            _direct_module._PER_FETCH_DEADLINE_SECONDS = 0.3
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = 2.0  # very generous

            async def slow_drip_get(*args, **kwargs):
                # Simulate a server that sends a byte every 100ms but
                # never completes. The inactivity timeout (2s) is large
                # enough that it never trips; the wall-clock deadline
                # (0.3s) must kick in and abort the request.
                start = _time.monotonic()
                while _time.monotonic() - start < 5.0:
                    await _asyncio.sleep(0.1)
                # Should never reach here — ``asyncio.wait_for`` cancels us.
                response = MagicMock()
                response.status_code = 200
                response.headers = {"content-type": "text/plain"}
                response.text = "should not be returned"
                return response

            with (
                _mock_direct_resolution("https://example.com/article"),
                patch(
                    "httpx.AsyncClient.get",
                    new_callable=AsyncMock,
                    side_effect=slow_drip_get,
                ),
            ):
                started = _time.monotonic()
                result = await DirectFetchStrategy(fetch_policy).fetch(
                    "https://example.com/article"
                )
                elapsed = _time.monotonic() - started

            assert result is None
            # Slow-drip response was bounded by the shared deadline
            # (~0.3s) rather than the inactivity timeout (2s) or the
            # 5s self-loop the mock would otherwise run.
            assert elapsed < 0.8, (
                f"slow-drip response outlived shared deadline: {elapsed:.2f}s "
                f"exceeds one deadline + slack"
            )
        finally:
            _direct_module._PER_FETCH_DEADLINE_SECONDS = original_deadline
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = original_attempt

    @pytest.mark.asyncio
    async def test_dns_unreachable_is_typed_separately_from_policy_violation(self, fetch_service):
        """``SsrfUnreachable`` must be caught and let fallbacks run.

        Mirrors Codex round-4 finding #1 — the prior keyword-scan approach
        scanned attacker-controlled exception text. The new typed
        ``SsrfUnreachable`` exception is the unambiguous signal that the
        target was *unreachable*, not *unsafe*, so the direct strategy
        returns ``None`` and the strategy chain proceeds to Jina/Archive.
        """
        from orchestrator.services.fetch.strategies import direct as _direct_module

        # Drive the same scenario as test_archive_fallback_used_when_direct_dns_unavailable
        # but assert the new typed exception is what unblocks the chain.
        archive_strategy = fetch_service.archive_strategy
        assert archive_strategy is not None
        archive_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://example.com",
                content=(
                    "This is a sufficiently long archive snapshot recovered "
                    "despite target DNS unavailability for testing"
                ),
                title="archive",
                strategy_used="archive",
                cached=False,
                fetch_time_ms=10.0,
                content_length=80,
            )
        )
        fetch_service.jina_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        with patch.object(
            _direct_module,
            "validate_url_and_resolve_async",
            new=AsyncMock(side_effect=SsrfUnreachable("gaierror: Name or service not known")),
        ):
            result = await fetch_service.fetch("https://example.com/")

        assert result is not None
        assert result.strategy_used == "archive"
        fetch_service.archive_strategy.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_policy_violation_does_not_trigger_fallback(self, fetch_service):
        """``SsrfPolicyViolation`` must terminate the chain, not fall back.

        Counterpart to the previous test — a blocked-IP / disallowed-port /
        malformed URL is a *policy* violation and the chain must not try
        Jina or Archive (which themselves contact external hosts). The
        ``FetchService.fetch`` caller does not see the exception
        propagated — the service catches ``SsrfViolation`` and returns
        ``None`` — but the fallback strategies must not be called.
        """
        from orchestrator.services.fetch.strategies import direct as _direct_module

        fetch_service.jina_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.archive_strategy.fetch = AsyncMock(return_value=None)
        fetch_service.cache.get = AsyncMock(return_value=None)

        with patch.object(
            _direct_module,
            "validate_url_and_resolve_async",
            new=AsyncMock(side_effect=SsrfPolicyViolation("port 8443 is not allowed")),
        ):
            result = await fetch_service.fetch("https://example.com/")

        # The service catches the policy violation, logs it, and returns
        # ``None`` — fallbacks are skipped.
        assert result is None
        fetch_service.jina_strategy.fetch.assert_not_awaited()
        fetch_service.archive_strategy.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archive_fallback_used_when_direct_dns_unavailable(self, fetch_service):
        """Jina/Archive fallbacks must run when the direct strategy's DNS fails.

        Mirrors Codex finding #2 — Archive.org can recover a recent snapshot
        even when the target host is temporarily unresolvable. Failing the
        whole fetch on direct DNS failure prevents those external fallbacks
        from running.
        """
        from orchestrator.services.fetch.strategies import direct as _direct_module

        # The real direct strategy is in play here. We patch only its DNS
        # validator to simulate a target-host DNS failure; the strategy's
        # own fetch method then distinguishes DNS unavailability from a
        # safety violation and returns ``None`` instead of propagating
        # SsrfViolation, so the chain proceeds to Jina and Archive.
        archive_strategy = fetch_service.archive_strategy
        assert archive_strategy is not None
        archive_strategy.fetch = AsyncMock(
            return_value=FetchResult(
                url="https://example.com",
                content=(
                    "This is a sufficiently long archive snapshot recovered "
                    "despite target DNS unavailability for testing"
                ),
                title="archive",
                strategy_used="archive",
                cached=False,
                fetch_time_ms=10.0,
                content_length=80,
            )
        )

        jina_strategy = fetch_service.jina_strategy
        assert jina_strategy is not None
        jina_strategy.fetch = AsyncMock(return_value=None)

        fetch_service.cache.get = AsyncMock(return_value=None)
        fetch_service.cache.set = AsyncMock(return_value=True)

        with patch.object(
            _direct_module,
            "validate_url_and_resolve_async",
            new=AsyncMock(side_effect=SsrfUnreachable("DNS validation timed out")),
        ):
            result = await fetch_service.fetch("https://example.com/")

        assert result is not None
        assert result.strategy_used == "archive"
        # Direct tried (and failed via DNS unavailability) before Jina and
        # Archive were attempted.
        fetch_service.jina_strategy.fetch.assert_awaited_once()
        fetch_service.archive_strategy.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_address_retry_bounded_by_shared_deadline(self, fetch_policy):
        """Many silently-dropping addresses must not multiply past one deadline.

        Mirrors Codex finding #4 — the direct strategy must share one
        per-fetch budget across all address attempts instead of granting each
        address its own ``timeout`` × ``addresses`` × ``hops`` budget.
        """
        import asyncio as _asyncio
        import time as _time

        strategy = DirectFetchStrategy(fetch_policy)

        # Five addresses; with the old per-address 10s timeout this would
        # be 50s of connect attempts. With the shared budget it should
        # finish in well under that.
        addresses = tuple(f"93.184.216.{i}" for i in range(2, 7))

        async def slow_connect(*args, **kwargs):
            # httpx will apply the per-attempt timeout to the connect call.
            # With the shared deadline the budget is ~15s and the per-attempt
            # cap is 5s, so we observe a bounded number of attempts instead
            # of all five.
            await _asyncio.sleep(0.05)
            raise httpx.ConnectError("simulated silent drop")

        started = _time.monotonic()
        with (
            _mock_direct_resolution(
                "https://example.com/article",
                addresses=addresses,
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=slow_connect,
            ) as mock_get,
        ):
            result = await strategy.fetch("https://example.com/article")
        elapsed = _time.monotonic() - started

        assert result is None
        # The shared deadline must cap total attempts at most
        # ``ceil(_PER_FETCH_DEADLINE_SECONDS / _PER_ADDRESS_TIMEOUT_SECONDS)``
        # plus one final attempt that observes the deadline already exhausted.
        from orchestrator.services.fetch.strategies.direct import (
            _PER_FETCH_DEADLINE_SECONDS,
            _MAX_ADDRESS_ATTEMPTS,
        )

        # Bound on attempts: at most the address cap, or fewer if the
        # budget runs out before reaching it. Either way, well below the
        # naive ``len(addresses)`` that the bug allowed.
        assert mock_get.await_count <= _MAX_ADDRESS_ATTEMPTS
        # Wallclock must be less than ``_PER_FETCH_DEADLINE_SECONDS + slack``
        # rather than ``len(addresses) * timeout``.
        assert elapsed < _PER_FETCH_DEADLINE_SECONDS + 1.0

    @pytest.mark.asyncio
    async def test_address_retry_skips_remaining_when_deadline_already_past(self, fetch_policy):
        """Once the deadline is exhausted, no further address is attempted."""
        import asyncio as _asyncio
        import time as _time

        strategy = DirectFetchStrategy(fetch_policy)

        async def slow_connect(*args, **kwargs):
            await _asyncio.sleep(2.0)
            raise httpx.ConnectError("simulated slow drop")

        started = _time.monotonic()
        with (
            _mock_direct_resolution(
                "https://example.com/article",
                addresses=("93.184.216.34", "93.184.216.35"),
            ),
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=slow_connect,
            ) as mock_get,
        ):
            result = await strategy.fetch("https://example.com/article")
        elapsed = _time.monotonic() - started

        assert result is None
        # The first attempt consumes the budget; the second short-circuits.
        assert mock_get.await_count <= 2
        # Wallclock bounded by per-attempt cap, not by ``2 × timeout``.
        from orchestrator.services.fetch.strategies.direct import (
            _PER_ADDRESS_TIMEOUT_SECONDS,
        )

        assert elapsed < _PER_ADDRESS_TIMEOUT_SECONDS + 2.0

    @pytest.mark.asyncio
    async def test_redirect_loop_shares_one_deadline_across_hops(self, fetch_policy):
        """One ``_PER_FETCH_DEADLINE_SECONDS`` budget spans every redirect hop.

        Mirrors Codex round-4 finding #3 — the prior implementation created
        a fresh deadline inside ``_request_with_address_fallback`` for every
        hop, so a server with six permitted hops could consume
        ``addresses × hops × timeout`` of wall time on silently-dropping
        addresses. The deadline must now be created once at ``fetch`` entry
        and carried through every hop.

        The bug manifests when a hop's address attempt consumes the
        budget, then the next hop is reached. With a per-hop reset, the
        next hop grants a *fresh* budget. With the fix, the next hop
        short-circuits because the shared deadline is already past.

        We patch the redirect loop to synthesize a chain of redirects so
        that hop 2 is actually reached; then assert that the wall time
        stays bounded by one shared deadline (not two).
        """
        import asyncio as _asyncio
        import time as _time
        from unittest.mock import MagicMock

        from orchestrator.services.fetch.strategies import direct as _direct_module

        # Use a very short shared deadline for the test by monkey-patching
        # the constant. This lets the test observe the cross-hop sharing
        # without waiting ``_PER_FETCH_DEADLINE_SECONDS`` (15s) wall time.
        original_deadline = _direct_module._PER_FETCH_DEADLINE_SECONDS
        original_attempt = _direct_module._PER_ADDRESS_TIMEOUT_SECONDS
        try:
            _direct_module._PER_FETCH_DEADLINE_SECONDS = 0.3
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = 0.15

            call_log: list[tuple[str, str]] = []

            async def slow_redirect_then_fail(*args, **kwargs):
                # The first call sleeps past the deadline so the shared
                # budget is exhausted; we then return a synthetic 302
                # response that triggers the redirect loop's next
                # iteration. The second iteration's address attempt
                # should short-circuit because the shared budget is
                # already past.
                n = len(call_log) + 1
                call_log.append(("hop", str(n)))
                if n == 1:
                    await _asyncio.sleep(0.5)  # exceeds 0.3s deadline
                    # Synthetic 302 response — gets the redirect loop
                    # to a second iteration.
                    response = MagicMock()
                    response.status_code = 302
                    response.headers = {"location": "/hop2"}
                    response.raise_for_status = MagicMock()
                    return response
                # Subsequent calls: short sleep (the budget should
                # already be exhausted).
                await _asyncio.sleep(0.05)
                raise httpx.ConnectError("hop 2 silent drop")

            started = _time.monotonic()
            with (
                _mock_direct_resolution("https://example.com/article"),
                patch(
                    "httpx.AsyncClient.get",
                    new_callable=AsyncMock,
                    side_effect=slow_redirect_then_fail,
                ),
            ):
                result = await strategy_fetch_with_short_deadline(
                    fetch_policy=fetch_policy,
                )
            elapsed = _time.monotonic() - started

            assert result is None
            # First call slept ~0.5s; under the fix the second hop's
            # address attempt short-circuits before sleeping. Under the
            # bug, the second hop would grant a fresh 0.3s budget,
            # adding another ~0.3s of wall time. The fix keeps elapsed
            # bounded by one deadline.
            assert elapsed < 0.8, (
                f"shared deadline failed: {elapsed:.2f}s exceeds one deadline "
                f"+ the first-hop 0.5s sleep"
            )
        finally:
            _direct_module._PER_FETCH_DEADLINE_SECONDS = original_deadline
            _direct_module._PER_ADDRESS_TIMEOUT_SECONDS = original_attempt


async def strategy_fetch_with_short_deadline(*, fetch_policy):
    """Helper that runs DirectFetchStrategy.fetch on a real policy."""
    from orchestrator.services.fetch.strategies.direct import DirectFetchStrategy

    return await DirectFetchStrategy(fetch_policy).fetch("https://example.com/article")
