"""Unit tests for L0 memory injection in build_memory_context."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.injection import (
    L0_TOKEN_BUDGET,
    MAX_L0_CHARS,
    _format_l0_block,
    _truncate_to_chars,
    build_memory_context,
    estimate_tokens,
)


class MockRecord:
    def __init__(self, **kwargs):
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data.keys())

    def keys(self):
        return self._data.keys()


@pytest.mark.parametrize(
    ("text", "limit", "expected_suffix"),
    [
        ("short", 10, "short"),
        ("exactly10!", 10, "exactly10!"),
        ("this is longer than 10", 10, "this is..."),
        ("abc", 3, "abc"),
        ("abcd", 3, "abc"),
    ],
)
def test_truncate_to_chars(text: str, limit: int, expected_suffix: str) -> None:
    result = _truncate_to_chars(text, limit)
    assert result.endswith(expected_suffix)


def test_format_l0_block_empty() -> None:
    result = _format_l0_block([])
    assert result == ""


def test_format_l0_block_single_memory() -> None:
    memories = [{"content": "User prefers dark mode"}]
    result = _format_l0_block(memories)
    assert "[FROZEN MEMORIES]" in result
    assert "User prefers dark mode" in result


def test_format_l0_block_multiple_memories() -> None:
    memories = [
        {"content": "User is a Python developer"},
        {"content": "User works at Acme Corp"},
        {"content": "User prefers email over chat"},
    ]
    result = _format_l0_block(memories)
    assert "[FROZEN MEMORIES]" in result
    assert "Python developer" in result
    assert "Acme Corp" in result
    assert "email over chat" in result


def test_format_l0_block_truncates_long_content() -> None:
    long_content = "x" * 1000
    memories = [{"content": long_content}]
    result = _format_l0_block(memories)
    lines = result.split("\n")
    content_line = lines[1] if len(lines) > 1 else ""
    assert len(content_line) <= MAX_L0_CHARS + 5


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1


def test_estimate_tokens_longer_text() -> None:
    text = "The quick brown fox jumps over the lazy dog"
    tokens = estimate_tokens(text)
    assert tokens > 5


@pytest.mark.asyncio
async def test_build_memory_context_no_l0_no_retrieved() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    store.get_l0_memories.return_value = []
    store.get_recent_messages.return_value = []
    store.get_recent_summaries.return_value = []

    result = await build_memory_context(store, conversation_id)
    assert result == ""


@pytest.mark.asyncio
async def test_build_memory_context_l0_at_top() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    store.get_l0_memories.return_value = [
        {"content": "Always remember: user is a Python developer", "category": "fact"},
    ]
    store.get_recent_messages.return_value = []
    store.get_recent_summaries.return_value = []

    result = await build_memory_context(store, conversation_id)
    assert "[FROZEN MEMORIES]" in result
    assert "Python developer" in result
    assert result.startswith('<memory_records trust="user_data">')
    assert result.rstrip().endswith("</memory_records>")


@pytest.mark.asyncio
async def test_build_memory_context_l0_bypasses_retrieval() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    store.get_l0_memories.return_value = [
        {"content": "Frozen fact", "category": "fact"},
    ]
    store.get_recent_messages.return_value = [
        {"role": "user", "content": "hello"},
    ]
    store.get_recent_summaries.return_value = []

    result = await build_memory_context(store, conversation_id)

    store.get_l0_memories.assert_called_once_with(user_id)
    assert "Frozen fact" in result


@pytest.mark.asyncio
async def test_build_memory_context_l0_enforces_token_budget() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    many_l0_memories = [
        {"content": f"Frozen memory number {i} " + ("x" * 100), "category": "fact"}
        for i in range(20)
    ]
    store.get_l0_memories.return_value = many_l0_memories
    store.get_recent_messages.return_value = []
    store.get_recent_summaries.return_value = []

    result = await build_memory_context(store, conversation_id)
    l0_block = result.split("About this user:")[0] if "About this user:" in result else result
    assert estimate_tokens(l0_block) <= L0_TOKEN_BUDGET * 1.5


@pytest.mark.asyncio
async def test_build_memory_context_l0_and_l1_separate() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    store.get_l0_memories.return_value = [
        {"content": "Frozen fact", "category": "fact"},
    ]
    store.get_recent_messages.return_value = [
        {"role": "user", "content": "tell me about Python"},
    ]
    store.get_recent_summaries.return_value = [
        {"content": "Previous session discussed Python", "category": "summary"},
    ]

    async def mock_retrieve(*args, **kwargs):
        return [
            {
                "id": "retrieved-memory",
                "content": "Retrieved fact about Python",
                "category": "fact",
                "similarity": 0.9,
            }
        ]

    with patch.object(store, "search_memories", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {
                "id": "retrieved-memory",
                "content": "Retrieved fact about Python",
                "category": "fact",
                "similarity": 0.9,
            }
        ]
        with (
            patch(
                "orchestrator.memory.injection.embed_query_with_metadata",
                AsyncMock(
                    return_value=SimpleNamespace(
                        embedding=[0.0] * 8,
                        model="voyage-4-lite",
                        storage_model="voyage-4-large",
                    )
                ),
            ),
            patch(
                "orchestrator.memory.injection.retrieve_memories_for_text",
                mock_retrieve,
            ),
        ):
            result = await build_memory_context(store, conversation_id)

    assert "[FROZEN MEMORIES]" in result
    assert "Frozen fact" in result
    frozen_pos = result.find("[FROZEN MEMORIES]")
    about_pos = result.find("About this user:")
    assert frozen_pos < about_pos


@pytest.mark.asyncio
async def test_build_memory_context_l0_not_in_search_memories() -> None:
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": str(conversation_id),
        "user_id": user_id,
    }
    store.get_l0_memories.return_value = []
    store.get_recent_messages.return_value = [
        {"role": "user", "content": "hello"},
    ]
    store.get_recent_summaries.return_value = []

    from orchestrator.memory import retrieval

    async def mock_retrieve(*args, **kwargs):
        return []

    with patch.object(retrieval, "retrieve_memories", mock_retrieve):
        result = await build_memory_context(store, conversation_id)

    assert "[FROZEN MEMORIES]" not in result
