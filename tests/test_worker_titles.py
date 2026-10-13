"""Title workers fail closed on stale titles and unavailable conversation state."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.worker import jobs
from orchestrator.services.account_control.guard import CapturedGeneration


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["generate_title", "generate_conversation_title_job"])
@pytest.mark.parametrize("state", ["no_store", "missing", "read_failed", "locked"])
async def test_title_workers_do_not_generate_without_valid_editable_conversation(
    monkeypatch, job_name, state
):
    store = MagicMock(spec=MemoryStore)
    owner = uuid.uuid4()
    store.read_conversation_title_inputs = AsyncMock(
        return_value=None
        if state == "missing"
        else {"title": None, "title_locked": state == "locked", "user_id": uuid.uuid4()}
    )
    if state == "read_failed":
        store.read_conversation_title_inputs.side_effect = RuntimeError("read failed")
    store.get_messages = AsyncMock()
    store.save_generated_conversation_title = AsyncMock()
    generator = AsyncMock()
    scope = MagicMock(side_effect=AssertionError("must not open compute scope"))
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    monkeypatch.setattr(jobs, "account_compute", scope)
    ctx = {"store": None if state == "no_store" else store, "db_pool": object()}
    conversation_id = uuid.uuid4()
    capture = {"origin_account_id": str(owner), "origin_account_generation": 7}

    if job_name == "generate_title":
        assert await jobs.generate_title(ctx, conversation_id, uuid.uuid4(), **capture) is None
    else:
        result = await jobs.generate_conversation_title_job(ctx, conversation_id, **capture)
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
    store.read_conversation_title_inputs = AsyncMock(
        return_value={
            "title": "New conversation",
            "title_locked": False,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    store.check_conversation_title_generation = AsyncMock(return_value=True)
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
    capture = {"origin_account_id": str(owner), "origin_account_generation": 7}
    expected_capture = CapturedGeneration(owner, 7)
    if job_name == "generate_title":
        result = await jobs.generate_title(ctx, conversation_id, source_message_id, **capture)
        assert result == ("Generated title" if save_result is True else None)
        store.read_conversation_title_inputs.assert_awaited_once_with(
            conversation_id, captured_generation=expected_capture, user_message_id=source_message_id
        )
    else:
        result = await jobs.generate_conversation_title_job(ctx, conversation_id, **capture)
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
        conversation_id,
        title="Generated title",
        expected_title="New conversation",
        captured_generation=expected_capture,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["generate_title", "generate_conversation_title_job"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"origin_account_id": "bad", "origin_account_generation": 0},
        *[
            {"origin_account_id": str(uuid.uuid4()), "origin_account_generation": value}
            for value in (None, True, -1, "0", 1.0, 2**63)
        ],
    ],
)
async def test_legacy_or_malformed_title_payload_is_terminal_without_reads(
    monkeypatch, job_name, payload
):
    store = MagicMock(spec=MemoryStore)
    generator = AsyncMock()
    monkeypatch.setattr(jobs, "generate_conversation_title", generator)
    compute = MagicMock(side_effect=AssertionError("legacy job must not admit compute"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    args = ({"store": store, "db_pool": object()}, uuid.uuid4())
    if job_name == "generate_title":
        result = await jobs.generate_title(*args, uuid.uuid4(), **payload)
        assert result is None
    else:
        result = await jobs.generate_conversation_title_job(*args, **payload)
        assert result == {"status": "skipped", "reason": "invalid_capture"}
    assert store.mock_calls == []
    compute.assert_not_called()
    generator.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [None, "legacy raw text", [], True])
async def test_first_message_job_never_falls_back_to_history_on_invalid_source(message_id):
    store = MagicMock(spec=MemoryStore)
    assert (
        await jobs.generate_title(
            {"store": store, "db_pool": object()},
            uuid.uuid4(),
            message_id,
            origin_account_id=str(uuid.uuid4()),
            origin_account_generation=7,
        )
        is None
    )
    assert store.mock_calls == []
