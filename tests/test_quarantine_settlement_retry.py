"""Accounting-pass fences/budgets; no DB, provider, or execution admission."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from orchestrator.entitlements import service as services
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks import runner
from orchestrator.tasks._invocation import InferenceInvocation, invocation
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.store import TaskStore


def setup_pass(monkeypatch, count=3):
    pool = object()
    store = TaskStore(
        cast(Any, pool),
        cast(Any, None),
        cast(Any, None),
        inference_recovery=InferenceRecovery(pool),
    )
    keys = [(UUID(int=1), UUID(int=i + 2)) for i in range(count)]
    monkeypatch.setattr(store, "_quarantine_hold_candidates", AsyncMock(return_value=keys))
    monkeypatch.setattr(store, "_observe_quarantine_hold", AsyncMock(return_value=100))
    money = SimpleNamespace(settle=AsyncMock())
    constructor = []

    def service(actual_pool, **kwargs):
        assert actual_pool is pool and kwargs == {"_bounded_settlement": True}
        constructor.append(invocation.get())
        return money

    monkeypatch.setattr(services, "EntitlementService", service)
    return pool, store, keys, money, constructor


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_inherited_execution_is_refused_before_any_sweep_effect(monkeypatch, blocked):
    pool, store, _, money, constructor = setup_pass(monkeypatch)
    wake = AsyncMock()
    monkeypatch.setattr(store, "due_for_wakeup", wake)
    state = InferenceInvocation()
    if blocked:
        state.block("original_unknown")
    token = invocation.set(state)
    try:
        with pytest.raises(ObservationBlocked, match="inherited_inference_invocation"):
            await runner.sweep_tasks({"task_store": store, "db_pool": pool, "redis": AsyncMock()})
        assert invocation.get() is state
        assert state.uncertainty == ("original_unknown" if blocked else None)
        wake.assert_not_awaited()
        cast(AsyncMock, store._quarantine_hold_candidates).assert_not_awaited()
        money.settle.assert_not_awaited()
        assert not constructor
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["pool", "recovery"])
async def test_wrong_composition_refuses_without_observation_or_latch(monkeypatch, mismatch):
    pool, store, _, money, _ = setup_pass(monkeypatch)
    if mismatch == "recovery":
        store._inference = InferenceRecovery(object())
    else:
        pool = object()
    with pytest.raises(ObservationBlocked):
        await runner.sweep_tasks({"task_store": store, "db_pool": pool})
    cast(AsyncMock, store._quarantine_hold_candidates).assert_not_awaited()
    money.settle.assert_not_awaited()
    assert invocation.get() is None


@pytest.mark.asyncio
async def test_accounting_without_redis_preserves_wakeup_return_count(monkeypatch):
    pool, store, keys, money, states = setup_pass(monkeypatch)
    assert await runner.sweep_tasks({"task_store": store, "db_pool": pool}) == 0
    assert money.settle.await_count == 3
    assert store._quarantine_hold_after == keys[-1]
    assert len(states) == 1 and states[0].uncertainty is None
    assert invocation.get() is None
    for call in money.settle.await_args_list:
        assert call.args[1] == 100
        assert call.kwargs == {"usage": {"estimated_cost": True, "recovered_after_timeout": True}}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["discovery", "observe", "settle", "latched_return"])
async def test_unknown_failure_stops_pass_preserves_error_and_attempted_cursor(monkeypatch, phase):
    pool, store, keys, money, states = setup_pass(monkeypatch)
    original = ConnectionError("exact failed owner")
    wake = AsyncMock()
    monkeypatch.setattr(store, "due_for_wakeup", wake)
    if phase == "discovery":
        cast(AsyncMock, store._quarantine_hold_candidates).side_effect = original
    elif phase == "observe":
        cast(AsyncMock, store._observe_quarantine_hold).side_effect = original
    elif phase == "settle":
        money.settle.side_effect = original
    else:

        async def latched(*args, **kwargs):
            state = invocation.get()
            assert state is not None
            state.block("unknown_returned_owner")

        money.settle.side_effect = latched
    with pytest.raises(
        ObservationBlocked if phase == "latched_return" else ConnectionError
    ) as caught:
        await runner.sweep_tasks({"task_store": store, "db_pool": pool, "redis": object()})
    if phase != "latched_return":
        assert caught.value is original
    assert states[0].uncertainty is not None
    assert store._quarantine_hold_after == (None if phase == "discovery" else keys[0])
    assert (
        money.settle.await_count <= 1
        and cast(AsyncMock, store._observe_quarantine_hold).await_count <= 1
    )
    wake.assert_not_awaited()
    assert invocation.get() is None


@pytest.mark.asyncio
async def test_cancel_preserves_original_object_and_stops_before_next_item(monkeypatch):
    pool, store, keys, money, states = setup_pass(monkeypatch)
    cancelled = asyncio.CancelledError("original financial cancellation")
    money.settle.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError) as caught:
        await runner.sweep_tasks({"task_store": store, "db_pool": pool})
    assert caught.value is cancelled
    assert store._quarantine_hold_after == keys[0] and money.settle.await_count == 1
    assert states[0].uncertainty is not None and invocation.get() is None


@pytest.mark.asyncio
async def test_single_absolute_budget_bounds_acquisition_and_all_items(monkeypatch):
    pool, store, keys, money, states = setup_pass(monkeypatch)
    timeouts = []
    native = asyncio.timeout_at
    running = asyncio.get_running_loop()
    before = running.time()

    def record(deadline):
        timeouts.append(deadline)
        return native(deadline)

    monkeypatch.setattr(runner.asyncio, "timeout_at", record)
    started = asyncio.Event()

    async def hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    money.settle.side_effect = hang
    task = asyncio.create_task(runner.sweep_tasks({"task_store": store, "db_pool": pool}))
    await started.wait()
    with pytest.raises(TimeoutError):
        await task
    assert len(timeouts) == 1 and before + 2 <= timeouts[0] <= before + 2.1
    assert store._quarantine_hold_after == keys[0] and money.settle.await_count == 1
    assert states[0].uncertainty is not None and invocation.get() is None


@pytest.mark.asyncio
async def test_concurrent_closed_observation_skips_money_without_new_authority(monkeypatch):
    pool, store, keys, money, _ = setup_pass(monkeypatch)
    cast(AsyncMock, store._observe_quarantine_hold).side_effect = [None, 100, None]
    await runner.sweep_tasks({"task_store": store, "db_pool": pool})
    assert money.settle.await_count == 1 and money.settle.await_args.args == (keys[1][1], 100)


@pytest.mark.asyncio
async def test_constructor_failure_does_not_leave_accounting_context_installed(monkeypatch):
    pool, store, _, _, _ = setup_pass(monkeypatch)

    def fail(*args, **kwargs):
        raise ValueError("bad policy")

    monkeypatch.setattr(services, "EntitlementService", fail)
    with pytest.raises(ValueError):
        await runner.sweep_tasks({"task_store": store, "db_pool": pool})
    assert invocation.get() is None


@pytest.mark.asyncio
async def test_missing_injection_never_enables_accounting(monkeypatch):
    pool, store, _, money, _ = setup_pass(monkeypatch)
    await runner.sweep_tasks({"task_store": store})
    store._inference = None
    await runner.sweep_tasks({"task_store": store, "db_pool": pool})
    cast(AsyncMock, store._quarantine_hold_candidates).assert_not_awaited()
    money.settle.assert_not_awaited()
