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
    assert store.get_summary_message_batch.await_count == 1
    batch_kwargs = store.get_summary_message_batch.await_args.kwargs
    assert batch_kwargs["offset"] == 3
    assert batch_kwargs["limit"] == 20
    assert "snapshot_at" in batch_kwargs
    update_kwargs = store.update_conversation_summary.await_args.kwargs
    assert update_kwargs["summary"] == "Updated summary."
    assert update_kwargs["expected_summary_updated_at"] is None
    assert update_kwargs["summarized_message_count"] == 5
    assert "summary_snapshot_at" in update_kwargs


@pytest.mark.asyncio
async def test_inline_summary_signals_continuation_when_batch_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inline batch fills, the result must signal continuation."""
    from types import SimpleNamespace
    from uuid import uuid4

    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": f"msg {i}"} for i in range(20)]
    )
    store.count_summary_messages_at = AsyncMock(return_value=85)
    store.update_conversation_summary = AsyncMock(return_value=True)

    provider = SimpleNamespace(timeout_s=10, base_url=None, api_key=None, extra_headers=None)
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

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary == "Updated summary."
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_no_continuation_when_tail_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=20)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": "only msg"}]
    )
    store.count_summary_messages_at = AsyncMock(return_value=1)
    store.update_conversation_summary = AsyncMock(return_value=True)

    provider = SimpleNamespace(timeout_s=10, base_url=None, api_key=None, extra_headers=None)
    settings = SimpleNamespace(
        auto_fast_model="openrouter/test-model",
        get_provider_config=lambda _: provider,
    )
    monkeypatch.setattr(summary_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        summary_module.litellm,
        "acompletion",
        AsyncMock(return_value=SimpleNamespace(choices=[{"message": {"content": "Summary."}}])),
    )

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary == "Summary."
    assert result.continuation_needed is False
