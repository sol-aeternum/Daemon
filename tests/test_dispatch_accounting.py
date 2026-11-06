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
from tests.test_compute_runtime import (
    _account_scope,
    _chat_service,
    _qualified_policy,
    _route,
    _status_error,
)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_failure_before_completion_call_releases(monkeypatch, stream):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    service = _chat_service(settle=ledger.settle, release=ledger.release)
    async with _account_scope(service) as scope:

        async def reserve(*args, **kwargs):
            scope.closed = True
            return "hold"

        service.reserve.side_effect = reserve
        with pytest.raises(runtime.ComputeUnavailable, match="scope is closed"):
            await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
        provider.assert_not_awaited()
        decision = scope.terminal_decisions["hold"]
        assert (decision.operation, decision.amount, decision.path) == (
            "release",
            0,
            "dispatch_not_sent_local",
        )
        assert ledger.actions == [("release", "hold", 0)]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504])
async def test_synthetic_status_never_releases(monkeypatch, stream, status):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    service = _chat_service(settle=ledger.settle, release=ledger.release)

    async def chunks():
        raise _status_error(status)
        yield {}

    provider = (
        AsyncMock(return_value=chunks()) if stream else AsyncMock(side_effect=_status_error(status))
    )
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
        size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
        assert (decision.operation, decision.amount, decision.path) == (
            "settle",
            route.estimate_microusd(size.bound, 1),
            "dispatch_unknown_floor",
        )
        assert ledger.actions[0][0] == "settle"


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["cleanup", "handler"])
@pytest.mark.parametrize("stream", [False, True])
async def test_dispatch_handler_and_cleanup_join_winner(monkeypatch, winner, stream):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    service = _chat_service(settle=ledger.settle, release=ledger.release)
    entered, send_failure, accounting, finish = (asyncio.Event() for _ in range(4))
    original_settle, original_release = ledger.settle, ledger.release

    async def settle(*args, **kwargs):
        accounting.set()
        await finish.wait()
        return await original_settle(*args, **kwargs)

    async def release(*args, **kwargs):
        accounting.set()
        await finish.wait()
        return await original_release(*args, **kwargs)

    service.settle, service.release = settle, release

    async def provider(**kwargs):
        evidence = runtime.inference_transport.current.get()
        assert evidence is not None
        evidence.attempts.append(runtime.inference_transport.Attempt(status=503))
        entered.set()
        await send_failure.wait()
        raise _status_error(503)

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:
        dispatch = asyncio.create_task(
            runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        hold = scope.outstanding["pooled-hold"]
        if winner == "cleanup":
            cleanup = asyncio.create_task(
                scope.finish_failure(hold, hold.failure_amount, path="scope_cleanup")
            )
            await asyncio.wait_for(accounting.wait(), 1)
            send_failure.set()
        else:
            send_failure.set()
            await asyncio.wait_for(accounting.wait(), 1)
            cleanup = asyncio.create_task(
                scope.finish_failure(hold, hold.failure_amount, path="scope_cleanup")
            )
        actual = hold.actual
        finish.set()
        results = await asyncio.wait_for(
            asyncio.gather(dispatch, cleanup, return_exceptions=True), 1
        )
        assert isinstance(results[0], runtime.ComputeUnavailable)
        assert results[0].code != "settlement_conflict" and results[1] is None
        assert len(ledger.actions) == 1 and hold.actual == actual
        assert hold.decision is not None
        assert hold.decision.path == (
            "scope_cleanup" if winner == "cleanup" else "dispatch_provider_rejected"
        )
        assert ledger.actions[0][0] == ("settle" if winner == "cleanup" else "release")


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["cleanup", "stream"])
async def test_stream_finally_and_cleanup_preserve_actual(monkeypatch, winner):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    eof, accounting, finish = (asyncio.Event() for _ in range(3))
    original = ledger.settle

    async def settle(*args, **kwargs):
        accounting.set()
        await finish.wait()
        return await original(*args, **kwargs)

    service = _chat_service(settle=settle, release=ledger.release)

    async def chunks():
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        await eof.wait()

    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value=chunks()))
    async with _account_scope(service) as scope:
        stream = await runtime.guarded_completion(
            model=route.model, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        await anext(stream)
        hold = scope.outstanding["pooled-hold"]
        if winner == "cleanup":
            cleanup = asyncio.create_task(
                scope.finish_failure(hold, hold.failure_amount, path="scope_cleanup")
            )
            await asyncio.wait_for(accounting.wait(), 1)
            eof.set()
            pulling = asyncio.create_task(anext(stream))
        else:
            eof.set()
            pulling = asyncio.create_task(anext(stream))
            await asyncio.wait_for(accounting.wait(), 1)
            cleanup = asyncio.create_task(
                scope.finish_failure(hold, hold.failure_amount, path="scope_cleanup")
            )
        actual = hold.actual
        finish.set()
        result, _ = await asyncio.wait_for(
            asyncio.gather(pulling, cleanup, return_exceptions=True), 1
        )
        assert isinstance(result, StopAsyncIteration)
        assert len(ledger.actions) == 1 and hold.actual == actual
        assert actual == (hold.bound if winner == "cleanup" else 15)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("uncertain", [False, True])
async def test_managed_dispatch_keeps_bound_and_uncertainty_skip(monkeypatch, stream, uncertain):
    from tests.test_inference_repair_seams import covered

    scope, state, service, _ = covered()
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    service.resolve = _chat_service().resolve
    service.settle, service.release = ledger.settle, ledger.release
    service.reserve_for_attempt.return_value = "managed-hold"
    scope.inference_recovery = cast(Any, SimpleNamespace(mark=AsyncMock()))

    def failure():
        if uncertain:
            state.block("synthetic uncertainty")
        return _status_error(503)

    async def chunks():
        raise failure()
        yield {}

    async def provider(**kwargs):
        assert "client" not in kwargs
        if stream:
            return chunks()
        raise failure()

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(scope)
    try:
        with pytest.raises(Exception):
            result = await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
            if stream:
                async for _ in result:
                    pass
        assert state.call_failed
        if uncertain:
            assert not ledger.actions and scope.outstanding
        else:
            bound = service.reserve_for_attempt.await_args.args[1]
            assert ledger.actions == [("settle", "managed-hold", bound)]
            decision = scope.terminal_decisions["managed-hold"]
            assert decision.path == ("stream_end" if stream else "dispatch_failure")
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding",
    [
        "current",
        "state",
        "identity",
        "recovery",
        "missing_state",
        "missing_identity",
        "missing_recovery",
        "cross_account",
        "cross_scope",
        "mismatched_current",
    ],
)
async def test_partial_and_mismatched_bindings_forbid_every_financial_action(binding):
    from orchestrator.services.account_control.transaction import ObservationBlocked
    from orchestrator.tasks._invocation import InferenceInvocation, invocation
    from tests.test_inference_repair_seams import covered

    scope, state, service, _ = covered()
    ledger = Ledger()
    service.settle, service.release = ledger.settle, ledger.release
    scope.outstanding["hold"] = runtime.ReservationHold("hold", 100, unknown_amount=1)
    current = None
    if binding in {"current", "state", "identity", "recovery"}:
        scope.inference_invocation = state if binding == "state" else None
        scope.inference_identity = scope.inference_identity if binding == "identity" else None
        scope.inference_recovery = scope.inference_recovery if binding == "recovery" else None
        current = state if binding == "current" else None
    elif binding.startswith("missing_"):
        setattr(
            scope,
            {
                "missing_state": "inference_invocation",
                "missing_identity": "inference_identity",
                "missing_recovery": "inference_recovery",
            }[binding],
            None,
        )
    elif binding == "cross_account":
        scope.user_id = uuid.uuid4()
    elif binding == "cross_scope":
        scope.scope_id = uuid.uuid4()
    else:
        current = InferenceInvocation()
    token = invocation.set(current)
    try:
        assert scope.invocation_managed()
        for action in (
            scope.settle("hold", 0),
            scope.release("hold"),
            scope.finish_failure(scope.outstanding["hold"], 1, path="scope_cleanup"),
        ):
            with pytest.raises(ObservationBlocked):
                await action
        assert not ledger.actions and not scope.terminal_decisions
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("financial_failure", [False, True])
async def test_cancel_no_output_preserves_cancellation_and_floor(
    monkeypatch, stream, financial_failure
):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    if financial_failure:
        ledger.error = RuntimeError("synthetic accounting failure")
    service = _chat_service(settle=ledger.settle, release=ledger.release)
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Future()

    async def chunks():
        await wait()
        yield {}

    async def provider(**kwargs):
        if stream:
            return chunks()
        await wait()

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:

        async def consume():
            result = await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
            if stream:
                async for _ in result:
                    pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 1)
        decision = scope.terminal_decisions["pooled-hold"]
        size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
        assert ledger.actions == [("settle", "pooled-hold", route.estimate_microusd(size.bound, 1))]
        assert decision.path == "dispatch_unknown_floor"
        if financial_failure:
            assert scope.outstanding
            assert any("cleanup failed" in note for note in caught.value.__notes__)
        else:
            assert not scope.outstanding


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("output_tokens", [16, 128])
async def test_unknown_floor_is_captured_for_explicit_and_pinned_routes(
    monkeypatch, stream, pinned, output_tokens
):
    from tests.test_compute_runtime import routing_document

    route = _route()
    _qualified_policy(
        monkeypatch, route=route, routing=routing_document([route.model], min_output_tokens=33)
    )
    ledger = Ledger()
    service = _chat_service(settle=ledger.settle, release=ledger.release)
    size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
    expected = route.estimate_microusd(size.bound, min(output_tokens, 33))

    def change_quote():
        route.estimate_microusd = lambda *args: 999_999

    async def chunks():
        change_quote()
        raise RuntimeError("synthetic failure")
        yield {}

    async def provider(**kwargs):
        if stream:
            return chunks()
        change_quote()
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable):
            result = await runtime.guarded_completion(
                model=route.model,
                _route_id=route.route_id if pinned else None,
                max_tokens=output_tokens,
                messages=[{"role": "user", "content": "hi"}],
                stream=stream,
            )
            if stream:
                async for _ in result:
                    pass
        assert scope.terminal_decisions["pooled-hold"].amount == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_observer_unavailable_does_not_refuse_or_trust_exception(monkeypatch, stream):
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    ledger = Ledger()
    service = _chat_service(settle=ledger.settle, release=ledger.release)
    monkeypatch.setattr(runtime.inference_transport, "get_handler", lambda: None)

    async def provider(**kwargs):
        assert "client" not in kwargs
        raise _status_error(503)

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable):
            await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}], stream=stream
            )
        decision = scope.terminal_decisions["pooled-hold"]
        assert decision.operation == "settle" and decision.path == "dispatch_unknown_floor"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_scope_cleanup_financial_failure_does_not_hide_behind_provider_error(
    monkeypatch, cancelled
):
    from tests.test_compute_runtime import _funded_service

    service = _funded_service()
    service.settle.side_effect = RuntimeError("synthetic ledger failure")
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    expected = asyncio.CancelledError if cancelled else runtime.ComputeUnavailable
    scope = None
    with pytest.raises(expected) as caught:
        async with runtime.account_compute(object(), uuid.uuid4()) as scope:
            scope.outstanding["hold"] = runtime.ReservationHold("hold", 100, unknown_amount=7)
            if cancelled:
                raise asyncio.CancelledError()
            raise _status_error(503)
    service.settle.assert_awaited_once_with("hold", 7)
    assert scope is not None
    assert scope.outstanding["hold"].decision is not None
    if cancelled:
        assert "Scope cleanup failed" in caught.value.__notes__[0]
    else:
        assert isinstance(caught.value, runtime.ComputeUnavailable)
        assert caught.value.category == "settlement_failed" and not caught.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [False, True])
