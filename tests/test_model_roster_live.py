"""No-network checks of roster live preflight, persistence and simulated loops."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.entitlements import EntitlementService
from scripts import model_roster_live as live
from scripts import model_roster_pilot as pilot


def service_double(value: SimpleNamespace) -> EntitlementService:
    """Use a lightweight test double where the runner expects a service."""
    return cast(EntitlementService, value)


@pytest.fixture
def fixtures():
    return pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH)


@pytest.fixture
def frozen_in_period_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the live clock just inside the simulated period (mid 2026-09).

    Only the tests that exercise the *accepting* path of
    ``ensure_period_window`` use this: the guard genuinely requires the real
    host clock to sit safely before the next funded month, which fails on any
    machine running the suite after the period has rolled over. The negative
    near-boundary and rollover tests retain their independent clock setup.
    """

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = cls(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(live, "datetime", FrozenClock)


def test_state_identity_restart_lock_and_pending_cost(tmp_path: Path, fixtures):
    path = tmp_path / "state.json"
    identity = {
        "fixtures_sha256": fixtures.sha256,
        "period_key": "2026-09",
        "pins": {"luna": "route-1"},
    }
    with live.locked_state(path, identity) as state:
        state["attempts"]["O01-luna-r1"] = {
            "status": "in_progress",
            "calls": [{"status": "dispatched_unknown", "provider_cost_usd": None}],
            "schema_valid": None,
        }
        live.write_state(path, state)
        with pytest.raises(live.LiveError, match="lock"):
            with live.locked_state(path, identity):
                pass
    with live.locked_state(path, identity) as resumed:
        assert resumed["attempts"]["O01-luna-r1"]["status"] == "in_progress"
        output = live.pending_results(resumed, fixtures)
        assert output["fixtures_sha256"] == fixtures.sha256
        assert output["attempts"][0]["cost_usd"] is None
        assert output["attempts"][0]["semantic_verdict"] == "pending"
    for change in (
        {"period_key": "2026-10"},
        {"pins": {"luna": "route-2"}},
        {"fixtures_sha256": "0" * 64},
    ):
        with pytest.raises(live.LiveError, match="identity drift"):
            with live.locked_state(path, {**identity, **change}):
                pass


def test_commercial_cap_and_trial_are_hard_gates():
    def policy(cap=25_000_000, trial=False, concurrency=1):
        return SimpleNamespace(
            period="calendar_month_utc",
            trial=SimpleNamespace(
                enabled=trial,
                granted_on_account_creation=False,
                budget_microusd=0,
                extended_agents=0,
            ),
            plan=lambda name: SimpleNamespace(
                limits=SimpleNamespace(
                    monthly_budget_microusd=cap,
                    max_concurrent_operations=concurrency,
                )
            ),
        )

    live.validate_commercial(policy())
    with pytest.raises(live.LiveError, match="budget"):
        live.validate_commercial(policy(cap=25_000_001))
    with pytest.raises(live.LiveError, match="trial"):
        live.validate_commercial(policy(trial=True))
    with pytest.raises(live.LiveError, match="concurrency"):
        live.validate_commercial(policy(concurrency=2))


def test_schema_numeric_bounds_and_nested_extra_fields():
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "number", "minimum": 0, "maximum": 5},
            "nested": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            },
        },
        "required": ["value", "nested"],
        "additionalProperties": False,
    }
    valid = {"value": 2.5, "nested": {"count": 1}}
    assert live.schema_matches(valid, schema)
    for invalid in (True, -1, 6, float("inf"), float("nan")):
        assert not live.schema_matches({**valid, "value": invalid}, schema)
    assert not live.schema_matches({**valid, "nested": {"count": 1, "extra": 2}}, schema)


def test_torn_checkpoint_is_not_replayed(tmp_path):
    path = tmp_path / "state.json"
    path.with_name("state.json.tmp").write_text("partial")
    with pytest.raises(live.LiveError, match="incomplete state checkpoint"):
        with live.locked_state(path, {}):
            pytest.fail("Torn state must not be entered")


