"""Unit tests for memory_reflect tool."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.tools.memory_reflect import MemoryReflectTool


def _query_result() -> SimpleNamespace:
    return SimpleNamespace(
        embedding=[0.1] * 128,
        model="voyage-4-lite",
        storage_model="voyage-4-large",
    )


class MockLitellmResponse:
    def __init__(self, content: str):
        self._content = content

    def model_dump(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def dict(self):
        return self.model_dump()


@pytest.mark.asyncio
async def test_reflect_empty_topic():
    """Reflect with no topic returns appropriate message."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    tool = MemoryReflectTool(store, user_id)
    result = await tool.execute(topic="")

    assert "No topic provided" in result


@pytest.mark.asyncio
async def test_reflect_whitespace_topic():
    """Reflect with only whitespace topic returns appropriate message."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    tool = MemoryReflectTool(store, user_id)
    result = await tool.execute(topic="   ")

    assert "No topic provided" in result


@pytest.mark.asyncio
async def test_reflect_no_memories_found():
    """Reflect when no memories match returns conservative response."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            result = await tool.execute(topic="my hobbies")

            assert "No relevant memories found" in result


@pytest.mark.asyncio
async def test_reflect_successful_synthesis():
    """Reflect with matching memories returns synthesized reflection."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    memories = [
        {
            "id": uuid.uuid4(),
            "content": "User enjoys playing guitar",
            "category": "fact",
            "memory_slot": "hobbies",
            "source": "hybrid",
        },
        {
            "id": uuid.uuid4(),
            "content": "User has a Fender Stratocaster",
            "category": "fact",
            "memory_slot": "equipment",
            "source": "l0",
        },
    ]

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = memories

            with patch("orchestrator.tools.memory_reflect.litellm.acompletion") as mock_llm:
                mock_llm.return_value = MockLitellmResponse(
                    "Based on your memories, you have a passion for guitar playing and own quality equipment."
                )

                with patch.object(
                    tool := MemoryReflectTool(store, user_id),
                    "_get_orchestrator_model",
                    return_value="openrouter/moonshotai/kimi-k2.5",
                ):
                    with patch("orchestrator.tools.memory_reflect.get_settings") as mock_settings:
                        mock_settings.return_value.get_tier_config.return_value.orchestrator.model = "openrouter/moonshotai/kimi-k2.5"
                        mock_settings.return_value.get_provider_config.return_value.timeout_s = 60

                        result = await tool.execute(topic="my musical interests")

                        assert "Fender" in result or "guitar" in result.lower()


@pytest.mark.asyncio
async def test_reflect_includes_l0_memories():
    """Reflect retrieval includes L0 memories via include_l0=True."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    l0_memory = {
        "id": uuid.uuid4(),
        "content": "Critical preference: User always takes coffee black",
        "category": "fact",
        "memory_slot": "preferences",
        "source": "l0",
    }

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = [l0_memory]

            with patch("orchestrator.tools.memory_reflect.litellm.acompletion") as mock_llm:
                mock_llm.return_value = MockLitellmResponse(
                    "The user has a strong coffee preference."
                )

                tool = MemoryReflectTool(store, user_id)
                await tool.execute(topic="coffee preferences")

                assert mock_retrieve.call_args.kwargs["include_l0"] is True


@pytest.mark.asyncio
async def test_reflect_uses_expanded_retrieval_limit():
    """Reflect uses top-15 (default limit of 15)."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="anything")

            assert mock_retrieve.call_args.kwargs["limit"] == 15


@pytest.mark.asyncio
async def test_reflect_custom_limit():
    """Reflect respects custom limit parameter."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="anything", limit=25)

            assert mock_retrieve.call_args.kwargs["limit"] == 25


