from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
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


class FakeDedupQueue:
    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.accepted_job_ids: set[str] = set()

    async def enqueue_job(
        self,
        *args: Any,
        _job_id: str | None = None,
        _defer_by: timedelta | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace | None:
        self.attempts.append(
            {
                "args": args,
                "job_id": _job_id,
                "defer_by": _defer_by,
                "kwargs": kwargs,
            }
        )
        if _job_id is not None and _job_id in self.accepted_job_ids:
            return None
        if _job_id is not None:
            self.accepted_job_ids.add(_job_id)
        return SimpleNamespace(job_id=_job_id)


async def _not_disconnected() -> bool:
    return False


async def _collect_stream(
    store: FakeMemoryStore,
    completion_events: AsyncIterator[dict[str, Any]],
    *,
    queue: FakeDedupQueue | None = None,
    conversation_uuid: uuid.UUID | None = None,
    is_disconnected=None,
) -> list[str]:
    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion_events:
            yield event

    frames: list[str] = []
    effective_conversation_uuid = conversation_uuid or uuid.uuid4()
    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        async for frame in stream_sse_chat(
            settings=Settings(mock_llm=False),
            provider_config=ProviderConfig(name="openrouter", model="test-model"),
            system_prompt="system",
            user_message="hello",
            request_id="req_123",
            conversation_id=f"conv_{effective_conversation_uuid.hex}",
            is_disconnected=is_disconnected or _not_disconnected,
            memory_store=store,
            user_id=uuid.uuid4(),
            conversation_uuid=effective_conversation_uuid,
            queue=queue,
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
    assert store.row["status"] == "error"
    assert any("event: error" in frame for frame in frames)
    assert any(call.get("status") == "error" for call in store.update_calls)
    assert store.row["status"] == "error"


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
async def test_streaming_generator_close_forced_status_cancelled() -> None:
    async def streaming_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        event = asyncio.Event()
        await event.wait()

    store = FakeMemoryStore()
    frames: list[str] = []
    conversation_uuid = uuid.uuid4()

    async def collect_stream() -> None:
        async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            async for event in streaming_completion():
                yield event

        with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
            async for frame in stream_sse_chat(
                settings=Settings(mock_llm=False),
                provider_config=ProviderConfig(name="openrouter", model="test-model"),
                system_prompt="system",
                user_message="hello",
                request_id="req_123",
                conversation_id=f"conv_{conversation_uuid.hex}",
                is_disconnected=_not_disconnected,
                memory_store=store,
                user_id=uuid.uuid4(),
                conversation_uuid=conversation_uuid,
            ):
                frames.append(frame)

    stream_task = asyncio.create_task(collect_stream())
    while not store.insert_calls:
        await asyncio.sleep(0)
    while not any("event: token" in frame for frame in frames):
        await asyncio.sleep(0)

    stream_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream_task

    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert store.row["content"] == "partial"
    assert any("event: token" in frame for frame in frames)


@pytest.mark.asyncio
async def test_disconnected_stream_updates_status_cancelled_and_skips_extraction() -> None:
    async def partial_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        yield {"type": "content_delta", "content": "more"}

    disconnected: dict[str, int] = {"count": 0}

    async def after_first_disconnect() -> bool:
        disconnected["count"] += 1
        return disconnected["count"] >= 2

    queue = FakeDedupQueue()
    store = FakeMemoryStore()

    frames = await _collect_stream(
        store,
        partial_completion(),
        queue=queue,
        is_disconnected=after_first_disconnect,
    )

    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert store.row["content"] == "partial"
    extraction_attempts = [
        attempt for attempt in queue.attempts if attempt["args"][0] == "extract_memories"
    ]
    assert extraction_attempts == []
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


@pytest.mark.asyncio
async def test_extraction_enqueue_uses_stable_conversation_debounce_key() -> None:
    async def successful_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "answer"}
        yield {"type": "done", "finish_reason": "stop"}

    conversation_uuid = uuid.uuid4()
    queue = FakeDedupQueue()

    for _ in range(5):
        await _collect_stream(
            FakeMemoryStore(),
            successful_completion(),
            queue=queue,
            conversation_uuid=conversation_uuid,
        )

    extraction_attempts = [
        attempt
        for attempt in queue.attempts
        if attempt["args"]
        and attempt["args"][0] == "extract_memories"
        and attempt.get("job_id") == f"extract:{conversation_uuid}"
    ]

    assert len(extraction_attempts) == 5
    assert {attempt["job_id"] for attempt in extraction_attempts} == {
        f"extract:{conversation_uuid}"
    }
    assert all(attempt["defer_by"] == timedelta(seconds=30) for attempt in extraction_attempts)
    # The first duplicate enqueue schedules a follow-up extraction so turns
    # that arrive during an in-flight run are not lost; subsequent duplicates
    # collapse into the same deterministic follow-up _job_id and arq drops them.
    assert queue.accepted_job_ids == {
        f"extract:{conversation_uuid}",
        f"extract:{conversation_uuid}:followup",
    }
    followup_attempts = [
        attempt
        for attempt in queue.attempts
        if attempt.get("job_id") == f"extract:{conversation_uuid}:followup"
    ]
    assert len(followup_attempts) == 4
    assert all(attempt["defer_by"] == timedelta(seconds=60) for attempt in followup_attempts)


def test_extract_memories_worker_registration_does_not_retain_result_key() -> None:
    from orchestrator.worker.worker import worker

    extract_function = worker.functions["extract_memories"]

    assert extract_function.keep_result_s == 0
