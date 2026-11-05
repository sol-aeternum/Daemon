"""Dedicated decomposition placement never changes other workload presets."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator import config, compute_runtime, model_routing
from orchestrator.entitlements.errors import PolicyError
from tests.test_compute_runtime import _route

ROOT = Path(__file__).resolve().parents[1]
LUNA = "openrouter/openai/gpt-6-luna"


def legacy_routing_document():
    """Undo only M1's three additions to the shipped deployment metadata."""
    raw = json.loads((ROOT / "config/model_routing.json").read_text())
    raw["profiles"] = [p for p in raw["profiles"] if p["profile"] != "memory_decomposition"]
    for model in raw["models"]:
        model["suitability"] = [p for p in model["suitability"] if p != "memory_decomposition"]
        model["parameter_presets"].pop("memory_decomposition", None)
    return raw


def test_deployment_owned_pre_pr_routing_loads_and_selects_existing_profile(monkeypatch, tmp_path):
    raw = legacy_routing_document()
    # Semantic SHA-256 of origin/main:config/model_routing.json at this rebase.
    assert hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest() == (
        "8c99984a8079ac286221fe798a678dd192af09799a72442a603ee59fd2a2d99a"
    )
    path = tmp_path / "deployment-routing.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("DAEMON_MODEL_ROUTING", str(path))
    settings = cast(Any, config.Settings)(_env_file=None, memory_multi_query_enabled=False)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    routing = model_routing.load_model_routing()
    assert routing.source_path == str(path)
    assert not routing.has_profile("memory_decomposition")
    route = _route(model=LUNA)
    monkeypatch.setattr(
        compute_runtime,
        "load_inference_policy",
        lambda: SimpleNamespace(routes={route.route_id: route}, requirements=object()),
    )
    with model_routing.routing_context("background") as parent:
        assert compute_runtime.choose_route(profile="background") is route
        assert model_routing.model_parameter_presets(LUNA, "background") == {
            "reasoning_effort": "medium"
        }
        with pytest.raises(model_routing.RoutingError) as caught:
            with model_routing.routing_context("memory_decomposition"):
                pytest.fail("An absent profile must never bind or substitute another profile")
        assert caught.value.code == "profile_unknown"
        assert model_routing.current_routing() is parent


@pytest.mark.parametrize("name", sorted(model_routing.REQUIRED_ROUTING_PROFILES))
def test_original_profiles_remain_required(name):
    raw = legacy_routing_document()
    raw["profiles"] = [p for p in raw["profiles"] if p["profile"] != name]
    with pytest.raises(PolicyError, match="missing required profile"):
        model_routing.parse_model_routing(raw)


@pytest.mark.parametrize("field,value", [("min_output_tokens", 0), ("groups", [])])
def test_optional_profile_is_validated_when_present(field, value):
    raw = json.loads((ROOT / "config/model_routing.json").read_text())
    next(p for p in raw["profiles"] if p["profile"] == "memory_decomposition")[field] = value
    with pytest.raises(PolicyError):
        model_routing.parse_model_routing(raw)


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
    assert not model_routing.parse_model_routing(missing).has_profile("memory_decomposition")
