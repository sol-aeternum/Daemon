"""Title workers fail closed on stale titles and unavailable conversation state."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.worker import jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["generate_title", "generate_conversation_title_job"])
@pytest.mark.parametrize("state", ["no_store", "missing", "read_failed", "locked"])
async def test_title_workers_do_not_generate_without_valid_editable_conversation(
    monkeypatch, job_name, state
):
    store = MagicMock(spec=MemoryStore)
    store.get_conversation = AsyncMock(
        return_value=None
        if state == "missing"
        else {"title": None, "title_locked": state == "locked", "user_id": uuid.uuid4()}
    )
    if state == "read_failed":
        store.get_conversation.side_effect = RuntimeError("read failed")
    store.get_messages = AsyncMock()
    store.save_generated_conversation_title = AsyncMock()
    generator = AsyncMock()
    scope = MagicMock(side_effect=AssertionError("must not open compute scope"))
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    monkeypatch.setattr(jobs, "account_compute", scope)
    ctx = {"store": None if state == "no_store" else store, "db_pool": object()}
    conversation_id = uuid.uuid4()

    if job_name == "generate_title":
        assert await jobs.generate_title(ctx, conversation_id, uuid.uuid4()) is None
    else:
        result = await jobs.generate_conversation_title_job(ctx, conversation_id)
        expected = {
            "no_store": {"status": "skipped", "reason": "store_unavailable"},
            "missing": {"status": "not_found"},
            "read_failed": {"status": "error", "reason": "read_failed"},
            "locked": {"status": "skipped", "reason": "title_locked"},
        }
        assert result == expected[state]
    generator.assert_not_awaited()
    scope.assert_not_called()
    store.get_messages.assert_not_awaited()
    store.save_generated_conversation_title.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["generate_title", "generate_conversation_title_job"])
@pytest.mark.parametrize("save_result", [True, False, "error"])
async def test_title_workers_report_only_persisted_titles(monkeypatch, job_name, save_result):
    owner, conversation_id = uuid.uuid4(), uuid.uuid4()
    pool = object()
    store = MagicMock(spec=MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"user_id": owner, "title": "New conversation", "title_locked": False}
    )
    store.get_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    source_message_id = uuid.uuid4()
    store.get_owned_message = AsyncMock(return_value={"role": "user", "content": "hello"})
    store.save_generated_conversation_title = AsyncMock(return_value=save_result)
    if save_result == "error":
        store.save_generated_conversation_title.side_effect = RuntimeError("save failed")
    generator = AsyncMock(return_value="Generated title")
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    scopes = []

    @asynccontextmanager
    async def checked_scope(scope_pool, account_id, **kwargs):
        assert scope_pool is pool
        assert account_id == owner
        scopes.append(kwargs)
        yield

    monkeypatch.setattr(jobs, "account_compute", checked_scope)
    ctx = {"store": store, "db_pool": pool}
    if job_name == "generate_title":
        result = await jobs.generate_title(ctx, conversation_id, source_message_id)
        assert result == ("Generated title" if save_result is True else None)
        store.get_owned_message.assert_awaited_once_with(
            source_message_id, user_id=owner, conversation_id=conversation_id
        )
    else:
        result = await jobs.generate_conversation_title_job(ctx, conversation_id)
        expected = (
            {"status": "ok", "title": "Generated title"}
            if save_result is True
            else {"status": "skipped", "reason": "title_changed_or_locked"}
            if save_result is False
            else {"status": "error", "reason": "persist_failed"}
        )
        assert result == expected
    assert scopes == [
        {"operation": "agent", "auto_route": True, "background": True, "profile": "background"}
    ]
    generator.assert_awaited_once()
    store.save_generated_conversation_title.assert_awaited_once_with(
        conversation_id, title="Generated title", expected_title="New conversation"
    )
