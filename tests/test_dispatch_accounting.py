"""Synthetic financial ownership checks for ordinary dispatch accounting (#681)."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.entitlements import EntitlementService
from tests.test_compute_runtime import _account_scope, _chat_service, _qualified_policy, _route


class Ledger:
    def __init__(self) -> None:
        self.actions: list[tuple[str, Any, int]] = []
        self.error: Exception | None = None

    async def settle(self, reservation: Any, amount: int, **kwargs: Any) -> Any:
        self.actions.append(("settle", reservation, amount))
        if self.error:
            raise self.error
        return SimpleNamespace(status="settled", overage_microusd=0)

    async def release(self, reservation: Any, **kwargs: Any) -> Any:
        self.actions.append(("release", reservation, 0))
        if self.error:
            raise self.error
        return SimpleNamespace(status="released", overage_microusd=0)


def held() -> tuple[runtime.ComputeScope, runtime.ReservationHold, Ledger]:
    ledger = Ledger()
    scope = runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, ledger))
    hold = runtime.ReservationHold("hold", 100)
    scope.outstanding["hold"] = hold
    return scope, hold, ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("released", [False, True])
async def test_terminal_operation_identity_and_failure_join(released: bool) -> None:
    scope, hold, ledger = held()
    if released:
        await scope.release("hold", path="dispatch_not_sent")
    else:
        await scope.settle("hold", 0, path="scope_cleanup")
    with pytest.raises(runtime.ComputeUnavailable, match="differently"):
        if released:
            await scope.settle("hold", 0)
        else:
            await scope.release("hold")
    await scope.finish_failure(hold, 99, path="dispatch_unknown_floor")
    assert len(ledger.actions) == 1 and hold.actual == 0
    assert hold.decision is scope.terminal_decisions["hold"]
    assert scope.settled == {"hold": 0}


@pytest.mark.asyncio
async def test_failed_terminal_owner_is_never_retried() -> None:
    scope, hold, ledger = held()
    ledger.error = RuntimeError("financial failure")
    with pytest.raises(runtime.ComputeUnavailable, match="settlement failed"):
        await scope.release("hold", path="dispatch_not_sent")
    owner = hold.settlement
    with pytest.raises(runtime.ComputeUnavailable, match="settlement failed"):
        await scope.finish_failure(hold, 100, path="scope_cleanup")
    assert hold.settlement is owner
    assert scope.outstanding["hold"] is hold and not scope.settled
    assert ledger.actions == [("release", "hold", 0)]


@pytest.mark.asyncio
async def test_racing_terminal_callers_join_first_decision(monkeypatch) -> None:
    scope, hold, ledger = held()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = ledger.settle

    async def settle(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(ledger, "settle", settle)
    first = asyncio.create_task(scope.settle("hold", 30, path="scope_cleanup"))
    await entered.wait()
    later = asyncio.create_task(
        scope.finish_failure(hold, 0, path="dispatch_not_sent", release=True)
    )
    resume.set()
    await asyncio.gather(first, later)
    assert hold.actual == 30
    assert ledger.actions == [("settle", "hold", 30)]
    assert hold.decision is not None and hold.decision.path == "scope_cleanup"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_synthetic_failure_uses_captured_floor(monkeypatch, stream) -> None:
    route = _route(input_price=1_000_000)
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    service = _chat_service(settle=ledger.settle, release=ledger.release)

    async def chunks():
        raise RuntimeError("offline")
        yield {}

    provider = AsyncMock(return_value=chunks()) if stream else AsyncMock(side_effect=RuntimeError())
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable):
            result = await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
            if stream:
                async for _ in result:
                    pass
        decision = scope.terminal_decisions["pooled-hold"]
        input_size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
        assert decision.amount == route.estimate_microusd(input_size.bound, 1)
        assert decision.path == "dispatch_unknown_floor" and decision.operation == "settle"
        assert ledger.actions == [("settle", "pooled-hold", decision.amount)]


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["none", "open", "settled", "released"])
async def test_extended_carrier_release_counts_nonreleased_history(other) -> None:
    scope, carrier, ledger = held()
    scope.extended = scope.extended_started = True
    scope.extended_holds = 1
    carrier.extended_run = carrier.extended_counted = True
    if other != "none":
        second = runtime.ReservationHold("second", 100, extended_counted=True)
        scope.outstanding["second"] = second
        scope.extended_holds += 1
        if other == "settled":
            await scope.settle("second", 3)
        elif other == "released":
            await scope.release("second", path="dispatch_not_sent")
    await scope.release("hold", path="dispatch_provider_rejected")
    refunded = other in {"none", "released"}
    assert ledger.actions[-1] == ("release" if refunded else "settle", "hold", 0)
    assert scope.extended_started is not refunded
    assert scope.extended_holds == (0 if refunded else 2)
    assert not scope.extended_lock.locked()


@pytest.mark.asyncio
async def test_extended_release_failure_retains_unit_and_count() -> None:
    scope, carrier, ledger = held()
    carrier.extended_run = carrier.extended_counted = True
    scope.extended_started = True
    scope.extended_holds = 1
    ledger.error = RuntimeError()
    with pytest.raises(runtime.ComputeUnavailable):
        await scope.release("hold")
    assert scope.extended_started and scope.extended_holds == 1
    assert not scope.extended_lock.locked()


@pytest.mark.asyncio
async def test_extended_release_waiter_cancellation_keeps_financial_lock(monkeypatch) -> None:
    scope, carrier, ledger = held()
    carrier.extended_run = carrier.extended_counted = True
    scope.extended_started = True
    scope.extended_holds = 1
    entered, resume = asyncio.Event(), asyncio.Event()
    original = ledger.release

    async def release(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(ledger, "release", release)
    waiter = asyncio.create_task(scope.release("hold"))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert scope.extended_lock.locked() and scope.extended_started
    resume.set()
    await scope.finish_failure(carrier, 100, path="scope_cleanup")
    assert not scope.extended_lock.locked() and not scope.extended_started
    assert scope.extended_holds == 0
