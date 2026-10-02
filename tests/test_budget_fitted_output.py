"""Output fitted to the remaining budget instead of refusing (optional work O2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from test_compute_runtime import _route, routing_document
from test_model_routing import FLASH, accepted_single, dispatch_fixture, last_kwargs, named_route

MESSAGES = [{"role": "user", "content": "hello"}]


def _input_bound() -> int:
    return runtime._request_bound({"messages": MESSAGES}).bound  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("prompt_price", "completion_price", "remaining"),
    [(1_000_000, 1_000_000, 5_000), (333_333, 777_777, 4_321), (2_200_000, 11_000_000, 90_000)],
)
def test_fitted_output_is_the_largest_whose_hold_fits(
    prompt_price: int, completion_price: int, remaining: int
) -> None:
    route: Any = _route(model=FLASH, input_price=prompt_price, output_price=completion_price)
    input_bound = 600
    fit = runtime._budget_fitted_output(route, input_bound, remaining, 10**9)  # pyright: ignore[reportPrivateUsage]
    assert fit > 0
    assert route.estimate_microusd(input_bound, fit) <= remaining
    assert route.estimate_microusd(input_bound, fit + 1) > remaining


def test_fitted_output_never_exceeds_the_ceiling_or_goes_negative() -> None:
    route: Any = _route(model=FLASH)
    assert runtime._budget_fitted_output(route, 600, 10**9, 2_048) == 2_048  # pyright: ignore[reportPrivateUsage]
    assert runtime._budget_fitted_output(route, 600, 100, 2_048) == 0  # pyright: ignore[reportPrivateUsage]
    unpriced = SimpleNamespace(price_ceiling=None)
    assert runtime._budget_fitted_output(unpriced, 600, 10**9, 2_048) == 0  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_automatic_request_is_fitted_to_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH, price=1_000_000)
    budget = 5_000
    with dispatch_fixture(monkeypatch, [route], routing=accepted_single(FLASH), budget=budget) as (
        service,
        provider,
        _scope,
    ):
        with model_routing.routing_context("routine") as state:
            await runtime.guarded_completion(messages=MESSAGES)
    expected = runtime._budget_fitted_output(route, _input_bound(), budget, 16_384)  # pyright: ignore[reportPrivateUsage]
    assert 0 < expected < 16_384
    assert last_kwargs(provider)["max_tokens"] == expected
    assert service.reserve.await_args.args[1] <= budget
    assert state.selected_budget_fitted is True


@pytest.mark.asyncio
async def test_full_size_request_is_not_marked_fitted(monkeypatch: pytest.MonkeyPatch) -> None:
    with dispatch_fixture(monkeypatch, [named_route(FLASH)], routing=accepted_single(FLASH)) as (
        _service,
        provider,
        _scope,
    ):
        with model_routing.routing_context("routine") as state:
            await runtime.guarded_completion(messages=MESSAGES)
    assert last_kwargs(provider)["max_tokens"] == 16_384
    assert state.selected_budget_fitted is False


@pytest.mark.asyncio
async def test_fitted_output_below_the_profile_floor_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH, price=1_000_000)
    with dispatch_fixture(
        monkeypatch,
        [route],
        routing=routing_document([FLASH], min_output_tokens=512),
        budget=_input_bound() + 100,
    ) as (service, provider, _scope):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable) as refused:
                await runtime.guarded_completion(messages=MESSAGES)
        assert refused.value.code == "budget_exceeded"
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_model_without_an_output_limit_is_fitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH, price=1_000_000)
    with dispatch_fixture(
        monkeypatch, [route], routing=accepted_single(FLASH), budget=5_000, auto_route=False
    ) as (_service, provider, _scope):
        with model_routing.routing_context("routine") as state:
            await runtime.guarded_completion(model=FLASH, messages=MESSAGES)
    assert last_kwargs(provider)["model"] == FLASH
    assert last_kwargs(provider)["max_tokens"] < 16_384
    assert state.selected_budget_fitted is True


@pytest.mark.asyncio
async def test_a_caller_output_limit_stays_exact_and_is_refused_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = named_route(FLASH, price=1_000_000)
    with dispatch_fixture(
        monkeypatch, [route], routing=accepted_single(FLASH), budget=5_000, auto_route=False
    ) as (service, provider, _scope):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable) as refused:
                await runtime.guarded_completion(model=FLASH, messages=MESSAGES, max_tokens=10_000)
        assert refused.value.code == "budget_exceeded"
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()


def test_callers_that_do_not_opt_in_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluation scripts call the pricing helper without fit_to_budget."""
    route = named_route(FLASH, price=1_000_000)
    with dispatch_fixture(monkeypatch, [route], routing=accepted_single(FLASH), budget=5_000) as (
        _service,
        _provider,
        _scope,
    ):
        with model_routing.routing_context("routine"):
            policy = SimpleNamespace(
                capabilities={"chat"},
                limits=SimpleNamespace(max_context_tokens=32_000, max_output_tokens=16_384),
                limits_for=lambda _premium: SimpleNamespace(
                    max_context_tokens=32_000, max_output_tokens=16_384
                ),
                remaining_for=lambda _premium: 5_000,
            )
            size = runtime._request_bound({"messages": MESSAGES})  # pyright: ignore[reportPrivateUsage]
            assert runtime._priced_candidates(policy, size, {"messages": MESSAGES}, None) == []  # pyright: ignore[reportPrivateUsage]
            fitted: set[str] = set()
            offered = runtime._priced_candidates(  # pyright: ignore[reportPrivateUsage]
                policy, size, {"messages": MESSAGES}, None, fit_to_budget=True, fitted=fitted
            )
    assert len(offered) == 1
    assert fitted == {route.route_id}
