import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
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
    assert store.get_summary_message_batch.await_count == 1
    batch_args = store.get_summary_message_batch.await_args
    assert batch_args is not None
    batch_kwargs = batch_args.kwargs
    assert batch_kwargs["offset"] == 7
    assert batch_kwargs["limit"] == 100
    assert "snapshot_at" in batch_kwargs
    update_args = store.update_conversation_summary.await_args
    assert update_args is not None
    update_kwargs = update_args.kwargs
    assert update_kwargs["summary"] == "updated"
    assert update_kwargs["expected_summary_updated_at"] == summary_time
    assert update_kwargs["summarized_message_count"] == 27
    assert "summary_snapshot_at" in update_kwargs


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


@pytest.mark.asyncio
async def test_extract_memories_enqueues_summary_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inline summary path signals continuation, enqueue the worker job."""
    from types import SimpleNamespace

    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_last_extraction_time = AsyncMock(return_value=None)
    store.get_messages = AsyncMock(
        return_value=[{"role": "user", "content": "msg", "created_at": None}]
    )
    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    messages_json = json.dumps([{"role": "user", "content": "msg"}])

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], True)  # success, no new memories, continuation_needed
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            await jobs.extract_memories(ctx, user_id, conversation_id, messages_json)

    # Only the summary continuation is expected (the resolve_entities enqueue is
    # gated on new_memories, which is empty here).
    assert enqueue.await_count == 1
    enqueue_args = enqueue.await_args
    assert enqueue_args is not None
    assert enqueue_args.args[0] is queue
    assert enqueue_args.args[1] == "generate_summary_job"
    assert enqueue_args.kwargs["args"] == (str(conversation_id), True)


@pytest.mark.asyncio
async def test_extract_memories_skips_continuation_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_last_extraction_time = AsyncMock(return_value=None)
    store.get_messages = AsyncMock(
        return_value=[{"role": "user", "content": "msg", "created_at": None}]
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    messages_json = json.dumps([{"role": "user", "content": "msg"}])

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], False)  # no continuation
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            await jobs.extract_memories(ctx, user_id, conversation_id, messages_json)

    assert enqueue.await_count == 0
