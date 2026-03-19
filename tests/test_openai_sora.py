from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import AsyncOpenAI
from openai.resources.videos import Videos
from openai.types.video import Video

from providers.openai_sora import (
    OpenAISoraClient,
    OpenAISoraError,
    VideoJob,
    VideoResult,
)


class TestOpenAISoraClient:
    @pytest.fixture
    def mock_sora_api_key(self) -> str:
        return "test-openai-key"

    @pytest.fixture
    def client(self, mock_sora_api_key: str) -> OpenAISoraClient:
        with patch(
            "providers.openai_sora.get_sora_api_key", return_value=mock_sora_api_key
        ):
            return OpenAISoraClient()

    def test_init_success(self, mock_sora_api_key: str) -> None:
        with patch(
            "providers.openai_sora.get_sora_api_key", return_value=mock_sora_api_key
        ):
            client = OpenAISoraClient()
            assert client.api_key == "test-openai-key"
            assert client.client is not None
            assert isinstance(client.client, AsyncOpenAI)

    def test_init_no_api_key(self) -> None:
        with patch("providers.openai_sora.get_sora_api_key", return_value=None):
            client = OpenAISoraClient()
            assert client.api_key is None
            assert client.client is None

    @pytest.mark.asyncio
    async def test_generate_video_success_text_prompt(
        self, client: OpenAISoraClient
    ) -> None:
        mock_video = Video(
            id="vid_123",
            prompt="A beautiful sunset",
            seconds="4",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            status="completed",
        )
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.create = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        result = await client.generate_video("A beautiful sunset", duration=5)

        assert isinstance(result, VideoJob)
        assert result.job_id == "vid_123"
        assert result.prompt == "A beautiful sunset"
        assert result.duration_seconds == 5
        assert result.source_image_url is None

        mock_videos.create.assert_called_once_with(
            model="sora-2", prompt="A beautiful sunset", seconds="4", size="1280x720"
        )

    @pytest.mark.asyncio
    async def test_generate_video_success_image_to_video(
        self, client: OpenAISoraClient
    ) -> None:
        mock_video = Video(
            id="vid_456",
            prompt="Animate this scene",
            seconds="8",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            status="completed",
        )
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.create = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        result = await client.generate_video(
            "Animate this scene", duration=10, image_url="https://example.com/image.jpg"
        )

        assert isinstance(result, VideoJob)
        assert result.job_id == "vid_456"
        assert result.prompt == "Animate this scene"
        assert result.duration_seconds == 10
        assert result.source_image_url == "https://example.com/image.jpg"

        mock_videos.create.assert_called_once_with(
            model="sora-2", prompt="Animate this scene", seconds="8", size="1280x720"
        )

    @pytest.mark.asyncio
    async def test_generate_video_duration_mapping(
        self, client: OpenAISoraClient
    ) -> None:
        mock_video = Video(
            id="vid_789",
            prompt="Test",
            seconds="12",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            status="completed",
        )
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.create = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        await client.generate_video("Test", duration=15)
        mock_videos.create.assert_called_with(
            model="sora-2", prompt="Test", seconds="12", size="1280x720"
        )

        mock_videos.create.reset_mock()
        await client.generate_video("Test", duration=20)
        mock_videos.create.assert_called_with(
            model="sora-2", prompt="Test", seconds="12", size="1280x720"
        )

        mock_videos.create.reset_mock()
        await client.generate_video("Test", duration=30)
        mock_videos.create.assert_called_with(
            model="sora-2", prompt="Test", seconds="12", size="1280x720"
        )

    @pytest.mark.asyncio
    async def test_generate_video_invalid_duration(
        self, client: OpenAISoraClient
    ) -> None:
        with pytest.raises(OpenAISoraError, match="Invalid duration"):
            await client.generate_video("Test", duration=7)

    @pytest.mark.asyncio
    async def test_generate_video_invalid_model(self, client: OpenAISoraClient) -> None:
        with pytest.raises(OpenAISoraError, match="Invalid model"):
            await client.generate_video("Test", model="invalid-model")

    @pytest.mark.asyncio
    async def test_generate_video_no_api_key(self) -> None:
        with patch("providers.openai_sora.get_sora_api_key", return_value=None):
            client = OpenAISoraClient()
            with pytest.raises(
                OpenAISoraError, match="OPENAI_SORA_API_KEY not configured"
            ):
                await client.generate_video("Test")

    @pytest.mark.asyncio
    async def test_generate_video_no_client(self) -> None:
        with patch("providers.openai_sora.get_sora_api_key", return_value="test-key"):
            client = OpenAISoraClient()
            client.client = None
            with pytest.raises(OpenAISoraError, match="OpenAI client not initialized"):
                await client.generate_video("Test")

    @pytest.mark.asyncio
    async def test_generate_video_api_error(self, client: OpenAISoraClient) -> None:
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.create = AsyncMock(side_effect=Exception("API error"))

        client.client = AsyncMock()
        client.client.videos = mock_videos

        with pytest.raises(OpenAISoraError, match="Video generation failed"):
            await client.generate_video("Test")

    @pytest.mark.asyncio
    async def test_poll_video_job_completed(self, client: OpenAISoraClient) -> None:
        mock_video = Video(
            id="vid_123",
            status="completed",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            seconds="4",
            prompt="A beautiful sunset",
        )
        setattr(mock_video, "url", "https://example.com/video.mp4")
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        result = await client.poll_video_job("vid_123")

        assert isinstance(result, VideoResult)
        assert result.url == "https://example.com/video.mp4"
        assert result.prompt == "A beautiful sunset"
        assert result.duration_seconds == 4
        assert result.status == "completed"
        assert result.source_image_url is None

    @pytest.mark.asyncio
    async def test_poll_video_job_pending_then_completed(
        self, client: OpenAISoraClient
    ) -> None:
        pending_video = Video(
            id="vid_456",
            status="in_progress",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=50,
            size="1280x720",
            seconds="8",
            prompt="City lights",
        )
        completed_video = Video(
            id="vid_456",
            status="completed",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            seconds="8",
            prompt="City lights",
        )
        setattr(completed_video, "url", "https://example.com/final.mp4")

        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(side_effect=[pending_video, completed_video])

        client.client = AsyncMock()
        client.client.videos = mock_videos

        with patch(
            "providers.openai_sora.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            result = await client.poll_video_job("vid_456")

        assert result.url == "https://example.com/final.mp4"
        mock_sleep.assert_awaited_once_with(5)

    @pytest.mark.asyncio
    async def test_poll_video_job_unknown_status(
        self, client: OpenAISoraClient
    ) -> None:
        mock_video = Video(
            id="vid_000",
            status="queued",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=0,
            size="1280x720",
            seconds="4",
        )
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        setattr(mock_video, "status", "unexpected_status")
        with pytest.raises(OpenAISoraError, match="Unknown video status"):
            await client.poll_video_job("vid_000")

    @pytest.mark.asyncio
    async def test_poll_video_job_no_url(self, client: OpenAISoraClient) -> None:
        mock_video = Video(
            id="vid_111",
            status="completed",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            seconds="4",
            prompt="Test",
        )
        setattr(mock_video, "url", "")
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(return_value=mock_video)

        client.client = AsyncMock()
        client.client.videos = mock_videos

        with pytest.raises(
            OpenAISoraError, match="Video completed but URL not available"
        ):
            await client.poll_video_job("vid_111")

    @pytest.mark.asyncio
    async def test_poll_video_job_timeout_retry_success(
        self, client: OpenAISoraClient
    ) -> None:
        mock_video = Video(
            id="vid_222",
            status="completed",
            created_at=1234567890,
            model="sora-2",
            object="video",
            progress=100,
            size="1280x720",
            seconds="4",
            prompt="Retried video",
        )
        setattr(mock_video, "url", "https://example.com/retried.mp4")

        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(
            side_effect=[httpx.TimeoutException("timeout"), mock_video]
        )

        client.client = AsyncMock()
        client.client.videos = mock_videos

        with patch("providers.openai_sora.asyncio.sleep", new_callable=AsyncMock):
            result = await client.poll_video_job("vid_222")

        assert result.url == "https://example.com/retried.mp4"

    @pytest.mark.asyncio
    async def test_poll_video_job_max_retries_exceeded(
        self, client: OpenAISoraClient
    ) -> None:
        mock_videos = AsyncMock(spec=Videos)
        mock_videos.retrieve = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        client.client = AsyncMock()
        client.client.videos = mock_videos

        with patch("providers.openai_sora.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(OpenAISoraError, match="Request timeout after retries"):
                await client.poll_video_job("vid_333")
