from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from orchestrator.config import ProviderConfig, Settings
from orchestrator.daemon import stream_sse_chat


class FakeMemoryStore:
    def __init__(self) -> None:
        self.message_id = uuid.uuid4()
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.row: dict[str, Any] | None = None

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        self.insert_calls.append(kwargs)
        self.row = {"id": self.message_id, **kwargs}
        return {"id": self.message_id}

    async def update_message(self, *, message_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append({"message_id": message_id, **kwargs})
        if self.row is not None:
            self.row.update({key: value for key, value in kwargs.items() if value is not None})
        return self.row or {"id": message_id}


async def _not_disconnected() -> bool:
    return False


async def _collect_stream(
    store: FakeMemoryStore,
    completion_events: AsyncIterator[dict[str, Any]],
) -> list[str]:
    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion_events:
            yield event

    frames: list[str] = []
    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        async for frame in stream_sse_chat(
            settings=Settings(mock_llm=False),
            provider_config=ProviderConfig(name="openrouter", model="test-model"),
            system_prompt="system",
            user_message="hello",
            request_id="req_123",
            conversation_id=f"conv_{uuid.uuid4().hex}",
            is_disconnected=_not_disconnected,
            memory_store=store,
            user_id=uuid.uuid4(),
            conversation_uuid=uuid.uuid4(),
        ):
            frames.append(frame)
    return frames


@pytest.mark.asyncio
async def test_real_stream_abort_leaves_streaming_row_with_partial_content() -> None:
    async def aborting_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        raise RuntimeError("simulated stream failure")

    store = FakeMemoryStore()

    frames = await _collect_stream(store, aborting_completion())

    assert len(store.insert_calls) == 1
    assert store.insert_calls[0]["role"] == "assistant"
    assert store.insert_calls[0]["content"] == ""
    assert store.insert_calls[0]["status"] == "streaming"
    assert store.row is not None
    assert store.row["content"] == "partial"
    assert store.row["status"] == "streaming"
    assert any("event: error" in frame for frame in frames)
    assert all(call.get("status") != "complete" for call in store.update_calls)


@pytest.mark.asyncio
async def test_real_completed_stream_updates_single_row_to_complete() -> None:
    async def successful_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "hel"}
        yield {"type": "content_delta", "content": "lo"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()

    frames = await _collect_stream(store, successful_completion())

    assert len(store.insert_calls) == 1
    assert store.row is not None
    assert store.row["content"] == "hello"
    assert store.row["status"] == "complete"
    assert sum(1 for call in store.update_calls if call.get("status") == "complete") == 1
    assert any("event: final" in frame for frame in frames)
    assert any("event: done" in frame for frame in frames)


@pytest.mark.asyncio
async def test_real_completed_stream_keeps_advisor_traces_on_preinserted_row() -> None:
    async def completion_with_advisor_trace() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "answer"}
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_1",
            "trace_key": "req_123:advisor_1",
            "event_scope": "advisor",
        }
        yield {
            "type": "advisor_text_delta",
            "advisor_id": "advisor_1",
            "content": "nested advice",
            "event_scope": "advisor",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_1",
            "status": "completed",
            "tokens_in": 3,
            "tokens_out": 2,
            "event_scope": "advisor",
        }
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()

    await _collect_stream(store, completion_with_advisor_trace())

    assert len(store.insert_calls) == 1
    final_updates = [call for call in store.update_calls if call.get("status") == "complete"]
    assert len(final_updates) == 1
    advisor_traces = final_updates[0]["advisor_traces"]
    assert advisor_traces["advisor_1"]["text"] == "nested advice"
    assert advisor_traces["advisor_1"]["status"] == "completed"
