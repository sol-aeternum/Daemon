"""Deployment approval must remain opt-in, pinned, and monitored for ZDR changes."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime, model_routing
from orchestrator.entitlements import EntitlementService, attestation
from orchestrator.entitlements.policy import (
    InferencePolicy,
    RouteNotApproved,
    parse_inference_policy,
)
from tests.test_compute_runtime import _status_error

ROOT = Path(__file__).resolve().parents[1]
# Evaluation time: the latest operator review in the shipped policy (#747).
REVIEWED = datetime(2026, 11, 19, tzinfo=timezone.utc)
STALE = REVIEWED + attestation.STALE_AFTER + timedelta(minutes=1)
GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"
GLM_ROUTE = "glm-flash-inceptron-fp8"
DEEPSEEK_FLASH = "openrouter/deepseek/deepseek-v4.1-flash-1017"
DEEPSEEK_ROUTE = "deepseek-flash-coreweave-fp8"


def _production() -> InferencePolicy:
    return parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.production.json").read_text())
    )


def _attested_snapshot(*, revoked: bool = False) -> attestation.AttestationSnapshot:
    keys = {
        (route.route_id, attestation.baseline_fingerprint(route))
        for route in _production().routes.values()
    }
    return attestation.AttestationSnapshot(
        loaded=True,
        attested_at=dict.fromkeys(keys, REVIEWED),
        revoked=frozenset(keys) if revoked else frozenset(),
    )


@pytest.fixture(autouse=True)
def _attested() -> Iterator[None]:
    """Every production route confirmed by a ZDR check at review time."""
    previous = attestation.snapshot()
    attestation.reset_local_revocations()
    attestation.set_snapshot(_attested_snapshot())
    yield
    attestation.reset_local_revocations()
    attestation.set_snapshot(previous)


@pytest.fixture
def production_policy(monkeypatch: pytest.MonkeyPatch) -> InferencePolicy:
    from orchestrator.entitlements import policy as policy_module

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return REVIEWED

    monkeypatch.setattr(policy_module, "datetime", ReviewClock)
    policy = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.production.json").read_text())
    )
    routing = model_routing.load_model_routing(ROOT / "config/model_routing.json")
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(model_routing, "load_model_routing", lambda *a, **kw: routing)
    return policy


@pytest.fixture
def mocked_dispatch(monkeypatch: pytest.MonkeyPatch, production_policy: InferencePolicy):
    """Real production admission and transport construction; no external dispatch."""
    limits = SimpleNamespace(max_context_tokens=32_000, max_output_tokens=4096)
    resolved = SimpleNamespace(
        capabilities={"chat", "premium_routing"},
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: 1_000_000,
    )
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=resolved),
        reserve=AsyncMock(return_value="mocked-hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(compute_runtime.litellm, "acompletion", provider)
    scope = compute_runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    token = compute_runtime._scope.set(scope)
    try:
        yield service, provider
    finally:
        compute_runtime._scope.reset(token)


def test_glm_flash_operator_caps_units_and_retained_controls(
    production_policy: InferencePolicy,
) -> None:
    route = production_policy.routes[GLM_ROUTE]
    assert route.model == GLM_FLASH
    assert route.route_class == "routine"
    assert route.model_capabilities == frozenset({"text", "tools", "json_schema"})
    assert (route.max_context_tokens, route.max_output_tokens) == (1048576, 131072)
    assert route.account_prompt_logging_disabled is True
    assert route.free_model_training_opt_out is True
    assert route.price_ceiling is not None
    assert route.price_ceiling.microusd_per_1m_prompt == 225000
    assert route.price_ceiling.microusd_per_1m_completion == 490000
    assert route.estimate_microusd(1_000_000, 0) == 225000
    assert route.estimate_microusd(0, 1_000_000) == 490000
    assert route.transport_payload(production_policy.requirements)["extra_body"]["provider"] == {
        "only": ["inceptron/fp8"],
        "order": ["inceptron/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "max_price": {"prompt": 0.225, "completion": 0.49},
    }


def test_deepseek_flash_serves_1017_checkpoint_with_operator_caps(
    production_policy: InferencePolicy,
) -> None:
    route = production_policy.routes[DEEPSEEK_ROUTE]
    assert route.model == DEEPSEEK_FLASH
    assert route.route_class == "routine"
    assert route.model_capabilities == frozenset({"text", "tools", "json_schema"})
    assert (route.max_context_tokens, route.max_output_tokens) == (1048576, 393216)
    assert route.review.reviewed_at == datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert route.price_ceiling is not None
    assert route.estimate_microusd(1_000_000, 0) == 300000
    assert route.estimate_microusd(0, 1_000_000) == 1200000
    assert route.transport_payload(production_policy.requirements)["extra_body"]["provider"] == {
        "only": ["coreweave/fp8"],
        "order": ["coreweave/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "max_price": {"prompt": 0.3, "completion": 1.2},
    }


@pytest.mark.asyncio
async def test_glm_flash_existing_selection_sends_production_caps(mocked_dispatch):
    service, provider = mocked_dispatch
    with model_routing.routing_context("routine", preferred_model=GLM_FLASH) as state:
        await compute_runtime.guarded_completion(
            model=GLM_FLASH,
            messages=[{"role": "user", "content": "fixture"}],
            max_tokens=4096,
        )
        assert state.selected_model == GLM_FLASH
        assert state.selected_route_id == GLM_ROUTE
        assert state.selected_effort == "low"
    provider.assert_awaited_once()
    call = provider.await_args.kwargs
    assert call["model"] == GLM_FLASH
    assert call["api_base"] == "https://openrouter.ai/api/v1"
    assert call["extra_body"]["provider"]["only"] == ["inceptron/fp8"]
    assert call["extra_body"]["provider"]["allow_fallbacks"] is False
    assert call["extra_body"]["provider"]["max_price"] == {"prompt": 0.225, "completion": 0.49}
    assert call["num_retries"] == 0
    assert service.reserve.await_args.kwargs["route_id"] == GLM_ROUTE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial", ["unapproved", "attestation_revoked", "review_in_future", "fallback"]
)
async def test_glm_flash_disqualified_route_never_reserves_or_dispatches(
    monkeypatch, production_policy: InferencePolicy, mocked_dispatch, denial
):
    route = production_policy.routes[GLM_ROUTE]
    if denial == "unapproved":
        route = replace(route, approved=False)
    elif denial == "attestation_revoked":
        key = (route.route_id, attestation.baseline_fingerprint(route))
        current = attestation.snapshot()
        attestation.set_snapshot(
            attestation.AttestationSnapshot(
                loaded=True, attested_at=current.attested_at, revoked=frozenset({key})
            )
        )
    elif denial == "review_in_future":
        later = REVIEWED + timedelta(days=1)
        route = replace(route, review=replace(route.review, reviewed_at=later))
    else:
        route = replace(
            route,
            transport=replace(
                route.transport,
                allow_fallbacks=True,
                provider_only=("inceptron/fp8", "inceptron"),
                provider_order=("inceptron/fp8", "inceptron"),
            ),
        )
    altered = replace(production_policy, routes={**production_policy.routes, GLM_ROUTE: route})
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: altered)
    service, provider = mocked_dispatch
    with pytest.raises(RouteNotApproved):
        altered.transport_payload(GLM_ROUTE)
    with pytest.raises(compute_runtime.ComputeUnavailable):
        await compute_runtime.guarded_completion(
            model=GLM_FLASH, messages=[{"role": "user", "content": "fixture"}]
        )
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_glm_flash_explicit_provider_failure_never_substitutes(mocked_dispatch):
    service, provider = mocked_dispatch
    provider.side_effect = RuntimeError("mock provider failure")
    with pytest.raises(compute_runtime.ComputeUnavailable):
        await compute_runtime.guarded_completion(
            model=GLM_FLASH, messages=[{"role": "user", "content": "fixture"}]
        )
    provider.assert_awaited_once()
    service.reserve.assert_awaited_once()
    service.settle.assert_awaited_once()
    assert provider.await_args.kwargs["model"] == GLM_FLASH


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["routine", "background", "research"])
async def test_flash_fallback_serves_only_when_no_luna_route_qualifies(
    monkeypatch, production_policy: InferencePolicy, mocked_dispatch, profile
):
    # #747: the operator-placed flash_fallback group answers these profiles when
    # Luna's route is not qualified, and is never selected ahead of a qualified Luna.
    flash_only = replace(
        production_policy,
        routes={
            route_id: production_policy.routes[route_id] for route_id in (GLM_ROUTE, DEEPSEEK_ROUTE)
        },
    )
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: flash_only)
    service, provider = mocked_dispatch
    with model_routing.routing_context(profile) as state:
        await compute_runtime.guarded_completion(messages=[{"role": "user", "content": "fixture"}])
        assert state.selected_group == "flash_fallback"
        assert state.selected_route_id in {GLM_ROUTE, DEEPSEEK_ROUTE}
    provider.assert_awaited_once()
    assert provider.await_args.kwargs["extra_body"]["provider"]["allow_fallbacks"] is False

    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: production_policy)
    assert compute_runtime.choose_route(profile=profile).route_id == "luna-azure"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["routine", "background", "research"])
async def test_failing_qualified_luna_fails_over_to_flash_on_a_second_hold(
    production_policy: InferencePolicy, mocked_dispatch, profile
):
    # #750 review: the owner approved failover, so a qualified Luna that fails
    # at dispatch hands the same turn to the flash group, as any automatic
    # multi-group profile does. Each attempt reserves and settles on its own.
    service, provider = mocked_dispatch
    service.reserve.side_effect = ["luna-hold", "flash-hold"]
    provider.side_effect = [_status_error(503), {"choices": []}]
    with model_routing.routing_context(profile):
        await compute_runtime.guarded_completion(messages=[{"role": "user", "content": "fixture"}])
    sent = [call.kwargs["model"] for call in provider.await_args_list]
    assert len(sent) == 2
    assert "gpt-6-luna" in sent[0]
    assert any(flash in sent[1] for flash in ("deepseek-v4.1-flash-1017", "glm-5.3-flash"))
    assert service.reserve.await_count == 2
    assert [call.args[0] for call in service.settle.await_args_list] == [
        "luna-hold",
        "flash-hold",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_failover_walk_is_bounded_by_luna_and_both_flash_routes(
    production_policy: InferencePolicy, mocked_dispatch, stream
):
    # #750 review: the owner approved trying both Flash routes on one call.
    # Luna and the cheaper Flash route fail before output, the other Flash
    # route serves: three dispatches, three holds settled in order, no fourth.
    service, provider = mocked_dispatch
    service.reserve.side_effect = ["luna-hold", "flash-hold-a", "flash-hold-b"]

    async def failing_chunks():
        raise _status_error(503)
        yield {}  # pragma: no cover - unreachable; marks this an async generator

    async def ok_chunks():
        yield {"text": "recovered"}

    if stream:
        provider.side_effect = [failing_chunks(), failing_chunks(), ok_chunks()]
    else:
        provider.side_effect = [_status_error(503), _status_error(503), {"choices": []}]
    with model_routing.routing_context("routine"):
        result = await compute_runtime.guarded_completion(
            messages=[{"role": "user", "content": "fixture"}], stream=stream
        )
        if stream:
            assert [chunk async for chunk in result] == [{"text": "recovered"}]
    sent = [call.kwargs["model"] for call in provider.await_args_list]
    assert len(sent) == 3
    assert "gpt-6-luna" in sent[0]
    # Cheaper bounded estimate first (GLM's ceilings are lower), then DeepSeek.
    assert "glm-5.3-flash" in sent[1] and "deepseek-v4.1-flash-1017" in sent[2]
    assert service.reserve.await_count == 3
    assert [call.args[0] for call in service.settle.await_args_list] == [
        "luna-hold",
        "flash-hold-a",
        "flash-hold-b",
    ]


@pytest.mark.asyncio
async def test_all_three_routes_failing_raises_after_three_holds(
    production_policy: InferencePolicy, mocked_dispatch
):
    service, provider = mocked_dispatch
    service.reserve.side_effect = ["luna-hold", "flash-hold-a", "flash-hold-b"]
    provider.side_effect = [_status_error(503)] * 3
    with model_routing.routing_context("routine"):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            await compute_runtime.guarded_completion(
                messages=[{"role": "user", "content": "fixture"}]
            )
    assert provider.await_count == 3
    assert service.reserve.await_count == 3
    assert service.settle.await_count == 3


@pytest.mark.asyncio
async def test_pre_output_luna_stream_failure_fails_over_to_flash(
    production_policy: InferencePolicy, mocked_dispatch
):
    service, provider = mocked_dispatch
    service.reserve.side_effect = ["luna-hold", "flash-hold"]

    async def failing_chunks():
        raise _status_error(503)
        yield {}  # pragma: no cover - unreachable; marks this an async generator

    async def ok_chunks():
        yield {"text": "recovered"}

    provider.side_effect = [failing_chunks(), ok_chunks()]
    with model_routing.routing_context("routine"):
        stream = await compute_runtime.guarded_completion(
            messages=[{"role": "user", "content": "fixture"}], stream=True
        )
        received = [chunk async for chunk in stream]
    assert received == [{"text": "recovered"}]
    sent = [call.kwargs["model"] for call in provider.await_args_list]
    assert "gpt-6-luna" in sent[0] and "gpt-6-luna" not in sent[1]
    assert [call.args[0] for call in service.settle.await_args_list] == [
        "luna-hold",
        "flash-hold",
    ]


@pytest.mark.asyncio
async def test_failing_luna_judge_never_fails_over(
    production_policy: InferencePolicy, mocked_dispatch
):
    # The merge judge's profile has no second group, so a Luna failure ends it.
    service, provider = mocked_dispatch
    provider.side_effect = [_status_error(503)]
    with model_routing.routing_context("memory_equivalence"):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            await compute_runtime.guarded_completion(
                messages=[{"role": "user", "content": "fixture"}]
            )
    assert provider.await_count == 1
    assert service.reserve.await_count == 1


@pytest.mark.asyncio
async def test_memory_equivalence_judge_never_falls_back_to_flash_routes(
    monkeypatch, production_policy: InferencePolicy, mocked_dispatch
):
    # #747: the merge judge was qualified only on Luna, so its profile carries
    # no flash_fallback group; without a qualified Luna route it is refused.
    routing = model_routing.load_model_routing()
    assert routing.profile("memory_equivalence").ranked_models() == (
        "openrouter/openai/gpt-6-luna",
    )
    assert routing.profile("background").group_names() == ("luna", "flash_fallback")
    flash_only = replace(
        production_policy,
        routes={
            route_id: production_policy.routes[route_id] for route_id in (GLM_ROUTE, DEEPSEEK_ROUTE)
        },
    )
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: flash_only)
    service, provider = mocked_dispatch
    with model_routing.routing_context("memory_equivalence"):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            await compute_runtime.guarded_completion(
                messages=[{"role": "user", "content": "fixture"}]
            )
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()

    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: production_policy)
    assert compute_runtime.choose_route(profile="memory_equivalence").route_id == "luna-azure"


@pytest.mark.asyncio
async def test_flash_routes_are_not_automatic_reasoning_candidates(
    monkeypatch, production_policy: InferencePolicy, mocked_dispatch
):
    flash_only = replace(
        production_policy,
        routes={
            route_id: production_policy.routes[route_id] for route_id in (GLM_ROUTE, DEEPSEEK_ROUTE)
        },
    )
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: flash_only)
    service, provider = mocked_dispatch
    with model_routing.routing_context("reasoning"):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            await compute_runtime.guarded_completion(
                messages=[{"role": "user", "content": "fixture"}]
            )
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


def test_deployment_policy_is_opt_in_and_fails_closed_without_attestation() -> None:
    default = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.json").read_text())
    )
    policy = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.production.json").read_text())
    )
    assert not any(
        r.is_approved(default.requirements, now=REVIEWED) for r in default.routes.values()
    )
    assert len(policy.routes) == 9
    assert policy.approved_tool_service_ids(now=REVIEWED) == {"brave-web-search"}
    assert not default.approved_tool_service_ids(now=REVIEWED)
    assert policy.default_route_id is None
    sol = policy.routes["sol-azure"]
    assert sol.model == "openrouter/openai/gpt-6.2-sol"
    assert sol.review.reviewed_at == REVIEWED
    assert all(
        r.model not in {"openrouter/openai/gpt-6-sol", "openrouter/openai/gpt-6.1-sol"}
        for r in policy.routes.values()
    )
    sonnet = policy.routes["sonnet-vertex-global"]
    assert sonnet.model == "openrouter/anthropic/claude-sonnet-5.5"
    assert sonnet.review.reviewed_at == REVIEWED
    assert all(r.model != "openrouter/anthropic/claude-sonnet-5" for r in policy.routes.values())
    for route in policy.routes.values():
        if route.route_id == "memory-decomposition-luna-azure-eu":
            assert not route.approved and route.approval_mode == "expiring"
            assert "not_approved" in route.rejection_reasons(policy.requirements, now=REVIEWED)
            continue
        assert route.is_approved(policy.requirements, now=REVIEWED)
        provider = route.transport_payload(policy.requirements, now=REVIEWED)["extra_body"][
            "provider"
        ]
        assert provider["only"] == provider["order"]
        assert len(provider["only"]) == 1
        assert provider["allow_fallbacks"] is False
        assert provider["require_parameters"] is True
        assert provider["data_collection"] == "deny"
        assert provider["zdr"] is True
        # Monitored: no calendar expiry, a ZDR baseline, and fail-closed attestation.
        assert route.approval_mode == "monitored"
        assert route.approval_expires_at is None
        assert route.review.review_expires_at is None
        assert route.zdr_baseline is not None
        assert route.zdr_baseline.data_policy["training"] is False
        assert route.rejection_reasons(policy.requirements, now=STALE) == ("zdr_attestation_stale",)
        with pytest.raises(RouteNotApproved):
            route.transport_payload(policy.requirements, now=STALE)
        assert route.supports(
            required_capabilities=frozenset({"text", "tools", "json_schema"}),
            input_tokens=route.max_context_tokens - route.max_output_tokens,
            output_tokens=route.max_output_tokens,
        )
        assert not route.supports(
            required_capabilities=frozenset({"text"}),
            input_tokens=route.max_context_tokens - route.max_output_tokens + 1,
            output_tokens=route.max_output_tokens,
        )


def test_approved_deployment_resolves_profiles_and_denies_excluded_models(monkeypatch) -> None:
    from orchestrator import compute_runtime
    from orchestrator.entitlements import policy as policy_module

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return REVIEWED

    monkeypatch.setattr(policy_module, "datetime", ReviewClock)
    deployment = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.production.json").read_text())
    )
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: deployment)
    for profile in ("routine", "background", "research"):
        assert compute_runtime.choose_route(profile=profile).route_id == "luna-azure"
    assert compute_runtime.choose_route(profile="reasoning").route_id == "sonnet-vertex-global"
    assert compute_runtime.choose_route(DEEPSEEK_FLASH).route_id == DEEPSEEK_ROUTE
    assert (
        compute_runtime.choose_route("openrouter/x-ai/grok-4.7").route_id == "grok-bedrock-global"
    )
    for model in (
        "openrouter/deepseek/deepseek-v4.1-flash",
        "openrouter/openai/gpt-6-sol",
        "openrouter/anthropic/claude-sonnet-5",
        "openrouter/qwen/qwen3.8-max-0902",
        "openrouter/google/gemini-3.8-flash",
        "openrouter/z-ai/glm-5.3",
    ):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            compute_runtime.choose_route(model)


def test_unknown_or_revoked_attestation_closes_every_route() -> None:
    policy = _production()
    attestation.set_snapshot(attestation.AttestationSnapshot())
    assert not any(r.is_approved(policy.requirements, now=REVIEWED) for r in policy.routes.values())
    attestation.set_snapshot(_attested_snapshot(revoked=True))
    for route in policy.routes.values():
        if route.route_id == "memory-decomposition-luna-azure-eu":
            assert "not_approved" in route.rejection_reasons(policy.requirements, now=REVIEWED)
            continue
        assert route.rejection_reasons(policy.requirements, now=REVIEWED) == (
            "zdr_attestation_revoked",
        )


REPINNED_BY_747 = {
    # route_id: (model, exact pin, (prompt, completion) microUSD per million)
    "luna-azure": ("openrouter/openai/gpt-6-luna", "azure", (220_000, 825_000)),
    "sol-azure": ("openrouter/openai/gpt-6.2-sol", "azure", (4_400_000, 16_500_000)),
    "astra-azure": ("openrouter/openai/gpt-6.2-astra", "azure", (17_600_000, 66_000_000)),
    "sonnet-vertex-global": (
        "openrouter/anthropic/claude-sonnet-5.5",
        "google-vertex/global",
        (2_200_000, 11_000_000),
    ),
    "opus-vertex-global": (
        "openrouter/anthropic/claude-opus-5.5",
        "google-vertex/global",
        (4_400_000, 22_000_000),
    ),
    "grok-bedrock-global": (
        "openrouter/x-ai/grok-4.7",
        "amazon-bedrock/global",
        (4_400_000, 13_200_000),
    ),
}


@pytest.mark.parametrize("route_id", sorted(REPINNED_BY_747))
def test_premium_repins_pin_exact_global_endpoints_with_operator_caps(route_id: str) -> None:
    # #747: the withdrawn regional pins are replaced by the remaining ZDR
    # endpoints under a geographic exception; no substitution or fallback.
    model, pin, (prompt, completion) = REPINNED_BY_747[route_id]
    policy = _production()
    route = policy.routes[route_id]
    assert route.model == model
    assert route.review.reviewed_at == REVIEWED
    assert "docs/INFERENCE_ROUTE_APPROVALS.md#premium-re-pins--19-november-2026" in (
        route.review.evidence
    )
    assert route.zdr_baseline is not None
    assert route.zdr_baseline.provider_slug == pin.split("/")[0]
    assert route.price_ceiling is not None
    assert route.estimate_microusd(1_000_000, 1_000_000) == prompt + completion
    provider = route.transport_payload(policy.requirements)["extra_body"]["provider"]
    assert provider["only"] == provider["order"] == [pin]
    assert provider["allow_fallbacks"] is False
    assert provider["zdr"] is True
    assert provider["max_price"] == {
        "prompt": prompt / 1_000_000,
        "completion": completion / 1_000_000,
    }


def test_withdrawn_regional_route_ids_and_models_are_gone() -> None:
    policy = _production()
    for old in (
        "luna-azure-eu",
        "sol-azure-eu",
        "astra-azure-us",
        "sonnet-vertex-europe",
        "opus-vertex-europe",
        "grok-zdr-us",
    ):
        assert old not in policy.routes
    models = {route.model for route in policy.routes.values()}
    assert not models & {"openrouter/openai/gpt-6.1-sol", "openrouter/openai/gpt-6-astra"}
    regional = {"azure/eu", "azure/us", "google-vertex/europe", "xai/zdr/us"}
    for route in policy.routes.values():
        if route.approved:
            assert not set(route.transport.provider_only or ()) & regional


def test_no_route_is_approved_with_known_provider_retention() -> None:
    # #747: Grok left xai/zdr/us (whose ordinary xAI policy reports 30-day
    # retention) for Bedrock global, so every baseline now reports none.
    baselines = {
        route.route_id: dict(route.zdr_baseline.data_policy)
        for route in _production().routes.values()
        if route.zdr_baseline is not None
    }
    grok = _production().routes["grok-bedrock-global"]
    assert grok.zdr_baseline is not None
    assert grok.zdr_baseline.provider_slug == "amazon-bedrock"
    assert grok.transport_payload(_production().requirements)["extra_body"]["provider"]["only"] == [
        "amazon-bedrock/global"
    ]
    assert all(
        policy == {"training": False, "retainsPrompts": False} for policy in baselines.values()
    )
