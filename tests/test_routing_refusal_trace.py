"""Offline issue #681 trace: real configured shortlist, mocked financial/SDK I/O.

The exception is injected at the same SDK boundary before/after the wire repair;
no table, candidate walk, ledger, settlement or invocation rule is replaced.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import uuid

import pytest
from litellm.exceptions import UnsupportedParamsError

from orchestrator import compute_runtime as runtime, model_routing, routing_log
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.tasks._invocation import InferenceInterrupted, InferenceInvocation
from tests.test_compute_runtime import _account_scope, _qualified_policy, _route
from tests.test_routing_litellm_contract import _load_config

MODELS = (
    "openrouter/z-ai/glm-5.3",
    "openrouter/openai/gpt-6.1-sol",
    "openrouter/anthropic/claude-sonnet-5.5",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("managed,refusals", [(False, 1), (False, 2), (False, 3), (True, 1)])
async def test_real_reasoning_selection_refusal_trace(monkeypatch, managed, refusals):
    routes = []
    for index, model in enumerate(MODELS, 1):
        route = _route(model=model, input_price=index * 1000, output_price=index * 1000)
        route.route_id = f"trace-{index}"
        routes.append(route)
    # Use the actual production model ladder and profile groups. Only approval
    # and prices are synthetic, making the within-group cost order deterministic.
    _qualified_policy(monkeypatch, route=routes, routing=_load_config())
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=8192)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat", "premium_routing"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(side_effect=["trace-hold-1", "trace-hold-2", "trace-hold-3"]),
        reserve_for_attempt=AsyncMock(return_value="trace-hold-1"),
        settle=AsyncMock(),
    )
    failed = UnsupportedParamsError(status_code=500, message="fictional content must not be logged")
    success = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    provider = AsyncMock(side_effect=[failed] * refusals + [success])
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    events: list[tuple[str, dict[str, Any]]] = []
    emit = routing_log.emit

    def record(event, **fields):
        events.append((event, fields))
        emit(event, **fields)

    monkeypatch.setattr(routing_log, "emit", record)
    state = InferenceInvocation()
    recovery = SimpleNamespace(mark=AsyncMock())
    with model_routing.routing_context("reasoning") as selected:
        async with _account_scope(service, auto_route=True) as scope:
            if managed:
                scope.inference_invocation = state
                scope.inference_recovery = cast(runtime.InferenceRecovery, recovery)
                scope.inference_identity = AttemptIdentity(
                    uuid.uuid4(),
                    scope.user_id,
                    0,
                    uuid.uuid4(),
                    1,
                    scope.scope_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                )
            params = {
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 4096,
            }
            if managed:
                with pytest.raises(UnsupportedParamsError) as caught:
                    await runtime.guarded_completion(**params)
                assert caught.value is failed
                assert state.call_failed is True
                assert state.uncertainty is None
                with pytest.raises(InferenceInterrupted):
                    await runtime.guarded_completion(**params)
                recovery.mark.assert_awaited_once()
                caller = "UnsupportedParamsError; subsequent send fenced"
            elif refusals == 3:
                with pytest.raises(runtime.ComputeUnavailable) as caught:
                    await runtime.guarded_completion(**params)
                assert caught.value.code == "capacity_unavailable"
                assert caught.value.message == "Qualified provider unavailable"
                assert caught.value.category == "unsupported_params"
                assert caught.value.status_code is None
                assert caught.value.retryable is False
                caller = "capacity_unavailable/unsupported_params"
            else:
                assert await runtime.guarded_completion(**params) is success
                assert scope.selected_model == MODELS[refusals]
                assert selected.selected_model == MODELS[refusals]
                caller = MODELS[refusals]
            assert not scope.outstanding
    attempted = 1 if managed else min(3, refusals + 1)
    reserve = service.reserve_for_attempt if managed else service.reserve
    assert reserve.await_count == provider.await_count == service.settle.await_count == attempted
    assert [call.kwargs["model"] for call in provider.await_args_list] == list(MODELS[:attempted])
    amounts = []
    for index, call in enumerate(service.settle.await_args_list):
        held = reserve.await_args_list[index].args[1]
        charge = call.args[1]
        if managed or index < refusals:
            assert charge == held
            assert "usage" not in call.kwargs
        else:
            assert charge == routes[index].estimate_microusd(1, 1) < held
            assert call.kwargs["usage"] == {"input_tokens": 1, "output_tokens": 1}
        amounts.append({"held": held, "charged": charge})
    outcomes = [fields for event, fields in events if event == "attempt_outcome"]
    for fields in outcomes[: min(refusals, attempted)]:
        assert fields["failure_category"] == "unsupported_params"
        assert fields["exception_type"] == "UnsupportedParamsError"
        assert fields["status_code"] is None
        assert fields["retryable"] is False
        assert fields["next_action"] == (
            "fallback" if not managed and fields["attempt_index"] < 2 else "raise"
        )
    assert "fictional content" not in json.dumps(events)
    print(
        json.dumps(
            {
                "managed": managed,
                "refusals": refusals,
                "models": list(MODELS[:attempted]),
                "caller": caller,
                "amounts_microusd": amounts,
            }
        )
    )


def test_exception_type_log_field_is_a_closed_vocabulary():
    record = routing_log.build_record(
        "attempt_outcome",
        {"failure_category": "unsupported_params", "exception_type": "UnsupportedParamsError"},
    )
    assert record["exception_type"] == "UnsupportedParamsError"
    unknown = routing_log.build_record(
        "attempt_outcome", {"exception_type": "fictional arbitrary content"}
    )
    assert unknown["exception_type"] == "unrecognized"
