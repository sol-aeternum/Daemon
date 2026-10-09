"""Bounded fetch/extraction contract tests.

Covers the approved conversation-scoped chunked-web-reading prerequisites
(docs/CHUNKED_WEB_READING_DESIGN.md) at the fetch-service boundary:

* streamed decoded response body bounded at the configured cap (including
  gzip-compressed and chunked responses), with the connection closed on
  oversize/deadline/cancellation/error and no partial page served as
  complete;
* readable article extraction via the existing trafilatura helper — no raw
  HTML fallback, no silent raw HTML as transcript, bounded metadata mode;
* #358 URL identity: scheme/host normalized, path/query case and order and
  meaningful trailing slash preserved, in a keyed v3 cache namespace that
  binds extraction mode/version and never touches legacy entries;
* ``use_cache=False`` bypasses both cache reads and writes;
* provenance: source URL and final logical redirect URL (not the pinned IP).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import pytest

from orchestrator.config import Settings
from orchestrator.services.fetch import bounds as bounds_module
from orchestrator.services.fetch.cache import (
    CACHE_NAMESPACE_V3,
    FetchCache,
    normalize_url,
    result_cache_key,
)
from orchestrator.services.fetch.models import (
    EXTRACTION_VERSION_V1,
    FetchExtractionError,
    FetchPageTooLargeError,
    FetchPolicy,
    FetchResult,
)
from orchestrator.services.fetch.service import (
    FetchService,
    FetchStrategy,
    normalize_extract_mode,
)
from orchestrator.services.fetch.strategies.direct import (
    DirectFetchStrategy,
    _read_bounded_body,
)
from orchestrator.tools.ssrf_guard import ValidatedUrl

MB = 1024 * 1024


@pytest.fixture
def fetch_policy() -> FetchPolicy:
    return FetchPolicy(min_content_length=10, error_signatures=[])


def _mock_cache() -> FetchCache:
    """FetchCache with a mocked Redis transport (real get/set methods)."""
    cache = FetchCache(user_id=uuid.UUID(int=1))
    cache.redis = AsyncMock()
    cache.redis.eval.return_value = 1
    cache.redis.sscan.return_value = (0, [])
    cache._ensure_connection = AsyncMock(return_value=True)
    return cache


def _mock_service_cache() -> FetchCache:
    """FetchCache with mocked get/set for FetchService-level assertions."""
    cache = _mock_cache()
    cache.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cache.set = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return cache


def _validated(url: str, addresses: tuple[str, ...] = ("93.184.216.34",)) -> ValidatedUrl:
    return ValidatedUrl(
        url=url,
        host=urlsplit(url).hostname or "example.com",
        port=443,
        addresses=addresses,
    )


def _patch_resolution() -> Any:
    """Pin the direct strategy's DNS validation to a public address."""

    async def validate(url: str, *args: Any, **kwargs: Any) -> ValidatedUrl:
        return _validated(url)

    return patch(
        "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
        new_callable=AsyncMock,
        side_effect=validate,
    )


def _with_mock_transport(transport: httpx.AsyncBaseTransport) -> Any:
    """Inject a MockTransport into every per-address AsyncClient."""

    original_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    return patch("httpx.AsyncClient", side_effect=patched)


# ---------------------------------------------------------------------------
# Bounded streamed body (unit, duck-typed response)
#
# ``_FakeStreamResponse`` is deliberately duck-typed (Astra's harness uses
# ``aiter_raw`` + ``is_stream_consumed``); each call site casts it to
# ``httpx.Response`` at the tested boundary because the production helper
# is annotated with the real response type.
# ---------------------------------------------------------------------------


def _as_response(fake: Any) -> httpx.Response:
    """Test-boundary cast: the fake satisfies the streamed-response surface."""
    return cast(httpx.Response, fake)


class _FakeStreamResponse:
    """Minimal duck-typed streaming response for bounded-read unit tests."""

    def __init__(self, chunks: list[bytes], *, chunk_delay: float = 0.0) -> None:
        self._chunks = chunks
        self._chunk_delay = chunk_delay
        self.closed = False
        self.is_stream_consumed = False
        self.status_code = 200
        self.headers = httpx.Headers({"content-type": "text/plain"})
        self.request = httpx.Request("GET", "https://example.com/x")

    def aiter_raw(self) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                if self._chunk_delay:
                    await asyncio.sleep(self._chunk_delay)
                yield chunk

        return gen()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_bounded_body_raises_and_closes_on_oversize(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.services.fetch.strategies.direct.max_response_bytes",
        lambda: 16,
    )
    response = _FakeStreamResponse([b"0123456789", b"0123456789"])

    with pytest.raises(FetchPageTooLargeError) as excinfo:
        await _read_bounded_body(_as_response(response), deadline_at=time.monotonic() + 5)

    assert excinfo.value.code == "page_too_large"
    assert response.closed is True


