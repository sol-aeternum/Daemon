"""Account/protocol boundaries of the private inference dispatch seam."""

from __future__ import annotations

import asyncio
import uuid
import runpy
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.entitlements import EntitlementService
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


def _qualified_policy(monkeypatch: pytest.MonkeyPatch, *, route=None) -> None:
    entries = route if isinstance(route, list) else [route] if route else []
    routes = {entry.route_id: entry for entry in entries}
    monkeypatch.setattr(
        runtime,
        "load_inference_policy",
        lambda: SimpleNamespace(routes=routes, requirements=object()),
    )


def test_no_qualified_route_refuses_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualified_policy(monkeypatch)
    with pytest.raises(runtime.ComputeUnavailable, match="Approved inference route unavailable"):
        runtime.choose_route()


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
    assert call["reasoning_effort"] == "medium"
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
    funded = _route(model="openrouter/funded", input_price=100000)
    zero = _route(model="openrouter/zero", input_price=0)
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
            model="openrouter/funded", messages=[{"role": "user", "content": "hi"}]
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_args.args[1] == 0
    assert service.reserve.await_args.kwargs["model"] == "openrouter/zero"
    service.settle.assert_awaited_once_with("zero-hold", 0, usage={"estimated_cost": True})
    assert provider.await_args is not None
    assert provider.await_args.kwargs["model"] == "openrouter/zero"


@pytest.mark.asyncio
async def test_auto_route_price_weights_actual_request_and_falls_back_after_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap_input = _route(model="openrouter/cheap-input", input_price=1000, output_price=1_000_000)
    cheap_output = _route(model="openrouter/cheap-output", input_price=500000, output_price=1000)
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
            model="openrouter/suggested-auto",
            messages=[{"role": "user", "content": "a" * 2000}],
        )
    finally:
        runtime._scope.reset(token)
    assert service.reserve.await_count == 2
    assert service.reserve.await_args_list[0].kwargs["model"] == "openrouter/cheap-input"
    assert service.reserve.await_args_list[1].kwargs["model"] == "openrouter/cheap-output"
    assert service.settle.await_args_list[0].args == (
        "first",
        service.reserve.await_args_list[0].args[1],
    )
    assert service.settle.await_args_list[1].args == (
        "second",
        service.reserve.await_args_list[1].args[1],
    )


@pytest.mark.asyncio
async def test_tool_request_skips_cheaper_text_only_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_only = _route(model="openrouter/text", input_price=0)
    text_only.route_id = "text"
    text_only.supports = lambda *, required_capabilities, input_tokens, output_tokens: (
        required_capabilities <= {"text"}
    )
    tools_route = _route(model="openrouter/tools", input_price=1000)
    tools_route.route_id = "tools"
    tools_route.max_output_tokens = 32
    _qualified_policy(monkeypatch, route=[text_only, tools_route])
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
    assert provider.await_args.kwargs["model"] == "openrouter/tools"
    assert provider.await_args.kwargs["max_tokens"] == 32


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
        update_conversation = AsyncMock(return_value={})

    @asynccontextmanager
    async def checked_scope(scope_pool, scope_user_id, *, operation, auto_route, background):
        assert scope_pool is pool
        assert scope_user_id == owner
        assert operation == "agent" and auto_route is True
        # Worker jobs never take the interactive rate or concurrency slots.
        assert background is True
        yield None

    globals_map = title_job.__globals__
    monkeypatch.setitem(globals_map, "MemoryStore", Store)
    monkeypatch.setitem(globals_map, "account_compute", checked_scope)
    generator = AsyncMock(return_value="Greeting")
    monkeypatch.setitem(globals_map, "generate_conversation_title", generator)
    result = await title_job({"store": Store(), "db_pool": pool}, conversation_id)
    assert result == {"status": "ok", "title": "Greeting"}
    generator.assert_awaited_once()
    Store.update_conversation.assert_awaited_once_with(conversation_id, title="Greeting")


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
        insert_memory=AsyncMock(return_value={"id": memory_id}),
        search_memories_bm25=AsyncMock(return_value=[]),
    )
    actual = await dedup.dedup_and_store(
        cast(MemoryStore, store),
        user_id,
        "explicit preference",
        "user_created",
        "preference",
    )
    assert actual == memory_id
    assert store.insert_memory.await_args.kwargs["user_id"] == user_id
    assert store.insert_memory.await_args.kwargs["embedding"] is None


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
        insert_memory=AsyncMock(return_value={"id": new_id}),
        _pool=SimpleNamespace(execute=AsyncMock()),
    )
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
    assert store.insert_memory.await_args.kwargs["embedding"] is None
    store._pool.execute.assert_not_awaited()
    store.search_memories_bm25.assert_awaited_once()


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
        search_memories_bm25=AsyncMock(return_value=[existing]),
        insert_memory=AsyncMock(),
    )
    result = await dedup.dedup_and_store(
        cast(MemoryStore, store),
        uuid.uuid4(),
        "User prefers tea",
        "extracted",
        "preference",
    )
    assert result == existing_id
    store.insert_memory.assert_not_awaited()


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
            return_value={"id": memory_id, "user_id": account, "content": "local"}
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


def _funded_service(*, max_context_tokens: int = 32000, max_output_tokens: int = 128, **extra):
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
    module.load_inference_policy = lambda: SimpleNamespace(
        routes={route.route_id: route}, requirements=object()
    )
    try:
        [(bound, output_tokens, _, _)] = runtime._priced_candidates(
            policy, size, {}, "openrouter/reviewed/model"
        )
    finally:
        module.load_inference_policy = original
    assert output_tokens == 128
    assert bound == route.estimate_microusd(size.bound, output_tokens)
