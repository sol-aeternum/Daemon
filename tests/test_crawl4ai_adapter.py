"""Synthetic 0.9.5 wire contracts; no upstream imports, listeners or live DNS."""

from __future__ import annotations

import asyncio
import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from orchestrator.services.fetch import extract
from orchestrator.services.fetch.models import (
    EXTRACTION_VERSION_V1,
    FetchExtractionError,
    FetchPageTooLargeError,
    FetchPolicy,
)
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.fetch.strategies import crawl4ai as adapter
from orchestrator.tools import ssrf_guard
from orchestrator.tools.ssrf_guard import SsrfPolicyViolation

URL = "https://example.com/article"
HTML = (
    "<html><head><title>A &amp; B</title></head><body><article><h1>A real article</h1>"
    + "<p>This is a synthetic article with useful readable paragraphs and context.</p>" * 8
    + "</article></body></html>"
)


def page(**overrides):
    # models.py:120-159 and :279-281 at pinned upstream commit 838869bd.
    return {
        "url": URL,
        "success": True,
        "status_code": 200,
        "html": HTML,
        "markdown": {
            "raw_markdown": "UPSTREAM representation must not be used",
            "markdown_with_citations": "",
            "references_markdown": "",
            "fit_markdown": None,
            "fit_html": None,
        },
        "response_headers": {"content-type": "text/html"},
        "redirected_url": None,
        **overrides,
    }


def envelope(item=None):
    # api.py:898-903: envelope success isn't per-result success.
    return {"success": True, "results": [page() if item is None else item]}


@pytest.fixture
def wire(monkeypatch):
    state = SimpleNamespace(
        data=envelope(), requests=[], clients=[], status=200, stream=None, error=None
    )
    monkeypatch.setattr(
        adapter,
        "get_settings",
        lambda: SimpleNamespace(
            crawl4ai_url="http://crawl4ai:11235", daemon_fetch_user_agent="Daemon/synthetic"
        ),
    )
    # Exercise the real static/DNS admission code; resolver answers are synthetic.
    monkeypatch.setattr(ssrf_guard, "_resolve_and_check", lambda *_: ("93.184.216.34",))
    monkeypatch.setattr(adapter, "SEM", asyncio.Semaphore(1))
    real_client = httpx.AsyncClient

    async def handle(request):
        state.requests.append(request)
        if state.error:
            raise state.error
        if state.stream is not None:
            return httpx.Response(state.status, stream=state.stream, headers=state.headers)
        return httpx.Response(state.status, json=state.data)

    def client(**kwargs):
        state.clients.append(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    return state


def strategy():
    return adapter.Crawl4AIStrategy(FetchPolicy(min_content_length=10, error_signatures=[]))


@pytest.mark.asyncio
async def test_request_contract_html_title_and_extraction(wire):
    result = await strategy().fetch(URL)
    assert result is not None
    assert result.content == HTML
    assert result.title == "A & B"
    assert result.url == result.source_url == result.final_url == URL
    assert result.strategy_used == "crawl4ai"
    assert not result.cached
    request = wire.requests[0]
    assert str(request.url) == "http://crawl4ai:11235/crawl"
    assert request.method == "POST"
    payload = json.loads(request.content)
    assert payload == {
        "urls": [URL],
        "browser_config": {
            "browser_type": "chromium",
            "headless": True,
            "accept_downloads": False,
            "enable_stealth": False,
            "user_agent": "Daemon/synthetic",
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
    assert wire.clients[0]["trust_env"] is False
    assert wire.clients[0]["follow_redirects"] is False
    service = FetchService(policy=strategy().policy)
    extracted = service._apply_extraction(result, "article")
    assert extracted is not None
    assert "synthetic article" in extracted.content
    assert "UPSTREAM" not in extracted.content and "<html>" not in extracted.content
    assert extracted.extraction_version == EXTRACTION_VERSION_V1
    assert extracted.content_length == len(extracted.content)


@pytest.mark.asyncio
async def test_metadata_uses_existing_extraction(wire):
    result = await strategy().fetch(URL)
    assert result is not None
    result.title = ""  # Also cover the shared metadata title fallback.
    result = FetchService(policy=strategy().policy)._apply_extraction(result, "metadata")
    assert result is not None
    assert json.loads(result.content) == {"title": "A & B", "url": URL, "content_type": "text/html"}


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "secret",
        {"success": 1, "results": [page()]},
        {"results": [page()]},
        {"success": False, "results": [page()]},
        {"success": True, "result": [page()]},
        {"success": True, "results": {}},
        {"success": True, "results": []},
        {"success": True, "results": [page(), page()]},
        {"success": True, "results": [None]},
    ],
)
@pytest.mark.asyncio
async def test_reject_envelope_shape_and_cardinality(wire, data):
    wire.data = data
    assert await strategy().fetch(URL) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"success": 1},
        {"success": "true"},
        {"success": False},
        {"status_code": True},
        {"status_code": "200"},
        {"status_code": None},
        {"status_code": 403},
        {"status_code": 500},
        {"status_code": 302},
        {"url": "https://other.example/"},
        {"html": {}},
        {"html": ""},
        {"markdown": "not structured"},
        {"markdown": {"raw_markdown": "missing required fields"}},
        {
            "markdown": {
                "raw_markdown": {},
                "markdown_with_citations": "",
                "references_markdown": "",
            }
        },
        {
            "markdown": {
                "raw_markdown": "",
                "markdown_with_citations": "",
                "references_markdown": "",
                "fit_html": [],
            }
        },
        {"response_headers": []},
        {"response_headers": {"content-type": {}}},
        {"redirected_url": {}},
        {"redirected_url": ""},
    ],
)
@pytest.mark.asyncio
async def test_reject_result_types_and_failures(wire, changes):
    wire.data = envelope(page(**changes))
    assert await strategy().fetch(URL) is None