@pytest.mark.asyncio
async def test_bounded_body_accepts_body_up_to_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.services.fetch.strategies.direct.max_response_bytes",
        lambda: 20,
    )
    response = _FakeStreamResponse([b"0123456789", b"0123456789"])

    body = await _read_bounded_body(_as_response(response), deadline_at=time.monotonic() + 5)

    assert body == b"01234567890123456789"
    assert response.closed is True


@pytest.mark.asyncio
async def test_bounded_body_deadline_closes_and_aborts(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.services.fetch.strategies.direct.max_response_bytes",
        lambda: 10 * MB,
    )
    response = _FakeStreamResponse([b"chunk", b"chunk", b"chunk"], chunk_delay=0.4)

    with pytest.raises(TimeoutError):
        await _read_bounded_body(_as_response(response), deadline_at=time.monotonic() + 0.5)

    assert response.closed is True


@pytest.mark.asyncio
async def test_bounded_body_iterator_error_closes_and_propagates(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.services.fetch.strategies.direct.max_response_bytes",
        lambda: 10 * MB,
    )

    class _BrokenStream(_FakeStreamResponse):
        def aiter_raw(self) -> AsyncIterator[bytes]:
            async def gen() -> AsyncIterator[bytes]:
                yield b"partial"
                raise ValueError("simulated stream failure")

            return gen()

    response = _BrokenStream([])

    with pytest.raises(ValueError):
        await _read_bounded_body(_as_response(response), deadline_at=time.monotonic() + 5)

    assert response.closed is True


@pytest.mark.asyncio
async def test_bounded_body_cancellation_closes_response(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.services.fetch.strategies.direct.max_response_bytes",
        lambda: 10 * MB,
    )

    class _SlowStream(_FakeStreamResponse):
        def aiter_raw(self) -> AsyncIterator[bytes]:
            async def gen() -> AsyncIterator[bytes]:
                yield b"start"
                await asyncio.Event().wait()  # never completes

            return gen()

    response = _SlowStream([])

    async def consume() -> bytes:
        return await _read_bounded_body(_as_response(response), deadline_at=time.monotonic() + 10)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert response.closed is True


