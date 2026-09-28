"""No-network checks of the endpoint reliability runner.

The runtime seam (``guarded_completion(_route_id=..., _dispatch_timeout_s=...)``)
is owned by the compute-runtime change and is mocked here: these tests prove the
*runner's* safety properties, not the seam's.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import BudgetExceeded, CapabilityDenied
from scripts import model_endpoint_reliability as rel
from scripts import model_roster_live as live
from scripts import model_roster_pilot as pilot

UTC = timezone.utc
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
BOUND = 1_000
MODEL = "openrouter/z-ai/glm-5.3-flash"
PERIOD = rel.ORIGINAL_FUNDED_PERIOD


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def unavailable(
    category: str,
    *,
    status_code: int | None = None,
    retry_after: object = None,
    retryable: bool = False,
) -> compute_runtime.ComputeUnavailable:
    """The seam's typed metadata, attached exactly as the runtime will attach it."""
    exc = compute_runtime.ComputeUnavailable(
        "capacity_unavailable", "Qualified provider unavailable"
    )
    exc.category = category  # type: ignore[attr-defined]
    exc.status_code = status_code  # type: ignore[attr-defined]
    exc.retry_after_seconds = retry_after  # type: ignore[attr-defined]
    exc.retryable = retryable  # type: ignore[attr-defined]
    return exc


def make_route(route_id: str, provider: str, model: str = MODEL) -> Any:
    return SimpleNamespace(
        route_id=route_id,
        provider="openrouter",
        model=model,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        route_class="routine",
        price_ceiling=SimpleNamespace(microusd_per_1m_prompt=1),
        max_output_tokens=8192,
        max_context_tokens=200_000,
        is_approved=lambda requirements: True,
        transport_payload=lambda requirements: {"provider": {"only": [provider]}},
        transport=SimpleNamespace(provider_only=(provider,), allow_fallbacks=False),
    )


class Ledger:
    """Authoritative exposure, moved only when a dispatch really settles."""

    def __init__(self, spent: int = 0, reserved: int = 0, open_holds: int = 0) -> None:
        self.spent = spent
        self.reserved = reserved
        self.open_holds = open_holds
        self.reads = 0

    def exposure(self) -> rel.LedgerExposure:
        self.reads += 1
        return rel.LedgerExposure(self.spent, self.reserved, self.open_holds)

    def settle(self, amount: int) -> None:
        self.reserved = max(0, self.reserved - amount)
        self.spent += amount


#: One schema-valid answer per utility case, so a test that means to exercise
#: reliability is never confounded by an unrelated schema failure.
SCHEMA_VALID_ANSWERS: dict[str, str] = {
    "U01": json.dumps({"title": "Retrospective review"}),
    "U03": json.dumps({"facts": [{"text": "Merged in March.", "status": "confirmed"}]}),
    "U05": json.dumps({"city": None, "since": None, "employer": None, "absent_fields": ["city"]}),
    "U08": json.dumps({"summary": "A 2026 change.", "identifiers": ["CHG-1"]}),
}


def answer(params: dict[str, Any], text: str = '{"ok": true}') -> dict[str, Any]:
    return {
        "model": MODEL,
        "provider": None,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
    }


