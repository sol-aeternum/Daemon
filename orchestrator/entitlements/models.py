"""Public data types returned by the entitlements service.

These are frozen dataclasses, not ORM rows. A caller can hold a
:class:`ResolvedPolicy` for the duration of one operation without holding a
database connection, and a :class:`Reservation` can be handed to a worker
process to settle later.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from orchestrator.entitlements.errors import (
    CapabilityDenied,
    InvalidSubscriptionEvent,
    InvalidUsageMetadata,
)
from orchestrator.entitlements.money import Microusd
from orchestrator.entitlements.plans import (
    BUDGET_SOURCE_NONE,
    BUDGET_SOURCE_PLAN,
    BUDGET_SOURCE_TRIAL,
    AccountStatus,
    Capability,
    ChargeKind,
    Plan,
    ReservationStatus,
    TrialState,
    UsageLimits,
)

#: Usage metadata keys that may be recorded on a settlement. An allowlist keeps
#: arbitrary payloads (and anything that looks like user content) out of the
#: ledger, and keeps the JSON shape stable for audit queries. There is no
#: free-text key: a note is where prompts and transcripts leak in.
ALLOWED_USAGE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "tool_calls",
        "duration_ms",
        "provider",
        "model",
        "route_id",
        "estimated_cost",
        "recovered_after_timeout",
    }
)

#: Subscription event metadata keys. Deliberately narrow and short: these are
#: audit breadcrumbs, never a place to carry a payload.
ALLOWED_EVENT_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {"legacy_tier", "external_ref", "invoice_id", "reason"}
)

#: Longest accepted value for a string usage or metadata field.
MAX_LABEL_LENGTH: Final[int] = 120

_USAGE_SCALARS: Final[tuple[type, ...]] = (int, float, str, bool, type(None))


def coerce_user_id(user_id: uuid.UUID | str) -> uuid.UUID:
    """Accept a UUID or a UUID-shaped string; reject anything else."""
    if isinstance(user_id, uuid.UUID):
        return user_id
    if isinstance(user_id, str):
        try:
            return uuid.UUID(user_id.strip())
        except ValueError as exc:
            raise ValueError(f"invalid user id: {user_id!r}") from exc
    raise TypeError(f"user_id must be a UUID or str, got {type(user_id).__name__}")


def validate_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate settlement usage metadata against :data:`ALLOWED_USAGE_KEYS`."""
    if usage is None:
        return {}
    if not isinstance(usage, Mapping):
        raise InvalidUsageMetadata("usage metadata must be a mapping")
    unknown = sorted(set(usage) - ALLOWED_USAGE_KEYS)
    if unknown:
        raise InvalidUsageMetadata(
            f"usage metadata has unknown keys: {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(ALLOWED_USAGE_KEYS))}"
        )
    validated: dict[str, Any] = {}
    for key, value in usage.items():
        if not isinstance(value, _USAGE_SCALARS):
            raise InvalidUsageMetadata(f"usage metadata {key!r} must be a scalar")
        if isinstance(value, str) and len(value) > MAX_LABEL_LENGTH:
            raise InvalidUsageMetadata(
                f"usage metadata {key!r} exceeds {MAX_LABEL_LENGTH} characters"
            )
        validated[key] = value
    return validated