# ---------------------------------------------------------------------------
# Bounded streamed body through real httpx (MockTransport)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_streamed_gzip_body_is_decoded_once(fetch_policy) -> None:
    """A gzip response streams, decompresses once, and keeps provenance."""
    text = "bounded body content from the streaming path " * 40
    payload = gzip.compress(text.encode("utf-8"))

    async def handler(request: httpx.Request) -> httpx.Response:
        async def gen() -> AsyncIterator[bytes]:
            step = max(1, len(payload) // 4)
            for index in range(0, len(payload), step):
                yield payload[index : index + step]

        return httpx.Response(
            200,
            content=gen(),
            headers={
                "content-type": "text/html",
                "content-encoding": "gzip",
                "content-length": str(len(payload)),
            },
        )

    with (
        _patch_resolution(),
        _with_mock_transport(httpx.MockTransport(handler)),
    ):
        result = await DirectFetchStrategy(fetch_policy).fetch("https://example.com/page")

    assert result is not None
    assert result.content == text  # decoded exactly once — no gzip artifacts
    assert result.content_length == len(result.content)
    assert result.content_type == "text/html"
    assert result.source_url == "https://example.com/page"
    assert result.final_url == "https://example.com/page"
    assert "93.184.216.34" not in (result.final_url or "")


@pytest.mark.asyncio
async def test_direct_chunked_body_over_cap_raises_page_too_large(fetch_policy) -> None:
    """A decoded body beyond the cap is rejected before content is served."""
    cap = 2 * 1024 * 1024

    async def handler(request: httpx.Request) -> httpx.Response:
        async def gen() -> AsyncIterator[bytes]:
            yield b"x" * cap  # at cap …
            yield b"y"  # … one byte over

        return httpx.Response(
            200,
            content=gen(),
            headers={"content-type": "text/html"},
        )

    with (
        _patch_resolution(),
        _with_mock_transport(httpx.MockTransport(handler)),
        pytest.raises(FetchPageTooLargeError) as excinfo,
    ):
        await DirectFetchStrategy(fetch_policy).fetch("https://example.com/big")

    assert excinfo.value.code == "page_too_large"


@pytest.mark.asyncio
async def test_direct_compressed_bomb_over_decoded_cap_raises(fetch_policy) -> None:
    """The cap applies to decoded bytes: a tiny compressed bomb is rejected."""
    bomb_text = b"a" * (2 * 1024 * 1024 + 1)
    payload = gzip.compress(bomb_text)
    assert len(payload) < 64 * 1024  # wire form is tiny; the decoded form is not

    async def handler(request: httpx.Request) -> httpx.Response:
        async def gen() -> AsyncIterator[bytes]:
            step = max(1, len(payload) // 8)
            for index in range(0, len(payload), step):
                yield payload[index : index + step]

        return httpx.Response(
            200,
            content=gen(),
            headers={"content-type": "text/html", "content-encoding": "gzip"},
        )

    with (
        _patch_resolution(),
        _with_mock_transport(httpx.MockTransport(handler)),
        pytest.raises(FetchPageTooLargeError) as excinfo,
    ):
        await DirectFetchStrategy(fetch_policy).fetch("https://example.com/bomb")

    assert excinfo.value.code == "page_too_large"


@pytest.mark.asyncio
async def test_direct_body_deadline_bounds_streaming(fetch_policy, monkeypatch) -> None:
    """The shared per-fetch deadline includes streaming body chunks."""
    from orchestrator.services.fetch.strategies import direct as direct_module

    original_deadline = direct_module._PER_FETCH_DEADLINE_SECONDS
    original_attempt = direct_module._PER_ADDRESS_TIMEOUT_SECONDS
    monkeypatch.setattr(direct_module, "_PER_FETCH_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(direct_module, "_PER_ADDRESS_TIMEOUT_SECONDS", 5.0)
    try:
        closed_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal closed_count

            async def gen() -> AsyncIterator[bytes]:
                nonlocal closed_count
                try:
                    for index in range(20):
                        await asyncio.sleep(0.3)
                        yield b"chunk-data\n"
                finally:
                    closed_count += 1

            return httpx.Response(
                200,
                content=gen(),
                headers={"content-type": "text/plain"},
            )

        started = time.monotonic()
        with (
            _patch_resolution(),
            _with_mock_transport(httpx.MockTransport(handler)),
        ):
            result = await DirectFetchStrategy(fetch_policy).fetch("https://example.com/drip")
        elapsed = time.monotonic() - started

        assert result is None  # deadline abort — never a partial page
        assert elapsed < 2.0
        assert closed_count == 1  # the aborted stream was closed
    finally:
        direct_module._PER_FETCH_DEADLINE_SECONDS = original_deadline
        direct_module._PER_ADDRESS_TIMEOUT_SECONDS = original_attempt


# ---------------------------------------------------------------------------
# FetchService: bounded errors, cache bypass, extraction modes
# ---------------------------------------------------------------------------


def _direct_result(
    content: str,
    *,
    content_type: str | None = "text/plain",
    url: str = "https://example.com/page",
) -> FetchResult:
    return FetchResult(
        url=url,
        content=content,
        title="",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=0.0,
        content_length=len(content),
        source_url=url,
        final_url=url,
        content_type=content_type,
    )


def _plain_text_service(fetch_policy: FetchPolicy) -> FetchService:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.return_value = _direct_result(
        "This is sufficiently long plain text content for the bounded contract."
    )
    return service


def _service_cache_mocks(service: FetchService) -> tuple[AsyncMock, AsyncMock]:
    """Typed (get, set) mocks for a service built with ``_mock_service_cache``."""
    assert service.cache is not None
    return cast(AsyncMock, service.cache.get), cast(AsyncMock, service.cache.set)


def _strategy_fetch_mock(strategy: FetchStrategy | None) -> AsyncMock:
    """Replace a strategy's ``fetch`` with an AsyncMock and return it typed."""
    assert strategy is not None
    fetch_mock = AsyncMock()
    cast(Any, strategy).fetch = fetch_mock
    return fetch_mock


@pytest.mark.asyncio
async def test_service_oversize_is_bounded_error_without_cache_write(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.side_effect = FetchPageTooLargeError()
    _, cache_set = _service_cache_mocks(service)

    with pytest.raises(FetchPageTooLargeError) as excinfo:
        await service.fetch("https://example.com/big")

    assert excinfo.value.code == "page_too_large"
    cache_set.assert_not_called()


@pytest.mark.asyncio
async def test_service_use_cache_false_bypasses_reads_and_writes(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)
    cache_get, cache_set = _service_cache_mocks(service)
    assert service.direct_strategy is not None
    direct_fetch = cast(AsyncMock, service.direct_strategy.fetch)

    result = await service.fetch("https://example.com/page", use_cache=False)

    assert result is not None
    cache_get.assert_not_called()
    cache_set.assert_not_called()
    direct_fetch.assert_awaited_once_with("https://example.com/page")


@pytest.mark.asyncio
async def test_service_force_refresh_skips_read_but_writes(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)
    cache_get, cache_set = _service_cache_mocks(service)

    result = await service.fetch("https://example.com/page", force_refresh=True)

    assert result is not None
    cache_get.assert_not_called()
    cache_set.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_cache_write_passes_extraction_mode(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)
    _, cache_set = _service_cache_mocks(service)

    await service.fetch("https://example.com/page")

    set_args = cache_set.await_args
    assert set_args is not None
    assert set_args.kwargs.get("extract") == "article"


@pytest.mark.asyncio
async def test_service_extracted_text_over_bound_is_page_too_large(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    big_text = "a" * (1024 * 1024 + 1)
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.return_value = _direct_result(big_text, content_type="text/plain")
    _, cache_set = _service_cache_mocks(service)

    with pytest.raises(FetchPageTooLargeError):
        await service.fetch("https://example.com/huge-text")

    cache_set.assert_not_called()


# ---------------------------------------------------------------------------
# Extraction modes
# ---------------------------------------------------------------------------

_ARTICLE_HTML = """
<!DOCTYPE html>
<html><head><title>Widget 2.0 Release Notes</title><script>alert("bad")</script><style>.x{}</style></head>
<body>
<nav><a href="/home">Home</a><a href="/pricing">Pricing</a></nav>
<header>Site header banner</header>
<article>
<h1>Widget 2.0 Release Notes</h1>
<p>Version 2.0 introduces streaming support. See the <a href="/docs/api">API documentation</a> for details and <a href="https://vendor.example/guide">the external guide</a>.</p>
<p>Perf improved by 30% this release.</p>
<p>Migration steps:</p>
<table>
<tr><th>Field</th><th>Action</th></tr>
<tr><td>api_key</td><td>rotate</td></tr>
<tr><td>endpoint</td><td>update</td></tr>
</table>
<p>Additional paragraph about the upgrade process for administrators with a lot of detail to keep the extractor confident this is main content.</p>
</article>
<footer>© Example Corp.</footer>
</body></html>
"""


@pytest.mark.asyncio
async def test_article_extraction_strips_chrome_and_keeps_links_tables(
    fetch_policy,
) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.return_value = _direct_result(_ARTICLE_HTML, content_type="text/html")
    _, cache_set = _service_cache_mocks(service)

    result = await service.fetch("https://example.com/docs")

    assert result is not None
    assert "Widget 2.0 Release Notes" in result.content
    assert "API documentation" in result.content
    assert "https://vendor.example/guide" in result.content  # links retained
    assert "api_key" in result.content and "rotate" in result.content  # table retained
    assert 'alert("bad")' not in result.content  # scripts stripped
    assert "Home" not in result.content  # nav stripped
    assert "Example Corp." not in result.content  # footer stripped
    assert "<script" not in result.content and "<nav" not in result.content
    assert result.extraction_version == EXTRACTION_VERSION_V1
    assert result.content_length == len(result.content)
    assert result.content_type == "text/html"
    cache_set.assert_awaited_once()


@pytest.mark.asyncio
async def test_extraction_failure_is_bounded_error_not_raw_html(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.return_value = _direct_result(_ARTICLE_HTML, content_type="text/html")
    _, cache_set = _service_cache_mocks(service)

    with patch(
        "orchestrator.services.fetch.extract.html_to_markdown",
        return_value=None,
    ):
        with pytest.raises(FetchExtractionError) as excinfo:
            await service.fetch("https://example.com/docs")

    assert excinfo.value.code == "extraction_failed"
    cache_set.assert_not_called()


@pytest.mark.asyncio
async def test_plain_text_retains_text_for_article_mode(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)

    result = await service.fetch("https://example.com/page")

    assert result is not None
    text = "This is sufficiently long plain text content for the bounded contract."
    assert result.content == text  # no lossy conversion of plain text
    assert result.content_length == len(result.content)
    assert result.extraction_version == EXTRACTION_VERSION_V1


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["text", "markdown", "TEXT", "Markdown"])
async def test_text_and_markdown_aliases_match_article(fetch_policy, alias) -> None:
    service = _plain_text_service(fetch_policy)

    result = await service.fetch("https://example.com/page", extract=alias)

    assert result is not None
    assert normalize_extract_mode(alias) == "article"


@pytest.mark.asyncio
async def test_metadata_mode_returns_bounded_metadata(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    direct_fetch = _strategy_fetch_mock(service.direct_strategy)
    direct_fetch.return_value = _direct_result(
        _ARTICLE_HTML, content_type="text/html", url="https://example.com/docs"
    )
    _, cache_set = _service_cache_mocks(service)

    result = await service.fetch("https://example.com/docs", extract="metadata")

    assert result is not None
    payload = json.loads(result.content)
    assert payload["title"] == "Widget 2.0 Release Notes"
    assert payload["url"] == "https://example.com/docs"
    assert payload["content_type"] == "text/html"
    assert result.content_length == len(result.content)
    assert result.title == "Widget 2.0 Release Notes"
    # Metadata entries are cached under their own mode namespace.
    set_args = cache_set.await_args
    assert set_args is not None and set_args.kwargs.get("extract") == "metadata"


@pytest.mark.asyncio
async def test_transcript_mode_requires_actual_transcript_strategy(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)

    with pytest.raises(FetchExtractionError) as excinfo:
        await service.fetch("https://example.com/page", extract="transcript")

    assert excinfo.value.code == "extraction_failed"


@pytest.mark.asyncio
async def test_transcript_mode_serves_youtube_transcript(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    transcript = "[00:00:00] Hello and welcome to the demonstration."
    youtube_fetch = _strategy_fetch_mock(service.youtube_strategy)
    youtube_fetch.return_value = FetchResult(
        url="https://www.youtube.com/watch?v=abc123",
        content=transcript,
        title="YouTube Transcript: abc123",
        strategy_used="youtube",
        cached=False,
        fetch_time_ms=0.0,
        content_length=len(transcript),
    )

    result = await service.fetch("https://www.youtube.com/watch?v=abc123", extract="transcript")

    assert result is not None
    assert result.content == transcript
    assert result.extraction_version == EXTRACTION_VERSION_V1


@pytest.mark.asyncio
async def test_metadata_mode_on_transcript_is_bounded_metadata(fetch_policy) -> None:
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    youtube_fetch = _strategy_fetch_mock(service.youtube_strategy)
    youtube_fetch.return_value = FetchResult(
        url="https://www.youtube.com/watch?v=abc123",
        content="[00:00:00] Hello and welcome to the demonstration.",
        title="YouTube Transcript: abc123",
        strategy_used="youtube",
        cached=False,
        fetch_time_ms=0.0,
        content_length=47,
    )

    result = await service.fetch("https://www.youtube.com/watch?v=abc123", extract="metadata")

    assert result is not None
    payload = json.loads(result.content)
    assert payload["title"] == "YouTube Transcript: abc123"
    assert payload["url"] == "https://www.youtube.com/watch?v=abc123"
    assert payload["content_type"] in (None, "")


# ---------------------------------------------------------------------------
# Cache identity (#358) and namespace
# ---------------------------------------------------------------------------


_EQUIVALENT_URLS = [
    # Scheme/host normalization applies…
    pytest.param("https://EXAMPLE.com/A", "https://example.com/A", id="host-case"),
    pytest.param("HTTPS://example.com/A", "https://example.com/A", id="scheme-case"),
    pytest.param("https://BÜCHER.example/A", "https://xn--bcher-kva.example/A", id="idn"),
    pytest.param("https://example.com:443/A", "https://example.com/A", id="default-port"),
    pytest.param("http://example.com:80/A", "http://example.com/A", id="default-port-http"),
    pytest.param("https://example.com/A#frag", "https://example.com/A", id="fragment"),
]

_DISTINCT_URLS = [
    # Resource identity is preserved:
    pytest.param("https://example.com/A", "https://example.com/a", id="path-case"),
    pytest.param("https://example.com/A", "https://example.com/A/", id="trailing-slash"),
    pytest.param(
        "https://example.com/A?Q=1&a=2",
        "https://example.com/A?A=2&q=1",
        id="query-order",
    ),
    pytest.param("https://example.com/A?Q=1", "https://example.com/A?q=1", id="query-case"),
    pytest.param(
        "https://example.com/A?token=AbC", "https://example.com/A?token=abc", id="token-case"
    ),
    pytest.param("https://example.com:8443/A", "https://example.com/A", id="non-default-port"),
    pytest.param("http://example.com/A", "https://example.com/A", id="scheme-differs"),
]


@pytest.mark.parametrize(("left", "right"), _EQUIVALENT_URLS)
def test_url_identity_equivalent_pairs(left: str, right: str) -> None:
    assert normalize_url(left) == normalize_url(right)


@pytest.mark.parametrize(("left", "right"), _DISTINCT_URLS)
def test_url_identity_distinct_pairs(left: str, right: str) -> None:
    assert normalize_url(left) != normalize_url(right)


def test_cache_namespace_binds_mode_and_version() -> None:
    article = result_cache_key("https://example.com/A")
    metadata = result_cache_key("https://example.com/A", "metadata")

    assert article.startswith(f"{CACHE_NAMESPACE_V3}:")
    assert metadata.startswith(f"{CACHE_NAMESPACE_V3}:")
    assert article != metadata
    assert article != result_cache_key("https://example.com/A", "article", "future-version")
    assert "https://" not in article
    # Legacy lossy keys are neither produced nor served.
    assert not article.startswith("fetch:result:")
    assert normalize_url("https://example.com/A") == normalize_url("https://example.com/A")


@pytest.mark.asyncio
async def test_cache_roundtrip_preserves_provenance() -> None:
    cache = _mock_cache()
    assert cache.redis is not None
    redis_publish = cast(AsyncMock, cache.redis.eval)
    redis_get = cast(AsyncMock, cache.redis.get)
    store: dict[str, str] = {}

    async def fake_publish(script: str, count: int, key: str, index: str, *args) -> int:
        if key == cache.owner_index:
            assert count == 2 and index == cache.prune_cursor_key and not args
            return 0
        data, ttl = args
        assert count == 2 and index == cache.owner_index and ttl == 3600
        store[key] = data
        return 1

    async def fake_get(key: str, *args: Any, **kwargs: Any) -> str | None:
        return store.get(key)

    redis_publish.side_effect = fake_publish
    redis_get.side_effect = fake_get

    result = FetchResult(
        url="https://example.com/Case",
        content="Roundtrip content body that is comfortably long enough.",
        title="Case Title",
        strategy_used="direct",
        cached=False,
        fetch_time_ms=1.0,
        content_length=len("Roundtrip content body that is comfortably long enough."),
        source_url="https://example.com/Case",
        final_url="https://example.com/Case/final",
        content_type="text/html",
        extraction_version=EXTRACTION_VERSION_V1,
    )

    assert await cache.set("https://example.com/Case", result) is True
    set_call = redis_publish.await_args_list[0]
    assert set_call is not None
    key = set_call.args[2]
    assert key.startswith(f"{CACHE_NAMESPACE_V3}:")
    assert "https://" not in key
    assert key != result_cache_key("https://example.com/case")  # case preserved in keyed identity

    hit = await cache.get("https://example.com/Case")
    assert hit is not None
    assert hit.cached is True
    assert hit.content_type == "text/html"
    assert hit.source_url == "https://example.com/Case"
    assert hit.final_url == "https://example.com/Case/final"
    assert hit.extraction_version == EXTRACTION_VERSION_V1
    assert hit.content_length == len(hit.content)


@pytest.mark.asyncio
async def test_cache_never_serves_or_deletes_legacy_entries() -> None:
    cache = _mock_cache()
    assert cache.redis is not None
    redis_get = cast(AsyncMock, cache.redis.get)
    redis_delete = cast(AsyncMock, cache.redis.delete)
    redis_get.return_value = None

    hit = await cache.get("https://example.com/legacy")

    assert hit is None
    get_call = redis_get.await_args
    assert get_call is not None
    requested_key = get_call.args[0]
    assert requested_key.startswith(f"{CACHE_NAMESPACE_V3}:")
    redis_delete.assert_not_called()


@pytest.mark.asyncio
async def test_cached_content_over_extraction_bound_is_rejected(fetch_policy) -> None:
    service = _plain_text_service(fetch_policy)
    cache_get, _ = _service_cache_mocks(service)
    oversized = "x" * (1024 * 1024 + 1)
    cache_get.return_value = FetchResult(
        url="https://example.com/page",
        content=oversized,
        title="",
        strategy_used="direct",
        cached=True,
        fetch_time_ms=0.0,
        content_length=len(oversized),
        extraction_version=EXTRACTION_VERSION_V1,
    )

    result = await service.fetch("https://example.com/page")

    # Oversized cached content must be refetched, not served as complete.
    assert result is not None
    assert result.cached is False
    assert len(result.content) < len(oversized)


@pytest.mark.asyncio
async def test_cached_metadata_entry_served_without_article_validation(
    fetch_policy,
) -> None:
    """Metadata results are small by design and must not fail min-length checks."""
    service = FetchService(policy=fetch_policy, cache=_mock_service_cache())
    cache_get, cache_set = _service_cache_mocks(service)
    metadata_json = json.dumps(
        {"title": "T", "url": "https://example.com/page", "content_type": "text/html"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    cache_get.return_value = FetchResult(
        url="https://example.com/page",
        content=metadata_json,
        title="T",
        strategy_used="direct",
        cached=True,
        fetch_time_ms=0.0,
        content_length=len(metadata_json),
        extraction_version=EXTRACTION_VERSION_V1,
    )

    result = await service.fetch("https://example.com/page", extract="metadata")

    assert result is not None
    assert result.content == metadata_json
    cache_set.assert_not_called()


# ---------------------------------------------------------------------------
# Provenance through the real direct strategy (redirects)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_provenance_records_logical_urls(fetch_policy) -> None:
    request_log: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        if str(request.url).endswith("/start"):
            return httpx.Response(
                302,
                headers={"location": "https://Other.Example/Final?q=B"},
                request=request,
            )
        return httpx.Response(
            200,
            text="redirected page body with enough content for the policy",
            headers={"content-type": "text/plain"},
            request=request,
        )

    with (
        _patch_resolution(),
        _with_mock_transport(httpx.MockTransport(handler)),
    ):
        result = await DirectFetchStrategy(fetch_policy).fetch("https://example.com/start")

    assert result is not None
    assert result.source_url == "https://example.com/start"
    assert result.final_url == "https://Other.Example/Final?q=B"
    assert "93.184.216.34" not in result.final_url
    assert result.content == "redirected page body with enough content for the policy"
    assert result.content_length == len(result.content)


# ---------------------------------------------------------------------------
# Bounds configuration
# ---------------------------------------------------------------------------


def test_bounds_read_settings_field_when_present(monkeypatch) -> None:
    class _FakeSettings:
        web_snapshot_max_response_bytes = 123
        web_snapshot_max_content_bytes = 45

    monkeypatch.setattr(bounds_module, "get_settings", lambda: _FakeSettings())

    assert bounds_module.max_response_bytes() == 123
    assert bounds_module.max_content_bytes() == 45


def test_bounds_fail_closed_on_invalid_configuration(monkeypatch) -> None:
    def invalid_settings():
        raise ValueError("Invalid deployment bounds")

    monkeypatch.setattr(bounds_module, "get_settings", invalid_settings)
    with pytest.raises(ValueError):
        bounds_module.max_response_bytes()
    with pytest.raises(ValueError):
        bounds_module.max_content_bytes()


def test_bounds_follow_live_settings_field(tmp_path, monkeypatch) -> None:
    """Runtime accessors use the validated settings fields."""
    monkeypatch.chdir(tmp_path)  # isolate from the root dotenv
    settings = Settings()
    assert bounds_module.max_response_bytes() == settings.web_snapshot_max_response_bytes
    assert bounds_module.max_content_bytes() == settings.web_snapshot_max_content_bytes


def test_normalize_extract_mode_aliases() -> None:
    assert normalize_extract_mode("article") == "article"
    assert normalize_extract_mode("text") == "article"
    assert normalize_extract_mode("markdown") == "article"
    assert normalize_extract_mode("transcript") == "transcript"
    assert normalize_extract_mode("metadata") == "metadata"
    assert normalize_extract_mode("nonsense") == "article"
    assert normalize_extract_mode(None) == "article"
