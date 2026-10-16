"""#373: synthetic direct -> service -> tool error-contract regressions.

No external requests, database, real cache or snapshot writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from orchestrator.services.fetch.cache import FetchCache
from orchestrator.services.fetch.models import FetchChallengeError, FetchContentError, FetchPolicy
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.web_snapshots import WebSnapshotStore
from orchestrator.tools.ssrf_guard import SsrfPolicyViolation, SsrfUnreachable, ValidatedUrl
from orchestrator.tools.web_fetch import WebFetchTool

URL = "https://example.com/article?private=synthetic-marker"
BODY = "Synthetic private body and header marker, never returned on failure. " * 20


@pytest.fixture
def pipeline():
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    service = FetchService(policy=FetchPolicy(), cache=cast(FetchCache, cache))
    store = SimpleNamespace(
        settings=SimpleNamespace(web_snapshot_max_new_per_turn=2),
        find_latest=AsyncMock(return_value=None),
        create=AsyncMock(),
    )
    tool = WebFetchTool(cast(WebSnapshotStore, store), uuid.uuid4(), uuid.uuid4())
    tool._fetch_service = service
    return service, cache, tool, store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "code"),
    [
        (403, {"cf-mitigated": "challenge", "server": "cloudflare"}, "upstream_challenge"),
        (200, {"CF-Mitigated": " Challenge "}, "upstream_challenge"),
        (503, {"cf-mitigated": "challenge"}, "upstream_challenge"),
        (403, {}, "access_denied"),
        (403, {"server": "cloudflare"}, "access_denied"),
        (403, {"cf-mitigated": "not-challenge"}, "access_denied"),
        (401, {}, "access_denied"),
        (407, {}, "access_denied"),
        (429, {}, "access_denied"),
        (404, {}, "http_error"),
        (500, {}, "http_error"),
        (503, {}, "http_error"),
        (304, {}, "http_error"),
        (302, {}, "http_error"),
        (302, {"location": "/next", "cf-mitigated": "challenge"}, "upstream_challenge"),
    ],
)
async def test_http_categories_reach_tool_without_content_or_publication(
    pipeline, status, headers, code, caplog
):
    service, cache, tool, store = pipeline
    caplog.set_level(logging.DEBUG, logger="orchestrator.services.fetch")
    response = httpx.Response(
        status,
        headers={"content-type": "text/html", "x-private": BODY, **headers},
        text=BODY,
        request=httpx.Request("GET", "https://93.184.216.34/article"),
    )
    with (
        patch(
            "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
            new=AsyncMock(return_value=ValidatedUrl(URL, "example.com", 443, ("93.184.216.34",))),
        ) as validate,
        patch("httpx.AsyncClient.send", new=AsyncMock(return_value=response)) as send,
        patch.object(service, "_apply_extraction") as extract,
    ):
        result = await tool.execute(url=URL)

    assert json.loads(result) == {"error": code}
    assert result == json.dumps({"error": code})
    validate.assert_awaited_once()
    send.assert_awaited_once()  # no retry/follow-up on denial or challenge
    extract.assert_not_called()
    cache.get.assert_not_awaited()
    cache.set.assert_not_awaited()
    store.create.assert_not_awaited()
    assert tool._created == 0
    assert response.is_closed
    assert BODY not in caplog.text
    assert "synthetic-marker" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["dns", "empty_addresses", "connect", "read", "timeout"])
async def test_transport_category_is_not_a_challenge(pipeline, failure, caplog):
    service, cache, tool, store = pipeline
    caplog.set_level(logging.DEBUG, logger="orchestrator.services.fetch")
    validate = AsyncMock(return_value=ValidatedUrl(URL, "example.com", 443, ("93.184.216.34",)))
    if failure == "dns":
        validate.side_effect = SsrfUnreachable(BODY)
    elif failure == "empty_addresses":
        validate.return_value = ValidatedUrl(URL, "example.com", 443, ())
    error = {
        "connect": httpx.ConnectError(BODY),
        "read": httpx.ReadError(BODY),
        "timeout": TimeoutError(BODY),
    }.get(failure, httpx.ConnectError(BODY))
    with (
        patch(
            "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
            new=validate,
        ),
        patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=error)) as send,
    ):
        assert json.loads(await tool.execute(url=URL)) == {"error": "transport_error"}
    if failure in {"dns", "empty_addresses"}:
        send.assert_not_awaited()
    cache.set.assert_not_awaited()
    store.create.assert_not_awaited()
    assert BODY not in caplog.text


@pytest.mark.asyncio
async def test_clean_success_retains_existing_result_and_cache_contract(pipeline):
    service, cache, _, _ = pipeline
    response = httpx.Response(
        200,
        text=BODY,
        headers={"content-type": "text/plain", "server": "cloudflare"},
        request=httpx.Request("GET", "https://93.184.216.34/article"),
    )
    with (
        patch(
            "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
            new=AsyncMock(return_value=ValidatedUrl(URL, "example.com", 443, ("93.184.216.34",))),
        ),
        patch("httpx.AsyncClient.send", new=AsyncMock(return_value=response)),
    ):
        result = await service.fetch(URL)
    assert result is not None
    assert result.content == BODY
    assert result.url == result.source_url == result.final_url == URL
    assert result.strategy_used == "direct"
    assert result.content_type == "text/plain"
    assert result.content_length == len(BODY)
    assert result.extraction_version == "web-reader-v1"
    assert result.cached is False
    cache.set.assert_awaited_once_with(URL, result, extract="article")


@pytest.mark.asyncio
async def test_challenge_terminates_even_an_injected_strategy_chain(pipeline):
    service, cache, _, _ = pipeline
    direct = SimpleNamespace(fetch=AsyncMock(side_effect=FetchChallengeError()))
    later = SimpleNamespace(fetch=AsyncMock())
    with patch.object(
        service, "_default_strategy_chain", return_value=(("direct", direct), ("later", later))
    ):
        with pytest.raises(FetchContentError, match="upstream_challenge"):
            await service.fetch(URL)
    later.fetch.assert_not_awaited()
    cache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_violation_remains_separate_and_cancellation_propagates(pipeline):
    service, _, tool, store = pipeline
    with (
        patch(
            "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
            new=AsyncMock(side_effect=SsrfPolicyViolation(BODY)),
        ),
        patch("httpx.AsyncClient.send", new=AsyncMock()) as send,
    ):
        assert json.loads(await tool.execute(url=URL)) == {"error": "fetch_failed"}
        send.assert_not_awaited()
    store.create.assert_not_awaited()
    with patch(
        "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await service.fetch(URL, use_cache=False)
