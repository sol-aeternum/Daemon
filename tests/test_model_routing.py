"""Regression contracts for workload suitability, bounded dispatch and attribution."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import PolicyError
from test_compute_runtime import _qualified_policy, _route, routing_document

FLASH = "openrouter/z-ai/glm-5.3-flash"
GLM = "openrouter/z-ai/glm-5.3"
DEEPSEEK = "openrouter/deepseek/deepseek-v4.1-flash"
GOOGLE = "openrouter/google/gemini-3.8-flash"
LUNA = "openrouter/openai/gpt-6-luna"
SOL = "openrouter/openai/gpt-6-sol"
SONNET = "openrouter/anthropic/claude-sonnet-5"
OPUS = "openrouter/anthropic/claude-opus-5.5"
ASTRA = "openrouter/openai/gpt-6-astra"
PRODUCTION_ROUTING = model_routing.load_model_routing()


def test_model_routing_loader_uses_validated_settings_and_explicit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator import config

    configured = tmp_path / "configured.json"
    explicit = tmp_path / "explicit.json"
    configured.write_text(json.dumps(routing_document(["openrouter/test/configured"])))
    explicit.write_text(json.dumps(routing_document(["openrouter/test/explicit"])))
    monkeypatch.setenv(model_routing.MODEL_ROUTING_ENV, str(tmp_path / "wrong.json"))
    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(daemon_model_routing=str(configured))
    )
    assert "openrouter/test/configured" in model_routing.load_model_routing().models
    assert "openrouter/test/explicit" in model_routing.load_model_routing(explicit).models


def last_kwargs(provider: AsyncMock) -> dict[str, Any]:
    assert provider.await_args is not None
    return dict(provider.await_args.kwargs)


class TrackedStream:
    def __init__(self, *, fail_before_output: bool = False):
        self.fail_before_output = fail_before_output
        self.sent = False
        self.aclose = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.fail_before_output:
            raise RuntimeError("upstream disconnected")
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return {"choices": [{"delta": {"content": "answer"}}]}


@contextmanager
def dispatch_fixture(
    monkeypatch: pytest.MonkeyPatch,
    routes: list[Any],
    *,
    routing: dict[str, Any] | None = None,
    capabilities: set[str] | None = None,
    budget: int = 1_000_000,
    max_output: int = 16_384,
    auto_route: bool = True,
    provider: AsyncMock | None = None,
):
    _qualified_policy(monkeypatch, route=routes, routing=routing)
    if routing is None:
        monkeypatch.setattr(
            model_routing, "load_model_routing", lambda *a, **kw: PRODUCTION_ROUTING
        )
    limits = SimpleNamespace(max_context_tokens=32_000, max_output_tokens=max_output)
    policy = SimpleNamespace(
        capabilities={"chat", "premium_routing"} if capabilities is None else capabilities,
        limits=limits,
        limits_for=lambda premium: limits,
        remaining_for=lambda premium: budget,
    )
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=policy),
        reserve=AsyncMock(side_effect=[object() for _ in range(20)]),
        settle=AsyncMock(),
    )
    provider = provider or AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    scope = runtime.ComputeScope(
        uuid.uuid4(), cast(EntitlementService, service), auto_route=auto_route
    )
    token = runtime._scope.set(scope)
    try:
        yield service, provider, scope
    finally:
        runtime._scope.reset(token)


def named_route(model: str, *, price: int = 1_000, premium: bool = False) -> Any:
    route = _route(model=model, input_price=price)
    route.route_id = model
    route.route_class = "premium" if premium else "routine"
    return route


def accepted_pair(first: str, second: str) -> dict[str, Any]:
    """A fixture whose own profiles accept exactly ``first`` then ``second``.

    Generic selector behaviour (price, output ceiling, failover, streaming
    lifecycle) is a property of the selector, not of the production shortlist, so
    these tests declare their own two-model profile instead of borrowing a
    production placement that a routing decision may legitimately change.
    """
    return routing_document([first, second])


def accepted_single(model: str) -> dict[str, Any]:
    """A fixture whose profiles accept exactly one model."""
    return routing_document([model])


def seed_only_routing(models: list[str], *, restricted: str) -> dict[str, Any]:
    """A fixture in which ``restricted`` declares only the seed sampling parameter.

    The shared document helper declares every sampling parameter for every model,
    which would make a per-model incompatibility untestable, so the narrower
    declaration is written explicitly.
    """
    document = routing_document(models)
    for entry in document["models"]:
        if entry["model"] == restricted:
            entry["sampling_parameters"] = ["seed"]
    return document


def test_production_profiles_are_distinct_and_optional_effort_does_not_imply_suitability() -> None:
    config = model_routing.load_model_routing()
    assert (
        config.profile("background").ranked_models() != config.profile("reasoning").ranked_models()
    )
    assert (
        config.profile("research").min_output_tokens
        > config.profile("background").min_output_tokens
    )
    assert config.profile("reasoning").group_names() == ("demanding", "escalation")
    assert model_routing.supports_reasoning_effort(FLASH, "high")
    assert model_routing.profile_candidate("reasoning", FLASH) is None
    # Declared suitability is not a placement: an effort ladder the model supports
    # says nothing about whether the profile may choose it.
    assert model_routing.profile_candidate("background", OPUS) is None
    with pytest.raises(model_routing.RoutingError, match="unknown workload profile"):
        with model_routing.routing_context("made-up"):
            pass


def test_approved_luna_first_placement_is_the_only_automatic_candidate() -> None:
    """The approved automatic shortlist is exactly one Luna per routine profile.

    The placement is a real, narrow measurement rather than a price ranking, so the
    contract worth locking is that the other measured candidates are *not* silently
    reinstated: an unplaced model is unreachable automatically however cheap or
    qualified it is, and the escalation ladders for demanding work stay where they
    were.
    """
    config = model_routing.load_model_routing()
    # Still provisional: a bounded screen is not a general qualification.
    assert config.provisional is True
    for name, floor, premium in [
        ("routine", 1024, False),
        ("background", 512, False),
        ("research", 2048, True),
    ]:
        profile = config.profile(name)
        assert profile.ranked_models() == (LUNA,), name
        assert profile.group_names() == ("luna-low",), name
        # The comparable output floor and the account contract are unchanged.
        assert profile.min_output_tokens == floor, name
        assert profile.allow_premium is premium, name
        for unplaced in (DEEPSEEK, FLASH, GOOGLE, GLM, SOL, SONNET, OPUS, ASTRA):
            assert model_routing.profile_candidate(name, unplaced) is None, (name, unplaced)
    # Luna is served at the effort the accepted screen actually measured.
    for name in ("routine", "background", "research"):
        assert model_routing.model_parameter_presets(LUNA, name) == {"reasoning_effort": "low"}
    # Demanding and council placements are untouched: bounded escalation survives.
    assert config.profile("reasoning").group_names() == ("demanding", "escalation")
    assert config.profile("council").group_names() == ("diverse", "escalation")
    assert config.profile("reasoning").ranked_models() == (
        GLM,
        "openrouter/qwen/qwen3.8-max-0902",
        SOL,
        SONNET,
        OPUS,
        ASTRA,
    )
    assert config.profile("council").groups[1].models == (OPUS, ASTRA)
    assert set(config.profile("council").groups[0].models) >= {DEEPSEEK, FLASH, LUNA, GLM, SOL}
    # An unplaced model is unreachable automatically but still dispatchable by an
    # exact manual selection, so removing a group never removes the model.
    assert model_routing.is_model_routable("routine", SOL) is False
    assert model_routing.is_model_routable("council", SOL) is True


@pytest.mark.asyncio
async def test_cheaper_qualified_route_never_displaces_the_accepted_luna_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cheapness is not a reason to switch models, in either direction.

    A cheaper *and* fully qualified route is available in the same account, yet
    routine, background and research all dispatch Luna, because it is the only
    accepted candidate rather than merely the lowest-priced one. Pinning a cheaper
    model must be an explicit caller decision.
    """
    for profile in ("routine", "background", "research"):
        cheap = named_route(DEEPSEEK, price=0)
        luna = named_route(LUNA, price=1_000)
        with dispatch_fixture(monkeypatch, [cheap, luna]) as (service, provider, _):
            with model_routing.routing_context(profile) as state:
                await runtime.guarded_completion(messages=[{"role": "user", "content": "hello"}])
                assert (state.selected_model, state.selected_route_id) == (LUNA, LUNA)
                assert state.selected_group == "luna-low"
            assert last_kwargs(provider)["model"] == LUNA
            assert [call.kwargs["model"] for call in service.reserve.await_args_list] == [LUNA]
            assert last_kwargs(provider)["reasoning_effort"] == "low"


