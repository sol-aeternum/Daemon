"""Controlled-event concurrency/deadline regressions. No real provider, DB or timer waits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED
from orchestrator.memory import injection, retrieval
from tests.test_multi_query_gather import (
    CONVERSATION_ID,
    USER_ID,
    Provider,
    Store,
    WAIT_TIMEOUT,
    _rows,
    setup as setup,
)


async def _gather(store=None, provider=None):
    return await injection.gather_memory_context_inputs(
        store or Store(),
        user_id=USER_ID,
        pipeline="cloud",
        query_text="question",
        conversation_id=CONVERSATION_ID,
        subquery_provider=provider or Provider(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [0, 1, 2, 3])
async def test_bounded_arms_overlap_and_merge_by_input_not_completion_order(
    setup, monkeypatch, maximum
):
    setup.settings.memory_max_subqueries = maximum
    setup.batch.return_value = [
        SimpleNamespace(embedding=[0.2], model="m", storage_model="s")
    ] * maximum
    started = [asyncio.Event() for _ in range(maximum)]
    release = [asyncio.Event() for _ in range(maximum)]
    completed = [asyncio.Event() for _ in range(maximum)]
    order = []

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return setup.baseline
        i = int(role.split(":")[1])
        started[i].set()
        await release[i].wait()
        order.append(i)
        completed[i].set()
        return _rows(10 + i)

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    task = asyncio.create_task(_gather(provider=Provider(["one", "two", "three"])))
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), WAIT_TIMEOUT)
        for i in reversed(range(maximum)):
            release[i].set()
            await asyncio.wait_for(completed[i].wait(), WAIT_TIMEOUT)
        inputs = await asyncio.wait_for(task, WAIT_TIMEOUT)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert order == list(reversed(range(maximum)))
    assert [r["id"] for r in inputs.retrieved] == [
        *[r["id"] for r in setup.baseline],
        *[UUID(int=10 + i) for i in range(maximum)],
    ]
    assert inputs.added_memory_ids == tuple(UUID(int=10 + i) for i in range(maximum))
    if maximum:
        setup.batch.assert_awaited_once_with(["one", "two", "three"][:maximum])
    else:
        setup.batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["failure", "expiry", "cancel"])
@pytest.mark.parametrize("accounting_on_drain", [False, True])
async def test_every_sibling_drained_and_cleanup_accounting_error_dominates_fallback(
    setup, monkeypatch, trigger, accounting_on_drain
):
    started = [asyncio.Event(), asyncio.Event()]
    stopped = [False, False]
    accounting = ComputeUnavailable(
        "settlement_failed", "Accounting incomplete", category=FAILURE_SETTLEMENT_FAILED
    )
    never = asyncio.Event()

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return setup.baseline
        i = int(role.split(":")[1])
        started[i].set()
        try:
            await asyncio.gather(*(e.wait() for e in started))
            if i == 0 and trigger == "failure":
                raise RuntimeError("synthetic failure")
            await never.wait()
        finally:
            stopped[i] = True
            if i == 1 and accounting_on_drain:
                raise accounting

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    before = injection._before_component_deadline
    calls = 0

    async def expire(task, deadline):
        nonlocal calls
        calls += 1
        if calls == 2 and trigger == "expiry":
            await asyncio.gather(*(e.wait() for e in started))
            raise TimeoutError
        return await before(task, deadline)

    monkeypatch.setattr(injection, "_before_component_deadline", expire)
    task = asyncio.create_task(_gather())
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), WAIT_TIMEOUT)
        if trigger == "cancel":
            task.cancel()
        if accounting_on_drain:
            with pytest.raises(ComputeUnavailable) as caught:
                await asyncio.wait_for(task, WAIT_TIMEOUT)
            assert caught.value is accounting
        elif trigger == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT_TIMEOUT)
        else:
            inputs = await asyncio.wait_for(task, WAIT_TIMEOUT)
            assert inputs.retrieved == setup.baseline
            assert inputs.retrieved_limit is None and inputs.added_memory_ids == ()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert stopped == [True, True]


@pytest.mark.asyncio
async def test_one_deadline_starts_at_accepted_decomposition_includes_embedding_time(
    setup, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(injection, "monotonic", lambda: clock[0])
    deadlines = []
    remaining = []
    before = injection._before_component_deadline

    async def batch(texts):
        clock[0] += 6.0
        return setup.batch.return_value

    async def record(task, deadline):
        deadlines.append(deadline)
        remaining.append(deadline - clock[0])
        if len(deadlines) == 2:
            clock[0] += 3.0
        return await before(task, deadline)

    monkeypatch.setattr(injection, "embed_queries_with_metadata", batch)
    monkeypatch.setattr(injection, "_before_component_deadline", record)
    inputs = await _gather()
    assert deadlines == [108.0, 108.0] and remaining == [8.0, 2.0]
    assert inputs.retrieved == setup.baseline and inputs.retrieved_limit is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "duplicates", "failure", "cancel"])
async def test_component_logs_are_buffered_discarded_or_flushed_in_input_order(
    setup, monkeypatch, outcome
):
    store = Store()
    store.log_retrieval = AsyncMock()
    ready = asyncio.Event()
    finished = asyncio.Event()

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return setup.baseline
        if role == "sub:0":
            await finished.wait()  # arm 1 completes first
        await retrieval._emit_retrieval_log(
            store,
            query_role=role,
            query_text="PRIVATE",
            query_embedding=[0.2],
            candidate_memory_ids=[UUID(int=6)],
            selected_memory_ids=[UUID(int=6)],
            candidate_scores={str(UUID(int=6)): {"final_score": 0.7}},
        )
        store.log_retrieval.assert_not_awaited()
        if role == "sub:1":
            finished.set()
        else:
            ready.set()
            if outcome == "failure":
                raise RuntimeError("synthetic")
            if outcome == "cancel":
                await asyncio.Event().wait()
        return setup.baseline if outcome == "duplicates" else _rows(6)

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    task = asyncio.create_task(_gather(store))
    try:
        await asyncio.wait_for(ready.wait(), WAIT_TIMEOUT)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT_TIMEOUT)
        else:
            await asyncio.wait_for(task, WAIT_TIMEOUT)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if outcome == "success":
        rows = [call.kwargs for call in store.log_retrieval.await_args_list]
        assert [row["query_role"] for row in rows] == ["sub:0", "sub:1"]
        assert all(row["query_text"] == "" and row["query_embedding"] is None for row in rows)
        assert all(row["candidate_scores"][str(UUID(int=6))]["final_score"] == 0.7 for row in rows)
    else:
        store.log_retrieval.assert_not_awaited()
    assert retrieval._RETRIEVAL_LOG_BUFFER.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ordinary", "accounting", "cancel"])
async def test_flush_is_owned_partial_writes_are_not_replayed(setup, monkeypatch, failure):
    store = Store()
    flush_started = asyncio.Event()
    flush_stopped = asyncio.Event()
    error = ComputeUnavailable(
        "settlement_failed", "Accounting incomplete", category=FAILURE_SETTLEMENT_FAILED
    )
    persisted = []

    async def persist(**row):
        if row["query_role"] == "sub:0":
            persisted.append(row["query_role"])
            return
        if failure == "ordinary":
            raise RuntimeError("synthetic")
        if failure == "accounting":
            raise error
        flush_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            flush_stopped.set()

    store.log_retrieval = AsyncMock(side_effect=persist)

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return setup.baseline
        await retrieval._emit_retrieval_log(store, query_role=role)
        return _rows(6)

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    task = asyncio.create_task(_gather(store))
    try:
        if failure == "cancel":
            await asyncio.wait_for(flush_started.wait(), WAIT_TIMEOUT)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, WAIT_TIMEOUT)
            assert flush_stopped.is_set()
        elif failure == "accounting":
            with pytest.raises(ComputeUnavailable) as caught:
                await asyncio.wait_for(task, WAIT_TIMEOUT)
            assert caught.value is error
        else:
            inputs = await asyncio.wait_for(task, WAIT_TIMEOUT)
            assert inputs.added_memory_ids == (UUID(int=6),)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert persisted == ["sub:0"]
    assert store.log_retrieval.await_count == 2


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_abandon_sibling_cleanup(setup, monkeypatch):
    started = [asyncio.Event(), asyncio.Event()]
    cleaning = [asyncio.Event(), asyncio.Event()]
    release_cleanup = asyncio.Event()
    stopped = [False, False]

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return setup.baseline
        i = int(role.split(":")[1])
        started[i].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[i].set()
            await release_cleanup.wait()
            stopped[i] = True

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    task = asyncio.create_task(_gather())
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), WAIT_TIMEOUT)
        task.cancel()
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in cleaning)), WAIT_TIMEOUT)
        task.cancel()
        assert not task.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, WAIT_TIMEOUT)
    finally:
        release_cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert stopped == [True, True]
