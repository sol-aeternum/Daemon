"""No-network contract tests for the separate routing follow-up experiment."""

from __future__ import annotations

import asyncio
import argparse
import json
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator.config import get_settings
from scripts import model_routing_followup as follow


@pytest.fixture(autouse=True)
def _fresh_cached_settings() -> Any:
    """Reset the cached Settings around each test.

    Routing env overrides (``DAEMON_MODEL_ROUTING``) are resolved through the
    cached ``get_settings()``, so a stale cached instance would keep a previous
    environment's routing catalog path alive. Tests that change the env mid-test
    additionally clear the cache again after the change.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def corpus() -> follow.FixtureSet:
    return follow.load_fixtures(follow.DEFAULT_FIXTURES_PATH)


def test_real_fixture_loader_simulator_and_schedule(corpus: follow.FixtureSet) -> None:
    cases = corpus.by_id()
    assert len(cases) == 14
    memo = follow.simulate(cases["D01"], "fetch_record", {"id": "M-71"}, {})
    assert memo["text"].endswith("ST-204, not in this memo.")
    assert (
        follow.simulate(cases["D01"], "fetch_record", {"id": "ST-204"}, {})["retention_days"] == 45
    )
    assert (
        follow.simulate(cases["D01"], "fetch_record", {"id": "ST-205"}, {})
        == follow.not_found_result()
    )
    assert follow.simulate(cases["D01"], "fetch_record", {"id": "M-71", "extra": 1}, {}) == memo
    assert (
        follow.simulate(cases["D01"], "fetch_record", {"extra": "M-71"}, {})
        == follow.not_found_result()
    )
    cursor: dict[str, int] = {}
    assert (
        follow.simulate(cases["D03"], "archive_search", {"query": "Emberline"}, cursor)["error"][
            "code"
        ]
        == "timeout"
    )
    for _ in range(2):
        assert (
            follow.simulate(cases["D03"], "archive_search", {"query": "Emberline"}, cursor)[
                "results"
            ][0]["id"]
            == "EV-314"
        )

    schedule = follow.build_schedule(corpus)
    assert len(schedule) == 120
    assert len({attempt.attempt_id for attempt in schedule}) == 120
    assert [attempt.case_id for attempt in schedule[:6]] == ["D01"] * 6
    assert [(a.candidate_label, a.condition) for a in schedule[:6]] == [
        (label, condition) for label in follow.CANDIDATE_LABELS for condition in follow.CONDITIONS
    ]
    assert Counter((a.stage, a.condition) for a in schedule) == {
        ("diagnostic", "default"): 36,
        ("diagnostic", "explicit"): 36,
        ("heldout", "explicit"): 48,
    }
    assert len(follow.schedule_for_stage(schedule, "diagnostic")) == 72
    assert len(follow.schedule_for_stage(schedule, "heldout")) == 48
    assert all(a.max_calls == 3 for a in schedule)
    assert follow.DISPATCH_BOUND == 360


def test_request_effort_is_omitted_by_default_and_explicit_presets(
    corpus: follow.FixtureSet,
) -> None:
    schedule = follow.build_schedule(corpus)
    case = corpus.by_id()["D01"]
    messages = [{"role": "user", "content": case.prompt}]
    for label, effort in (("luna", "low"), ("sol", "high"), ("sonnet", "high")):
        route = cast(
            follow.RoutePolicy,
            SimpleNamespace(model=follow.CANDIDATE_BY_LABEL[label].model, max_output_tokens=8192),
        )
        default = next(
            a
            for a in schedule
            if a.case_id == "D01" and a.candidate_label == label and a.condition == "default"
        )
        explicit = next(
            a
            for a in schedule
            if a.case_id == "D01" and a.candidate_label == label and a.condition == "explicit"
        )
        default_params = follow.request_for(case, route, default, messages)
        explicit_params = follow.request_for(case, route, explicit, messages)
        assert "reasoning_effort" not in default_params
        assert explicit_params["reasoning_effort"] == effort
        assert {
            k: v for k, v in explicit_params.items() if k != "reasoning_effort"
        } == default_params
        assert default_params["max_tokens"] == 4096


def test_active_preset_is_rejected_and_isolated_catalog_has_empty_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_compute_runtime import routing_document

    # This historical experiment keeps its old exact candidates. Its isolation
    # contract must not depend on which models today's deployment still lists.
    source = routing_document([candidate.model for candidate in follow.CANDIDATES])
    active_path = tmp_path / "active.json"
    active_path.write_text(json.dumps(source))
    monkeypatch.setenv("DAEMON_MODEL_ROUTING", str(active_path))
    get_settings.cache_clear()
    models = {candidate.model for candidate in follow.CANDIDATES}
    assert all(follow.model_routing.model_parameter_presets(model, "routine") for model in models)
    routes = {
        candidate.label: SimpleNamespace(
            route_id=f"route-{candidate.label}",
            model=candidate.model,
            provider="openrouter",
            route_class="premium",
            price_ceiling=object(),
            transport=SimpleNamespace(provider_only=("azure/eu",)),
            is_approved=lambda _: True,
            transport_payload=lambda _: {"provider": {"only": ["azure/eu"]}},
        )
        for candidate in follow.CANDIDATES
    }
    inference = SimpleNamespace(
        requirements=object(),
        route=lambda route_id: next(
            (route for route in routes.values() if route.route_id == route_id), None
        ),
        routes={route.route_id: route for route in routes.values()},
    )
    pins = {label: route.route_id for label, route in routes.items()}
    with pytest.raises(follow.PolicyViolation, match="isolated routing catalog"):
        follow.qualified_routes(pins, inference)
    for item in source["models"]:
        if item["model"] in models:
            item["parameter_presets"] = {"default": {}}
    catalog_path = tmp_path / "isolated.json"
    catalog_path.write_text(json.dumps(source))
    monkeypatch.setenv("DAEMON_MODEL_ROUTING", str(catalog_path))
    get_settings.cache_clear()
    assert set(follow.qualified_routes(pins, inference)) == set(follow.CANDIDATE_LABELS)
    assert all(
        not follow.model_routing.model_parameter_presets(model, "routine") for model in models
    )


def test_heldout_requires_every_recorded_diagnostic_even_failed(corpus: follow.FixtureSet) -> None:
    schedule = follow.build_schedule(corpus)
    diagnostic = follow.schedule_for_stage(schedule, "diagnostic")
    state: dict[str, Any] = {
        "attempts": {a.attempt_id: {"status": "failed"} for a in diagnostic[:-1]}
    }
    with pytest.raises(follow.PolicyViolation, match="1 unrecorded"):
        follow.phase_admission(state, schedule, "heldout")
    state["attempts"][diagnostic[-1].attempt_id] = {"status": "completed"}
    follow.phase_admission(state, schedule, "heldout")
    state["attempts"][diagnostic[-1].attempt_id] = {"status": "interrupted"}
    with pytest.raises(follow.PolicyViolation, match="no automatic resume"):
        follow.phase_admission(state, schedule, "heldout")
    state["attempts"][diagnostic[-1].attempt_id] = {"status": "completed"}
    state["attempts"]["unplanned"] = {"status": "completed"}
    with pytest.raises(follow.PolicyViolation, match="unplanned"):
        follow.phase_admission(state, schedule, "diagnostic")


def test_unknown_usage_keeps_full_microusd_bound_and_exclusivity() -> None:
    state = {
        "attempts": {
            "one": {
                "calls": [{"reservation_bound_microusd": 80_000, "account_charge_microusd": 200}]
            },
            "two": {
                "calls": [{"reservation_bound_microusd": 70_000, "account_charge_microusd": None}]
            },
        },
        "account_ledger_baseline": {"exposure_microusd": 370_314},
    }
    assert follow.accounted_microusd(state) == 70_200
    exposure = follow.reliability.LedgerExposure(440_514, 0, 0)
    assert follow.require_exclusive(state, exposure) == 70_200
    with pytest.raises(follow.AccountingViolation, match="exclusive"):
        follow.require_exclusive(state, follow.reliability.LedgerExposure(440_515, 0, 0))
    with pytest.raises(follow.AccountingViolation, match="open reservation"):
        follow.require_exclusive(state, follow.reliability.LedgerExposure(440_514, 0, 1))


@pytest.mark.asyncio
async def test_admission_checks_both_caps_before_dispatch(
    corpus: follow.FixtureSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = follow.build_schedule(corpus)
    state: dict[str, Any] = {"attempts": {}, "account_ledger_baseline": {"exposure_microusd": 0}}
    ctx = cast(
        follow.RunContext,
        SimpleNamespace(
            state=state,
            schedule=schedule,
            pool=object(),
            account=uuid.uuid4(),
            period="2026-09",
            account_ceiling_microusd=25_000_000,
            profile=follow.DEFAULT_PROFILE,
        ),
    )
    current = {"exposure": 0}

    async def exposure(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(current["exposure"], 0, 0)

    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", exposure)
    assert await follow.admit_call(ctx, 19_999_999, 3) == 0
    with pytest.raises(follow.AccountingViolation, match="incremental"):
        await follow.admit_call(ctx, 20_000_001, 3)
    state["account_ledger_baseline"]["exposure_microusd"] = 24_999_990
    current["exposure"] = 24_999_990
    with pytest.raises(follow.AccountingViolation, match="aggregate"):
        await follow.admit_call(ctx, 11, 3)


def test_frozen_baseline_cannot_rebase_and_rejects_open_holds(tmp_path: Path) -> None:
    state: dict[str, Any] = {"attempts": {}, "account_ledger_baseline": None}
    path = tmp_path / "state.json"
    with pytest.raises(follow.AccountingViolation, match="open reservations"):
        follow.freeze_baseline(state, path, follow.reliability.LedgerExposure(10, 0, 1), "2026-09")
    follow.freeze_baseline(state, path, follow.reliability.LedgerExposure(10, 0, 0), "2026-09")
    follow.freeze_baseline(state, path, follow.reliability.LedgerExposure(11, 0, 0), "2026-09")
    assert state["account_ledger_baseline"]["exposure_microusd"] == 10


def test_response_model_provider_and_tool_schema_fail_closed(corpus: follow.FixtureSet) -> None:
    route = cast(
        follow.RoutePolicy,
        SimpleNamespace(
            model="openrouter/openai/gpt-6-luna",
            transport=SimpleNamespace(provider_only=("azure/eu",)),
        ),
    )
    row: dict[str, Any] = {}
    with pytest.raises(follow.PolicyViolation, match="model drift"):
        follow._record_response(row, {"model": "wrong", "provider": "azure/eu"}, route)
    with pytest.raises(follow.PolicyViolation, match="provider drift"):
        follow._record_response(row, {"model": route.model, "provider": "wrong"}, route)
    with pytest.raises(follow.TaskFailure, match="invalid_response"):
        follow._response_message({"choices": []})
    with pytest.raises(follow.TaskFailure, match="invalid_response"):
        follow._response_message({"choices": [{"message": {}}, {"message": {}}]})
    case = corpus.by_id()["D01"]
    for arguments in ('{"extra":"M-71"}', '{"id":1}', "{bad"):
        with pytest.raises(follow.TaskFailure, match="arguments"):
            follow._parse_tool_arguments(
                case, {"function": {"name": "fetch_record", "arguments": arguments}}
            )
    with pytest.raises(follow.TaskFailure, match="undeclared"):
        follow._parse_tool_arguments(
            case, {"function": {"name": "write_record", "arguments": "{}"}}
        )


def _proposal(name: str, argument: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"query": argument})},
    }


def test_bundled_searches_count_individually_without_extra_completion(
    corpus: follow.FixtureSet, tmp_path: Path
) -> None:
    case = corpus.by_id()["D02"]
    ctx = cast(follow.RunContext, SimpleNamespace(state_path=tmp_path / "state.json", state={}))
    entry: dict[str, Any] = {
        "tool_call_count": 0,
        "tool_steps": [],
        "disallowed_tool_proposals": [],
    }
    messages: list[dict[str, Any]] = []
    cursors: dict[str, int] = {}
    bundle = [_proposal("archive_search", "willow", str(i)) for i in range(2)]
    assistant = {
        "role": "assistant",
        "content": "Searching the archive.",
        "tool_calls": bundle,
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "synthetic-signature"}],
    }
    follow._handle_tool_calls(ctx, entry, case, cursors, messages, bundle, 0, message=assistant)
    assert messages[0] == assistant
    assert entry["tool_call_count"] == 2
    assert len(entry["tool_steps"]) == 2
    with pytest.raises(follow.TaskFailure, match="tool_budget_exceeded"):
        follow._handle_tool_calls(ctx, entry, case, cursors, messages, bundle[:1], 1)
    assert len(entry["disallowed_tool_proposals"]) == 1
    assert entry["tool_call_count"] == 2
    assert (
        len(messages) == 3
    )  # one assistant bundle and two tool responses; no synthetic completion
    with pytest.raises(follow.TaskFailure, match="without_final_answer_budget"):
        follow._handle_tool_calls(
            ctx,
            {"tool_call_count": 0, "tool_steps": [], "disallowed_tool_proposals": []},
            case,
            {},
            [],
            bundle[:1],
            2,
        )


@pytest.mark.asyncio
async def test_durable_attempt_cancellation_keeps_unknown_intent_and_deadline(
    corpus: follow.FixtureSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = corpus.by_id()["D01"]
    attempt = next(a for a in follow.build_schedule(corpus) if a.case_id == "D01")
    route = cast(
        follow.RoutePolicy,
        SimpleNamespace(
            route_id="test-route",
            model=follow.CANDIDATE_BY_LABEL[attempt.candidate_label].model,
            max_output_tokens=8192,
            transport=SimpleNamespace(provider_only=("azure/eu",)),
            estimate_microusd=lambda *_: 80_000,
        ),
    )
    state: dict[str, Any] = {"attempts": {}, "account_ledger_baseline": {"exposure_microusd": 100}}
    path = tmp_path / "state.json"

    async def resolve(_account: Any) -> Any:
        return object()

    ctx = follow.RunContext(
        pool=object(),
        account=uuid.uuid4(),
        service=cast(follow.EntitlementService, SimpleNamespace(resolve=resolve)),
        period="2026-09",
        identity={},
        state=state,
        state_path=path,
        schedule=follow.build_schedule(corpus),
        cases={case.case_id: case},
        routes={attempt.candidate_label: route},
        commercial_path=path,
        inference_path=path,
        account_ceiling_microusd=25_000_000,
    )

    async def preflight(_ctx: Any) -> None:
        return None

    async def admit(_ctx: Any, reachable: int, calls_left: int) -> int:
        assert reachable > 80_000 and calls_left == 3
        return 100

    monkeypatch.setattr(follow, "_attempt_preflight", preflight)
    monkeypatch.setattr(follow, "_self_check", lambda _ctx: None)
    monkeypatch.setattr(follow, "ensure_funded_window", lambda *_: None)
    monkeypatch.setattr(follow, "verify_candidate", lambda *_: 80_000)
    monkeypatch.setattr(follow, "admit_call", admit)
    monkeypatch.setattr(
        follow.compute_runtime,
        "_request_bound",
        lambda _: follow.compute_runtime.InputSize(bound=1000, estimate=334),
    )
    monkeypatch.setattr(
        follow.compute_runtime,
        "_priced_candidates",
        lambda *_args, **_kw: [(80_000, 4096, route, False, None)],
    )

    @asynccontextmanager
    async def account_compute(*_args: Any, **_kw: Any) -> AsyncIterator[Any]:
        with follow.model_routing.routing_context("routine"):
            yield SimpleNamespace(settled={})

    async def cancelled(**params: Any) -> Any:
        assert params["_route_id"] == route.route_id
        assert 0 < params["_dispatch_timeout_s"] <= 90
        persisted = json.loads(path.read_text())
        assert (
            persisted["attempts"][attempt.attempt_id]["calls"][0]["status"] == "dispatched_unknown"
        )
        raise asyncio.CancelledError()

    monkeypatch.setattr(follow.compute_runtime, "account_compute", account_compute)
    monkeypatch.setattr(follow.compute_runtime, "guarded_completion", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await follow.run_attempt(ctx, attempt)
    row = json.loads(path.read_text())["attempts"][attempt.attempt_id]
    assert row["status"] == "interrupted"
    assert len(row["calls"]) == 1
    assert row["calls"][0]["reservation_bound_microusd"] == 80_000
    assert follow.accounted_microusd(state) == 80_000
    assert follow.model_routing.active_routing() is None


@pytest.mark.asyncio
async def test_actual_runtime_dispatch_omits_default_effort_and_settles_each_call(
    corpus: follow.FixtureSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use real account_compute and guarded_completion with only their external I/O mocked."""
    source = json.loads(follow.model_routing.DEFAULT_MODEL_ROUTING.read_text())
    for item in source["models"]:
        if item["model"] in {candidate.model for candidate in follow.CANDIDATES}:
            item["parameter_presets"] = {"default": {}}
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(source))
    monkeypatch.setenv("DAEMON_MODEL_ROUTING", str(catalog))
    get_settings.cache_clear()
    routes: dict[str, follow.RoutePolicy] = {
        candidate.label: cast(
            follow.RoutePolicy,
            SimpleNamespace(
                route_id=f"route-{candidate.label}",
                model=candidate.model,
                provider="openrouter",
                route_class="premium",
                endpoint="https://example.invalid/api",
                price_ceiling=object(),
                max_output_tokens=8192,
                transport=SimpleNamespace(provider_only=("azure/eu",)),
                is_approved=lambda _: True,
                transport_payload=lambda _: {"provider": {"only": ["azure/eu"]}},
                estimate_microusd=lambda input_tokens, output_tokens: (
                    input_tokens + output_tokens + 1000
                ),
            ),
        )
        for candidate in follow.CANDIDATES
    }
    inference = SimpleNamespace(
        requirements=object(), routes={route.route_id: route for route in routes.values()}
    )
    routing_by_id = inference.routes
    monkeypatch.setattr(follow.compute_runtime, "load_inference_policy", lambda: inference)
    monkeypatch.setattr(
        follow.compute_runtime, "_pinned_route", lambda route_id, model: routing_by_id[route_id]
    )

    def priced(
        _policy: Any, input_size: Any, params: Any, model: str, *_args: Any, **_kw: Any
    ) -> list[Any]:
        route = next(route for route in routes.values() if route.model == model)
        # The runtime reserves against the byte-level settlement hold
        # (compute_runtime prices with input_size.bound).
        return [
            (
                route.estimate_microusd(input_size.bound, params["max_tokens"]),
                params["max_tokens"],
                route,
                True,
                None,
            )
        ]

    monkeypatch.setattr(follow.compute_runtime, "_priced_candidates", priced)
    settings = SimpleNamespace(
        request_timeout_s=120,
        openrouter_api_key="stub",
        get_provider_config=lambda _: SimpleNamespace(extra_headers={}),
    )
    monkeypatch.setattr(follow.compute_runtime, "get_settings", lambda: settings)
    spent = {"value": 100}
    reservations: list[tuple[int, dict[str, Any]]] = []
    settlements: list[int] = []

    class FakeService:
        def __init__(self, _pool: Any) -> None:
            pass

        async def resolve(self, _user: Any) -> Any:
            return SimpleNamespace(capabilities={"chat", "premium_routing"})

        async def reconcile_expired_reservations(self, *_args: Any, **_kw: Any) -> None:
            pass

        async def reserve(self, _user: Any, bound: int, **kwargs: Any) -> Any:
            assert kwargs["expected_period"] == "2026-09"
            reservation = object()
            reservations.append((bound, kwargs))
            return reservation

        async def settle(self, _reservation: Any, amount: int, **_kw: Any) -> None:
            spent["value"] += amount
            settlements.append(amount)

    monkeypatch.setattr(follow.compute_runtime, "EntitlementService", FakeService)
    sent: list[dict[str, Any]] = []

    async def transport(**params: Any) -> dict[str, Any]:
        sent.append(params)
        assert params["num_retries"] == 0
        assert params["provider"]["only"] == ["azure/eu"]
        assert params["timeout"] <= 90
        assert "_route_id" not in params and "_dispatch_timeout_s" not in params
        return {
            "model": params["model"],
            "provider": "azure/eu",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {"receipt_id": "RCP-64", "lot_id": "LOT-92", "amount_usd": 37.2}
                                ],
                                "summary": "RCP-64 LOT-92 37.20 USD",
                            }
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0001},
        }

    monkeypatch.setattr(follow.compute_runtime.litellm, "acompletion", transport)

    async def exposure(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(spent["value"], 0, 0)

    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", exposure)
    monkeypatch.setattr(follow, "_attempt_preflight", lambda _ctx: asyncio.sleep(0))
    monkeypatch.setattr(follow, "_self_check", lambda _ctx: None)
    monkeypatch.setattr(follow, "ensure_funded_window", lambda *_: None)
    schedule = follow.build_schedule(corpus)
    case = corpus.by_id()["D06"]
    state: dict[str, Any] = {"attempts": {}, "account_ledger_baseline": {"exposure_microusd": 100}}
    path = tmp_path / "state.json"
    ctx = follow.RunContext(
        pool=object(),
        account=uuid.uuid4(),
        service=cast(follow.EntitlementService, FakeService(None)),
        period="2026-09",
        identity={},
        state=state,
        state_path=path,
        schedule=schedule,
        cases={case.case_id: case},
        routes=routes,
        commercial_path=path,
        inference_path=path,
        account_ceiling_microusd=25_000_000,
    )
    attempts = [a for a in schedule if a.case_id == "D06" and a.repeat == 1]
    for attempt in attempts:
        await follow.run_attempt(ctx, attempt)
        assert state["attempts"][attempt.attempt_id]["status"] == "completed"
        assert state["attempts"][attempt.attempt_id]["schema_valid"] is True
        assert follow.model_routing.active_routing() is None
    assert len(sent) == len(reservations) == len(settlements) == 6
    for attempt, request in zip(attempts, sent, strict=True):
        if attempt.condition == "default":
            assert "reasoning_effort" not in request
        else:
            assert (
                request["reasoning_effort"]
                == follow.CANDIDATE_BY_LABEL[attempt.candidate_label].effort
            )
        call = state["attempts"][attempt.attempt_id]["calls"][0]
        assert call["reservation_bound_microusd"] > call["input_token_bound"]
        assert call["route_id"] == call["runtime_route_id"]
        assert call["requested_model"] == call["runtime_model"]
        assert call["account_charge_microusd"] == 1150
    assert follow.require_exclusive(state, await exposure()) == sum(settlements)

    # Cancellation in the real account scope conservatively settles its full
    # reservation and leaves a stopped, never-replayable attempt on disk.
    async def interrupted_transport(**params: Any) -> Any:
        assert params["model"] == follow.CANDIDATE_BY_LABEL["luna"].model
        raise asyncio.CancelledError()

    monkeypatch.setattr(follow.compute_runtime.litellm, "acompletion", interrupted_transport)
    pointer = next(a for a in schedule if a.case_id == "D01")
    ctx.cases = corpus.by_id()
    with pytest.raises(asyncio.CancelledError):
        await follow.run_attempt(ctx, pointer)
    interrupted = state["attempts"][pointer.attempt_id]
    assert interrupted["status"] == "interrupted"
    assert len(interrupted["calls"]) == 1
    assert interrupted["calls"][0]["account_charge_microusd"] == reservations[-1][0]
    assert settlements[-1] == reservations[-1][0]
    assert follow.require_exclusive(state, await exposure()) == sum(settlements)
    assert follow.model_routing.active_routing() is None


@pytest.mark.asyncio
async def test_run_restart_refuses_recorded_interruption_without_replaying(
    corpus: follow.FixtureSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real run orchestration and durable files, with only preflight/dispatch I/O mocked."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    account = uuid.uuid4()
    database_url = "postgresql://example.invalid/followup_test"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(
        follow,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url, openrouter_api_key="unused"),
    )
    monkeypatch.setattr(
        follow.live, "policy_paths", lambda: (Path("commercial.json"), Path("inference.json"))
    )
    monkeypatch.setattr(follow.live, "digest", lambda path: f"fixed:{path.name}")
    monkeypatch.setattr(follow, "implementation_fingerprint", lambda: "fixed-implementation")
    monkeypatch.setattr(follow, "load_policy", lambda _: SimpleNamespace())
    monkeypatch.setattr(follow.live, "validate_commercial", lambda _: None)
    monkeypatch.setattr(follow, "load_inference_policy", lambda _: object())
    routes = {
        label: SimpleNamespace(route_id=f"route-{label}", route_class="routine")
        for label in follow.CANDIDATE_LABELS
    }
    monkeypatch.setattr(follow, "qualified_routes", lambda *_: routes)
    monkeypatch.setattr(follow.reliability, "account_ceiling", lambda _: 25_000_000)

    class Pool:
        async def close(self) -> None:
            pass

    async def create_pool(**_kwargs: Any) -> Pool:
        return Pool()

    class Service:
        def __init__(self, _pool: Pool) -> None:
            pass

        async def resolve(self, _account: uuid.UUID) -> Any:
            return object()

    async def database(*_args: Any) -> str:
        return "followup_test"

    async def exposure(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(370_314, 0, 0)

    monkeypatch.setattr(follow.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(follow, "EntitlementService", Service)
    monkeypatch.setattr(follow.live, "validate_database", database)
    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", exposure)
    seen: list[str] = []

    async def interrupted(ctx: follow.RunContext, attempt: follow.Attempt) -> None:
        seen.append(attempt.attempt_id)
        ctx.state["attempts"][attempt.attempt_id] = {
            "status": "interrupted" if len(seen) == 2 else "completed",
            "calls": [],
            "stop": {"category": "interrupted"} if len(seen) == 2 else None,
        }
        follow.write_state(ctx.state_path, ctx.state)
        if len(seen) == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(follow, "run_attempt", interrupted)
    pins = private / "pins.json"
    pins.write_text(json.dumps({label: route.route_id for label, route in routes.items()}))
    state = private / "state.json"

    def args(results_name: str) -> argparse.Namespace:
        return argparse.Namespace(
            period="2026-09",
            fixtures=follow.DEFAULT_FIXTURES_PATH,
            pins=pins,
            phase="diagnostic",
            account=account,
            state=state,
            results=private / results_name,
            dry_run=False,
        )

    with pytest.raises(asyncio.CancelledError):
        await follow.run(args("first-results.json"))
    checkpoint = json.loads(state.read_text())
    assert len(seen) == len(checkpoint["attempts"]) == 2
    assert checkpoint["account_ledger_baseline"]["exposure_microusd"] == 370_314
    with pytest.raises(follow.PolicyViolation, match="no automatic resume"):
        await follow.run(args("second-results.json"))
    assert len(seen) == 2
    assert json.loads(state.read_text()) == checkpoint


# ---------------------------------------------------------------------------
# Experiment-profile seam: inert by default, authoritative when injected
# ---------------------------------------------------------------------------

#: The frozen results-row key set. A re-run may add keys through its extension;
#: it may never rename, drop or rewrite one of these.
RESULTS_ROW_KEYS = (
    "account_charge_microusd",
    "attempt_id",
    "calls",
    "calls_used",
    "calls_with_unknown_charge",
    "candidate_label",
    "case_id",
    "condition",
    "cost_usd",
    "disallowed_tool_proposals",
    "final_response",
    "latency_class",
    "latency_seconds",
    "max_tool_calls",
    "repeat",
    "requested_reasoning_effort",
    "reviewer",
    "schema_valid",
    "semantic_verdict",
    "stage",
    "status",
    "stop",
    "task_failures",
    "tool_calls_used",
    "tool_steps",
)


def test_default_profile_preserves_this_experiments_identity_and_caps(tmp_path: Path) -> None:
    """No profile argument means exactly the approved follow-up experiment."""
    profile = follow.DEFAULT_PROFILE
    assert profile.experiment == follow.EXPERIMENT == "model-routing-followup/1"
    assert profile.state_version == follow.STATE_VERSION == "model-routing-followup-state/1"
    assert (
        profile.results_artifact_version
        == follow.RESULTS_ARTIFACT_VERSION
        == "model-routing-followup-results/1"
    )
    assert profile.generated_by == "scripts/model_routing_followup.py"
    assert profile.incremental_cap_microusd == follow.INCREMENTAL_CAP_MICROUSD == 20_000_000
    assert profile.total_attempts == follow.TOTAL_ATTEMPTS == 120
    assert profile.dispatch_bound == follow.DISPATCH_BOUND == 360
    assert profile.implementation_paths == ()
    assert dict(profile.phase_stages) == {"diagnostic": "diagnostic", "heldout": "heldout"}
    assert dict(profile.phase_labels)["heldout"] == "held-out stage"
    assert (profile.gated_phase, profile.gate_stage) == ("heldout", "diagnostic")
    # Execution bounds are shared constants, not profile fields: a re-run cannot
    # lengthen the deadline or widen the token envelope through the seam.
    assert (
        follow.MAX_OUTPUT_TOKENS,
        follow.MAX_CONTEXT_TOKENS,
        follow.MAX_CALLS_PER_ATTEMPT,
        follow.ATTEMPT_DEADLINE_S,
    ) == (4096, 16_000, 3, 90.0)
    account = uuid.uuid4()
    state_path = tmp_path / "state.json"
    assert follow.account_lock_path(state_path, account) == state_path.with_name(
        f".model-routing-followup-1.{account}.lock"
    )
    assert follow.implementation_fingerprint() == follow.implementation_fingerprint(extra_paths=())


def test_locked_state_never_adopts_another_experiments_records(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    account = uuid.uuid4()
    identity: dict[str, Any] = {"fixtures_sha256": "aa", "schedule_sha256": "bb"}
    path = private / "followup-state.json"
    with follow.locked_state(path, account, identity) as state:
        assert state["version"] == follow.STATE_VERSION
    injected = replace(
        follow.DEFAULT_PROFILE,
        experiment="model-sonnet-upgrade/1",
        state_version="model-sonnet-upgrade-state/1",
        lock_holder_label="Sonnet upgrade runner",
    )
    before = path.read_bytes()
    with pytest.raises(follow.PolicyViolation, match="state version changed"):
        with follow.locked_state(path, account, identity, profile=injected):
            pass
    assert path.read_bytes() == before
    other = private / "upgrade-state.json"
    with follow.locked_state(other, account, identity, profile=injected) as state:
        assert state["version"] == injected.state_version
    assert sorted(item.name for item in private.iterdir() if item.name.startswith(".")) == [
        f".model-routing-followup-1.{account}.lock",
        f".model-sonnet-upgrade-1.{account}.lock",
    ]


@pytest.mark.asyncio
async def test_admission_charges_each_dispatch_to_its_own_profile_caps(
    corpus: follow.FixtureSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The injected ceiling is enforced per dispatch, not only at preflight."""
    schedule = follow.build_schedule(corpus)
    ledger = {"exposure": 0}

    async def exposure(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(ledger["exposure"], 0, 0)

    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", exposure)

    def context(profile: follow.ExperimentProfile, recorded_calls: int) -> follow.RunContext:
        calls = [{"account_charge_microusd": 0} for _ in range(recorded_calls)]
        state: dict[str, Any] = {
            "attempts": {"a": {"calls": calls}},
            "account_ledger_baseline": {"exposure_microusd": 0},
        }
        ledger["exposure"] = 0
        return cast(
            follow.RunContext,
            SimpleNamespace(
                state=state,
                schedule=schedule,
                pool=object(),
                account=uuid.uuid4(),
                period="2026-09",
                account_ceiling_microusd=25_000_000,
                profile=profile,
            ),
        )

    default_ctx = context(follow.DEFAULT_PROFILE, 0)
    assert await follow.admit_call(default_ctx, 20_000_000, 3) == 0
    with pytest.raises(follow.AccountingViolation, match="incremental"):
        await follow.admit_call(default_ctx, 20_000_001, 3)

    stricter = replace(
        follow.DEFAULT_PROFILE,
        experiment="model-sonnet-upgrade/1",
        incremental_cap_microusd=14_000_000,
        total_attempts=56,
        dispatch_bound=168,
    )
    strict_ctx = context(stricter, 0)
    # This experiment's 120-attempt schedule does not fit the injected envelope.
    with pytest.raises(follow.PolicyViolation, match="attempt envelope"):
        await follow.admit_call(strict_ctx, 1, 3)
    strict_ctx.schedule = schedule[:56]
    assert await follow.admit_call(strict_ctx, 14_000_000, 3) == 0
    with pytest.raises(follow.AccountingViolation, match="incremental"):
        await follow.admit_call(strict_ctx, 14_000_001, 3)
    # The follow-up's looser USD 20 allowance is unreachable under the tighter cap.
    with pytest.raises(follow.AccountingViolation, match="incremental"):
        await follow.admit_call(strict_ctx, 19_999_999, 3)
    exhausted = context(stricter, 168)
    exhausted.schedule = schedule[:56]
    with pytest.raises(follow.PolicyViolation, match="dispatch envelope"):
        await follow.admit_call(exhausted, 1, 3)


def test_phase_gate_and_results_discriminators_follow_the_profile(
    corpus: follow.FixtureSet,
) -> None:
    schedule = follow.build_schedule(corpus)
    diagnostic = follow.schedule_for_stage(schedule, "diagnostic")
    state: dict[str, Any] = {
        "attempts": {attempt.attempt_id: {"status": "completed"} for attempt in diagnostic[:-1]},
        "account_ledger_baseline": {"exposure_microusd": 0},
        "identity": {"fixtures_sha256": "aa"},
        "stages": {},
    }
    with pytest.raises(
        follow.PolicyViolation,
        match="held-out stage requires all 72 recorded diagnostic attempts; 1 unrecorded",
    ):
        follow.phase_admission(state, schedule, "heldout")
    with pytest.raises(follow.PolicyViolation, match="unknown phase"):
        follow.phase_admission(state, schedule, "regression")
    injected = replace(
        follow.DEFAULT_PROFILE,
        experiment="model-sonnet-upgrade/1",
        results_artifact_version="model-sonnet-upgrade-results/1",
        generated_by="scripts/model_sonnet_upgrade.py",
        incremental_cap_microusd=14_000_000,
        dispatch_bound=168,
        phase_stages={"diagnostic": "diagnostic", "regression": "heldout"},
        phase_labels={"diagnostic": "diagnostic phase", "regression": "regression phase"},
        gated_phase="regression",
    )
    with pytest.raises(
        follow.PolicyViolation,
        match="regression phase requires all 72 recorded diagnostic attempts; 1 unrecorded",
    ):
        follow.phase_admission(state, schedule, "regression", profile=injected)
    state["attempts"][diagnostic[-1].attempt_id] = {"status": "completed"}
    follow.phase_admission(state, schedule, "regression", profile=injected)

    document = follow.pending_results(state, corpus.by_id(), schedule)
    assert document["artifact_version"] == follow.RESULTS_ARTIFACT_VERSION
    assert document["generated_by"] == "scripts/model_routing_followup.py"
    assert document["caps"] == {
        "incremental_cap_microusd": 20_000_000,
        "aggregate_cap_microusd": 25_000_000,
    }
    assert document["counts"]["dispatch_bound"] == 360
    assert document["notes"].startswith("Human adjudication required.")
    row = document["attempts"][0]
    assert sorted(row) == list(RESULTS_ROW_KEYS)
    assert row["semantic_verdict"] == "pending" and row["reviewer"] is None

    rerun = follow.pending_results(
        state,
        corpus.by_id(),
        schedule,
        profile=injected,
        attempt_extension=lambda attempt, case, entry: {"evidence_class": case.stage},
        document_extension={"provider_pin": "google-vertex/europe"},
    )
    assert rerun["artifact_version"] == "model-sonnet-upgrade-results/1"
    assert rerun["generated_by"] == "scripts/model_sonnet_upgrade.py"
    assert rerun["caps"]["incremental_cap_microusd"] == 14_000_000
    assert rerun["counts"]["dispatch_bound"] == 168
    assert rerun["provider_pin"] == "google-vertex/europe"
    assert rerun["attempts"][0]["evidence_class"] == "diagnostic"
    assert set(RESULTS_ROW_KEYS) < set(rerun["attempts"][0])
    # Extensions may only add: a reserved accounting or verdict key is refused.
    with pytest.raises(follow.PolicyViolation, match="reserved key"):
        follow.pending_results(
            state,
            corpus.by_id(),
            schedule,
            profile=injected,
            attempt_extension=lambda *_args: {"semantic_verdict": "acceptable"},
        )
    with pytest.raises(follow.PolicyViolation, match="reserved key"):
        follow.pending_results(
            state,
            corpus.by_id(),
            schedule,
            profile=injected,
            document_extension={"caps": {"incremental_cap_microusd": 1}},
        )
