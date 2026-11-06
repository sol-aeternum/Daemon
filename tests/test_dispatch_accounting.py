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
