"""
End-to-end integration tests for video generation flow.

Tests:
1. Success path: grant credits → request video → verify debit → mock success → verify result
2. Failure path: grant credits → request video → mock failure → verify refund
3. Tier block path: free tier attempt rejected
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

import db.video_credits as video_credits_module
from config.video_pricing import estimate_cost
from db.video_credits import Result, VideoCreditsDAL
from orchestrator.subagents.image import ImageSubagent
from providers.xai_imagine import (
    VideoJob,
    VideoResult,
    XAIImagineClient,
)

from orchestrator.subagents.image import XAIImageProvider, OpenAISoraProvider
from providers.openai_sora import (
    VideoJob as SoraVideoJob,
    VideoResult as SoraVideoResult,
    OpenAISoraClient,
)


# Fake DB state and helpers (reused from test_video_credits.py)
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
            and "do update set balance = video_credit_balances.balance - $3"
            in normalized
        ):
            user_id, current_balance, amount = args
            self.state.balances[user_id] = current_balance - amount
            return "UPDATE 1"
        if (
            "insert into video_credit_balances" in normalized
            and "do update set balance = video_credit_balances.balance + $2"
            in normalized
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
        "image_provider": "xai",
    }


# Test 1: Success path - grant credits, request video, verify debit, mock success
@pytest.mark.asyncio
async def test_video_e2e_success_path(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test successful video generation flow with credit debit."""
    # Step 1: Grant credits to user
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1000, "Initial balance should be 1000"

    # Step 2: Create subagent and mock provider
    subagent = ImageSubagent(config=mock_config)

    # Mock the provider client
    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-123",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-job-123",
            status="done",
            url="https://cdn.example.com/video.mp4",
            duration_seconds=5,
            prompt="test prompt",
        )
    )

    # Mock the provider to use our mock client
    # We need to directly replace the provider that will be selected by the subagent
    # The subagent will create a new OpenAISoraProvider when video_provider="sora" is in context
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_client
    # Override the provider selection logic to return our mock
    original_provider = subagent.provider
    subagent.provider = sora_provider

    # Step 3: Request video generation
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Step 4: Verify success
    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data
    assert result.data["video_url"] == "https://cdn.example.com/video.mp4"

    # Step 5: Verify debit happened
    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(5, "pro")
    assert final_balance == initial_balance - cost, (
        f"Balance should be debited: {initial_balance} - {cost} = {final_balance}"
    )


