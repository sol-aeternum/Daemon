"""Dedicated decomposition placement never changes other workload presets."""

import copy
import json
from pathlib import Path

import pytest

from orchestrator import model_routing
from orchestrator.entitlements.errors import PolicyError

ROOT = Path(__file__).resolve().parents[1]
LUNA = "openrouter/openai/gpt-6-luna"


def test_profile_presets_and_existing_candidates(monkeypatch):
    raw = json.loads((ROOT / "config/model_routing.json").read_text())
    routing = model_routing.parse_model_routing(raw)
    monkeypatch.setattr(model_routing, "load_model_routing", lambda: routing)
    assert model_routing.model_parameter_presets(LUNA, "memory_decomposition") == {
        "reasoning_effort": "none"
    }
    for name in ("background", "routine", "research"):
        assert routing.profile(name).ranked_models() == (LUNA,)
        assert model_routing.model_parameter_presets(LUNA, name) == {"reasoning_effort": "medium"}
    assert routing.profile("reasoning").ranked_models() == (
        "openrouter/z-ai/glm-5.3",
        "openrouter/openai/gpt-6.1-sol",
        "openrouter/anthropic/claude-sonnet-5.5",
        "openrouter/anthropic/claude-opus-5.5",
        "openrouter/openai/gpt-6-astra",
    )
    assert routing.profile("memory_decomposition").ranked_models() == (LUNA,)
    missing = copy.deepcopy(raw)
    missing["profiles"] = [p for p in missing["profiles"] if p["profile"] != "memory_decomposition"]
    with pytest.raises(PolicyError, match="missing required profile"):
        model_routing.parse_model_routing(missing)