def validate_event_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate trusted-event metadata: allowlisted keys, short string values.

    Audit breadcrumbs only. An unknown key is refused rather than dropped, so a
    caller cannot believe it recorded something it did not.
    """
    if not metadata:
        return {}
    if not isinstance(metadata, Mapping):
        raise InvalidSubscriptionEvent("event metadata must be a mapping")
    unknown = sorted(set(metadata) - ALLOWED_EVENT_METADATA_KEYS)
    if unknown:
        raise InvalidSubscriptionEvent(
            f"event metadata has unknown keys: {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(ALLOWED_EVENT_METADATA_KEYS))}"
        )
    validated: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(value, str):
            raise InvalidSubscriptionEvent(f"event metadata {key!r} must be a string")
        if len(value) > MAX_LABEL_LENGTH:
            raise InvalidSubscriptionEvent(
                f"event metadata {key!r} exceeds {MAX_LABEL_LENGTH} characters"
            )
        validated[key] = value
    return validated


@dataclass(frozen=True, slots=True)
class TrialStatus:
    """Trial facts for one account.

    ``state`` is ``active`` or ``exhausted`` only. There is no ``expired``
    state and no expiry timestamp anywhere: a trial ends when its finite
    allowance is consumed, and paid state never resurrects it.

    The extended-agent count is a second, independent lifetime allowance. It is
    not the plan's per-period extended quota.
    """

    state: TrialState
    source: str
    budget_microusd: Microusd
    consumed_microusd: Microusd
    reserved_microusd: Microusd
    remaining_microusd: Microusd
    extended_agents: int
    extended_agents_used: int
    extended_agents_reserved: int
    extended_agents_remaining: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "source": self.source,
            "budget_microusd": self.budget_microusd,
            "consumed_microusd": self.consumed_microusd,
            "reserved_microusd": self.reserved_microusd,
            "remaining_microusd": self.remaining_microusd,
            "extended_agents": self.extended_agents,
            "extended_agents_used": self.extended_agents_used,
            "extended_agents_reserved": self.extended_agents_reserved,
            "extended_agents_remaining": self.extended_agents_remaining,
        }


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """The ``entitlement_accounts`` row, as a typed value."""

    user_id: uuid.UUID
    plan: Plan
    plan_source: str
    status: AccountStatus
    byok_enabled: bool
    trial_state: TrialState
    trial_budget_microusd: Microusd
    trial_consumed_microusd: Microusd
    trial_reserved_microusd: Microusd
    trial_extended_agents: int = 0
    trial_extended_agents_used: int = 0
    trial_extended_agents_reserved: int = 0
    plan_changed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    """What a user is currently allowed to do.

    Resolution is independent of any billing provider and of the legacy global
    tier default: it reads one account row and the commercial policy.

    Which allowance pays depends on the operation, so it is asked for with
    :meth:`charge_kind_for` rather than baked in. Routine compute draws the
    plan's recurring funded budget; only ``premium=True`` on a free account can
    draw the finite trial.
    """

    user_id: uuid.UUID
    plan: Plan
    plan_source: str
    status: AccountStatus
    byok_enabled: bool
    capabilities: frozenset[Capability]
    limits: UsageLimits
    trial_limits: UsageLimits
    recurring_budget_microusd: Microusd
    trial: TrialStatus
    trial_funds_premium: bool
    period_key: str
    period_spent_microusd: int
    period_reserved_microusd: int

    def charge_kind_for(self, premium: bool) -> ChargeKind:
        """Which allowance funds an operation.

        Never :attr:`ChargeKind.EXTERNAL`: there is no credential-funding
        adapter, and ``byok_enabled`` is a capability, not a funding source.
        """
        if self.status is not AccountStatus.ACTIVE:
            return ChargeKind.PLAN
        if premium and self.trial_funds_premium:
            return ChargeKind.TRIAL
        return ChargeKind.PLAN

    def limits_for(self, premium: bool) -> UsageLimits:
        """Limits that apply to one operation.

        Premium work on a trial-funded account runs under the trial's overlay,
        which can only raise the plan's ceilings.
        """
        return self.trial_limits if (premium and self.trial_funds_premium) else self.limits

    def budget_ceiling_for(self, premium: bool) -> Microusd:
        """Hard ceiling for one operation, in microusd."""
        if self.charge_kind_for(premium) is ChargeKind.TRIAL:
            return self.trial.remaining_microusd
        if self.status is not AccountStatus.ACTIVE:
            return 0
        return self.recurring_budget_microusd

    def budget_source_for(self, premium: bool) -> str:
        if self.status is not AccountStatus.ACTIVE:
            return BUDGET_SOURCE_NONE
        if self.charge_kind_for(premium) is ChargeKind.TRIAL:
            return BUDGET_SOURCE_TRIAL
        return BUDGET_SOURCE_PLAN

    def remaining_for(self, premium: bool) -> Microusd:
        """What is left of the ceiling right now, after open reservations."""
        if self.charge_kind_for(premium) is ChargeKind.TRIAL:
            return self.trial.remaining_microusd
        if self.status is not AccountStatus.ACTIVE:
            return 0
        return max(
            0,
            self.recurring_budget_microusd
            - self.period_spent_microusd
            - self.period_reserved_microusd,
        )

    @property
    def charge_kind(self) -> ChargeKind:
        """Charge kind of a routine (non-premium) operation."""
        return self.charge_kind_for(False)

    @property
    def budget_ceiling_microusd(self) -> Microusd:
        return self.budget_ceiling_for(False)

    @property
    def budget_source(self) -> str:
        return self.budget_source_for(False)

    @property
    def budget_remaining_microusd(self) -> Microusd:
        return self.remaining_for(False)

    def has(self, capability: Capability | str) -> bool:
        wanted = capability if isinstance(capability, Capability) else Capability(capability)
        return wanted in self.capabilities

    def require(self, capability: Capability | str) -> None:
        """Raise :class:`CapabilityDenied` unless ``capability`` is granted."""
        wanted = capability if isinstance(capability, Capability) else Capability(capability)
        if wanted not in self.capabilities:
            raise CapabilityDenied(wanted.value)

    def limits_dict(self) -> dict[str, Any]:
        """Limits as JSON, including the effective budget facts.

        Contains integer microusd budgets for both the recurring funded budget
        and the trial, never a per-unit price and never a provider or model
        name. ``premium_limits`` is only present while the trial is funding
        premium work, so a client cannot mistake a stale overlay for a grant.
        """
        payload: dict[str, Any] = dict(self.limits.as_dict())
        payload["budget_source"] = self.budget_source
        payload["budget_ceiling_microusd"] = self.budget_ceiling_microusd
        payload["budget_remaining_microusd"] = self.budget_remaining_microusd
        payload["premium_budget_source"] = self.budget_source_for(True)
        payload["premium_budget_ceiling_microusd"] = self.budget_ceiling_for(True)
        payload["premium_budget_remaining_microusd"] = self.remaining_for(True)
        if self.trial_funds_premium:
            payload["premium_limits"] = self.trial_limits.as_dict()
        return payload


@dataclass(frozen=True, slots=True)
class Reservation:
    """A held allowance for one in-flight operation."""

    id: uuid.UUID
    user_id: uuid.UUID
    period_key: str
    plan: Plan
    operation: str
    charge_kind: ChargeKind
    premium: bool
    extended: bool
    reserved_microusd: Microusd
    status: ReservationStatus
    overage_microusd: Microusd = 0
    created_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status is ReservationStatus.OPEN


@dataclass(frozen=True, slots=True)
class Settlement:
    """The outcome of settling (or releasing) a reservation.

    ``applied`` is False when the reservation was already settled: settlement
    is idempotent, so a retried or duplicated settlement never double-charges.
    """

    reservation: Reservation
    applied: bool
    actual_microusd: Microusd
    released_microusd: Microusd
    period_key: str
    status: ReservationStatus
    overage_microusd: Microusd = 0


@dataclass(frozen=True, slots=True)
class SubscriptionEvent:
    """A trusted plan-change request.

    This type is only constructible by trusted callers. There is no HTTP route
    that accepts one, and :class:`UntrustedSubscriptionSource` rejects any
    source outside the policy allowlist, which never includes a global default
    such as ``DEFAULT_TIER``.
    """

    event_id: str
    user_id: uuid.UUID
    plan: Plan
    source: str
    active: bool = True
    byok: bool | None = None
    occurred_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def payload_fingerprint(self) -> dict[str, Any]:
        """The part of the event that must match for a replay to be idempotent."""
        return {
            "user_id": str(self.user_id),
            "plan": self.plan.value,
            "source": self.source,
            "active": self.active,
            "byok": self.byok,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "metadata": validate_event_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SubscriptionResult:
    """Outcome of applying a subscription event."""

    user_id: uuid.UUID
    plan: Plan
    previous_plan: Plan
    applied: bool
    duplicate: bool
    byok_enabled: bool
    plan_source: str


__all__ = [
    "ALLOWED_EVENT_METADATA_KEYS",
    "ALLOWED_USAGE_KEYS",
    "MAX_LABEL_LENGTH",
    "AccountRecord",
    "AccountStatus",
    "Capability",
    "ChargeKind",
    "Plan",
    "Reservation",
    "ReservationStatus",
    "ResolvedPolicy",
    "Settlement",
    "SubscriptionEvent",
    "SubscriptionResult",
    "TrialState",
    "TrialStatus",
    "UsageLimits",
    "coerce_user_id",
    "validate_event_metadata",
    "validate_usage",
]