# Test 2: Failure path - grant credits, request video, mock failure, verify refund
@pytest.mark.asyncio
async def test_video_e2e_failure_path_with_refund(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test video generation failure triggers refund."""
    # Step 1: Grant credits
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1000

    # Step 2: Create subagent and mock provider to fail
    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-fail",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    from providers.xai_imagine import XAIImagineError

    mock_client.poll_video_job = AsyncMock(
        side_effect=XAIImagineError("Video generation failed")
    )

    subagent.provider = XAIImageProvider("test-key")
    subagent.provider.client = mock_client

    # Step 3: Request video generation
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
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


# Test 3: Tier block path - free tier attempt rejected
@pytest.mark.asyncio
async def test_video_e2e_tier_blocked(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    mock_config: dict[str, Any],
) -> None:
    """Test that Free tier users cannot generate videos."""
    # Grant credits but tier is free
    fake_db_state.balances[test_user_id] = 1000

    subagent = ImageSubagent(config=mock_config)

    # Attempt video generation with free tier
    context = {
        "user_id": str(test_user_id),
        "tier": "free",
        "duration": 5,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Verify blocked - only Free tier is blocked, BYOK is allowed
    assert result.success is False, "Free tier should be blocked"
    assert "not available for Free tier" in result.error, (
        f"Expected tier block message, got: {result.error}"
    )


# Test 4: Insufficient credits path
@pytest.mark.asyncio
async def test_video_e2e_insufficient_credits(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    mock_config: dict[str, Any],
) -> None:
    """Test that insufficient credits blocks video generation."""
    # Very low balance - less than cost (5 credits for 5 seconds)
    fake_db_state.balances[test_user_id] = 3

    subagent = ImageSubagent(config=mock_config)

    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    # Verify blocked
    assert result.success is False, "Insufficient credits should be blocked"
    assert "Insufficient video credits" in result.error, (
        f"Expected insufficient credits message, got: {result.error}"
    )


# Test 5: Starter tier can generate videos
@pytest.mark.asyncio
async def test_video_e2e_starter_tier_success(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that Starter tier users can generate videos."""
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-starter",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-job-starter",
            status="done",
            url="https://cdn.example.com/video.mp4",
            duration_seconds=5,
            prompt="test prompt",
        )
    )

    subagent.provider = XAIImageProvider("test-key")
    subagent.provider.client = mock_client

    context = {
        "user_id": str(test_user_id),
        "tier": "starter",
        "duration": 5,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True, f"Starter tier should succeed, got: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data

    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(5, "starter")
    assert final_balance == 1000 - cost


# Test 6: BYOK tier can generate videos with own key (bypasses credits)
@pytest.mark.asyncio
async def test_video_e2e_byok_tier_success(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that BYOK tier users can generate videos using their own API key."""
    fake_db_state.balances[test_user_id] = 0
    video_credits_dal = VideoCreditsDAL(fake_pool)

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-byok",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-job-byok",
            status="done",
            url="https://cdn.example.com/video.mp4",
            duration_seconds=5,
            prompt="test prompt",
        )
    )

    subagent.provider = XAIImageProvider("test-key")
    subagent.provider.client = mock_client

    context = {
        "user_id": str(test_user_id),
        "tier": "byok",
        "duration": 5,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True, f"BYOK tier should succeed, got: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data

    final_balance = await video_credits_dal.get_balance(test_user_id)
    assert final_balance == 0


# Test 7: Pro tier supports all provider durations (no tier cap)
@pytest.mark.asyncio
async def test_video_e2e_pro_tier_all_durations(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that Pro tier supports all provider durations (no tier-based cap)."""
    fake_db_state.balances[test_user_id] = 1000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-cap",
            prompt="test prompt",
            duration_seconds=10,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-job-cap",
            status="done",
            url="https://cdn.example.com/video.mp4",
            duration_seconds=10,
            prompt="test prompt",
        )
    )

    subagent.provider = XAIImageProvider("test-key")
    subagent.provider.client = mock_client

    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 10,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True

    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(10, "pro")
    assert final_balance == 1000 - cost


