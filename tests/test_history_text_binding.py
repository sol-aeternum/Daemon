"""Independent required context and provider-free negative boundaries."""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
from typing import Any, cast

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks._history_binding import required_history
from orchestrator.tasks._invocation import InferenceInvocation, invocation
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_required_root_omitting_every_optional_binding_refuses_before_sql(monkeypatch):
    shared = InferenceInvocation()
    marker = required_history.set(cast(Any, object()))
    current = invocation.set(shared)
    resolve = AsyncMock(side_effect=AssertionError("resolved without binding"))
    monkeypatch.setattr(runtime.EntitlementService, "resolve", resolve)
    try:
        with pytest.raises(ObservationBlocked, match="history_binding_unavailable"):
            async with runtime.account_compute(object(), uuid4()):
                pytest.fail("omitted binding admitted")
        assert shared.uncertainty == "history_binding_unavailable"
        resolve.assert_not_called()
    finally:
        invocation.reset(current)
        required_history.reset(marker)


@pytest.mark.asyncio
@pytest.mark.parametrize("captured,current", [(True, False), (False, True), (True, True)])
async def test_scope_checks_captured_and_current_requirement_before_uncovered_return(
    captured, current
):
    shared = InferenceInvocation()
    marker: Any = object()
    scope = runtime.ComputeScope(uuid4(), cast(Any, SimpleNamespace()))
    scope.history_requirement = marker if captured else None
    token = required_history.set(marker if current else None)
    active = invocation.set(shared)
    try:
        with pytest.raises(ObservationBlocked, match="history_binding_unavailable"):
            scope.require_inference_effect()
        assert shared.uncertainty is not None
    finally:
        invocation.reset(active)
        required_history.reset(token)


@pytest.mark.asyncio
async def test_direct_native_mark_cannot_bypass_required_history():
    shared = InferenceInvocation()
    token = required_history.set(cast(Any, object()))
    try:
        with pytest.raises(ObservationBlocked, match="history_mark_required"):
            await InferenceRecovery(object()).mark(cast(Any, None), uuid4(), shared)
        assert shared.uncertainty == "history_mark_required"
    finally:
        required_history.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["embedding", "tool", "executor", "completion"])
async def test_restricted_boundaries_refuse_before_identity_policy_or_reservation(boundary):
    # Deliberately no optional identity/recovery/binding: restrictions precede
    # inference admission and must never resolve policy/reserve in this case.
    service: Any = SimpleNamespace(resolve=AsyncMock(), reserve=AsyncMock())
    scope = runtime.ComputeScope(uuid4(), service)
    scope.history_requirement = object()
    parent = runtime._scope.set(scope)
    token = required_history.set(scope.history_requirement)
    try:
        with pytest.raises(runtime.ComputeUnavailable, match="unavailable"):
            if boundary == "embedding":
                async with runtime.metered_embedding_call(cast(Any, None), 1):
                    pytest.fail("embedding yielded")
            elif boundary == "tool":
                async with runtime.metered_tool_call(cast(Any, None)):
                    pytest.fail("tool yielded")
            elif boundary == "executor":
                await ToolExecutor(ToolRegistry()).execute_invocation("not_registered", {})
            else:
                await runtime.guarded_completion(tools=[{"type": "function"}])
        service.resolve.assert_not_called()
        service.reserve.assert_not_called()
    finally:
        required_history.reset(token)
        runtime._scope.reset(parent)


def test_ordinary_uncovered_scope_is_unchanged():
    runtime.ComputeScope(uuid4(), cast(Any, SimpleNamespace())).require_inference_effect()


@pytest.mark.asyncio
async def test_captured_requirement_with_lost_current_context_blocks_direct_native_mark(
    monkeypatch,
):
    shared = InferenceInvocation()
    scope = runtime.ComputeScope(uuid4(), cast(Any, SimpleNamespace()))
    scope.history_requirement = object()
    scope.inference_invocation = shared
    owner = runtime._scope.set(scope)
    current = invocation.set(shared)
    recovery = InferenceRecovery(object())
    mark = AsyncMock(side_effect=AssertionError("native owned mark was reached"))
    monkeypatch.setattr(recovery, "_mark_with_stage", mark)
    try:
        assert required_history.get() is None
        with pytest.raises(ObservationBlocked, match="history_binding_unavailable"):
            await recovery.mark(cast(Any, None), uuid4(), shared)
        mark.assert_not_called()
        assert shared.uncertainty is not None
    finally:
        invocation.reset(current)
        runtime._scope.reset(owner)


@pytest.mark.asyncio
async def test_default_native_mark_still_selects_original_owned_seam(monkeypatch):
    recovery = InferenceRecovery(object())
    mark = AsyncMock()
    monkeypatch.setattr(recovery, "_mark_with_stage", mark)
    shared, reservation = InferenceInvocation(), uuid4()
    await recovery.mark(cast(Any, None), reservation, shared)
    mark.assert_awaited_once_with(None, reservation, shared, stage=None)
