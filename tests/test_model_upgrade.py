from __future__ import annotations

import copy
import json
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts import model_upgrade as upgrade
from scripts import model_routing_followup as follow

MANIFEST = upgrade.ROOT / "tests/fixtures/model_upgrades/sol61_20260930.json"


def test_approved_manifest_drives_both_settings_without_provider_defaults() -> None:
    spec, raw, fixtures, attempts = upgrade.load_plan(MANIFEST)
    profile = upgrade.profile_for(raw, attempts, MANIFEST)
    assert spec.total_attempts == len(attempts) == 92
    assert spec.dispatch_bound == profile.dispatch_bound == 272
    assert profile.incremental_cap_microusd == 22_000_000
    assert sum(a.is_probe for a in attempts) == 4
    assert all(a.max_calls == 2 for a in attempts if a.is_probe)
    seen = []
    for a in attempts:
        model = spec.candidate(a.candidate_label).model
        route = cast(follow.RoutePolicy, SimpleNamespace(model=model, max_output_tokens=4096))
        case = fixtures.by_id()[a.case_id]
        request = follow.request_for(case, route, a, [{"role": "user", "content": case.prompt}])
        assert request["reasoning_effort"] == a.effort in {"high", "xhigh"}
        assert request.get("tool_choice", "auto") == "auto"
        seen.append((model, a.phase, a.effort))
    assert len(set(seen)) == 10  # diagnostic high + regression/probes high and xhigh, each model


@pytest.mark.asyncio
async def test_preflight_prices_all_attempts_and_probes_before_any_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, raw, fixtures, attempts = upgrade.load_plan(MANIFEST)
    profile = upgrade.profile_for(raw, attempts, MANIFEST)
    routes = {c["label"]: SimpleNamespace(model=c["model"]) for c in raw["candidates"]}
    for route in routes.values():
        route.max_output_tokens = 4096

    async def resolve(_account: Any) -> object:
        return object()

    exposure = {"spent": 960986}

    async def read(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(exposure["spent"], 0, 0)

    service = SimpleNamespace(resolve=resolve)
    ctx = cast(
        follow.RunContext,
        SimpleNamespace(
            account=uuid.uuid4(),
            service=service,
            routes=routes,
            cases=fixtures.by_id(),
            schedule=attempts,
            profile=profile,
            identity={},
            pool=None,
            period="2026-09",
            account_ceiling_microusd=25_000_000,
        ),
    )
    checked = []
    monkeypatch.setattr(
        follow, "verify_candidate", lambda _c, _r, _u, p: checked.append(p) or 80256
    )
    monkeypatch.setattr(follow, "ceiling_bound", lambda _r: 80256)
    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", read)
    monkeypatch.setattr(follow, "ensure_funded_window", lambda *_args: None)
    monkeypatch.setattr(upgrade.model_routing, "supports_reasoning_effort", lambda *_args: True)
    before = list(tmp_path.iterdir())
    result = await upgrade.preflight(ctx)
    assert len(checked) == 92
    assert result["planning_bound_microusd"] == 21_829_632
    assert result["dispatch_bound"] == 272
    assert result["ledger_exposure_microusd"] == 960986
    assert list(tmp_path.iterdir()) == before
    ctx.profile = replace(profile, incremental_cap_microusd=21_829_631)
    with pytest.raises(follow.AccountingViolation, match="incremental"):
        await upgrade.preflight(ctx)
    ctx.profile = profile
    exposure["spent"] = 3_170_369  # one microusd beyond the remaining-allowance envelope
    with pytest.raises(follow.AccountingViolation, match="allowance"):
        await upgrade.preflight(ctx)


def test_result_adapter_preserves_mechanical_fields_and_pairs_settings_separately() -> None:
    spec, raw, fixtures, schedule = upgrade.load_plan(MANIFEST)
    selected = tuple(a for a in schedule if a.case_id == "H01" and a.repeat == 1)
    routes = {c["label"]: SimpleNamespace(model=c["model"]) for c in raw["candidates"]}
    state: dict[str, Any] = {"attempts": {}, "identity": {}, "stages": {}}
    for n, a in enumerate(selected):
        state["attempts"][a.attempt_id] = {
            "status": "completed",
            "calls": [{"account_charge_microusd": n + 1, "provider_cost_usd": (n + 1) / 1_000_000}],
            "latency_seconds": n + 1,
            "final_response": "synthetic",
        }
    ctx = cast(
        follow.RunContext,
        SimpleNamespace(
            state=state,
            routes=routes,
            schedule=schedule,
            cases=fixtures.by_id(),
            profile=upgrade.profile_for(raw, schedule, MANIFEST),
        ),
    )
    before = copy.deepcopy(state)
    document = upgrade.evidence_document(ctx)
    assert state == before
    assert all(
        r["condition"] == "explicit"
        and r["semantic_verdict"] == "pending"
        and r["reviewer"] is None
        for r in document["attempts"]
    )
    summary = upgrade.specs.result_summary(document, spec)
    assert {p["condition"] for p in summary["paired_deltas_seconds"]} == {"high", "xhigh"}
    assert len(summary["paired_deltas_seconds"]) == 2
    assert all(p["charge_delta_microusd"] == 1 for p in summary["paired_deltas_seconds"])
    assert summary["cost_usd"]["cost_per_acceptable"] is None


def test_manifest_changes_are_in_the_executor_fingerprint(tmp_path: Path) -> None:
    _spec, raw, _fixtures, schedule = upgrade.load_plan(MANIFEST)
    path = tmp_path / "manifest.json"
    path.write_bytes(MANIFEST.read_bytes())
    profile = upgrade.profile_for(raw, schedule, path)
    before = follow.implementation_fingerprint(extra_paths=profile.implementation_paths)
    path.write_text(json.dumps({**raw, "period": "2026-10"}))
    assert follow.implementation_fingerprint(extra_paths=profile.implementation_paths) != before
    assert asdict(schedule[-1])["max_calls"] == 2