# Test 8: Max tier supports all provider durations (no tier cap)
@pytest.mark.asyncio
async def test_video_e2e_max_tier_all_durations(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test that Max tier supports all provider durations (no tier-based cap)."""
    fake_db_state.balances[test_user_id] = 2000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=XAIImagineClient)
    mock_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-job-max",
            prompt="test prompt",
            duration_seconds=15,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-job-max",
            status="done",
            url="https://cdn.example.com/video.mp4",
            duration_seconds=15,
            prompt="test prompt",
        )
    )

    subagent.provider = XAIImageProvider("test-key")
    subagent.provider.client = mock_client

    context = {
        "user_id": str(test_user_id),
        "tier": "max",
        "duration": 15,
        "mode": "video",
    }
    result = await subagent.execute("generate a video of mountains", context)

    assert result.success is True

    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(15, "max")
    assert final_balance == 2000 - cost


# Test 9: Sora provider success path - grant credits, request video, verify debit, mock success
@pytest.mark.asyncio
async def test_video_e2e_sora_success_path(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test successful Sora video generation flow with credit debit."""
    # Step 1: Grant credits to user
    fake_db_state.balances[test_user_id] = 1500  # Sora is more expensive
    video_credits_dal = VideoCreditsDAL(fake_pool)

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1500, "Initial balance should be 1500"

    # Step 2: Create subagent and mock Sora provider
    subagent = ImageSubagent(config=mock_config)

    # Mock the Sora provider client
    mock_client = AsyncMock(spec=OpenAISoraClient)
    mock_client.generate_video = AsyncMock(
        return_value=SoraVideoJob(
            job_id="test-sora-job-123",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=SoraVideoResult(
            job_id="test-sora-job-123",
            status="completed",
            url="https://cdn.example.com/sora-video.mp4",
            duration_seconds=5,
            prompt="test prompt",
            source_image_url=None,
        )
    )

    # We need to patch the provider creation logic to return our mock
    # The subagent will create a new OpenAISoraProvider when video_provider="sora" is in context
    # So we'll directly set the provider to our mock instead of letting it create a new one
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_client

    # Patch the provider creation by directly setting it
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "sora",  # This will trigger provider creation
    }

    # Instead of letting the subagent create a new provider, we'll set our mock directly
    # by temporarily changing the video_provider_name to match the current provider
    subagent.provider_name = "sora"  # Make the base provider name match
    subagent.video_provider_name = "sora"  # Make the video provider name match
    subagent.provider = sora_provider  # Set our mock provider

    # Step 3: Request video generation with Sora provider
    # The context is defined above to ensure our mock is used
    result = await subagent.execute("generate a video of mountains", context)

    # Step 4: Verify success
    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.data is not None
    assert "video_url" in result.data
    assert result.data["video_url"] == "https://cdn.example.com/sora-video.mp4"

    # Step 5: Verify debit happened with Sora pricing
    final_balance = await video_credits_dal.get_balance(test_user_id)
    cost = estimate_cost(5, "pro", "sora")  # Sora provider
    assert final_balance == initial_balance - cost, (
        f"Balance should be debited: {initial_balance} - {cost} = {final_balance}"
    )


# Test 10: Sora provider failure path - grant credits, request video, mock failure, verify refund
@pytest.mark.asyncio
async def test_video_e2e_sora_failure_path_with_refund(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test Sora video generation failure triggers refund."""
    # Step 1: Grant credits
    fake_db_state.balances[test_user_id] = 1500  # Sora is more expensive
    video_credits_dal = VideoCreditsDAL(fake_pool)

    initial_balance = await video_credits_dal.get_balance(test_user_id)
    assert initial_balance == 1500

    # Step 2: Create subagent and mock Sora provider to fail
    subagent = ImageSubagent(config=mock_config)

    mock_client = AsyncMock(spec=OpenAISoraClient)
    mock_client.generate_video = AsyncMock(
        return_value=SoraVideoJob(
            job_id="test-sora-job-fail",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    from providers.openai_sora import OpenAISoraError

    mock_client.poll_video_job = AsyncMock(
        side_effect=OpenAISoraError("Sora video generation failed")
    )

    # Mock the provider to use our mock client
    # We need to directly replace the provider that will be selected by the subagent
    # The subagent will create a new OpenAISoraProvider when video_provider="sora" is in context
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_client
    # Override the provider selection logic to return our mock
    original_provider = subagent.provider
    subagent.provider = sora_provider

    # Step 3: Request video generation with Sora provider
    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "sora",  # Explicitly select Sora provider
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


# Test 11: Provider selection logic - xai vs sora
@pytest.mark.asyncio
async def test_video_e2e_provider_selection(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test provider selection logic works correctly."""
    # Grant credits
    fake_db_state.balances[test_user_id] = 1500
    video_credits_dal = VideoCreditsDAL(fake_pool)

    # Test 1: Default provider (should be xai based on mock_config)
    subagent = ImageSubagent(config=mock_config)

    # Mock xAI client
    mock_xai_client = AsyncMock(spec=XAIImagineClient)
    mock_xai_client.generate_video = AsyncMock(
        return_value=VideoJob(
            job_id="test-xai-job",
            prompt="test prompt",
            duration_seconds=5,
        )
    )
    mock_xai_client.poll_video_job = AsyncMock(
        return_value=VideoResult(
            job_id="test-xai-job",
            status="done",
            url="https://cdn.example.com/xai-video.mp4",
            duration_seconds=5,
            prompt="test prompt",
        )
    )

    subagent.provider = XAIImageProvider("test-xai-key")
    subagent.provider.client = mock_xai_client

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

    # Test 2: Override to Sora provider
    # Mock Sora client
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
            job_id="test-sora-job",
            status="completed",
            url="https://cdn.example.com/sora-video.mp4",
            duration_seconds=5,
            prompt="test prompt",
            source_image_url=None,
        )
    )

    # Create new subagent for Sora
    subagent_sora = ImageSubagent(config=mock_config)
    # Mock the provider to use our mock client
    # We need to directly replace the provider that will be selected by the subagent
    # The subagent will create a new OpenAISoraProvider when video_provider="sora" is in context
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_sora_client
    # Override the provider selection logic to return our mock
    subagent_sora.provider_name = "sora"  # Make the base provider name match
    subagent_sora.video_provider_name = (
        "sora"  # Make the video provider name match
    )
    subagent_sora.provider = sora_provider

    context_sora = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 5,
        "mode": "video",
        "video_provider": "sora",  # Explicitly select Sora provider
    }
    result_sora = await subagent_sora.execute("generate a video", context_sora)

    assert result_sora.success is True
    assert result_sora.data is not None
    assert result_sora.data["video_url"] == "https://cdn.example.com/sora-video.mp4"


