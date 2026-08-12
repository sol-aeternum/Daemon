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
    """Codex P2 #2 (PR #165): the worker must advance the persisted
    baseline by ONLY the rows actually incorporated, capped at the
    contiguous-prefix boundary (matches the inline path on
    ``summary.py:225``). The prior ``last_summarized_msg_count +
    len(messages)`` advanced by the inflated
    ``max(persisted, contiguous)`` which caused the persisted baseline
    to skip finalized rows that were contiguous-but-not-yet-summarized.
    """
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
    # The contiguous-finalized-prefix count at the snapshot matches the
    # persisted baseline (no rows have transitioned streaming->complete
    # since the prior commit). The SQL is responsible for stopping the
    # batch at this boundary; the worker treats ``contiguous_baseline``
    # as the cap for ``summarized_message_count`` defense-in-depth.
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=7)
    # If the SQL correctly enforces the contiguous prefix, the batch
    # fetched at offset=7 with cap=7 returns zero rows. With the prior
    # buggy SQL, 20 rows were returned and persisted at offset 27.
    store.get_summary_message_batch = AsyncMock(return_value=[])
    store.update_conversation_summary = AsyncMock(return_value=True)

    should_summarize = AsyncMock(return_value=True)
    generate_summary = AsyncMock(return_value="updated")
    monkeypatch.setattr(summarization, "should_summarize", should_summarize)
    monkeypatch.setattr(summarization, "generate_summary", generate_summary)

    result = await jobs.generate_summary_job({"store": store}, str(conversation_id))

    # The batch is empty because the SQL correctly stops at the
    # contiguous-prefix boundary; the worker returns ``up_to_date``.
    assert result == {
        "status": "skipped",
        "reason": "up_to_date",
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


@pytest.mark.asyncio
async def test_generate_summary_job_enqueues_continuation_for_full_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): the worker advances the persisted baseline
    by ONLY the rows actually incorporated in this iteration, capped at
    the contiguous-prefix boundary. A full 100-row batch signals that
    the tail is not yet drained and enqueues a forced-summary
    continuation with the advanced baseline as the job-id suffix.
    """
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": None, "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    # All 100 rows are in the contiguous-finalized prefix at the
    # iteration snapshot (no streaming gaps). The advanced baseline is
    # min(persisted + len, contiguous) = min(0 + 100, 100) = 100.
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=100)
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
    # The persisted baseline advance is min(persisted + len, contiguous).
    update_args = store.update_conversation_summary.await_args
    assert update_args is not None
    update_kwargs = update_args.kwargs
    assert update_kwargs["summarized_message_count"] == 100


@pytest.mark.asyncio
async def test_generate_summary_job_uses_contiguous_baseline_not_raw_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #1 (PR #165): worker path uses
    ``max(persisted_baseline, contiguous_baseline)`` as the offset, so a
    row that was streaming at the prior snapshot but is now finalized
    does not push the cursor forward into the contiguous-finalized
    prefix (otherwise already-counted rows are replayed).
    """
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={
            "summary": "existing",
            "summary_updated_at": summary_time,
            "metadata": {"last_summarized_msg_count": 50},
        }
    )
    store.count_summary_messages = AsyncMock(return_value=52)
    # A previously-streaming row at position 51 has completed, but a NEW
    # streaming row at position 52 means the contiguous-finalized prefix
    # is still 50 (matches the persisted baseline — ``max(50, 50) == 50``).
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=50)
    store.get_summary_message_batch = AsyncMock(return_value=[])
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summarization, "should_summarize", AsyncMock(return_value=True))
    monkeypatch.setattr(summarization, "generate_summary", AsyncMock(return_value="x"))

    await jobs.generate_summary_job({"store": store}, str(conversation_id))

    fetch_args = store.get_summary_message_batch.await_args
    assert fetch_args is not None
    assert fetch_args.kwargs["offset"] == 50


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


@pytest.mark.asyncio
async def test_extract_memories_recovers_pending_continuation_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 on PR #165, ``worker/jobs.py:295`` — when a previous
    attempt committed a summary with ``summary_continuation_pending=true``
    but failed to enqueue the forced-summary continuation (e.g. Redis
    transient error -> ``Retry``), the retry may find ``no_messages``
    after the watermark advances. The recovery path consumes the flag at
    the top of ``extract_memories`` and enqueues the continuation
    regardless of the messages check.
    """
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    # ``consume_summary_continuation_pending`` returns True (flag was set)
    store.consume_summary_continuation_pending = AsyncMock(return_value=True)
    # No messages found — the watermark advanced past everything
    store.get_last_extraction_cursor = AsyncMock(return_value=(datetime.now(timezone.utc), None))
    store.get_messages_after_cursor = AsyncMock(return_value=[])

    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            # Even with no messages, the recovery path enqueues the
            # continuation because the pending flag was set.
            await jobs.extract_memories(ctx, user_id, conversation_id, messages_json=None)
        # process_extraction must NOT have been called because the
        # messages path returned early
        proc.assert_not_called()

    # The pending flag was consumed
    store.consume_summary_continuation_pending.assert_awaited_once()
    # The forced-summary continuation was enqueued exactly once
    assert enqueue.await_count == 1
    enqueue_args = enqueue.await_args
    assert enqueue_args is not None
    assert enqueue_args.args[1] == "generate_summary_job"
    assert enqueue_args.kwargs["args"] == (str(conversation_id), True)


@pytest.mark.asyncio
async def test_extract_memories_does_not_recover_when_flag_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pending flag is NOT set, ``extract_memories`` follows
    the normal flow (no recovery enqueue).
    """
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(datetime.now(timezone.utc), None))
    store.get_messages_after_cursor = AsyncMock(return_value=[])

    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            await jobs.extract_memories(ctx, user_id, conversation_id, messages_json=None)

    # Flag was checked (and returned False)
    store.consume_summary_continuation_pending.assert_awaited_once()
    # No enqueue at all (messages empty, no flag set)
    assert enqueue.await_count == 0
    # The early no_messages path was hit (process_extraction not called)
    proc.assert_not_called()
