from __future__ import annotations

import json
import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

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

    with patch(
        "orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock
    ) as proc:
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            result = await extract_memories(
                ctx, user_id, conversation_id, messages_json
            )

    assert result["status"] == "ok"
    proc.assert_awaited_once()
    assert proc.await_args is not None
    extracted_text = proc.await_args.kwargs["text"]

    assert "memory_write" not in extracted_text.lower()
    assert "user: I live in Adelaide" in extracted_text
    assert "assistant: Anything else?" in extracted_text
