"""Benchmark extraction must stay exact inside automatic worker scopes."""

from unittest.mock import AsyncMock

import pytest

from orchestrator import model_routing
from orchestrator.memory.extraction import (
    BENCHMARK_EXTRACTION_MODEL,
    BENCHMARK_SEED,
    BenchmarkProviderError,
    extract_facts_from_text,
)
from test_background_sampling_compatibility import EXTRACTION_REPLY, TransportResponse
from test_model_routing import LUNA, PRODUCTION_ROUTING, dispatch_fixture, last_kwargs, named_route


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_route", [False, True])
async def test_worker_benchmark_uses_exact_dispatch_and_real_provenance(monkeypatch, auto_route):
    provider = AsyncMock(return_value=TransportResponse(EXTRACTION_REPLY))
    with dispatch_fixture(
        monkeypatch,
        [named_route(LUNA), named_route(BENCHMARK_EXTRACTION_MODEL)],
        routing=None,
        provider=provider,
        auto_route=auto_route,
    ) as (service, _, _scope):
        with model_routing.routing_context("background"):
            outcome = await extract_facts_from_text("I live in Adelaide", benchmark_mode=True)

    sent = last_kwargs(provider)
    assert sent["model"] == BENCHMARK_EXTRACTION_MODEL
    assert sent["seed"] == BENCHMARK_SEED
    assert sent["temperature"] == 0.0
    assert "_exact_model" not in sent
    assert outcome.model_used == sent["model"]
    assert provider.await_count == service.reserve.await_count == service.settle.await_count == 1


@pytest.mark.asyncio
async def test_unqualified_benchmark_never_substitutes_automatic_luna(monkeypatch):
    provider = AsyncMock(return_value=TransportResponse(EXTRACTION_REPLY))
    with dispatch_fixture(
        monkeypatch,
        [named_route(LUNA)],
        routing=None,
        provider=provider,
        auto_route=True,
    ) as (service, _, _scope):
        with model_routing.routing_context("background"):
            with pytest.raises(BenchmarkProviderError):
                await extract_facts_from_text("I live in Adelaide", benchmark_mode=True)

    provider.assert_not_awaited()
    service.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_benchmark_outcome_does_not_fabricate_dispatch_provenance(monkeypatch):
    from orchestrator.memory import extraction

    # A substituted/mocked boundary that supplies no dispatch provenance must
    # not be represented as a successfully served benchmark snapshot.
    monkeypatch.setattr(model_routing, "load_model_routing", lambda: PRODUCTION_ROUTING)
    monkeypatch.setattr(
        extraction,
        "guarded_completion",
        AsyncMock(return_value=TransportResponse(EXTRACTION_REPLY)),
    )
    outcome = await extract_facts_from_text("I live in Adelaide", benchmark_mode=True)
    assert outcome.model_used is None
