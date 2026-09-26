"""Loaders for the two configuration files this layer owns.

``config/commercial.json``          plans, capabilities, limits, trial, trusted sources
``config/inference_policy.json``   which provider/model routes and tool services are approved

Both loaders validate strictly and raise
:class:`~orchestrator.entitlements.errors.PolicyError` on any problem, so a
malformed or half-specified policy fails closed at import time instead of
granting capabilities or approving an unverified route at request time.

Neither loader reads ``orchestrator.config.Settings.DEFAULT_TIER`` or any other
global: paid state is per account or it is not.

Inference policy model
----------------------
A route is usable only when **every** requirement passes. The requirements are
deliberately redundant, because each one is a way a route could leak data or
overspend:

* provider-side transport flags, pinned per request: ``zdr: true`` and
  ``data_collection: "deny"``, ``allow_fallbacks: false``,
  ``require_parameters: true``, and ``provider: {only: [slug], order: [slug]}``
  so the request cannot be silently served by a different provider;
* account-side operator verification, because provider ZDR alone is not
  sufficient: prompt/completion logging must be disabled on the account and
  free models need a separate training opt-out;
* a pinned https endpoint and a pinned maximum price, so spend is bounded
  without fetching a live price at request time;
* a dated approval **and** a dated operator review with recorded evidence.

Tool services (web search, embeddings, speech, ...) carry their own price
policy and are **deny by default**: a service that is absent from the file, or
present but unqualified, is not usable. This stops an unqualified service from
becoming a bypass around the route rules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from orchestrator.entitlements.errors import PolicyError, RouteNotApproved
from orchestrator.entitlements.money import MICRO_USD_PER_USD, Microusd, require_microusd
from orchestrator.entitlements.plans import (
    Capability,
    Plan,
    PlanDefinition,
    UsageLimits,
    immutable_map,
    parse_capabilities,
    parse_plan,
)

#: repository root: orchestrator/entitlements/policy.py -> parents[2]
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

DEFAULT_COMMERCIAL_CONFIG: Final[Path] = REPO_ROOT / "config" / "commercial.json"
DEFAULT_INFERENCE_POLICY: Final[Path] = REPO_ROOT / "config" / "inference_policy.json"

COMMERCIAL_CONFIG_ENV: Final[str] = "DAEMON_COMMERCIAL_CONFIG"
INFERENCE_POLICY_ENV: Final[str] = "DAEMON_INFERENCE_POLICY"

SUPPORTED_COMMERCIAL_VERSION: Final[int] = 1
SUPPORTED_INFERENCE_VERSION: Final[int] = 1

#: Retired tier names that may appear in ``legacy_plan_map``.
LEGACY_TIER_NAMES: Final[frozenset[str]] = frozenset(
    {"free", "starter", "pro", "max", "byok", "power"}
)

#: Mappings the wholesale replacement requires, verified at load time.
REQUIRED_LEGACY_PLAN_MAP: Final[Mapping[str, str]] = {
    "starter": "pro",
    "max": "power",
    "byok": "pro",
}

#: The only accepted value for the transport data-collection flag.
REQUIRED_DATA_COLLECTION: Final[str] = "deny"

#: Availability value that means "someone actually checked".
VERIFIED_AVAILABILITY: Final[str] = "verified"

_LIMIT_FIELDS: Final[tuple[str, ...]] = (
    "max_concurrent_operations",
    "max_context_tokens",
    "max_output_tokens",
    "max_tool_loop_iterations",
    "requests_per_minute",
    "extended_agents_per_period",
    "extended_agent_budget_microusd",
    "monthly_budget_microusd",
)


# --------------------------------------------------------------------------- #
# commercial.json
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DisplayPrice:
    """A configured, displayed price. Never used for internal accounting."""

    label: str
    currency: str
    amount_minor: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "currency": self.currency,
            "amount_minor": self.amount_minor,
        }


@dataclass(frozen=True, slots=True)
class TrialLimits:
    """The premium limit overlay the trial grants.

    Only the context and output ceilings are overlaid: concurrency, rate and
    extended-agent quotas are account-level properties of the plan, and the
    trial's own extended allowance is a lifetime count held on the account row
    rather than a per-period quota.
    """

    max_context_tokens: int
    max_output_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True, slots=True)
class TrialPolicy:
    """The finite lifetime trial entitlement.

    The trial is an entitlement in its own right, not a decoration on the free
    plan. It carries:

    * a money budget and an extended-agent count, both lifetime and independent
      of any plan's per-period quota;
    * its own capabilities, unioned by the resolver only while the allowance
      lasts, so callers never subtract anything by hand;
    * its own premium limit overlay;
    * no calendar expiry, because a trial that lapses on a schedule is not an
      independent lifetime-usage allowance.

    ``terminate_on_paid_plan`` defaults to ``False``: buying a plan does not
    consume the trial. The unspent remainder is preserved across an upgrade and
    a later downgrade, and because the counters only move on settled usage it
    can never be refilled.
    """

    enabled: bool
    granted_on_account_creation: bool
    budget_microusd: Microusd
    extended_agents: int
    capabilities: frozenset[Capability]
    limits: TrialLimits
    terminate_on_paid_plan: bool

    def budget_for_new_account(self) -> Microusd:
        if not self.enabled or not self.granted_on_account_creation:
            return 0
        return self.budget_microusd

    def extended_agents_for_new_account(self) -> int:
        if not self.enabled or not self.granted_on_account_creation:
            return 0
        return self.extended_agents

    def is_granted_to_new_account(self) -> bool:
        """Whether a brand new account receives the trial entitlement at all."""
        return self.enabled and self.granted_on_account_creation


@dataclass(frozen=True, slots=True)
class CommercialPolicy:
    """Validated ``config/commercial.json``."""

    version: int
    internal_currency: str
    internal_unit: str
    display_currency: str
    period: str
    operations: frozenset[str]
    trusted_subscription_sources: frozenset[str]
    trial: TrialPolicy
    legacy_plan_map: Mapping[str, Plan]
    plans: Mapping[Plan, PlanDefinition]
    source_path: str

    def plan(self, plan: Plan | str) -> PlanDefinition:
        """Look up a plan definition, accepting a name or enum member."""
        parsed = plan if isinstance(plan, Plan) else parse_plan(plan)
        definition = self.plans.get(parsed)
        if definition is None:
            raise PolicyError(f"plan is not configured: {parsed.value}")
        return definition

    def legacy_target(self, legacy_tier: str) -> Plan:
        """Map a retired tier name to its replacement plan.

        Only for explicit per-user imports. There is no code path from a
        global default to this function.
        """
        key = legacy_tier.strip().lower()
        target = self.legacy_plan_map.get(key)
        if target is None:
            known = ", ".join(sorted(self.legacy_plan_map))
            raise PolicyError(f"unknown legacy tier {legacy_tier!r}; known: {known}")
        return target

    def is_trusted_source(self, source: str) -> bool:
        return source.strip().lower() in self.trusted_subscription_sources

    def public_catalog(self) -> list[dict[str, Any]]:
        """Plan catalog for display. Contains displayed prices only."""
        return [
            self.plans[plan].public_display()
            for plan in sorted(self.plans, key=lambda item: item.value)
        ]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path} must contain a JSON object")
    return data


def _require_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{field} must be an object")
    return value


def _require_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _require_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{field} must be a boolean")
    return value


def _optional_bool(value: object, *, field: str) -> bool | None:
    if value is None:
        return None
    return _require_bool(value, field=field)


def _require_microusd(value: object, *, field: str) -> Microusd:
    try:
        return require_microusd(value, field=field)
    except ValueError as exc:
        raise PolicyError(str(exc)) from exc


def _parse_limits(raw: object, *, plan: Plan) -> UsageLimits:
    mapping = _require_mapping(raw, field=f"plans.{plan.value}.limits")
    unknown = sorted(set(mapping) - set(_LIMIT_FIELDS))
    if unknown:
        raise PolicyError(
            f"plans.{plan.value}.limits has unknown fields: {', '.join(unknown)}; "
            f"known: {', '.join(_LIMIT_FIELDS)}"
        )
    missing = [name for name in _LIMIT_FIELDS if name not in mapping]
    if missing:
        raise PolicyError(f"plans.{plan.value}.limits is missing: {', '.join(missing)}")

    values: dict[str, int] = {}
    for name in _LIMIT_FIELDS:
        value = mapping[name]
        if name in ("extended_agent_budget_microusd", "monthly_budget_microusd"):
            values[name] = _require_microusd(value, field=f"plans.{plan.value}.limits.{name}")
        else:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PolicyError(
                    f"plans.{plan.value}.limits.{name} must be a non-negative integer"
                )
            values[name] = value
    return UsageLimits(**values)


def _parse_display(raw: object, *, plan: Plan, display_currency: str) -> DisplayPrice:
    mapping = _require_mapping(raw, field=f"plans.{plan.value}.display")
    currency = _require_str(
        mapping.get("currency", display_currency), field=f"plans.{plan.value}.display.currency"
    )
    if currency != display_currency:
        raise PolicyError(
            f"plans.{plan.value}.display.currency must be {display_currency!r}, got {currency!r}"
        )
    amount_minor = mapping.get("amount_minor")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor < 0:
        raise PolicyError(f"plans.{plan.value}.display.amount_minor must be a non-negative integer")
    return DisplayPrice(
        label=_require_str(
            mapping.get("label", plan.value), field=f"plans.{plan.value}.display.label"
        ),
        currency=currency,
        amount_minor=amount_minor,
    )


def _parse_trial(raw: object) -> TrialPolicy:
    mapping = _require_mapping(raw, field="trial")
    expiry = mapping.get("calendar_expiry_days", None)
    if expiry is not None:
        raise PolicyError(
            "trial.calendar_expiry_days must be null: the trial is a finite lifetime "
            "allowance driven by usage and never expires on a calendar"
        )
    enabled = _require_bool(mapping.get("enabled"), field="trial.enabled")
    granted = _require_bool(
        mapping.get("granted_on_account_creation"),
        field="trial.granted_on_account_creation",
    )
    budget = _require_microusd(mapping.get("budget_microusd"), field="trial.budget_microusd")
    if enabled and budget <= 0:
        raise PolicyError(
            "trial.budget_microusd must be greater than zero when the trial is enabled"
        )
    extended_agents = mapping.get("extended_agents", 0)
    if (
        not isinstance(extended_agents, int)
        or isinstance(extended_agents, bool)
        or (extended_agents < 0)
    ):
        raise PolicyError("trial.extended_agents must be a non-negative integer")
    capabilities = parse_capabilities(mapping.get("capabilities", []), field="trial.capabilities")
    limits_map = _require_mapping(mapping.get("limits", {}), field="trial.limits")
    trial_limits: dict[str, int] = {}
    for name in ("max_context_tokens", "max_output_tokens"):
        value = limits_map.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PolicyError(f"trial.limits.{name} must be a non-negative integer")
        trial_limits[name] = value
    if trial_limits["max_output_tokens"] > trial_limits["max_context_tokens"]:
        raise PolicyError(
            "trial.limits.max_output_tokens must not exceed trial.limits.max_context_tokens"
        )
    terminate_on_paid_plan = _require_bool(
        mapping.get("terminate_on_paid_plan"), field="trial.terminate_on_paid_plan"
    )
    if terminate_on_paid_plan:
        raise PolicyError(
            "trial.terminate_on_paid_plan must be false: paid plans preserve unused trial allowance"
        )
    return TrialPolicy(
        enabled=enabled,
        granted_on_account_creation=granted,
        budget_microusd=budget,
        extended_agents=extended_agents,
        capabilities=capabilities,
        limits=TrialLimits(**trial_limits),
        terminate_on_paid_plan=terminate_on_paid_plan,
    )


def _parse_legacy_plan_map(raw: object) -> Mapping[str, Plan]:
    mapping = _require_mapping(raw, field="legacy_plan_map")
    entries = {key: value for key, value in mapping.items() if key != "notes"}
    if not entries:
        raise PolicyError("legacy_plan_map must not be empty")
    unknown = sorted(set(entries) - LEGACY_TIER_NAMES)
    if unknown:
        raise PolicyError(
            f"legacy_plan_map has unknown legacy tier(s): {', '.join(unknown)}; "
            f"known: {', '.join(sorted(LEGACY_TIER_NAMES))}"
        )
    resolved: dict[str, Plan] = {}
    for key, value in entries.items():
        resolved[key.strip().lower()] = parse_plan(value)
    for legacy, expected in REQUIRED_LEGACY_PLAN_MAP.items():
        actual = resolved.get(legacy)
        if actual is None:
            raise PolicyError(f"legacy_plan_map must map {legacy!r}")
        if actual.value != expected:
            raise PolicyError(
                f"legacy_plan_map must map {legacy!r} -> {expected!r}, got {actual.value!r}"
            )
    return MappingProxyType(resolved)


def _parse_string_set(raw: object, *, field: str) -> frozenset[str]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise PolicyError(f"{field} must be a non-empty list of strings")
    return frozenset(_require_str(item, field=field) for item in raw)


def parse_commercial_policy(data: object, *, source_path: str = "<memory>") -> CommercialPolicy:
    """Validate a commercial policy mapping. Exposed for tests and overrides."""
    mapping = _require_mapping(data, field="commercial policy")

    version = mapping.get("version")
    if version != SUPPORTED_COMMERCIAL_VERSION:
        raise PolicyError(
            f"commercial policy version must be {SUPPORTED_COMMERCIAL_VERSION}, got {version!r}"
        )

    internal_currency = _require_str(mapping.get("internal_currency"), field="internal_currency")
    if internal_currency != "USD":
        raise PolicyError(
            f"internal_currency must be USD (internal budgets are microusd), "
            f"got {internal_currency!r}"
        )
    internal_unit = _require_str(mapping.get("internal_unit"), field="internal_unit")
    if internal_unit != "microusd":
        raise PolicyError(f"internal_unit must be microusd, got {internal_unit!r}")

    display_currency = _require_str(mapping.get("display_currency"), field="display_currency")
    period = _require_str(mapping.get("period"), field="period")
    if period != "calendar_month_utc":
        raise PolicyError(
            f"period must be calendar_month_utc (period keys are UTC months), got {period!r}"
        )

    operations = _parse_string_set(mapping.get("operations"), field="operations")
    trusted_sources = _parse_string_set(
        mapping.get("trusted_subscription_sources"), field="trusted_subscription_sources"
    )
    forbidden_sources = {"default_tier", "env", "public", "unauthenticated", "anonymous"}
    leaked = sorted(trusted_sources & forbidden_sources)
    if leaked:
        raise PolicyError(
            f"trusted_subscription_sources must not include {', '.join(leaked)}: a plan change "
            f"must come from a trusted per-account event, never from a global default"
        )

    trial = _parse_trial(mapping.get("trial"))
    legacy_plan_map = _parse_legacy_plan_map(mapping.get("legacy_plan_map"))

    raw_plans = _require_mapping(mapping.get("plans"), field="plans")
    plan_names = {parse_plan(key) for key in raw_plans}
    missing_plans = {item.value for item in Plan} - plan_names
    if missing_plans:
        raise PolicyError(f"plans is missing required plan(s): {', '.join(sorted(missing_plans))}")
    unknown_plans = sorted(set(raw_plans) - {item.value for item in Plan})
    if unknown_plans:
        raise PolicyError(f"plans contains unknown plan(s): {', '.join(unknown_plans)}")

    funded_free_floor: int | None = None

    plans: dict[Plan, PlanDefinition] = {}
    for key, raw_definition in raw_plans.items():
        plan = parse_plan(key)
        definition = _require_mapping(raw_definition, field=f"plans.{plan.value}")
        display = _parse_display(
            definition.get("display"), plan=plan, display_currency=display_currency
        )
        capabilities = parse_capabilities(
            definition.get("capabilities"), field=f"plans.{plan.value}.capabilities"
        )
        limits = _parse_limits(definition.get("limits"), plan=plan)
        limits.validate(plan=plan, capabilities=capabilities)
        if plan is Plan.FREE:
            funded_free_floor = limits.monthly_budget_microusd
        plans[plan] = PlanDefinition(
            plan=plan,
            display_label=display.label,
            display_currency=display.currency,
            display_amount_minor=display.amount_minor,
            capabilities=capabilities,
            limits=limits,
        )

    # A capability with no allowance behind it would be a grant that silently
    # fails at request time, so the two have to be reconciled here.
    for plan, definition in plans.items():
        if Capability.EXTENDED_AGENTS not in definition.capabilities:
            continue
        if definition.limits.extended_agents_per_period <= 0 and trial.extended_agents <= 0:
            raise PolicyError(
                f"{plan.value}: extended_agents is granted but nothing funds it; set "
                f"limits.extended_agents_per_period or trial.extended_agents"
            )
    for plan, definition in plans.items():
        if plan is Plan.FREE or funded_free_floor is None:
            continue
        if definition.limits.monthly_budget_microusd <= funded_free_floor:
            raise PolicyError(
                f"{plan.value}.monthly_budget_microusd must exceed the free funded floor "
                f"({funded_free_floor}) so a paid plan is always a strict upgrade"
            )

    # The free plan's durable core must not duplicate its temporary trial
    # overlay. Paid plans grant the same capabilities from their own budget.
    plan_granted: set[Capability] = set(plans[Plan.FREE].capabilities)
    duplicated = sorted(capability.value for capability in (plan_granted & set(trial.capabilities)))
    if duplicated:
        raise PolicyError(
            f"trial.capabilities must not duplicate a plan capability: {', '.join(duplicated)}; "
            f"the trial overlay and the plan definitions are two different sources of the "
            f"same grant and only one of them may own it"
        )
    if Capability.EXTENDED_AGENTS in trial.capabilities and trial.extended_agents <= 0:
        raise PolicyError(
            "trial grants extended_agents but trial.extended_agents is 0: a grant must be "
            "funded by its own allowance"
        )

    return CommercialPolicy(
        version=version,
        internal_currency=internal_currency,
        internal_unit=internal_unit,
        display_currency=display_currency,
        period=period,
        operations=operations,
        trusted_subscription_sources=trusted_sources,
        trial=trial,
        legacy_plan_map=dict(legacy_plan_map),
        plans=immutable_map(plans),
        source_path=source_path,
    )


def load_policy(path: str | Path | None = None) -> CommercialPolicy:
    """Load and validate the commercial policy. Cached per resolved path.

    The path may be overridden with ``DAEMON_COMMERCIAL_CONFIG`` so a
    deployment can ship its own commercial defaults without a code change.
    """
    return _load_policy_cached(
        str(_resolve_path(path, COMMERCIAL_CONFIG_ENV, DEFAULT_COMMERCIAL_CONFIG))
    )


@lru_cache(maxsize=8)
def _load_policy_cached(resolved: str) -> CommercialPolicy:
    return parse_commercial_policy(_read_json(Path(resolved)), source_path=resolved)


# --------------------------------------------------------------------------- #
# inference_policy.json
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PolicyRequirements:
    """Which conditions must hold before a route or tool service may be used.

    Every flag defaults to ``True``: a requirement has to be turned off
    deliberately, so adding a new check cannot accidentally make the policy
    more permissive.
    """

    require_approval: bool = True
    require_unexpired_approval: bool = True
    require_operator_review: bool = True
    require_unexpired_operator_review: bool = True
    require_verified_availability: bool = True
    require_zdr: bool = True
    require_no_training: bool = True
    require_account_logging_disabled: bool = True
    require_free_model_training_opt_out: bool = True
    require_price_ceiling: bool = True
    require_pinned_endpoint: bool = True
    require_pinned_transport: bool = True
    require_pinned_provider_selection: bool = True

    def as_dict(self) -> dict[str, bool]:
        return {
            "require_approval": self.require_approval,
            "require_unexpired_approval": self.require_unexpired_approval,
            "require_operator_review": self.require_operator_review,
            "require_unexpired_operator_review": self.require_unexpired_operator_review,
            "require_verified_availability": self.require_verified_availability,
            "require_zdr": self.require_zdr,
            "require_no_training": self.require_no_training,
            "require_account_logging_disabled": self.require_account_logging_disabled,
            "require_free_model_training_opt_out": self.require_free_model_training_opt_out,
            "require_price_ceiling": self.require_price_ceiling,
            "require_pinned_endpoint": self.require_pinned_endpoint,
            "require_pinned_transport": self.require_pinned_transport,
            "require_pinned_provider_selection": self.require_pinned_provider_selection,
        }


@dataclass(frozen=True, slots=True)
class TransportPrivacy:
    """Provider-side privacy flags, pinned per request.

    ``allow_fallbacks`` must be ``False`` and ``require_parameters`` ``True``
    so a request cannot be served by a provider that ignores the privacy
    parameters. ``provider.only``/``provider.order`` must both contain exactly
    the approved provider slug, so there is no fallback provider to leak to.
    """

    provider_only: tuple[str, ...] | None
    provider_order: tuple[str, ...] | None
    allow_fallbacks: bool | None
    require_parameters: bool | None
    data_collection: str | None
    zdr: bool | None

    def rejection_reasons(self, requirements: PolicyRequirements) -> tuple[str, ...]:
        """Transport-level reasons this route is not usable, in a stable order."""
        reasons: list[str] = []
        if not self.zdr:
            reasons.append("transport_zdr_not_asserted")
        if self.data_collection != REQUIRED_DATA_COLLECTION:
            reasons.append("transport_data_collection_not_denied")
        if self.allow_fallbacks is not False:
            reasons.append("transport_allow_fallbacks_not_false")
        if self.require_parameters is not True:
            reasons.append("transport_require_parameters_not_true")
        if not self.provider_only or not self.provider_order:
            reasons.append("transport_provider_not_pinned")
        elif len(self.provider_only) != 1 or self.provider_only != self.provider_order:
            reasons.append("transport_provider_not_pinned")
        return tuple(reasons)

    def as_transport_kwargs(self) -> dict[str, Any]:
        """The exact per-request flags the inference transport must send."""
        if (
            not self.provider_only
            or not self.provider_order
            or self.rejection_reasons(PolicyRequirements())
        ):
            raise PolicyError("transport privacy flags or provider selection are not pinned")
        return {
            "extra_body": {
                "provider": {
                    "only": list(self.provider_only),
                    "order": list(self.provider_order),
                    "allow_fallbacks": self.allow_fallbacks,
                    "require_parameters": self.require_parameters,
                    "data_collection": self.data_collection,
                    "zdr": self.zdr,
                }
            }
        }


@dataclass(frozen=True, slots=True)
class OperatorReview:
    """Named, dated, expiring human sign-off with recorded evidence.

    Provider-side ZDR is necessary but not sufficient: prompt/completion
    logging lives in the account, and free models need a separate training
    opt-out. Both are recorded as evidence here so the assertion is auditable
    and expires on its own if nobody renews it.
    """

    reviewer: str | None
    reviewed_at: datetime | None
    review_expires_at: datetime | None
    evidence: tuple[str, ...]

    def rejection_reasons(
        self,
        requirements: PolicyRequirements,
        *,
        now: datetime,
        require: bool = True,
    ) -> tuple[str, ...]:
        if not require:
            return ()
        reasons: list[str] = []
        if not self.reviewer or not self.evidence:
            reasons.append("operator_review_missing_evidence")
        if self.review_expires_at is None:
            reasons.append("operator_review_expiry_missing")
        elif self.review_expires_at <= now:
            reasons.append("operator_review_expired")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class PriceCeiling:
    """Operator-pinned maximum unit prices, in microusd per 1M tokens.

    These mirror the provider's ``max_price: {prompt, completion}`` transport
    cap. Prices are pinned maxima, never fetched live: the reservation ledger
    holds spend against these ceilings, so a live price lookup at request time
    would be both redundant and unauditable.
    """

    microusd_per_1m_prompt: Microusd
    microusd_per_1m_completion: Microusd

    def estimate_microusd(self, prompt_tokens: int, completion_tokens: int) -> Microusd:
        """Conservative integer token cost under this route's pinned ceiling."""
        for name, value in (
            ("prompt_tokens", prompt_tokens),
            ("completion_tokens", completion_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PolicyError(f"{name} must be a non-negative integer")
        total = (
            prompt_tokens * self.microusd_per_1m_prompt
            + completion_tokens * self.microusd_per_1m_completion
        )
        return (total + 999_999) // 1_000_000

    def as_max_price(self) -> dict[str, float]:
        """USD-per-1M-token values for the transport ``max_price`` parameter."""
        return {
            "prompt": self.microusd_per_1m_prompt / MICRO_USD_PER_USD,
            "completion": self.microusd_per_1m_completion / MICRO_USD_PER_USD,
        }


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    """One candidate provider/model route plus every approval fact about it."""

    route_id: str
    provider: str
    model: str
    endpoint: str
    route_class: str
    approved: bool
    availability: str
    approval_expires_at: datetime | None
    transport: TransportPrivacy
    account_prompt_logging_disabled: bool | None
    free_model_training_opt_out: bool | None
    review: OperatorReview
    price_ceiling: PriceCeiling | None
    notes: str = ""
    model_capabilities: frozenset[str] = frozenset()
    max_context_tokens: int = 0
    max_output_tokens: int = 0

    def supports(
        self,
        *,
        required_capabilities: frozenset[str],
        input_tokens: int,
        output_tokens: int,
    ) -> bool:
        """Check verified model support before considering its execution price."""
        return (
            required_capabilities <= self.model_capabilities
            and input_tokens >= 0
            and 0 < output_tokens <= self.max_output_tokens
            and input_tokens + output_tokens <= self.max_context_tokens
        )

    def estimate_microusd(self, prompt_tokens: int, completion_tokens: int) -> Microusd:
        """Price an admitted token bound; unpriced routes fail closed."""
        if self.price_ceiling is None:
            raise RouteNotApproved(self.route_id, ("price_ceiling_missing",))
        return self.price_ceiling.estimate_microusd(prompt_tokens, completion_tokens)

    def rejection_reasons(
        self, requirements: PolicyRequirements, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        """Why this route is not usable, in a stable order. Empty means approved.

        Every condition is reported even when ``requirements`` disables it, so
        a caller can still see what is unverified about a route.
        """
        moment = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        if (
            "text" not in self.model_capabilities
            or self.max_context_tokens <= 0
            or not 0 < self.max_output_tokens <= self.max_context_tokens
        ):
            reasons.append("model_capabilities_unverified")
        if requirements.require_approval and not self.approved:
            reasons.append("not_approved")
        if requirements.require_verified_availability and (
            self.availability != VERIFIED_AVAILABILITY
        ):
            reasons.append(f"availability_{self.availability}")
        if requirements.require_pinned_endpoint and not _is_pinned_endpoint(self.endpoint):
            reasons.append("endpoint_not_pinned")
        if requirements.require_zdr and not self.transport.zdr:
            reasons.append("transport_zdr_not_asserted")
        if requirements.require_no_training and (
            self.transport.data_collection != REQUIRED_DATA_COLLECTION
        ):
            reasons.append("transport_training_not_denied")
        if requirements.require_pinned_transport and (
            self.transport.allow_fallbacks is not False
            or self.transport.require_parameters is not True
        ):
            reasons.append("transport_flags_not_pinned")
        if requirements.require_pinned_provider_selection and not _provider_pinned(self.transport):
            reasons.append("transport_provider_not_pinned")
        if requirements.require_account_logging_disabled and (
            self.account_prompt_logging_disabled is not True
        ):
            reasons.append("account_logging_not_disabled")
        if requirements.require_free_model_training_opt_out and (
            self.free_model_training_opt_out is not True
        ):
            reasons.append("free_model_training_opt_out_missing")
        if requirements.require_price_ceiling and self.price_ceiling is None:
            reasons.append("price_ceiling_missing")
        if requirements.require_unexpired_approval:
            if self.approval_expires_at is None:
                reasons.append("approval_expiry_missing")
            elif self.approval_expires_at <= moment:
                reasons.append("approval_expired")
        reasons.extend(
            self.review.rejection_reasons(
                requirements,
                now=moment,
                require=requirements.require_operator_review,
            )
        )
        return tuple(reasons)

    def is_approved(self, requirements: PolicyRequirements, *, now: datetime | None = None) -> bool:
        return not self.rejection_reasons(requirements, now=now)

    def transport_payload(
        self,
        requirements: PolicyRequirements,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The per-request payload an inference transport must send.

        Raises :class:`RouteNotApproved` when the route fails any requirement,
        so a caller cannot accidentally send an unqualified request.
        """
        reasons = self.rejection_reasons(requirements, now=now)
        if reasons:
            raise RouteNotApproved(self.route_id, reasons)
        assert self.price_ceiling is not None  # guaranteed by rejection_reasons
        payload = self.transport.as_transport_kwargs()
        payload["extra_body"]["provider"]["max_price"] = self.price_ceiling.as_max_price()
        return payload


@dataclass(frozen=True, slots=True)
class ToolServicePolicy:
    """A non-LLM billable service (search, embeddings, speech, media).

    Tool services carry their own price ceiling and operator review, and are
    deny-by-default: a service that is absent from the policy is not usable,
    so an unconfigured service cannot become a bypass around the route rules.
    """

    service_id: str
    service: str
    provider: str
    unit: str
    approved: bool
    availability: str
    ceiling_microusd_per_unit: Microusd | None
    review: OperatorReview
    notes: str = ""

    def rejection_reasons(
        self, requirements: PolicyRequirements, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        moment = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        if not self.approved:
            reasons.append("not_approved")
        if self.availability != VERIFIED_AVAILABILITY:
            reasons.append(f"availability_{self.availability}")
        if self.ceiling_microusd_per_unit is None:
            reasons.append("price_ceiling_missing")
        reasons.extend(
            self.review.rejection_reasons(
                requirements,
                now=moment,
                require=requirements.require_operator_review,
            )
        )
        return tuple(reasons)

    def is_approved(self, requirements: PolicyRequirements, *, now: datetime | None = None) -> bool:
        return not self.rejection_reasons(requirements, now=now)


@dataclass(frozen=True, slots=True)
class InferencePolicy:
    """Validated ``config/inference_policy.json``.

    The shipped file approves nothing, and that is the intended steady state
    until a route has a verified availability result, the full pinned
    transport privacy block, account-level logging disabled, a free-model
    training opt-out, a pinned endpoint, a price ceiling, and a dated approval
    plus a dated operator review.
    """

    version: int
    requirements: PolicyRequirements
    routes: Mapping[str, RoutePolicy]
    tool_services: Mapping[str, ToolServicePolicy]
    default_route_id: str | None
    source_path: str

    def route(self, route_id: str) -> RoutePolicy | None:
        return self.routes.get(route_id)

    def tool_service(self, service_id: str) -> ToolServicePolicy | None:
        return self.tool_services.get(service_id)

    def is_approved(self, route_id: str, *, now: datetime | None = None) -> bool:
        route = self.routes.get(route_id)
        if route is None:
            return False
        return route.is_approved(self.requirements, now=now)

    def is_tool_service_approved(self, service_id: str, *, now: datetime | None = None) -> bool:
        service = self.tool_services.get(service_id)
        if service is None:
            return False
        return service.is_approved(self.requirements, now=now)

    def approved_route_ids(self, *, now: datetime | None = None) -> frozenset[str]:
        return frozenset(
            route_id
            for route_id, route in self.routes.items()
            if route.is_approved(self.requirements, now=now)
        )

    def approved_tool_service_ids(self, *, now: datetime | None = None) -> frozenset[str]:
        return frozenset(
            service_id
            for service_id, service in self.tool_services.items()
            if service.is_approved(self.requirements, now=now)
        )

    def effective_default_route_id(self, *, now: datetime | None = None) -> str | None:
        """The default route, but only when it is actually approved."""
        if self.default_route_id is None:
            return None
        if self.default_route_id not in self.approved_route_ids(now=now):
            return None
        return self.default_route_id

    def transport_payload(self, route_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Pinned transport payload for ``route_id``; raises if not approved."""
        route = self.routes.get(route_id)
        if route is None:
            raise RouteNotApproved(route_id, ("unknown_route",))
        return route.transport_payload(self.requirements, now=now)


def _provider_pinned(transport: TransportPrivacy) -> bool:
    only = transport.provider_only
    order = transport.provider_order
    if not only or not order:
        return False
    return len(only) == 1 and tuple(only) == tuple(order)


def _is_pinned_endpoint(endpoint: str) -> bool:
    if not endpoint.startswith("https://"):
        return False
    host = endpoint[len("https://") :]
    if not host or any(token in endpoint for token in ("*", "$", "{", "}", " ")):
        return False
    return "/" in host or "." in host


def _parse_requirements(raw: object) -> PolicyRequirements:
    mapping = _require_mapping(raw, field="inference policy requirements")
    values: dict[str, bool] = {}
    for name, default in PolicyRequirements().as_dict().items():
        if name not in mapping:
            values[name] = default
            continue
        values[name] = _require_bool(mapping[name], field=f"requirements.{name}")
        if not values[name]:
            raise PolicyError(f"requirements.{name} cannot be disabled")
    return PolicyRequirements(**values)


def _parse_timestamp(raw: object, *, field: str) -> datetime | None:
    if raw is None:
        return None
    text = _require_str(raw, field=field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PolicyError(f"{field} must be an ISO-8601 timestamp, got {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_str_tuple(
    raw: object, *, field: str, allow_empty: bool = False
) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or (not raw and not allow_empty):
        expected = "a list of strings" if allow_empty else "a non-empty list of strings"
        raise PolicyError(f"{field} must be {expected}")
    return tuple(_require_str(item, field=field) for item in raw)


def _parse_transport(raw: object, *, route_id: str) -> TransportPrivacy:
    mapping = _require_mapping(raw, field=f"routes.{route_id}.transport")
    return TransportPrivacy(
        provider_only=_parse_str_tuple(
            mapping.get("provider_only"), field=f"routes.{route_id}.transport.provider_only"
        ),
        provider_order=_parse_str_tuple(
            mapping.get("provider_order"), field=f"routes.{route_id}.transport.provider_order"
        ),
        allow_fallbacks=_optional_bool(
            mapping.get("allow_fallbacks"), field=f"routes.{route_id}.transport.allow_fallbacks"
        ),
        require_parameters=_optional_bool(
            mapping.get("require_parameters"),
            field=f"routes.{route_id}.transport.require_parameters",
        ),
        data_collection=(
            None
            if mapping.get("data_collection") is None
            else _require_str(
                mapping.get("data_collection"), field=f"routes.{route_id}.transport.data_collection"
            )
        ),
        zdr=_optional_bool(mapping.get("zdr"), field=f"routes.{route_id}.transport.zdr"),
    )


def _parse_review(raw: object, *, field: str) -> OperatorReview:
    if raw is None:
        return OperatorReview(reviewer=None, reviewed_at=None, review_expires_at=None, evidence=())
    mapping = _require_mapping(raw, field=field)
    # An empty evidence list is a legitimate "not reviewed yet" state: it is
    # reported as a rejection reason, not as a malformed policy.
    evidence = _parse_str_tuple(
        mapping.get("evidence"), field=f"{field}.evidence", allow_empty=True
    )
    return OperatorReview(
        reviewer=(
            None
            if mapping.get("reviewer") is None
            else _require_str(mapping.get("reviewer"), field=f"{field}.reviewer")
        ),
        reviewed_at=_parse_timestamp(mapping.get("reviewed_at"), field=f"{field}.reviewed_at"),
        review_expires_at=_parse_timestamp(
            mapping.get("review_expires_at"), field=f"{field}.review_expires_at"
        ),
        evidence=evidence or (),
    )


def _parse_price_ceiling(raw: object, *, field: str) -> PriceCeiling | None:
    if raw is None:
        return None
    mapping = _require_mapping(raw, field=field)
    return PriceCeiling(
        microusd_per_1m_prompt=_require_microusd(
            mapping.get("microusd_per_1m_prompt"), field=f"{field}.microusd_per_1m_prompt"
        ),
        microusd_per_1m_completion=_require_microusd(
            mapping.get("microusd_per_1m_completion"), field=f"{field}.microusd_per_1m_completion"
        ),
    )


def _parse_route_class(raw: object, *, route_id: str) -> str:
    value = _require_str(raw, field=f"routes.{route_id}.route_class")
    if value not in {"routine", "premium"}:
        raise PolicyError(f"routes.{route_id}.route_class must be routine or premium")
    return value


def _parse_route(raw: object, *, index: int) -> RoutePolicy:
    route_map = _require_mapping(raw, field=f"routes[{index}]")
    route_id = _require_str(route_map.get("route_id"), field=f"routes[{index}].route_id")
    privacy = _require_mapping(route_map.get("privacy"), field=f"routes.{route_id}.privacy")
    raw_capabilities = route_map.get("model_capabilities", [])
    if not isinstance(raw_capabilities, (list, tuple)):
        raise PolicyError(f"routes.{route_id}.model_capabilities must be a list of strings")
    model_capabilities = frozenset(
        _require_str(value, field=f"routes.{route_id}.model_capabilities")
        for value in raw_capabilities
    )
    capacities: dict[str, int] = {}
    for key in ("max_context_tokens", "max_output_tokens"):
        value = route_map.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyError(f"routes.{route_id}.{key} must be a non-negative integer")
        capacities[key] = value
    return RoutePolicy(
        route_id=route_id,
        provider=_require_str(route_map.get("provider"), field=f"routes.{route_id}.provider"),
        model=_require_str(route_map.get("model"), field=f"routes.{route_id}.model"),
        endpoint=_require_str(route_map.get("endpoint"), field=f"routes.{route_id}.endpoint"),
        route_class=_parse_route_class(route_map.get("route_class"), route_id=route_id),
        model_capabilities=model_capabilities,
        max_context_tokens=capacities["max_context_tokens"],
        max_output_tokens=capacities["max_output_tokens"],
        approved=_require_bool(route_map.get("approved"), field=f"routes.{route_id}.approved"),
        availability=_require_str(
            route_map.get("availability", "unverified"),
            field=f"routes.{route_id}.availability",
        ),
        approval_expires_at=_parse_timestamp(
            route_map.get("approval_expires_at"), field=f"routes.{route_id}.approval_expires_at"
        ),
        transport=_parse_transport(privacy.get("transport"), route_id=route_id),
        account_prompt_logging_disabled=_optional_bool(
            privacy.get("account_prompt_logging_disabled"),
            field=f"routes.{route_id}.privacy.account_prompt_logging_disabled",
        ),
        free_model_training_opt_out=_optional_bool(
            privacy.get("free_model_training_opt_out"),
            field=f"routes.{route_id}.privacy.free_model_training_opt_out",
        ),
        review=_parse_review(
            route_map.get("operator_review"), field=f"routes.{route_id}.operator_review"
        ),
        price_ceiling=_parse_price_ceiling(
            route_map.get("price_ceiling"), field=f"routes.{route_id}.price_ceiling"
        ),
        notes=str(route_map.get("notes", "")),
    )


def _parse_tool_service(raw: object, *, index: int) -> ToolServicePolicy:
    service_map = _require_mapping(raw, field=f"tool_services[{index}]")
    service_id = _require_str(
        service_map.get("service_id"), field=f"tool_services[{index}].service_id"
    )
    return ToolServicePolicy(
        service_id=service_id,
        service=_require_str(
            service_map.get("service"), field=f"tool_services[{service_id}].service"
        ),
        provider=_require_str(
            service_map.get("provider"), field=f"tool_services[{service_id}].provider"
        ),
        unit=_require_str(service_map.get("unit"), field=f"tool_services[{service_id}].unit"),
        approved=_require_bool(
            service_map.get("approved"), field=f"tool_services[{service_id}].approved"
        ),
        availability=_require_str(
            service_map.get("availability", "unverified"),
            field=f"tool_services[{service_id}].availability",
        ),
        ceiling_microusd_per_unit=(
            None
            if service_map.get("ceiling_microusd_per_unit") is None
            else _require_microusd(
                service_map.get("ceiling_microusd_per_unit"),
                field=f"tool_services.{service_id}.ceiling_microusd_per_unit",
            )
        ),
        review=_parse_review(
            service_map.get("operator_review"), field=f"tool_services.{service_id}.operator_review"
        ),
        notes=str(service_map.get("notes", "")),
    )


def parse_inference_policy(data: object, *, source_path: str = "<memory>") -> InferencePolicy:
    """Validate an inference policy mapping. Exposed for tests and overrides."""
    mapping = _require_mapping(data, field="inference policy")
    version = mapping.get("version")
    if version != SUPPORTED_INFERENCE_VERSION:
        raise PolicyError(
            f"inference policy version must be {SUPPORTED_INFERENCE_VERSION}, got {version!r}"
        )
    requirements = _parse_requirements(mapping.get("requirements", {}))

    raw_routes = mapping.get("routes")
    if not isinstance(raw_routes, list):
        raise PolicyError("routes must be a list")
    routes: dict[str, RoutePolicy] = {}
    for index, raw_route in enumerate(raw_routes):
        route = _parse_route(raw_route, index=index)
        if route.route_id in routes:
            raise PolicyError(f"duplicate route_id: {route.route_id}")
        routes[route.route_id] = route

    raw_services = mapping.get("tool_services", [])
    if not isinstance(raw_services, list):
        raise PolicyError("tool_services must be a list")
    tool_services: dict[str, ToolServicePolicy] = {}
    for index, raw_service in enumerate(raw_services):
        service = _parse_tool_service(raw_service, index=index)
        if service.service_id in tool_services:
            raise PolicyError(f"duplicate tool service_id: {service.service_id}")
        tool_services[service.service_id] = service

    raw_default = mapping.get("default_route_id")
    default_route_id: str | None
    if raw_default is None:
        default_route_id = None
    else:
        default_route_id = _require_str(raw_default, field="default_route_id")
        if default_route_id not in routes:
            raise PolicyError(f"default_route_id {default_route_id!r} is not present in routes")

    return InferencePolicy(
        version=version,
        requirements=requirements,
        routes=MappingProxyType(routes),
        tool_services=MappingProxyType(tool_services),
        default_route_id=default_route_id,
        source_path=source_path,
    )


def load_inference_policy(path: str | Path | None = None) -> InferencePolicy:
    """Load and validate the inference route policy. Cached per resolved path."""
    resolved = _resolve_path(path, INFERENCE_POLICY_ENV, DEFAULT_INFERENCE_POLICY)
    return _load_inference_policy_cached(str(resolved))


@lru_cache(maxsize=8)
def _load_inference_policy_cached(resolved: str) -> InferencePolicy:
    return parse_inference_policy(_read_json(Path(resolved)), source_path=resolved)


def _resolve_path(path: str | Path | None, env_name: str, default: Path) -> Path:
    if path is not None:
        return Path(path)
    from orchestrator.config import get_settings

    settings = get_settings()
    configured_paths = {
        COMMERCIAL_CONFIG_ENV: settings.daemon_commercial_config,
        INFERENCE_POLICY_ENV: settings.daemon_inference_policy,
    }
    from_env = configured_paths[env_name]
    if from_env:
        return Path(from_env)
    return default


__all__ = [
    "COMMERCIAL_CONFIG_ENV",
    "DEFAULT_COMMERCIAL_CONFIG",
    "DEFAULT_INFERENCE_POLICY",
    "DisplayPrice",
    "INFERENCE_POLICY_ENV",
    "InferencePolicy",
    "OperatorReview",
    "PolicyRequirements",
    "PriceCeiling",
    "REPO_ROOT",
    "RoutePolicy",
    "ToolServicePolicy",
    "TransportPrivacy",
    "TrialPolicy",
    "load_inference_policy",
    "load_policy",
    "parse_commercial_policy",
    "parse_inference_policy",
]
