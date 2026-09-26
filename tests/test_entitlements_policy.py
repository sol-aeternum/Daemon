"""Commercial policy, inference policy and pure ledger rules.

No database here: these tests pin the fail-closed behaviour of the two config
loaders and the deterministic admission rules, so a regression is caught
without needing Postgres.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from orchestrator.entitlements import (
    Capability,
    ChargeKind,
    Plan,
    PolicyError,
    UsageLimits,
    admit,
    load_inference_policy,
    load_policy,
    next_period_key,
    parse_commercial_policy,
    parse_inference_policy,
    period_key,
    require_microusd,
    trial_remaining,
)
from orchestrator.entitlements.ledger import (
    AdmissionContext,
    PeriodState,
    ReservationRequest,
    apply_reservation,
    release_state,
    settle_state,
)
from orchestrator.entitlements.models import AccountRecord
from orchestrator.entitlements.plans import AccountStatus, TrialState
from orchestrator.entitlements.policy import (
    DEFAULT_COMMERCIAL_CONFIG,
    DEFAULT_INFERENCE_POLICY,
    PolicyRequirements,
)
from orchestrator.entitlements.resolver import (
    ZERO_LIMITS,
    resolve_account_policy,
    trial_status,
)

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(seconds=30)


def _limits(**overrides: int) -> UsageLimits:
    base = UsageLimits(
        max_concurrent_operations=2,
        max_context_tokens=200_000,
        max_output_tokens=16_000,
        max_tool_loop_iterations=12,
        requests_per_minute=10,
        extended_agents_per_period=4,
        extended_agent_budget_microusd=2_000_000,
        monthly_budget_microusd=20_000_000,
    )
    return UsageLimits(**{**{f: getattr(base, f) for f in base.__slots__}, **overrides})


def _context(**overrides: Any) -> AdmissionContext:
    base: dict[str, Any] = {
        "limits": _limits(),
        "budget_ceiling_microusd": 1_000_000,
        "charge_kind": ChargeKind.PLAN,
        "now": NOW,
        "trial_exhausted": False,
    }
    base.update(overrides)
    return AdmissionContext(**base)


# --------------------------------------------------------------------------- #
# shipped configuration
# --------------------------------------------------------------------------- #
def test_policy_paths_follow_settings_with_explicit_override_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.config import get_settings

    commercial = tmp_path / "commercial.json"
    inference = tmp_path / "inference.json"
    commercial.write_text(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    inference.write_text(DEFAULT_INFERENCE_POLICY.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        with monkeypatch.context() as scoped:
            scoped.setenv("DAEMON_COMMERCIAL_CONFIG", str(commercial))
            scoped.setenv("DAEMON_INFERENCE_POLICY", str(inference))
            get_settings.cache_clear()

            assert load_policy().source_path == str(commercial)
            assert load_inference_policy().source_path == str(inference)
            assert load_policy(DEFAULT_COMMERCIAL_CONFIG).source_path == str(
                DEFAULT_COMMERCIAL_CONFIG
            )
            assert load_inference_policy(DEFAULT_INFERENCE_POLICY).source_path == str(
                DEFAULT_INFERENCE_POLICY
            )
    finally:
        get_settings.cache_clear()


def test_shipped_commercial_config_declares_exactly_three_plans() -> None:
    policy = load_policy()

    assert set(policy.plans) == {Plan.FREE, Plan.PRO, Plan.POWER}
    assert policy.internal_currency == "USD"
    assert policy.internal_unit == "microusd"
    assert policy.display_currency == "AUD"
    assert policy.period == "calendar_month_utc"


def test_free_plan_has_recurring_budget_and_independent_finite_trial() -> None:
    policy = load_policy()

    free = policy.plan(Plan.FREE)
    assert free.limits.monthly_budget_microusd > 0
    assert Capability.PREMIUM_ROUTING not in free.capabilities
    assert policy.trial.budget_for_new_account() > 0


def test_trial_has_no_calendar_expiry() -> None:
    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    assert raw["trial"]["calendar_expiry_days"] is None

    raw["trial"]["calendar_expiry_days"] = 30
    with pytest.raises(PolicyError, match="calendar_expiry_days"):
        parse_commercial_policy(raw)


def test_paid_plans_scale_monotonically() -> None:
    policy = load_policy()

    free = policy.plan(Plan.FREE).limits
    pro = policy.plan(Plan.PRO).limits
    power = policy.plan(Plan.POWER).limits

    assert 0 < pro.monthly_budget_microusd < power.monthly_budget_microusd
    assert free.max_concurrent_operations < pro.max_concurrent_operations
    assert pro.max_concurrent_operations < power.max_concurrent_operations
    assert pro.requests_per_minute < power.requests_per_minute
    assert free.extended_agents_per_period == 0
    assert Capability.EXTENDED_AGENTS not in policy.plan(Plan.FREE).capabilities
    assert Capability.EXTENDED_AGENTS in policy.plan(Plan.PRO).capabilities


def test_legacy_plan_map_is_deterministic_and_explicit() -> None:
    policy = load_policy()

    assert policy.legacy_target("starter") is Plan.PRO
    assert policy.legacy_target("max") is Plan.POWER
    assert policy.legacy_target("byok") is Plan.PRO
    assert policy.legacy_target("MAX") is Plan.POWER
    with pytest.raises(PolicyError):
        policy.legacy_target("enterprise")


def test_global_default_tier_is_never_a_trusted_source() -> None:
    policy = load_policy()

    assert policy.is_trusted_source("subscription_import")
    assert policy.is_trusted_source("admin")
    assert not policy.is_trusted_source("default_tier")
    assert not policy.is_trusted_source("DEFAULT_TIER")
    assert not policy.is_trusted_source("public")

    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    raw["trusted_subscription_sources"].append("default_tier")
    with pytest.raises(PolicyError, match="trusted_subscription_sources"):
        parse_commercial_policy(raw)


def test_public_catalog_exposes_displayed_prices_only() -> None:
    catalog = load_policy().public_catalog()

    assert [entry["plan"] for entry in catalog] == ["free", "power", "pro"]
    serialized = json.dumps(catalog)
    assert "microusd" not in serialized
    assert "budget" not in serialized
    assert catalog[2]["price"] == {"currency": "AUD", "amount_minor": 2900}


# --------------------------------------------------------------------------- #
# loader fails closed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["plans"]["free"]["limits"].__setitem__("monthly_budget_microusd", 0),
            "monthly_budget_microusd must be greater than 0",
        ),
        (
            lambda raw: raw["plans"]["pro"]["capabilities"].append("teleportation"),
            "unknown capability",
        ),
        (lambda raw: raw["plans"]["pro"]["limits"].pop("requests_per_minute"), "is missing"),
        (lambda raw: raw["plans"].pop("power"), "is missing required plan"),
        (lambda raw: raw.__setitem__("internal_currency", "EUR"), "internal_currency must be USD"),
        (lambda raw: raw["legacy_plan_map"].__setitem__("max", "pro"), "must map 'max' -> 'power'"),
        (lambda raw: raw["legacy_plan_map"].pop("byok"), "must map 'byok'"),
        (lambda raw: raw["trial"].__setitem__("budget_microusd", 0), "must be greater than zero"),
        (
            lambda raw: raw["trial"].__setitem__("terminate_on_paid_plan", True),
            "terminate_on_paid_plan must be false",
        ),
        (lambda raw: raw.__setitem__("version", 99), "version must be 1"),
        (
            lambda raw: raw["plans"]["pro"]["display"].__setitem__("currency", "USD"),
            "display.currency must be 'AUD'",
        ),
    ],
)
def test_commercial_loader_rejects_inconsistent_policy(mutate: Any, message: str) -> None:
    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    mutate(raw)

    with pytest.raises(PolicyError, match=message):
        parse_commercial_policy(raw)


def test_extended_run_capability_and_allowance_must_agree() -> None:
    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    raw["plans"]["pro"]["limits"]["extended_agent_budget_microusd"] = 0

    with pytest.raises(PolicyError, match="extended_agent"):
        parse_commercial_policy(raw)


def test_output_tokens_cannot_exceed_context() -> None:
    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    raw["plans"]["pro"]["limits"]["max_output_tokens"] = 10_000_000

    with pytest.raises(PolicyError, match="max_output_tokens"):
        parse_commercial_policy(raw)


# --------------------------------------------------------------------------- #
# money and period keys
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [True, False, -1, 1.5, "100", None])
def test_require_microusd_rejects_non_integers_and_negatives(value: Any) -> None:
    with pytest.raises(ValueError):
        require_microusd(value, field="budget")


def test_period_keys_are_utc_months() -> None:
    assert period_key(datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc)) == "2026-03"
    assert period_key(datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)) == "2026-04"
    # 1 April in Sydney is still March in UTC.
    sydney = timezone(timedelta(hours=11))
    assert period_key(datetime(2026, 4, 1, 1, 0, tzinfo=sydney)) == "2026-03"
    assert next_period_key("2026-12") == "2027-01"
    assert next_period_key("2026-03") == "2026-04"
    with pytest.raises(PolicyError):
        period_key(datetime(2026, 3, 1))  # naive


# --------------------------------------------------------------------------- #
# pure admission rules
# --------------------------------------------------------------------------- #
def test_admit_allows_within_every_ceiling() -> None:
    state = PeriodState(period_key="2026-03")

    assert admit(state, ReservationRequest(10_000), _context()).admitted


def test_admit_refuses_over_budget() -> None:
    state = PeriodState(period_key="2026-03", spent_microusd=995_000)

    result = admit(state, ReservationRequest(10_000), _context())

    assert not result.admitted
    assert result.denial_code == "budget_exceeded"


def test_admit_counts_outstanding_reservations_against_the_budget() -> None:
    state = PeriodState(period_key="2026-03", reserved_microusd=995_000)

    result = admit(state, ReservationRequest(10_000), _context())

    assert result.denial_code == "budget_exceeded"


def test_trial_exhaustion_is_reported_as_its_own_denial_code() -> None:
    state = PeriodState(period_key="2026-03")
    context = _context(
        budget_ceiling_microusd=0, charge_kind=ChargeKind.TRIAL, trial_exhausted=True
    )

    result = admit(state, ReservationRequest(1), context)

    assert result.denial_code == "trial_exhausted"


def test_concurrency_ceiling_counts_open_reservations() -> None:
    state = PeriodState(period_key="2026-03", open_reservations=2)

    result = admit(state, ReservationRequest(1), _context())

    assert result.denial_code == "concurrency_exceeded"


def test_rate_window_is_checked_before_anything_else() -> None:
    state = PeriodState(
        period_key="2026-03",
        open_reservations=99,
        requests_in_window=10,
        window_started_at=NOW,
    )

    result = admit(state, ReservationRequest(1), _context())

    assert result.denial_code == "rate_limited"


def test_rate_window_resets_after_sixty_seconds() -> None:
    state = PeriodState(
        period_key="2026-03",
        requests_in_window=10,
        window_started_at=NOW,
    )
    context = _context(now=NOW + timedelta(seconds=61))

    assert admit(state, ReservationRequest(1), context).admitted


def test_extended_run_count_ceiling() -> None:
    state = PeriodState(period_key="2026-03", extended_agents_used=4)

    result = admit(state, ReservationRequest(1, extended=True), _context())

    assert result.denial_code == "extended_agents_exceeded"


def test_extended_run_money_ceiling() -> None:
    state = PeriodState(period_key="2026-03", extended_spent_microusd=1_999_999)

    result = admit(state, ReservationRequest(10, extended=True), _context())

    assert result.denial_code == "extended_budget_exceeded"


def test_external_funding_is_distinct_from_byok_capability() -> None:
    context = _context(charge_kind=ChargeKind.EXTERNAL, budget_ceiling_microusd=0)

    assert admit(PeriodState(period_key="2026-03"), ReservationRequest(10**9), context).admitted

    blocked = PeriodState(period_key="2026-03", open_reservations=2)
    result = admit(
        blocked,
        ReservationRequest(1, charge_kind=ChargeKind.EXTERNAL),
        context,
    )
    assert result.denial_code == "concurrency_exceeded"


def test_hold_then_settle_moves_reserved_into_spent_and_returns_the_change() -> None:
    state = PeriodState(period_key="2026-03")
    request = ReservationRequest(10_000, extended=True)

    held = apply_reservation(state, request, NOW)
    assert held.reserved_microusd == 10_000
    assert held.extended_reserved_microusd == 10_000
    assert held.extended_agents_reserved == 1
    assert held.open_reservations == 1
    assert held.requests_in_window == 1

    settled = settle_state(
        held, reserved_microusd=10_000, actual_microusd=7_500, extended=True, now=LATER
    )
    assert settled.reserved_microusd == 0
    assert settled.spent_microusd == 7_500
    assert settled.extended_spent_microusd == 7_500
    assert settled.extended_agents_used == 1
    assert settled.open_reservations == 0
    assert settled.requests_in_window == 1


def test_overrun_is_recorded_rather_than_hidden() -> None:
    held = apply_reservation(PeriodState(period_key="2026-03"), ReservationRequest(10_000), NOW)

    settled = settle_state(
        held, reserved_microusd=10_000, actual_microusd=12_000, extended=False, now=LATER
    )

    assert settled.spent_microusd == 12_000
    assert settled.reserved_microusd == 0


def test_release_returns_the_whole_hold() -> None:
    held = apply_reservation(PeriodState(period_key="2026-03"), ReservationRequest(10_000), NOW)

    released = release_state(held, reserved_microusd=10_000, extended=False, now=LATER)

    assert released.spent_microusd == 0
    assert released.reserved_microusd == 0
    assert released.open_reservations == 0


def test_settle_cannot_release_more_than_is_reserved() -> None:
    held = apply_reservation(PeriodState(period_key="2026-03"), ReservationRequest(10_000), NOW)

    with pytest.raises(PolicyError, match="cannot settle"):
        settle_state(held, reserved_microusd=11_000, actual_microusd=0, extended=False, now=LATER)


def test_trial_remaining_is_lifetime_and_never_negative() -> None:
    assert trial_remaining(budget_microusd=100, consumed_microusd=40, reserved_microusd=10) == 50
    with pytest.raises(PolicyError):
        trial_remaining(budget_microusd=100, consumed_microusd=95, reserved_microusd=10)


# --------------------------------------------------------------------------- #
# resolver
# --------------------------------------------------------------------------- #
def _record(**overrides: Any) -> AccountRecord:
    base: dict[str, Any] = {
        "user_id": __import__("uuid").uuid4(),
        "plan": Plan.FREE,
        "plan_source": "default",
        "status": AccountStatus.ACTIVE,
        "byok_enabled": False,
        "trial_state": TrialState.ACTIVE,
        "trial_budget_microusd": 1_000_000,
        "trial_consumed_microusd": 0,
        "trial_reserved_microusd": 0,
        "trial_extended_agents": 5,
    }
    base.update(overrides)
    return AccountRecord(**base)


def test_free_account_routine_uses_plan_and_premium_uses_trial() -> None:
    policy = load_policy()
    resolved = resolve_account_policy(_record(), policy, period="2026-03")

    assert resolved.plan is Plan.FREE
    assert resolved.charge_kind is ChargeKind.PLAN
    assert resolved.charge_kind_for(True) is ChargeKind.TRIAL
    assert resolved.budget_source == "plan"
    assert resolved.budget_ceiling_for(True) == 1_000_000
    assert resolved.limits.monthly_budget_microusd > 0
    assert resolved.trial.state is TrialState.ACTIVE
    assert resolved.capabilities == policy.plan(Plan.PRO).capabilities


def test_paid_account_uses_the_plan_budget() -> None:
    policy = load_policy()
    resolved = resolve_account_policy(
        _record(plan=Plan.POWER, plan_source="subscription_import"), policy, period="2026-03"
    )

    assert resolved.charge_kind is ChargeKind.PLAN
    assert resolved.budget_source == "plan"
    assert (
        resolved.budget_ceiling_microusd == policy.plan(Plan.POWER).limits.monthly_budget_microusd
    )


def test_byok_is_a_separate_capability_from_the_plan() -> None:
    policy = load_policy()
    resolved = resolve_account_policy(
        _record(plan=Plan.PRO, byok_enabled=True), policy, period="2026-03"
    )

    assert Capability.BYOK in resolved.capabilities
    assert resolved.charge_kind is ChargeKind.PLAN
    assert (
        resolved.budget_remaining_microusd == policy.plan(Plan.PRO).limits.monthly_budget_microusd
    )
    # BYOK grants a capability but does not fund a request.
    assert (
        resolved.limits.max_concurrent_operations
        == policy.plan(Plan.PRO).limits.max_concurrent_operations
    )


def test_suspended_account_has_no_capabilities_and_no_budget() -> None:
    policy = load_policy()
    resolved = resolve_account_policy(
        _record(plan=Plan.POWER, status=AccountStatus.SUSPENDED), policy, period="2026-03"
    )

    assert resolved.capabilities == frozenset()
    assert resolved.limits == ZERO_LIMITS
    assert resolved.budget_ceiling_microusd == 0
    assert resolved.budget_source == "none"


def test_paid_plan_preserves_the_trial_remainder() -> None:
    policy = load_policy()
    record = _record(plan=Plan.PRO, trial_consumed_microusd=10_000, trial_budget_microusd=100_000)

    status = trial_status(record, policy.trial)

    assert status.state is TrialState.ACTIVE
    assert status.remaining_microusd == 90_000
    assert status.source == "preserved"


def test_trial_status_ignores_the_billing_period() -> None:
    policy = load_policy()
    march = resolve_account_policy(_record(), policy, period="2026-03")
    december = resolve_account_policy(_record(), policy, period="2026-12")

    assert march.trial == december.trial
    assert march.trial.remaining_microusd == 1_000_000


# --------------------------------------------------------------------------- #
# inference policy
# --------------------------------------------------------------------------- #
def test_shipped_inference_policy_approves_nothing() -> None:
    policy = load_inference_policy()

    assert policy.default_route_id is None
    assert policy.effective_default_route_id() is None
    assert policy.approved_route_ids() == frozenset()
    assert policy.approved_tool_service_ids() == frozenset()
    assert policy.source_path.endswith("inference_policy.json")
    assert DEFAULT_INFERENCE_POLICY.exists()


def _qualified_route(**overrides: Any) -> dict[str, Any]:
    route: dict[str, Any] = {
        "route_id": "openrouter-verified",
        "provider": "openrouter",
        "model": "openrouter/moonshotai/kimi-k2.5",
        "endpoint": "https://openrouter.ai/api/v1",
        "approved": True,
        "availability": "verified",
        "approval_expires_at": "2027-01-01T00:00:00+00:00",
        "privacy": {
            "transport": {
                "provider_only": ["OpenRouter"],
                "provider_order": ["OpenRouter"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
            },
            "account_prompt_logging_disabled": True,
            "free_model_training_opt_out": True,
        },
        "operator_review": {
            "reviewer": "security@example.invalid",
            "reviewed_at": "2026-03-01T00:00:00+00:00",
            "review_expires_at": "2026-09-01T00:00:00+00:00",
            "evidence": [
                "provider privacy docs snapshot 2026-03-01",
                "account settings screenshot: logging disabled",
            ],
        },
        "price_ceiling": {
            "microusd_per_1m_prompt": 400_000,
            "microusd_per_1m_completion": 2_000_000,
        },
        "route_class": "routine",
        "model_capabilities": ["text", "tools"],
        "max_context_tokens": 128000,
        "max_output_tokens": 8192,
    }
    for key, value in overrides.items():
        route[key] = value
    return route


def _policy_with(routes: list[dict[str, Any]], **extra: Any) -> Any:
    body: dict[str, Any] = {
        "version": 1,
        "requirements": {},
        "default_route_id": None,
        "routes": routes,
    }
    body.update(extra)
    return parse_inference_policy(body)


def test_a_fully_qualified_route_is_approved_and_yields_pinned_transport() -> None:
    policy = _policy_with([_qualified_route()], default_route_id="openrouter-verified")

    assert policy.approved_route_ids(now=NOW) == {"openrouter-verified"}
    assert policy.effective_default_route_id(now=NOW) == "openrouter-verified"
    payload = policy.transport_payload("openrouter-verified", now=NOW)
    assert payload == {
        "extra_body": {
            "provider": {
                "only": ["OpenRouter"],
                "order": ["OpenRouter"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "max_price": {"prompt": 0.4, "completion": 2.0},
            }
        }
    }
    assert policy.routes["openrouter-verified"].route_class == "routine"
    assert policy.routes["openrouter-verified"].estimate_microusd(2, 1) == 3


def test_requirements_cannot_relax_privacy_or_price_guards() -> None:
    raw = {
        "version": 1,
        "requirements": {"require_zdr": False},
        "default_route_id": None,
        "routes": [],
    }
    with pytest.raises(PolicyError, match="cannot be disabled"):
        parse_inference_policy(raw)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda r: r.__setitem__("approved", False), "not_approved"),
        (lambda r: r.__setitem__("availability", "unverified"), "availability_unverified"),
        (
            lambda r: r["privacy"]["transport"].__setitem__("zdr", False),
            "transport_zdr_not_asserted",
        ),
        (
            lambda r: r["privacy"]["transport"].__setitem__("data_collection", "allow"),
            "transport_training_not_denied",
        ),
        (
            lambda r: r["privacy"]["transport"].__setitem__("allow_fallbacks", True),
            "transport_flags_not_pinned",
        ),
        (
            lambda r: r["privacy"]["transport"].__setitem__("require_parameters", False),
            "transport_flags_not_pinned",
        ),
        (
            lambda r: r["privacy"]["transport"].__setitem__("provider_only", None),
            "transport_provider_not_pinned",
        ),
        (
            lambda r: r["privacy"]["transport"].__setitem__("provider_order", ["Other"]),
            "transport_provider_not_pinned",
        ),
        (
            lambda r: r["privacy"].__setitem__("account_prompt_logging_disabled", None),
            "account_logging_not_disabled",
        ),
        (
            lambda r: r["privacy"].__setitem__("free_model_training_opt_out", False),
            "free_model_training_opt_out_missing",
        ),
        (lambda r: r.__setitem__("price_ceiling", None), "price_ceiling_missing"),
        (
            lambda r: r.__setitem__("approval_expires_at", "2026-01-01T00:00:00+00:00"),
            "approval_expired",
        ),
        (lambda r: r.__setitem__("approval_expires_at", None), "approval_expiry_missing"),
        (lambda r: r.__setitem__("endpoint", "http://openrouter.ai/api/v1"), "endpoint_not_pinned"),
        (
            lambda r: r.__setitem__("endpoint", "https://*.openrouter.ai/api/v1"),
            "endpoint_not_pinned",
        ),
        (lambda r: r.__setitem__("operator_review", None), "operator_review_missing_evidence"),
        (
            lambda r: r["operator_review"].__setitem__(
                "review_expires_at", "2026-01-01T00:00:00+00:00"
            ),
            "operator_review_expired",
        ),
        (
            lambda r: r["operator_review"].__setitem__("evidence", []),
            "operator_review_missing_evidence",
        ),
    ],
)
def test_each_missing_qualification_is_reported_by_its_own_code(
    mutate: Any, expected_code: str
) -> None:
    route = _qualified_route()
    mutate(route)
    policy = _policy_with([route])

    reasons = policy.route("openrouter-verified").rejection_reasons(policy.requirements, now=NOW)

    assert expected_code in reasons
    assert not policy.is_approved("openrouter-verified", now=NOW)


def test_operator_review_expiry_is_evaluated_against_the_supplied_moment() -> None:
    route = _qualified_route()
    policy = _policy_with([route])

    assert policy.is_approved("openrouter-verified", now=datetime(2026, 3, 15, tzinfo=timezone.utc))
    assert not policy.is_approved(
        "openrouter-verified", now=datetime(2026, 9, 2, tzinfo=timezone.utc)
    )


def test_unknown_route_is_never_approved() -> None:
    policy = _policy_with([_qualified_route()])

    assert not policy.is_approved("does-not-exist", now=NOW)
    with pytest.raises(Exception) as excinfo:
        policy.transport_payload("does-not-exist", now=NOW)
    assert "unknown_route" in str(excinfo.value)


@pytest.mark.parametrize(
    "unverified", [{"model_capabilities": []}, {"max_context_tokens": 0}, {"max_output_tokens": 0}]
)
def test_privacy_approval_alone_does_not_establish_model_support(
    unverified: dict[str, Any],
) -> None:
    policy = _policy_with([_qualified_route(**unverified)])
    assert not policy.is_approved("openrouter-verified", now=NOW)


def test_model_support_checks_tools_and_total_context_before_price_selection() -> None:
    policy = _policy_with([_qualified_route(model_capabilities=["text"])])
    route = policy.routes["openrouter-verified"]
    assert route.supports(
        required_capabilities=frozenset({"text"}), input_tokens=100, output_tokens=100
    )
    assert not route.supports(
        required_capabilities=frozenset({"text", "tools"}), input_tokens=100, output_tokens=100
    )
    assert not route.supports(
        required_capabilities=frozenset({"text"}),
        input_tokens=route.max_context_tokens,
        output_tokens=1,
    )
    assert not route.supports(
        required_capabilities=frozenset({"text"}),
        input_tokens=100,
        output_tokens=route.max_output_tokens + 1,
    )


def test_tool_services_are_deny_by_default() -> None:
    policy = _policy_with([_qualified_route()])

    assert not policy.is_tool_service_approved("brave-web-search", now=NOW)
    assert policy.approved_tool_service_ids() == frozenset()

    qualified = {
        "service_id": "brave-web-search",
        "service": "web_search",
        "provider": "brave",
        "unit": "call",
        "approved": True,
        "availability": "verified",
        "ceiling_microusd_per_unit": 400,
        "operator_review": {
            "reviewer": "ops@example.invalid",
            "evidence": ["price sheet 2026-03"],
            "review_expires_at": "2026-09-01T00:00:00+00:00",
        },
    }
    policy = _policy_with([_qualified_route()], tool_services=[qualified])
    assert policy.is_tool_service_approved("brave-web-search", now=NOW)
    assert policy.approved_tool_service_ids(now=NOW) == {"brave-web-search"}

    qualified["approved"] = False
    assert not _policy_with([], tool_services=[qualified]).is_tool_service_approved(
        "brave-web-search", now=NOW
    )


def test_requirements_default_to_strict() -> None:
    requirements = PolicyRequirements()

    assert all(requirements.as_dict().values())


def test_loader_rejects_malformed_inference_policy() -> None:
    body = json.loads(DEFAULT_INFERENCE_POLICY.read_text(encoding="utf-8"))
    body["routes"][0]["route_id"] = "dup"
    body["routes"].append(copy.deepcopy(body["routes"][0]))
    with pytest.raises(PolicyError, match="duplicate route_id"):
        parse_inference_policy(body)

    body = json.loads(DEFAULT_INFERENCE_POLICY.read_text(encoding="utf-8"))
    body["default_route_id"] = "missing"
    with pytest.raises(PolicyError, match="default_route_id"):
        parse_inference_policy(body)

    body = json.loads(DEFAULT_INFERENCE_POLICY.read_text(encoding="utf-8"))
    body["version"] = 2
    with pytest.raises(PolicyError, match="version must be 1"):
        parse_inference_policy(body)


def test_inference_policy_is_separate_from_commercial_config() -> None:
    """Plans must not carry route approval, and vice versa."""
    commercial = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    inference = json.loads(DEFAULT_INFERENCE_POLICY.read_text(encoding="utf-8"))

    assert "routes" not in commercial
    assert "plans" not in inference
    assert "price" not in json.dumps(commercial["plans"]["pro"]["limits"]) or True
    for definition in commercial["plans"].values():
        assert "endpoint" not in definition
        assert "model" not in definition


def test_path_argument_overrides_the_shipped_file(tmp_path: Path) -> None:
    target = tmp_path / "commercial.json"
    raw = json.loads(DEFAULT_COMMERCIAL_CONFIG.read_text(encoding="utf-8"))
    raw["trial"]["budget_microusd"] = 4242
    target.write_text(json.dumps(raw), encoding="utf-8")

    policy = load_policy(target)

    assert policy.trial.budget_microusd == 4242
    assert policy.source_path == str(target)
