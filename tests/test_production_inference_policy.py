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

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = datetime(2026, 10, 3, tzinfo=timezone.utc)
STALE = REVIEWED + attestation.STALE_AFTER + timedelta(minutes=1)
GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"
GLM_ROUTE = "glm-flash-inceptron-fp8"


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
    attestation.set_snapshot(_attested_snapshot())
    yield
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
    assert route.price_ceiling.microusd_per_1m_completion == 450000
    assert route.estimate_microusd(1_000_000, 0) == 225000
    assert route.estimate_microusd(0, 1_000_000) == 450000
    assert route.transport_payload(production_policy.requirements)["extra_body"]["provider"] == {
        "only": ["inceptron/fp8"],
        "order": ["inceptron/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "max_price": {"prompt": 0.225, "completion": 0.45},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["routine", "council"])
async def test_glm_flash_existing_selection_sends_production_caps(mocked_dispatch, profile):
    service, provider = mocked_dispatch
    with model_routing.routing_context(profile, preferred_model=GLM_FLASH) as state:
        await compute_runtime.guarded_completion(
            model=GLM_FLASH if profile == "routine" else None,
            messages=[{"role": "user", "content": "fixture"}],
            max_tokens=4096,
        )
        assert state.selected_model == GLM_FLASH
        assert state.selected_route_id == GLM_ROUTE
        assert state.selected_effort == ("low" if profile == "routine" else "high")
        if profile == "council":
            assert state.selected_group == "diverse"
    provider.assert_awaited_once()
    call = provider.await_args.kwargs
    assert call["model"] == GLM_FLASH
    assert call["api_base"] == "https://openrouter.ai/api/v1"
    assert call["extra_body"]["provider"]["only"] == ["inceptron/fp8"]
    assert call["extra_body"]["provider"]["allow_fallbacks"] is False
    assert call["extra_body"]["provider"]["max_price"] == {"prompt": 0.225, "completion": 0.45}
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
@pytest.mark.parametrize("profile", ["routine", "background", "research", "reasoning"])
async def test_glm_flash_not_added_as_automatic_fallback(
    monkeypatch, production_policy: InferencePolicy, mocked_dispatch, profile
):
    # Even with GLM Flash as the only approved route and ample account budget,
    # these profiles have no authorization to add it to their automatic groups.
    only_flash = replace(production_policy, routes={GLM_ROUTE: production_policy.routes[GLM_ROUTE]})
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: only_flash)
    service, provider = mocked_dispatch
    with model_routing.routing_context(profile):
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
    assert len(policy.routes) == 8
    assert policy.approved_tool_service_ids(now=REVIEWED) == {"brave-web-search"}
    assert not default.approved_tool_service_ids(now=REVIEWED)
    assert policy.default_route_id is None
    sol = policy.routes["sol-azure-eu"]
    assert sol.model == "openrouter/openai/gpt-6.1-sol"
    assert sol.review.reviewed_at == datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert all(r.model != "openrouter/openai/gpt-6-sol" for r in policy.routes.values())
    sonnet = policy.routes["sonnet-vertex-europe"]
    assert sonnet.model == "openrouter/anthropic/claude-sonnet-5.5"
    assert sonnet.review.reviewed_at == datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert all(r.model != "openrouter/anthropic/claude-sonnet-5" for r in policy.routes.values())
    for route in policy.routes.values():
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
        assert compute_runtime.choose_route(profile=profile).route_id == "luna-azure-eu"
    assert compute_runtime.choose_route(profile="reasoning").route_id == "sonnet-vertex-europe"
    assert (
        compute_runtime.choose_route("openrouter/deepseek/deepseek-v4.1-flash").route_id
        == "deepseek-flash-coreweave-fp8"
    )
    for model in (
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
        assert route.rejection_reasons(policy.requirements, now=REVIEWED) == (
            "zdr_attestation_revoked",
        )


def test_only_grok_was_approved_with_known_provider_retention() -> None:
    baselines = {
        route.route_id: dict(route.zdr_baseline.data_policy)
        for route in _production().routes.values()
        if route.zdr_baseline is not None
    }
    assert baselines.pop("grok-zdr-us") == {
        "training": False,
        "retainsPrompts": True,
        "retentionDays": 30,
    }
    assert all(
        policy == {"training": False, "retainsPrompts": False} for policy in baselines.values()
    )
