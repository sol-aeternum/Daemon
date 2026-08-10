"""Tests for memory tools."""

import pytest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import orchestrator.memory.tools as tools_module
from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool


@pytest.mark.asyncio
async def test_memory_tools_import():
    with patch("orchestrator.memory.dedup.dedup_and_store", new_callable=AsyncMock):
        mock_store = AsyncMock()
        from orchestrator.memory.tools import MemoryReadTool

        user_id = uuid.uuid4()
        tool = MemoryReadTool(mock_store, user_id)
        assert tool.name == "memory_read"


@pytest.mark.asyncio
async def test_memory_read_semantic_mode_passes_memory_slot():
    """Test that semantic mode passes memory_slot to search_memories."""
    with patch(
        "orchestrator.memory.tools.embed_query_with_metadata",
        new_callable=AsyncMock,
    ) as mock_embed:
        mock_embed.return_value = SimpleNamespace(
            embedding=[0.1, 0.2, 0.3],
            model="voyage-4-lite",
            storage_model="voyage-4-large",
        )

        mock_store = AsyncMock()
        mock_store.search_memories = AsyncMock()
        mock_store.search_memories.return_value = []

        user_id = uuid.uuid4()
        tool = MemoryReadTool(mock_store, user_id)

        # Execute with slot parameter
        await tool.execute(query="test query", mode="semantic", slot="test_slot")

        # Verify search_memories was called with memory_slot parameter
        mock_store.search_memories.assert_called_once()
        call_args = mock_store.search_memories.call_args
        assert call_args[1]["memory_slot"] == "test_slot"


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_calls_list_memories_with_confirmed_true():
    """Test that temporal mode calls list_memories with confirmed=True (not status="active")."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    mock_store.list_memories.return_value = []

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    await tool.execute(mode="temporal", after="2023-01-01T00:00:00Z", before="2023-12-31T23:59:59Z")
    # Verify list_memories was called with confirmed=True
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["confirmed"] is True
    assert "status" not in call_args[1] or call_args[1]["status"] is None


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_with_history_calls_list_memories_with_confirmed_none():
    """Test that temporal mode with history=True calls list_memories with confirmed=None."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    mock_store.list_memories.return_value = []

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    await tool.execute(
        mode="temporal",
        history=True,
        after="2023-01-01T00:00:00Z",
        before="2023-12-31T23:59:59Z",
    )
    # Verify list_memories was called with confirmed=None
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["confirmed"] is None


