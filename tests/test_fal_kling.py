from __future__ import annotations

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import os
from uuid import UUID

import httpx

from providers.fal_kling import (
    VideoJob,
    VideoResult,
    FalKlingError,
    FalKlingClient,
)


@pytest.fixture(autouse=True)
def mock_fal_key_env():
    with patch.dict(os.environ, {"FAL_KEY": "test-fal-key"}):
        yield


def submit_response(request_id: str) -> httpx.Response:
    return httpx.Response(200, json={"request_id": request_id})


@pytest.mark.asyncio
async def test_generate_video_text_to_video_o3_pro_success():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", return_value=submit_response("req_123")
    ):
        result = await client.generate_video(
            prompt="cinematic mountains at sunset",
            duration_seconds=10,
            kling_model="o3-pro",
        )

        assert isinstance(result, VideoJob)
        assert result.job_id == "req_123"
        assert result.prompt == "cinematic mountains at sunset"
        assert result.duration_seconds == 10
        assert result.source_image_url is None
        assert result.kling_model == "o3-pro"
        assert result.audio_enabled is False


@pytest.mark.asyncio
async def test_generate_video_text_to_video_v3_pro_with_audio_success():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", return_value=submit_response("req_456")
    ):
        result = await client.generate_video(
            prompt="ocean waves crashing on rocks",
            duration_seconds=15,
            kling_model="v3-pro",
            audio_enabled=True,
        )

        assert isinstance(result, VideoJob)
        assert result.job_id == "req_456"
        assert result.prompt == "ocean waves crashing on rocks"
        assert result.duration_seconds == 15
        assert result.source_image_url is None
        assert result.kling_model == "v3-pro"
        assert result.audio_enabled is True


@pytest.mark.asyncio
async def test_generate_video_image_to_video_success():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", return_value=submit_response("req_789")
    ):
        result = await client.generate_video(
            prompt="animate this landscape",
            duration_seconds=5,
            source_image_url="https://example.com/image.jpg",
            kling_model="o3-pro",
        )

        assert isinstance(result, VideoJob)
        assert result.job_id == "req_789"
        assert result.prompt == "animate this landscape"
        assert result.duration_seconds == 5
        assert result.source_image_url == "https://example.com/image.jpg"
        assert result.kling_model == "o3-pro"


@pytest.mark.asyncio
async def test_generate_video_duration_clamping():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", return_value=submit_response("req_999")
    ):
        result = await client.generate_video(
            prompt="test video", duration_seconds=1, kling_model="o3-pro"
        )

        assert result.duration_seconds == 3

        result = await client.generate_video(
            prompt="test video", duration_seconds=20, kling_model="o3-pro"
        )

        assert result.duration_seconds == 15


@pytest.mark.asyncio
async def test_generate_video_invalid_model_fallback():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", return_value=submit_response("req_111")
    ):
        result = await client.generate_video(
            prompt="test video", duration_seconds=5, kling_model="invalid-model"
        )

        assert result.kling_model == "o3-pro"


@pytest.mark.asyncio
async def test_generate_video_missing_api_key_raises():
    with patch.dict(os.environ, {"FAL_KEY": ""}):
        with pytest.raises(FalKlingError, match="FAL_KEY not configured"):
            FalKlingClient()


def test_client_authenticates_with_settings_key():
    settings = MagicMock(fal_key="settings-only-fal-key")
    with (
        patch("orchestrator.config.get_settings", return_value=settings),
        patch("providers.fal_kling.fal_client.AsyncClient") as async_client,
    ):
        client = FalKlingClient()

    assert client.api_key == "settings-only-fal-key"
    async_client.assert_called_once_with(key="settings-only-fal-key")


@pytest.mark.asyncio
async def test_wrapper_override_preserves_sdk_credentials_for_submit_and_poll():
    from orchestrator.subagents.image import FalKlingProvider

    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return submit_response("provider-job")
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED", "logs": []})
        return httpx.Response(200, json={"video": {"url": "https://example.test/video.mp4"}})

    def make_client(*args, **kwargs):
        client = real_client(*args, transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    with (
        patch("orchestrator.config.get_settings", return_value=MagicMock(fal_key="settings-key")),
        patch("httpx.AsyncClient", side_effect=make_client),
    ):
        provider = FalKlingProvider("different-wrapper-key")
        try:
            result = await provider.generate_video("prompt", 5)
        finally:
            for client in clients:
                await client.aclose()

    assert result["url"] == "https://example.test/video.mp4"
    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    # Before P0.1, both SDK operations used the initialization credential,
    # even when the wrapper subsequently assigned its api_key attribute.
    assert {request.headers["Authorization"] for request in requests} == {"Key settings-key"}
    assert requests[0].headers["X-Fal-No-Retry"] == "1"


@pytest.mark.asyncio
async def test_generate_video_submit_failure_raises():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.httpx.AsyncClient.post",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to submit video generation job"):
            await client.generate_video("test prompt")