@pytest.mark.asyncio
async def test_absent_or_null_markdown_does_not_replace_html(wire):
    for item in (page(markdown=None), page()):
        item.pop("markdown", None)
        wire.data = envelope(item)
        result = await strategy().fetch(URL)
        assert result is not None and result.content == HTML
    wire.data = envelope(page(markdown=None))
    assert await strategy().fetch(URL) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/",
        "http://169.254.169.254/",
        "file:///etc/hosts",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://2130706433/",
    ],
)
@pytest.mark.asyncio
async def test_unsafe_target_never_submitted(wire, url):
    with pytest.raises(SsrfPolicyViolation):
        await strategy().fetch(url)
    assert not wire.requests


@pytest.mark.asyncio
async def test_blocked_domain_and_private_dns_never_submitted(wire, monkeypatch):
    blocked = adapter.Crawl4AIStrategy(FetchPolicy(blocked_domains=["EXAMPLE.com."]))
    with pytest.raises(SsrfPolicyViolation):
        await blocked.fetch(URL)
    monkeypatch.setattr(
        ssrf_guard,
        "_resolve_and_check",
        lambda *_: (_ for _ in ()).throw(SsrfPolicyViolation("synthetic private DNS")),
    )
    with pytest.raises(SsrfPolicyViolation):
        await strategy().fetch(URL)
    assert not wire.requests


@pytest.mark.asyncio
async def test_resolver_unavailable_is_not_sent(wire, monkeypatch):
    async def unavailable(*_, **__):
        raise ssrf_guard.SsrfUnreachable("secret URL")

    monkeypatch.setattr(adapter, "validate_url_and_resolve_async", unavailable)
    assert await strategy().fetch(URL) is None
    assert not wire.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_reported_redirect_is_admitted_but_not_claimed_as_remote_proof(wire, status):
    wire.data = envelope(
        page(
            status_code=status,
            redirected_status_code=200,
            redirected_url="https://example.org/final",
        )
    )
    result = await strategy().fetch(URL)
    assert result is not None and result.final_url == "https://example.org/final"
    wire.data = envelope(page(redirected_url="http://127.0.0.1/", redirected_status_code=200))
    with pytest.raises(SsrfPolicyViolation):
        await strategy().fetch(URL)