def case_answer(case_id: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A handler that answers one utility case with a schema-valid object."""
    return lambda params: answer(params, SCHEMA_VALID_ANSWERS[case_id])


@dataclass
class Harness:
    ctx: rel.RunContext
    ledger: Ledger
    clock: dict[str, float]
    waits: list[float] = field(default_factory=list)

    @property
    def state(self) -> dict[str, Any]:
        return self.ctx.state

    def calls(self) -> list[dict[str, Any]]:
        return self.ctx.state.setdefault("_calls", [])  # type: ignore[arg-type]

    def entry(self, logical: rel.Logical) -> dict[str, Any]:
        return self.ctx.state["logicals"][logical.logical_id]


# ---------------------------------------------------------------------------
# Harness construction
# ---------------------------------------------------------------------------


@pytest.fixture
def fixtures() -> pilot.FixtureSet:
    return pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH)


@pytest.fixture
def routes() -> dict[str, Any]:
    return {
        "primary": make_route("route-primary", "inceptron/fp8"),
        "alternate": make_route("route-alternate", "together"),
    }


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


def install_pricing(
    monkeypatch: pytest.MonkeyPatch, routes: Mapping[str, Any], bound: int = BOUND
) -> None:
    """One deterministic route-specific bound per approved route, as the
    runtime would price it. ``_request_bound`` stays the real implementation so a
    test cannot pass by hiding the internal seam keys from pricing."""

    def candidates(_resolved: Any, _bound: int, _params: Any, model: str, **_kw: Any) -> list[Any]:
        return [
            (bound, 4096, route, False, None) for route in routes.values() if route.model == model
        ]

    monkeypatch.setattr(rel.compute_runtime, "_priced_candidates", candidates)


def make_ctx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
    state: dict[str, Any] | None = None,
    ceiling: int = 25_000_000,
    bound: int = BOUND,
) -> Harness:
    """A frozen clock, a private state file and a mocked pricing helper."""
    clock = {"now": 1000.0}
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["now"] += seconds

    async def exposure(_pool: Any, _account: uuid.UUID, _period: str) -> rel.LedgerExposure:
        return ledger.exposure()

    install_pricing(monkeypatch, routes, bound)
    monkeypatch.setattr(rel, "read_ledger_exposure", exposure)
    monkeypatch.setattr(rel, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rel, "utcnow", lambda: NOW)
    monkeypatch.setattr(rel.asyncio, "sleep", fake_sleep)

    identity = {"account": "fixed", "period_key": PERIOD, "plan_sha256": "p"}
    resolved_state = state if state is not None else rel.empty_state(identity)
    resolved_state.setdefault(
        "account_ledger_baseline",
        {
            "exposure_microusd": ledger.exposure().total_microusd,
            "open_holds": 0,
            "recorded_at": rel.iso(NOW),
        },
    )
    service = SimpleNamespace(resolve=AsyncMock(return_value=object()))
    state_path = tmp_path / "state.json"
    rel.write_state(state_path, resolved_state)
    ctx = rel.RunContext(
        pool=object(),
        account=uuid.uuid4(),
        service=cast(EntitlementService, service),
        period=PERIOD,
        identity=identity,
        state=resolved_state,
        state_path=state_path,
        routes=routes,
        cases={case_id: fixtures.by_id()[case_id] for case_id in rel.UTILITY_CASE_IDS},
        account_ceiling_microusd=ceiling,
        ends_at=rel.period_end(PERIOD),
    )
    return Harness(ctx=ctx, ledger=ledger, clock=clock, waits=waits)


def install_runtime(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[int, dict[str, Any]], Any],
    *,
    settles: bool = True,
) -> list[dict[str, Any]]:
    """Mock the seam, the account scope and the routed-selection record."""
    calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def account_compute(*_args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        assert kwargs.get("expected_period") == PERIOD
        assert kwargs.get("profile") == "routine"
        with rel.model_routing.routing_context("routine"):
            yield SimpleNamespace(service=SimpleNamespace(), settled={})

    # The runtime's own selection record is the corroboration the runner checks
    # after a response; route drift here must be reproducible in tests.

    async def guarded(**params: Any) -> Any:
        calls.append(params)
        rel.model_routing.current_routing().record_selection(
            model=MODEL, route_id=params["_route_id"], group=None, explicit=True
        )
        outcome = handler(len(calls), params)
        if isinstance(outcome, BaseException):
            # A real seam raises; only a settled success moves the ledger.
            raise outcome
        if settles:
            harness.ledger.settle(BOUND // 2)
        return outcome

    monkeypatch.setattr(rel.compute_runtime, "account_compute", account_compute)
    monkeypatch.setattr(rel.compute_runtime, "guarded_completion", guarded)
    return calls


def logical_for(arm: str, case_id: str = "U01", repeat: int = 1) -> rel.Logical:
    return rel.Logical(
        logical_id=f"rel-{case_id}-{arm}-r{repeat}",
        arm=arm,
        case_id=case_id,
        repeat=repeat,
        route_roles=rel.ARM_ROUTE_ROLES[arm],
    )


def cool(state: dict[str, Any], route_id: str, seconds: float = 60.0) -> None:
    state["cooldowns"][route_id] = {
        "route_id": route_id,
        "until": rel.iso(NOW + timedelta(seconds=seconds)),
        "source": "provider",
        "recorded_at": rel.iso(NOW),
        "capped_at_period_end": False,
    }


# ---------------------------------------------------------------------------
# Run identity, durable state and resume
# ---------------------------------------------------------------------------


def test_state_identity_is_frozen_and_locked_or_torn_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    identity = {"account": "a", "period_key": PERIOD, "plan_sha256": "p"}
    with rel.locked_state(path, identity) as state:
        state["logicals"]["rel-U01-primary_only-r1"] = {"status": "in_progress", "dispatches": []}
        rel.write_state(path, state)
        with pytest.raises(rel.ReliabilityError, match="holds the state lock"):
            with rel.locked_state(path, identity):
                pytest.fail("a second runner must not enter the state")

    # A recorded identity is immutable: no new routes, period or budget on restart.
    with rel.locked_state(path, identity) as resumed:
        assert resumed["logicals"]["rel-U01-primary_only-r1"]["status"] == "in_progress"
    for drift in ({"account": "b"}, {"period_key": "2026-10"}, {"plan_sha256": "other"}):
        with pytest.raises(rel.ReliabilityError, match="identity drift"):
            with rel.locked_state(path, {**identity, **drift}):
                pytest.fail("identity drift must not be adopted")

    torn = tmp_path / "torn.json"
    torn.with_name("torn.json.tmp").write_text("partial")
    with pytest.raises(rel.ReliabilityError, match="incomplete state checkpoint"):
        with rel.locked_state(torn, identity):
            pytest.fail("a torn checkpoint must not be repaired or replayed")

    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(rel.ReliabilityError, match="private"):
        with rel.locked_state(public / "state.json", identity):
            pytest.fail("state must live in a private directory")


def test_plan_and_ids_are_ours_and_never_pilot_attempt_ids(fixtures: pilot.FixtureSet) -> None:
    logicals = rel.build_logicals()
    assert len(logicals) == 24
    assert {item.case_id for item in logicals} == set(rel.UTILITY_CASE_IDS)
    assert {item.arm for item in logicals} == set(rel.ARMS)
    assert {item.repeat for item in logicals} == {1, 2}
    assert rel.TOTAL_DISPATCH_BOUND == 34
    assert rel.LOGICAL_DISPATCH_BOUND == 32
    assert rel.MAX_DISPATCHES_PER_LOGICAL == {
        "primary_only": 1,
        "alternate_only": 1,
        "primary_then_alternate": 2,
    }
    # Deterministic interleaving: repeat, then case, then arm.
    assert [item.logical_id for item in logicals[:3]] == [
        "rel-U01-primary_only-r1",
        "rel-U01-alternate_only-r1",
        "rel-U01-primary_then_alternate-r1",
    ]
    assert rel.build_logicals() == logicals
    pilot_ids = {attempt.attempt_id for attempt in pilot.build_attempts(fixtures)}
    assert not {item.logical_id for item in logicals} & pilot_ids
    assert [item.smoke_id for item in rel.build_smokes()] == ["smoke-primary", "smoke-alternate"]
    assert rel.plan_fingerprint(logicals, rel.build_smokes()) == rel.plan_fingerprint(
        logicals, rel.build_smokes()
    )
    assert rel.plan_fingerprint(logicals[:1], rel.build_smokes()) != rel.plan_fingerprint(
        logicals, rel.build_smokes()
    )


# ---------------------------------------------------------------------------
# Route preflight: one exact model, two explicit pins
# ---------------------------------------------------------------------------


def test_two_routes_must_be_distinct_and_share_one_exact_model(routes: dict[str, Any]) -> None:
    by_id = {route.route_id: route for route in routes.values()}
    inference = SimpleNamespace(routes=by_id, requirements=object(), route=by_id.get)
    assert set(rel.qualified_routes(inference, "route-primary", "route-alternate")) == {
        "primary",
        "alternate",
    }
    with pytest.raises(rel.ReliabilityError, match="distinct"):
        rel.qualified_routes(inference, "route-primary", "route-primary")
    with pytest.raises(rel.ReliabilityError, match="unqualified"):
        rel.qualified_routes(inference, "route-primary", "route-missing")

    other = make_route("route-other", "together", model="openrouter/vendor/other")
    inference.routes["route-other"] = other
    with pytest.raises(rel.ReliabilityError, match="same exact model"):
        rel.qualified_routes(inference, "route-primary", "route-other")

    unapproved = make_route("route-unapproved", "together")
    unapproved.is_approved = lambda requirements: False
    inference.routes["route-unapproved"] = unapproved
    with pytest.raises(rel.ReliabilityError, match="unqualified"):
        rel.qualified_routes(inference, "route-primary", "route-unapproved")

    # Two approved routes for one model is exactly what the original pilot's
    # single-eligible-route helper refuses, which is why it is not reused here.
    with pytest.raises(live.LiveError, match="ambiguous model route pin"):
        live.pinned_routes(
            {"glm-flash": "route-primary", "deepseek-flash": "route-alternate"},
            inference,
            pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH),
        )
    assert "pinned_routes" not in vars(rel)
    assert "verify_candidate" not in vars(rel)


def test_requests_are_tool_free_non_streaming_and_exclude_the_seam_keys(
    routes: dict[str, Any], fixtures: pilot.FixtureSet
) -> None:
    case = fixtures.by_id()["U03"]
    params = rel.completion_params(case, routes["primary"], rel.COMMON_MAX_OUTPUT_TOKENS)
    assert "tools" not in params and "stream" not in params
    assert params["max_tokens"] == rel.COMMON_MAX_OUTPUT_TOKENS
    assert params["response_format"]["json_schema"]["schema"] == case.schema
    assert "_route_id" not in params and "_dispatch_timeout_s" not in params
    # The real runtime bound rejects unknown keys, so this also proves the seam
    # arguments are never part of a priced request.
    input_size = compute_runtime._request_bound(params)
    assert input_size.bound > 0 and input_size.estimate > 0
    smoke = rel.completion_params(None, routes["alternate"], rel.SMOKE_MAX_OUTPUT_TOKENS)
    assert smoke["messages"] == [{"role": "user", "content": rel.SMOKE_PROMPT}]
    assert "response_format" not in smoke
    assert smoke["max_tokens"] == rel.SMOKE_MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# Eligible failure, backup dispatch, recovered vs avoided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_recovers_on_the_backup_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(
        harness,
        monkeypatch,
        lambda count, params: (
            unavailable(
                compute_runtime.FAILURE_RATE_LIMITED,
                status_code=429,
                retry_after=30.0,
                retryable=True,
            )
            if count == 1
            else case_answer("U01")(params)
        ),
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "recovered"
    assert [call["_route_id"] for call in calls] == ["route-primary", "route-alternate"]
    assert entry["dispatch_count"] == 2
    assert entry["dispatches"][0]["failure"] == {
        "category": compute_runtime.FAILURE_RATE_LIMITED,
        "status_code": 429,
        "retry_after_seconds": 30.0,
        "outcome": "fallback",
    }
    # The provider's guidance is durable and route-specific, and it is consulted
    # for backups and later cases, not just the process that saw the 429.
    assert harness.state["cooldowns"]["route-primary"]["until"] == rel.iso(
        NOW + timedelta(seconds=30)
    )
    assert "route-alternate" not in harness.state["cooldowns"]
    # The 429 settles nothing attributable, so it keeps its full bound; the
    # settled backup contributes only what the ledger actually moved.
    assert entry["dispatches"][0]["account_charge_microusd"] is None
    assert entry["dispatches"][1]["account_charge_microusd"] == BOUND // 2
    assert rel.accounted_microusd(harness.state) == BOUND + BOUND // 2


@pytest.mark.asyncio
async def test_each_dispatch_timeout_is_clipped_by_the_remaining_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)

    def handler(count: int, params: dict[str, Any]) -> Any:
        if count == 1:
            # The first endpoint burns 40 of the redundancy arm's 90 seconds.
            harness.clock["now"] += 40.0
            return unavailable(compute_runtime.FAILURE_TIMEOUT, retryable=True)
        return case_answer("U01")(params)

    calls = install_runtime(harness, monkeypatch, handler)
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    timeouts = [call["_dispatch_timeout_s"] for call in calls]
    assert timeouts[0] == rel.ENDPOINT_TIMEOUT_S
    # min(45, 90 - 40) = 45, but a longer first endpoint leaves less to clip by.
    assert timeouts[1] == pytest.approx(rel.ENDPOINT_TIMEOUT_S)
    assert all(0 < value <= rel.ENDPOINT_TIMEOUT_S for value in timeouts)
    assert harness.state["logicals"][logical.logical_id]["outcome"] == "recovered"


@pytest.mark.asyncio
async def test_a_dispatch_past_the_logical_deadline_is_never_sent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)

    def handler(count: int, params: dict[str, Any]) -> Any:
        harness.clock["now"] += rel.REDUNDANT_DEADLINE_S + 1.0
        return unavailable(compute_runtime.FAILURE_RATE_LIMITED, status_code=429, retryable=True)

    calls = install_runtime(harness, monkeypatch, handler)
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    # The primary was already sent; the backup has no remaining deadline.
    assert [call["_route_id"] for call in calls] == ["route-primary"]
    entry = harness.entry(logical)
    assert entry["outcome"] == "deadline_exceeded"
    assert len(entry["dispatches"]) == 1


@pytest.mark.asyncio
async def test_single_arms_never_attempt_a_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    for arm, pinned in (("primary_only", "route-primary"), ("alternate_only", "route-alternate")):
        harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
        calls = install_runtime(
            harness,
            monkeypatch,
            lambda count, params: unavailable(
                "rate_limit", status_code=429, retry_after=1.0, retryable=True
            ),
        )
        logical = logical_for(arm)
        await rel.run_logical(harness.ctx, logical)
        entry = harness.entry(logical)
        assert entry["outcome"] == "failed"
        assert [call["_route_id"] for call in calls] == [pinned]
        assert len(entry["dispatches"]) == 1


@pytest.mark.asyncio
async def test_a_success_never_contacts_the_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(harness, monkeypatch, lambda count, params: case_answer("U01")(params))
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "completed"
    assert len(calls) == 1
    assert entry["schema_valid"] is True
    assert entry["avoided_dispatch"] is False
    assert entry["final_response"] == SCHEMA_VALID_ANSWERS["U01"]


@pytest.mark.asyncio
async def test_a_schema_invalid_answer_is_a_quality_failure_not_a_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(
        harness, monkeypatch, lambda count, params: answer(params, "not json at all")
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "quality:schema_invalid"
    assert entry["schema_valid"] is False
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Non-eligible failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "status"),
    [
        (compute_runtime.FAILURE_AUTHENTICATION_FAILED, 401),
        (compute_runtime.FAILURE_INVALID_REQUEST, 400),
        (compute_runtime.FAILURE_UNSPECIFIED, 500),
        (compute_runtime.FAILURE_RATE_LIMITED, 429),
    ],
)
@pytest.mark.asyncio
async def test_non_eligible_failures_stop_without_a_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
    category: str,
    status: int,
) -> None:
    """A declared-false status, or an undeclared class, never earns a backup."""
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(
        harness,
        monkeypatch,
        lambda count, params: unavailable(category, status_code=status, retryable=False),
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "failed"
    assert len(calls) == 1
    assert entry["dispatches"][0]["failure"]["outcome"] == "stop"


@pytest.mark.parametrize("category", [compute_runtime.FAILURE_SETTLEMENT_FAILED, "policy"])
@pytest.mark.asyncio
async def test_accounting_and_policy_failures_abort_the_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
    category: str,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(
        harness,
        monkeypatch,
        lambda count, params: unavailable(category, retryable=True),
    )
    logical = logical_for("primary_then_alternate")
    with pytest.raises(rel.ReliabilityError, match="aborted the run"):
        await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "aborted"
    assert entry["dispatches"][0]["failure"]["category"] == category
    assert len(entry["dispatches"]) == 1


@pytest.mark.asyncio
async def test_a_capability_denial_aborts_while_a_budget_refusal_is_a_capacity_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(harness, monkeypatch, lambda count, params: CapabilityDenied("chat"))
    with pytest.raises(rel.ReliabilityError, match="aborted the run"):
        await rel.run_logical(harness.ctx, logical_for("primary_only"))
    assert harness.entry(logical_for("primary_only"))["outcome"] == "aborted"

    # A ledger refusal is a truthful capacity stop, not provider unavailability.
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(
        harness,
        monkeypatch,
        lambda count, params: BudgetExceeded(
            requested=BOUND, spent=25_000_000, reserved=0, ceiling=25_000_000
        ),
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "capacity_stop"
    assert len(calls) == 1
    assert entry["dispatches"][0]["failure"] == {
        "category": "budget",
        "status_code": None,
        "retry_after_seconds": None,
        "outcome": "capacity_stop",
    }
    assert rel._arm_summary([entry])["capacity_stops"] == 1
    assert "route-primary" not in harness.state["cooldowns"]


@pytest.mark.asyncio
async def test_cancellation_records_an_interrupted_dispatch_and_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)

    async def cancelled(**params: Any) -> Any:
        raise asyncio.CancelledError()

    install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    monkeypatch.setattr(rel.compute_runtime, "guarded_completion", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await rel.run_logical(harness.ctx, logical_for("primary_then_alternate"))
    row = harness.entry(logical_for("primary_then_alternate"))["dispatches"][0]
    assert row["status"] == "interrupted"
    assert row["failure"] == {
        "category": "cancellation",
        "status_code": None,
        "retry_after_seconds": None,
        "outcome": "abort",
    }
    # The interrupted call keeps its full conservative bound: no refund.
    assert row["account_charge_microusd"] is None
    assert rel.accounted_microusd(harness.state) == BOUND


@pytest.mark.asyncio
async def test_unknown_exceptions_never_classify_as_a_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(
        harness,
        monkeypatch,
        lambda count, params: RuntimeError("quota exhausted sk-secret 429 rate limited"),
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "failed"
    assert entry["dispatches"][0]["failure"] == {
        "category": compute_runtime.FAILURE_UNSPECIFIED,
        "status_code": None,
        "retry_after_seconds": None,
        "outcome": "stop",
    }
    # The raw message is never stored, and a 429 inside it is not a classifier.
    serialised = json.dumps(harness.state)
    assert "quota exhausted" not in serialised
    assert "sk-secret" not in serialised


# ---------------------------------------------------------------------------
# Cooldowns, pacing, deadlines
# ---------------------------------------------------------------------------


def test_cooldowns_are_validated_capped_at_the_period_end_and_never_shortened() -> None:
    state = rel.empty_state({})
    ends_at = rel.period_end(PERIOD)
    assert ends_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert rel.validate_retry_delay(30) == (30.0, "provider")
    assert rel.validate_retry_delay(0) == (0.0, "provider")
    for malformed in (None, True, "30", -1, float("nan"), float("inf")):
        delay, source = rel.validate_retry_delay(malformed)
        assert delay is None
        assert source in {"absent", "malformed"}

    # Malformed guidance must not produce an immediate retry: a rate-limited
    # failure still opens the conservative default cooldown.
    record = rel.record_cooldown(
        state, "route-primary", status_code=429, retry_after="later", now=NOW, ends_at=ends_at
    )
    assert record is not None
    assert record["source"] == "default:malformed"
    assert record["until"] == rel.iso(NOW + timedelta(seconds=rel.DEFAULT_COOLDOWN_S))
    extended = rel.record_cooldown(
        state, "route-primary", status_code=429, retry_after=600.0, now=NOW, ends_at=ends_at
    )
    assert extended is not None
    assert extended["until"] == rel.iso(NOW + timedelta(seconds=600))
    # A shorter hint never shortens a promise already made.
    assert (
        rel.record_cooldown(
            state, "route-primary", status_code=429, retry_after=1.0, now=NOW, ends_at=ends_at
        )
        is None
    )
    thirty_days_s = 30 * 24 * 3600.0
    capped = rel.record_cooldown(
        state,
        "route-alternate",
        status_code=429,
        retry_after=thirty_days_s,
        now=NOW,
        ends_at=ends_at,
    )
    assert capped is not None
    assert capped["until"] == rel.iso(ends_at)
    assert capped["capped_at_period_end"] is True
    # Past the funded period end nothing is dispatchable at all.
    assert rel.active_cooldown(state, "route-alternate", ends_at) is None
    assert (
        rel.record_cooldown(
            state, "route-third", status_code=503, retry_after=None, now=NOW, ends_at=ends_at
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_cooling_primary_is_an_avoided_dispatch_not_a_recovered_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(harness, monkeypatch, lambda count, params: case_answer("U01")(params))
    cool(harness.state, "route-primary")
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "avoided_dispatch"
    assert entry["avoided_dispatch"] is True
    assert entry["dispatch_count"] == 1
    assert [call["_route_id"] for call in calls] == ["route-alternate"]
    assert entry["skipped"] == [
        {
            "route_id": "route-primary",
            "role": "primary",
            "reason": "cooldown",
            "until": rel.iso(NOW + timedelta(seconds=60)),
        }
    ]
    summary = rel._arm_summary([entry])
    assert summary["recovered_failures"] == 0
    assert summary["avoided_dispatches"] == 1
    assert summary["dispatches"] == 1


@pytest.mark.asyncio
async def test_a_cooling_sole_endpoint_skips_and_a_cooling_backup_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(
        harness, monkeypatch, lambda count, params: pytest.fail("a cooling route was dispatched")
    )
    cool(harness.state, "route-primary")
    logical = logical_for("primary_only")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "skipped_cooldown"
    assert entry["dispatches"] == []
    assert rel._arm_summary([entry])["skipped_cooldown"] == 1

    # A blocked backup is reported as backup_cooldown, not as an unexplained
    # error: the primary is genuinely attempted and genuinely failed first.
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(
        harness,
        monkeypatch,
        lambda count, params: unavailable(
            compute_runtime.FAILURE_UPSTREAM_UNAVAILABLE, status_code=503, retryable=True
        ),
    )
    cool(harness.state, "route-alternate")
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    entry = harness.entry(logical)
    assert entry["outcome"] == "backup_cooldown"
    assert entry["avoided_dispatch"] is False
    assert [row["route_id"] for row in entry["dispatches"]] == ["route-primary"]
    assert [skip["route_id"] for skip in entry["skipped"]] == ["route-alternate"]


@pytest.mark.asyncio
async def test_a_cooldown_does_not_stop_later_distinct_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)

    def handler(count: int, params: dict[str, Any]) -> Any:
        if params["_route_id"] == "route-primary":
            return unavailable(
                compute_runtime.FAILURE_RATE_LIMITED,
                status_code=429,
                retry_after=600.0,
                retryable=True,
            )
        return answer(params)

    install_runtime(harness, monkeypatch, handler)
    for case_id in ("U01", "U03"):
        await rel.run_logical(harness.ctx, logical_for("primary_only", case_id=case_id))
    first = harness.entry(logical_for("primary_only", case_id="U01"))
    second = harness.entry(logical_for("primary_only", case_id="U03"))
    assert first["outcome"] == "failed"
    assert second["outcome"] == "skipped_cooldown"
    assert second["skipped"][0]["route_id"] == "route-primary"


@pytest.mark.asyncio
async def test_pacing_spaces_dispatch_starts_and_consumes_the_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    harness.state["pacing"]["last_dispatch_started_at"] = rel.iso(NOW)
    await rel.run_logical(harness.ctx, logical_for("primary_only"))
    assert harness.waits == [rel.MIN_PACING_S]
    assert len(calls) == 1

    # A wait that cannot fit inside the arm's deadline stops the case instead of
    # sending an unpaced or late dispatch.
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(
        harness, monkeypatch, lambda count, params: pytest.fail("a late dispatch was sent")
    )
    harness.state["pacing"]["last_dispatch_started_at"] = rel.iso(NOW - timedelta(seconds=30))
    monkeypatch.setattr(rel, "MIN_PACING_S", rel.SINGLE_ARM_DEADLINE_S * 2)
    logical = logical_for("primary_only")
    await rel.run_logical(harness.ctx, logical)
    assert harness.entry(logical)["outcome"] == "deadline_exceeded"
    assert calls == [] and harness.entry(logical)["dispatches"] == []


# ---------------------------------------------------------------------------
# Budgets, dispatch bounds and crash/restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sub_cap_counts_failures_and_unknown_charges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, ceiling=BOUND * 2
    )
    # The provider fails and no ledger movement is attributable: the full
    # reservation bound stays charged to the experiment. A 503 with no retry
    # guidance opens no cooldown, so the next scheduled case still dispatches.
    install_runtime(
        harness,
        monkeypatch,
        lambda count, params: unavailable(
            compute_runtime.FAILURE_UPSTREAM_UNAVAILABLE, status_code=503, retryable=True
        ),
        settles=False,
    )
    await rel.run_smoke(harness.ctx, rel.Smoke("smoke-primary", "primary"))
    smoke = harness.state["smokes"]["smoke-primary"]
    assert smoke["outcome"] == "failed"
    assert smoke["dispatches"][0]["account_charge_microusd"] is None
    assert rel.accounted_microusd(harness.state) == BOUND

    await rel.run_logical(harness.ctx, logical_for("primary_only", case_id="U03"))
    assert rel.accounted_microusd(harness.state) == 2 * BOUND

    # The shared account allowance is the other, independent bound: a nearly
    # spent account stops a dispatch without recording a charge.
    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, ceiling=2 * BOUND
    )
    ledger.spent = 2 * BOUND
    install_runtime(
        harness, monkeypatch, lambda count, params: pytest.fail("the allowance was overrun")
    )
    logical = logical_for("primary_only", case_id="U05")
    await rel.run_logical(harness.ctx, logical)
    assert harness.entry(logical)["outcome"] == "capacity_stop"
    assert harness.entry(logical)["dispatches"] == []


@pytest.mark.asyncio
async def test_the_incremental_ceiling_stops_a_dispatch_that_would_overrun_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, ceiling=10**9
    )
    # Both possible redundancy dispatches are checked before the first is sent.
    harness.state["logicals"]["spent"] = {
        "dispatches": [
            {
                "reservation_bound_microusd": rel.INCREMENTAL_CAP_MICROUSD,
                "account_charge_microusd": None,
            }
        ]
    }
    calls = install_runtime(
        harness, monkeypatch, lambda count, params: pytest.fail("the sub-cap was overrun")
    )
    logical = logical_for("primary_then_alternate")
    await rel.run_logical(harness.ctx, logical)
    assert harness.entry(logical)["outcome"] == "capacity_stop"
    assert calls == []
    assert rel.accounted_microusd(harness.state) == rel.INCREMENTAL_CAP_MICROUSD


@pytest.mark.asyncio
async def test_case_and_global_dispatch_bounds_are_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, ceiling=10**9
    )

    # An eligible primary failure is what earns the arm's second dispatch, and
    # two is the hard ceiling for the case budget of three.
    def handler(count: int, params: dict[str, Any]) -> Any:
        if count == 1:
            return unavailable(
                compute_runtime.FAILURE_UPSTREAM_UNAVAILABLE, status_code=503, retryable=True
            )
        return case_answer("U01")(params)

    install_runtime(harness, monkeypatch, handler)
    harness.state["smokes"]["smoke-primary"] = {
        "smoke_id": "smoke-primary",
        "role": "primary",
        "dispatches": [{} for _ in range(rel.TOTAL_DISPATCH_BOUND)],
    }
    with pytest.raises(rel.ReliabilityError, match="global dispatch bound"):
        await rel.run_logical(harness.ctx, logical_for("primary_only"))
    harness.state["smokes"].clear()

    await rel.run_logical(harness.ctx, logical_for("primary_then_alternate"))
    assert len(harness.entry(logical_for("primary_then_alternate"))["dispatches"]) == 2
    assert harness.entry(logical_for("primary_then_alternate"))["dispatch_count"] == 2
    assert rel.CASE_MAX_DISPATCHES == pilot.MAX_CALLS_PER_ATTEMPT == 3
    for case_id in rel.UTILITY_CASE_IDS:
        case = fixtures.by_id()[case_id]
        assert case.max_calls >= rel.MAX_DISPATCHES_PER_LOGICAL["primary_then_alternate"]
        assert not case.tools  # this experiment sends no tools


@pytest.mark.asyncio
async def test_a_crashed_dispatch_is_never_replayed_and_keeps_its_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)

    async def crash(**params: Any) -> Any:
        raise KeyboardInterrupt("operator stopped the run")

    install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    monkeypatch.setattr(rel.compute_runtime, "guarded_completion", crash)
    with pytest.raises(KeyboardInterrupt):
        await rel.run_logical(harness.ctx, logical_for("primary_only"))
    persisted = json.loads(harness.ctx.state_path.read_text(encoding="utf-8"))
    interrupted = persisted["logicals"]["rel-U01-primary_only-r1"]["dispatches"][0]
    assert interrupted["status"] == "in_progress"
    assert interrupted["account_charge_microusd"] is None
    assert rel.accounted_microusd(persisted) == BOUND

    # Restart: the recorded logical ID is skipped, including an in-progress one,
    # and the interrupted call's bound still counts against the ceiling.
    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, state=persisted
    )
    calls = install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    logical = logical_for("primary_only")
    assert logical.logical_id in harness.state["logicals"]
    for pending in rel.build_logicals():
        if pending.logical_id in harness.state["logicals"]:
            continue
        await rel.run_logical(harness.ctx, pending)
    # Only the 23 un-recorded logicals ran; the interrupted one was never re-sent
    # and its durable bound is still charged.
    assert len(calls) == 23
    # The interrupted logical stays in progress with no outcome: it is recorded
    # as uncertain work, never re-sent and never resolved by a later resume.
    assert len(harness.entry(logical)["dispatches"]) == 1
    assert harness.entry(logical)["outcome"] is None
    assert harness.entry(logical)["dispatches"][0]["status"] == "in_progress"
    assert rel.accounted_microusd(harness.state) == BOUND + 23 * (BOUND // 2)


@pytest.mark.asyncio
async def test_resume_skips_recorded_smokes_and_logicals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    await rel.run_smoke(harness.ctx, rel.Smoke("smoke-primary", "primary"))
    await rel.run_logical(harness.ctx, logical_for("primary_only"))
    persisted = json.loads(harness.ctx.state_path.read_text(encoding="utf-8"))

    harness = make_ctx(
        tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures, state=persisted
    )
    install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    for smoke in rel.build_smokes():
        if smoke.smoke_id in harness.state["smokes"]:
            continue
        await rel.run_smoke(harness.ctx, smoke)
    assert len(harness.state["smokes"]["smoke-primary"]["dispatches"]) == 1
    assert len(harness.state["smokes"]["smoke-alternate"]["dispatches"]) == 1
    assert rel.smokes_allow_run(harness.state) == (True, "")


# ---------------------------------------------------------------------------
# Smokes, ledger evidence and the results artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smokes_are_one_bounded_dispatch_each_and_gate_the_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    calls = install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    assert rel.smokes_allow_run(harness.state) == (False, "smoke for primary has not run")
    for smoke in rel.build_smokes():
        await rel.run_smoke(harness.ctx, smoke)
    assert rel.smokes_allow_run(harness.state) == (True, "")
    assert len(calls) == 2
    for smoke_id, route_id in (
        ("smoke-primary", "route-primary"),
        ("smoke-alternate", "route-alternate"),
    ):
        smoke = harness.state["smokes"][smoke_id]
        assert smoke["outcome"] == "completed"
        assert len(smoke["dispatches"]) == 1
        assert 0 < smoke["dispatches"][0]["dispatch_timeout_s"] <= rel.SMOKE_DEADLINE_S == 45.0
        assert smoke["dispatches"][0]["runtime_route_id"] == route_id
        assert smoke["dispatches"][0]["route_id"] == route_id
    assert rel.model_routing.active_routing() is None
    # Smokes are part of the experiment's global bound.
    assert len(rel.dispatch_rows(harness.state)) == 2


def test_a_failed_or_interrupted_smoke_blocks_the_live_arms() -> None:
    state = rel.empty_state({})
    state["smokes"]["smoke-primary"] = {"outcome": "failed"}
    state["smokes"]["smoke-alternate"] = {"outcome": "completed"}
    allowed, reason = rel.smokes_allow_run(state)
    assert not allowed and "pilot blocked" in reason
    state["smokes"]["smoke-primary"] = {"outcome": None, "status": "in_progress"}
    allowed, reason = rel.smokes_allow_run(state)
    assert not allowed and "pilot blocked" in reason
    state["smokes"]["smoke-primary"] = {"outcome": "quality:empty_answer"}
    allowed, reason = rel.smokes_allow_run(state)
    assert not allowed and "pilot blocked" in reason


@pytest.mark.asyncio
async def test_ledger_post_delta_is_evidence_while_durable_state_stays_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    ledger: Ledger,
    fixtures: pilot.FixtureSet,
) -> None:
    # A pre-existing account balance is outside this experiment and must not be
    # attributed to it in either direction.
    ledger.spent = 12_000
    harness = make_ctx(tmp_path, monkeypatch, routes=routes, ledger=ledger, fixtures=fixtures)
    harness.state["account_ledger_baseline"] = {
        "exposure_microusd": 12_000,
        "open_holds": 0,
        "recorded_at": rel.iso(NOW),
        "period_key": PERIOD,
    }
    install_runtime(harness, monkeypatch, lambda count, params: answer(params))
    await rel.run_logical(harness.ctx, logical_for("primary_only"))
    row = harness.entry(logical_for("primary_only"))["dispatches"][0]
    assert row["ledger_exposure_before_microusd"] == 12_000
    assert row["ledger_delta_microusd"] == BOUND // 2
    assert row["account_charge_microusd"] == BOUND // 2
    assert rel.baseline_exposure(harness.state) == 12_000
    assert rel.ledger_delta_microusd(harness.state, ledger.exposure()) == BOUND // 2
    # Unrelated movement larger than this dispatch's own bound is never trusted.
    ledger.spent += BOUND * 10
    assert rel.accounted_microusd(harness.state) == BOUND // 2


def test_baseline_must_exist_before_accounting() -> None:
    state = rel.empty_state({})
    with pytest.raises(rel.ReliabilityError, match="baseline missing"):
        rel.baseline_exposure(state)
    state["account_ledger_baseline"] = {"exposure_microusd": 5}
    assert rel.ledger_delta_microusd(state, rel.LedgerExposure(9, 1, 0)) == 5
    state["account_ledger_baseline"] = {"exposure_microusd": "5"}
    with pytest.raises(rel.ReliabilityError, match="baseline exposure invalid"):
        rel.baseline_exposure(state)


def test_results_artifact_is_separate_and_keeps_every_verdict_pending(
    tmp_path: Path,
) -> None:
    state = rel.empty_state({"plan_sha256": "p", "account": "a", "period_key": PERIOD})
    state["account_ledger_baseline"] = {"exposure_microusd": 100, "open_holds": 0}
    state["cooldowns"] = {"route-primary": {"until": rel.iso(NOW)}}
    state["smokes"]["smoke-primary"] = {
        "smoke_id": "smoke-primary",
        "role": "primary",
        "outcome": "completed",
        "dispatches": [
            {
                "account_charge_microusd": 100,
                "provider_cost_usd": 0.01,
                "status": "completed",
            }
        ],
    }
    state["logicals"]["rel-U01-primary_then_alternate-r1"] = {
        "logical_id": "rel-U01-primary_then_alternate-r1",
        "arm": "primary_then_alternate",
        "case_id": "U01",
        "repeat": 1,
        "outcome": "recovered",
        "schema_valid": True,
        "avoided_dispatch": False,
        "latency_seconds": 4.0,
        "skipped": [],
        "dispatches": [
            {
                "status": "failed",
                "account_charge_microusd": None,
                "reservation_bound_microusd": 900,
            },
            {"status": "completed", "account_charge_microusd": 100, "provider_cost_usd": 0.01},
        ],
    }
    results = rel.build_results(state, rel.LedgerExposure(300, 0, 0))
    assert results["artifact_version"] == rel.RESULTS_ARTIFACT_VERSION
    assert results["artifact_version"] != pilot.RESULTS_ARTIFACT_VERSION
    assert results["scorer_compatible"] is False
    assert results["logicals"][0]["semantic_verdict"] == "pending"
    arm = results["arms"]["primary_then_alternate"]
    assert arm["recovered_failures"] == 1
    assert arm["first_dispatch_failures"] == 1
    assert arm["dispatches"] == 2
    # 100 settled smoke + the 900 unknown bound kept in full + 100 settled backup
    assert results["accounting"]["accounted_microusd"] == 1_100
    assert results["accounting"]["unknown_charge_dispatches"] == 1
    assert results["accounting"]["ledger_delta_microusd"] == 200
    assert results["bounds"]["dispatches_recorded"] == 3
    assert results["cooldowns"] == {"route-primary": rel.iso(NOW)}
    assert "not the offline scorer import format" in results["notes"]

    path = tmp_path / "results.json"
    rel.write_results(path, results)
    assert json.loads(path.read_text(encoding="utf-8"))["artifact_version"] == (
        rel.RESULTS_ARTIFACT_VERSION
    )
    with pytest.raises(rel.ReliabilityError, match="already exists"):
        rel.write_results(path, results)


# ---------------------------------------------------------------------------
# Preflight and CLI
# ---------------------------------------------------------------------------


def test_worst_case_bounds_cover_every_declared_dispatch(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any], fixtures: pilot.FixtureSet
) -> None:
    install_pricing(monkeypatch, routes)
    logicals = rel.build_logicals()
    resolved = SimpleNamespace(recurring_budget_microusd=25_000_000)
    bounds = rel.check_worst_case(
        routes, fixtures.by_id(), resolved, logicals, rel.LedgerExposure(0, 0, 0)
    )
    assert len(bounds["logicals"]) == 24
    assert len(bounds["smokes"]) == 2
    # Two smokes, eight single-dispatch arm cases each way, and eight
    # two-dispatch redundancy cases: 34 route-specific bounds in total.
    assert sum(len(item) for item in bounds["logicals"].values()) + 2 == rel.TOTAL_DISPATCH_BOUND
    assert bounds["total_microusd"] == 34 * BOUND
    assert bounds["total_microusd"] <= rel.INCREMENTAL_CAP_MICROUSD
    assert rel.account_ceiling(resolved) == rel.AGGREGATE_CAP_MICROUSD
    assert rel.account_ceiling(SimpleNamespace(recurring_budget_microusd=34 * BOUND)) == 34 * BOUND

    # The same 34-dispatch proof against a nearly full account must refuse.
    with pytest.raises(rel.ReliabilityError, match="account allowance"):
        rel.check_worst_case(
            routes,
            fixtures.by_id(),
            resolved,
            logicals,
            rel.LedgerExposure(25_000_000 - 34 * BOUND + 1, 0, 0),
        )


def test_a_worst_case_that_cannot_fit_the_incremental_ceiling_is_refused(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any], fixtures: pilot.FixtureSet
) -> None:
    # Every one of the 34 bounds is charged against the USD 1 experiment ceiling.
    install_pricing(monkeypatch, routes, rel.INCREMENTAL_CAP_MICROUSD // 8)
    with pytest.raises(rel.ReliabilityError, match="incremental ceiling"):
        rel.check_worst_case(
            routes,
            fixtures.by_id(),
            SimpleNamespace(recurring_budget_microusd=25_000_000),
            rel.build_logicals(),
            rel.LedgerExposure(0, 0, 0),
        )


def test_period_gate_is_the_original_funded_period() -> None:
    rel.validate_period(rel.ORIGINAL_FUNDED_PERIOD)
    for invalid in ("2026-10", "20269", "nope", "2026-1"):
        with pytest.raises(rel.ReliabilityError):
            rel.validate_period(invalid)
    assert rel.ORIGINAL_FUNDED_PERIOD == "2026-09"
    assert rel.AGGREGATE_CAP_MICROUSD == 25_000_000
    assert rel.INCREMENTAL_CAP_MICROUSD == 1_000_000


def test_cli_requires_the_explicit_evaluation_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["model_endpoint_reliability.py"])
    with pytest.raises(SystemExit) as exit_info:
        rel.main()
    assert exit_info.value.code == 2
    required = (
        "--account",
        "--period",
        "--state",
        "--results",
        "--primary-route",
        "--alternate-route",
        "--phase",
    )
    printed = capsys.readouterr().err
    for flag in required:
        assert flag in printed
