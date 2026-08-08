from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.memory import summarization
from orchestrator.worker import jobs


@pytest.mark.asyncio
async def test_generate_summary_job_passes_persisted_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={
            "summary": "existing",
            "summary_updated_at": summary_time,
            "metadata": {"last_summarized_msg_count": 7},
        }
    )
    store.count_summary_messages = AsyncMock(return_value=27)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": "new"}] * 20
    )
    store.update_conversation_summary = AsyncMock(return_value=True)

    should_summarize = AsyncMock(return_value=True)
    generate_summary = AsyncMock(return_value="updated")
    monkeypatch.setattr(summarization, "should_summarize", should_summarize)
    monkeypatch.setattr(summarization, "generate_summary", generate_summary)

    result = await jobs.generate_summary_job({"store": store}, str(conversation_id))

    assert result == {
        "status": "success",
        "summary_length": 7,
        "summarized_message_count": 27,
        "continuation_enqueued": False,
    }
    should_summarize.assert_awaited_once_with(
        conversation_id,
        summary_time,
        7,
        store,
        {},
    )
    store.get_summary_message_batch.assert_awaited_once_with(
        conversation_id,
        offset=7,
        limit=100,
    )
    store.update_conversation_summary.assert_awaited_once_with(
        conversation_id,
        summary="updated",
        expected_summary_updated_at=summary_time,
        summarized_message_count=27,
    )


@pytest.mark.asyncio
async def test_generate_summary_job_enqueues_continuation_for_full_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": None, "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": "message"}] * 100
    )
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summarization, "generate_summary", AsyncMock(return_value="summary"))
    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = object()

    result = await jobs.generate_summary_job(
        {"store": store, "redis": queue},
        str(conversation_id),
        True,
    )

    assert result["continuation_enqueued"] is True
    enqueue.assert_awaited_once_with(
        queue,
        "generate_summary_job",
        job_id=f"summary:{conversation_id}:100",
        defer_by=jobs.timedelta(seconds=1),
        args=(str(conversation_id), True),
    )


def test_invalid_baseline_replays_from_zero() -> None:
    assert (
        summarization.validated_summary_baseline(
            {"summary": None, "metadata": {"last_summarized_msg_count": 25}},
            25,
        )
        == 0
    )
    assert (
        summarization.validated_summary_baseline(
            {"summary": "existing", "metadata": {"last_summarized_msg_count": 26}},
            25,
        )
        == 0
    )
