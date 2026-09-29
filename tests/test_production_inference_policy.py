"""Deployment approval must remain opt-in, pinned, and time-bounded."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.entitlements.policy import RouteNotApproved, parse_inference_policy

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = datetime(2026, 9, 29, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 10, 6, tzinfo=timezone.utc)


def test_deployment_policy_is_opt_in_and_expires_closed() -> None:
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
        assert route.approval_expires_at == EXPIRES
        assert route.review.review_expires_at == EXPIRES
        assert "operator_review_expired" in route.rejection_reasons(
            policy.requirements, now=EXPIRES
        )
        assert "approval_expired" in route.rejection_reasons(policy.requirements, now=EXPIRES)
        with pytest.raises(RouteNotApproved):
            route.transport_payload(policy.requirements, now=EXPIRES)
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
        "openrouter/qwen/qwen3.8-max-0902",
        "openrouter/google/gemini-3.8-flash",
        "openrouter/z-ai/glm-5.3",
    ):
        with pytest.raises(compute_runtime.ComputeUnavailable):
            compute_runtime.choose_route(model)
