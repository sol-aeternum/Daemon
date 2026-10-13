"""Worker summary path under J1c original-generation fencing.

Legacy regression coverage (full-batch continuation, baseline semantics,
recovery pending flag) is preserved on the immutable-capture interface. The
guard seam is stubbed for compute-focused worker cases; real transaction/guard
behavior is proven in ``test_summary_account_guard_postgres.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from orchestrator.compute_runtime import current_scope
from orchestrator.memory import summarization
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.worker import jobs
from tests.qualified_compute import install_qualified_compute


def _account_context(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
    user_id: UUID,
    *,
    queue: object | None = None,
) -> dict[str, object]:
    install_qualified_compute(monkeypatch)
    store.get_conversation = AsyncMock(return_value={"user_id": user_id})
    ctx: dict[str, object] = {"store": store, "db_pool": object()}
    if queue is not None:
        ctx["redis"] = queue
    return ctx


def _summary_input(
    *,
    summary: str | None = "existing",
    summary_updated_at: datetime | None,
    persisted_baseline: int,
    current_count: int,
    finalized_count: int | None = None,
    contiguous_count: int | None = None,
    has_hole: bool = False,
    messages: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    finalized = finalized_count if finalized_count is not None else current_count
    contiguous = contiguous_count if contiguous_count is not None else current_count
    return {
        "summary": summary,
        "summary_updated_at": summary_updated_at,
        "persisted_baseline": persisted_baseline,
        "current_message_count": current_count,
        "finalized_count": finalized,
        "contiguous_count": contiguous,
        "has_hole": has_hole,
        "messages": messages if messages is not None else [],
        "snapshot_at": SimpleNamespace(),
    }


@pytest.mark.asyncio
async def test_generate_summary_job_passes_persisted_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): the worker advances the persisted baseline by
    ONLY the rows actually incorporated; with an empty batch it delegates to
    the guarded drained-state check (which refuses the tail here) and reports
    ``up_to_date``."""
    conversation_id = uuid4()
    owner = uuid4()
    summary_time = datetime.now(timezone.utc)
    store = object.__new__(MemoryStore)
    captured = CapturedGeneration(owner, 7)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_summary_input(
            summary="existing",
            summary_updated_at=summary_time,
            persisted_baseline=7,
            current_count=27,
            finalized_count=27,
            contiguous_count=7,
            messages=[],
        )
    )
    store.clear_finished_summary_continuation = AsyncMock(return_value=False)

    should_summarize = AsyncMock(return_value=True)
    generate_summary = AsyncMock(return_value="updated")
    monkeypatch.setattr(summarization, "should_summarize", should_summarize)
    monkeypatch.setattr(summarization, "generate_summary", generate_summary)

    result = await jobs.generate_summary_job(
        {"store": store},
        str(conversation_id),
        origin_account_id=str(owner),
        origin_account_generation=7,
    )

    # The batch is empty because the SQL correctly stops at the
    # contiguous-prefix boundary; the worker returns ``up_to_date``.
    assert result == {"status": "skipped", "reason": "up_to_date"}
    should_summarize.assert_awaited_once_with(
        conversation_id,
        summary_time,
        7,
        store,
        {},
        current_message_count=27,
    )
    read = store.read_conversation_summary_inputs.await_args
    assert read is not None
    assert read.args[0] == conversation_id
    assert read.kwargs["captured_generation"] == captured
    assert read.kwargs["limit"] == 100
    # Clearing is delegated to SQL, which must refuse this undrained tail.
    store.clear_finished_summary_continuation.assert_awaited_once_with(
        conversation_id,
        captured_generation=captured,
        expected_summary_updated_at=summary_time,
        expected_baseline=7,
    )


