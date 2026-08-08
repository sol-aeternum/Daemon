from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory import summary as summary_module
from orchestrator.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_extraction_summary_advances_same_persisted_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={
            "summary": "existing",
            "summary_updated_at": None,
            "metadata": {"last_summarized_msg_count": 3},
        }
    )
    store.count_summary_messages = AsyncMock(return_value=5)
    store.get_summary_message_batch = AsyncMock(
        return_value=[
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ]
    )
    store.update_conversation_summary = AsyncMock(return_value=True)

    provider = SimpleNamespace(
        timeout_s=10,
        base_url=None,
        api_key=None,
        extra_headers=None,
    )
    settings = SimpleNamespace(
        auto_fast_model="openrouter/test-model",
        get_provider_config=lambda _: provider,
    )
    monkeypatch.setattr(summary_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        summary_module.litellm,
        "acompletion",
        AsyncMock(
            return_value=SimpleNamespace(choices=[{"message": {"content": "Updated summary."}}])
        ),
    )

    result = await summary_module.generate_or_update_summary(conversation_id, store)

    assert result == "Updated summary."
    store.get_summary_message_batch.assert_awaited_once_with(
        conversation_id,
        offset=3,
        limit=20,
    )
    store.update_conversation_summary.assert_awaited_once_with(
        conversation_id,
        summary="Updated summary.",
        expected_summary_updated_at=None,
        summarized_message_count=5,
    )
