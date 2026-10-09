from __future__ import annotations

import asyncio
import uuid

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
    def __init__(self, status_code: int, payload: object | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload


class DummyAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses: list[object] = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def __aenter__(self) -> DummyAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> object:
        self.calls.append(("POST", url, kwargs))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
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
    # Reference is exposed as a python-internal property only.
    assert isinstance(result.client_reference, str)
    uuid.UUID(result.client_reference)  # opaque UUID, valid

    # Wire payload shape is unchanged: no reference field leaks into the request.
    payload = transport.calls[0][2]["json"]
    assert isinstance(payload, dict)
    assert set(payload.keys()) == {"prompt", "aspect_ratio", "model"}

    # Serialization is unchanged: no reference field.
    serialized_keys = set(result.model_dump().keys())
    assert serialized_keys == {"url", "prompt", "model", "aspect_ratio"}
    assert "client_reference" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_generate_image_no_retry_on_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(503, text="upstream unavailable")])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(XAIImagineError, match="API error; not retried: 503") as exc_info:
        await client.generate_image("retry please")

    # Exactly one POST, no backoff sleep, and the failure retains its reference.
    assert len(transport.calls) == 1
    assert sleeps == []
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_image_429_no_resubmit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(429, text="rate limited")])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_image("again")

    assert len(transport.calls) == 1
    assert sleeps == []
    assert exc_info.value.client_reference is not None
    uuid.UUID(exc_info.value.client_reference)

    # Distinct submissions get distinct references (no shared singleton).
    transport._responses = [DummyResponse(200, payload={"url": "https://cdn.example/x.png"})]
    second = await client.generate_image("again")
    assert second.client_reference != exc_info.value.client_reference


@pytest.mark.asyncio
async def test_generate_image_timeout_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([httpx.TimeoutException("slow")])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(
        XAIImagineError, match="Request timeout: outcome unconfirmed; not retried"
    ) as exc_info:
        await client.generate_image("ashen dawn")

    assert len(transport.calls) == 1
    assert sleeps == []
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_image_request_error_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    request = httpx.Request("POST", "https://example.xai.test/v1/images/generations")
    transport = DummyAsyncClient([httpx.RequestError("boom", request=request)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="Request error; not retried") as exc_info:
        await client.generate_image("ashen dusk")

    assert len(transport.calls) == 1
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_image_malformed_response_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={"unexpected": True})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_image("broken payload")

    assert len(transport.calls) == 1
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_image_reference_created_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reference is generated strictly before the POST is issued."""
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(502, text="bad gateway")])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    events: list[str] = []
    real_uuid4 = uuid.uuid4

    def fake_uuid4() -> uuid.UUID:
        events.append("reference-generated")
        return real_uuid4()

    monkeypatch.setattr("providers.xai_imagine.uuid.uuid4", fake_uuid4)

    original_post = transport.post

    def post_and_record(url: str, **kwargs: object) -> object:
        events.append("submit")
        return original_post(url, **kwargs)

    transport.post = post_and_record  # type: ignore[method-assign]

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_image("early reference")

    assert events == ["reference-generated", "submit"]
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_video_success_with_duration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={"request_id": "req_123"})])
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
    assert set(payload.keys()) == {"prompt", "duration_seconds", "source_image_url"}

    # Reference exposed internally; serialization unchanged.
    assert isinstance(result.client_reference, str)
    assert set(result.model_dump().keys()) == {
        "job_id",
        "prompt",
        "duration_seconds",
        "source_image_url",
    }
    assert "client_reference" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_generate_video_auth_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(api_key="")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyAsyncClient([]))

    with pytest.raises(XAIImagineError, match="XAI_API_KEY not configured"):
        await client.generate_video("blocked")


@pytest.mark.asyncio
async def test_generate_video_no_retry_on_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(503, text="upstream unavailable")])
    sleeps: list[float] = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("providers.xai_imagine.asyncio.sleep", fake_sleep)

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_video("storm over mountains")

    # Exactly one POST: no retry, no backoff sleep on the non-idempotent submit.
    assert [c[0] for c in transport.calls] == ["POST"]
    assert sleeps == []
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_video_timeout_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([httpx.TimeoutException("slow")])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(
        XAIImagineError, match="Request timeout: outcome unconfirmed; not retried"
    ) as exc_info:
        await client.generate_video("tide pool")

    assert len(transport.calls) == 1
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_video_request_error_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    request = httpx.Request("POST", "https://example.xai.test/v1/videos/generations")
    transport = DummyAsyncClient([httpx.RequestError("boom", request=request)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="Request error; not retried") as exc_info:
        await client.generate_video("storm over mountains")

    assert len(transport.calls) == 1
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_video_malformed_response_single_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={"unexpected": True})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_video("lost response")

    assert len(transport.calls) == 1
    assert exc_info.value.client_reference is not None


@pytest.mark.asyncio
async def test_generate_video_failed_submission_returns_error_with_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lost response prerequisite: local correlation survives, not lookup (§8)."""
    client = make_client()
    reference: list[str] = []

    real_uuid4 = uuid.uuid4

    def fake_uuid4() -> uuid.UUID:
        value = real_uuid4()
        reference.append(str(value))
        return value

    monkeypatch.setattr("providers.xai_imagine.uuid.uuid4", fake_uuid4)
    transport = DummyAsyncClient([httpx.TimeoutException("connection lost")])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError) as exc_info:
        await client.generate_video("fjord reflections")

    assert exc_info.value.client_reference == reference[0]
    uuid.UUID(exc_info.value.client_reference)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["generate_image", "generate_video"])