@pytest.mark.asyncio
async def test_generate_summary_job_enqueues_continuation_for_full_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): the worker advances the persisted baseline
    by ONLY the rows actually incorporated in this iteration, capped at
    the contiguous-prefix boundary. A full 100-row batch signals that
    the tail is not yet drained and enqueues a forced-summary
    continuation carrying the immutable original capture."""
    conversation_id = uuid4()
    owner = uuid4()
    store = object.__new__(MemoryStore)
    captured = CapturedGeneration(owner, 7)
    messages = [{"role": "user", "content": f"message {i}"} for i in range(100)]
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_summary_input(
            summary=None,
            summary_updated_at=None,
            persisted_baseline=0,
            current_count=100,
            finalized_count=100,
            contiguous_count=100,
            messages=messages,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    async def summarize_in_account_scope(*_args: object, **_kwargs: object) -> str:
        assert current_scope().user_id == user_id_alias
        return "summary"

    # The account scope is opened for the captured origin, not the wire string.
    user_id_alias = owner
    monkeypatch.setattr(
        summarization, "generate_summary", AsyncMock(side_effect=summarize_in_account_scope)
    )
    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = object()

    install_qualified_compute(monkeypatch)

    result = await jobs.generate_summary_job(
        {"store": store, "db_pool": object(), "redis": queue},
        str(conversation_id),
        True,
        origin_account_id=str(owner),
        origin_account_generation=7,
    )

    assert result["continuation_enqueued"] is True
    enqueue.assert_awaited_once_with(
        queue,
        "generate_summary_job",
        job_id=f"summary:{conversation_id}:100",
        user_id=owner,
        defer_by=jobs.timedelta(seconds=1),
        args=(str(conversation_id), True),
        kwargs={
            "origin_account_id": str(owner),
            "origin_account_generation": 7,
        },
    )
    # The persisted baseline advance is min(persisted + len, contiguous) = 100.
    update_args = store.update_conversation_summary.await_args
    assert update_args is not None
    update_kwargs = update_args.kwargs
    assert update_kwargs["summarized_message_count"] == 100
    # A full batch marks the persistent recovery flag: the tail is not
    # drained until a follow-up finds fewer rows (pending rides the same
    # guarded persist as the summary/cursor).
    assert update_kwargs["summary_continuation_pending"] is True
    assert update_kwargs["captured_generation"] == captured
    # The guard recheck ran inside the account scope before inference.
    store.check_conversation_summary_generation.assert_awaited_once_with(
        conversation_id,
        captured_generation=captured,
        expected_summary_updated_at=None,
    )


@pytest.mark.asyncio
async def test_generate_summary_job_uses_contiguous_baseline_not_raw_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claimed baseline is min(persisted + len(messages), contiguous).
    With a fresh streaming row at 41, the batch stops at 40, so the claim
    stays 40 even though 52 finalized rows now exist."""
    conversation_id = uuid4()
    owner = uuid4()
    summary_time = datetime.now(timezone.utc)
    store = object.__new__(MemoryStore)
    messages = [{"role": "user", "content": "message"} for _ in range(40)]
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_summary_input(
            summary="existing",
            summary_updated_at=summary_time,
            persisted_baseline=40,
            current_count=52,
            finalized_count=41,
            contiguous_count=40,
            messages=messages,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summarization, "should_summarize", AsyncMock(return_value=True))
    monkeypatch.setattr(summarization, "generate_summary", AsyncMock(return_value="x"))
    install_qualified_compute(monkeypatch)

    result = await jobs.generate_summary_job(
        {"store": store, "db_pool": object()},
        str(conversation_id),
        True,
        origin_account_id=str(owner),
        origin_account_generation=7,
    )
    assert result["status"] == "success"

    fetch_args = store.update_conversation_summary.await_args
    assert fetch_args is not None
    assert fetch_args.kwargs["summarized_message_count"] == 40