@pytest.mark.parametrize(
    "initial,final,final_url",
    [
        (301, None, "https://example.org/final"),
        (200, None, "https://example.org/final"),
        (301, 200, None),
        (301, 200, ""),
        (300, 200, "https://example.org/final"),
        (304, 200, "https://example.org/final"),
        (305, 200, "https://example.org/final"),
        (306, 200, "https://example.org/final"),
        (301, 301, "https://example.org/final"),
        (200, 404, "https://example.org/final"),
        (302, 500, "https://example.org/final"),
        (200, True, URL),
        (200, "200", URL),
        (200, 0, URL),
        (403, 200, URL),
    ],
)
@pytest.mark.asyncio
async def test_first_hop_success_cannot_hide_final_failure(wire, initial, final, final_url):
    wire.data = envelope(
        page(status_code=initial, redirected_status_code=final, redirected_url=final_url)
    )
    assert await strategy().fetch(URL) is None


@pytest.mark.parametrize(
    "base",
    [
        "file:///tmp/service",
        "http://",
        "http://user:secret@host/",
        "http://host?key=secret",
        "http://host#fragment",
        "http://host/other",
        "http://host:0",
        "http://host:65536",
        "http://host?",
        "http://host#",
        " http://host",
        "http://host\n",
    ],
)
def test_operator_endpoint_rejection_is_opaque(wire, monkeypatch, base):
    monkeypatch.setattr(adapter, "get_settings", lambda: SimpleNamespace(crawl4ai_url=base))
    with pytest.raises(ValueError, match="^Invalid Crawl4AI service origin$"):
        strategy()


@pytest.mark.asyncio
async def test_endpoint_captured_once_no_environment_proxy_or_redirect(wire, monkeypatch):
    client = strategy()
    monkeypatch.setenv("HTTP_PROXY", "http://unrelated.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://unrelated.invalid:9999")
    monkeypatch.setattr(adapter, "get_settings", lambda: (_ for _ in ()).throw(AssertionError()))
    wire.status = 302
    assert await client.fetch(URL) is None
    assert len(wire.requests) == 1
    assert str(wire.requests[0].url) == "http://crawl4ai:11235/crawl"


@pytest.mark.asyncio
async def test_failure_categories_and_logs_never_echo_content(wire, caplog):
    caplog.set_level("INFO", logger=adapter.__name__)
    for status, item, category in [
        (503, page(), "service_http_error"),
        (
            200,
            page(
                success=False,
                status_code=None,
                markdown=None,
                error_message="Blocked by anti-bot protection: secret",
            ),
            "target_result_error",
        ),
        (200, page(status_code=403), "target_http_error"),
        (200, page(response_headers={"CF-Mitigated": "challenge"}), "challenge_evidence"),
    ]:
        caplog.clear()
        wire.status, wire.data = status, envelope(item)
        assert await strategy().fetch(URL) is None
        assert category in caplog.text
        assert "secret" not in caplog.text and URL not in caplog.text and HTML not in caplog.text
        assert ("challenge_evidence" in caplog.text) == (category == "challenge_evidence")
    # A successful ordinary page mentioning a challenge/403 isn't evidence.
    wire.data = envelope(page(html=HTML + "403 captcha access denied"))
    assert await strategy().fetch(URL) is not None
    wire.error = RuntimeError("secret raw exception " + URL)
    caplog.clear()
    assert await strategy().fetch(URL) is None
    assert "secret" not in caplog.text and URL not in caplog.text


class Stream(httpx.AsyncByteStream):
    def __init__(self, content=b"", *, stall=False):
        self.content, self.stall, self.closed = content, stall, False
        self.entered = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        if self.stall:
            await asyncio.Event().wait()
        yield self.content

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_decoded_gzip_bound_closes_stream(wire, monkeypatch):
    # Exercise production bounded decoder, not a mocked reader.
    from orchestrator.services.fetch.strategies import direct

    monkeypatch.setattr(direct, "max_response_bytes", lambda: 100)
    wire.stream = Stream(gzip.compress(b"x" * 10000))
    wire.headers = {"content-encoding": "gzip"}
    with pytest.raises(FetchPageTooLargeError):
        await strategy().fetch(URL)
    assert wire.stream.closed


@pytest.mark.asyncio
async def test_html_utf8_bound(wire, monkeypatch):
    monkeypatch.setattr(adapter, "max_response_bytes", lambda: 100)
    wire.data = envelope(page(html="\u00e9" * 51))
    with pytest.raises(FetchPageTooLargeError):
        await strategy().fetch(URL)