@pytest.mark.asyncio
async def test_reflect_llm_failure_returns_error():
    """Reflect returns error message when LLM call fails."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    memories = [
        {
            "id": uuid.uuid4(),
            "content": "User enjoys playing guitar",
            "category": "fact",
            "memory_slot": "hobbies",
            "source": "hybrid",
        },
    ]

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = memories

            with patch("orchestrator.tools.memory_reflect.litellm.acompletion") as mock_llm:
                mock_llm.side_effect = Exception("LLM unavailable")

                with patch.object(
                    tool := MemoryReflectTool(store, user_id),
                    "_get_orchestrator_model",
                    return_value="openrouter/moonshotai/kimi-k2.5",
                ):
                    with patch("orchestrator.tools.memory_reflect.get_settings") as mock_settings:
                        mock_settings.return_value.get_tier_config.return_value.orchestrator.model = "openrouter/moonshotai/kimi-k2.5"
                        mock_settings.return_value.get_provider_config.return_value.timeout_s = 60

                        result = await tool.execute(topic="my hobbies")

                        assert "Reflection synthesis failed" in result


@pytest.mark.asyncio
async def test_reflect_is_non_persistent():
    """Reflect does not write any memories to the store."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="test")

            store.insert_memory.assert_not_called()
            store.update_memory.assert_not_called()
            store.close_memory.assert_not_called()
            store.delete_memory.assert_not_called()


@pytest.mark.asyncio
async def test_reflect_tool_schema():
    """MemoryReflectTool has correct tool schema."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    tool = MemoryReflectTool(store, user_id)
    schema = tool.to_openai_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "memory_reflect"
    assert "topic" in schema["function"]["parameters"]["properties"]
    assert "limit" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["properties"]["limit"]["default"] == 15


def test_prompt_guidance_memory_reflect_vs_memory_read():
    """System prompt correctly distinguishes memory_reflect from memory_read."""
    from orchestrator.prompts import DAEMON_SYSTEM_PROMPT

    prompt = DAEMON_SYSTEM_PROMPT

    assert "memory_reflect" in prompt
    assert "memory_read" in prompt
    assert "synthesis" in prompt.lower() or "synthes" in prompt.lower()

    assert "simple factual" in prompt.lower() or "factual recall" in prompt.lower()
    assert "pattern" in prompt.lower() or "patterns" in prompt.lower()
    assert "synthesis, patterns, and history" in prompt.lower() or "synthesis" in prompt.lower()

    assert (
        "do not call it for simple factual lookups" in prompt.lower()
        or "simple factual" in prompt.lower()
    )
    assert "only call it when" in prompt.lower() or "only call it" in prompt.lower()
    assert "genuinely asks for synthesis" in prompt.lower() or "synthesis" in prompt.lower()

    assert (
        "non-persistent" in prompt.lower()
        or "no memory writes" in prompt.lower()
        or "produces no memory writes" in prompt.lower()
    )

    assert "expanded retrieval" in prompt.lower() or "top-15" in prompt.lower()

    assert "evolved" in prompt or "evolve" in prompt or "history" in prompt.lower()
    assert "patterns" in prompt.lower() or "pattern" in prompt.lower()


def test_prompt_version_bumped():
    """Prompt version was bumped after adding memory_reflect guidance."""
    from orchestrator.prompts import DAEMON_PROMPT_VERSION

    assert DAEMON_PROMPT_VERSION >= 3


@pytest.mark.asyncio
async def test_reflect_truncates_limit_to_max_50():
    """Reflect caps limit at 50 even when higher value is requested."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="anything", limit=100)

            assert mock_retrieve.call_args.kwargs["limit"] == 50