@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
async def test_submit_http_failure_has_one_attempt_and_reference(
    monkeypatch: pytest.MonkeyPatch, method: str, status: int
) -> None:
    client = make_client()
    # Polling retry configuration must not influence submission count.
    client.max_retries = 0
    transport = DummyAsyncClient([DummyResponse(status, text="unavailable")])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)
    with pytest.raises(XAIImagineError, match="not retried") as caught:
        await getattr(client, method)("prompt")
    assert [call[0] for call in transport.calls] == ["POST"]
    assert caught.value.client_reference is not None
    assert uuid.UUID(caught.value.client_reference).version == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, field", [("generate_image", "url"), ("generate_video", "request_id")]
)
@pytest.mark.parametrize("value", [None, "", 42, [], {}])
async def test_invalid_success_field_retains_reference(
    monkeypatch: pytest.MonkeyPatch, method: str, field: str, value: object
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={field: value})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)
    with pytest.raises(XAIImagineError, match="malformed response") as caught:
        await getattr(client, method)("prompt")
    assert len(transport.calls) == 1
    assert caught.value.client_reference is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["generate_image", "generate_video"])
async def test_submit_cancellation_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    client = make_client()
    transport = DummyAsyncClient([asyncio.CancelledError()])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)
    with pytest.raises(asyncio.CancelledError):
        await getattr(client, method)("prompt")
    assert [call[0] for call in transport.calls] == ["POST"]


@pytest.mark.asyncio
async def test_concurrent_submits_have_independent_internal_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient(
        [DummyResponse(200, payload={"request_id": f"job-{index}"}) for index in range(2)]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)
    first, second = await asyncio.gather(
        client.generate_video("first"), client.generate_video("second")
    )
    assert first.client_reference is not None
    assert second.client_reference is not None
    assert first.client_reference != second.client_reference
    assert len(transport.calls) == 2
    assert "client_reference" not in first.model_json_schema()["properties"]


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
    transport = DummyAsyncClient([DummyResponse(200, payload={"video": {"status": "failed"}})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="Video generation failed"):
        await client.poll_video_job("job_failed")


@pytest.mark.asyncio
async def test_poll_video_job_expired_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    transport = DummyAsyncClient([DummyResponse(200, payload={"video": {"status": "expired"}})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: transport)

    with pytest.raises(XAIImagineError, match="expired"):
        await client.poll_video_job("job_expired")


@pytest.mark.asyncio
async def test_poll_video_job_status_retryable_get_retries_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET polling keeps its retry/backoff behaviour (fix scope: POST only)."""
    client = make_client()
    transport = DummyAsyncClient(
        [
            DummyResponse(503, text="polled upstream too early"),
            DummyResponse(
                200,
                payload={
                    "video": {
                        "status": "finished",
                        "url": {"generation": "https://cdn.example/late.mp4"},
                        "settings": {"prompt": ["late bloom"]},
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

    result = await client.poll_video_job("job_retry_get")

    assert result.url == "https://cdn.example/late.mp4"
    # GET attempts: the first 503, then the successful one. Backoff preserved.
    assert [c[0] for c in transport.calls] == ["GET", "GET"]
    assert sleeps == [1.0]


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
