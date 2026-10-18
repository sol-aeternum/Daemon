"""PR550 embedding-boundary counterexamples: synthetic accounting and HTTP only."""

import asyncio
from contextvars import Context
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.memory import embedding_adapter as adapter
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks._invocation import InferenceInterrupted, InferenceInvocation, invocation
from tests.test_embedding_adapter import installed as _installed, receipt

installed = _installed


@pytest.fixture
def covered(installed):
    scope = installed.scope
    identity = AttemptIdentity(
        uuid4(), scope.user_id, 0, uuid4(), 1, scope.scope_id, uuid4(), uuid4()
    )
    shared = InferenceInvocation()
    recovery = SimpleNamespace(mark=AsyncMock())
    scope.inference_identity = identity
    scope.inference_invocation = shared
    scope.inference_recovery = recovery
    scope.expected_period = "2026-10"
    installed.service.reserve_for_attempt = AsyncMock(
        side_effect=lambda *a, **kw: SimpleNamespace(id=uuid4())
    )
    token = invocation.set(shared)
    try:
        yield SimpleNamespace(
            **vars(installed), shared=shared, identity=identity, recovery=recovery
        )
    finally:
        invocation.reset(token)


def blocked_send():
    raise InferenceInterrupted("synthetic lease/cancel/permission refusal")


@pytest.mark.asyncio
@pytest.mark.parametrize("ambient", ["same", "reset", "moved"])
@pytest.mark.parametrize("reason", ["uncertain", "failed", "callback", "closed"])
async def test_bound_mark_refuses_without_mutating_hold(covered, ambient, reason):
    shared, scope = covered.shared, covered.scope
    expected = ObservationBlocked if reason == "uncertain" else InferenceInterrupted
    outer = pytest.raises(ObservationBlocked) if reason == "uncertain" else None
    observed = {}

    async def attempt():
        async with runtime.metered_embedding_call(covered.route, 1000) as charge:
            hold = next(iter(scope.outstanding.values()))
            if reason == "uncertain":
                shared.block("synthetic_unknown_ack")
            elif reason == "failed":
                shared.call_failed = True
            elif reason == "callback":
                shared.send_check = blocked_send
            else:
                scope.closed = True
            token = invocation.set(None if ambient != "same" else shared)
            try:
                try:
                    if ambient == "moved":
                        Context().run(charge.mark_dispatched)
                    else:
                        charge.mark_dispatched()
                except expected:
                    observed["refused"] = True
                else:
                    observed["refused"] = False
                observed.update(actual=hold.actual, settlement=hold.settlement, state=charge._state)
            finally:
                invocation.reset(token)

    if outer is not None:
        with outer:
            await attempt()
        covered.service.settle.assert_not_awaited()
    else:
        await attempt()
        covered.service.settle.assert_awaited_once()
        assert covered.service.settle.await_args.args[1] == 0
    assert covered.service.reserve_for_attempt.await_count == 1
    covered.service.reserve.assert_not_awaited()
    assert observed == {"refused": True, "actual": 0, "settlement": None, "state": "pre_dispatch"}


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["mark", "confirm", "finally"])
@pytest.mark.parametrize("change", ["state", "identity", "recovery", "account", "scope"])
async def test_charge_cannot_recapture_replaced_scope_binding(covered, boundary, change):
    scope, shared = covered.scope, covered.shared
    observed = {}
    with pytest.raises(ObservationBlocked):
        async with runtime.metered_embedding_call(covered.route, 1000) as charge:
            hold = next(iter(scope.outstanding.values()))
            if boundary != "mark":
                charge.mark_dispatched()
            if change == "state":
                scope.inference_invocation = InferenceInvocation()
            elif change == "identity":
                scope.inference_identity = replace(covered.identity, task_id=uuid4())
            elif change == "recovery":
                scope.inference_recovery = SimpleNamespace(mark=AsyncMock())
            elif change == "account":
                scope.user_id = uuid4()
            else:
                scope.scope_id = uuid4()
            token = invocation.set(None)
            try:
                if boundary != "finally":
                    try:
                        if boundary == "mark":
                            charge.mark_dispatched()
                        else:
                            charge.confirm(input_tokens=2, actual_microusd=1)
                    except ObservationBlocked:
                        observed["refused"] = True
                    else:
                        observed["refused"] = False
                    observed["actual"] = hold.actual
                observed["settlement"] = hold.settlement
            finally:
                invocation.reset(token)
    assert shared.uncertainty is not None
    covered.service.settle.assert_not_awaited()
    assert observed["settlement"] is None
    if boundary != "finally":
        assert observed["refused"] is True
        assert observed["actual"] == (0 if boundary == "mark" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["mark", "confirm"])
async def test_conflicting_context_blocks_both_original_and_current(covered, boundary):
    other = InferenceInvocation()
    refused = False
    with pytest.raises(ObservationBlocked):
        async with runtime.metered_embedding_call(covered.route, 1000) as charge:
            if boundary == "confirm":
                charge.mark_dispatched()
            token = invocation.set(other)
            try:
                try:
                    if boundary == "mark":
                        charge.mark_dispatched()
                    else:
                        charge.confirm(input_tokens=2, actual_microusd=1)
                except ObservationBlocked:
                    refused = True
            finally:
                invocation.reset(token)
    assert other.uncertainty is not None and covered.shared.uncertainty is not None
    assert refused
    covered.service.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncovered_charge_cannot_adopt_ambient_coverage(installed):
    other = InferenceInvocation()
    async with runtime.metered_embedding_call(installed.route, 1000) as charge:
        token = invocation.set(other)
        try:
            with pytest.raises(ObservationBlocked):
                charge.mark_dispatched()
        finally:
            invocation.reset(token)
    assert other.uncertainty is not None
    installed.service.reserve.assert_awaited_once()
    assert installed.service.settle.await_args.args[1] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ambient", ["same", "reset", "moved"])
