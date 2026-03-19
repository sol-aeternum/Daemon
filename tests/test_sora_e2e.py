import uuid
from unittest.mock import AsyncMock

import pytest

from config.video_pricing import estimate_cost
from db.video_credits import Result
from orchestrator.subagents import image as image_module
from orchestrator.subagents.image import (
    ImageSubagent,
    OpenAISoraProvider,
    XAIImageProvider,
)
from providers.openai_sora import OpenAISoraClient, VideoJob as SoraVideoJob
from providers.openai_sora import VideoResult as SoraVideoResult
from providers.xai_imagine import VideoJob as XAIVideoJob
from providers.xai_imagine import VideoResult as XAIVideoResult
from providers.xai_imagine import XAIImagineClient


def _mock_config() -> dict[str, object]:
    return {
        "xai_api_key": "test-xai-key",
        "image_provider": "xai",
        "openai_sora_api_key": "test-openai-key",
        "db_pool": object(),
    }


class _FakeVideoCreditsDAL:
    def __init__(self, db_pool: object) -> None:
        self._pool = db_pool

    async def get_balance(self, user_id: uuid.UUID) -> int:
        return 1000

    async def debit_credits(
        self,
        user_id: uuid.UUID,
        amount: int,
        description: str,
        reference_id: str | None = None,
    ) -> Result:
        return Result(
            success=True,
            message="Credits debited successfully",
            transaction_id=uuid.uuid4(),
            new_balance=1000 - amount,
        )

    async def refund_transaction(self, transaction_id: uuid.UUID) -> Result:
        return Result(
            success=True,
            message="Transaction refunded successfully",
            transaction_id=transaction_id,
            new_balance=1000,
        )


@pytest.mark.asyncio
async def test_sora_e2e_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_module, "VideoCreditsDAL", _FakeVideoCreditsDAL)
    subagent = ImageSubagent(config=_mock_config())

    mock_sora_client = AsyncMock(spec=OpenAISoraClient)
    mock_sora_client.generate_video = AsyncMock(
        return_value=SoraVideoJob(
            job_id="test-sora-job",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_sora_client.poll_video_job = AsyncMock(
        return_value=SoraVideoResult(
            url="https://cdn.example.com/sora-video.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="completed",
            source_image_url=None,
        )
    )

    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_sora_client
    subagent.provider_name = "openai_sora"
    subagent.video_provider_name = "openai_sora"
    subagent.provider = sora_provider

    result = await subagent.execute(
        "generate a video",
        {
            "mode": "video",
            "duration": 5,
            "video_provider": "openai_sora",
            "tier": "pro",
            "user_id": str(uuid.uuid4()),
        },
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["video_url"] == "https://cdn.example.com/sora-video.mp4"


@pytest.mark.asyncio
async def test_sora_e2e_provider_switching_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_module, "VideoCreditsDAL", _FakeVideoCreditsDAL)
    subagent = ImageSubagent(config=_mock_config())

    mock_xai_client = AsyncMock(spec=XAIImagineClient)
    mock_xai_client.generate_video = AsyncMock(
        return_value=XAIVideoJob(
            job_id="test-xai-job",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_xai_client.poll_video_job = AsyncMock(
        return_value=XAIVideoResult(
            url="https://cdn.example.com/xai-video.mp4",
            duration_seconds=5,
            prompt="test prompt",
            source_image_url=None,
            status="done",
        )
    )
    xai_provider = XAIImageProvider("test-xai-key")
    xai_provider.client = mock_xai_client

    mock_sora_client = AsyncMock(spec=OpenAISoraClient)
    mock_sora_client.generate_video = AsyncMock(
        return_value=SoraVideoJob(
            job_id="test-sora-job",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_sora_client.poll_video_job = AsyncMock(
        return_value=SoraVideoResult(
            url="https://cdn.example.com/sora-video.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="completed",
            source_image_url=None,
        )
    )
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_sora_client

    subagent.provider_name = "xai"
    subagent.video_provider_name = "xai"
    subagent.provider = xai_provider
    first = await subagent.execute(
        "generate a video",
        {
            "mode": "video",
            "duration": 5,
            "video_provider": "xai",
            "tier": "pro",
            "user_id": str(uuid.uuid4()),
        },
    )

    subagent.provider_name = "openai_sora"
    subagent.video_provider_name = "openai_sora"
    subagent.provider = sora_provider
    second = await subagent.execute(
        "generate a video",
        {
            "mode": "video",
            "duration": 5,
            "video_provider": "openai_sora",
            "tier": "pro",
            "user_id": str(uuid.uuid4()),
        },
    )

    subagent.provider_name = "xai"
    subagent.video_provider_name = "xai"
    subagent.provider = xai_provider
    third = await subagent.execute(
        "generate a video",
        {
            "mode": "video",
            "duration": 5,
            "video_provider": "xai",
            "tier": "pro",
            "user_id": str(uuid.uuid4()),
        },
    )

    assert first.success is True
    assert second.success is True
    assert third.success is True


def test_sora_e2e_cost_estimation_with_provider() -> None:
    xai_cost_pro = estimate_cost(10, "pro", "xai")
    sora_cost_pro = estimate_cost(10, "pro", "openai_sora")
    assert sora_cost_pro > xai_cost_pro