@pytest.mark.asyncio
async def test_poll_video_job_success():
    client = FalKlingClient()

    job = VideoJob(job_id="job_123", prompt="test video", duration_seconds=5, kling_model="o3-pro")

    mock_result = {"video": {"url": "https://cdn.fal.ai/video.mp4"}}
    with patch("providers.fal_kling.fal_client.AsyncClient.result", return_value=mock_result):
        result = await client.poll_video_job(job)

        assert isinstance(result, VideoResult)
        assert result.url == "https://cdn.fal.ai/video.mp4"
        assert result.prompt == "test video"
        assert result.duration_seconds == 5
        assert result.source_image_url is None
        assert result.status == "finished"
        assert result.kling_model == "o3-pro"


@pytest.mark.asyncio
async def test_poll_video_job_get_result_failure_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_111", prompt="test video", duration_seconds=5)

    with patch(
        "providers.fal_kling.fal_client.AsyncClient.result",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to poll video generation job"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_success():  # noqa: F811
    client = FalKlingClient()

    job = VideoJob(job_id="job_123", prompt="test video", duration_seconds=5, kling_model="o3-pro")

    mock_result = {"video": {"url": "https://cdn.fal.ai/video.mp4"}}
    with patch("providers.fal_kling.fal_client.AsyncClient.result", return_value=mock_result):
        result = await client.poll_video_job(job)

        assert isinstance(result, VideoResult)
        assert result.url == "https://cdn.fal.ai/video.mp4"
        assert result.prompt == "test video"
        assert result.duration_seconds == 5
        assert result.source_image_url is None
        assert result.status == "finished"
        assert result.kling_model == "o3-pro"


@pytest.mark.asyncio
async def test_poll_video_job_with_audio_success():
    client = FalKlingClient()

    job = VideoJob(
        job_id="job_456",
        prompt="test video with audio",
        duration_seconds=10,
        kling_model="v3-pro",
        audio_enabled=True,
    )

    mock_result = {"video": {"url": "https://cdn.fal.ai/video-with-audio.mp4"}}
    with patch("providers.fal_kling.fal_client.AsyncClient.result", return_value=mock_result):
        result = await client.poll_video_job(job)

        assert isinstance(result, VideoResult)
        assert result.url == "https://cdn.fal.ai/video-with-audio.mp4"
        assert result.prompt == "test video with audio"
        assert result.duration_seconds == 10
        assert result.source_image_url is None
        assert result.status == "finished"
        assert result.kling_model == "v3-pro"
        assert result.audio_enabled is True


@pytest.mark.asyncio
async def test_poll_video_job_missing_video_data_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_789", prompt="test video", duration_seconds=5)

    mock_result = {"status": "finished"}
    with patch("providers.fal_kling.fal_client.AsyncClient.result", return_value=mock_result):
        with pytest.raises(FalKlingError, match="Video generation job failed"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_missing_url_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_999", prompt="test video", duration_seconds=5)

    mock_result = {"video": {"status": "finished"}}
    with patch("providers.fal_kling.fal_client.AsyncClient.result", return_value=mock_result):
        with pytest.raises(FalKlingError, match="Video generation job failed"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_get_result_failure_raises():  # noqa: F811
    client = FalKlingClient()

    job = VideoJob(job_id="job_111", prompt="test video", duration_seconds=5)

    with patch(
        "providers.fal_kling.fal_client.AsyncClient.result",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to poll video generation job"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_get_result_failure_raises():  # noqa: F811
    client = FalKlingClient()

    job = VideoJob(job_id="job_111", prompt="test video", duration_seconds=5)

    with patch(
        "providers.fal_kling.fal_client.AsyncClient.result",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to poll video generation job"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_missing_api_key_raises():
    with patch.dict(os.environ, {"FAL_KEY": ""}):
        with pytest.raises(FalKlingError, match="FAL_KEY not configured"):
            FalKlingClient()


def test_get_model_endpoint():
    client = FalKlingClient()

    endpoint = client._get_model_endpoint("o3-pro", False)
    assert endpoint == "fal-ai/kling-video/o3/pro/text-to-video"

    endpoint = client._get_model_endpoint("o3-pro", True)
    assert endpoint == "fal-ai/kling-video/o3/pro/image-to-video"

    endpoint = client._get_model_endpoint("v3-pro", False)
    assert endpoint == "fal-ai/kling-video/v3/pro/text-to-video"

    endpoint = client._get_model_endpoint("v3-pro", True)
    assert endpoint == "fal-ai/kling-video/v3/pro/image-to-video"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
async def test_submit_http_failure_is_never_retried(status: int) -> None:
    client = FalKlingClient()
    with patch(
        "providers.fal_kling.httpx.AsyncClient.post",
        return_value=httpx.Response(status, text="nginx private provider message"),
    ) as post:
        with pytest.raises(FalKlingError) as caught:
            await client.generate_video("private prompt")
    post.assert_awaited_once()
    assert caught.value.client_reference is not None
    assert UUID(caught.value.client_reference).version == 4
    assert "private" not in str(caught.value)
    assert "nginx" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError])
async def test_submit_transport_failure_is_never_retried(error_type: type[Exception]) -> None:
    client = FalKlingClient()
    with patch(
        "providers.fal_kling.httpx.AsyncClient.post",
        side_effect=error_type("private transport message"),
    ) as post:
        with pytest.raises(FalKlingError) as caught:
            await client.generate_video("private prompt")
    post.assert_awaited_once()
    assert caught.value.client_reference is not None
    assert UUID(caught.value.client_reference).version == 4
    assert "unconfirmed" in str(caught.value)
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"request_id": ""}, {"request_id": None}, ["bad"]])
async def test_malformed_submit_response_retains_reference_without_retry(payload: object) -> None:
    client = FalKlingClient()
    with patch(
        "providers.fal_kling.httpx.AsyncClient.post",
        return_value=httpx.Response(200, json=payload),
    ) as post:
        with pytest.raises(FalKlingError) as caught:
            await client.generate_video("prompt")
    post.assert_awaited_once()
    assert caught.value.client_reference is not None


