"""Focused #477 repair counterexamples; no DB/provider calls or #539 selection."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks._invocation import InferenceInterrupted, InferenceInvocation, invocation


def covered():
    user = uuid4()
    identity = AttemptIdentity(uuid4(), user, 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    state = InferenceInvocation()
    service = SimpleNamespace(
        reserve=AsyncMock(),
        reserve_for_attempt=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        settle=AsyncMock(return_value=SimpleNamespace(status="settled")),
        resolve=AsyncMock(),
        reconcile_expired_reservations=AsyncMock(),
    )
    scope = runtime.ComputeScope(
        user,
        cast(Any, service),
        scope_id=identity.financial_scope_id,
        inference_identity=identity,
        inference_recovery=cast(Any, object()),
        inference_invocation=state,
    )
    approval = runtime.ToolServiceApproval("synthetic", "search", "synthetic", "call", 10, 7)
    return scope, state, service, approval


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["state", "identity", "recovery"])
async def test_explicit_incomplete_scope_refuses_before_any_service_effect(monkeypatch, missing):
    scope, state, service, _ = covered()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    token = invocation.set(None if missing == "state" else state)
    try:
        with pytest.raises((ObservationBlocked, runtime.ComputeUnavailable)):
            async with runtime.account_compute(
                object(),
                scope.user_id,
                inference_identity=None if missing == "identity" else scope.inference_identity,
                inference_recovery=None if missing == "recovery" else scope.inference_recovery,
            ):
                pytest.fail("incomplete covered scope admitted")
        service.reconcile_expired_reservations.assert_not_awaited()
        service.resolve.assert_not_awaited()
        service.reserve.assert_not_awaited()
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
async def test_direct_scope_without_state_never_reserves_ordinary():
    scope, _, service, _ = covered()
    scope.inference_invocation = None
    with pytest.raises((ObservationBlocked, runtime.ComputeUnavailable)):
        await scope.reserve_inference(10, operation="chat")
    service.reserve.assert_not_awaited()
    service.reserve_for_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_incomplete_scope_cannot_start_settlement():
    scope, _, service, _ = covered()
    scope.inference_invocation = None
    reservation = service.reserve_for_attempt.return_value
    scope.outstanding[reservation.id] = runtime.ReservationHold(reservation, 10)
    with pytest.raises(ObservationBlocked):
        await scope.settle(reservation, 0)
    service.settle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cross", ["account", "scope"])
async def test_crossed_direct_scope_refuses_before_reserve(cross):
    scope, _, service, _ = covered()
    if cross == "account":
        scope.user_id = uuid4()
    else:
        scope.scope_id = uuid4()
    with pytest.raises((ObservationBlocked, runtime.ComputeUnavailable)):
        await scope.reserve_inference(10, operation="chat")
    service.reserve.assert_not_awaited()
    service.reserve_for_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_failure_refuses_tool_before_reservation_without_context(monkeypatch):
    scope, state, service, approval = covered()
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **_: approval)
    state.call_failed = True
    with pytest.raises(InferenceInterrupted):
        async with runtime.metered_tool_call(approval, scope=scope, dispatch_aware=True):
            pytest.fail("tool admitted after known failure")
    service.reserve.assert_not_awaited()
    service.reserve_for_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_tool_qualified_options_and_bound_mark_after_context_reset(monkeypatch):
    scope, state, service, approval = covered()
    scope.expected_period = "2026-10"
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **_: approval)
    async with runtime.metered_tool_call(approval, scope=scope, dispatch_aware=True) as charge:
        service.reserve.assert_not_awaited()
        service.reserve_for_attempt.assert_awaited_once_with(
            scope.inference_identity,
            10,
            operation="chat",
            provider="synthetic",
            route_id="synthetic",
            premium=False,
            extended=False,
            extended_run=False,
            background=False,
            expected_period="2026-10",
        )
        state.call_failed = True
        with pytest.raises(InferenceInterrupted):
            charge.mark_dispatched()
    service.settle.assert_awaited_once()
    assert service.settle.await_args.args[1] == 0


@pytest.mark.asyncio
async def test_failure_during_acquisition_never_yields_but_keeps_known_hold(monkeypatch):
    scope, state, service, approval = covered()
    original = service.reserve_for_attempt.return_value

    async def acquire(*args, **kwargs):
        state.call_failed = True
        return original

    service.reserve_for_attempt.side_effect = acquire
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **_: approval)
    with pytest.raises(InferenceInterrupted):
        async with runtime.metered_tool_call(approval, scope=scope, dispatch_aware=True):
            pytest.fail("yielded after failed sibling")
    service.settle.assert_awaited_once_with(original, 0)


@pytest.mark.asyncio
async def test_parent_bound_state_survives_reset_without_legacy_reconcile(monkeypatch):
    scope, state, service, _ = covered()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    token = runtime._scope.set(scope)
    state.call_failed = True
    try:
        with pytest.raises(InferenceInterrupted):
            async with runtime.account_compute(object(), scope.user_id):
                pytest.fail("nested scope escaped parent's state")
        service.reconcile_expired_reservations.assert_not_awaited()
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
async def test_explicit_conflicting_nested_identity_refused(monkeypatch):
    scope, state, service, _ = covered()
    assert scope.inference_identity is not None
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    parent = runtime._scope.set(scope)
    token = invocation.set(state)
    try:
        with pytest.raises((ObservationBlocked, runtime.ComputeUnavailable)):
            async with runtime.account_compute(
                object(),
                scope.user_id,
                inference_identity=replace(scope.inference_identity, task_id=uuid4()),
            ):
                pytest.fail("identity replaced within bound invocation")
        service.reconcile_expired_reservations.assert_not_awaited()
    finally:
        invocation.reset(token)
        runtime._scope.reset(parent)


@pytest.mark.asyncio
async def test_two_healthy_distinct_tools_then_uncertain_mark_without_context(monkeypatch):
    scope, state, service, approval = covered()
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **_: approval)
    for _ in range(2):
        async with runtime.metered_tool_call(approval, scope=scope, dispatch_aware=True) as charge:
            charge.mark_dispatched()
            charge.confirm()
    assert service.reserve_for_attempt.await_count == 2
    assert [call.args[1] for call in service.settle.await_args_list] == [7, 7]
    with pytest.raises(ObservationBlocked):
        async with runtime.metered_tool_call(approval, scope=scope, dispatch_aware=True) as charge:
            state.block("unknown_commit")
            with pytest.raises(ObservationBlocked):
                charge.mark_dispatched()
            # No secondary accounting effects under uncertainty, including finally.
            with pytest.raises(ObservationBlocked):
                await scope.settle(service.reserve_for_attempt.return_value, 0)
    assert service.settle.await_count == 2


@pytest.mark.asyncio
async def test_conflicting_context_refuses_before_tool_effect(monkeypatch):
    scope, state, service, approval = covered()
    token = invocation.set(InferenceInvocation())
    try:
        with pytest.raises(ObservationBlocked):
            async with runtime.metered_tool_call(approval, scope=scope):
                pytest.fail("cross-invocation tool")
        service.reserve.assert_not_awaited()
        service.reserve_for_attempt.assert_not_awaited()
        assert state.uncertainty is not None
    finally:
        invocation.reset(token)