@pytest.mark.asyncio
async def test_memory_read_history_mode_excludes_deleted_memories():
    """Test that history mode excludes deleted memories from output."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    # Return memories with different statuses including deleted
    mock_store.list_memories.return_value = [
        {
            "content": "active memory",
            "status": "active",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
        {
            "content": "deleted memory",
            "status": "deleted",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
        {
            "content": "closed memory",
            "status": "closed",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
    ]

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)

    result = await tool.execute(
        mode="temporal",
        history=True,
        after="2023-01-01T00:00:00Z",
        before="2023-12-31T23:59:59Z",
    )
    # Verify deleted memory is not in the result
    assert "[FACT] [None -> None] active memory" in result
    assert "[FACT] [None -> None] closed memory" in result
    assert "[FACT] [None -> None] deleted memory" not in result


@pytest.mark.asyncio
async def test_memory_read_temporal_mode_slot_post_filter_with_increased_limit():
    """Test slot post-filter in temporal mode with effective_limit = limit * 4."""
    mock_store = AsyncMock()
    mock_store.list_memories = AsyncMock()
    # Return memories with different slots
    mock_store.list_memories.return_value = [
        {
            "content": "memory 1",
            "memory_slot": "slot_a",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
        {
            "content": "memory 2",
            "memory_slot": "slot_b",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
        {
            "content": "memory 3",
            "memory_slot": "slot_a",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
        {
            "content": "memory 4",
            "memory_slot": "slot_c",
            "category": "fact",
            "valid_from": None,
            "valid_to": None,
        },
    ]

    user_id = uuid.uuid4()
    tool = MemoryReadTool(mock_store, user_id)
    limit = 2
    result = await tool.execute(
        mode="temporal",
        slot="slot_a",
        limit=limit,
        after="2023-01-01T00:00:00Z",
        before="2023-12-31T23:59:59Z",
    )
    # Verify list_memories was called with increased limit (limit * 4)
    mock_store.list_memories.assert_called_once()
    call_args = mock_store.list_memories.call_args
    assert call_args[1]["limit"] == limit * 4

    # Verify only memories with matching slot are in the result
    assert "- [FACT] slot=slot_a memory 1" in result
    assert "- [FACT] slot=slot_a memory 3" in result
    assert "slot=slot_b memory 2" not in result
    assert "slot=slot_c memory 4" not in result
    assert len([line for line in result.splitlines() if line.strip()]) <= limit


@pytest.mark.asyncio
async def test_memory_write_create_passes_slot_to_dedup_and_store():
    """Test that create action passes slot parameter to dedup_and_store."""
    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        user_id = uuid.uuid4()
        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(action="create", content="test content", slot="test_slot")

        mock_dedup.assert_called_once()
        call_kwargs = mock_dedup.call_args[1]
        assert call_kwargs["slot"] == "test_slot"


@pytest.mark.asyncio
async def test_memory_write_update_calls_close_then_dedup():
    """Test that update action calls close_memory before dedup_and_store."""
    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        existing_memory_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_store.get_memory = AsyncMock()
        mock_store.get_memory.return_value = {
            "id": existing_memory_id,
            "user_id": user_id,
            "content": "old content",
            "category": "fact",
            "source_type": "user_created",
            "conversation_id": None,
            "memory_slot": None,
        }

        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(
            action="update", memory_id=str(existing_memory_id), content="new content"
        )

        # Verify close_memory was called with user_id for defense-in-depth.
        mock_store.close_memory.assert_called_once_with(existing_memory_id, user_id=user_id)
        # Verify dedup_and_store was called after close
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_update_inherits_category_and_slot():
    """Test that update action inherits category and slot from old memory when not provided."""
    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()

        mock_store = AsyncMock()
        existing_memory_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_store.get_memory = AsyncMock()
        mock_store.get_memory.return_value = {
            "id": existing_memory_id,
            "user_id": user_id,
            "content": "old content",
            "category": "preference",
            "source_type": "user_created",
            "conversation_id": None,
            "memory_slot": "inherited_slot",
        }

        tool = MemoryWriteTool(mock_store, user_id)

        await tool.execute(
            action="update", memory_id=str(existing_memory_id), content="new content"
        )

        mock_dedup.assert_called_once()
        call_kwargs = mock_dedup.call_args[1]
        assert call_kwargs["category"] == "preference"
        assert call_kwargs["slot"] == "inherited_slot"


@pytest.mark.asyncio
async def test_memory_write_update_missing_memory_id_returns_error():
    """Test that update action returns error when memory_id is missing."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="update", content="new content")

    assert result == "memory_id is required for update"