@pytest.mark.asyncio
@pytest.mark.parametrize("alternate", [SOL, DEEPSEEK, OPUS])
async def test_unavailable_luna_denies_truthfully_instead_of_substituting_an_alternate(
    monkeypatch: pytest.MonkeyPatch, alternate: str
) -> None:
    """No accepted automatic alternative means a refusal, not a quiet swap.

    Each alternate is an approved, routine-class, fully capable route with budget
    behind it, so the *only* reason it cannot serve is that the profile does not
    accept it automatically. A routine-class Sol in particular is not a premium
    route and needs no premium capability or funding, so it must not be mistaken
    for an escalation the account refused.
    """
    fallback = named_route(alternate, price=0)
    with dispatch_fixture(monkeypatch, [fallback]) as (service, provider, _):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable) as denied:
                await runtime.guarded_completion(messages=[{"role": "user", "content": "hello"}])
    assert denied.value.code == "route_unavailable"
    assert denied.value.retryable is False
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_pin_outside_the_luna_shortlist_stays_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Narrowing the automatic shortlist must not narrow what a caller may ask for.

    The same qualified Sol route that automatic routine work refuses is dispatched
    exactly when it is pinned, and it is dispatched as Sol rather than quietly
    rewritten to the profile's accepted candidate.
    """
    luna = named_route(LUNA, price=0)
    sol = named_route(SOL, price=1_000)
    with dispatch_fixture(monkeypatch, [luna, sol], auto_route=False) as (service, provider, _):
        with model_routing.routing_context("routine") as state:
            await runtime.guarded_completion(
                model=SOL, messages=[{"role": "user", "content": "explain"}]
            )
            assert (state.selected_model, state.selected_route_id) == (SOL, SOL)
            assert state.explicit is True
            assert state.selected_group is None
        assert last_kwargs(provider)["model"] == SOL
        assert [call.kwargs["model"] for call in service.reserve.await_args_list] == [SOL]
    # Luna was qualified, cheaper and the profile's only accepted candidate, and
    # still was not dispatched for a pinned request.
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_demanding_work_never_downgrades_to_cheaper_flash_with_same_protocol_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flash = named_route(FLASH, price=0)
    glm = named_route(GLM, price=1_000)
    with dispatch_fixture(monkeypatch, [flash, glm]) as (service, provider, _):
        with model_routing.routing_context("reasoning") as state:
            await runtime.guarded_completion(messages=[{"role": "user", "content": "prove it"}])
            assert (state.selected_model, state.selected_route_id, state.selected_group) == (
                GLM,
                GLM,
                "demanding",
            )
        assert last_kwargs(provider)["model"] == GLM
        assert service.reserve.await_args.kwargs["model"] == GLM


@pytest.mark.asyncio
async def test_council_exclusion_and_soft_preference_stay_within_eligible_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deepseek = named_route(DEEPSEEK, price=0)
    flash = named_route(FLASH, price=1_000)
    opus = named_route(OPUS, price=0)
    with dispatch_fixture(monkeypatch, [deepseek, flash, opus]) as (service, provider, _):
        with model_routing.routing_context(
            "council",
            preferred_model=OPUS,
            excluded_developers=frozenset({"deepseek"}),
        ) as state:
            await runtime.guarded_completion(messages=[{"role": "user", "content": "review"}])
            assert state.selected_model == FLASH
            assert state.selected_group == "diverse"
        assert last_kwargs(provider)["model"] == FLASH
        assert service.reserve.await_args.kwargs["model"] == FLASH


@pytest.mark.asyncio
async def test_premium_auto_escalation_requires_capability_and_budget_without_cheap_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flash = named_route(FLASH, price=0)
    premium = named_route(OPUS, price=1_000, premium=True)
    for capabilities, budget, admitted in [
        ({"chat", "premium_routing"}, 100_000, True),
        ({"chat"}, 100_000, False),
        ({"chat", "premium_routing"}, 0, False),
    ]:
        with dispatch_fixture(
            monkeypatch, [flash, premium], capabilities=capabilities, budget=budget
        ) as (service, provider, _):
            with model_routing.routing_context("reasoning"):
                if admitted:
                    await runtime.guarded_completion(messages=[{"role": "user", "content": "hard"}])
                    assert last_kwargs(provider)["model"] == OPUS
                    assert service.reserve.await_args.kwargs["premium"] is True
                else:
                    with pytest.raises(runtime.ComputeUnavailable):
                        await runtime.guarded_completion(
                            messages=[{"role": "user", "content": "hard"}]
                        )
                    service.reserve.assert_not_awaited()
                    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_model_outside_profile_stays_exact_but_never_bypasses_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flash = named_route(FLASH, price=0)
    premium = named_route(OPUS, price=1_000, premium=True)
    with dispatch_fixture(monkeypatch, [flash, premium], auto_route=False) as (
        service,
        provider,
        _,
    ):
        with model_routing.routing_context("background") as state:
            await runtime.guarded_completion(
                model=OPUS, messages=[{"role": "user", "content": "explicit"}]
            )
            assert state.selected_model == OPUS
            assert state.explicit is True
            assert state.selected_group is None
        assert last_kwargs(provider)["model"] == OPUS
        assert service.reserve.await_args.kwargs["premium"] is True
    with dispatch_fixture(
        monkeypatch, [flash, premium], auto_route=False, capabilities={"chat"}
    ) as (service, provider, _):
        with model_routing.routing_context("background"):
            with pytest.raises(runtime.ComputeUnavailable) as denied:
                await runtime.guarded_completion(
                    model=OPUS, messages=[{"role": "user", "content": "explicit"}]
                )
        assert denied.value.code == "capability_unavailable"
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_unapproved_or_incompatible_model_does_not_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flash = named_route(FLASH, price=0)
    flash.supports = lambda *, required_capabilities, input_tokens, output_tokens: (
        required_capabilities <= {"text"}
    )
    with dispatch_fixture(monkeypatch, [flash], auto_route=False) as (service, provider, _):
        with model_routing.routing_context("background"):
            for model, extra in [
                ("openrouter/test/unapproved", {}),
                (FLASH, {"tools": [{"type": "function", "function": {"name": "clock"}}]}),
            ]:
                with pytest.raises(runtime.ComputeUnavailable):
                    await runtime.guarded_completion(
                        model=model, messages=[{"role": "user", "content": "hello"}], **extra
                    )
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_output_target_excludes_cheaper_model_with_inadequate_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = named_route(DEEPSEEK, price=0)
    short.max_output_tokens = 511
    long = named_route(FLASH, price=1_000)
    with dispatch_fixture(
        monkeypatch,
        [short, long],
        routing=routing_document([DEEPSEEK, FLASH], min_output_tokens=512),
    ) as (service, provider, _):
        with model_routing.routing_context("background"):
            await runtime.guarded_completion(messages=[{"role": "user", "content": "title"}])
        assert last_kwargs(provider)["model"] == FLASH
        assert last_kwargs(provider)["max_tokens"] >= 512
        assert service.reserve.await_args.kwargs["model"] == FLASH


@pytest.mark.asyncio
async def test_automatic_output_uses_shared_account_target_not_profile_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = named_route(DEEPSEEK, price=0)
    cheap.max_output_tokens = 512  # Meets floor, but not this account's output target.
    sufficient = named_route(FLASH, price=1_000)
    with dispatch_fixture(
        monkeypatch,
        [cheap, sufficient],
        routing=routing_document([DEEPSEEK, FLASH], min_output_tokens=512),
        max_output=2048,
    ) as (service, provider, _):
        with model_routing.routing_context("background"):
            await runtime.guarded_completion(messages=[{"role": "user", "content": "title"}])
        assert last_kwargs(provider)["model"] == FLASH
        assert last_kwargs(provider)["max_tokens"] == 2048
        assert service.reserve.await_args.kwargs["model"] == FLASH


@pytest.mark.asyncio
async def test_automatic_account_context_bounds_target_for_every_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A trial overlay can permit a larger premium route, but ordinary automatic
    # work compares both classes against the base account's context-bound target.
    routine = named_route(DEEPSEEK, price=0)
    premium = named_route(OPUS, price=1_000, premium=True)
    routing = routing_document([DEEPSEEK, OPUS], min_output_tokens=512)
    _qualified_policy(monkeypatch, route=[routine, premium], routing=routing)
    base = SimpleNamespace(max_context_tokens=1024, max_output_tokens=4096)
    overlay = SimpleNamespace(max_context_tokens=32000, max_output_tokens=8000)
    policy = SimpleNamespace(
        capabilities={"chat", "premium_routing"},
        limits=base,
        limits_for=lambda premium: overlay if premium else base,
        remaining_for=lambda premium: 1_000_000,
    )
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=policy),
        reserve=AsyncMock(return_value="hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    scope = runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service), auto_route=True)
    token = runtime._scope.set(scope)
    try:
        with model_routing.routing_context("reasoning"):
            message = [{"role": "user", "content": "hello"}]
            expected = (
                base.max_context_tokens - runtime._request_bound({"messages": message}).estimate
            )
            await runtime.guarded_completion(messages=message)
    finally:
        runtime._scope.reset(token)
    assert last_kwargs(provider)["model"] == routine.model
    assert last_kwargs(provider)["max_tokens"] == expected
    assert service.reserve.await_args.args[1] == routine.estimate_microusd(
        runtime._request_bound({"messages": message}).bound, expected
    )


@pytest.mark.asyncio
async def test_automatic_floor_unmet_denies_instead_of_shrinking_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH)
    with dispatch_fixture(
        monkeypatch,
        [route],
        routing=routing_document([FLASH], min_output_tokens=512),
        max_output=256,
    ) as (service, provider, _):
        with model_routing.routing_context("background"):
            with pytest.raises(runtime.ComputeUnavailable) as denied:
                await runtime.guarded_completion(messages=[{"role": "user", "content": "title"}])
        assert denied.value.code == "capacity_unavailable"
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_output_limit_stays_exact_even_below_profile_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH)
    route.max_output_tokens = 24
    with dispatch_fixture(
        monkeypatch, [route], routing=routing_document([FLASH], min_output_tokens=512)
    ) as (service, provider, _):
        with model_routing.routing_context("background"):
            await runtime.guarded_completion(
                messages=[{"role": "user", "content": "title"}], max_tokens=24
            )
        assert last_kwargs(provider)["max_tokens"] == 24
        service.reserve.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("exclude", ["model", "developer"])
async def test_explicit_model_cannot_bypass_context_exclusions(
    monkeypatch: pytest.MonkeyPatch, exclude: str
) -> None:
    # Sol is absent from routine's shortlist but has a qualified route. Exclusion
    # applies to manual selection and the internal exact-model seam alike.
    sol = named_route(SOL)
    with dispatch_fixture(monkeypatch, [sol], auto_route=False) as (service, provider, scope):
        options: dict[str, Any] = (
            {"excluded_models": frozenset({SOL})}
            if exclude == "model"
            else {"excluded_developers": frozenset({"openai"})}
        )
        with model_routing.routing_context("routine", **options):
            for exact in (False, True):
                scope.auto_route = exact
                with pytest.raises(runtime.ComputeUnavailable) as denied:
                    await runtime.guarded_completion(
                        model=SOL,
                        _exact_model=exact,
                        messages=[{"role": "user", "content": "review"}],
                    )
                assert denied.value.code == "route_unavailable"
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_nested_routing_cannot_enable_premium_disallowed_by_account_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    premium = named_route(OPUS, premium=True)
    _qualified_policy(monkeypatch, route=premium)
    limits = SimpleNamespace(max_context_tokens=32000, max_output_tokens=4096)
    service = SimpleNamespace(
        reconcile_expired_reservations=AsyncMock(return_value=0),
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities={"chat", "premium_routing"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )
        ),
        reserve=AsyncMock(return_value="hold"),
        settle=AsyncMock(),
    )
    provider = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    monkeypatch.setattr(runtime.litellm, "acompletion", provider)
    for account_profile in ("routine", "background"):
        async with runtime.account_compute(
            object(), uuid.uuid4(), profile=account_profile
        ) as scope:
            assert scope.account_allow_premium is False
            with model_routing.routing_context("reasoning"):
                with pytest.raises(runtime.ComputeUnavailable):
                    await runtime.guarded_completion(messages=[{"role": "user", "content": "hard"}])
                # Explicit premium still uses the independent account capability.
                await runtime.guarded_completion(
                    model=OPUS, messages=[{"role": "user", "content": "explicit"}]
                )
    assert service.reserve.await_count == 2
    assert provider.await_count == 2
    assert all(call.kwargs["premium"] for call in service.reserve.await_args_list)


@pytest.mark.asyncio
async def test_auto_scope_exact_model_pin_obeys_qualification_and_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    luna = named_route(LUNA, price=0)
    sol = named_route(SOL, price=1000)
    with dispatch_fixture(monkeypatch, [luna, sol]) as (service, provider, scope):
        with model_routing.routing_context("routine") as state:
            await runtime.guarded_completion(
                model=SOL, _exact_model=True, messages=[{"role": "user", "content": "benchmark"}]
            )
            assert (state.selected_model, state.explicit, runtime.selected_model()) == (
                SOL,
                True,
                SOL,
            )
            assert scope.selected_model == SOL
        assert last_kwargs(provider)["model"] == SOL
        assert "_exact_model" not in last_kwargs(provider)
        assert service.reserve.await_args.kwargs["model"] == SOL

    failed = AsyncMock(side_effect=RuntimeError("unavailable"))
    with dispatch_fixture(monkeypatch, [luna, sol], provider=failed) as (service, _, _):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable):
                await runtime.guarded_completion(
                    model=SOL,
                    _exact_model=True,
                    messages=[{"role": "user", "content": "benchmark"}],
                )
        assert service.reserve.await_count == 1
        assert failed.await_count == 1
        assert failed.await_args is not None
        assert failed.await_args.kwargs["model"] == SOL

    with dispatch_fixture(monkeypatch, [luna]) as (service, provider, _):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable):
                await runtime.guarded_completion(
                    model=SOL,
                    _exact_model=True,
                    messages=[{"role": "user", "content": "benchmark"}],
                )
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_short_title_output_is_not_blocked_by_background_profile_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = named_route(FLASH)
    short.max_output_tokens = 24
    with dispatch_fixture(monkeypatch, [short], auto_route=False) as (service, provider, _):
        with model_routing.routing_context("background"):
            await runtime.guarded_completion(
                model=FLASH, messages=[{"role": "user", "content": "title"}], max_tokens=24
            )
        assert last_kwargs(provider)["max_tokens"] == 24
        service.reserve.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_applies_selected_models_own_reasoning_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheap = named_route(DEEPSEEK, price=0)
    fallback = named_route(LUNA, price=1_000)
    provider = AsyncMock(side_effect=[RuntimeError("offline"), {"choices": []}])
    with dispatch_fixture(monkeypatch, [cheap, fallback], provider=provider) as (service, _, _):
        with model_routing.routing_context("council") as state:
            await runtime.guarded_completion(messages=[{"role": "user", "content": "review"}])
            assert (state.selected_model, state.selected_route_id) == (LUNA, LUNA)
        assert [call.kwargs["reasoning_effort"] for call in provider.await_args_list] == [
            "high",
            "medium",
        ]
        assert all(call.kwargs["include_reasoning"] is True for call in provider.await_args_list)
        assert [call.kwargs["model"] for call in service.reserve.await_args_list] == [
            DEEPSEEK,
            LUNA,
        ]


@pytest.mark.asyncio
async def test_sampling_incompatibility_is_filtered_before_reserving(monkeypatch) -> None:
    luna, flash = named_route(LUNA, price=0), named_route(FLASH, price=1_000)
    with dispatch_fixture(
        monkeypatch,
        [luna, flash],
        routing=seed_only_routing([LUNA, FLASH], restricted=LUNA),
    ) as (service, provider, scope):
        with model_routing.routing_context("background"):
            await runtime.guarded_completion(
                messages=[{"role": "user", "content": "Extract facts"}], temperature=0.1
            )
        assert last_kwargs(provider)["model"] == FLASH
        assert last_kwargs(provider)["temperature"] == 0.1
        assert service.reserve.await_count == 1
        provider.reset_mock()
        service.reserve.reset_mock()
        scope.auto_route = False
        with model_routing.routing_context("background"):
            with pytest.raises(runtime.ComputeUnavailable):
                await runtime.guarded_completion(
                    model=LUNA,
                    messages=[{"role": "user", "content": "Extract facts"}],
                    temperature=0.1,
                )
        provider.assert_not_awaited()
        service.reserve.assert_not_awaited()


def test_presets_validate_each_selected_model_ladder_and_preserve_caller_intent() -> None:
    assert model_routing.model_parameter_presets(GLM, "council")["reasoning_effort"] == "high"
    assert model_routing.model_parameter_presets(DEEPSEEK, "routine")["reasoning_effort"] == "low"
    assert model_routing.model_parameter_presets(GOOGLE, "council")["reasoning_effort"] == "high"
    assert (
        model_routing.apply_model_parameter_presets({"reasoning_effort": "max"}, GLM, "council")[
            "reasoning_effort"
        ]
        == "max"
    )
    with pytest.raises(model_routing.RoutingError) as denied:
        model_routing.apply_model_parameter_presets({"reasoning_effort": "medium"}, GLM)
    assert denied.value.code == "reasoning_effort_unsupported"
    assert model_routing.model_parameter_presets("openrouter/test/unplaced", "council") == {}


@pytest.mark.asyncio
async def test_preoutput_failover_closes_abandoned_stream_before_next_attempt(monkeypatch) -> None:
    failed, successful = TrackedStream(fail_before_output=True), TrackedStream()

    async def send(**kwargs):
        if kwargs["model"] == DEEPSEEK:
            return failed
        failed.aclose.assert_awaited_once()
        return successful

    routes = [named_route(DEEPSEEK, price=0), named_route(FLASH)]
    with dispatch_fixture(
        monkeypatch,
        routes,
        routing=accepted_pair(DEEPSEEK, FLASH),
        provider=AsyncMock(side_effect=send),
    ) as (
        service,
        _,
        _,
    ):
        with model_routing.routing_context("routine"):
            stream = await runtime.guarded_completion(
                messages=[{"role": "user", "content": "hello"}], stream=True
            )
            assert len([chunk async for chunk in stream]) == 1
        failed.aclose.assert_awaited_once()
        successful.aclose.assert_awaited_once()
        assert service.settle.await_count == 2


@pytest.mark.asyncio
async def test_scope_closes_returned_stream_even_when_never_started(monkeypatch) -> None:
    upstream = TrackedStream()
    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(return_value=upstream),
    ) as (service, _, scope):
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
        async with runtime.account_compute(object(), scope.user_id, auto_route=True):
            await runtime.guarded_completion(
                messages=[{"role": "user", "content": "hello"}], stream=True
            )
            upstream.aclose.assert_not_awaited()
        upstream.aclose.assert_awaited_once()
        service.settle.assert_awaited_once()
        assert service.settle.await_args.args[1] == service.reserve.await_args.args[1]


@pytest.mark.asyncio
async def test_hung_close_cannot_block_account_settlement(monkeypatch) -> None:
    release = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    async def stubborn_close():
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        finally:
            finished.set()

    upstream = TrackedStream()
    upstream.aclose = AsyncMock(side_effect=stubborn_close)
    monkeypatch.setattr(runtime, "STREAM_CLOSE_TIMEOUT_S", 0.01)
    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(return_value=upstream),
    ) as (service, _, scope):
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
        try:
            async with asyncio.timeout(1):
                async with runtime.account_compute(
                    object(), scope.user_id, auto_route=True
                ) as account:
                    await runtime.guarded_completion(
                        messages=[{"role": "user", "content": "hello"}], stream=True
                    )
            await asyncio.wait_for(cancelled.wait(), 1)
            assert not account.outstanding
            service.settle.assert_awaited_once()
            upstream.aclose.assert_awaited_once()
            assert service.settle.await_args.args[1] == service.reserve.await_args.args[1]
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 1)


@pytest.mark.asyncio
async def test_cancelled_consumer_and_scope_exit_close_and_settle_once(monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    upstream = TrackedStream()

    async def slow_close():
        entered.set()
        await release.wait()

    upstream.aclose = AsyncMock(side_effect=slow_close)
    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(return_value=upstream),
    ) as (service, _, scope):
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)

        async def consume():
            async with runtime.account_compute(object(), scope.user_id, auto_route=True):
                stream = await runtime.guarded_completion(
                    messages=[{"role": "user", "content": "hello"}], stream=True
                )
                async for _ in stream:
                    pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        service.settle.assert_awaited_once()
        upstream.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_awaits_every_hold_before_reporting_ledger_failure(monkeypatch) -> None:
    first, second = TrackedStream(), TrackedStream()
    settled = []

    async def settle(reservation, amount, **kwargs):
        settled.append(reservation)
        if len(settled) == 1:
            raise RuntimeError("ledger unavailable")

    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(side_effect=[first, second]),
    ) as (service, _, scope):
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        service.settle = AsyncMock(side_effect=settle)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
        with pytest.raises(runtime.ComputeUnavailable) as denied:
            async with runtime.account_compute(object(), scope.user_id, auto_route=True):
                for _ in range(2):
                    await runtime.guarded_completion(
                        messages=[{"role": "user", "content": "hello"}], stream=True
                    )
        assert denied.value.code == "settlement_failed"
        assert len(settled) == 2
        first.aclose.assert_awaited_once()
        second.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_incompatible_explicit_effort_is_not_reported_as_budget_exhaustion(
    monkeypatch,
) -> None:
    with dispatch_fixture(monkeypatch, [named_route(GLM)], auto_route=False) as (
        service,
        provider,
        _,
    ):
        with model_routing.routing_context("reasoning"):
            with pytest.raises(runtime.ComputeUnavailable) as denied:
                await runtime.guarded_completion(
                    model=GLM,
                    messages=[{"role": "user", "content": "reason"}],
                    reasoning_effort="medium",
                )
        assert denied.value.code == "capacity_unavailable"
        assert "reasoning effort" in denied.value.message
        provider.assert_not_awaited()
        service.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_contexts_and_nested_auto_helper_keep_own_model_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    luna, glm = named_route(LUNA, price=0), named_route(GLM, price=1_000)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def provider(**kwargs: Any) -> dict[str, Any]:
        if kwargs["model"] == GLM and kwargs["messages"][0]["content"] == "hard":
            entered.set()
            await release.wait()
        return {"choices": []}

    with dispatch_fixture(monkeypatch, [luna, glm], provider=AsyncMock(side_effect=provider)) as (
        service,
        _,
        scope,
    ):

        async def separate() -> tuple[str | None, str | None]:
            with model_routing.routing_context("reasoning") as state:
                await runtime.guarded_completion(messages=[{"role": "user", "content": "hard"}])
                return state.selected_model, state.selected_route_id

        with model_routing.routing_context("routine") as parent:
            # An explicit parent dispatch must not force an omitted-model nested
            # background helper to use the same model or create another allowance.
            scope.auto_route = False
            await runtime.guarded_completion(
                model=GLM, messages=[{"role": "user", "content": "parent"}]
            )
            child_task = asyncio.create_task(separate())
            await entered.wait()
            with model_routing.routing_context("background") as child:
                await runtime.guarded_completion(messages=[{"role": "user", "content": "helper"}])
                assert child.selected_model == LUNA
            assert parent.selected_model == GLM
            assert runtime.selected_model() == GLM
            release.set()
            assert await child_task == (GLM, GLM)
            assert parent.selected_model == GLM
        assert {call.kwargs["model"] for call in service.reserve.await_args_list} == {LUNA, GLM}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.update(version=2),
        lambda doc: doc["models"].append(copy.deepcopy(doc["models"][0])),
        lambda doc: doc["profiles"].append(copy.deepcopy(doc["profiles"][0])),
        lambda doc: doc["profiles"][0]["groups"][0]["models"].append("openrouter/test/unknown"),
        lambda doc: doc["models"][0]["parameter_presets"]["default"].update(
            reasoning_effort="medium"
        ),
        lambda doc: doc["models"][0]["parameter_presets"]["default"].update(api_base="https://bad"),
    ],
)
def test_invalid_routing_configuration_fails_closed(mutation: Any) -> None:
    doc = routing_document(["openrouter/test/valid"])
    doc["models"][0]["reasoning_efforts"] = ["low"]
    doc["models"][0]["parameter_presets"]["council"]["reasoning_effort"] = "low"
    mutation(doc)
    with pytest.raises(PolicyError):
        model_routing.parse_model_routing(doc)