@pytest.mark.asyncio
async def test_generate_summary_job_recheck_stale_version_raises_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version mismatch after admission means the read inputs went stale:
    the worker raises arq Retry so the wire capture is replayed under a
    fresh guarded read rather than summarizing unseen inputs."""
    from arq import Retry

    conversation_id = uuid4()
    owner = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_summary_input(
            summary=None,
            summary_updated_at=None,
            persisted_baseline=0,
            current_count=3,
            finalized_count=3,
            contiguous_count=3,
            messages=[{"role": "user", "content": "msg"}],
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=False)
    generate_summary = AsyncMock()
    monkeypatch.setattr(summarization, "generate_summary", generate_summary)
    install_qualified_compute(monkeypatch)

    with pytest.raises(Retry):
        await jobs.generate_summary_job(
            {"store": store, "db_pool": object()},
            str(conversation_id),
            True,
            origin_account_id=str(owner),
            origin_account_generation=7,
        )
    generate_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_summary_job_refusal_marks_capture_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard refusal at input-read time surfaces as a terminal skip; the
    affected worker tolerates it without compute admission or model calls."""
    conversation_id = uuid4()
    owner = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        side_effect=WriteGuardRefusal("stale_generation")
    )
    generate_summary = AsyncMock()
    monkeypatch.setattr(summarization, "generate_summary", generate_summary)
    compute = AsyncMock()
    monkeypatch.setattr(jobs, "account_compute", compute)

    result = await jobs.generate_summary_job(
        {"store": store, "db_pool": object()},
        str(conversation_id),
        True,
        origin_account_id=str(owner),
        origin_account_generation=7,
    )

    assert result == {"status": "skipped", "reason": "stale_generation"}
    generate_summary.assert_not_awaited()
    compute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"origin_account_id": "bad"},
        {"origin_account_generation": 7},
        *[
            {"origin_account_id": str(uuid4()), "origin_account_generation": value}
            for value in (None, True, -1, "7", 1.0, 2**63)
        ],
    ],
)
async def test_legacy_or_malformed_capture_is_terminal_without_reads(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    """Missing/malformed capture payloads refuse without reads, decrypts,
    compute admission or model calls (mirrors title fencing)."""
    from unittest.mock import MagicMock

    store = MagicMock(spec=MemoryStore)
    generator = AsyncMock()
    compute = MagicMock(side_effect=AssertionError("legacy job must not admit compute"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    monkeypatch.setattr(summarization, "generate_summary", generator)

    result = await jobs.generate_summary_job(
        {"store": store, "db_pool": object()},
        str(uuid4()),
        True,
        **payload,
    )
    assert result == {"status": "skipped", "reason": "invalid_capture"}
    assert store.mock_calls == []
    compute.assert_not_called()
    generator.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_summary_job_hole_preserves_pending_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short batch bounded by a streaming hole persists ``pending=True``
    (the hole recovery evidence rides the same persist), and the persist
    claims only rows covered by the batch capped at the contiguous prefix."""
    conversation_id = uuid4()
    owner = uuid4()
    summary_time = datetime.now(timezone.utc)
    store = object.__new__(MemoryStore)
    messages = [{"role": "user", "content": "batch"} for _ in range(45)]
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_summary_input(
            summary=None,
            summary_updated_at=summary_time,
            persisted_baseline=5,
            current_count=70,
            finalized_count=70,
            contiguous_count=50,
            messages=messages,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)
    monkeypatch.setattr(summarization, "generate_summary", AsyncMock(return_value="summary"))
    monkeypatch.setattr(summarization, "should_summarize", AsyncMock(return_value=True))
    install_qualified_compute(monkeypatch)

    result = await jobs.generate_summary_job(
        {"store": store, "db_pool": object()},
        str(conversation_id),
        True,
        origin_account_id=str(owner),
        origin_account_generation=7,
    )

    assert store.update_conversation_summary.await_args is not None
    update_kwargs = store.update_conversation_summary.await_args.kwargs
    # Claim capped at the contiguous prefix: min(5 + 45, 50) = 50.
    assert update_kwargs["summarized_message_count"] == 50
    # Hole keeps the persistent recovery flag with the summary persist.
    assert update_kwargs["summary_continuation_pending"] is True
    assert result["status"] == "success"
    assert result["summarized_message_count"] == 50
    assert result["continuation_enqueued"] is False


@pytest.mark.asyncio
async def test_extract_memories_enqueues_summary_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inline summary path signals continuation, enqueue the worker
    job with the immutable original capture."""
    from types import SimpleNamespace

    from orchestrator.worker import jobs
    from tests.qualified_compute import install_qualified_compute as _iqc

    _iqc(monkeypatch)

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_last_extraction_time = AsyncMock(return_value=None)
    store.get_messages = AsyncMock(
        return_value=[{"role": "user", "content": "msg", "created_at": None}]
    )
    store.read_summary_continuation_pending = AsyncMock(return_value=False)
    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    messages_json = json.dumps([{"role": "user", "content": "msg"}])

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], True)  # success, no new memories, continuation_needed
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json,
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )

    # Only the summary continuation is expected (the resolve_entities enqueue is
    # gated on new_memories, which is empty here).
    assert enqueue.await_count == 1
    enqueue_args = enqueue.await_args
    assert enqueue_args is not None
    assert enqueue_args.args[0] is queue
    assert enqueue_args.args[1] == "generate_summary_job"
    assert enqueue_args.kwargs["args"] == (str(conversation_id), True)
    # Preserve the original route: continuity with the captured origin.
    assert enqueue_args.kwargs["kwargs"] == {
        "origin_account_id": str(user_id),
        "origin_account_generation": 7,
    }