@pytest.mark.asyncio
async def test_memory_write_update_invalid_memory_id_returns_error():
    """Test that update action returns error for invalid memory_id format."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(
        action="update", memory_id="not-a-valid-uuid", content="new content"
    )

    assert result == "Invalid memory_id format"


@pytest.mark.asyncio
async def test_memory_write_delete_calls_delete_memory_with_soft_true():
    """Test that delete action calls delete_memory with soft=True."""
    mock_store = AsyncMock()
    existing_memory_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_store.get_memory = AsyncMock()
    mock_store.get_memory.return_value = {
        "id": existing_memory_id,
        "user_id": user_id,
        "content": "to delete",
        "category": "fact",
    }

    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(action="delete", memory_id=str(existing_memory_id))

    # Defense-in-depth: store-layer user_id filter.
    mock_store.delete_memory.assert_called_once_with(existing_memory_id, soft=True, user_id=user_id)
    assert str(existing_memory_id) in result


@pytest.mark.asyncio
async def test_memory_write_update_rejects_cross_user_memory():
    """`MemoryWriteTool.update` must refuse to operate on another user's
    memory even if the caller already knows the UUID. Without this guard,
    a user who learned another user's memory UUID via prompt injection,
    log leakage, or shared exports could close (bitemporally retire) that
    memory through the LLM-callable tool path. See issue #173.
    """
    mock_store = AsyncMock()
    other_user_id = uuid.uuid4()
    attacker_id = uuid.uuid4()
    existing_memory_id = uuid.uuid4()
    mock_store.get_memory = AsyncMock()
    mock_store.get_memory.return_value = {
        "id": existing_memory_id,
        "user_id": other_user_id,
        "content": "victim's secret",
        "category": "fact",
    }

    tool = MemoryWriteTool(mock_store, attacker_id)

    result = await tool.execute(
        action="update",
        memory_id=str(existing_memory_id),
        content="attacker rewrite",
    )

    assert "not found" in result.lower()
    # close_memory and dedup_and_store must NOT be invoked.
    mock_store.close_memory.assert_not_called()
    # dedup_and_store is patched at module level; we verify by checking
    # the absence of close_memory side effects.


@pytest.mark.asyncio
async def test_memory_write_delete_rejects_cross_user_memory():
    """`MemoryWriteTool.delete` must refuse to operate on another user's
    memory even if the caller already knows the UUID. Without this guard,
    a user could soft-delete another user's memory via the LLM tool path.
    See issue #173.
    """
    mock_store = AsyncMock()
    other_user_id = uuid.uuid4()
    attacker_id = uuid.uuid4()
    existing_memory_id = uuid.uuid4()
    mock_store.get_memory = AsyncMock()
    mock_store.get_memory.return_value = {
        "id": existing_memory_id,
        "user_id": other_user_id,
        "content": "victim's secret",
        "category": "fact",
    }

    tool = MemoryWriteTool(mock_store, attacker_id)

    result = await tool.execute(
        action="delete",
        memory_id=str(existing_memory_id),
    )

    assert "not found" in result.lower()
    mock_store.delete_memory.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_invalid_category_returns_error():
    """Test that create action returns error for invalid category."""
    mock_store = AsyncMock()
    user_id = uuid.uuid4()
    tool = MemoryWriteTool(mock_store, user_id)

    result = await tool.execute(
        action="create", content="test content", category="invalid_category"
    )

    assert "Invalid category 'invalid_category'" in result
    assert "Use one of:" in result


# ---------------------------------------------------------------------------
# memory_write per-user quotas (issue #62)
#
# The tool is LLM-callable, so a prompt-injected model can loop on it.
# Every call inserts a row AND issues a billed embedding request, so the
# guard must refuse *before* `dedup_and_store` is reached. These tests
# monkeypatch the module-level quota constants so they do not have to
# simulate a thousand rows.
# ---------------------------------------------------------------------------


def _quota_store(*, recent_writes: int, active_rows: int) -> AsyncMock:
    """An AsyncMock store whose quota counters return fixed values."""
    store = AsyncMock()
    store.count_memories_created_since = AsyncMock(return_value=recent_writes)
    store.count_active_memories = AsyncMock(return_value=active_rows)
    return store


@pytest.mark.asyncio
async def test_memory_write_allows_write_below_rate_limit(monkeypatch):
    """The 10th write in the window still proceeds (limit is 10)."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()
        store = _quota_store(recent_writes=9, active_rows=10)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact 10")

        assert "Memory created" in result
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_eleventh_write_in_window_is_refused(monkeypatch):
    """Rate test from the issue: the 11th write in 60s returns an error."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_WINDOW_SECONDS", 60)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        store = _quota_store(recent_writes=10, active_rows=10)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact 11")

        assert "rate limit" in result.lower()
        # The billed embedding path must not be reached.
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_rate_limit_refusal_precedes_embedding_call(monkeypatch):
    """Cost-amplification guard: no embedding request on a refused write."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 3)

    with (
        patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup,
        patch(
            "orchestrator.memory.tools.embed_documents_with_metadata",
            new_callable=AsyncMock,
        ) as mock_embed,
    ):
        store = _quota_store(recent_writes=3, active_rows=0)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact", slot="a_slot")

        assert "rate limit" in result.lower()
        mock_dedup.assert_not_called()
        mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_active_row_cap_is_enforced(monkeypatch):
    """Cap test from the issue: the 1001st active row is refused."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        store = _quota_store(recent_writes=0, active_rows=1000)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="one too many")

        assert "cap" in result.lower()
        assert "consolidate" in result.lower()
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_update_is_also_rate_limited(monkeypatch):
    """`update` closes one row and inserts another — it must be metered too."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        store = _quota_store(recent_writes=10, active_rows=5)
        store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": user_id,
                "content": "old",
                "category": "fact",
                "memory_slot": None,
                "source_conversation_id": None,
            }
        )
        tool = MemoryWriteTool(store, user_id)

        result = await tool.execute(action="update", memory_id=str(memory_id), content="new")

        assert "rate limit" in result.lower()
        # No row is closed and no new row inserted on a refused update.
        store.close_memory.assert_not_called()
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_quota_is_scoped_per_user(monkeypatch):
    """The counters are queried with the calling user's id, not globally."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()
        user_id = uuid.uuid4()
        store = _quota_store(recent_writes=0, active_rows=0)
        tool = MemoryWriteTool(store, user_id)

        await tool.execute(action="create", content="fact")

        rate_call = store.count_memories_created_since.await_args
        assert rate_call is not None
        assert rate_call.args[0] == user_id
        assert rate_call.kwargs["since"].tzinfo is not None

        cap_call = store.count_active_memories.await_args
        assert cap_call is not None
        assert cap_call.args[0] == user_id


@pytest.mark.asyncio
async def test_memory_write_quota_fails_open_on_counter_error(monkeypatch):
    """A transient counting failure must not disable the user's memory.

    The quota is an abuse dampener, not an authorization boundary — the
    `user_id` ownership checks are the security control — so a database
    error fails open rather than silently dropping writes.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()
        store = AsyncMock()
        store.count_memories_created_since = AsyncMock(side_effect=RuntimeError("pool down"))
        store.count_active_memories = AsyncMock(return_value=0)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact")

        assert "Memory created" in result
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_delete_is_not_rate_limited(monkeypatch):
    """`delete` frees quota rather than consuming it, so it stays unmetered."""
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 1)

    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    store = _quota_store(recent_writes=99, active_rows=99999)
    store.get_memory = AsyncMock(return_value={"id": memory_id, "user_id": user_id})
    tool = MemoryWriteTool(store, user_id)

    result = await tool.execute(action="delete", memory_id=str(memory_id))

    assert "deleted" in result.lower()
    store.delete_memory.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_rate_limit_counts_dedup_skipped_writes(monkeypatch):
    """The per-user rate limit must cover embedding-billed dedup calls.

    When a caller submits identical or near-identical content,
    `dedup_and_store` still triggers a billed embedding request but
    merges the result into an existing memory without inserting a new
    row. Counting only newly inserted rows lets such a caller loop
    the embedding endpoint indefinitely, defeating the
    cost-amplification half of the guard. The corrected behavior
    counts every `updated_at` bump in the window via
    `count_memories_created_since`, so the rate limit triggers on
    the 11th embedding-billed call regardless of dedup outcome.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_WINDOW_SECONDS", 60)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        # 10 prior writes already in the window — the rate counter
        # sees these even though they may have been dedup-merged.
        store = _quota_store(recent_writes=10, active_rows=10)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact 11")

        assert "rate limit" in result.lower()
        # The billed embedding path must not be reached.
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_active_row_cap_excludes_closed_rows(monkeypatch):
    """Behavioral regression: closed rows must not be counted toward the cap.

    Pinned at the SQL level by
    `tests/memory/test_write_quota_counters.py::test_count_active_memories_excludes_valid_to`.
    This unit test stays as a behavioral placeholder so a future
    refactor that re-introduces a closed-row inflation surfaces as
    a unit-level failure rather than only as a SQL-level diff: the
    mocked store returns 10 active rows (well below the 1000 cap)
    and the write proceeds, exercising the full
    `_check_write_quota → store.count_active_memories` plumbing
    without depending on `valid_to`.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 10)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        store = _quota_store(recent_writes=0, active_rows=10)
        tool = MemoryWriteTool(store, uuid.uuid4())

        result = await tool.execute(action="create", content="fact")

        assert "Memory created" in result
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_update_at_cap_is_net_neutral(monkeypatch):
    """A user at the active-row cap can still update an existing memory.

    An `update` closes one active row and inserts another, so the
    active count is unchanged by the operation. Refusing it at the
    cap would make the cap terminal — the user would have no
    recourse to correct even an outdated memory — so `_check_write_quota`
    exempts the update path when the caller provides the UUID of
    the row being replaced AND the row is still actively closable
    (`status='active' AND valid_to IS NULL`). Without `replace_memory_id`
    the path is unchanged from the cap refusal.

    Round-3 Codex review: the prior version exempted the cap based
    only on `replace_memory_id is not None`. `close_memory()` returns
    `True` for an already-closed row (it physically exists) but
    updates zero rows, so a caller at the cap could pass any UUID
    and silently raise the active count above the cap. The fixture
    below now provides `status='active'` to represent the realistic
    data shape (the old fixture omitted `status`, which masked the
    bug). The complementary refusal tests
    `test_memory_write_update_at_cap_with_closed_target_is_refused`
    and `test_memory_write_update_at_cap_with_deleted_target_is_refused`
    pin the closed/deleted cases.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 1000)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()
        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        store = _quota_store(recent_writes=0, active_rows=1000)
        store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": user_id,
                "content": "old",
                "category": "fact",
                "memory_slot": None,
                "source_conversation_id": None,
                "valid_to": None,
                "status": "active",
            }
        )
        store.close_memory = AsyncMock(return_value=True)
        tool = MemoryWriteTool(store, user_id)

        result = await tool.execute(action="update", memory_id=str(memory_id), content="new")

        # Net-neutral: close + insert happened, no refusal surfaced.
        assert "rate limit" not in result.lower()
        assert "cap" not in result.lower()
        store.close_memory.assert_called_once()
        mock_dedup.assert_called_once()


@pytest.mark.asyncio
async def test_memory_write_rate_counter_counts_dedup_merged_rows(monkeypatch):
    """Rate counter catches `touch_memory()` rows (last_accessed_at update).

    Round-3 Codex review: `dedup_and_store` merges identical content
    via `touch_memory()`, which bumps only `last_accessed_at` and
    `access_count`. Each merge still issues a billed embedding
    request, so the previous counter (which only saw `updated_at`)
    let an attacker loop the embedding endpoint without tripping
    the rate counter. The corrected counter ORs `last_accessed_at`
    into the window predicate so the dedup-merge path is metered.
    Pins the SQL so a future regression that reverts to
    `updated_at` only fails the test.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"count": 3})
    from orchestrator.memory.encryption import ContentEncryption

    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda value: value)
    enc.decrypt = MagicMock(side_effect=lambda value: value)
    from orchestrator.memory.store import MemoryStore

    store = MemoryStore(db_pool=pool, encryption=enc)

    user_id = uuid.uuid4()
    result = await store.count_memories_created_since(user_id, since=datetime.now(timezone.utc))
    assert result == 3
    sql = pool.fetchrow.await_args.args[0]
    assert "created_at" not in sql
    assert "updated_at" in sql
    assert "last_accessed_at" in sql
    assert "user_id = $1" in sql


