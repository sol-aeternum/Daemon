"""End-to-end integration tests for Kling video generation flow.

Tests:
1. Success path: grant credits → request video via @image with provider=fal → verify correct client dispatched → mock successful generation → verify result format → verify credit debit correct for Kling pricing
2. Failure path: grant credits → request video → mock failure → verify refund
3. Provider switching: Test switching between xAI and Kling providers
4. Tier block path: free tier attempt rejected
5. Kling-specific features: Test O3 Pro vs V3 Pro models with audio
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock

import pytest

from config.video_pricing import estimate_cost
from db.video_credits import VideoCreditsDAL
from orchestrator.subagents.image import ImageSubagent
from providers.fal_kling import (
    VideoJob,
    VideoResult,
    FalKlingClient,
)
from providers.xai_imagine import (
    VideoJob as XAIVideoJob,
    VideoResult as XAIVideoResult,
    XAIImagineClient,
)

from orchestrator.subagents.image import XAIImageProvider, FalKlingProvider


# Fake DB state and helpers (reused from test_video_e2e.py)
class FakeTransactionRow(TypedDict):
    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    amount: int
    description: str | None
    reference_id: str | None
    created_at: datetime


def make_transaction_row(
    *,
    user_id: uuid.UUID,
    transaction_type: str,
    amount: int,
    description: str | None,
    reference_id: str | None,
) -> FakeTransactionRow:
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "type": transaction_type,
        "amount": amount,
        "description": description,
        "reference_id": reference_id,
        "created_at": datetime.now(timezone.utc),
    }


@dataclass
class FakeDbState:
    balances: dict[uuid.UUID, int] = field(default_factory=dict)
    transactions: list[FakeTransactionRow] = field(default_factory=list)
    balance_locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=dict)
    transaction_locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=dict)

    def get_balance_lock(self, user_id: uuid.UUID) -> asyncio.Lock:
        return self.balance_locks.setdefault(user_id, asyncio.Lock())

    def get_transaction_lock(self, transaction_id: uuid.UUID) -> asyncio.Lock:
        return self.transaction_locks.setdefault(transaction_id, asyncio.Lock())


class FakePoolAcquire:
    def __init__(self, state: FakeDbState) -> None:
        self._state = state

    async def __aenter__(self) -> FakeConnection:
        return FakeConnection(self._state)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        while self._connection.held_locks:
            self._connection.held_locks.pop().release()
        return None


class FakeConnection:
    def __init__(self, state: FakeDbState) -> None:
        self.state = state
        self.held_locks: list[asyncio.Lock] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def fetchrow(
        self, query: str, *args: object
    ) -> FakeTransactionRow | dict[str, int | uuid.UUID] | None:
        normalized = " ".join(query.split()).lower()
        if "video_credit_balances" in normalized and "select" in normalized:
            user_id = args[0]
            balance = self.state.balances.get(user_id, 0)
            return {"user_id": user_id, "balance": balance}
        if (
            "video_credit_balances" in normalized
            and "insert" in normalized
            and "on conflict" in normalized
        ):
            user_id = args[0]
            amount = args[1]
            self.state.balances[user_id] = self.state.balances.get(user_id, 0) + amount
            return {"user_id": user_id, "balance": self.state.balances[user_id]}
        if "video_credit_transactions" in normalized and "insert" in normalized:
            # Query is: INSERT ... VALUES ($1, 'spend', $2, $3, $4)
            # So args are: user_id, amount, description, reference_id
            tx = make_transaction_row(
                user_id=args[0],
                transaction_type="spend",
                amount=args[1],
                description=args[2] if len(args) > 2 else None,
                reference_id=args[3] if len(args) > 3 else None,
            )
            self.state.transactions.append(tx)
            return tx
        if "video_credit_transactions" in normalized and "select" in normalized:
            if "for update" in normalized:
                tx_id = args[0]
                for tx in self.state.transactions:
                    if tx["id"] == tx_id and tx["type"] == "spend":
                        return tx
                return None
            user_id = args[0]
            return [tx for tx in self.state.transactions if tx["user_id"] == user_id]
        if "refund" in normalized and "update" in normalized:
            tx_id = args[0]
            for tx in self.state.transactions:
                if tx["id"] == tx_id:
                    tx["type"] = "refund"
                    tx["amount"] = abs(tx["amount"])
                    return tx
            return None
        return None

    async def execute(self, query: str, *args: object) -> str:
        normalized = " ".join(query.split()).lower()
        if (
            "insert into video_credit_balances" in normalized
            and "do update set balance = video_credit_balances.balance - $3" in normalized
        ):
            user_id, current_balance, amount = args
            self.state.balances[user_id] = current_balance - amount
            return "UPDATE 1"
        if (
            "insert into video_credit_balances" in normalized
            and "do update set balance = video_credit_balances.balance + $2" in normalized
        ):
            user_id, amount = args[0], args[1]
            self.state.balances[user_id] = self.state.balances.get(user_id, 0) + amount
            return "UPDATE 1"
        if "video_credit_balances" in normalized and "select" in normalized:
            user_id = args[0]
            balance = self.state.balances.get(user_id, 0)
            return balance
        if "video_credit_transactions" in normalized and "insert" in normalized:
            tx = make_transaction_row(
                user_id=args[0],
                transaction_type=args[1],
                amount=args[2],
                description=args[3] if len(args) > 3 else None,
                reference_id=args[4] if len(args) > 4 else None,
            )
            self.state.transactions.append(tx)
            return "INSERT 0 1"
        return "OK"

    async def fetch(self, query: str, *args: object) -> list[FakeTransactionRow]:
        normalized = " ".join(query.split()).lower()
        if "video_credit_transactions" in normalized:
            user_id = args[0]
            return [tx for tx in self.state.transactions if tx["user_id"] == user_id]
        return []


class FakePool:
    def __init__(self, state: FakeDbState) -> None:
        self._state = state

    def acquire(self) -> FakePoolAcquire:
        return FakePoolAcquire(self._state)

    async def close(self) -> None:
        pass


# Test fixtures
@pytest.fixture
def test_user_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def fake_db_state() -> FakeDbState:
    return FakeDbState()


@pytest.fixture
def fake_pool(fake_db_state: FakeDbState) -> FakePool:
    return FakePool(fake_db_state)


@pytest.fixture
def mock_config(fake_pool: FakePool) -> dict[str, Any]:
    return {
        "db_pool": fake_pool,
        "xai_api_key": "test-xai-key",
        "fal_api_key": "test-fal-key",
        "openrouter_api_key": "test-openrouter-key",
        "image_provider": "openrouter",
        "video_provider": "fal",
    }


@pytest.fixture(autouse=True)
def fake_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")


def install_video_provider(subagent: ImageSubagent, provider_name: str, provider: object) -> None:
    subagent.provider = provider
    subagent.provider_name = provider_name
    subagent.video_provider_name = provider_name


# Test 1: Success path - grant credits, request video with fal provider, verify debit, mock success
@pytest.mark.asyncio
async def test_kling_e2e_success_path(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test successful Kling video generation flow with credit debit."""
    # Step 1: Grant credits to user
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1000, "Initial balance should be 1000"

    # Step 2: Create subagent and mock provider
    subagent = ImageSubagent(config=mock_config)

    # Mock the Kling provider client
    mock_client = AsyncMock(spec=FalKlingClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-job-123",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/video.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )

    # Create a Kling provider with our mock client
    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_client

    # Override the provider selection to use our mock
    install_video_provider(subagent, "fal", kling_provider)

    # Step 3: Request video generation with fal provider
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",  # Explicitly select fal provider
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Step 4: Verify success
    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data
    assert result.data["video_url"] == "https://cdn.fal.ai/video.mp4"

    # Step 5: Verify debit happened with correct Kling pricing
    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(
        duration_seconds=5,
        tier="pro",
        provider="fal",
        kling_model="o3-pro",
        audio_enabled=False,
    )
    assert final_balance == initial_balance - cost, (
        f"Balance should be debited: {initial_balance} - {cost} = {final_balance}"
    )


# Test 2: Failure path - grant credits, request video, mock failure, verify refund
@pytest.mark.asyncio
async def test_kling_e2e_failure_path_with_refund(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test Kling video generation failure triggers refund."""
    # Step 1: Grant credits
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1000

    # Step 2: Create subagent and mock provider to fail
    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=FalKlingClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-job-fail",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    from providers.fal_kling import FalKlingError

    mock_client.poll_video_job = AsyncMock(
        side_effect=FalKlingError("Kling video generation failed")
    )

    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_client
    install_video_provider(subagent, "fal", kling_provider)

    # Step 3: Request video generation
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Step 4: Verify failure
    assert result.success is False, "Expected failure"
    assert result.error is not None

    # Step 5: Verify refund happened
    final_balance = await video_credits_dal.get_balance(test_user_id)
    assert final_balance == initial_balance, (
        f"Balance should be refunded: {initial_balance} == {final_balance}"
    )


# Test 3: Provider switching between xAI and Kling
@pytest.mark.asyncio
async def test_kling_e2e_provider_switching(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test switching between xAI and Kling providers."""
    # Grant credits
    fake_db_state.balances[test_user_id] = 2000
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    # Test xAI provider first
    subagent = ImageSubagent(config=mock_config)

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
            job_id="test-xai-job",
            status="done",
            url="https://cdn.example.com/xai-video.mp4",
            duration_seconds=5,
            prompt="test prompt",
        )
    )

    xai_provider = XAIImageProvider("test-xai-key")
    xai_provider.client = mock_xai_client
    install_video_provider(subagent, "xai", xai_provider)

    context_xai = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "xai",  # Explicitly select xAI provider
    }
    result_xai = await subagent.execute("generate a video", context_xai)

    assert result_xai.success is True
    assert result_xai.data is not None
    assert result_xai.data["video_url"] == "https://cdn.example.com/xai-video.mp4"

    # Verify xAI cost was deducted
    balance_after_xai = await video_credits_dal.get_balance(test_user_id)
    xai_cost = estimate_cost(duration_seconds=5, tier="pro", provider="xai")
    assert balance_after_xai == 2000 - xai_cost

    # Now test Kling provider
    mock_kling_client = AsyncMock(spec=FalKlingClient)
    mock_kling_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-job-switch",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    mock_kling_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/switch-video.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )

    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_kling_client
    install_video_provider(subagent, "fal", kling_provider)

    context_kling = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",  # Explicitly select fal provider
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result_kling = await subagent.execute("generate a video", context_kling)

    assert result_kling.success is True
    assert result_kling.data is not None
    assert result_kling.data["video_url"] == "https://cdn.fal.ai/switch-video.mp4"

    # Verify Kling cost was deducted
    final_balance = await video_credits_dal.get_balance(test_user_id)
    kling_cost = estimate_cost(
        duration_seconds=5,
        tier="pro",
        provider="fal",
        kling_model="o3-pro",
        audio_enabled=False,
    )
    assert final_balance == balance_after_xai - kling_cost