@pytest.mark.asyncio
async def test_queue_and_stream_deadline_and_cancellation(wire, monkeypatch):
    monkeypatch.setattr(adapter, "_DEADLINE_SECONDS", 0.03)
    await adapter.SEM.acquire()
    try:
        assert await strategy().fetch(URL) is None
        assert not wire.requests
    finally:
        adapter.SEM.release()
    wire.stream, wire.headers = Stream(stall=True), {}
    assert await strategy().fetch(URL) is None
    assert wire.stream.closed and not adapter.SEM.locked()
    monkeypatch.setattr(adapter, "_DEADLINE_SECONDS", 5)
    wire.stream = Stream(stall=True)
    task = asyncio.create_task(strategy().fetch(URL))
    await asyncio.wait_for(wire.stream.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.stream.closed and not adapter.SEM.locked()


@pytest.mark.asyncio
async def test_shared_deadline_includes_dns(wire, monkeypatch):
    monkeypatch.setattr(adapter, "_DEADLINE_SECONDS", 0.02)

    async def stalled(*_, **__):
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter, "validate_url_and_resolve_async", stalled)
    assert await strategy().fetch(URL) is None
    assert not wire.requests


@pytest.mark.asyncio
async def test_expiry_after_synchronous_parse_rejects_success(wire, monkeypatch):
    clock = SimpleNamespace(monotonic=adapter.time.monotonic)
    original_loads = json.loads
    original_time = adapter.time.monotonic

    def late_parse(body):
        data = original_loads(body)
        clock.monotonic = lambda: original_time() + 60
        return data

    monkeypatch.setattr(adapter, "time", clock)
    monkeypatch.setattr(adapter, "json", SimpleNamespace(loads=late_parse))
    assert await strategy().fetch(URL) is None


@pytest.mark.parametrize(
    "body,headers,extraction_error",
    [
        (b"not JSON containing secret", {}, False),
        (b"not gzip containing secret", {"content-encoding": "gzip"}, False),
        (b"not supported", {"content-encoding": "br"}, True),
        (gzip.compress(b"{}")[:-2], {"content-encoding": "gzip"}, True),
    ],
)
@pytest.mark.asyncio
async def test_malformed_stream_closes_without_leaking(
    wire, body, headers, extraction_error, caplog
):
    caplog.set_level("INFO", logger=adapter.__name__)
    wire.stream, wire.headers = Stream(body), headers
    if extraction_error:
        with pytest.raises(FetchExtractionError, match="^extraction_failed:"):
            await strategy().fetch(URL)
    else:
        assert await strategy().fetch(URL) is None
        assert "Crawl4AI service_error" in caplog.text
    assert wire.stream.closed
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_service_error_or_redirect_does_not_read_body(wire):
    for status in (301, 302, 307, 400, 500, 503):
        wire.status = status
        wire.stream, wire.headers = Stream(stall=True), {"location": "http://other.invalid/"}
        assert await strategy().fetch(URL) is None
        assert wire.stream.closed and not wire.stream.entered.is_set()


@pytest.mark.asyncio
async def test_client_serializes_calls_and_releases_on_failure(wire):
    wire.stream, wire.headers = Stream(stall=True), {}
    first = asyncio.create_task(strategy().fetch(URL))
    await asyncio.wait_for(wire.stream.entered.wait(), 1)
    second = asyncio.create_task(strategy().fetch(URL))
    await asyncio.sleep(0)
    assert len(wire.requests) == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    wire.stream = None
    assert await asyncio.wait_for(second, 1) is not None
    assert len(wire.requests) == 2


def test_shared_extraction_errors_redacted(monkeypatch, caplog):
    def fail(*_, **__):
        raise ValueError("secret page payload")

    monkeypatch.setattr(extract.trafilatura, "extract", fail)
    assert extract.html_to_markdown(HTML) is None
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_production_selection_disabled_and_extraction_failure_honest(wire, monkeypatch):
    service = FetchService(policy=strategy().policy)
    assert service.crawl4ai_strategy is None
    assert [name for name, _ in service._default_strategy_chain()] == ["direct"]
    service.direct_strategy = MagicMock(fetch=AsyncMock(return_value=None))
    assert await service.fetch(URL, use_cache=False) is None
    assert not wire.requests
    result = await strategy().fetch(URL)
    assert result is not None
    monkeypatch.setattr(extract, "html_to_markdown", lambda _: None)
    with pytest.raises(FetchExtractionError):
        service._apply_extraction(result, "article")