async def test_scope_cleanup_without_decision_uses_output_flag(monkeypatch, output):
    from tests.test_compute_runtime import _funded_service

    service = _funded_service()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    async with runtime.account_compute(object(), uuid.uuid4()) as scope:
        hold = runtime.ReservationHold("hold", 100, unknown_amount=7, output_released=output)
        scope.outstanding["hold"] = hold
    amount = 100 if output else 7
    service.settle.assert_awaited_once_with("hold", amount)
    assert hold.actual == amount
    assert hold.decision is not None and hold.decision.path == "scope_cleanup"


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_managed_scope_cleanup_keeps_bound_or_uncertainty_skip(monkeypatch, uncertain):
    from orchestrator.tasks._invocation import invocation
    from tests.test_inference_repair_seams import covered

    original, state, service, _ = covered()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    token = invocation.set(state)
    try:
        async with runtime.account_compute(
            object(),
            original.user_id,
            inference_identity=original.inference_identity,
            inference_recovery=original.inference_recovery,
        ) as scope:
            scope.outstanding["hold"] = runtime.ReservationHold("hold", 100, unknown_amount=7)
            if uncertain:
                state.block("synthetic uncertainty")
        if uncertain:
            service.settle.assert_not_awaited()
            assert scope.outstanding and not scope.terminal_decisions
        else:
            service.settle.assert_awaited_once_with("hold", 100)
            assert not scope.outstanding
        service.reconcile_expired_reservations.assert_not_awaited()
    finally:
        invocation.reset(token)
