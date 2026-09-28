"""Malformed manual selections fail through the existing unavailable contract."""

import pytest

from orchestrator.compute_runtime import ComputeUnavailable, choose_route, guarded_completion
from test_model_routing import LUNA, dispatch_fixture, named_route


@pytest.mark.parametrize("model", ["   ", "openrouter/", "///"])
def test_invalid_pin_admission_is_typed_unavailable(model):
    with pytest.raises(ComputeUnavailable, match="Approved inference route unavailable"):
        choose_route(model)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["   ", "openrouter/", "///"])
async def test_invalid_internal_exact_pin_never_reserves(monkeypatch, model):
    with dispatch_fixture(monkeypatch, [named_route(LUNA)], auto_route=True) as (
        service,
        provider,
        _scope,
    ):
        with pytest.raises(ComputeUnavailable):
            await guarded_completion(
                model=model,
                _exact_model=True,
                messages=[{"role": "user", "content": "Hello"}],
            )
    service.reserve.assert_not_awaited()
    provider.assert_not_awaited()
