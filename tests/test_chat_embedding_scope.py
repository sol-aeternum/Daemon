"""Deferred native prompt preparation shares one producer account scope."""

from contextlib import asynccontextmanager
import uuid
from unittest.mock import AsyncMock

import pytest

from orchestrator import main, compute_runtime as runtime
from orchestrator.memory import injection
from orchestrator.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_account_denial_never_prepares_memory_or_dispatches(monkeypatch):
    @asynccontextmanager
    async def denied(*args, **kwargs):
        raise runtime.ComputeUnavailable("account_unavailable", "denied")
        yield

    monkeypatch.setattr(main, "account_compute", denied)
    prepare = AsyncMock()
    stream = AsyncMock()
    monkeypatch.setattr(main, "stream_sse_chat", stream)
    with pytest.raises(runtime.ComputeUnavailable):
        _ = [
            frame
            async for frame in main._account_chat_frames(
                object(), uuid.uuid4(), prepare_system_prompt=prepare
            )
        ]
    prepare.assert_not_awaited()
    stream.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_prompt_preparation_runs_inside_single_producer_scope(monkeypatch, fail):
    events = []
    owner = uuid.uuid4()
    scope = runtime.ComputeScope(owner, AsyncMock())

    @asynccontextmanager
    async def account(*args, **kwargs):
        events.append("enter")
        token = runtime._scope.set(scope)
        try:
            yield scope
        finally:
            runtime._scope.reset(token)
            events.append("cleanup")

    monkeypatch.setattr(main, "account_compute", account)

    async def prepare():
        assert runtime.current_scope() is scope
        events.append("prepare")
        if fail:
            raise runtime.ComputeUnavailable(
                "settlement_failed", "failed", category=runtime.FAILURE_SETTLEMENT_FAILED
            )
        return "qualified memory prompt"

    async def stream(**kwargs):
        assert runtime.current_scope() is scope
        assert kwargs["system_prompt"] == "qualified memory prompt"
        events.append("dispatch")
        yield "frame"

    monkeypatch.setattr(main, "stream_sse_chat", stream)
    frames = main._account_chat_frames(
        object(), owner, prepare_system_prompt=prepare, system_prompt="base"
    )
    if fail:
        with pytest.raises(runtime.ComputeUnavailable):
            _ = [frame async for frame in frames]
        assert events == ["enter", "prepare", "cleanup"]
    else:
        assert [frame async for frame in frames] == ["frame"]
        assert events == ["enter", "prepare", "dispatch", "cleanup"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["cloud", "local"])
async def test_context_respects_l0_and_summary_locality(monkeypatch, pipeline):
    store = AsyncMock()
    store.get_conversation.return_value = {"user_id": uuid.uuid4(), "pipeline": pipeline}
    store.get_recent_messages.return_value = []
    rows = [
        {"content": "CLOUD", "local_only": False},
        {"content": "PRIVATE", "local_only": True},
        {"content": "UNKNOWN"},
    ]
    store.get_l0_memories.return_value = rows
    store.get_recent_summaries.return_value = rows
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "selected")
    context = await injection.build_memory_context(store, uuid.uuid4())
    assert "CLOUD" in context
    assert ("PRIVATE" in context) == (pipeline == "local")
    assert ("UNKNOWN" in context) == (pipeline == "local")
    assert store.get_l0_memories.await_args is not None
    assert store.get_l0_memories.await_args.kwargs["include_local"] == (pipeline == "local")


@pytest.mark.asyncio
@pytest.mark.parametrize("include_local", [False, True, None, "true"])
async def test_store_filters_source_locality_before_summary_limit(include_local):
    store = object.__new__(MemoryStore)
    store._pool = AsyncMock()
    store._pool.fetch.return_value = []
    await store.get_l0_memories(uuid.uuid4(), include_local=include_local)
    call = store._pool.fetch.await_args
    assert call is not None
    assert "($2::boolean OR local_only IS FALSE)" in call.args[0]
    assert call.args[2] is (include_local is True)
    await store.get_recent_summaries(uuid.uuid4(), limit=3, include_local=include_local)
    call = store._pool.fetch.await_args
    assert call is not None
    assert "($3::boolean OR local_only IS FALSE)" in call.args[0]
    assert call.args[0].index("local_only IS FALSE") < call.args[0].index("LIMIT $2")
    assert call.args[2] == 3
    assert call.args[3] is (include_local is True)
