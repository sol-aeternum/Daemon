from __future__ import annotations

import httpx
import pytest

from providers.xai_imagine import (
    ImageResult,
    VideoJob,
    VideoResult,
    XAIImagineClient,
    XAIImagineError,
)


class DummyResponse:
    def __init__(
        self, status_code: int, payload: object | None = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload


class DummyAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def __aenter__(self) -> DummyAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> object:
        self.calls.append(("POST", url, kwargs))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url: str, **kwargs: object) -> object:
        self.calls.append(("GET", url, kwargs))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(api_key: str = "test-xai-key") -> XAIImagineClient:
    client = XAIImagineClient()
    client.api_key = api_key
    client.base_url = "https://example.xai.test/v1"
    client.timeout = 1.0
    client.max_retries = 3
    return client


@pytest.mark.asyncio
async def test_generate_image_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [DummyResponse(200, payload={"url": "https://cdn.example/image.png"})]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    result = await client.generate_image("golden hour mountains", aspect_ratio="16:9")

    assert isinstance(result, ImageResult)
    assert result.url == "https://cdn.example/image.png"
    assert result.prompt == "golden hour mountains"
    assert result.aspect_ratio == "16:9"
    assert transport.calls[0][0] == "POST"
    assert transport.calls[0][1] == "https://example.xai.test/v1/images/generations"


@pytest.mark.asyncio
async def test_generate_image_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [
            DummyResponse(503, text="upstream unavailable"),
            DummyResponse(
                200, payload={"url": "https://cdn.example/retried-image.png"}
            ),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    result = await client.generate_image("retry please")

    assert result.url == "https://cdn.example/retried-image.png"
    assert len(transport.calls) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_generate_image_missing_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={"unexpected": True})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises((KeyError, XAIImagineError, ValueError)):
        await client.generate_image("broken payload")


@pytest.mark.asyncio
async def test_generate_video_success_with_duration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [DummyResponse(200, payload={"request_id": "req_123"})]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    result = await client.generate_video(
        "cinematic waterfall",
        duration_seconds=20,
        source_image_url="https://cdn.example/source.png",
    )

    assert isinstance(result, VideoJob)
    assert result.job_id == "req_123"
    assert result.duration_seconds == 15
    method, url, kwargs = transport.calls[0]
    assert method == "POST"
    assert url == "https://example.xai.test/v1/videos/generations"
    payload = kwargs["json"]
    assert isinstance(payload, dict)
    assert payload["duration_seconds"] == 15
    assert payload["source_image_url"] == "https://cdn.example/source.png"


@pytest.mark.asyncio
async def test_generate_video_auth_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(api_key="")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *args, **kwargs: DummyAsyncClient([])
    )

    with pytest.raises(XAIImagineError, match="XAI_API_KEY not configured"):
        await client.generate_video("blocked")


@pytest.mark.asyncio
async def test_poll_video_job_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [
            DummyResponse(
                200,
                payload={
                    "video": {
                        "status": "finished",
                        "url": {"generation": "https://cdn.example/video.mp4"},
                        "settings": {"prompt": ["foggy harbor"]},
                    }
                },
            )
        ]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    result = await client.poll_video_job("job_123")

    assert isinstance(result, VideoResult)
    assert result.url == "https://cdn.example/video.mp4"
    assert result.prompt == "foggy harbor"
    assert result.status == "finished"


@pytest.mark.asyncio
async def test_poll_video_job_pending_then_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [
            DummyResponse(200, payload={"video": {"status": "pending"}}),
            DummyResponse(
                200,
                payload={
                    "video": {
                        "status": "finished",
                        "url": {"generation": "https://cdn.example/final.mp4"},
                        "settings": {"prompt": ["city lights"]},
                    }
                },
            ),
        ]
    )
    sleeps: list[float] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    result = await client.poll_video_job("job_pending")

    assert result.url == "https://cdn.example/final.mp4"
    assert sleeps == [5]
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_poll_video_job_failed_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [DummyResponse(200, payload={"video": {"status": "failed"}})]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="Video generation failed"):
        await client.poll_video_job("job_failed")


@pytest.mark.asyncio
async def test_poll_video_job_expired_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [DummyResponse(200, payload={"video": {"status": "expired"}})]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="expired"):
        await client.poll_video_job("job_expired")


@pytest.mark.asyncio
async def test_poll_video_job_timeout_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [
            httpx.TimeoutException("slow"),
            httpx.TimeoutException("still slow"),
            httpx.TimeoutException("give up"),
        ]
    )
    sleeps: list[float] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(XAIImagineError, match="Request timeout after retries"):
        await client.poll_video_job("job_timeout")

    assert sleeps == [1.0, 2.1]


@pytest.mark.asyncio
async def test_generate_video_request_error_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    request = httpx.Request("POST", "https://example.xai.test/v1/videos/generations")
    transport = DummyAsyncClient(
        [
            httpx.RequestError("boom", request=request),
            httpx.RequestError("still boom", request=request),
            httpx.RequestError("last boom", request=request),
        ]
    )
    sleeps: list[float] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(XAIImagineError, match="Request error after retries"):
        await client.generate_video("storm over mountains")

    assert sleeps == [1.0, 2.1]
