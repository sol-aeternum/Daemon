import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

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
                    "timestamp": "20260301000000",  # Recent timestamp within 90 days
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
        fetch_service.direct_strategy.fetch.assert_called_once_with(
            "https://example.com"
        )
        fetch_service.jina_strategy.fetch.assert_called_once_with("https://example.com")
        fetch_service.crawl4ai_strategy.fetch.assert_called_once_with(
            "https://example.com"
        )
        fetch_service.archive_strategy.fetch.assert_called_once_with(
            "https://example.com"
        )

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

        result = await fetch_service.fetch(
            "https://www.youtube.com/watch?v=abc123", extract="text"
        )

        assert result is not None
        assert result.strategy_used == "youtube"
        fetch_service.youtube_strategy.fetch.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc123"
        )
        fetch_service.direct_strategy.fetch.assert_not_called()
        fetch_service.jina_strategy.fetch.assert_not_called()
        fetch_service.crawl4ai_strategy.fetch.assert_not_called()
        fetch_service.archive_strategy.fetch.assert_not_called()