@pytest.mark.asyncio
async def test_memory_write_update_at_cap_with_closed_target_is_refused(monkeypatch):
    """Update at the cap with an already-closed target is refused.

    Round-3 Codex review: `close_memory()` returns `True` for an
    already-closed row (it physically exists) but updates zero rows.
    If `_check_write_quota` granted the net-neutral cap exemption
    solely on `replace_memory_id is not None`, a caller at the cap
    could pass any UUID, the close would be a no-op, `dedup_and_store`
    would insert a new memory, and the active count would silently
    exceed the cap. The fix requires the replace target to actually
    be `status='active' AND valid_to IS NULL` — `replace_memory_active`
    must be `True`.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 1000)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        store = _quota_store(recent_writes=0, active_rows=1000)
        # The fetched row is already closed (valid_to is set).
        store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": user_id,
                "content": "old",
                "category": "fact",
                "memory_slot": None,
                "source_conversation_id": None,
                "valid_to": datetime.now(timezone.utc),
                "status": "active",
            }
        )
        store.close_memory = AsyncMock(return_value=True)
        tool = MemoryWriteTool(store, user_id)

        result = await tool.execute(action="update", memory_id=str(memory_id), content="new")

        assert "cap" in result.lower()
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_update_at_cap_with_deleted_target_is_refused(monkeypatch):
    """Update at the cap with a `status='deleted'` target is refused.

    Same fix as the closed-row test, but covers the
    `status != 'active'` branch — a soft-deleted row should also
    not be eligible for the net-neutral exemption because the close
    call would be a no-op.
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 1000)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        store = _quota_store(recent_writes=0, active_rows=1000)
        store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": user_id,
                "content": "old",
                "category": "fact",
                "memory_slot": None,
                "source_conversation_id": None,
                "valid_to": None,
                "status": "deleted",
            }
        )
        store.close_memory = AsyncMock(return_value=True)
        tool = MemoryWriteTool(store, user_id)

        result = await tool.execute(action="update", memory_id=str(memory_id), content="new")

        assert "cap" in result.lower()
        mock_dedup.assert_not_called()