async def test_confirm_refuses_uncertainty_without_mutating_receipt(covered, ambient):
    observed = {}
    with pytest.raises(ObservationBlocked):
        async with runtime.metered_embedding_call(covered.route, 1000) as charge:
            charge.mark_dispatched()
            covered.shared.block("synthetic_unknown_ack")
            token = invocation.set(None if ambient != "same" else covered.shared)
            try:
                try:
                    if ambient == "moved":
                        Context().run(charge.confirm, input_tokens=2, actual_microusd=1)
                    else:
                        charge.confirm(input_tokens=2, actual_microusd=1)
                except ObservationBlocked:
                    observed["refused"] = True
                else:
                    observed["refused"] = False
                observed.update(
                    actual=charge.actual,
                    tokens=charge.input_tokens,
                    hold=next(iter(covered.scope.outstanding.values())).actual,
                )
            finally:
                invocation.reset(token)
    covered.service.settle.assert_not_awaited()
    assert observed == {"refused": True, "actual": None, "tokens": None, "hold": None}


@pytest.mark.asyncio
async def test_known_interruption_keeps_valid_receipt_and_original_financial_binding(covered):
    scope, shared = covered.scope, covered.shared
    async with runtime.metered_embedding_call(covered.route, 1000) as charge:
        charge.mark_dispatched()
        shared.call_failed = True
        shared.send_check = blocked_send
        scope.closed = True
        Context().run(charge.confirm, input_tokens=2, actual_microusd=1)
    call = covered.service.reserve_for_attempt.await_args
    assert call.args == (covered.identity, 20)
    assert call.kwargs == {
        "operation": "chat",
        "provider": covered.route.provider,
        "model": covered.route.model,
        "route_id": covered.route.route_id,
        "premium": False,
        "extended": False,
        "extended_run": False,
        "background": False,
        "expected_period": "2026-10",
    }
    reservation = covered.service.settle.await_args.args[0]
    covered.recovery.mark.assert_awaited_once_with(covered.identity, reservation.id, shared)
    covered.service.settle.assert_awaited_once_with(
        reservation, 1, usage={"input_tokens": 2, "output_tokens": 0}
    )
    assert scope.outstanding == {} and shared.uncertainty is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("secondary", ["uncertain", "settlement"])
async def test_primary_exception_survives_nested_finally_refusal(covered, cancel, secondary):
    primary = asyncio.CancelledError("original") if cancel else RuntimeError("original")
    with pytest.raises(type(primary)) as caught:
        async with runtime.metered_embedding_call(covered.route, 1000) as charge:
            charge.mark_dispatched()
            try:
                raise primary
            finally:
                if secondary == "uncertain":
                    covered.shared.block("synthetic_unknown_ack")
                else:
                    covered.service.settle.side_effect = RuntimeError("secondary")
    assert caught.value is primary
    assert covered.shared.call_failed and covered.shared.uncertainty is not None
    if secondary == "uncertain":
        covered.service.settle.assert_not_awaited()
    else:
        covered.service.settle.assert_awaited_once()
    assert len(covered.scope.outstanding) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["uncertain", "failed", "callback", "closed", "healthy"])
async def test_real_adapter_rechecks_bound_send_after_yield(monkeypatch, covered, reason):
    calls = []
    original_client = httpx.AsyncClient
    original_route = adapter.approved_route
    callback = Mock(side_effect=blocked_send if reason == "callback" else None)

    def fresh(route_id):
        route = original_route(route_id)
        if covered.scope.outstanding:
            # Real adapter reattestation occurs AFTER metered yield, BEFORE mark/POST.
            if reason == "uncertain":
                covered.shared.block("synthetic_unknown_ack")
            elif reason == "failed":
                covered.shared.call_failed = True
            elif reason == "closed":
                covered.scope.closed = True
            covered.shared.send_check = callback
            invocation.set(None)
        return route

    async def handle(request):
        calls.append(request)
        assert covered.service.reserve_for_attempt.await_count == 1
        return httpx.Response(200, json=receipt())

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(**kwargs)

    monkeypatch.setattr(adapter, "approved_route", fresh)
    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    if reason == "healthy":
        result = await adapter.embed(
            ["fictional"], route_id=covered.route.route_id, api_key="fictional"
        )
        assert len(result) == len(calls) == 1
        callback.assert_called_once()
        covered.service.settle.assert_awaited_once()
    else:
        expected = ObservationBlocked if reason == "uncertain" else InferenceInterrupted
        with pytest.raises(expected):
            await adapter.embed(["fictional"], route_id=covered.route.route_id, api_key="fictional")
        assert calls == []
        if reason == "uncertain":
            covered.service.settle.assert_not_awaited()
        else:
            assert covered.service.settle.await_args.args[1] == 0
    covered.service.reserve.assert_not_awaited()
