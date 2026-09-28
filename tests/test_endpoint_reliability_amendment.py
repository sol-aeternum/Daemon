"""Offline checks for the one-time primary smoke qualification amendment."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.policy import RoutePolicy

from scripts import model_endpoint_reliability as rel
from scripts import model_roster_pilot as pilot


def original_state() -> dict[str, Any]:
    plan = rel.plan_fingerprint(rel.build_logicals(), rel.build_smokes())
    state = rel.empty_state({"plan_sha256": plan})
    state["account_ledger_baseline"] = {
        "exposure_microusd": 200,
        "period_key": rel.ORIGINAL_FUNDED_PERIOD,
    }
    state["smokes"] = {
        "smoke-primary": {
            "smoke_id": "smoke-primary",
            "status": "quality_failed",
            "outcome": "quality:truncated_response",
            "dispatches": [{"reservation_bound_microusd": 100}],
        },
        "smoke-alternate": {
            "smoke_id": "smoke-alternate",
            "status": "completed",
            "outcome": "completed",
            "dispatches": [{"account_charge_microusd": 30}],
        },
    }
    return state


def authorize(state: dict[str, Any]) -> None:
    payload = rel.amendment_payload(state["identity"]["plan_sha256"])
    import hashlib

    state["plan_amendment"] = {
        **payload,
        "amendment_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest(),
    }


def test_exact_amendment_and_original_evidence_are_immutable() -> None:
    state = original_state()
    original = copy.deepcopy(state)
    assert rel.smokes_allow_run(state)[0] is False
    assert rel.amended(state) is False
    authorize(state)
    assert rel.amended(state)
    assert state["identity"] == original["identity"]
    assert state["account_ledger_baseline"] == original["account_ledger_baseline"]
    assert state["smokes"] == original["smokes"]
    assert rel.smokes_allow_run(state)[0] is False
    state["smokes"][rel.RECHECK_SMOKE_ID] = {
        "status": "completed",
        "outcome": "completed",
        "dispatches": [{"max_output_tokens": 4096}],
    }
    assert rel.smokes_allow_run(state)[0]
    assert original["smokes"]["smoke-primary"]["outcome"] == "quality:truncated_response"
    for field, value in (
        ("total_dispatch_bound", 36),
        ("max_output_tokens", 64),
        ("role", "alternate"),
    ):
        tampered = copy.deepcopy(state)
        tampered["plan_amendment"][field] = value
        with pytest.raises(rel.ReliabilityError, match="amendment drift"):
            rel.amended(tampered)
    del state["plan_amendment"]
    with pytest.raises(rel.ReliabilityError, match="recheck without amendment"):
        rel.amended(state)


def test_original_and_amended_whole_plan_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH).by_id()
    routes = {role: cast(RoutePolicy, SimpleNamespace()) for role in rel.ROUTE_ROLES}
    seen: list[tuple[str, int | None]] = []

    def price(
        _resolved: Any, _routes: Any, role: str, _case: Any, max_output_tokens: int | None = None
    ) -> int:
        seen.append((role, max_output_tokens))
        return 400 if max_output_tokens == 4096 else 100

    monkeypatch.setattr(rel, "bound_for", price)
    exposure = rel.LedgerExposure(25_000_000 - 5000, 0, 0)
    regular = rel.check_worst_case(
        routes,
        cases,
        SimpleNamespace(recurring_budget_microusd=25_000_000),
        rel.build_logicals(),
        exposure,
    )
    assert regular["total_microusd"] == 3400
    assert "recheck" not in regular and len(seen) == 34
    amended = rel.check_worst_case(
        routes,
        cases,
        SimpleNamespace(recurring_budget_microusd=25_000_000),
        rel.build_logicals(),
        exposure,
        include_recheck=True,
    )
    assert amended["total_microusd"] == 3800
    assert amended["recheck"] == {rel.RECHECK_SMOKE_ID: 400}
    assert seen.count(("primary", 4096)) == 1
    with pytest.raises(rel.ReliabilityError, match="shared account allowance"):
        rel.check_worst_case(
            routes,
            cases,
            SimpleNamespace(recurring_budget_microusd=25_000_000),
            rel.build_logicals(),
            rel.LedgerExposure(25_000_000 - 3700, 0, 0),
            include_recheck=True,
        )
    monkeypatch.setattr(rel, "bound_for", lambda *_args, **_kwargs: 30_000)
    with pytest.raises(rel.ReliabilityError, match="incremental ceiling"):
        rel.check_worst_case(
            routes,
            cases,
            SimpleNamespace(recurring_budget_microusd=25_000_000),
            rel.build_logicals(),
            exposure,
            include_recheck=True,
        )


@pytest.mark.asyncio
async def test_recheck_one_dispatch_never_replays_and_failure_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = original_state()
    authorize(state)
    path = tmp_path / "state.json"
    rel.write_state(path, state)
    ctx = rel.RunContext(
        pool=None,
        account=uuid.uuid4(),
        service=cast(EntitlementService, SimpleNamespace()),
        period=rel.ORIGINAL_FUNDED_PERIOD,
        identity=state["identity"],
        state=state,
        state_path=path,
        routes={"primary": cast(RoutePolicy, SimpleNamespace(route_id="primary"))},
        cases={},
        account_ceiling_microusd=25_000_000,
        ends_at=rel.period_end(rel.ORIGINAL_FUNDED_PERIOD),
    )
    calls: list[int | None] = []

    async def dispatch(*_args: Any, **kwargs: Any) -> rel.DispatchResult:
        calls.append(kwargs["max_output_tokens"])
        kwargs_entry = _args[1]
        kwargs_entry["dispatches"].append({"max_output_tokens": kwargs["max_output_tokens"]})
        return rel.DispatchResult(response={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(rel, "dispatch_once", dispatch)
    monkeypatch.setattr(rel, "verify_response", lambda *_args: "empty_answer")
    await rel.run_smoke(ctx, rel.Smoke(rel.RECHECK_SMOKE_ID, "primary"))
    assert calls == [4096]
    persisted = json.loads(path.read_text())
    assert persisted["smokes"][rel.RECHECK_SMOKE_ID]["outcome"] == "quality:empty_answer"
    assert persisted["smokes"]["smoke-primary"] == original_state()["smokes"]["smoke-primary"]
    assert rel.smokes_allow_run(persisted)[0] is False
    # A resume skips every recorded recheck, including a quality failure or in-progress intent.
    for status in ("quality_failed", "in_progress"):
        persisted["smokes"][rel.RECHECK_SMOKE_ID]["status"] = status
        assert rel.RECHECK_SMOKE_ID in persisted["smokes"]
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_phase_recheck_checkpoint_resume_and_run_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    state_path = private / "state.json"
    account = uuid.uuid4()
    url = "postgresql://example.invalid/evaluation_test"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(
        rel, "get_settings", lambda: SimpleNamespace(database_url=url, openrouter_api_key="test")
    )
    monkeypatch.setattr(rel.live, "policy_paths", lambda: (Path("commercial"), Path("inference")))
    monkeypatch.setattr(rel.live, "digest", lambda path: f"frozen:{path.name}")
    monkeypatch.setattr(rel, "load_policy", lambda path: object())
    monkeypatch.setattr(rel.live, "validate_commercial", lambda policy: None)

    def route(route_id: str, pin: str) -> Any:
        return SimpleNamespace(
            route_id=route_id,
            provider="openrouter",
            model="openrouter/test-model",
            route_class="routine",
            price_ceiling=object(),
            max_output_tokens=8192,
            transport=SimpleNamespace(provider_only=(pin,)),
            is_approved=lambda requirements: True,
            transport_payload=lambda requirements: {},
        )

    primary, alternate = route("p", "inceptron"), route("a", "together")
    by_id = {item.route_id: item for item in (primary, alternate)}
    monkeypatch.setattr(
        rel,
        "load_inference_policy",
        lambda path: SimpleNamespace(requirements=object(), route=by_id.get),
    )
    monkeypatch.setattr(
        rel.compute_runtime,
        "_priced_candidates",
        lambda *_args, **_kwargs: [(100, 4096, item, False, None) for item in by_id.values()],
    )

    class Pool:
        async def close(self) -> None:
            pass

    class Service:
        def __init__(self, pool: Pool) -> None:
            pass

        async def resolve(self, account_id: uuid.UUID) -> Any:
            return SimpleNamespace(recurring_budget_microusd=25_000_000)

    async def create_pool(**kwargs: Any) -> Pool:
        return Pool()

    async def validate_db(*_args: Any) -> str:
        return "evaluation_test"

    async def exposure(*_args: Any) -> rel.LedgerExposure:
        return rel.LedgerExposure(200, 0, 0)

    monkeypatch.setattr(rel.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(rel, "EntitlementService", Service)
    monkeypatch.setattr(rel.live, "validate_database", validate_db)
    monkeypatch.setattr(rel, "read_ledger_exposure", exposure)
    state = original_state()
    state["identity"] = {
        "account": str(account),
        "period_key": rel.ORIGINAL_FUNDED_PERIOD,
        "primary_route_id": "p",
        "alternate_route_id": "a",
        "exact_model": primary.model,
        "commercial_sha256": "frozen:commercial",
        "inference_sha256": "frozen:inference",
        "fixtures_sha256": pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH).sha256,
        "plan_sha256": rel.plan_fingerprint(rel.build_logicals(), rel.build_smokes()),
        "database": "evaluation_test",
        "database_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
    }
    rel.write_state(state_path, state)

    def args(phase: str, result: str, *, dry_run: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            period=rel.ORIGINAL_FUNDED_PERIOD,
            fixtures=pilot.DEFAULT_FIXTURES_PATH,
            primary_route="p",
            alternate_route="a",
            account=account,
            state=state_path,
            results=private / result,
            phase=phase,
            dry_run=dry_run,
        )

    before = state_path.read_bytes()
    assert await rel.run(args("recheck", "dry", dry_run=True)) == 0
    assert state_path.read_bytes() == before
    assert not (private / "dry").exists()

    calls: list[str] = []

    async def fake_smoke(ctx: rel.RunContext, smoke: rel.Smoke) -> None:
        assert rel.amended(json.loads(ctx.state_path.read_text()))
        assert ctx.state["identity"] == state["identity"]
        assert ctx.state["account_ledger_baseline"] == state["account_ledger_baseline"]
        calls.append(smoke.smoke_id)
        ctx.state["smokes"][smoke.smoke_id] = {
            "smoke_id": smoke.smoke_id,
            "status": "quality_failed",
            "outcome": "quality:empty_answer",
            "dispatches": [{"max_output_tokens": 4096}],
        }
        rel.write_state(ctx.state_path, ctx.state)

    monkeypatch.setattr(rel, "run_smoke", fake_smoke)
    assert await rel.run(args("recheck", "failed.json")) == 0
    assert calls == [rel.RECHECK_SMOKE_ID]
    failed = json.loads(state_path.read_text())
    assert failed["preflight"]["dispatch_bound"] == 35
    assert json.loads((private / "failed.json").read_text())["bounds"]["total_dispatch_bound"] == 35
    assert await rel.run(args("recheck", "resumed.json")) == 0
    assert calls == [rel.RECHECK_SMOKE_ID]
    assert json.loads(state_path.read_text())["smokes"] == failed["smokes"]
    with pytest.raises(rel.ReliabilityError, match="smoke did not complete"):
        await rel.run(args("run", "blocked.json"))
    assert not (private / "blocked.json").exists()
    failed["smokes"][rel.RECHECK_SMOKE_ID]["status"] = "completed"
    failed["smokes"][rel.RECHECK_SMOKE_ID]["outcome"] = "completed"
    rel.write_state(state_path, failed)
    logical_calls: list[str] = []

    async def fake_logical(ctx: rel.RunContext, logical: rel.Logical) -> None:
        logical_calls.append(logical.logical_id)
        ctx.state["logicals"][logical.logical_id] = {
            "arm": logical.arm,
            "outcome": "completed",
            "dispatches": [],
        }
        rel.write_state(ctx.state_path, ctx.state)

    monkeypatch.setattr(rel, "run_logical", fake_logical)
    assert await rel.run(args("run", "accepted.json")) == 0
    assert len(logical_calls) == 24
    assert calls == [rel.RECHECK_SMOKE_ID]
    assert json.loads(state_path.read_text())["identity"] == state["identity"]