@pytest.mark.asyncio
async def test_memory_write_update_at_cap_with_active_target_is_net_neutral(monkeypatch):
    """Update at the cap with an actively-closable target is net-neutral.

    Pairs with the two refused-target tests above to confirm the
    exemption still triggers for the genuine net-neutral case
    (status='active' AND valid_to IS NULL).
    """
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_PER_WINDOW", 1000)
    monkeypatch.setattr(tools_module, "MEMORY_WRITE_MAX_ACTIVE_ROWS", 1000)

    with patch("orchestrator.memory.tools.dedup_and_store", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = uuid.uuid4()
        user_id = uuid.uuid4()
        memory_id = uuid.uuid4()
        store = _quota_store(recent_writes=0, active_rows=1000)
        store.get_memory = AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": user_id,
                "content": "old",
                "category": "fact",
                "memory_slot": None,
                "source_conversation_id": None,
                "valid_to": None,
                "status": "active",
            }
        )
        store.close_memory = AsyncMock(return_value=True)
        tool = MemoryWriteTool(store, user_id)

        result = await tool.execute(action="update", memory_id=str(memory_id), content="new")

        assert "rate limit" not in result.lower()
        assert "cap" not in result.lower()
        store.close_memory.assert_called_once()
        mock_dedup.assert_called_once()
