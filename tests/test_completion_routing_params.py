"""Completion adapters must leave model-specific options to final dispatch."""

from __future__ import annotations

import pytest

from orchestrator.config import Settings
from orchestrator.compute_runtime import _request_bound
from orchestrator.tools.completion import _prepare_call_params


@pytest.mark.parametrize(
    "hint",
    ["auto", "openrouter/google/gemini-3.8-flash", "openrouter/anthropic/claude-sonnet-5"],
)
def test_preselection_does_not_leak_model_specific_parameters(hint: str) -> None:
    settings = Settings(openrouter_api_key="test-only")
    params = _prepare_call_params(
        settings,
        settings.get_provider_config(),
        [{"role": "user", "content": "hello"}],
        actual_model=hint,
    )
    assert _request_bound(params).bound > 0
    assert not {"reasoning", "reasoning_effort", "verbosity", "temperature"} & params.keys()
