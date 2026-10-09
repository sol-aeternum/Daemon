"""Source-site defense in depth, independently of the managed sink."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from orchestrator.services.fetch.cache import FetchCache
from orchestrator.services.fetch.service import FetchService
from orchestrator.subagents import image

SECRET = "PRIVATE-SOURCE-PROMPT-ACCOUNT-RESPONSE"
URL = f"https://example.invalid/private/{SECRET}?token={SECRET}"


@pytest.mark.asyncio
async def test_fetch_failure_logs_no_requested_url_or_exception(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    service = FetchService()

    class Failure:
        async def fetch(self, url: str):
            raise RuntimeError(SECRET)

    failed = Failure()
    assert await service._attempt_strategy(URL, URL, "article", "direct", failed) is None
    assert "exception" in caplog.text.lower()
    assert SECRET not in caplog.text
    assert URL not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_consolidation_rejected_output_is_not_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    from orchestrator.memory import consolidation

    caplog.set_level(logging.DEBUG)
    provider = SimpleNamespace(timeout_s=1, base_url="", api_key=None, extra_headers={})
    settings = SimpleNamespace(get_provider_config=lambda _: provider, daemon_encryption_key=None)
    monkeypatch.setattr(consolidation, "get_settings", lambda: settings)
    monkeypatch.setattr(consolidation, "ContentEncryption", lambda _: object())
    monkeypatch.setattr(
        consolidation,
        "guarded_completion",
        AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="encrypted " + SECRET))]
            )
        ),
    )
    cluster = consolidation.MemoryCluster("fixture", [{"id": uuid.uuid4(), "content": SECRET}])
    assert await consolidation.consolidate_cluster(cluster, AsyncMock(), uuid.uuid4()) == []
    assert "rejecting" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_dreaming_job_logs_omit_account_and_error_preserve_result(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    from orchestrator.config import Settings
    from orchestrator.memory.store import MemoryStore
    from orchestrator.worker import jobs

    @asynccontextmanager
    async def scope(*args, **kwargs):
        yield

    caplog.set_level(logging.DEBUG)
    uid = uuid.uuid4()
    monkeypatch.setattr(jobs, "account_compute", scope)
    monkeypatch.setattr(jobs, "run_dreaming", AsyncMock(side_effect=RuntimeError(SECRET)))
    store = AsyncMock(spec=MemoryStore)
    result = await jobs.run_dreaming_job(
        {"store": store, "settings": Settings(dreaming_enabled=True)},
        user_id=str(uid),
    )
    assert result["dream_runs_failed"] == 1
    assert result["errors"] == [f"{uid}: {SECRET}"]
    assert str(uid) not in caplog.text
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_fetch_rejection_logs_no_url(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    service = FetchService()
    assert await service.fetch(f"ftp://example.invalid/{SECRET}") is None
    assert "unsupported" in caplog.text.lower()
    assert SECRET not in caplog.text


def test_corrupt_cache_payload_does_not_log_url_or_payload(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    assert FetchCache()._deserialize_result(SECRET, URL) is None
    assert "deserialize" in caplog.text.lower()
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_daemon_upstream_failure_logs_no_content_and_preserves_error(
    caplog: pytest.LogCaptureFixture,
):
    from orchestrator.daemon import stream_with_keepalives

    async def frames():
        yield "first"
        raise RuntimeError(SECRET)

    caplog.set_level(logging.DEBUG)
    iterator = stream_with_keepalives(frames(), 1)
    assert await anext(iterator) == "first"
    with pytest.raises(RuntimeError, match="SSE upstream generator failed") as failure:
        await anext(iterator)
    assert str(failure.value.__cause__) == SECRET
    assert "SSE upstream generator raised" in caplog.text
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,embedded_error", [(200, False), (200, True), (503, True)])
async def test_image_provider_logs_no_prompt_headers_or_response(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    embedded_error: bool,
):
    caplog.set_level(logging.DEBUG)
    data = {
        "choices": [{"message": {"content": SECRET, "images": [{"image_url": {"url": URL}}]}}],
        "private_extra": SECRET,
    }
    if embedded_error:
        data["error"] = {"message": SECRET}
    response = httpx.Response(
        status, json=data, headers={"X-Private": SECRET}, request=httpx.Request("POST", URL)
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(image.httpx, "AsyncClient", lambda: client)
    provider = image.OpenRouterImageProvider(SECRET, "https://fixture.invalid", "fixture")
    if embedded_error:
        with pytest.raises(RuntimeError, match=SECRET):
            await provider.generate_image(SECRET, "small")
    else:
        result = await provider.generate_image(SECRET, "small")
        assert result["url"] == URL
    # P0.3 changes logs only, not the request or caller-visible provider result.
    assert client.post.call_args.kwargs["json"]["messages"][0]["content"] == SECRET
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