def test_provider_display_echo_keeps_exact_outbound_subroute():
    assert live.provider_echo_matches("Inceptron", "inceptron/fp8")
    assert live.provider_echo_matches("CoreWeave", "coreweave/fp8")
    assert live.provider_echo_matches("inceptron/fp8", "inceptron/fp8")
    assert not live.provider_echo_matches("inceptron/fp4", "inceptron/fp8")
    assert not live.provider_echo_matches("Unrelated provider", "inceptron/fp8")
    assert not live.provider_echo_matches("", "inceptron/fp8")


def test_batch_pause_preserves_pin_map_and_rejects_unpinned_candidates():
    pins = {"glm-flash": "pilot-glm-flash", "deepseek-flash": "pilot-deepseek-flash"}
    assert live.batch_candidates(pins, None) == set(pins)
    assert live.batch_candidates(pins, ["deepseek-flash"]) == {"deepseek-flash"}
    assert pins == {"glm-flash": "pilot-glm-flash", "deepseek-flash": "pilot-deepseek-flash"}
    for invalid in ([], ["luna"]):
        with pytest.raises(live.LiveError, match="batch candidates must be pinned"):
            live.batch_candidates(pins, invalid)


def test_exact_route_pin_refuses_ambiguous_or_unqualified(fixtures):
    class Route:
        def __init__(self, key):
            self.route_id = key
            self.model = "openrouter/vendor/revision"
            self.provider = "openrouter"
            self.route_class = "routine"
            self.price_ceiling = object()

        def is_approved(self, requirements):
            return self.route_id != "unapproved"

        def transport_payload(self, requirements):
            return {"provider": {"only": ["provider-pin"]}}

    labels = {attempt.candidate_label for attempt in pilot.build_attempts(fixtures)}
    pins = {label: label for label in labels}
    inference = SimpleNamespace(
        routes={label: Route(label) for label in labels}, requirements=object()
    )
    inference.route = inference.routes.get
    # A single model exposed under multiple approved endpoints cannot be selected
    # by guarded_completion's model-only explicit selector.
    with pytest.raises(live.LiveError, match="ambiguous"):
        live.pinned_routes(pins, inference, fixtures)
    for label, route in inference.routes.items():
        route.model = f"openrouter/vendor/{label}"
    assert set(live.pinned_routes(pins, inference, fixtures)) == labels
    selected = {label: pins[label] for label in ("deepseek-flash", "glm-flash")}
    assert set(live.pinned_routes(selected, inference, fixtures)) == set(selected)
    with pytest.raises(live.LiveError, match="nonempty planned candidate subset"):
        live.pinned_routes({}, inference, fixtures)
    with pytest.raises(live.LiveError, match="nonempty planned candidate subset"):
        live.pinned_routes({"invented-candidate": "luna"}, inference, fixtures)
    inference.routes["luna"].route_id = "unapproved"
    with pytest.raises(live.LiveError, match="unqualified"):
        live.pinned_routes(pins, inference, fixtures)