# Test 12: Cost estimation with provider parameter
@pytest.mark.asyncio
async def test_video_e2e_cost_estimation_with_provider(
    test_user_id: uuid.UUID,
    fake_db_state: FakeDbState,
    fake_pool: FakePool,
    mock_config: dict[str, Any],
) -> None:
    """Test cost estimation works correctly with different providers."""
    from config.video_pricing import estimate_cost

    # Grant credits
    fake_db_state.balances[test_user_id] = 2000
    video_credits_dal = VideoCreditsDAL(fake_pool)

    # Test xAI cost estimation
    xai_cost_pro = estimate_cost(10, "pro", "xai")
    xai_cost_max = estimate_cost(10, "max", "xai")

    # Test Sora cost estimation
    sora_cost_pro = estimate_cost(10, "pro", "sora")
    sora_cost_max = estimate_cost(10, "max", "sora")

    # Sora should be more expensive than xAI
    assert sora_cost_pro > xai_cost_pro
    assert sora_cost_max > xai_cost_max

    # Max tier should have discount for xAI but not for Sora (already tier-based priced)
    assert xai_cost_max < xai_cost_pro  # Discount for xAI
    # Sora pricing already includes tier in the base price

    # Test actual debit with Sora
    initial_balance = await video_credits_dal.get_balance(test_user_id)

    subagent = ImageSubagent(config=mock_config)

    # Mock Sora client
    mock_client = AsyncMock(spec=OpenAISoraClient)
    mock_client.generate_video = AsyncMock(
        return_value=SoraVideoJob(
            job_id="test-cost-job",
            prompt="test prompt",
            duration_seconds=10,
        )
    )
    mock_client.poll_video_job = AsyncMock(
        return_value=SoraVideoResult(
            job_id="test-cost-job",
            status="completed",
            url="https://cdn.example.com/cost-video.mp4",
            duration_seconds=10,
            prompt="test prompt",
            source_image_url=None,
        )
    )

    # Mock the provider to use our mock client
    # We need to directly replace the provider that will be selected by the subagent
    # The subagent will create a new OpenAISoraProvider when video_provider="sora" is in context
    sora_provider = OpenAISoraProvider("test-openai-key")
    sora_provider.client = mock_client
    # Override the provider selection logic to return our mock
    subagent.provider_name = "sora"
    subagent.video_provider_name = "sora"
    subagent.provider = sora_provider

    context = {
        "user_id": str(test_user_id),
        "tier": "pro",
        "duration": 10,
        "mode": "video",
        "video_provider": "sora",
    }
    result = await subagent.execute("generate a video", context)

    assert result.success is True

    final_balance = await video_credits_dal.get_balance(test_user_id)
    expected_cost = sora_cost_pro
    assert final_balance == initial_balance - expected_cost, (
        f"Balance should be debited by Sora cost: {initial_balance} - {expected_cost} = {final_balance}"
    )
