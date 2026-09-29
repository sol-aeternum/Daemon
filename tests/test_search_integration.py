"""Search selection, operator approval and chat capability integration."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator import compute_runtime
from orchestrator.config import Settings
from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.policy import parse_inference_policy
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
from orchestrator.tools.builtin import create_default_registry
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import Tool, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
FUTURE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def deployment():
    return json.loads((ROOT / "config/inference_policy.production.json").read_text())


def test_brave_manual_review_has_no_hard_cutoff_but_inference_still_expires():
    policy = parse_inference_policy(deployment())
    assert policy.approved_tool_service_ids(now=FUTURE) == {"brave-web-search"}
    assert not policy.approved_route_ids(now=FUTURE)
    body = deployment()
    body["tool_services"][0]["approved"] = False
    assert not parse_inference_policy(body).approved_tool_service_ids(now=NOW)


@pytest.mark.parametrize("field", ["reviewer", "reviewed_at", "evidence"])
def test_manual_review_requires_dated_evidence(field):
    body = deployment()
    body["tool_services"][0]["operator_review"][field] = [] if field == "evidence" else None
    assert not parse_inference_policy(body).approved_tool_service_ids(now=NOW)


def test_manual_review_rejects_contradictory_expiry_and_future_review():
    body = deployment()
    review = body["tool_services"][0]["operator_review"]
    review["review_expires_at"] = "2026-09-28T00:00:00Z"
    assert not parse_inference_policy(body).approved_tool_service_ids(now=NOW)
    review["review_expires_at"] = None
    review["reviewed_at"] = "2030-01-01T00:00:00Z"
    assert not parse_inference_policy(body).approved_tool_service_ids(now=NOW)


def test_default_review_mode_still_requires_expiry_and_unknown_mode_is_invalid():
    body = deployment()
    body["tool_services"][0].pop("review_mode")
    assert not parse_inference_policy(body).approved_tool_service_ids(now=NOW)
    body["tool_services"][0]["review_mode"] = "ignore"
    with pytest.raises(PolicyError, match="review_mode"):
        parse_inference_policy(body)


def test_tool_manual_review_mode_cannot_disable_inference_expiry():
    body = deployment()
    route = body["routes"][0]
    route["review_mode"] = "manual"
    route["approval_expires_at"] = None
    route["operator_review"]["review_expires_at"] = None
    policy = parse_inference_policy(body)
    assert not policy.is_approved(route["route_id"], now=NOW)


def test_search_provider_setting_is_explicit_and_validated():
    assert Settings(web_search_provider="tavily").web_search_provider == "tavily"
    with pytest.raises(ValidationError):
        Settings.model_validate({"web_search_provider": "random"})


def test_registry_selects_only_configured_provider_and_hides_disabled_spawn(monkeypatch):
    from orchestrator.tools.web_search import WebSearchTool

    seen = []

    def available(tool):
        seen.append((tool.provider, tool.api_key))
        return tool.provider == "brave" and bool(tool.api_key)

    monkeypatch.setattr(WebSearchTool, "available", available)
    registry = create_default_registry(brave_api_key="brave-test", tavily_api_key="tavily-test")
    assert registry.get("web_search") is not None
    assert registry.get("web_fetch") is not None
    assert registry.get("spawn_agent") is None
    assert registry.get("spawn_multiple") is None
    selected = create_default_registry(
        brave_api_key="brave-test", tavily_api_key="tavily-test", web_search_provider="tavily"
    )
    assert selected.get("web_search") is None
    assert seen == [("brave", "brave-test"), ("tavily", "tavily-test")]


@pytest.mark.asyncio
async def test_executor_propagates_settlement_failure_instead_of_retryable_tool_text():
    class BrokenSettlement(Tool):
        name = "web_search"
        description = "test"
        parameters = {}

        async def execute(self, **kwargs):
            raise compute_runtime.ComputeUnavailable("settlement_failed", "Settlement failed")

    registry = ToolRegistry()
    registry.register(BrokenSettlement())
    with pytest.raises(compute_runtime.ComputeUnavailable) as caught:
        await ToolExecutor(registry).execute("web_search", {"query": "example"})
    assert caught.value.code == "settlement_failed"


def test_prompt_recommends_direct_search_and_honest_capabilities():
    assert "Research does not require spawning a subagent" in DAEMON_SYSTEM_PROMPT
    assert "schemas are authoritative" in DAEMON_SYSTEM_PROMPT
    assert "Cite the returned source URLs" in DAEMON_SYSTEM_PROMPT
    assert "When to use spawn_agent" not in DAEMON_SYSTEM_PROMPT
    assert "Video generation is available" not in DAEMON_SYSTEM_PROMPT
