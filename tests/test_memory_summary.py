from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory import summary as summary_module
from orchestrator.memory.store import MemoryStore


def _build_provider() -> SimpleNamespace:
    return SimpleNamespace(
        timeout_s=10,
        base_url=None,
        api_key=None,
        extra_headers=None,
    )


def _build_settings(provider: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        auto_fast_model="openrouter/test-model",
        get_provider_config=lambda _: provider,
    )


def _patch_litellm(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> AsyncMock:
    acompletion = AsyncMock(
        return_value=SimpleNamespace(choices=[{"message": {"content": content}}])
    )
    monkeypatch.setattr(summary_module.litellm, "acompletion", acompletion)
    return acompletion


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
    # The contiguous-finalized-prefix count at the snapshot matches the
    # persisted baseline (no rows have transitioned streaming->complete
    # since the prior commit).
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=3)
    store.get_summary_message_batch = AsyncMock(
        return_value=[
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ]
    )
    store.update_conversation_summary = AsyncMock(return_value=True)

    provider = _build_provider()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(provider))
    _patch_litellm(monkeypatch, "Updated summary.")

    result = await summary_module.generate_or_update_summary(conversation_id, store)

    assert result == "Updated summary."
    assert store.get_summary_message_batch.await_count == 1
    batch_args = store.get_summary_message_batch.await_args
    assert batch_args is not None
    batch_kwargs = batch_args.kwargs
    assert batch_kwargs["offset"] == 3
    assert batch_kwargs["limit"] == 20
    assert "snapshot_at" in batch_kwargs
    update_args = store.update_conversation_summary.await_args
    assert update_args is not None
    update_kwargs = update_args.kwargs
    assert update_kwargs["summary"] == "Updated summary."
    assert update_kwargs["expected_summary_updated_at"] is None
    # The persisted claim is capped at the contiguous-prefix boundary
    # (``min(persisted_baseline + len(messages), contiguous_baseline)``
    # = ``min(5, 3) = 3``) so the two fetched rows — which are beyond
    # the contiguous finalized prefix at the snapshot — do not push
    # the cursor past the prefix (Codex P2 on PR #165,
    # ``summary.py:147``).
    assert update_kwargs["summarized_message_count"] == 3
    assert "summary_snapshot_at" in update_kwargs


@pytest.mark.asyncio
async def test_inline_summary_signals_continuation_when_batch_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inline batch fills, the result must signal continuation."""
    from uuid import uuid4

    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=0)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": f"msg {i}"} for i in range(20)]
    )
    store.count_summary_messages_at = AsyncMock(return_value=85)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_litellm(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary == "Updated summary."
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_no_continuation_when_tail_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import uuid4

    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=20)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=0)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": "only msg"}]
    )
    store.count_summary_messages_at = AsyncMock(return_value=1)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_litellm(monkeypatch, "Summary.")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary == "Summary."
    assert result.continuation_needed is False


# ---------------------------------------------------------------------------
# Codex P2 regression coverage on PR #165
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_summary_uses_contiguous_baseline_when_streaming_row_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #1 (PR #165): a row that was ``streaming`` at the prior
    snapshot but is now finalized must NOT push the offset forward into
    the contiguous-finalized prefix (otherwise messages already counted
    toward the baseline are replayed).
    """
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={
            # Non-empty summary so ``validated_summary_baseline`` accepts
            # ``last_summarized_msg_count=20``; with an empty summary
            # the validator conservatively replays from 0.
            "summary": "prior",
            "summary_updated_at": None,
            # Persisted baseline is 20 — the previous commit saw 20
            # finalized rows. The 21st row was streaming at the snapshot.
            "metadata": {"last_summarized_msg_count": 20},
        }
    )
    store.count_summary_messages = AsyncMock(return_value=22)
    # Contiguous-finalized-prefix count at the new snapshot is still 20
    # because the previously-streaming row remains excluded from the
    # contiguous prefix (a *new* streaming row appeared at position 21).
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=20)
    store.get_summary_message_batch = AsyncMock(return_value=[])
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_litellm(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    # No messages were fetched because the offset is still 20, so the
    # result is a no-op and continuation is not needed. The existing
    # summary text is preserved (the worker returns it unchanged when
    # the batch is empty).
    assert result.summary == "prior"
    assert result.continuation_needed is False
    fetch_args = store.get_summary_message_batch.await_args
    assert fetch_args is not None
    assert fetch_args.kwargs["offset"] == 20


@pytest.mark.asyncio
async def test_inline_summary_signals_continuation_on_optimistic_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): when ``update_conversation_summary`` returns
    False (optimistic-concurrency conflict) and the batch was full, the
    result must surface continuation so the extraction caller enqueues a
    forced job to drain any messages the winner's snapshot may have
    missed.
    """
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=0)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": f"msg {i}"} for i in range(20)]
    )
    store.count_summary_messages_at = AsyncMock(return_value=85)
    store.update_conversation_summary = AsyncMock(return_value=False)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_litellm(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary is None
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_signals_continuation_on_transient_storage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #3 (PR #165): when the summary was persisted but a
    transient follow-up error (``count_summary_messages_at`` raising in
    a future iteration, or any exception during the litellm call AFTER
    the pre-persist continuation decision was made) surfaces, the result
    must still surface continuation so the remaining tail isn't stranded.
    """
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"summary": "", "summary_updated_at": None, "metadata": {}}
    )
    store.count_summary_messages = AsyncMock(return_value=100)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=0)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": f"msg {i}"} for i in range(20)]
    )
    # Continuation count succeeds — we already know continuation is
    # needed before persistence. Then the persist itself raises.
    store.count_summary_messages_at = AsyncMock(return_value=85)
    store.update_conversation_summary = AsyncMock(side_effect=RuntimeError("transient"))

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_litellm(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result.summary is None
    assert result.continuation_needed is True