# Test 4: Tier block path - free tier attempt rejected
@pytest.mark.asyncio
async def test_kling_e2e_tier_blocked(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    mock_config: dict[str, Any],
) -> None:
    """Test that Free tier users cannot generate Kling videos."""
    # Grant credits but tier is free
    fake_db_state.balances[test_user_id] = 1000

    subagent = ImageSubagent(config=mock_config)

    # Attempt video generation with free tier
    context = {
        "user_id": str(test_user_id),
        "tier": "free",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Verify blocked - only Free tier is blocked, BYOK is allowed
    assert result.success is False, "Free tier should be blocked"
    assert "not available for Free tier" in result.error, (
        f"Expected tier block message, got: {result.error}"
    )


# Test 5: Kling-specific features - O3 Pro vs V3 Pro models with audio
@pytest.mark.asyncio
async def test_kling_e2e_model_and_audio_variations(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test different Kling models and audio settings."""
    # Grant credits
    fake_db_state.balances[test_user_id] = 3000
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 3000

    subagent = ImageSubagent(config=mock_config)

    # Test O3 Pro without audio
    mock_client_o3_no_audio = AsyncMock(spec=FalKlingClient)
    mock_client_o3_no_audio.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-o3-no-audio",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    mock_client_o3_no_audio.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/o3-no-audio.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )

    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_client_o3_no_audio
    install_video_provider(subagent, "fal", kling_provider)

    context_o3_no_audio = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result_o3_no_audio = await subagent.execute("generate a video", context_o3_no_audio)

    assert result_o3_no_audio.success is True
    assert result_o3_no_audio.data is not None
    assert result_o3_no_audio.data["video_url"] == "https://cdn.fal.ai/o3-no-audio.mp4"

    balance_after_o3_no_audio = await video_credits_dal.get_balance(test_user_id)
    o3_no_audio_cost = estimate_cost(
        duration_seconds=5,
        tier="pro",
        provider="fal",
        kling_model="o3-pro",
        audio_enabled=False,
    )
    assert balance_after_o3_no_audio == initial_balance - o3_no_audio_cost

    # Test O3 Pro with audio
    mock_client_o3_with_audio = AsyncMock(spec=FalKlingClient)
    mock_client_o3_with_audio.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-o3-with-audio",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=True,
        )
    )
    mock_client_o3_with_audio.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/o3-with-audio.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=True,
        )
    )

    kling_provider.client = mock_client_o3_with_audio
    install_video_provider(subagent, "fal", kling_provider)

    context_o3_with_audio = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": True,
    }
    result_o3_with_audio = await subagent.execute("generate a video", context_o3_with_audio)

    assert result_o3_with_audio.success is True
    assert result_o3_with_audio.data is not None
    assert result_o3_with_audio.data["video_url"] == "https://cdn.fal.ai/o3-with-audio.mp4"

    balance_after_o3_with_audio = await video_credits_dal.get_balance(test_user_id)
    o3_with_audio_cost = estimate_cost(
        duration_seconds=5,
        tier="pro",
        provider="fal",
        kling_model="o3-pro",
        audio_enabled=True,
    )
    assert balance_after_o3_with_audio == balance_after_o3_no_audio - o3_with_audio_cost

    # Test V3 Pro with audio
    mock_client_v3_with_audio = AsyncMock(spec=FalKlingClient)
    mock_client_v3_with_audio.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-v3-with-audio",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="v3-pro",
            audio_enabled=True,
        )
    )
    mock_client_v3_with_audio.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/v3-with-audio.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="v3-pro",
            audio_enabled=True,
        )
    )

    kling_provider.client = mock_client_v3_with_audio
    install_video_provider(subagent, "fal", kling_provider)

    context_v3_with_audio = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "v3-pro",
        "audio_enabled": True,
    }
    result_v3_with_audio = await subagent.execute("generate a video", context_v3_with_audio)

    assert result_v3_with_audio.success is True
    assert result_v3_with_audio.data is not None
    assert result_v3_with_audio.data["video_url"] == "https://cdn.fal.ai/v3-with-audio.mp4"

    final_balance = await video_credits_dal.get_balance(test_user_id)
    v3_with_audio_cost = estimate_cost(
        duration_seconds=5,
        tier="pro",
        provider="fal",
        kling_model="v3-pro",
        audio_enabled=True,
    )
    assert final_balance == balance_after_o3_with_audio - v3_with_audio_cost


# Test 6: BYOK tier can generate videos with own key (bypasses credits)
@pytest.mark.asyncio
async def test_kling_e2e_byok_tier_success(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that BYOK tier users can generate Kling videos using their own API key."""
    fake_db_state.balances[test_user_id] = 0
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=FalKlingClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-byok",
            prompt="test prompt",
            duration_seconds=5,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/byok-video.mp4",
            prompt="test prompt",
            duration_seconds=5,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )

    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_client
    install_video_provider(subagent, "fal", kling_provider)

    context = {
        "user_id": str(test_user_id),
        "tier": "byok",
        "duration": 5,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True, f"BYOK tier should succeed, got: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data

    final_balance = await video_credits_dal.get_balance(test_user_id)
    assert final_balance == 0


# Test 7: Pro tier supports all provider durations (no tier cap)
@pytest.mark.asyncio
async def test_kling_e2e_pro_tier_all_durations(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that Pro tier supports all Kling durations (no tier-based cap)."""
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(cast(Any, fake_pool))

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=FalKlingClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-kling-duration",
            prompt="test prompt",
            duration_seconds=10,
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            url="https://cdn.fal.ai/duration-video.mp4",
            prompt="test prompt",
            duration_seconds=10,
            status="finished",
            kling_model="o3-pro",
            audio_enabled=False,
        )
    )

    kling_provider = FalKlingProvider("test-fal-key")
    kling_provider.client = mock_client
    install_video_provider(subagent, "fal", kling_provider)

    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 10,
        "mode": "video",
        "video_provider": "fal",
        "kling_model": "o3-pro",
        "audio_enabled": False,
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True

    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(
        duration_seconds=10,
        tier="pro",
        provider="fal",
        kling_model="o3-pro",
        audio_enabled=False,
    )
    assert final_balance == 1000 - cost
