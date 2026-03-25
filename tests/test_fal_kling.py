from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import os

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


@pytest.mark.asyncio
async def test_generate_video_text_to_video_o3_pro_success():
    client = FalKlingClient()

    mock_result = MagicMock()
    mock_result.request_id = "req_123"
    with patch("providers.fal_kling.fal_client.submit_async", return_value=mock_result):
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

    mock_result = MagicMock()
    mock_result.request_id = "req_456"
    with patch("providers.fal_kling.fal_client.submit_async", return_value=mock_result):
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

    mock_result = MagicMock()
    mock_result.request_id = "req_789"
    with patch("providers.fal_kling.fal_client.submit_async", return_value=mock_result):
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

    mock_result = MagicMock()
    mock_result.request_id = "req_999"
    with patch("providers.fal_kling.fal_client.submit_async", return_value=mock_result):
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

    mock_result = MagicMock()
    mock_result.request_id = "req_111"
    with patch("providers.fal_kling.fal_client.submit_async", return_value=mock_result):
        result = await client.generate_video(
            prompt="test video", duration_seconds=5, kling_model="invalid-model"
        )

        assert result.kling_model == "o3-pro"


@pytest.mark.asyncio
async def test_generate_video_missing_api_key_raises():
    with patch.dict(os.environ, {"FAL_KEY": ""}):
        with pytest.raises(FalKlingError, match="FAL_KEY not configured"):
            FalKlingClient()


@pytest.mark.asyncio
async def test_generate_video_submit_failure_raises():
    client = FalKlingClient()

    with patch(
        "providers.fal_kling.fal_client.submit_async",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(
            FalKlingError, match="Failed to submit video generation job"
        ):
            await client.generate_video("test prompt")


@pytest.mark.asyncio
async def test_poll_video_job_success():
    client = FalKlingClient()

    job = VideoJob(
        job_id="job_123", prompt="test video", duration_seconds=5, kling_model="o3-pro"
    )

    mock_result = {"video": {"url": "https://cdn.fal.ai/video.mp4"}}
    with patch("providers.fal_kling.fal_client.get_result", return_value=mock_result):
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
        "providers.fal_kling.fal_client.result_async",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to poll video generation job"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_success():
    client = FalKlingClient()

    job = VideoJob(
        job_id="job_123", prompt="test video", duration_seconds=5, kling_model="o3-pro"
    )

    mock_result = {"video": {"url": "https://cdn.fal.ai/video.mp4"}}
    with patch("providers.fal_kling.fal_client.result_async", return_value=mock_result):
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
    with patch("providers.fal_kling.fal_client.result_async", return_value=mock_result):
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
    with patch("providers.fal_kling.fal_client.result_async", return_value=mock_result):
        with pytest.raises(FalKlingError, match="Video generation job failed"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_missing_url_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_999", prompt="test video", duration_seconds=5)

    mock_result = {"video": {"status": "finished"}}
    with patch("providers.fal_kling.fal_client.result_async", return_value=mock_result):
        with pytest.raises(FalKlingError, match="Video generation job failed"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_get_result_failure_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_111", prompt="test video", duration_seconds=5)

    with patch(
        "providers.fal_kling.fal_client.result_async",
        side_effect=Exception("API error"),
    ):
        with pytest.raises(FalKlingError, match="Failed to poll video generation job"):
            await client.poll_video_job(job)


@pytest.mark.asyncio
async def test_poll_video_job_get_result_failure_raises():
    client = FalKlingClient()

    job = VideoJob(job_id="job_111", prompt="test video", duration_seconds=5)

    with patch(
        "providers.fal_kling.fal_client.result_async",
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
