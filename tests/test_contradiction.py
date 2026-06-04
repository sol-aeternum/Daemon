from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
import pytest

from orchestrator.memory.dedup import check_contradiction, deduplicate_facts
from orchestrator.memory.extraction import ExtractedFact


def _new_fact(content: str, slot: str | None = None) -> ExtractedFact:
    return ExtractedFact(content=content, category="fact", confidence=0.9, slot=slot)


class MockLitellmResponse:
    def __init__(self, content: str):
        self._content = content

    def model_dump(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def dict(self):
        return self.model_dump()


@pytest.mark.asyncio
async def test_check_contradiction_yes() -> None:
    with patch("orchestrator.memory.dedup.litellm.acompletion") as mock:
        mock.return_value = MockLitellmResponse("YES. Fact B states the opposite of Fact A.")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User does not drive a Tesla",
        )
        assert contradiction is True
        assert "opposite" in explanation.lower()


@pytest.mark.asyncio
async def test_check_contradiction_no() -> None:
    with patch("orchestrator.memory.dedup.litellm.acompletion") as mock:
        mock.return_value = MockLitellmResponse("NO. Both facts can be true simultaneously.")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User owns a Tesla",
        )
        assert contradiction is False
        assert explanation == ""


@pytest.mark.asyncio
async def test_check_contradiction_llm_failure() -> None:
    with patch("orchestrator.memory.dedup.litellm.acompletion") as mock:
        mock.side_effect = Exception("LLM unavailable")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User flies a plane",
        )
        assert contradiction is False
        assert explanation == ""


@pytest.mark.asyncio
async def test_dedup_supersession_with_contradiction() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
        "metadata": {"contradiction_detected": True},
    }

    with (
        patch("orchestrator.memory.dedup.embed_documents", new_callable=AsyncMock) as embed,
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        embed.return_value = [[0.01, 0.02]]
        litellm_mock.return_value = MockLitellmResponse("YES. Fact B directly contradicts Fact A.")
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    call_kwargs = store.supersede_memory.await_args.kwargs
    assert call_kwargs["metadata"] is not None
    assert call_kwargs["metadata"]["contradiction_detected"] is True
    assert "contradicts" in call_kwargs["metadata"]["contradiction_explanation"].lower()


@pytest.mark.asyncio
async def test_check_contradiction_uses_background_reasoning_model() -> None:
    """Test that check_contradiction routes to BACKGROUND_REASONING_MODEL."""
    mock_settings = Mock()
    mock_settings.background_reasoning_model = "openrouter/deepseek/deepseek-chat"

    with (
        patch("orchestrator.memory.dedup.get_settings", return_value=mock_settings),
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        litellm_mock.return_value = MockLitellmResponse("NO. The facts are consistent.")
        await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert litellm_mock.await_args is not None
        call_kwargs = litellm_mock.await_args.kwargs
        assert call_kwargs["model"] == "openrouter/deepseek/deepseek-chat"


@pytest.mark.asyncio
async def test_check_contradiction_empty_model_string_passed_directly() -> None:
    """Test that empty model string is passed through to LLM as-is."""
    mock_settings = Mock()
    mock_settings.background_reasoning_model = ""

    with (
        patch("orchestrator.memory.dedup.get_settings", return_value=mock_settings),
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        litellm_mock.return_value = MockLitellmResponse("NO. The facts are consistent.")
        await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert litellm_mock.await_args is not None
        call_kwargs = litellm_mock.await_args.kwargs
        assert call_kwargs["model"] == ""


@pytest.mark.asyncio
async def test_check_contradiction_whitespace_model_string_passed_directly() -> None:
    """Test that whitespace-only model string is passed through to LLM as-is."""
    mock_settings = Mock()
    mock_settings.background_reasoning_model = "   "

    with (
        patch("orchestrator.memory.dedup.get_settings", return_value=mock_settings),
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        litellm_mock.return_value = MockLitellmResponse("NO. The facts are consistent.")
        await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert litellm_mock.await_args is not None
        call_kwargs = litellm_mock.await_args.kwargs
        assert call_kwargs["model"] == "   "


@pytest.mark.asyncio
async def test_check_contradiction_none_model_passed_directly() -> None:
    """Test that None model string is passed through to LLM as-is."""
    mock_settings = Mock()
    mock_settings.background_reasoning_model = None

    with (
        patch("orchestrator.memory.dedup.get_settings", return_value=mock_settings),
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        litellm_mock.side_effect = Exception("Model not found")
        contradiction, explanation = await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert contradiction is False
        assert explanation == ""


@pytest.mark.asyncio
async def test_dedup_supersession_retries_without_metadata_column() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    fallback_memory = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
    }
    store.supersede_memory.side_effect = [
        asyncpg.UndefinedColumnError('column "metadata" does not exist'),
        fallback_memory,
    ]

    with (
        patch("orchestrator.memory.dedup.embed_documents", new_callable=AsyncMock) as embed,
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        embed.return_value = [[0.01, 0.02]]
        litellm_mock.return_value = MockLitellmResponse("YES. Fact B directly contradicts Fact A.")
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    assert result.superseded[0] == fallback_memory
    assert store.supersede_memory.await_count == 2
    first_call = store.supersede_memory.await_args_list[0].kwargs
    second_call = store.supersede_memory.await_args_list[1].kwargs
    assert first_call["metadata"] is not None
    assert second_call["metadata"] is None


@pytest.mark.asyncio
async def test_dedup_supersession_proceeds_on_llm_failure() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
    }

    with (
        patch("orchestrator.memory.dedup.embed_documents", new_callable=AsyncMock) as embed,
        patch("orchestrator.memory.dedup.litellm.acompletion") as litellm_mock,
    ):
        embed.return_value = [[0.01, 0.02]]
        litellm_mock.side_effect = Exception("LLM unavailable")
        result = await deduplicate_facts(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    call_kwargs = store.supersede_memory.await_args.kwargs
    assert call_kwargs["metadata"] is None
