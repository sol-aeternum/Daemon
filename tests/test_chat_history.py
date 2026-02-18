"""Integration tests for chat history round-trip with page refresh scenario.

These tests verify that:
1. Multi-turn conversations are stored in DB
2. Page refresh (empty messages array) loads history from DB
3. DB history is used when conversation exists
4. Streaming messages are excluded from history
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.db import AppState


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client with mock DB."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def create_mock_app_state(mock_store: AsyncMock | None = None) -> AppState:
    """Create a mock AppState with optional memory store."""
    app_state = MagicMock(spec=AppState)
    app_state.memory_store = mock_store
    app_state.redis = None
    return app_state


def set_app_state(mock_app_state: AppState) -> None:
    """Set the app state on the FastAPI app."""
    app.state.app_state = mock_app_state


@pytest.mark.asyncio
async def test_chat_history_loaded_on_page_refresh(client, monkeypatch) -> None:
    """Test that DB history is loaded when frontend sends empty messages (page refresh).

    Scenario:
    1. Conversation exists in DB with prior messages
    2. Frontend sends request with conversation_id but empty messages array
    3. Endpoint should load history from DB and include it in LLM context
    """
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_conversation = {
        "id": conversation_id,
        "user_id": user_id,
        "pipeline": "cloud",
        "title": "Test conversation",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    # Mock messages representing a 3-turn conversation
    db_messages = [
        {"role": "user", "content": "Hello, my name is Alice", "status": "complete"},
        {
            "role": "assistant",
            "content": "Hello Alice! Nice to meet you.",
            "status": "complete",
        },
        {"role": "user", "content": "What's the weather like?", "status": "complete"},
        {
            "role": "assistant",
            "content": "I don't have real-time weather data.",
            "status": "complete",
        },
    ]

    captured_history: list[dict[str, Any]] | None = None

    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        nonlocal captured_history
        captured_history = history_messages
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "Hello"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    # Create mock store
    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(return_value=mock_conversation)
    mock_store.get_recent_messages = AsyncMock(return_value=db_messages)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})

    # Set mock app state directly on the app
    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "What was my name again?",
                "messages": [],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # Verify get_recent_messages was called with streaming exclusion
    mock_store.get_recent_messages.assert_called()
    # Find the call with exclude_status parameter
    calls_with_exclude = [
        call
        for call in mock_store.get_recent_messages.call_args_list
        if call.kwargs.get("exclude_status") == ["streaming"]
    ]
    assert len(calls_with_exclude) > 0, (
        "Expected at least one call with exclude_status=['streaming']"
    )

    # Verify history was populated from DB
    assert captured_history is not None
    assert len(captured_history) == 4
    assert captured_history[0]["role"] == "user"
    assert captured_history[0]["content"] == "Hello, my name is Alice"
    assert captured_history[1]["role"] == "assistant"
    assert captured_history[1]["content"] == "Hello Alice! Nice to meet you."


@pytest.mark.asyncio
async def test_chat_history_excludes_streaming_messages(client, monkeypatch) -> None:
    """Test that streaming messages are excluded from history loaded from DB.

    Scenario:
    1. Conversation exists with some complete and some streaming messages
    2. Page refresh occurs (empty messages array)
    3. Only complete messages should be included in LLM context
    """
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_conversation = {
        "id": conversation_id,
        "user_id": user_id,
        "pipeline": "cloud",
        "title": "Test conversation",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    # Mix of complete and streaming messages (as returned by get_recent_messages with exclude_status)
    db_messages = [
        {"role": "user", "content": "First user message", "status": "complete"},
        {
            "role": "assistant",
            "content": "First assistant response",
            "status": "complete",
        },
        # Streaming message is NOT in this list because get_recent_messages excludes it
        {"role": "user", "content": "Second user message", "status": "complete"},
    ]

    captured_history: list[dict[str, Any]] | None = None

    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        nonlocal captured_history
        captured_history = history_messages
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "Hi"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(return_value=mock_conversation)
    mock_store.get_recent_messages = AsyncMock(return_value=db_messages)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Continue",
                "messages": [],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200

    mock_store.get_recent_messages.assert_called()
    # Verify at least one call excluded streaming status
    calls_with_exclude = [
        call
        for call in mock_store.get_recent_messages.call_args_list
        if "streaming" in (call.kwargs.get("exclude_status") or [])
    ]
    assert len(calls_with_exclude) > 0

    # Verify streaming message is not in history
    assert captured_history is not None
    assert len(captured_history) == 3
    contents = [m["content"] for m in captured_history]
    assert "Partial streaming response..." not in contents
    assert "First user message" in contents


@pytest.mark.asyncio
async def test_chat_uses_frontend_messages_when_no_db_history(
    client, monkeypatch
) -> None:
    """Test that frontend messages are used when conversation doesn't exist in DB.

    Scenario:
    1. New conversation (no DB history)
    2. Frontend sends messages array
    3. Endpoint should use frontend messages as history
    """
    conversation_id = uuid.uuid4()

    captured_history: list[dict[str, Any]] | None = None

    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        nonlocal captured_history
        captured_history = history_messages
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "OK"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    # Store returns None for conversation (doesn't exist)
    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(return_value=None)
    mock_store.create_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "title": "New conversation",
        }
    )
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "What's my name?",
                "messages": [
                    {"role": "user", "content": "My name is Bob"},
                    {"role": "assistant", "content": "Hello Bob!"},
                ],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200

    assert captured_history is not None
    assert captured_history is not None
    assert len(captured_history) == 2
    assert captured_history[0]["role"] == "user"
    assert captured_history[0]["content"] == "My name is Bob"
    assert captured_history[1]["role"] == "assistant"
    assert captured_history[1]["content"] == "Hello Bob!"


@pytest.mark.asyncio
async def test_chat_empty_messages_with_new_conversation(client, monkeypatch) -> None:
    """Test that new conversation with empty messages starts fresh.

    Scenario:
    1. New conversation (doesn't exist in DB)
    2. Frontend sends empty messages array (e.g., first load with no history)
    3. Endpoint should handle gracefully with no history
    """
    conversation_id = uuid.uuid4()

    captured_history: list[dict[str, Any]] | None = None

    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        nonlocal captured_history
        captured_history = history_messages
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "Hi"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(return_value=None)
    mock_store.create_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "title": "Test",
        }
    )
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})

    mock_app_state = create_mock_app_state(mock_store)
    set_app_state(mock_app_state)

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert captured_history is None or len(captured_history) == 0


@pytest.mark.asyncio
async def test_chat_multiple_turns_roundtrip(client, monkeypatch) -> None:
    """Test full round-trip: messages persist and are retrieved on refresh.

    This simulates:
    1. User sends message (turn 1)
    2. Assistant responds (turn 1)
    3. Page refresh - history loaded from DB
    4. User sends another message (turn 2)
    """
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()

    histories_captured: list[list[dict[str, Any]] | None] = []

    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        histories_captured.append(history_messages)
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "Response"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    # Simulate DB with accumulated messages
    db_messages: list[dict] = []

    def create_mock_store():
        store = AsyncMock()
        store.get_conversation = AsyncMock(
            return_value={
                "id": conversation_id,
                "user_id": user_id,
                "pipeline": "cloud",
                "title": "Test conversation",
            }
        )
        # Return current DB messages (excluding streaming)
        store.get_recent_messages = AsyncMock(
            return_value=[
                {"role": m["role"], "content": m["content"], "status": m["status"]}
                for m in db_messages
                if m["status"] != "streaming"
            ]
        )

        async def insert_message(*, conversation_id, user_id, role, content, **kwargs):
            msg_id = uuid.uuid4()
            db_messages.append(
                {
                    "id": msg_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "status": kwargs.get("status", "complete"),
                }
            )
            return {"id": msg_id}

        store.insert_message = AsyncMock(side_effect=insert_message)
        return store

    # Turn 1: Initial message with no prior history
    mock_store_1 = create_mock_store()
    set_app_state(create_mock_app_state(mock_store_1))
    histories_captured.clear()

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "My favorite color is blue",
                "messages": [],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    turn1_history = histories_captured[-1]
    assert turn1_history is None or len(turn1_history) == 0

    # Simulate first turn being stored in DB
    db_messages.extend(
        [
            {
                "role": "user",
                "content": "My favorite color is blue",
                "status": "complete",
            },
            {
                "role": "assistant",
                "content": "Got it! I'll remember that.",
                "status": "complete",
            },
        ]
    )

    # Turn 2: Page refresh with empty messages - should load from DB
    mock_store_2 = create_mock_store()
    set_app_state(create_mock_app_state(mock_store_2))
    histories_captured.clear()

    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat):
        response = await client.post(
            "/chat",
            json={
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "What is my favorite color?",
                "messages": [],
            },
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200

    turn2_history = histories_captured[-1]
    assert turn2_history is not None
    # History includes prior messages + current user message (3 total)
    assert len(turn2_history) >= 2
    # Should contain the prior turn from DB
    has_user_msg = any("blue" in m.get("content", "") for m in turn2_history)
    has_assistant_msg = any("remember" in m.get("content", "") for m in turn2_history)
    assert has_user_msg
    assert has_assistant_msg

    mock_store_2.get_recent_messages.assert_called()
    calls_with_exclude = [
        call
        for call in mock_store_2.get_recent_messages.call_args_list
        if call.kwargs.get("exclude_status") == ["streaming"]
    ]
    assert len(calls_with_exclude) > 0
