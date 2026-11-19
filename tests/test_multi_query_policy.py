"""Synthetic opt-in transport validation and shipped legacy-policy identity."""

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.entitlements import attestation
from orchestrator.entitlements.errors import PolicyError, RouteNotApproved
from orchestrator.entitlements.policy import parse_inference_policy, _provider_pinned
from orchestrator.memory.embedding_adapter import validate_adapter_route

ROOT = Path(__file__).resolve().parents[1]
ROUTE = "memory-decomposition-luna-azure-eu"
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
BASELINE = {
    "inference_policy.json": "5aeeec92c36ed0e22c8daf26b86526175bec73ca51d8cd1c3e154c71a2403f66",
    "inference_policy.production.json": "1e8648205f767df96b80a123009c6c03075eb45e1e658ee6901ec422dd444392",
}
FINGERPRINTS = {
    "luna-azure": "4840f7a2260476a7bfe27915c661d70013408d38a2841671f3838be3c0928b42",
    "deepseek-flash-coreweave-fp8": "1b2f90c2fb75f199842c761372d36ff26cd057ccb7382edd63d041569436372f",
    "glm-flash-inceptron-fp8": "947e96a619e03a55c438e686441a7ad7f6e4c12537d4694008d303974cf3deeb",
    "sol-azure": "36d1158fa6df83a10246b13a7d42d32cd5234612ed13c7cd174c4a871476969b",
    "sonnet-vertex-global": "a797d65b593acde7585c7a30d09e0cfa5c0a6ef7fc44df7f42b9f11e42609d24",
    "opus-vertex-global": "fc3fc2272112465c849bcc479a0319db34868ca31fe4771ca471c631a7bda6cd",
    "astra-azure": "64dd1e0ccdee386b24c7dfe2e440c12c542ca94c8cfc0a476f92b428784c4f3c",
    "grok-bedrock-global": "40751e6fc809ad323844cf71b04122b9bf255e51abeace716dc989e868f6a200",
}


def _raw(name="inference_policy.production.json"):
    return json.loads((ROOT / "config" / name).read_text())


def _proposal(raw):
    return next(route for route in raw["routes"] if route["route_id"] == ROUTE)


@pytest.mark.parametrize("name", BASELINE)
def test_shipped_policy_legacy_payloads_approvals_and_attestation_unchanged(name):
    raw = _raw(name)
    legacy = [r for r in raw["routes"] if r["route_id"] != ROUTE]
    assert hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest() == BASELINE[name]
    before = parse_inference_policy({**raw, "routes": legacy})
    after = parse_inference_policy(raw)
    monitored = [r for r in before.monitorable_routes() if attestation.is_monitored(r)]
    keys = {(r.route_id, attestation.baseline_fingerprint(r)): NOW for r in monitored}
    old = attestation.snapshot()
    attestation.set_snapshot(attestation.AttestationSnapshot(loaded=True, attested_at=keys))
    try:
        assert before.approved_route_ids(now=NOW) == after.approved_route_ids(now=NOW)
        assert {r.route_id for r in monitored} == {
            r.route_id for r in after.monitorable_routes() if attestation.is_monitored(r)
        }
        for r in before.monitorable_routes():
            new = after.route(r.route_id) or after.embedding_route(r.route_id)
            assert new is not None
            assert new == r
            assert attestation.baseline_fingerprint(new) == attestation.baseline_fingerprint(r)
            if r.route_id in FINGERPRINTS:
                assert attestation.baseline_fingerprint(new) == FINGERPRINTS[r.route_id]
            assert new.rejection_reasons(after.requirements, now=NOW) == r.rejection_reasons(
                before.requirements, now=NOW
            )
            if not r.transport.rejection_reasons(before.requirements):
                expected = {
                    "extra_body": {
                        "provider": {
                            "only": list(r.transport.provider_only or ()),
                            "order": list(r.transport.provider_order or ()),
                            "allow_fallbacks": False,
                            "require_parameters": True,
                            "data_collection": "deny",
                            "zdr": True,
                        }
                    }
                }
                assert json.dumps(new.transport.as_transport_kwargs()) == json.dumps(expected)
        proposal = after.route(ROUTE)
        assert proposal is not None
        assert not proposal.approved and not attestation.is_monitored(proposal)
        with pytest.raises(RouteNotApproved):
            after.transport_payload(ROUTE, now=NOW)
        embedding = after.embedding_route("azure-openrouter-small-1024")
        assert embedding is not None
        validate_adapter_route(embedding)
    finally:
        attestation.set_snapshot(old)


