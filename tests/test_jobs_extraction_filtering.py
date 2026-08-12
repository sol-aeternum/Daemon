from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.memory.extraction import messages_to_extraction_text
from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import _is_memory_write_artifact, extract_memories


def test_is_memory_write_artifact_detects_tool_calls() -> None:
    message = {
        "role": "assistant",
        "content": "Saving this memory now",
        "tool_calls": [{"name": "memory_write", "arguments": {"content": "x"}}],
    }
    assert _is_memory_write_artifact(message) is True


def test_is_memory_write_artifact_ignores_regular_messages() -> None:
    message = {
        "role": "user",
        "content": "I work on Daemon every day",
    }
    assert _is_memory_write_artifact(message) is False


@pytest.mark.asyncio
async def test_extract_memories_filters_memory_write_artifacts() -> None:
    store = AsyncMock()
    ctx = cast(dict[str, object], {"store": store})
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    messages_json = json.dumps(
        [
            {"role": "user", "content": "I live in Adelaide"},
            {
                "role": "assistant",
                "content": "I'll save that.",
                "tool_calls": [
                    {
                        "name": "memory_write",
                        "arguments": {"content": "User lives in Adelaide"},
                    }
                ],
            },
            {
                "role": "tool",
                "content": '{"ok": true, "tool": "memory_write"}',
                "tool_results": [{"tool": "memory_write", "ok": True}],
            },
            {"role": "assistant", "content": "Anything else?"},
        ]
    )

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], False)  # success, no new memories, no continuation
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            result = await extract_memories(ctx, user_id, conversation_id, messages_json)

    assert result["status"] == "ok"
    proc.assert_awaited_once()
    assert proc.await_args is not None
    extracted_text = proc.await_args.kwargs["text"]

    assert "memory_write" not in extracted_text.lower()
    assert "[User]: I live in Adelaide" in extracted_text
    assert "[Assistant]: Anything else?" in extracted_text


def test_extraction_text_uses_bracketed_role_markers() -> None:
    messages = [
        {"role": "user", "content": "My name is Julian"},
        {"role": "assistant", "content": "Nice to meet you, Julian!"},
        {"role": "user", "content": "I'm building Daemon"},
    ]
    text = messages_to_extraction_text(messages)
    assert "[User]:" in text
    assert "[Assistant]:" in text
    assert "user: " not in text
    assert "assistant: " not in text
    assert "My name is Julian" in text
    assert "Nice to meet you, Julian!" in text
    assert "I'm building Daemon" in text


@pytest.mark.asyncio
async def test_artifact_only_page_advances_filter_checkpoint() -> None:
    store = object.__new__(MemoryStore)
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(None, None))
    store.get_messages_after_cursor = AsyncMock(
        return_value=[
            {
                "id": "artifact-1",
                "role": "assistant",
                "content": "Saving this memory now",
                "created_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                "tool_calls": [{"name": "memory_write", "arguments": {"content": "x"}}],
            }
        ]
    )
    store.log_extraction = AsyncMock(return_value={"id": "checkpoint"})
    ctx = cast(dict[str, object], {"store": store})

    with (
        patch("orchestrator.worker.jobs.MemoryStore", object),
        patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as process,
    ):
        result = await extract_memories(ctx, uuid.uuid4(), uuid.uuid4())

    process.assert_not_awaited()
    store.log_extraction.assert_awaited_once()
    assert store.log_extraction.await_args is not None
    assert (
        store.log_extraction.await_args.kwargs["dedup_results"]["filtered_page_checkpoint"] is True
    )
    assert result["last_processed_message_id"] == "artifact-1"