@pytest.mark.asyncio
async def test_reflect_enforces_minimum_limit_of_1():
    """Reflect enforces minimum limit of 1 even when 0 or negative is requested."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="anything", limit=0)

            assert mock_retrieve.call_args.kwargs["limit"] == 1


@pytest.mark.asyncio
async def test_reflect_passes_timeout_from_provider_config():
    """Reflect uses timeout_s from provider config in LLM call."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    memories = [
        {
            "id": uuid.uuid4(),
            "content": "User enjoys playing guitar",
            "category": "fact",
            "memory_slot": "hobbies",
            "source": "hybrid",
        },
    ]

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = memories

            with patch("orchestrator.tools.memory_reflect.litellm.acompletion") as mock_llm:
                mock_llm.return_value = MockLitellmResponse("Guitar hobby synthesis.")

                with patch.object(
                    tool := MemoryReflectTool(store, user_id),
                    "_get_orchestrator_model",
                    return_value="openrouter/moonshotai/kimi-k2.5",
                ):
                    with patch("orchestrator.tools.memory_reflect.get_settings") as mock_settings:
                        mock_settings.return_value.get_tier_config.return_value.orchestrator.model = "openrouter/moonshotai/kimi-k2.5"
                        mock_settings.return_value.get_provider_config.return_value.timeout_s = 30.0
                        mock_settings.return_value.get_provider_config.return_value.base_url = ""
                        mock_settings.return_value.get_provider_config.return_value.api_key = None
                        mock_settings.return_value.get_provider_config.return_value.extra_headers = {}
                        mock_settings.return_value.get_provider_config.return_value.requires_auth = False
                        mock_settings.return_value.get_provider_config.return_value.name = "test"

                        result = await tool.execute(topic="my hobbies")  # noqa: F841

                        mock_llm.assert_awaited_once()
                        assert mock_llm.await_args is not None
                        call_kwargs = mock_llm.await_args.kwargs
                        assert call_kwargs["timeout"] == 30.0


@pytest.mark.asyncio
async def test_reflect_uses_zero_timeout_when_configured():
    """Reflect handles zero timeout_s without error."""
    store = AsyncMock()
    user_id = uuid.uuid4()

    memories = [
        {
            "id": uuid.uuid4(),
            "content": "User enjoys playing guitar",
            "category": "fact",
            "memory_slot": "hobbies",
            "source": "hybrid",
        },
    ]

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = memories

            with patch("orchestrator.tools.memory_reflect.litellm.acompletion") as mock_llm:
                mock_llm.return_value = MockLitellmResponse("Synthesis.")

                with patch.object(
                    tool := MemoryReflectTool(store, user_id),
                    "_get_orchestrator_model",
                    return_value="openrouter/moonshotai/kimi-k2.5",
                ):
                    with patch("orchestrator.tools.memory_reflect.get_settings") as mock_settings:
                        mock_settings.return_value.get_tier_config.return_value.orchestrator.model = "openrouter/moonshotai/kimi-k2.5"
                        mock_settings.return_value.get_provider_config.return_value.timeout_s = 0.0
                        mock_settings.return_value.get_provider_config.return_value.base_url = ""
                        mock_settings.return_value.get_provider_config.return_value.api_key = None
                        mock_settings.return_value.get_provider_config.return_value.extra_headers = {}
                        mock_settings.return_value.get_provider_config.return_value.requires_auth = False
                        mock_settings.return_value.get_provider_config.return_value.name = "test"

                        result = await tool.execute(topic="my hobbies")  # noqa: F841

                        mock_llm.assert_awaited_once()
                        assert mock_llm.await_args is not None
                        call_kwargs = mock_llm.await_args.kwargs
                        assert call_kwargs["timeout"] == 0.0


@pytest.mark.asyncio
async def test_reflect_includes_dream_observations():
    """Reflect passes include_dream_observations=True to retrieval.

    Regression test: analysis flows (memory_reflect, diagnostics) must be
    able to see dream observations even though default factual retrieval
    excludes them.
    """
    store = AsyncMock()
    user_id = uuid.uuid4()

    with patch("orchestrator.tools.memory_reflect.embed_query_with_metadata") as mock_embed:
        mock_embed.return_value = _query_result()

        with patch("orchestrator.tools.memory_reflect.retrieve_memories_for_text") as mock_retrieve:
            mock_retrieve.return_value = []

            tool = MemoryReflectTool(store, user_id)
            await tool.execute(topic="my dreams and aspirations")

            assert mock_retrieve.call_args.kwargs.get("include_dream_observations") is True
