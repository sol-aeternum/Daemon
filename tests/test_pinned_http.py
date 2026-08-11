"""Tests for SSRF-safe pinned HTTP transport."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from orchestrator.services.fetch.pinned_http import pinned_get
from orchestrator.tools.ssrf_guard import SsrfPolicyViolation, SsrfUnreachable, ValidatedUrl


def _client_context(get: AsyncMock) -> MagicMock:
    client = MagicMock()
    client.get = get
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_pinned_get_preserves_host_sni_and_disables_proxy() -> None:
    origin = "https://r.jina.ai/"
    request_url = f"{origin}{'a' * 3000}"
    validation = ValidatedUrl(
        url=origin,
        host="r.jina.ai",
        port=443,
        addresses=("8.8.8.8",),
    )
    response = httpx.Response(200, text="ok")
    get = AsyncMock(return_value=response)
    context = _client_context(get)

    with (
        patch(
            "orchestrator.services.fetch.pinned_http.validate_url_and_resolve_async",
            new=AsyncMock(return_value=validation),
        ) as validate,
        patch(
            "orchestrator.services.fetch.pinned_http.httpx.AsyncClient",
            return_value=context,
        ) as client_class,
    ):
        result = await pinned_get(
            request_url,
            validation_url=origin,
            headers={"Authorization": "Bearer test", "host": "evil.invalid"},
            timeout=10.0,
        )

    assert result is response
    validate.assert_awaited_once_with(
        origin,
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({443}),
        timeout=10.0,
    )
    client_call = client_class.call_args
    assert client_call is not None
    assert client_call.kwargs["timeout"] == 5.0
    assert client_call.kwargs["follow_redirects"] is False
    assert client_call.kwargs["trust_env"] is False
    request = get.await_args
    assert request is not None
    assert request.args[0] == f"https://8.8.8.8/{'a' * 3000}"
    assert request.kwargs["headers"] == {
        "Authorization": "Bearer test",
        "Host": "r.jina.ai",
    }
    assert request.kwargs["extensions"] == {"sni_hostname": "r.jina.ai"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_url",
    [
        "https://evil.example/path",
        "http://r.jina.ai/path",
        "https://r.jina.ai:8443/path",
        "https://user:pass@r.jina.ai/path",
    ],
)
async def test_pinned_get_rejects_request_outside_validated_origin(request_url: str) -> None:
    validation = ValidatedUrl(
        url="https://r.jina.ai/",
        host="r.jina.ai",
        port=443,
        addresses=("8.8.8.8",),
    )

    with (
        patch(
            "orchestrator.services.fetch.pinned_http.validate_url_and_resolve_async",
            new=AsyncMock(return_value=validation),
        ),
        patch("orchestrator.services.fetch.pinned_http.httpx.AsyncClient") as client_class,
        pytest.raises(SsrfPolicyViolation),
    ):
        await pinned_get(request_url, validation_url=validation.url)

    client_class.assert_not_called()


@pytest.mark.asyncio
async def test_pinned_get_treats_resolver_failure_as_unavailable() -> None:
    with (
        patch(
            "orchestrator.services.fetch.pinned_http.validate_url_and_resolve_async",
            new=AsyncMock(side_effect=SsrfUnreachable("DNS timed out")),
        ),
        patch("orchestrator.services.fetch.pinned_http.httpx.AsyncClient") as client_class,
    ):
        result = await pinned_get("https://r.jina.ai/", timeout=10.0)

    assert result is None
    client_class.assert_not_called()


@pytest.mark.asyncio
async def test_pinned_get_caps_addresses_and_shares_deadline() -> None:
    validation = ValidatedUrl(
        url="https://example.com/path",
        host="example.com",
        port=443,
        addresses=("8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4", "8.8.8.5"),
    )
    response = httpx.Response(200, text="ok")
    attempts: list[AsyncMock] = []
    client_kwargs: list[dict[str, object]] = []

    def make_client(**kwargs: object) -> MagicMock:
        client_kwargs.append(kwargs)
        address_number = len(client_kwargs)
        request = httpx.Request("GET", f"https://8.8.8.{address_number}/path")
        if address_number < 4:
            get = AsyncMock(side_effect=httpx.ConnectError("unreachable", request=request))
        else:
            get = AsyncMock(return_value=response)
        attempts.append(get)
        return _client_context(get)

    monotonic = MagicMock(side_effect=[100.0, 100.5, 102.0, 104.0, 106.0])
    with (
        patch(
            "orchestrator.services.fetch.pinned_http.validate_url_and_resolve_async",
            new=AsyncMock(return_value=validation),
        ),
        patch(
            "orchestrator.services.fetch.pinned_http.httpx.AsyncClient",
            side_effect=make_client,
        ),
        patch("orchestrator.services.fetch.pinned_http._monotonic", monotonic),
    ):
        result = await pinned_get(validation.url, timeout=10.0)

    assert result is response
    assert len(attempts) == 4
    attempted_urls: list[str] = []
    for attempt in attempts:
        attempt_call = attempt.await_args
        assert attempt_call is not None
        attempted_urls.append(attempt_call.args[0])
    assert attempted_urls == [
        "https://8.8.8.1/path",
        "https://8.8.8.2/path",
        "https://8.8.8.3/path",
        "https://8.8.8.4/path",
    ]
    assert [kwargs["timeout"] for kwargs in client_kwargs] == [5.0, 5.0, 5.0, 4.0]
