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
        self.reservations: dict[Any, dict[str, Any]] = {}
        self.units_used = 0

    async def reserve(self, user: Any, amount: int, **options: Any) -> Any:
        key = str(uuid.uuid4())
        self.reservations[key] = {**options, "bound": amount, "status": "open"}
        return key

    async def settle(self, reservation: Any, amount: int, **kwargs: Any) -> Any:
        self.actions.append(("settle", reservation, amount))
        if self.error:
            raise self.error
        if reservation in self.reservations:
            record = self.reservations[reservation]
            self.units_used += int(record.get("extended_run", False))
            record["status"] = "settled"
        return SimpleNamespace(status="settled", overage_microusd=0)

    async def release(self, reservation: Any, **kwargs: Any) -> Any:
        self.actions.append(("release", reservation, 0))
        if self.error:
            raise self.error
        if reservation in self.reservations:
            self.reservations[reservation]["status"] = "released"
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


def test_production_reasoning_failure_arithmetic(monkeypatch) -> None:
    from orchestrator import model_routing
    from orchestrator.entitlements import attestation
    from orchestrator.entitlements.models import AccountRecord
    from orchestrator.entitlements.plans import AccountStatus, Plan, TrialState
    from orchestrator.entitlements.policy import load_policy, load_inference_policy
    from orchestrator.entitlements.resolver import resolve_account_policy

    inference = load_inference_policy("config/inference_policy.production.json")
    monkeypatch.setattr(runtime, "load_inference_policy", lambda: inference)
    # Arithmetic only: synthetic current monitoring, never a qualification claim.
    monkeypatch.setattr(attestation, "attestation_reasons", lambda *args, **kwargs: ())
    record = AccountRecord(
        uuid.uuid4(), Plan.PRO, "admin", AccountStatus.ACTIVE, False, TrialState.EXHAUSTED, 0, 0, 0
    )
    policy = resolve_account_policy(record, load_policy(), period="2026-11")
    params = {"messages": [{"role": "user", "content": "hi"}]}
    amounts: dict[str, int] = {}
    with model_routing.routing_context("reasoning"):
        candidates = runtime._priced_candidates(
            policy, runtime._request_bound(params), params, None, unknown_amounts=amounts
        )
    assert len(candidates) == 4
    bound = sum(entry[0] for entry in candidates)
    unknown = sum(amounts[entry[2].route_id] for entry in candidates)
    assert 0 < unknown < bound
    print(
        f"PRO_REASONING four_candidates rejection_usd=0 unknown_usd={unknown / 1_000_000:.6f} bound_usd={bound / 1_000_000:.6f}"
    )


@pytest.mark.asyncio
async def test_nonstream_response_after_deadline_stays_at_bound(monkeypatch) -> None:
    _qualified_policy(monkeypatch, route=_route())
    ledger = Ledger()
    service = _chat_service(
        reserve=AsyncMock(side_effect=ledger.reserve), settle=ledger.settle, release=ledger.release
    )

    async def late(**kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return {"choices": []}

    monkeypatch.setattr(runtime.litellm, "acompletion", late)
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable, match="deadline"):
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[{"role": "user", "content": "hi"}],
                _dispatch_timeout_s=0.01,
            )
        decision = next(iter(scope.terminal_decisions.values()))
        assert decision.amount == next(iter(ledger.reservations.values()))["bound"]
        assert decision.path == "dispatch_failure" and decision.operation == "settle"


@pytest.mark.asyncio
async def test_release_wins_before_conservative_finalizer(monkeypatch) -> None:
    scope, hold, ledger = held()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = ledger.release

    async def release(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(ledger, "release", release)
    first = asyncio.create_task(scope.release("hold", path="dispatch_not_sent"))
    await entered.wait()
    later = asyncio.create_task(scope.finish_failure(hold, 100, path="scope_cleanup"))
    resume.set()
    await asyncio.gather(first, later)
    assert ledger.actions == [("release", "hold", 0)]
    assert hold.actual == 0 and hold.decision is not None
    assert hold.decision.path == "dispatch_not_sent"