@pytest.mark.asyncio
async def test_extract_memories_no_summarization_without_capture_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy queued extraction payloads carry no summary origin; fact
    extraction proceeds but the summary family is disabled."""
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_last_extraction_time = AsyncMock(return_value=None)
    store.get_messages = AsyncMock(return_value=[{"role": "user", "content": "msg"}])
    store.read_summary_continuation_pending = AsyncMock(
        side_effect=AssertionError("disabled summary family must not observe pending")
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    messages_json = json.dumps([{"role": "user", "content": "msg"}])

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], True)
        await jobs.extract_memories(ctx, user_id, conversation_id, messages_json)

    # No summary enqueue despite inline continuation: summary work disabled.
    assert enqueue.await_count == 0
    proc.assert_awaited_once()
    assert proc.await_args is not None
    assert proc.await_args.kwargs["summary_generation"] is None


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
    store.read_summary_continuation_pending = AsyncMock(return_value=False)
    enqueue = AsyncMock()
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    messages_json = json.dumps([{"role": "user", "content": "msg"}])

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        proc.return_value = (True, [], False)  # no continuation
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json,
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )

    assert enqueue.await_count == 0


@pytest.mark.asyncio
async def test_extract_memories_recovers_pending_continuation_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a previous attempt committed a summary with
    ``summary_continuation_pending=true`` but failed to enqueue the
    forced-summary continuation (e.g. Redis transient error -> ``Retry``),
    the retry may find ``no_messages`` after the watermark advances. The
    recovery path observes the guarded flag at the top of
    ``extract_memories`` and enqueues the continuation regardless of the
    messages check. The observation is non-destructive: the flag stays
    set until guarded summary progress clears it."""
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    # ``read_summary_continuation_pending`` returns True (flag was set)
    store.read_summary_continuation_pending = AsyncMock(return_value=True)
    # No messages found — the watermark advanced past everything
    store.get_last_extraction_cursor = AsyncMock(return_value=(datetime.now(timezone.utc), None))
    store.get_messages_after_cursor = AsyncMock(return_value=[])

    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        # Even with no messages, the recovery path enqueues the
        # continuation because the pending flag was set.
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json=None,
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )
        # process_extraction must NOT have been called because the
        # messages path returned early
        proc.assert_not_called()

    # The pending flag was observed (never destructively consumed)
    store.read_summary_continuation_pending.assert_awaited_once()
    read_args = store.read_summary_continuation_pending.await_args
    assert read_args is not None
    assert read_args.kwargs["captured_generation"] == CapturedGeneration(user_id, 7)
    # The forced-summary continuation was enqueued exactly once
    assert enqueue.await_count == 1
    enqueue_args = enqueue.await_args
    assert enqueue_args is not None
    assert enqueue_args.args[1] == "generate_summary_job"
    assert enqueue_args.kwargs["args"] == (str(conversation_id), True)
    assert enqueue_args.kwargs["kwargs"] == {
        "origin_account_id": str(user_id),
        "origin_account_generation": 7,
    }


@pytest.mark.asyncio
async def test_extract_memories_does_not_recover_when_flag_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pending flag is NOT set, ``extract_memories`` follows
    the normal flow (no recovery enqueue)."""
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(datetime.now(timezone.utc), None))
    store.get_messages_after_cursor = AsyncMock(return_value=[])

    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json=None,
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )

    # Flag was observed (and returned False)
    store.read_summary_continuation_pending.assert_awaited_once()
    # No enqueue at all (messages empty, no flag set)
    assert enqueue.await_count == 0
    # The early no_messages path was hit (process_extraction not called)
    proc.assert_not_called()


@pytest.mark.asyncio
async def test_extract_memories_foreign_capture_disables_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign-origin capture is refused before summary state is observed:
    no summary enqueue, no continuation payload with foreign origin."""
    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_last_extraction_cursor = AsyncMock(return_value=(datetime.now(timezone.utc), None))
    store.get_messages_after_cursor = AsyncMock(return_value=[])
    store.read_summary_continuation_pending = AsyncMock(
        side_effect=AssertionError("foreign capture must not observe summary state")
    )
    enqueue = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as proc:
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json=None,
            origin_account_id=str(uuid4()),
            origin_account_generation=7,
        )

    proc.assert_not_called()
    assert enqueue.await_count == 0


@pytest.mark.asyncio
async def test_extract_memories_read_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient guarded-read failure raises arq Retry so the whole
    extraction attempt is retried rather than silently skipping summary
    recovery observation."""
    from arq import Retry

    from orchestrator.worker import jobs

    conversation_id = uuid4()
    user_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_summary_continuation_pending = AsyncMock(side_effect=RuntimeError("pool closed"))
    store.get_last_extraction_cursor = AsyncMock()
    store.get_messages_after_cursor = AsyncMock()

    enqueue = AsyncMock()
    monkeypatch.setattr(jobs, "enqueue_with_debounce", enqueue)
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = _account_context(monkeypatch, store, user_id, queue=queue)

    with (
        pytest.raises(Retry),
        patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock),
    ):
        await jobs.extract_memories(
            ctx,
            user_id,
            conversation_id,
            messages_json=None,
            origin_account_id=str(user_id),
            origin_account_generation=7,
        )

    store.read_summary_continuation_pending.assert_awaited_once()
    store.get_last_extraction_cursor.assert_not_awaited()
    assert enqueue.await_count == 0
