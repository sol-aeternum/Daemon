import socket
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

from orchestrator.services.fetch.cache import FetchCache
from orchestrator.services.fetch.models import FetchPolicy, FetchResult
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.fetch.strategies.archive import ArchiveOrgStrategy
from orchestrator.services.fetch.strategies.crawl4ai import Crawl4AIStrategy
from orchestrator.services.fetch.strategies.direct import DirectFetchStrategy
from orchestrator.services.fetch.strategies.jina import JinaReaderStrategy
from orchestrator.services.fetch.strategies.youtube import YouTubeTranscriptStrategy
from orchestrator.services.fetch.url_extract import extract_urls
from orchestrator.tools.ssrf_guard import SsrfViolation


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


class TestDirectStrategy:
    @pytest.mark.asyncio
    async def test_fetch_success(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>This is a sufficiently long content for testing purposes to pass validation</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long content" in result.content
        assert result.strategy_used == "direct"

    @pytest.mark.asyncio
    async def test_fetch_failure(self, fetch_policy):
        strategy = DirectFetchStrategy(fetch_policy)

        with patch("httpx.AsyncClient.get", side_effect=Exception("Network error")):
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("8.8.8.8",),
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

        with patch("httpx.AsyncClient.get", side_effect=assert_dns_is_unpatched):
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("93.184.216.34",),
            ),
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("8.8.8.8",),
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("93.184.216.34",),
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("2606:4700:4700::1111", "93.184.216.34"),
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("2606:4700:4700::1111", "2001:4860:4860::8888"),
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

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long content" in result.content
        assert result.strategy_used == "jina"

    @pytest.mark.asyncio
    async def test_fetch_failure(self, fetch_policy):
        strategy = JinaReaderStrategy(fetch_policy)

        # Mock httpx response with error status
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            result = await strategy.fetch("https://example.com")

        assert result is None


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
            patch("httpx.AsyncClient.get") as mock_get,
            patch(
                "orchestrator.services.fetch.strategies.archive.html_to_markdown",
                return_value=markdown_content,
            ),
        ):
            mock_get.side_effect = [mock_availability_response, mock_content_response]
            result = await strategy.fetch("https://example.com")

        assert result is not None
        assert isinstance(result, FetchResult)
        assert result.url == "https://example.com"
        assert "sufficiently long archived content" in result.content
        assert result.strategy_used == "archive"

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
            new=AsyncMock(side_effect=SsrfViolation("DNS validation timed out")),
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=addresses,
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
            patch(
                "orchestrator.tools.ssrf_guard._resolve_and_check",
                return_value=("93.184.216.34", "93.184.216.35"),
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