@pytest.mark.asyncio
async def test_submit_generates_reference_before_http_and_keeps_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FalKlingClient()
    reference = UUID("327f3a14-249e-44e7-8e89-20944972c236")
    generated: list[UUID] = []

    def make_reference() -> UUID:
        generated.append(reference)
        return reference

    async def send_once(url: str, **kwargs: object) -> httpx.Response:
        assert generated == [reference]
        assert url == "https://queue.fal.run/fal-ai/kling-video/v3/pro/image-to-video"
        assert kwargs["headers"] == {
            "Authorization": "Key test-fal-key",
            "X-Fal-No-Retry": "1",
        }
        assert kwargs["json"] == {
            "prompt": "prompt",
            "duration": 10,
            "image_url": "https://example.test/source.png",
            "audio_enabled": True,
        }
        return submit_response("provider-job")

    monkeypatch.setattr("providers.fal_kling.uuid4", make_reference)
    post = AsyncMock(side_effect=send_once)
    with (
        patch("providers.fal_kling.httpx.AsyncClient.post", post),
        patch("providers.fal_kling.fal_client.AsyncClient.submit") as sdk_submit,
    ):
        result = await client.generate_video(
            "prompt", 10, "https://example.test/source.png", "v3-pro", True
        )
    post.assert_awaited_once()
    sdk_submit.assert_not_called()
    assert result.client_reference == str(reference)
    assert result.job_id == "provider-job"
    assert set(result.model_dump()) == {
        "job_id",
        "prompt",
        "duration_seconds",
        "source_image_url",
        "kling_model",
        "audio_enabled",
    }
    assert "client_reference" not in result.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_submit_cancellation_propagates_without_retry() -> None:
    client = FalKlingClient()
    with patch(
        "providers.fal_kling.httpx.AsyncClient.post", side_effect=asyncio.CancelledError()
    ) as post:
        with pytest.raises(asyncio.CancelledError):
            await client.generate_video("prompt")
    post.assert_awaited_once()