@pytest.mark.asyncio
async def test_isolated_db_account_and_period_preflight():
    account = uuid.uuid4()
    row: dict[str, Any] = {"name": "daemon_roster_pilot_20260927", "marker": live.DB_MARKER}
    user = {"id": account, "name": live.ACCOUNT_MARKER, "username": live.USERNAME_MARKER}
    record = SimpleNamespace(
        plan=SimpleNamespace(value="pro"),
        plan_source="admin",
        status=SimpleNamespace(value="active"),
        trial_state=SimpleNamespace(value="exhausted"),
        byok_enabled=False,
        trial_budget_microusd=0,
        trial_consumed_microusd=0,
        trial_reserved_microusd=0,
        trial_extended_agents=0,
    )

    class Connection:
        async def fetchrow(self, sql, *args):
            return user if "FROM users WHERE" in sql else row

        async def fetchval(self, sql, *args):
            if "_migrations" in sql:
                return True
            return funded[0] if "entitlement_accounts" in sql else duplicate[0]

        async def fetch(self, sql, *args):
            return [{"filename": name} for name in applied]

    @asynccontextmanager
    async def acquire():
        yield Connection()

    pool = SimpleNamespace(acquire=acquire)
    duplicate, funded = [0], [0]
    applied = [path.name for path in sorted(live.MIGRATIONS_DIR.glob("*.sql"))]
    service = SimpleNamespace(
        store=SimpleNamespace(get_account=AsyncMock(return_value=record)),
        policy=SimpleNamespace(
            plan=lambda name: SimpleNamespace(
                limits=SimpleNamespace(monthly_budget_microusd=25_000_000)
            )
        ),
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                plan=SimpleNamespace(value="pro"),
                period_key="2026-09",
                recurring_budget_microusd=25_000_000,
                trial_funds_premium=False,
            )
        ),
        current_period_key=lambda: "2026-09",
    )
    assert (
        await live.validate_database(pool, account, service_double(service), "2026-09")
        == row["name"]
    )
    row["marker"] = None
    with pytest.raises(live.LiveError, match="marker"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    row["marker"] = live.DB_MARKER
    # A schema behind the code is refused before any account or ledger check (#403).
    latest = applied.pop()
    with pytest.raises(live.LiveError, match="behind the code: 1 unapplied"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    applied.append(latest)
    record.trial_budget_microusd = 1
    with pytest.raises(live.LiveError, match="trial"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    record.trial_budget_microusd = 0
    duplicate[0] = 1
    with pytest.raises(live.LiveError, match="duplicate"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    duplicate[0] = 0
    funded[0] = 1
    with pytest.raises(live.LiveError, match="funded account"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    funded[0] = 0
    user["id"] = uuid.uuid4()
    with pytest.raises(live.LiveError, match="dedicated"):
        await live.validate_database(pool, account, service_double(service), "2026-09")
    user["id"] = account
    service.current_period_key = lambda: "2026-10"
    with pytest.raises(live.LiveError, match="period drift"):
        await live.validate_database(pool, account, service_double(service), "2026-09")


def test_fixture_dispatch_match_before_positional_and_schema(fixtures):
    case = fixtures.by_id()["R04"]
    assert case.tools
    positions = {}
    spec = case.tools[0]
    entry = case.tool_responses[spec.name][0]
    if "match" in entry:
        args = {**entry["match"]}
        for required in spec.parameters.get("required", []):
            args.setdefault(required, "query")
        if live.schema_matches(args, spec.parameters):
            assert live.simulate(case, spec.name, args, positions) == {
                k: v for k, v in entry.items() if k != "match"
            }
            assert positions == {}
    with pytest.raises(live.LiveError, match="undeclared"):
        live.simulate(case, "delete_all", {}, positions)
    assert not live.schema_matches(
        {"query": "x", "unapproved": True},
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    assert live.schema_matches(
        {"facts": [{"text": "x", "status": "negated"}]}, fixtures.by_id()["U03"].schema
    )


def test_pinned_candidate_and_budget_preflight(monkeypatch, fixtures):
    case = fixtures.by_id()["O01"]
    route = SimpleNamespace(
        model="openrouter/vendor/revision", route_id="pinned", max_output_tokens=1024
    )
    params = live.request_for(case, route, [{"role": "user", "content": case.prompt}], [])
    monkeypatch.setattr(live.compute_runtime, "_request_bound", lambda request: 100)
    monkeypatch.setattr(
        live.compute_runtime,
        "_priced_candidates",
        lambda *args, **kwargs: [(250, 1024, route, False, None)],
    )
    assert live.verify_candidate(case, route, object(), params) == 250
    monkeypatch.setattr(live.compute_runtime, "_priced_candidates", lambda *args, **kwargs: [])
    with pytest.raises(live.LiveError, match="ineligible"):
        live.verify_candidate(case, route, object(), params)
    wrong = SimpleNamespace(route_id="other")
    monkeypatch.setattr(
        live.compute_runtime,
        "_priced_candidates",
        lambda *args, **kwargs: [(250, 1024, wrong, False, None)],
    )
    with pytest.raises(live.LiveError, match="ineligible"):
        live.verify_candidate(case, route, object(), params)


def test_refuses_calls_near_original_boundary(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 30, 23, 59, 58, tzinfo=timezone.utc)

    monkeypatch.setattr(live, "datetime", Clock)
    service = SimpleNamespace(current_period_key=lambda: "2026-09")
    with pytest.raises(live.LiveError, match="too close"):
        live.ensure_period_window(service_double(service), "2026-09")


@pytest.mark.asyncio
async def test_loop_counts_every_dispatch_and_does_not_replay_interrupted(
    tmp_path, monkeypatch, fixtures, frozen_in_period_clock
):
    case = fixtures.by_id()["O03"]
    route = SimpleNamespace(
        model="openrouter/vendor/test",
        route_id="pinned",
        max_output_tokens=1024,
        transport=SimpleNamespace(provider_only=("provider-pin",)),
    )
    account = uuid.uuid4()
    attempt = next(a for a in pilot.build_attempts(fixtures) if a.case_id == case.case_id)
    identity = {"period_key": "2026-09", "commercial_sha256": "hash", "inference_sha256": "hash"}
    state = {"attempts": {}}
    path = tmp_path / "state.json"
    monkeypatch.setattr(live, "digest", lambda path: "hash")
    monkeypatch.setattr(live, "validate_database", AsyncMock())
    service = SimpleNamespace(
        current_period_key=lambda: "2026-09", resolve=AsyncMock(return_value=object())
    )
    scope = SimpleNamespace(service=service, settled={})

    @asynccontextmanager
    async def account_compute(*args, **kwargs):
        assert kwargs["expected_period"] == identity["period_key"]
        yield scope

    monkeypatch.setattr(live.compute_runtime, "account_compute", account_compute)
    monkeypatch.setattr(live.compute_runtime, "_request_bound", lambda params: 100)
    monkeypatch.setattr(
        live.compute_runtime,
        "_priced_candidates",
        lambda *args, **kwargs: [(10, 1024, route, False, None)],
    )
    routing = SimpleNamespace(selected_route_id="pinned", selected_model=route.model)
    monkeypatch.setattr(live.model_routing, "current_routing", lambda: routing)
    tool_name = case.tools[0].name
    arguments = {
        key: "Ridgeway incident postmortem" for key in case.tools[0].parameters.get("required", [])
    }

    async def completion(**params):
        assert params["model"] == route.model
        assert "delete_all" not in json.dumps(params)
        number = len(scope.settled) + 1
        scope.settled[number] = 10
        if number == 1:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            reason = "tool_calls"
        else:
            message = {"content": "The revision date is in the returned document."}
            reason = "stop"
        return {
            "model": route.model,
            "choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }

    mock = AsyncMock(side_effect=completion)
    monkeypatch.setattr(live.compute_runtime, "guarded_completion", mock)
    await live.run_attempt(
        attempt,
        case,
        route,
        account,
        object(),
        service_double(service),
        state,
        path,
        identity,
        path,
        path,
    )
    entry = state["attempts"][attempt.attempt_id]
    assert entry["status"] == "completed"
    assert len(entry["calls"]) == 2 == mock.await_count
    assert sum(c["account_charge_microusd"] for c in entry["calls"]) == 20
    assert all(c["provider_cost_usd"] is None for c in entry["calls"])
    assert len(entry["tool_steps"]) == 1
    assert live.pending_results(state, fixtures)["attempts"][0]["cost_usd"] is None


@pytest.mark.asyncio
async def test_period_rollover_refuses_dispatch(tmp_path, monkeypatch, fixtures):
    case = fixtures.by_id()["O01"]
    route = SimpleNamespace(
        model="openrouter/vendor/test",
        route_id="pinned",
        max_output_tokens=1024,
        transport=SimpleNamespace(provider_only=("pin",)),
    )
    attempt = next(a for a in pilot.build_attempts(fixtures) if a.case_id == case.case_id)
    scope = SimpleNamespace(service=SimpleNamespace(resolve=AsyncMock()), settled={})

    @asynccontextmanager
    async def account_compute(*args, **kwargs):
        yield scope

    monkeypatch.setattr(live.compute_runtime, "account_compute", account_compute)
    completion = AsyncMock()
    monkeypatch.setattr(live.compute_runtime, "guarded_completion", completion)
    state = {"attempts": {}}
    with pytest.raises(live.LiveError, match="period ended"):
        await live.run_attempt(
            attempt,
            case,
            route,
            uuid.uuid4(),
            object(),
            service_double(SimpleNamespace(current_period_key=lambda: "2026-10")),
            state,
            tmp_path / "state.json",
            {"period_key": "2026-09"},
            tmp_path,
            tmp_path,
        )
    completion.assert_not_awaited()
    assert state["attempts"][attempt.attempt_id]["status"] == "failed"


@pytest.mark.asyncio
async def test_served_model_drift_fails_and_keeps_raw_evidence(
    tmp_path, monkeypatch, fixtures, frozen_in_period_clock
):
    case = fixtures.by_id()["O01"]
    route = SimpleNamespace(
        model="openrouter/vendor/approved",
        route_id="route-1",
        max_output_tokens=1024,
        transport=SimpleNamespace(provider_only=("provider-one",)),
    )
    attempt = next(a for a in pilot.build_attempts(fixtures) if a.case_id == case.case_id)
    state = {"attempts": {}}
    scope = SimpleNamespace(
        service=SimpleNamespace(resolve=AsyncMock(return_value=object())), settled={}
    )

    @asynccontextmanager
    async def account_compute(*args, **kwargs):
        yield scope

    monkeypatch.setattr(live.compute_runtime, "account_compute", account_compute)
    monkeypatch.setattr(live, "validate_database", AsyncMock())
    monkeypatch.setattr(live, "digest", lambda path: "hash")
    monkeypatch.setattr(live.compute_runtime, "_request_bound", lambda params: 100)
    monkeypatch.setattr(
        live.compute_runtime,
        "_priced_candidates",
        lambda *a, **kw: [(10, 1024, route, False, None)],
    )
    monkeypatch.setattr(
        live.model_routing,
        "current_routing",
        lambda: SimpleNamespace(selected_route_id="route-1", selected_model=route.model),
    )
    completion = AsyncMock(
        return_value={
            "model": "vendor/other",
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        }
    )
    monkeypatch.setattr(live.compute_runtime, "guarded_completion", completion)
    with pytest.raises(live.LiveError, match="served model drift"):
        await live.run_attempt(
            attempt,
            case,
            route,
            uuid.uuid4(),
            object(),
            service_double(SimpleNamespace(current_period_key=lambda: "2026-09")),
            state,
            tmp_path / "state.json",
            {"period_key": "2026-09", "commercial_sha256": "hash", "inference_sha256": "hash"},
            tmp_path,
            tmp_path,
        )
    assert completion.await_count == 1
    entry = state["attempts"][attempt.attempt_id]
    assert entry["status"] == "uncertain"
    assert entry["calls"][0]["raw_response"]["model"] == "vendor/other"


def test_pending_migrations_lists_unapplied_files_in_apply_order():
    names = [path.name for path in sorted(live.MIGRATIONS_DIR.glob("*.sql"))]
    assert names and live.pending_migrations(names) == []
    assert live.pending_migrations(names[:-2]) == names[-2:]
    # Rollback scripts live in a subdirectory and are never expected.
    assert not any("rollback" in name or name.endswith("down.sql") for name in names)


@pytest.mark.asyncio
async def test_untracked_schema_is_refused():
    class Connection:
        async def fetchval(self, sql, *args):
            return False

    with pytest.raises(live.LiveError, match="no migration record"):
        await live.require_current_schema(Connection())
