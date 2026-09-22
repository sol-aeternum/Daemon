from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from orchestrator.config import Settings
from orchestrator.services.fetch.models import FetchPolicy
from orchestrator.services.fetch.service import FetchService
from orchestrator.tools.ssrf_guard import ValidatedUrl


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [None, "ExampleBot (+https://example.org/contact)", "Mozilla/5.0 AuthorizedIntegration"],
)
async def test_fetch_identity_survives_retries_redirects_and_repeat_fetches(
    monkeypatch, tmp_path, caplog, override
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAEMON_FETCH_USER_AGENT", raising=False)
    if override is not None:
        monkeypatch.setenv("DAEMON_FETCH_USER_AGENT", override)
    caplog.set_level(logging.WARNING)

    # Exercise the real service constructor so the deployment setting must reach
    # its direct strategy, not just a strategy configured by the test itself.
    strategy = FetchService(policy=FetchPolicy(min_content_length=10)).direct_strategy
    assert strategy is not None
    start = "https://example.com/article"
    target = "https://other.example/article"
    redirect = httpx.Response(302, headers={"location": target})
    success = httpx.Response(
        200,
        headers={"content-type": "text/plain"},
        text="This response contains enough content to pass the fetch policy.",
        request=httpx.Request("GET", target),
    )
    with (
        patch(
            "orchestrator.services.fetch.strategies.direct.validate_url_and_resolve_async",
            new=AsyncMock(
                side_effect=[
                    ValidatedUrl(start, "example.com", 443, ("8.8.8.8", "8.8.4.4")),
                    ValidatedUrl(target, "other.example", 443, ("1.1.1.1",)),
                    ValidatedUrl(start, "example.com", 443, ("8.8.4.4",)),
                ]
            ),
        ),
        patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(
                side_effect=[
                    httpx.ConnectError("first address unavailable"),
                    redirect,
                    success,
                    success,
                ]
            ),
        ) as get,
    ):
        assert await strategy.fetch(start) is not None
        assert await strategy.fetch(start) is not None

    expected = override or "Daemon (+https://github.com/sol-aeternum/Daemon)"
    assert get.await_count == 4
    assert [call.kwargs["headers"]["User-Agent"] for call in get.await_args_list] == [expected] * 4
    assert [call.kwargs["headers"]["Host"] for call in get.await_args_list] == [
        "example.com",
        "example.com",
        "other.example",
        "example.com",
    ]
    audit_messages = [
        message for message in caplog.messages if "DAEMON_FETCH_USER_AGENT" in message
    ]
    assert len(audit_messages) == (1 if override else 0)
    if override:
        assert override not in audit_messages[0]


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        " ",
        " leading",
        "Daemon\n",
        "Daemon\r\nInjected: yes",
        "Daemon\0",
        "Dæmon",
        "Daemon\x7f",
        "x" * 513,
    ],
)
def test_user_agent_rejects_invalid_header_values(invalid) -> None:
    with pytest.raises(ValidationError, match="daemon_fetch_user_agent"):
        Settings(daemon_fetch_user_agent=invalid)
