"""Account/protocol boundaries of the private inference dispatch seam."""

from __future__ import annotations

import asyncio
import math
import uuid
import runpy
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
import httpx
from litellm import exceptions as litellm
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import LimitExceeded
from orchestrator.entitlements.errors import (
    AccountSuspended,
    BudgetExceeded,
    ConcurrencyExceeded,
    RateLimitExceeded,
)
from orchestrator.memory.store import MemoryStore


def _route(
    *,
    model: str = "openrouter/reviewed/model",
    input_price: int = 1_000_000,
    output_price: int | None = None,
):
    output_price = input_price if output_price is None else output_price
    return SimpleNamespace(
        route_id="approved-1",
        route_class="routine",
        model=model,
        provider="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        max_context_tokens=32000,
        max_output_tokens=32000,
        supports=lambda *, required_capabilities, input_tokens, output_tokens: (
            required_capabilities <= {"text", "tools", "json_schema"}
            and input_tokens <= 32000
            and output_tokens <= 32000
        ),
        price_ceiling=SimpleNamespace(
            microusd_per_1m_prompt=input_price,
            microusd_per_1m_completion=output_price,
        ),
        transport=SimpleNamespace(provider_only=("reviewed-provider",)),
        is_approved=lambda requirements: True,
        estimate_microusd=lambda prompt, completion: (
            (prompt * input_price + completion * output_price + 999_999) // 1_000_000
        ),
        transport_payload=lambda requirements: {
            "extra_body": {
                "provider": {
                    "only": ["reviewed-provider"],
                    "order": ["reviewed-provider"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                    "max_price": {
                        "prompt": input_price / 1_000_000,
                        "completion": output_price / 1_000_000,
                    },
                }
            }
        },
    )


def routing_document(
    models: list[str],
    *,
    min_output_tokens: int = 1,
    allow_premium: bool = True,
    extra_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An explicit routing config for these fake models.

    The production placements name real models, so a fake route would never be
    routable. Rather than weaken the production shortlist test, each fixture declares
    exactly the fake models it approved and places them in every profile. The floors
    are deliberately tiny so the numeric expectations in these tests are about token
    accounting, not about the workload output budget, which
    ``tests/test_model_routing.py`` exercises with real floors.
    """
    declared = models or ["openrouter/test/placeholder"]
    profiles: list[dict[str, Any]] = [
        {
            "profile": "routine",
            "min_output_tokens": min_output_tokens,
            "allow_premium": False,
            "groups": [{"group": "cheap", "models": list(declared)}],
        },
        {
            "profile": "background",
            "min_output_tokens": min_output_tokens,
            "allow_premium": False,
            "groups": [{"group": "cheap", "models": list(declared)}],
        },
        {
            "profile": "reasoning",
            "min_output_tokens": min_output_tokens,
            "allow_premium": allow_premium,
            "groups": [{"group": "demanding", "models": list(declared)}],
        },
        {
            "profile": "research",
            "min_output_tokens": min_output_tokens,
            "allow_premium": allow_premium,
            "groups": [{"group": "demanding", "models": list(declared)}],
        },
        {
            "profile": "council",
            "min_output_tokens": min_output_tokens,
            "allow_premium": allow_premium,
            "diversity": {"distinct_developers": True},
            "groups": [{"group": "diverse", "models": list(declared)}],
        },
    ]
    profiles.extend(extra_profiles or [])
    return {
        "version": 1,
        "provisional": True,
        "models": [
            {
                "model": model,
                "developer": model_routing.developer_for_model(model),
                "suitability": sorted(model_routing.ROUTING_PROFILES),
                "reasoning_efforts": sorted(model_routing.KNOWN_REASONING_EFFORTS),
                "sampling_parameters": sorted(model_routing.SAMPLING_PARAMETERS),
                "parameter_presets": {
                    "default": {"reasoning_effort": "low"},
                    "council": {"reasoning_effort": "high", "include_reasoning": True},
                },
            }
            for model in declared
        ],
        "profiles": profiles,
    }


def install_routing(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> None:
    """Point the routing module at an explicit test config."""
    parsed = model_routing.parse_model_routing(document, source_path="<test>")
    monkeypatch.setattr(model_routing, "load_model_routing", lambda *args, **kwargs: parsed)


def _qualified_policy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    route=None,
    routing: dict[str, Any] | None = None,
) -> None:
    entries = route if isinstance(route, list) else [route] if route else []
    routes = {entry.route_id: entry for entry in entries}
    monkeypatch.setattr(
        runtime,
        "load_inference_policy",
        lambda: SimpleNamespace(routes=routes, requirements=object()),
    )
    install_routing(
        monkeypatch, routing or routing_document(sorted({entry.model for entry in entries}))
    )


def test_no_qualified_route_refuses_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualified_policy(monkeypatch)
    with pytest.raises(runtime.ComputeUnavailable, match="Approved inference route unavailable"):
        runtime.choose_route()


@pytest.mark.asyncio
async def test_tool_context_allowance_reserves_answer_and_counts_unicode_serialization(monkeypatch):
    from orchestrator.tools.context_budget import tool_context_budget

    route = _route(input_price=0)
    _qualified_policy(monkeypatch, route=route)
    limits = SimpleNamespace(max_context_tokens=2048, max_output_tokens=128)
    policy = SimpleNamespace(
        capabilities={"chat"},
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 100000,
    )
    service = SimpleNamespace(resolve=AsyncMock(return_value=policy), reserve=AsyncMock())
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    base = [{"role": "user", "content": "Read a source"}]
    try:
        budget = await tool_context_budget({"model": route.model, "messages": base}, base)
        assert budget is not None
        assert budget.output_tokens == 128
        assert budget.fits([*base, {"role": "tool", "content": "x" * 2000}])
        assert not budget.fits([*base, {"role": "tool", "content": "界" * 2000}])
        assert not budget.fits([*base, {"role": "tool", "content": "\\" * 4000}])
        service.reserve.assert_not_awaited()  # The allowance never admits paid work.
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
async def test_tool_context_allowance_does_not_switch_to_a_larger_route_to_fit_evidence(
    monkeypatch,
):
    from orchestrator.tools.context_budget import tool_context_budget

    small = _route(model="openrouter/reviewed/small", input_price=1)
    small.route_id = "small"
    small.max_context_tokens = 1024
    large = _route(model="openrouter/reviewed/large", input_price=10000)
    large.route_id = "large"
    _qualified_policy(monkeypatch, route=[small, large])
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    policy = SimpleNamespace(
        capabilities={"chat"},
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 100000,
    )
    service = SimpleNamespace(resolve=AsyncMock(return_value=policy))
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    base = [{"role": "user", "content": "Read a source"}]
    try:
        budget = await tool_context_budget({"model": small.model, "messages": base}, base)
        assert budget is not None and budget.route_id == "small"
        assert budget.fits(base)
        assert not budget.fits([*base, {"role": "tool", "content": "x" * 4000}])
    finally:
        runtime._scope.reset(token)


def test_auto_routing_does_not_promote_a_premium_only_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    route.route_class = "premium"
    _qualified_policy(monkeypatch, route=route)
    with pytest.raises(runtime.ComputeUnavailable):
        runtime.choose_route()
    assert runtime.choose_route(route.model).route_class == "premium"


@pytest.mark.asyncio
async def test_explicit_premium_requires_premium_routing_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    route.route_class = "premium"
    _qualified_policy(monkeypatch, route=route)
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(),
    )
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=route.model, messages=[{"role": "user", "content": "hi"}]
            )
    finally:
        runtime._scope.reset(token)
    assert denied.value.code == "capability_unavailable"
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_council_reasoning_round_uses_guarded_approved_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.council.engine import _call_model

    route = _route(input_price=1000)
    route.max_output_tokens = 64
    _qualified_policy(monkeypatch, route=route)
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(return_value="council-hold"),
        settle=AsyncMock(),
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="Council answer", reasoning="Why"))
        ],
        usage=None,
        model=route.model,
    )
    provider = AsyncMock(return_value=response)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        role, content, error, reasoning, _, actual_model = await _call_model(
            role="auditor",
            model=route.model,
            prompt="Question",
            system_prompt="Deliberate",
            timeout_s=10,
        )
    finally:
        runtime._scope.reset(token)

    assert (role, content, error, reasoning, actual_model) == (
        "auditor",
        "Council answer",
        None,
        "Why",
        route.model,
    )
    provider.assert_awaited_once()
    assert provider.await_args is not None
    call = provider.await_args.kwargs
    assert call["reasoning_effort"] == "high"
    assert call["include_reasoning"] is True
    assert call["max_tokens"] == 64
    assert call["num_retries"] == 0
    assert call["extra_body"]["provider"]["allow_fallbacks"] is False
    service.reserve.assert_awaited_once()
    service.settle.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe",
    [
        {"reasoning_effort": "arbitrary"},
        {"reasoning_effort": None},
        {"reasoning_effort": True},
        {"reasoning_effort": ["medium"]},
        {"include_reasoning": "true"},
        {"include_reasoning": 1},
        {"include_reasoning": None},
        {"reasoning": {"effort": "high"}},
    ],
)
async def test_unsafe_reasoning_options_never_reserve_or_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    unsafe: dict[str, Any],
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(capabilities={"chat"})), reserve=AsyncMock()
    )
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[{"role": "user", "content": "hi"}],
                **unsafe,
            )
    finally:
        runtime._scope.reset(token)
    assert denied.value.code == "capacity_unavailable"
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_funded_allowance_still_admits_qualified_zero_cost_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    funded = _route(model="openrouter/test/funded", input_price=100000)
    zero = _route(model="openrouter/test/zero", input_price=0)
    funded.route_id = "funded"
    zero.route_id = "zero"
    _qualified_policy(monkeypatch, route=[funded, zero])
    account = uuid.uuid4()
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=256)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 0,
            )
        ),
        reserve=AsyncMock(return_value="zero-hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(account, cast(EntitlementService, service), auto_route=True)
    )
    try:
        await runtime.guarded_completion(
            model="openrouter/test/funded", messages=[{"role": "user", "content": "hi"}]
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_args.args[1] == 0
    assert service.reserve.await_args.kwargs["model"] == "openrouter/test/zero"
    service.settle.assert_awaited_once_with("zero-hold", 0, usage={"estimated_cost": True})
    assert provider.await_args is not None
    assert provider.await_args.kwargs["model"] == "openrouter/test/zero"


@pytest.mark.asyncio
async def test_auto_route_price_weights_actual_request_and_falls_back_after_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap_input = _route(
        model="openrouter/test/cheap-input", input_price=1000, output_price=1_000_000
    )
    cheap_output = _route(
        model="openrouter/test/cheap-output", input_price=500000, output_price=1000
    )
    cheap_input.route_id = "cheap-input"
    cheap_output.route_id = "cheap-output"
    _qualified_policy(monkeypatch, route=[cheap_input, cheap_output])
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(side_effect=["first", "second"]),
        settle=AsyncMock(),
    )
    provider = AsyncMock(side_effect=[RuntimeError("unavailable"), {"choices": []}])
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        await runtime.guarded_completion(
            model="auto",
            messages=[{"role": "user", "content": "a" * 2000}],
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_count == 2
    assert service.reserve.await_args_list[0].kwargs["model"] == "openrouter/test/cheap-input"
    assert service.reserve.await_args_list[1].kwargs["model"] == "openrouter/test/cheap-output"
    assert service.settle.await_args_list[0].args == (
        "first",
        service.reserve.await_args_list[0].args[1],
    )
    assert service.settle.await_args_list[1].args == (
        "second",
        service.reserve.await_args_list[1].args[1],
    )


@pytest.mark.asyncio
async def test_automatic_walk_still_serves_the_next_candidate_after_an_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = _route(model="openrouter/test/auth-cheap", input_price=1000, output_price=1000)
    dearer = _route(model="openrouter/test/auth-dearer", input_price=5000, output_price=5000)
    cheap.route_id = "auth-cheap"
    dearer.route_id = "auth-dearer"
    _qualified_policy(monkeypatch, route=[cheap, dearer])
    service = _chat_service()
    service.reserve = AsyncMock(side_effect=["auth-hold-a", "auth-hold-b"])
    provider = AsyncMock(side_effect=[_status_error(401), {"choices": []}])
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        result = await runtime.guarded_completion(
            model="auto",
            messages=[{"role": "user", "content": "hi"}],
        )
    # The walk rules are unchanged: a refused candidate returns normally from
    # the next approved candidate, each attempt on its own settled hold.
    assert result == {"choices": []}
    assert service.reserve.await_count == 2
    assert service.settle.await_count == 2
    assert provider.await_args_list[0].kwargs["model"] == "openrouter/test/auth-cheap"
    assert provider.await_args_list[1].kwargs["model"] == "openrouter/test/auth-dearer"
    assert service.settle.await_args_list[0].args == (
        "auth-hold-a",
        service.reserve.await_args_list[0].args[1],
    )
    assert service.settle.await_args_list[1].args == (
        "auth-hold-b",
        service.reserve.await_args_list[1].args[1],
    )


@pytest.mark.asyncio
async def test_automatic_walk_exhausted_by_auth_failures_carries_the_terminal_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = _route(model="openrouter/test/auth-cheap", input_price=1000, output_price=1000)
    dearer = _route(model="openrouter/test/auth-dearer", input_price=5000, output_price=5000)
    cheap.route_id = "auth-cheap"
    dearer.route_id = "auth-dearer"
    _qualified_policy(monkeypatch, route=[cheap, dearer])
    service = _chat_service()
    service.reserve = AsyncMock(side_effect=["auth-hold-a", "auth-hold-b"])
    provider = AsyncMock(side_effect=_status_error(401))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model="auto",
                messages=[{"role": "user", "content": "hi"}],
            )
    # Every candidate was still tried, each on its own hold settled at bound...
    assert service.reserve.await_count == 2
    assert service.settle.await_count == 2
    assert service.settle.await_args_list[0].args == (
        "auth-hold-a",
        service.reserve.await_args_list[0].args[1],
    )
    assert service.settle.await_args_list[1].args == (
        "auth-hold-b",
        service.reserve.await_args_list[1].args[1],
    )
    # ...and the surfaced error is the last failure's typed verdict instead of
    # a bare capacity message that invites retrying an authentication failure.
    assert denied.value.code == "capacity_unavailable"
    assert denied.value.message == "Qualified provider unavailable"
    assert denied.value.category == "authentication_failed"
    assert denied.value.status_code == 401
    assert denied.value.retryable is False
    assert denied.value.retry_after_seconds is None


@pytest.mark.asyncio
async def test_sole_automatic_candidate_auth_failure_carries_the_terminal_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route(model="openrouter/test/only-auth"))
    service = _chat_service()
    provider = AsyncMock(side_effect=_status_error(401))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model="auto",
                messages=[{"role": "user", "content": "hi"}],
            )
    # One candidate means one dispatch; no second hold is ever taken.
    assert service.reserve.await_count == 1
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )
    assert denied.value.code == "capacity_unavailable"
    assert denied.value.message == "Qualified provider unavailable"
    assert denied.value.category == "authentication_failed"
    assert denied.value.status_code == 401
    assert denied.value.retryable is False


@pytest.mark.asyncio
async def test_automatic_stream_walk_after_a_pre_output_failure_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = _route(model="openrouter/test/stream-cheap", input_price=1000, output_price=1000)
    dearer = _route(model="openrouter/test/stream-dearer", input_price=5000, output_price=5000)
    cheap.route_id = "stream-cheap"
    dearer.route_id = "stream-dearer"
    _qualified_policy(monkeypatch, route=[cheap, dearer])
    service = _chat_service()
    service.reserve = AsyncMock(side_effect=["stream-hold-a", "stream-hold-b"])

    async def failing_chunks() -> Any:
        raise _status_error(401)
        yield {}  # pragma: no cover - unreachable; marks this an async generator

    async def ok_chunks() -> Any:
        yield {"text": "recovered"}

    provider = AsyncMock(side_effect=[failing_chunks(), ok_chunks()])
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        stream = await runtime.guarded_completion(
            model="auto",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        received = [chunk async for chunk in stream]
    # Current stream behaviour, pinned: a pre-output failure still walks to the
    # next candidate, the refused stream is closed and settled at its bound,
    # and the surviving candidate serves the request on its own hold.
    assert received == [{"text": "recovered"}]
    assert service.reserve.await_count == 2
    assert service.settle.await_count == 2
    assert service.settle.await_args_list[0].args == (
        "stream-hold-a",
        service.reserve.await_args_list[0].args[1],
    )
    assert service.settle.await_args_list[1].args == (
        "stream-hold-b",
        service.reserve.await_args_list[1].args[1],
    )


@pytest.mark.asyncio
async def test_tool_request_skips_cheaper_text_only_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_only = _route(model="openrouter/test/text", input_price=0)
    text_only.route_id = "text"
    text_only.supports = lambda *, required_capabilities, input_tokens, output_tokens: (
        required_capabilities <= {"text"}
    )
    tools_route = _route(model="openrouter/test/tools", input_price=1000)
    tools_route.route_id = "tools"
    tools_route.max_output_tokens = 128
    _qualified_policy(
        monkeypatch,
        route=[text_only, tools_route],
        routing=routing_document([text_only.model, tools_route.model], min_output_tokens=32),
    )
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(return_value="tool-hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        await runtime.guarded_completion(
            model="auto",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "clock", "parameters": {}}}],
        )
    finally:
        runtime._scope.reset(token)
    assert provider.await_args is not None
    assert provider.await_args.kwargs["model"] == "openrouter/test/tools"
    assert provider.await_args.kwargs["max_tokens"] == 128


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account_context", "account_output", "expected"),
    [
        # A route capped below the account target still serves, at its own cap.
        (32000, 128, 64),
        # An uncapped (paid) account is bounded by the route alone.
        (None, None, 64),
        # An account target below the route cap still wins.
        (32000, 48, 48),
    ],
)
async def test_automatic_output_clamps_to_the_route_and_account(
    monkeypatch: pytest.MonkeyPatch,
    account_context: int | None,
    account_output: int | None,
    expected: int,
) -> None:
    route = _route(model="openrouter/test/capped")
    route.route_id = "capped"
    route.max_output_tokens = 64
    _qualified_policy(
        monkeypatch,
        route=route,
        routing=routing_document([route.model], min_output_tokens=32),
    )
    limits = SimpleNamespace(max_context_tokens=account_context, max_output_tokens=account_output)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(return_value="capped-hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        await runtime.guarded_completion(model="auto", messages=[{"role": "user", "content": "hi"}])
    finally:
        runtime._scope.reset(token)
    assert provider.await_args is not None
    assert provider.await_args.kwargs["max_tokens"] == expected


@pytest.mark.asyncio
async def test_public_model_lists_offer_auto_and_only_approved_selectable_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator import main
    from orchestrator import models_cache

    approved = _route(model="openrouter/openai/gpt-5.2")
    _qualified_policy(monkeypatch, route=approved)
    monkeypatch.setattr(main, "load_inference_policy", runtime.load_inference_policy)
    monkeypatch.setattr(
        main,
        "fetch_openrouter_models",
        AsyncMock(
            return_value=[
                {"id": "openai/gpt-5.2"},
                {"id": "unapproved/private"},
            ]
        ),
    )
    monkeypatch.setattr(models_cache, "get_cached_models", lambda: [])
    listed = await main.openai_list_models(main.get_settings())
    assert {model.id for model in listed.data} == {"auto", approved.model}
    catalog = await main.get_model_catalog()
    assert catalog["auto"]["id"] == "auto"
    assert [model["id"] for model in catalog["featured"]] == [approved.model]


@pytest.mark.asyncio
async def test_entitlement_snapshot_uses_authenticated_account_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.auth import AuthenticatedDevice
    from orchestrator.db import AppState
    from orchestrator.routes import entitlements

    account = uuid.uuid4()
    resolver = SimpleNamespace(public_snapshot=AsyncMock(return_value={"plan": "free"}))
    monkeypatch.setattr(entitlements, "EntitlementService", lambda pool: resolver)
    result = await entitlements.get_entitlements(
        AuthenticatedDevice(account, uuid.uuid4(), uuid.uuid4()),
        cast(AppState, SimpleNamespace(db_pool=object())),
    )
    assert result["plan"] == "free"
    resolver.public_snapshot.assert_awaited_once_with(account)


@pytest.mark.asyncio
async def test_explicit_unapproved_model_never_reserves_or_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=False,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=4096),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=4096
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(),
        settle=AsyncMock(),
    )
    account = uuid.uuid4()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(runtime.ComputeScope(account, cast(EntitlementService, service)))
    try:
        with pytest.raises(runtime.ComputeUnavailable):
            await runtime.guarded_completion(
                model="openrouter/unreviewed/model", messages=[{"role": "user", "content": "hello"}]
            )
    finally:
        runtime._scope.reset(token)
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_cannot_override_approved_provider_privacy_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(capabilities={"chat"})), reserve=AsyncMock()
    )
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        with pytest.raises(runtime.ComputeUnavailable, match="Unsupported completion parameters"):
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[{"role": "user", "content": "hi"}],
                extra_body={"provider": {"allow_fallbacks": True}},
            )
    finally:
        runtime._scope.reset(token)
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_tiny_remote_image_url_cannot_evade_text_cost_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    account = uuid.uuid4()
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=True,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=4096),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=4096
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(),
        settle=AsyncMock(),
    )
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(runtime.ComputeScope(account, cast(EntitlementService, service)))
    try:
        with pytest.raises(runtime.ComputeUnavailable, match="Multimodal compute unavailable"):
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://example.org/i"}},
                        ],
                    }
                ],
            )
    finally:
        runtime._scope.reset(token)
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_reserves_by_account_and_settles_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    account = uuid.uuid4()
    reservation = object()
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=False,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=2048),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=2048
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value=reservation),
        settle=AsyncMock(),
    )

    async def chunks():
        yield {"text": "first"}
        yield {"text": "second"}

    provider = AsyncMock(return_value=chunks())
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(runtime.ComputeScope(account, cast(EntitlementService, service)))
    try:
        stream = await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        assert await anext(stream) == {"text": "first"}
        await stream.aclose()
    finally:
        runtime._scope.reset(token)
    args, kwargs = service.reserve.await_args
    assert args[0] == account
    assert args[1] > 0
    assert kwargs["route_id"] == "approved-1"
    service.settle.assert_awaited_once_with(reservation, args[1], usage={"estimated_cost": True})
    assert provider.await_args is not None
    provider_kwargs = provider.await_args.kwargs
    assert provider_kwargs["extra_body"]["provider"]["only"] == ["reviewed-provider"]
    assert provider_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False
    assert provider_kwargs["extra_body"]["provider"]["zdr"] is True
    assert provider_kwargs["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_zero_cost_route_keeps_concurrency_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route(input_price=0))
    account = uuid.uuid4()
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=True,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=4096),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=4096
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="reservation"),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value={"choices": []}))
    token = runtime._scope.set(runtime.ComputeScope(account, cast(EntitlementService, service)))
    try:
        await runtime.guarded_completion(
            model="openrouter/reviewed/model", messages=[{"role": "user", "content": "hi"}]
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_args.args[:2] == (account, 0)
    service.settle.assert_awaited_once_with("reservation", 0, usage={"estimated_cost": True})


@pytest.mark.asyncio
async def test_missing_owner_scope_denies_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    with pytest.raises(runtime.ComputeUnavailable, match="Account compute unavailable"):
        await runtime.guarded_completion(model="openrouter/reviewed/model", messages=[])
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_unstarted_stream_is_charged_at_scope_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualified_policy(monkeypatch, route=_route())
    account = uuid.uuid4()
    reservation = object()
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=False,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=128),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=128
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value=reservation),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    service.reconcile_expired_reservations = AsyncMock(return_value=0)

    async def chunks():
        yield {"text": "never started"}

    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value=chunks()))
    async with runtime.account_compute(object(), account):
        _unused = await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        service.settle.assert_not_awaited()
    bound = service.reserve.await_args.args[1]
    service.settle.assert_awaited_once_with(reservation, bound)


@pytest.mark.asyncio
async def test_stream_total_deadline_closes_full_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    settings = SimpleNamespace(
        request_timeout_s=0.02,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="stream-hold"),
        settle=AsyncMock(),
    )

    async def chunks():
        yield {"text": "first"}
        await asyncio.sleep(0.1)
        yield {"text": "second"}

    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value=chunks()))
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        stream = await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        assert await anext(stream) == {"text": "first"}
        with pytest.raises(runtime.ComputeUnavailable, match="Qualified provider unavailable"):
            await anext(stream)
    finally:
        runtime._scope.reset(token)
    service.settle.assert_awaited_once_with(
        "stream-hold",
        service.reserve.await_args.args[1],
        usage={"estimated_cost": True},
    )


@pytest.mark.asyncio
async def test_validated_provider_usage_settles_below_held_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    account = uuid.uuid4()
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                byok_enabled=False,
                limits=SimpleNamespace(max_context_tokens=32000, max_output_tokens=4096),
                limits_for=lambda premium: SimpleNamespace(
                    max_context_tokens=32000, max_output_tokens=4096
                ),
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="hold"),
        settle=AsyncMock(),
    )
    provider_response = {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value=provider_response))
    token = runtime._scope.set(runtime.ComputeScope(account, cast(EntitlementService, service)))
    try:
        await runtime.guarded_completion(
            model="openrouter/reviewed/model", messages=[{"role": "user", "content": "hi"}]
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_args.args[1] > 5
    service.settle.assert_awaited_once_with(
        "hold", 5, usage={"input_tokens": 2, "output_tokens": 3}
    )


@pytest.mark.asyncio
async def test_reported_overrun_is_passed_to_ledger_not_capped_to_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=2)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="overrun-hold"),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(
        runtime.litellm,
        "acompletion",
        AsyncMock(
            return_value={
                "choices": [],
                "usage": {"prompt_tokens": 900, "completion_tokens": 3},
            }
        ),
    )
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_args.args[1] < 903
    service.settle.assert_awaited_once_with(
        "overrun-hold",
        903,
        usage={"input_tokens": 900, "output_tokens": 3},
    )


@pytest.mark.asyncio
async def test_scope_recovers_old_holds_using_configured_timeout_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    service = SimpleNamespace(reconcile_expired_reservations=AsyncMock(return_value=1))
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    now = datetime.now(timezone.utc)
    account = uuid.uuid4()
    async with runtime.account_compute(object(), account):
        assert runtime.current_scope().user_id == account
    args, kwargs = service.reconcile_expired_reservations.await_args
    assert args == (account,)
    timeout = runtime.get_settings().request_timeout_s
    assert abs((now - kwargs["before"]).total_seconds() - 2 * timeout) < 3


@pytest.mark.asyncio
async def test_extended_root_consumes_one_unit_then_exhausted_next_root_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route(input_price=0))
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    entitled = SimpleNamespace(
        capabilities={"chat", "extended_agents"},
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 0,
    )
    exhausted = SimpleNamespace(
        capabilities={"chat"},
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 0,
    )
    service = SimpleNamespace(
        reconcile_expired_reservations=AsyncMock(return_value=0),
        resolve=AsyncMock(side_effect=[entitled, entitled, exhausted, exhausted]),
        reserve=AsyncMock(side_effect=["first", "second"]),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    account = uuid.uuid4()
    async with runtime.account_compute(object(), account, operation="agent", extended=True):
        for _ in range(2):
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[{"role": "user", "content": "hi"}],
            )
    # Every call of the run is charged to the extended budget; only the first
    # takes the extended-run slot, and both share the scope's concurrency slot.
    reserves = service.reserve.await_args_list
    assert [call.kwargs["extended"] for call in reserves] == [True, True]
    assert [call.kwargs["extended_run"] for call in reserves] == [True, False]
    assert reserves[0].kwargs["scope_id"] == reserves[1].kwargs["scope_id"]
    assert all(call.kwargs["premium"] for call in service.reserve.await_args_list)
    assert provider.await_count == 2
    with pytest.raises(runtime.ComputeUnavailable, match="normal chat is still available"):
        async with runtime.account_compute(object(), account, operation="agent", extended=True):
            pass
    assert service.reserve.await_count == 2


@pytest.mark.asyncio
async def test_background_title_job_uses_persisted_conversation_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Load the job directly while an unrelated pre-existing worker.py import
    # mismatch prevents normal package collection.
    jobs = runpy.run_path(str(Path(__file__).resolve().parents[1] / "orchestrator/worker/jobs.py"))
    title_job = jobs["generate_conversation_title_job"]
    owner, conversation_id = uuid.uuid4(), uuid.uuid4()
    pool = object()

    class Store:
        get_conversation = AsyncMock(return_value={"user_id": owner, "title_locked": False})
        get_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
        save_generated_conversation_title = AsyncMock(return_value=True)

    @asynccontextmanager
    async def checked_scope(
        scope_pool, scope_user_id, *, operation, auto_route, background, profile
    ):
        assert scope_pool is pool
        assert scope_user_id == owner
        assert operation == "agent" and auto_route is True
        # Worker jobs never take the interactive rate or concurrency slots.
        assert background is True
        assert profile == "background"
        yield None

    globals_map = title_job.__globals__
    monkeypatch.setitem(globals_map, "MemoryStore", Store)
    monkeypatch.setitem(globals_map, "account_compute", checked_scope)
    generator = AsyncMock(return_value="Greeting")
    monkeypatch.setitem(globals_map, "generate_conversation_title", generator)
    result = await title_job({"store": Store(), "db_pool": pool}, conversation_id)
    assert result == {"status": "ok", "title": "Greeting"}
    generator.assert_awaited_once()
    Store.save_generated_conversation_title.assert_awaited_once_with(
        conversation_id, title="Greeting", expected_title=None
    )


@pytest.mark.asyncio
async def test_stream_keepalive_task_switch_preserves_authenticated_compute_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator import main

    _qualified_policy(monkeypatch, route=_route(input_price=0))
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    owner = uuid.uuid4()
    service = SimpleNamespace(
        reconcile_expired_reservations=AsyncMock(return_value=0),
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 0,
            )
        ),
        reserve=AsyncMock(return_value=uuid.uuid4()),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)

    async def source():
        yield "first"
        assert runtime.current_scope().user_id == owner
        await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
        )
        yield "second"

    stream = main._account_frames(object(), owner, source)

    async def next_frame() -> str:
        return await anext(stream)

    assert await asyncio.create_task(next_frame()) == "first"
    assert await asyncio.create_task(next_frame()) == "second"
    with pytest.raises(StopAsyncIteration):
        await asyncio.create_task(next_frame())
    provider.assert_awaited_once()
    service.settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_unapproved_embedding_uses_account_scoped_lexical_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import retrieval
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(query: str, **kwargs: Any) -> list[float]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", unavailable)
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    store = SimpleNamespace(
        search_memories=AsyncMock(),
        search_memories_bm25=AsyncMock(
            return_value=[
                {
                    "id": memory_id,
                    "content": "A local private memory",
                    "bm25_score": 1.0,
                }
            ]
        ),
        bulk_touch_memories=AsyncMock(),
    )
    memories = await retrieval.retrieve_memories_for_text(
        cast(MemoryStore, store),
        "local private",
        user_id=user_id,
        limit=2,
    )
    assert [memory["id"] for memory in memories] == [memory_id]
    assert store.search_memories_bm25.await_args.kwargs["user_id"] == user_id
    store.search_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_memory_write_remains_local_when_embeddings_unapproved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import dedup
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(dedup, "embed_documents_with_metadata", unavailable)
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    store = SimpleNamespace(
        _insert_memory_with_outcome=AsyncMock(return_value=({"id": memory_id}, True)),
        _discover_equivalence_candidates=AsyncMock(return_value=[]),
    )
    _install_memory_mutation_context(store)
    actual = await dedup.dedup_and_store(
        cast(MemoryStore, store),
        user_id,
        "explicit preference",
        "user_created",
        "preference",
    )
    assert actual == memory_id
    assert store._insert_memory_with_outcome.await_args.kwargs["user_id"] == user_id
    assert store._insert_memory_with_outcome.await_args.kwargs["embedding"] is None


def _install_memory_mutation_context(store: Any) -> None:
    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def acquire():
        yield SimpleNamespace(transaction=transaction)

    store._pool = SimpleNamespace(acquire=acquire, execute=AsyncMock())


@pytest.mark.asyncio
async def test_extracted_fact_without_embeddings_persists_and_never_supersedes_slot_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import dedup
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(dedup, "embed_documents_with_metadata", unavailable)
    new_id = uuid.uuid4()
    existing_id = uuid.uuid4()
    store = SimpleNamespace(
        search_memories_bm25=AsyncMock(
            return_value=[
                {
                    "id": existing_id,
                    "content": "Another employer",
                    "memory_slot": "work.current",
                    "category": "career",
                    "source_type": "extracted",
                    "status": "active",
                    "valid_to": None,
                }
            ]
        ),
        _insert_memory_with_outcome=AsyncMock(return_value=({"id": new_id}, True)),
        _discover_equivalence_candidates=AsyncMock(return_value=[]),
        _pool=SimpleNamespace(execute=AsyncMock()),
    )
    _install_memory_mutation_context(store)
    fact = SimpleNamespace(
        content="User works at NewCo", category="career", confidence=0.9, slot="work.current"
    )
    result = await dedup.deduplicate_facts(
        cast(MemoryStore, store),
        uuid.uuid4(),
        [fact],
        uuid.uuid4(),
    )
    assert [memory["id"] for memory in result.new] == [new_id]
    assert result.superseded == []
    assert store._insert_memory_with_outcome.await_args.kwargs["embedding"] is None
    store._pool.execute.assert_not_awaited()
    store._discover_equivalence_candidates.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_lexical_memory_is_merged_without_new_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import dedup
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(dedup, "embed_documents_with_metadata", unavailable)
    existing_id = uuid.uuid4()
    existing = {
        "id": existing_id,
        "content": "User prefers tea",
        "memory_slot": None,
        "category": "preference",
        "source_type": "extracted",
        "status": "active",
        "valid_to": None,
    }
    store = SimpleNamespace(
        _discover_equivalence_candidates=AsyncMock(return_value=[]),
        _insert_memory_with_outcome=AsyncMock(return_value=(existing, False)),
    )
    _install_memory_mutation_context(store)
    result = await dedup.dedup_and_store(
        cast(MemoryStore, store),
        uuid.uuid4(),
        "User prefers tea",
        "extracted",
        "preference",
    )
    assert result == existing_id
    # Reliable SQL conflict disposition, not a lexical equivalence assumption.
    store._insert_memory_with_outcome.assert_awaited_once()


@pytest.mark.asyncio
async def test_skill_projection_remains_storable_without_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator import skills_projection
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(skills_projection, "embed_documents", unavailable)
    assert await skills_projection.embed_skill_content("name", "description", "content") is None
    pool = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"skill_id": "local", "embedding": None})
    )
    store = skills_projection.SkillProjectionStore(cast(Any, pool))
    await store.upsert_projection(
        skill_id="local",
        name="name",
        description="description",
        source_file_path="local.md",
        source_hash="hash",
        embedding=None,
    )
    assert pool.fetchrow.await_args.args[10] is None


@pytest.mark.asyncio
async def test_reembed_endpoint_fails_cleanly_without_approved_embedding_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from orchestrator.auth import AuthenticatedDevice
    from orchestrator.db import AppState
    from orchestrator.routes import memories
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    async def unavailable(texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("No approved embedding route")

    monkeypatch.setattr(memories, "embed_documents_with_metadata", unavailable)
    account, memory_id = uuid.uuid4(), uuid.uuid4()
    store = SimpleNamespace(
        get_memory=AsyncMock(
            return_value={
                "id": memory_id,
                "user_id": account,
                "content": "cloud",
                "local_only": False,
            }
        ),
        update_memory_embedding=AsyncMock(),
    )
    with pytest.raises(HTTPException) as exc:
        await memories.reembed_memories(
            memories.MemoryReembedRequest(memory_ids=[memory_id]),
            cast(AppState, SimpleNamespace(memory_store=store)),
            AuthenticatedDevice(account, uuid.uuid4(), uuid.uuid4()),
        )
    assert exc.value.status_code == 503
    assert cast(dict[str, str], exc.value.detail)["code"] == "route_unavailable"
    store.update_memory_embedding.assert_not_awaited()


def _funded_service(
    *, max_context_tokens: int | None = 32000, max_output_tokens: int | None = 128, **extra
):
    limits = SimpleNamespace(
        max_context_tokens=max_context_tokens, max_output_tokens=max_output_tokens
    )
    return SimpleNamespace(
        reconcile_expired_reservations=AsyncMock(return_value=0),
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000_000,
            )
        ),
        reserve=AsyncMock(return_value="hold"),
        settle=AsyncMock(),
        **extra,
    )


@pytest.mark.asyncio
async def test_account_frames_closing_mid_stream_does_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator import main

    service = _funded_service()
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)

    async def source():
        for index in range(10):
            yield f"frame-{index}"

    stream = main._account_frames(object(), uuid.uuid4(), source)
    assert await anext(stream) == "frame-0"
    # Let the producer fill the one-slot queue and block on the next put.
    await asyncio.sleep(0.01)
    # Not wait_for: its cancellation would be swallowed by the consumer's
    # cleanup and hide a producer stuck forever on the full queue.
    closing = asyncio.create_task(cast(AsyncGenerator[str, None], stream).aclose())
    done, _ = await asyncio.wait({closing}, timeout=1)
    if closing not in done:
        closing.cancel()
    assert closing in done


@pytest.mark.asyncio
async def test_stream_requests_usage_and_settles_at_reported_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = _funded_service(max_output_tokens=4096)

    async def chunks():
        yield {"choices": [{"delta": {"content": "hi"}}]}
        yield {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    provider = AsyncMock(return_value=chunks())
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        stream = await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            stream_options={"include_usage": False},
        )
        assert len([chunk async for chunk in stream]) == 2
    finally:
        runtime._scope.reset(token)
    assert provider.await_args is not None
    assert provider.await_args.kwargs["stream_options"] == {"include_usage": True}
    service.settle.assert_awaited_once_with(
        "hold", 15, usage={"input_tokens": 10, "output_tokens": 5}
    )


@pytest.mark.asyncio
async def test_slow_consumer_near_deadline_is_not_a_silent_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    settings = SimpleNamespace(
        request_timeout_s=0.05,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    service = _funded_service()

    async def chunks():
        yield {"text": "first"}
        yield {"text": "second"}

    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(return_value=chunks()))
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        stream = await runtime.guarded_completion(
            model="openrouter/reviewed/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        assert await anext(stream) == {"text": "first"}
        # The consumer, not the provider, is slow: the deadline passes while
        # this task is suspended outside the provider wait.
        await asyncio.sleep(0.1)
        with pytest.raises(runtime.ComputeUnavailable):
            await anext(stream)
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RateLimitExceeded(requests_in_window=10, ceiling=10), "rate_limited"),
        (ConcurrencyExceeded(open_reservations=1, ceiling=1), "concurrency_exceeded"),
        (AccountSuspended("suspended"), "account_suspended"),
    ],
)
async def test_account_limits_keep_their_code_through_auto_routing(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = _funded_service()
    service.reserve = AsyncMock(side_effect=error)
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        with pytest.raises(runtime.ComputeUnavailable) as raised:
            await runtime.guarded_completion(messages=[{"role": "user", "content": "hi"}])
    finally:
        runtime._scope.reset(token)
    assert raised.value.code == code
    assert "ceiling" not in raised.value.message
    provider.assert_not_awaited()


def test_compute_error_sanitizes_ledger_details() -> None:
    error = runtime.compute_error(
        BudgetExceeded(requested=10, spent=250_000, reserved=0, ceiling=250_000)
    )
    assert error is not None
    assert error.code == "budget_exceeded"
    assert "250000" not in error.message
    assert runtime.compute_error(ValueError("boom")) is None


def test_context_admission_uses_estimate_while_hold_keeps_byte_bound() -> None:
    text = "word " * 12_000  # 60 kB: over 32k "tokens" by bytes, ~20k by estimate
    size = runtime._request_bound({"messages": [{"role": "user", "content": text}]})
    assert size.bound >= len(text.encode("utf-8"))
    assert size.estimate < 32_000 < size.bound

    policy = SimpleNamespace(
        capabilities={"chat"},
        limits_for=lambda premium: SimpleNamespace(max_context_tokens=32000, max_output_tokens=128),
        remaining_for=lambda premium: 1_000_000_000,
    )
    route = _route()
    route.supports = lambda *, required_capabilities, input_tokens, output_tokens: True
    import orchestrator.compute_runtime as module

    original = module.load_inference_policy
    original_routing = model_routing.load_model_routing
    module.load_inference_policy = lambda: SimpleNamespace(
        routes={route.route_id: route}, requirements=object()
    )
    model_routing.load_model_routing = lambda *args, **kwargs: model_routing.parse_model_routing(
        routing_document([route.model])
    )
    try:
        [(bound, output_tokens, _, _, _)] = runtime._priced_candidates(
            policy, size, {}, "openrouter/reviewed/model"
        )
    finally:
        module.load_inference_policy = original
        model_routing.load_model_routing = original_routing
    assert output_tokens == 128
    assert bound == route.estimate_microusd(size.bound, output_tokens)


# --------------------------------------------------------------------------- #
# Exact-route dispatch seam.
#
# ``_route_id``/``_dispatch_timeout_s`` are an internal keyword-only seam: they
# pin one approved route for one exact model and bound one dispatch, and they
# never reach the transport as request parameters. The seam has no failover loop;
# it reports a typed, sanitized failure and the caller owns the second call.
# --------------------------------------------------------------------------- #
PINNED_MODEL = "openrouter/test/pooled"


def _pooled_route(route_id: str, *, provider: str, price: int, endpoint: str | None = None) -> Any:
    """One approved route for :data:`PINNED_MODEL`, distinct at the wire."""
    route = _route(model=PINNED_MODEL, input_price=price, output_price=price)
    route.route_id = route_id
    route.endpoint = endpoint or f"https://openrouter.ai/api/v1/{route_id}"
    route.transport = SimpleNamespace(provider_only=(provider,))
    route.transport_payload = lambda requirements: {
        "extra_body": {
            "provider": {
                "only": [provider],
                "order": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "max_price": {"prompt": price / 1_000_000, "completion": price / 1_000_000},
            }
        }
    }
    return route


def _pooled_pool() -> list[Any]:
    """Two approved routes for ONE exact model: a cheap one and a dearer one."""
    return [
        _pooled_route("pooled-cheap", provider="shared-provider", price=1000),
        _pooled_route("pooled-dear", provider="alternate-provider", price=9000),
    ]


class _APIErrorWithResponse(litellm.APIError):
    response: httpx.Response


def _status_error(
    status: int, *, message: str = "provider refused", headers: dict[str, str] | None = None
) -> BaseException:
    """A transport error whose only structured evidence is its HTTP status."""
    response = httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    error = _APIErrorWithResponse(
        status_code=status,
        message=message,
        llm_provider="openrouter",
        model=PINNED_MODEL,
    )
    error.response = response
    return error


def _chat_service(**attributes: Any) -> Any:
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="pooled-hold"),
        settle=AsyncMock(),
    )
    for name, value in attributes.items():
        setattr(service, name, value)
    return service


@asynccontextmanager
async def _account_scope(service: Any, **attributes: Any) -> AsyncIterator[runtime.ComputeScope]:
    scope = runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), **attributes)
    token = runtime._scope.set(scope)
    try:
        yield scope
    finally:
        runtime._scope.reset(token)


def _assert_nothing_dispatched(service: Any, provider: AsyncMock) -> None:
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_pinned_route_dispatches_exactly_one_of_two_same_model_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pooled_pool()
    assert {route.model for route in pool} == {PINNED_MODEL}
    assert len({route.route_id for route in pool}) == 2
    _qualified_policy(monkeypatch, route=pool)
    service = _chat_service()
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        # Unpinned: the profile still picks the cheapest acceptable route.
        await runtime.guarded_completion(
            model=PINNED_MODEL, messages=[{"role": "user", "content": "hi"}]
        )
        cheap = provider.await_args
        assert cheap is not None
        assert cheap.kwargs["extra_body"]["provider"]["only"] == ["shared-provider"]
        assert service.reserve.await_args.kwargs["route_id"] == "pooled-cheap"

        # Pinned: the exact route wins over price, on the same exact model.
        await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            _route_id="pooled-dear",
        )
    pinned = provider.await_args
    assert pinned is not None
    assert pinned.kwargs["model"] == PINNED_MODEL
    assert pinned.kwargs["api_base"] == "https://openrouter.ai/api/v1/pooled-dear"
    assert pinned.kwargs["extra_body"]["provider"] == {
        **provider.await_args_list[0].kwargs["extra_body"]["provider"],
        "only": ["alternate-provider"],
        "order": ["alternate-provider"],
        "max_price": {"prompt": 0.009, "completion": 0.009},
    }
    assert service.reserve.await_args.kwargs["route_id"] == "pooled-dear"
    # The seam's own arguments are not request parameters and the SDK still never
    # retries: a hidden retry would re-send a pinned route on unreserved budget.
    assert not {"_route_id", "_dispatch_timeout_s"} & pinned.kwargs.keys()
    assert pinned.kwargs["num_retries"] == 0
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_pinned_route_failure_never_walks_another_route_of_the_same_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(side_effect=RuntimeError("provider refused"))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as failed:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert failed.value.code == "capacity_unavailable"
    # One reservation, one transport call: the dearer same-model route is a
    # caller's decision, never a silent walk outward from a pin.
    assert provider.await_count == 1
    assert service.reserve.await_count == 1
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route_id", ["pooled-unknown", "", "   ", 7])
async def test_pinned_route_refuses_an_unusable_route_selector(
    monkeypatch: pytest.MonkeyPatch, route_id: Any
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id=route_id,
            )
    assert denied.value.code in {"route_unavailable", "capacity_unavailable"}
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
async def test_none_route_selector_preserves_default_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            _route_id=None,
        )
    assert service.reserve.await_args.kwargs["route_id"] == "pooled-cheap"
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_pinned_route_refuses_a_route_that_serves_another_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model="openrouter/reviewed/model",
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.code == "route_unavailable"
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
async def test_pinned_route_refuses_an_unapproved_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pooled_pool()
    pool[1].is_approved = lambda requirements: False
    _qualified_policy(monkeypatch, route=pool)
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-dear",
            )
    assert denied.value.code == "route_unavailable"
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
async def test_route_revoked_between_selection_and_dispatch_never_reserves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pooled_pool()
    _qualified_policy(monkeypatch, route=pool)
    approved_policy = runtime.load_inference_policy()
    loads = 0

    def rotating_policy() -> Any:
        nonlocal loads
        loads += 1
        if loads >= 3:
            pool[0].is_approved = lambda requirements: False
        return approved_policy

    monkeypatch.setattr(runtime, "load_inference_policy", rotating_policy)
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.code == "route_unavailable"
    assert denied.value.retryable is False
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
async def test_automatic_walk_skips_only_the_revoked_candidate_before_reserving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = _route(model="openrouter/test/revoked", input_price=0)
    cheap.route_id = "revoked"
    next_route = _route(model="openrouter/test/available", input_price=1000)
    next_route.route_id = "available"
    _qualified_policy(monkeypatch, route=[cheap, next_route])
    approved_policy = runtime.load_inference_policy()
    loads = 0

    def rotating_policy() -> Any:
        nonlocal loads
        loads += 1
        if loads >= 2:
            cheap.is_approved = lambda requirements: False
        return approved_policy

    monkeypatch.setattr(runtime, "load_inference_policy", rotating_policy)
    service = _chat_service()
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service, auto_route=True):
        await runtime.guarded_completion(messages=[{"role": "user", "content": "hello"}])
    assert service.reserve.await_count == 1
    assert service.reserve.await_args.kwargs["model"] == next_route.model
    assert provider.await_count == 1
    assert provider.await_args is not None
    assert provider.await_args.kwargs["model"] == next_route.model


@pytest.mark.asyncio
async def test_pinned_route_refuses_a_route_that_cannot_serve_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Approved and on the right model, but text-only: a tools request is not a
    # capability gap to be filled by dispatching the other route instead.
    pool = _pooled_pool()
    pool[0].supports = lambda *, required_capabilities, input_tokens, output_tokens: (
        required_capabilities <= {"text"}
    )
    _qualified_policy(
        monkeypatch,
        route=pool,
        routing=routing_document([PINNED_MODEL], min_output_tokens=32),
    )
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "clock", "parameters": {}}}],
                _route_id="pooled-cheap",
            )
    assert denied.value.code == "route_unavailable"
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["auto", "", None])
async def test_pinned_route_requires_the_exact_selected_model(
    monkeypatch: pytest.MonkeyPatch, model: Any
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service, auto_route=True):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.code == "route_unavailable"
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
async def test_dispatch_timeout_is_clipped_to_the_remaining_operation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    settings = SimpleNamespace(
        request_timeout_s=0.05,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    service = _chat_service()
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        # A generous per-endpoint ask may never lengthen the operation's bound.
        await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            _route_id="pooled-cheap",
            _dispatch_timeout_s=45.0,
        )
    call = provider.await_args
    assert call is not None
    assert 0 < cast(float, call.kwargs["timeout"]) <= 0.05
    assert "_dispatch_timeout_s" not in call.kwargs
    assert call.kwargs["num_retries"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 0.25])
async def test_positive_finite_dispatch_timeout_bounds_the_dispatch(
    monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            _route_id="pooled-cheap",
            _dispatch_timeout_s=value,
        )
    call = provider.await_args
    assert call is not None
    assert 0 < call.kwargs["timeout"] <= value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [0, 0.0, -1, -0.5, math.nan, math.inf, -math.inf, True, False, "5", object()]
)
async def test_non_positive_or_non_finite_dispatch_timeout_is_denied(
    monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
                _dispatch_timeout_s=value,
            )
    assert denied.value.code == "capacity_unavailable"
    _assert_nothing_dispatched(service, provider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "category", "status", "retryable"),
    [
        (_status_error(429), "rate_limited", 429, True),
        (_status_error(502), "upstream_unavailable", 502, True),
        (_status_error(503), "upstream_unavailable", 503, True),
        (_status_error(504), "upstream_unavailable", 504, True),
        (
            litellm.RateLimitError(
                message="slow down", llm_provider="openrouter", model=PINNED_MODEL
            ),
            "rate_limited",
            429,
            True,
        ),
        (
            litellm.ServiceUnavailableError(
                message="busy", llm_provider="openrouter", model=PINNED_MODEL
            ),
            "upstream_unavailable",
            503,
            True,
        ),
        (
            litellm.Timeout(
                model=PINNED_MODEL, message="read timed out", llm_provider="openrouter"
            ),
            "timeout",
            None,
            True,
        ),
        (
            litellm.APIConnectionError(
                model=PINNED_MODEL, message="connection reset", llm_provider="openrouter"
            ),
            "connection_failed",
            None,
            True,
        ),
        (httpx.ConnectError("no route to host"), "connection_failed", None, True),
        (httpx.ReadTimeout("read timed out"), "timeout", None, True),
        (_status_error(401), "authentication_failed", 401, False),
        (_status_error(402), "payment_failed", 402, False),
        (_status_error(403), "authentication_failed", 403, False),
        (_status_error(400), "invalid_request", 400, False),
        (_status_error(404), "invalid_request", 404, False),
        (_status_error(422), "invalid_request", 422, False),
        # An upstream timeout response is a timeout to read, but it is not one of
        # the approved retryable statuses, so it stops.
        (_status_error(408), "timeout", 408, False),
        (_status_error(500), "unspecified", 500, False),
        (_status_error(451), "invalid_request", 451, False),
        # A message that merely looks retryable is not evidence of anything.
        (RuntimeError("503 Service Unavailable: rate limit exceeded"), "unspecified", None, False),
        (ValueError("401 Unauthorized: invalid api key sk-secret"), "unspecified", None, False),
        (
            litellm.BudgetExceededError(current_cost=2.0, max_budget=1.0),
            "unspecified",
            None,
            False,
        ),
    ],
)
async def test_pinned_dispatch_failure_carries_a_typed_retry_classification(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    category: str,
    status: int | None,
    retryable: bool,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(side_effect=failure)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.code == "capacity_unavailable"
    assert denied.value.message == "Qualified provider unavailable"
    assert denied.value.category == category
    assert denied.value.status_code == status
    assert denied.value.retryable is retryable
    # The failed attempt is still charged its conservative bound, exactly once.
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "12"}, 12.0),
        ({"retry-after": "0"}, 0.0),
        ({"Retry-After": "-5"}, None),
        ({"Retry-After": "not-a-number"}, None),
        # Long provider delays are not silently shortened by the adapter.
        ({"Retry-After": "999999"}, 999999.0),
    ],
)
async def test_retry_guidance_is_validated_without_shortening_provider_delays(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected: float | None,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(side_effect=_status_error(429, headers=headers))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.retryable is True
    assert denied.value.retry_after_seconds == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Wed, 21 Oct 2099 07:28:00 GMT", True),
        ("Wed, 21 Oct 2020 07:28:00 GMT", False),
    ],
)
def test_http_date_retry_guidance_respects_provider_wait(header: str, expected: bool) -> None:
    failure = _status_error(429, headers={"Retry-After": header})
    delay = runtime._provider_retry_after(failure)
    assert delay is not None
    assert (delay > 3600) is expected


@pytest.mark.asyncio
async def test_retry_guidance_is_absent_for_a_failure_that_must_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(side_effect=_status_error(401, headers={"Retry-After": "3"}))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.category == "authentication_failed"
    assert denied.value.retryable is False
    assert denied.value.retry_after_seconds is None


@pytest.mark.asyncio
async def test_classified_failure_carries_no_provider_text_or_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(
        side_effect=_status_error(
            503,
            message="upstream x-openrouter-slug leaked sk-secret-token",
            headers={
                "x-openrouter-served-provider": "private-slug",
                "authorization": "Bearer sk-secret-token",
            },
        )
    )
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.retryable is True
    assert denied.value.status_code == 503
    assert denied.value.retry_after_seconds is None
    surfaced = f"{denied.value} {denied.value.category} {denied.value.message}"
    for secret in ("sk-secret-token", "private-slug", "leaked", "openrouter.ai"):
        assert secret not in surfaced
    # The provider exception is not chained in, so its text cannot reach a log.
    assert denied.value.__cause__ is None


@pytest.mark.asyncio
async def test_dispatch_timeout_before_output_is_retryable_and_charged_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    settings = SimpleNamespace(
        request_timeout_s=60.0,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    service = _chat_service()

    async def never_answers(**call: Any) -> Any:
        await asyncio.sleep(5)
        raise AssertionError("the dispatch timeout must fire first")

    provider = AsyncMock(side_effect=never_answers)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
                # Reach the provider before the dispatch expires, independently
                # of cold deployment-settings loading or a narrow 10 ms window.
                _dispatch_timeout_s=0.1,
            )
    provider.assert_awaited_once()
    call = provider.await_args
    assert call is not None
    assert 0 < call.kwargs["timeout"] <= 0.1
    assert denied.value.category == "timeout"
    assert denied.value.status_code is None
    assert denied.value.retryable is True
    # A timeout may mean the endpoint did billed work, so the full held bound is
    # charged rather than refunded.
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )


@pytest.mark.asyncio
async def test_exhausted_operation_deadline_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    settings = SimpleNamespace(
        request_timeout_s=0.02,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    service = _chat_service()

    async def never_answers(**call: Any) -> Any:
        await asyncio.sleep(5)
        raise AssertionError("the operation deadline must fire first")

    provider = AsyncMock(side_effect=never_answers)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL, messages=[{"role": "user", "content": "hi"}]
            )
    # The operation's own budget, not this endpoint's: nothing is left to retry.
    assert denied.value.category == "deadline_exceeded"
    assert denied.value.retryable is False


@pytest.mark.asyncio
async def test_upstream_429_after_operation_deadline_cannot_authorize_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    settings = SimpleNamespace(
        request_timeout_s=0.02,
        openrouter_api_key=None,
        get_provider_config=lambda name: SimpleNamespace(extra_headers=None),
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)

    async def late_429(**call: Any) -> Any:
        await asyncio.sleep(0.02)
        raise _status_error(429)

    service = _chat_service()
    monkeypatch.setattr(runtime.litellm, "acompletion", AsyncMock(side_effect=late_429))
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.retryable is False
    assert denied.value.retry_after_seconds is None
    assert service.settle.await_count == 1


@pytest.mark.asyncio
async def test_settlement_failure_is_never_masked_as_a_retryable_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service(settle=AsyncMock(side_effect=RuntimeError("ledger unavailable")))
    provider = AsyncMock(side_effect=_status_error(429))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as broken:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    # The retryable upstream verdict is replaced by the accounting failure, so a
    # caller can never spend a second dispatch over an unsettled first one.
    assert broken.value.code == "settlement_failed"
    assert broken.value.category == "settlement_failed"
    assert broken.value.retryable is False
    assert "ledger unavailable" not in str(broken.value)
    service.settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_ledger_transport_looking_error_is_not_a_provider_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service(settle=AsyncMock(side_effect=_status_error(503)))
    provider = AsyncMock(side_effect=_status_error(429))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.category == "settlement_failed"
    assert denied.value.retryable is False
    assert denied.value.status_code is None
    provider.assert_awaited_once()
    service.settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_settlement_conflict_is_classified_and_not_retryable() -> None:
    service = _chat_service()
    scope = runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    scope.outstanding[runtime._hold_key("reservation")] = runtime.ReservationHold("reservation", 7)
    await scope.settle("reservation", 5)
    with pytest.raises(runtime.ComputeUnavailable) as conflict:
        await scope.settle("reservation", 7)
    assert conflict.value.code == "settlement_conflict"
    assert conflict.value.category == "settlement_failed"
    assert conflict.value.retryable is False
    assert conflict.value.status_code is None
    assert conflict.value.retry_after_seconds is None


@pytest.mark.asyncio
async def test_cancellation_is_never_reported_as_a_retryable_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()

    async def cancelled(**call: Any) -> Any:
        raise asyncio.CancelledError

    provider = AsyncMock(side_effect=cancelled)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(asyncio.CancelledError):
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    # Cancellation is not reclassified as a provider failure, and the reserved
    # bound is still settled rather than left open.
    service.settle.assert_awaited_once_with("pooled-hold", service.reserve.await_args.args[1])


@pytest.mark.asyncio
async def test_failed_pinned_dispatch_keeps_the_account_period_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    account = uuid.uuid4()
    service = _chat_service(reconcile_expired_reservations=AsyncMock(return_value=0))
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    provider = AsyncMock(side_effect=_status_error(503))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with runtime.account_compute(object(), account, expected_period="2026-09"):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                _route_id="pooled-cheap",
            )
    assert denied.value.retryable is True
    assert service.reserve.await_args.kwargs["expected_period"] == "2026-09"
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )


@pytest.mark.asyncio
async def test_stream_failure_after_released_output_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()

    async def chunks() -> Any:
        yield {"text": "partial"}
        raise litellm.RateLimitError(
            message="slow down", llm_provider="openrouter", model=PINNED_MODEL
        )

    provider = AsyncMock(return_value=chunks())
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        stream = await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            _route_id="pooled-cheap",
        )
        assert await anext(stream) == {"text": "partial"}
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await anext(stream)
    # The same status that is retryable before output is not retryable once a
    # token has been observed downstream.
    assert denied.value.category == "rate_limited"
    assert denied.value.status_code == 429
    assert denied.value.retryable is False
    assert service.settle.await_args.args == (
        "pooled-hold",
        service.reserve.await_args.args[1],
    )


@pytest.mark.asyncio
async def test_pinned_dispatch_returns_the_normal_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="pinned answer"))],
        usage=None,
        model=PINNED_MODEL,
    )
    provider = AsyncMock(return_value=response)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service) as scope:
        result = await runtime.guarded_completion(
            model=PINNED_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            _route_id="pooled-cheap",
        )
    assert result is response
    assert scope.selected_model == PINNED_MODEL
    assert scope.settled


@pytest.mark.asyncio
async def test_unpinned_caller_keeps_its_existing_failure_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_pooled_pool())
    service = _chat_service()
    provider = AsyncMock(side_effect=RuntimeError("provider refused"))
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with _account_scope(service):
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            await runtime.guarded_completion(
                model=PINNED_MODEL, messages=[{"role": "user", "content": "hi"}]
            )
    # Without the seam an explicit model still stops, and now carries a
    # classification that still says stop.
    assert denied.value.code == "capacity_unavailable"
    assert denied.value.message == "Qualified provider unavailable"
    assert denied.value.retryable is False
    assert service.reserve.await_args.kwargs["route_id"] == "pooled-cheap"


@pytest.mark.asyncio
async def test_expected_period_reaches_each_tool_loop_reservation_and_refuses_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    _qualified_policy(monkeypatch, route=route)
    account = uuid.uuid4()
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=128)
    current = ["2026-09"]
    reservation = object()

    async def reserve(*args: Any, **kwargs: Any) -> object:
        assert kwargs["expected_period"] == "2026-09"
        if current[0] != kwargs["expected_period"]:
            raise LimitExceeded("reservation period changed")
        return reservation

    service = SimpleNamespace(
        reconcile_expired_reservations=AsyncMock(return_value=0),
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 100000,
            )
        ),
        reserve=AsyncMock(side_effect=reserve),
        settle=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    async with runtime.account_compute(object(), account, expected_period="2026-09") as scope:
        assert scope.expected_period == "2026-09"
        await runtime.guarded_completion(
            model=route.model,
            messages=[{"role": "user", "content": "call tool"}],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        )
        current[0] = "2026-10"
        with pytest.raises(runtime.ComputeUnavailable):
            await runtime.guarded_completion(
                model=route.model,
                messages=[{"role": "tool", "content": "tool result"}],
            )
    assert service.reserve.await_count == 2
    assert provider.await_count == 1
    assert len(scope.settled) == 1


def _uncapped_candidates(
    monkeypatch: pytest.MonkeyPatch,
    route: Any,
    *,
    max_context_tokens: int | None,
    max_output_tokens: int | None,
    text: str = "hi",
) -> list[tuple[int, int, Any, bool, Any]]:
    limits = SimpleNamespace(
        max_context_tokens=max_context_tokens, max_output_tokens=max_output_tokens
    )
    policy = SimpleNamespace(
        capabilities={"chat"},
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 1_000_000_000,
    )
    monkeypatch.setattr(
        runtime,
        "load_inference_policy",
        lambda: SimpleNamespace(routes={route.route_id: route}, requirements=object()),
    )
    size = runtime._request_bound({"messages": [{"role": "user", "content": text}]})
    return runtime._priced_candidates(policy, size, {}, route.model)


def test_uncapped_plan_output_is_bounded_by_the_route_and_holds_its_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    route.max_output_tokens = 8000

    [(bound, output_tokens, _, _, _)] = _uncapped_candidates(
        monkeypatch, route, max_context_tokens=None, max_output_tokens=None
    )

    assert output_tokens == 8000
    size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
    assert bound == route.estimate_microusd(size.bound, 8000)


def test_capped_plan_output_still_clamps_below_the_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    route.max_output_tokens = 8000

    [(_, output_tokens, _, _, _)] = _uncapped_candidates(
        monkeypatch, route, max_context_tokens=32000, max_output_tokens=4096
    )

    assert output_tokens == 4096


def test_uncapped_context_admits_input_a_capped_plan_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _route()
    route.max_context_tokens = 200_000
    route.supports = lambda *, required_capabilities, input_tokens, output_tokens: True
    text = "word " * 30_000  # ~50k estimated tokens

    assert (
        _uncapped_candidates(
            monkeypatch, route, max_context_tokens=32000, max_output_tokens=4096, text=text
        )
        == []
    )
    [(_, output_tokens, _, _, _)] = _uncapped_candidates(
        monkeypatch, route, max_context_tokens=None, max_output_tokens=None, text=text
    )
    assert output_tokens == route.max_output_tokens


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan_cap", "expected"),
    [(4, 4), (None, runtime.TOOL_ROUND_SAFETY_CEILING), (0, 1)],
)
async def test_tool_round_limit_follows_the_plan_or_the_safety_ceiling(
    plan_cap: int | None, expected: int
) -> None:
    limits = SimpleNamespace(max_tool_loop_iterations=plan_cap)
    service = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(limits=limits)))
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    )
    try:
        assert await runtime.tool_round_limit() == expected
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
async def test_tool_round_limit_outside_an_account_scope_keeps_the_historic_limit() -> None:
    assert await runtime.tool_round_limit() == runtime.UNSCOPED_TOOL_ROUNDS == 4


@pytest.mark.asyncio
async def test_uncapped_plan_input_beyond_every_route_is_a_context_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified_policy(monkeypatch, route=_route())
    service = _funded_service(max_context_tokens=None, max_output_tokens=None)
    provider = AsyncMock()
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            await runtime.guarded_completion(
                messages=[{"role": "user", "content": "word " * 30_000}]
            )
    finally:
        runtime._scope.reset(token)
    assert caught.value.code == "context_limit"
    provider.assert_not_awaited()
    service.reserve.assert_not_awaited()


async def _auto_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    routes: list[Any],
    routing: dict[str, Any],
    *,
    account_output: int | None,
    remaining: int = 100000,
) -> Mapping[str, Any]:
    """Dispatch one automatic request and return the provider kwargs."""
    _qualified_policy(monkeypatch, route=routes, routing=routing)
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=account_output)
    service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: remaining,
            )
        ),
        reserve=AsyncMock(return_value="group-hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    token = runtime._scope.set(
        runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    )
    try:
        await runtime.guarded_completion(model="auto", messages=[{"role": "user", "content": "hi"}])
    finally:
        runtime._scope.reset(token)
    assert provider.await_args is not None
    return provider.await_args.kwargs


def _capped(model: str, cap: int, price: int) -> Any:
    route = _route(model=model, input_price=price)
    route.route_id = model.rsplit("/", 1)[-1]
    route.max_output_tokens = cap
    return route


@pytest.mark.asyncio
async def test_shorter_output_route_cannot_win_a_group_on_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Account target 128 exceeds both caps; the group's common target is its
    # largest capacity (64), so the cheaper 48-cap route is not comparable.
    larger = _capped("openrouter/test/larger", 64, price=1000)
    shorter = _capped("openrouter/test/shorter", 48, price=0)
    kwargs = await _auto_dispatch(
        monkeypatch,
        [larger, shorter],
        routing_document([larger.model, shorter.model], min_output_tokens=32),
        account_output=128,
    )
    assert kwargs["model"] == larger.model
    assert kwargs["max_tokens"] == 64


@pytest.mark.asyncio
async def test_first_group_serves_at_its_feasible_target_instead_of_escalating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _capped("openrouter/test/first", 64, price=0)
    later = _capped("openrouter/test/later", 128, price=1000)
    routing = routing_document([first.model, later.model], min_output_tokens=32)
    routine = next(entry for entry in routing["profiles"] if entry["profile"] == "routine")
    routine["groups"] = [
        {"group": "cheap", "models": [first.model]},
        {"group": "step", "models": [later.model]},
    ]
    kwargs = await _auto_dispatch(monkeypatch, [first, later], routing, account_output=128)
    assert kwargs["model"] == first.model
    assert kwargs["max_tokens"] == 64


@pytest.mark.asyncio
async def test_unaffordable_largest_route_lowers_the_group_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 128-cap route cannot fit the remaining budget at 128 output tokens, so
    # it is not eligible and the group's target falls to the 64-cap route.
    pricey = _capped("openrouter/test/pricey", 128, price=10_000_000)
    modest = _capped("openrouter/test/modest", 64, price=1000)
    kwargs = await _auto_dispatch(
        monkeypatch,
        [pricey, modest],
        routing_document([pricey.model, modest.model], min_output_tokens=32),
        account_output=None,
        remaining=1000,
    )
    assert kwargs["model"] == modest.model
    assert kwargs["max_tokens"] == 64


# Dispatch-aware metered tools opt into known-zero local outcomes. Legacy
# metered tools deliberately retain conservative accounting without a mark.
def _metered_approval(monkeypatch: pytest.MonkeyPatch) -> runtime.ToolServiceApproval:
    approval = runtime.ToolServiceApproval(
        service_id="test-search",
        service="web_search",
        provider="brave",
        unit="call",
        ceiling_microusd=7000,
        fixed_microusd=5000,
    )
    monkeypatch.setattr(runtime, "approved_tool_service", lambda **kwargs: approval)
    return approval


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch_aware", "phase", "expected"),
    [
        (False, "local", 7000),
        (False, "confirmed", 5000),
        (True, "local", 0),
        (True, "dispatched", 7000),
        (True, "confirmed", 5000),
    ],
)
async def test_metered_tool_failure_accounting_depends_on_explicit_dispatch_tracking(
    monkeypatch: pytest.MonkeyPatch, dispatch_aware: bool, phase: str, expected: int
) -> None:
    approval = _metered_approval(monkeypatch)
    service = _chat_service()
    async with _account_scope(service) as scope:
        with pytest.raises(RuntimeError, match="local failure"):
            async with runtime.metered_tool_call(approval, dispatch_aware=dispatch_aware) as charge:
                if dispatch_aware and phase != "local":
                    charge.mark_dispatched()
                if phase == "confirmed":
                    charge.confirm()
                raise RuntimeError("local failure")
        assert scope.outstanding == {}
    service.settle.assert_awaited_once()
    assert service.settle.await_args.args == ("pooled-hold", expected)


@pytest.mark.asyncio
async def test_metered_tool_dispatch_state_cannot_be_reset_or_confirmed_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _chat_service()
    approval = _metered_approval(monkeypatch)
    async with _account_scope(service) as scope:
        async with runtime.metered_tool_call(approval, dispatch_aware=True) as charge:
            hold = scope.outstanding["pooled-hold"]
            assert hold.actual == 0
            with pytest.raises(RuntimeError):
                charge.confirm()
            for name, value in [("known_not_dispatched", True), ("confirmed", True), ("units", 0)]:
                with pytest.raises(AttributeError):
                    setattr(charge, name, value)
            charge.mark_dispatched()
            assert hold.actual is None
            assert not charge.known_not_dispatched
            with pytest.raises(RuntimeError):
                charge.mark_dispatched()
            charge.confirm()
            with pytest.raises(RuntimeError):
                charge.mark_dispatched()
            with pytest.raises(RuntimeError):
                charge.confirm(units=2)
    service.settle.assert_awaited_once_with("pooled-hold", 5000, usage={"tool_calls": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("units", [0, -1, True, False, 1.5, "1", None])
async def test_metered_tool_invalid_confirmation_cannot_restore_zero_after_dispatch(
    monkeypatch: pytest.MonkeyPatch, units: Any
) -> None:
    approval = _metered_approval(monkeypatch)
    service = _chat_service()
    async with _account_scope(service):
        with pytest.raises(ValueError):
            async with runtime.metered_tool_call(approval, dispatch_aware=True) as charge:
                charge.mark_dispatched()
                charge.confirm(units=units)
    service.settle.assert_awaited_once_with("pooled-hold", 7000, usage={"estimated_cost": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatched", [False, True])
async def test_metered_tool_cancellation_before_and_after_dispatch(
    monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    approval = _metered_approval(monkeypatch)
    service = _chat_service()
    entered = asyncio.Event()

    async def call() -> None:
        async with runtime.metered_tool_call(approval, dispatch_aware=True) as charge:
            if dispatched:
                charge.mark_dispatched()
            entered.set()
            await asyncio.Event().wait()

    async with _account_scope(service) as scope:
        task = asyncio.create_task(call())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scope.outstanding == {}
    assert service.settle.await_args.args == ("pooled-hold", 7000 if dispatched else 0)


@pytest.mark.asyncio
async def test_metered_tool_cancel_during_committed_reservation_recovers_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _metered_approval(monkeypatch)
    committed = asyncio.Event()
    return_id = asyncio.Event()

    async def reserve(*args: Any, **kwargs: Any) -> str:
        committed.set()
        await return_id.wait()
        return "committed-hold"

    service = _chat_service(reserve=AsyncMock(side_effect=reserve))

    async def call() -> None:
        async with runtime.metered_tool_call(approval, dispatch_aware=True):
            pytest.fail("cancelled acquisition must not yield provider work")

    async with _account_scope(service) as scope:
        task = asyncio.create_task(call())
        await asyncio.wait_for(committed.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # repeated cancellation must not cancel recovery either
        await asyncio.sleep(0)
        assert not task.done()
        return_id.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert scope.outstanding == {}
        assert not scope.extended_lock.locked()
    service.settle.assert_awaited_once_with("committed-hold", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "lock"])
async def test_metered_tool_deadline_before_acquisition_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    approval = _metered_approval(monkeypatch)

    async def resolve(*args: Any) -> Any:
        await asyncio.Event().wait()

    service = _chat_service()
    if stage == "resolve":
        service.resolve = AsyncMock(side_effect=resolve)
    async with _account_scope(service) as scope:
        if stage == "lock":
            await scope.extended_lock.acquire()
        try:
            with pytest.raises(TimeoutError):
                async with runtime.metered_tool_call(
                    approval,
                    required_capability="chat",
                    dispatch_aware=True,
                    work_deadline=asyncio.get_running_loop().time() + 0.03,
                ):
                    pytest.fail("expired work must not yield")
        finally:
            if stage == "lock":
                scope.extended_lock.release()
        assert scope.outstanding == {}
    service.reserve.assert_not_awaited()
    service.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_metered_tool_late_reservation_settles_zero_outside_work_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _metered_approval(monkeypatch)

    async def reserve(*args: Any, **kwargs: Any) -> str:
        await asyncio.sleep(0.06)
        return "late-hold"

    service = _chat_service(reserve=AsyncMock(side_effect=reserve))
    async with _account_scope(service) as scope:
        with pytest.raises(TimeoutError):
            async with runtime.metered_tool_call(
                approval,
                dispatch_aware=True,
                work_deadline=asyncio.get_running_loop().time() + 0.02,
            ):
                pytest.fail("late reservation must not yield")
        assert scope.outstanding == {}
    service.settle.assert_awaited_once_with("late-hold", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("acquisition_timeout", [False, True])
async def test_metered_tool_failed_zero_settlement_retains_zero_and_stops(
    monkeypatch: pytest.MonkeyPatch, acquisition_timeout: bool
) -> None:
    approval = _metered_approval(monkeypatch)

    async def reserve(*args: Any, **kwargs: Any) -> str:
        if acquisition_timeout:
            await asyncio.sleep(0.04)
        return "zero-hold"

    service = _chat_service(
        reserve=AsyncMock(side_effect=reserve),
        settle=AsyncMock(side_effect=RuntimeError("ledger unavailable")),
    )
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            async with runtime.metered_tool_call(
                approval,
                dispatch_aware=True,
                work_deadline=asyncio.get_running_loop().time() + 0.02
                if acquisition_timeout
                else None,
            ):
                raise RuntimeError("local refusal")
        assert caught.value.code == "settlement_failed"
        assert not caught.value.retryable
        assert scope.outstanding["zero-hold"].actual == 0
    service.settle.assert_awaited_once_with("zero-hold", 0)


@pytest.mark.asyncio
async def test_legacy_metered_tool_database_timeout_keeps_sanitized_account_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _metered_approval(monkeypatch)
    service = _chat_service(reserve=AsyncMock(side_effect=TimeoutError("private database detail")))
    async with _account_scope(service) as scope:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            async with runtime.metered_tool_call(approval):
                pytest.fail("failed reservation must not yield")
        assert caught.value.code == "account_unavailable"
        assert "private" not in caught.value.message
        assert scope.outstanding == {}
    service.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_metered_tool_failed_late_reservation_has_no_hold_to_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _metered_approval(monkeypatch)

    async def reserve(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.04)
        raise RuntimeError("reservation failed")

    service = _chat_service(reserve=AsyncMock(side_effect=reserve))
    async with _account_scope(service) as scope:
        with pytest.raises(TimeoutError):
            async with runtime.metered_tool_call(
                approval,
                dispatch_aware=True,
                work_deadline=asyncio.get_running_loop().time() + 0.02,
            ):
                pytest.fail("late failed acquisition must not yield")
        assert scope.outstanding == {}
    service.settle.assert_not_awaited()