@pytest.mark.parametrize("sort", [None, "latency", "throughput"])
@pytest.mark.parametrize("providers", [["azure/eu"], ["azure/eu", "azure/us"]])
def test_new_mode_payload_and_shared_approval_predicate(sort, providers):
    raw = _raw()
    entry = _proposal(raw)
    transport = entry["privacy"]["transport"]
    transport.update(provider_only=providers, provider_order=list(reversed(providers)), sort=sort)
    p = parse_inference_policy(raw)
    route = p.route(ROUTE)
    assert route is not None
    assert _provider_pinned(route.transport)
    assert not route.transport.rejection_reasons(p.requirements)
    payload = route.transport.as_transport_kwargs()["extra_body"]["provider"]
    assert payload["only"] == providers
    if sort:
        assert payload["sort"] == sort and "order" not in payload
    else:
        assert payload["order"] == list(reversed(providers)) and "sort" not in payload
    assert "transport_provider_not_pinned" not in route.rejection_reasons(p.requirements)
    if len(providers) > 1:
        assert "multi_provider_proposal_only" in replace(route, approved=True).rejection_reasons(
            p.requirements
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("sort", "cost"),
        ("provider_only", []),
        ("provider_only", ["azure/eu", "azure/eu"]),
        ("provider_only", ["a", "b", "c", "d", "e"]),
        ("provider_order", ["other"]),
        ("zdr", False),
        ("data_collection", "allow"),
        ("require_parameters", False),
        ("allow_fallbacks", True),
    ],
)
def test_invalid_new_mode_rejected(field, value):
    raw = _raw()
    _proposal(raw)["privacy"]["transport"][field] = value
    with pytest.raises(PolicyError):
        parse_inference_policy(raw)


def test_new_mode_rejects_variants_missing_price_and_approved_multi():
    for key, value in (("model", "openrouter/openai/gpt-6-luna:nitro"), ("price_ceiling", None)):
        raw = _raw()
        _proposal(raw)[key] = value
        with pytest.raises(PolicyError):
            parse_inference_policy(raw)
    raw = _raw()
    entry = _proposal(raw)
    entry["approved"] = True
    entry["privacy"]["transport"].update(provider_only=["a", "b"], provider_order=["a", "b"])
    with pytest.raises(PolicyError):
        parse_inference_policy(raw)


def test_monitored_singleton_sort_valid_multi_forbidden_and_embedding_unaffected():
    raw = _raw()
    original = next(r for r in raw["routes"] if r["route_id"] == "luna-azure")
    original["privacy"]["transport"].update(multi_provider=True, sort="latency")
    route = parse_inference_policy(raw).route("luna-azure")
    assert route is not None
    assert attestation.baseline_fingerprint(route) == FINGERPRINTS[route.route_id]
    assert "sort" in route.transport.as_transport_kwargs()["extra_body"]["provider"]
    changed = copy.deepcopy(raw)
    entry = next(r for r in changed["routes"] if r["route_id"] == "luna-azure")
    entry["privacy"]["transport"].update(
        provider_only=["azure/eu", "azure/us"], provider_order=["azure/eu", "azure/us"]
    )
    with pytest.raises(PolicyError):
        parse_inference_policy(changed)
    raw["embedding_routes"][0]["privacy"]["transport"]["multi_provider"] = True
    with pytest.raises(PolicyError, match="Embedding routes"):
        parse_inference_policy(raw)
