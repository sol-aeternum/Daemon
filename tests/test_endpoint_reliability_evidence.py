"""Approved historical compatibility evidence never rewrites availability failures."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator.entitlements.policy import RoutePolicy
from scripts import model_endpoint_reliability as rel


def evidence_fixture(tmp_path: Path) -> tuple[dict[str, Any], RoutePolicy, Path]:
    model = "openrouter/z-ai/glm-5.3-flash"
    identity = {
        "account": "isolated-account",
        "period_key": "2026-09",
        "database": "isolated-db",
        "database_url_sha256": "dsn-hash",
        "fixtures_sha256": "fixture-hash",
        "commercial_sha256": "funding-hash",
        "exact_model": model,
        "primary_route_id": "primary",
        "plan_sha256": "original-plan",
    }
    state = rel.empty_state(identity)
    state["account_ledger_baseline"] = {"exposure_microusd": 38181}
    state["smokes"] = {
        "smoke-primary": {
            "outcome": "quality:truncated_response",
            "status": "quality_failed",
            "dispatches": [{}],
        },
        "smoke-alternate": {"outcome": "completed", "status": "completed", "dispatches": [{}]},
        rel.RECHECK_SMOKE_ID: {
            "outcome": "failed",
            "dispatches": [
                {
                    "failure": {
                        "category": "rate_limited",
                        "status_code": 429,
                        "outcome": "fallback",
                    }
                }
            ],
        },
    }
    payload = rel.amendment_payload(identity["plan_sha256"])
    state["plan_amendment"] = {
        **payload,
        "amendment_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest(),
    }
    route = cast(
        RoutePolicy,
        SimpleNamespace(
            route_id="primary",
            model=model,
            transport=SimpleNamespace(provider_only=("inceptron/fp8",)),
        ),
    )
    source = {
        "version": "model-roster-live-state/1",
        "identity": {**identity, "pins": {"glm-flash": "historical-primary"}},
        "attempts": {
            "O01-glm-flash-r2": {
                "status": "completed",
                "calls": [
                    {
                        "status": "completed",
                        "route_id": "historical-primary",
                        "requested_model": model,
                        "provider_pin": ["inceptron/fp8"],
                        "raw_response": {
                            "model": model.removeprefix("openrouter/"),
                            "provider": "Inceptron",
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"content": "Synthetic answer"},
                                }
                            ],
                        },
                    }
                ],
            }
        },
    }
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    return state, route, path


def test_evidence_admits_without_rewriting_failures_or_budget(tmp_path: Path) -> None:
    state, route, path = evidence_fixture(tmp_path)
    original = copy.deepcopy(state)
    assert not rel.smokes_allow_run(state)[0]
    rel.admit_prior_compatibility(state, route, path)
    assert rel.smokes_allow_run(state)[0]
    for key in original:
        assert state[key] == original[key]
    record = state["primary_compatibility_evidence"]
    assert record["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    state["smokes"]["smoke-alternate"]["outcome"] = "failed"
    with pytest.raises(rel.ReliabilityError, match="smoke evidence drift"):
        rel.smokes_allow_run(state)


@pytest.mark.parametrize("bad", ["scope", "provider", "model", "unfinished", "missing_echo"])
def test_wrong_prior_evidence_cannot_admit(tmp_path: Path, bad: str) -> None:
    state, route, path = evidence_fixture(tmp_path)
    source = json.loads(path.read_text())
    call = source["attempts"]["O01-glm-flash-r2"]["calls"][0]
    if bad == "scope":
        source["identity"]["account"] = "other-account"
    elif bad == "provider":
        call["provider_pin"] = ["together"]
    elif bad == "model":
        call["raw_response"]["model"] = "different/model"
    elif bad == "unfinished":
        call["raw_response"]["choices"][0]["finish_reason"] = "length"
    else:
        call["raw_response"]["provider"] = None
    path.write_text(json.dumps(source))
    with pytest.raises(rel.ReliabilityError):
        rel.admit_prior_compatibility(state, route, path)
    assert not rel.smokes_allow_run(state)[0]


def test_evidence_is_only_for_429_and_before_arms(tmp_path: Path) -> None:
    state, route, path = evidence_fixture(tmp_path)
    state["logicals"]["started"] = {"status": "in_progress"}
    with pytest.raises(rel.ReliabilityError, match="precede"):
        rel.admit_prior_compatibility(state, route, path)
    state["logicals"].clear()
    state["smokes"][rel.RECHECK_SMOKE_ID]["dispatches"][0]["failure"]["status_code"] = 401
    with pytest.raises(rel.ReliabilityError, match="429"):
        rel.admit_prior_compatibility(state, route, path)
    assert not rel.smokes_allow_run(state)[0]


def test_evidence_tampering_fails_closed(tmp_path: Path) -> None:
    state, route, path = evidence_fixture(tmp_path)
    rel.admit_prior_compatibility(state, route, path)
    state["primary_compatibility_evidence"]["model"] = "different/model"
    with pytest.raises(rel.ReliabilityError, match="evidence drift"):
        rel.smokes_allow_run(state)
