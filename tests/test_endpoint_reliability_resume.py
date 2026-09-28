"""End-to-end checkpoint/resume regression for the evaluation-only reliability runner."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import model_endpoint_reliability as rel
from scripts import model_roster_pilot as pilot

MODEL = "openrouter/z-ai/glm-5.3-flash"
BOUND = 1_000
CHARGE = 500


class InterruptedRun(BaseException):
    """Simulate a process interruption after a dispatch intent is checkpointed."""


@pytest.mark.asyncio
async def test_run_phase_resumes_after_completed_and_in_progress_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No environment files, paid endpoints, or database are consulted. Only this
    # runner's state and results files are real, in a private temporary directory.
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    state_path = private / "state.json"
    account = uuid.uuid4()
    database_url = "postgresql://example.invalid/evaluation_test"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(
        rel,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, openrouter_api_key="test"),
    )
    monkeypatch.setattr(
        rel.live, "policy_paths", lambda: (Path("commercial.json"), Path("inference.json"))
    )
    monkeypatch.setattr(rel.live, "digest", lambda path: f"frozen:{path.name}")
    monkeypatch.setattr(rel, "load_policy", lambda path: object())
    monkeypatch.setattr(rel.live, "validate_commercial", lambda policy: None)

    def route(route_id: str, pin: str) -> Any:
        return SimpleNamespace(
            route_id=route_id,
            provider="openrouter",
            model=MODEL,
            route_class="routine",
            price_ceiling=object(),
            max_output_tokens=8192,
            transport=SimpleNamespace(provider_only=(pin,)),
            is_approved=lambda requirements: True,
            transport_payload=lambda requirements: {"provider": {"only": [pin]}},
        )

    primary = route("route-primary", "primary-pin")
    alternate = route("route-alternate", "alternate-pin")
    by_id = {item.route_id: item for item in (primary, alternate)}
    monkeypatch.setattr(
        rel,
        "load_inference_policy",
        lambda path: SimpleNamespace(requirements=object(), route=by_id.get),
    )
    monkeypatch.setattr(
        rel.compute_runtime,
        "_priced_candidates",
        lambda resolved, bound, params, model, **kw: [
            (BOUND, 4096, item, False, None) for item in by_id.values() if item.model == model
        ],
    )

    class Pool:
        closed = 0

        async def close(self) -> None:
            self.closed += 1

    pool = Pool()
    pools: list[Pool] = []

    async def create_pool(**kwargs: Any) -> Pool:
        assert kwargs["dsn"] == database_url
        pools.append(pool)
        return pool

    monkeypatch.setattr(rel.asyncpg, "create_pool", create_pool)

    class Service:
        def __init__(self, db_pool: Pool) -> None:
            assert db_pool is pool

        async def resolve(self, user: uuid.UUID) -> Any:
            assert user == account
            return SimpleNamespace(recurring_budget_microusd=25_000_000)

    monkeypatch.setattr(rel, "EntitlementService", Service)

    async def validate_database(
        db_pool: Pool, user: uuid.UUID, service: Service, period: str
    ) -> str:
        assert db_pool is pool and user == account and period == rel.ORIGINAL_FUNDED_PERIOD
        return "evaluation_test"

    monkeypatch.setattr(rel.live, "validate_database", validate_database)
    spent = 10_000  # Unrelated pre-existing spend must not be rebased on resume.

    async def exposure(db_pool: Pool, user: uuid.UUID, period: str) -> rel.LedgerExposure:
        assert db_pool is pool and user == account and period == rel.ORIGINAL_FUNDED_PERIOD
        return rel.LedgerExposure(spent, 0, 0)

    monkeypatch.setattr(rel, "read_ledger_exposure", exposure)
    clock = {"utc": datetime(2026, 9, 28, 12, tzinfo=timezone.utc), "mono": 1_000.0}
    monkeypatch.setattr(rel, "utcnow", lambda: clock["utc"])
    monkeypatch.setattr(rel, "monotonic", lambda: clock["mono"])

    async def sleep(seconds: float) -> None:
        clock["mono"] += seconds
        clock["utc"] += timedelta(seconds=seconds)

    monkeypatch.setattr(rel.asyncio, "sleep", sleep)

    @asynccontextmanager
    async def account_compute(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
        assert args[:2] == (pool, account)
        assert kwargs["expected_period"] == rel.ORIGINAL_FUNDED_PERIOD
        with rel.model_routing.routing_context("routine"):
            yield

    monkeypatch.setattr(rel.compute_runtime, "account_compute", account_compute)

    answers = {
        "U01": {"title": "Retrospective review"},
        "U03": {"facts": [{"text": "Merged in March.", "status": "confirmed"}]},
        "U05": {"city": None, "since": None, "employer": None, "absent_fields": ["city"]},
        "U08": {"summary": "A 2026 change.", "identifiers": ["CHG-1"]},
    }
    calls: list[dict[str, Any]] = []
    interrupt_at: int | None = None

    async def guarded(**params: Any) -> dict[str, Any]:
        nonlocal spent
        assert rel.model_routing.active_routing() is not None
        assert params["_route_id"] in by_id
        assert 0 < params["_dispatch_timeout_s"] <= rel.ENDPOINT_TIMEOUT_S
        assert "stream" not in params and "tools" not in params
        calls.append(params)
        if len(calls) == interrupt_at:
            raise InterruptedRun()
        rel.model_routing.current_routing().record_selection(
            model=MODEL, route_id=params["_route_id"], group=None, explicit=True
        )
        spent += CHARGE
        response_format = params.get("response_format")
        case_id = (
            response_format["json_schema"]["name"].removeprefix("reliability_")
            if response_format
            else None
        )
        content = json.dumps(answers[case_id]) if case_id else "ok"
        return {
            "model": MODEL,
            "provider": None,
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
        }

    monkeypatch.setattr(rel.compute_runtime, "guarded_completion", guarded)

    def args(phase: str, result_name: str) -> argparse.Namespace:
        return argparse.Namespace(
            period=rel.ORIGINAL_FUNDED_PERIOD,
            fixtures=pilot.DEFAULT_FIXTURES_PATH,
            primary_route=primary.route_id,
            alternate_route=alternate.route_id,
            account=account,
            state=state_path,
            results=private / result_name,
            phase=phase,
            dry_run=False,
        )

    assert await rel.run(args("smoke", "smoke-results.json")) == 0
    smoke_state = json.loads(state_path.read_text())
    assert {entry["outcome"] for entry in smoke_state["smokes"].values()} == {"completed"}
    assert [call["_route_id"] for call in calls] == [primary.route_id, alternate.route_id]
    assert 0 < calls[1]["_dispatch_timeout_s"] < rel.SMOKE_DEADLINE_S
    assert rel.model_routing.active_routing() is None

    interrupt_at = 4  # First logical completes; the next has a durable intent.
    with pytest.raises(InterruptedRun):
        await rel.run(args("run", "interrupted-results.json"))
    checkpoint = json.loads(state_path.read_text())
    first, second = rel.build_logicals()[:2]
    assert checkpoint["logicals"][first.logical_id]["outcome"] == "completed"
    interrupted = checkpoint["logicals"][second.logical_id]["dispatches"][0]
    assert interrupted["status"] == "in_progress"
    assert interrupted["account_charge_microusd"] is None
    assert rel.accounted_microusd(checkpoint) == 3 * CHARGE + BOUND
    assert checkpoint["identity"] == smoke_state["identity"]
    assert checkpoint["identity"]["plan_sha256"] == rel.plan_fingerprint(
        rel.build_logicals(), rel.build_smokes()
    )
    assert checkpoint["account_ledger_baseline"] == smoke_state["account_ledger_baseline"]
    assert rel.model_routing.active_routing() is None

    interrupt_at = None
    assert await rel.run(args("run", "resumed-results.json")) == 0
    persisted = json.loads(state_path.read_text())
    assert persisted["identity"] == checkpoint["identity"]
    assert persisted["account_ledger_baseline"] == checkpoint["account_ledger_baseline"]
    assert persisted["logicals"][first.logical_id] == checkpoint["logicals"][first.logical_id]
    assert persisted["logicals"][second.logical_id] == checkpoint["logicals"][second.logical_id]
    assert len(persisted["logicals"]) == len(rel.build_logicals()) == 24
    assert len(rel.dispatch_rows(persisted)) == len(calls) == 26
    assert rel.accounted_microusd(persisted) == 25 * CHARGE + BOUND
    assert {
        row["outcome"] for key, row in persisted["logicals"].items() if key != second.logical_id
    } == {"completed"}
    for row in rel.dispatch_rows(persisted):
        if row["status"] == "completed":
            assert row["runtime_route_id"] == row["route_id"]
            assert row["runtime_model"] == MODEL
    assert rel.model_routing.active_routing() is None
    assert pool.closed == len(pools) == 3
    results = json.loads((private / "resumed-results.json").read_text())
    assert results["bounds"]["dispatches_recorded"] == 26
    assert results["identity"] == checkpoint["identity"]
