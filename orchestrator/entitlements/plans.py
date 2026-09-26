"""Plans, capabilities and per-plan usage limits.

These are the only vocabulary types in the entitlements layer. They are
deliberately closed: an unknown plan or capability in configuration is a hard
:func:`~orchestrator.entitlements.errors.PolicyError`, so a typo fails closed
instead of silently granting or dropping access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.money import Microusd, require_microusd


class Plan(StrEnum):
    """Commercial plans. There is no ``starter``/``max``/``byok`` plan.

    The retired tier names survive only as keys in the policy's
    ``legacy_plan_map`` for explicit per-user migration.
    """

    FREE = "free"
    PRO = "pro"
    POWER = "power"


class Capability(StrEnum):
    """A gated product capability, independent of any specific model.

    Names are the product's agreed vocabulary. There are deliberately no
    synonyms: two spellings of the same grant would drift apart over time.
    """

    CHAT = "chat"
    WEB_RESEARCH = "web_research"
    DEEP_RESEARCH = "deep_research"
    SCHEDULED_TASKS = "scheduled_tasks"
    PARALLEL_AGENTS = "parallel_agents"
    EXTENDED_AGENTS = "extended_agents"
    LARGE_FILE_PROCESSING = "large_file_processing"
    LARGE_CONTEXT = "large_context"
    PRIORITY_EXECUTION = "priority_execution"
    PREMIUM_ROUTING = "premium_routing"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    AUDIO_GENERATION = "audio_generation"
    BYOK = "byok"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class TrialState(StrEnum):
    """Trial lifecycle. There is no ``expired`` state: trials do not expire."""

    ACTIVE = "active"
    EXHAUSTED = "exhausted"


class ChargeKind(StrEnum):
    """Which allowance a reservation is charged against.

    ``TRIAL``  the account's finite lifetime trial allowance. Only a
        ``premium=True`` operation on a free account draws this, so routine
        compute never eats the premium trial.
    ``PLAN``   the plan's recurring funded budget. Every plan has one,
        including ``free``: a Daemon-funded floor that survives trial
        exhaustion.
    ``EXTERNAL``  the user's own provider credentials paid for the call.

        **Never produced by the resolver.** It exists in the schema for a
        credential-funding adapter that does not exist yet, and a capability
        flag such as ``byok`` is not a funding adapter: enabling BYOK must not
        bypass the Daemon-funded budget.
    """

    TRIAL = "trial"
    PLAN = "plan"
    EXTERNAL = "external"


class ReservationStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"
    RELEASED = "released"


#: ``ResolvedPolicy.budget_source`` values: which allowance pays.
BUDGET_SOURCE_PLAN = "plan"
BUDGET_SOURCE_TRIAL = "trial"
BUDGET_SOURCE_NONE = "none"

#: ``TrialStatus.source`` values: whether the trial can still fund premium work.
TRIAL_SOURCE_ACTIVE = "trial"
TRIAL_SOURCE_PRESERVED = "preserved"
TRIAL_SOURCE_DISABLED = "disabled"


#: Capabilities a plan may grant. Used to validate configuration.
KNOWN_CAPABILITIES: frozenset[Capability] = frozenset(Capability)


@dataclass(frozen=True, slots=True)
class UsageLimits:
    """Hard ceilings for one resolved plan.

    ``monthly_budget_microusd`` is the plan's *recurring* funded allowance and
    every plan has one, ``free`` included: a small Daemon-funded floor that
    keeps routine compute working after the finite trial is gone. The finite
    trial is a separate, lifetime allowance tracked on the account row, and
    only a ``premium=True`` operation draws on it.
    """

    max_concurrent_operations: int
    max_context_tokens: int
    max_output_tokens: int
    max_tool_loop_iterations: int
    requests_per_minute: int
    extended_agents_per_period: int
    extended_agent_budget_microusd: Microusd
    monthly_budget_microusd: Microusd

    def as_dict(self) -> dict[str, int]:
        """JSON-safe mapping used by the public snapshot."""
        return {
            "max_concurrent_operations": self.max_concurrent_operations,
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_tool_loop_iterations": self.max_tool_loop_iterations,
            "requests_per_minute": self.requests_per_minute,
            "extended_agents_per_period": self.extended_agents_per_period,
            "extended_agent_budget_microusd": self.extended_agent_budget_microusd,
            "monthly_budget_microusd": self.monthly_budget_microusd,
        }

    def validate(self, *, plan: Plan, capabilities: frozenset[Capability]) -> None:
        """Reject internally inconsistent limits at load time."""
        for name in (
            "max_concurrent_operations",
            "max_context_tokens",
            "max_output_tokens",
            "max_tool_loop_iterations",
            "requests_per_minute",
            "extended_agents_per_period",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PolicyError(f"{plan.value}.limits.{name} must be a non-negative integer")

        for name in ("extended_agent_budget_microusd", "monthly_budget_microusd"):
            try:
                require_microusd(getattr(self, name), field=f"{plan.value}.limits.{name}")
            except ValueError as exc:
                raise PolicyError(str(exc)) from exc

        if self.max_concurrent_operations < 1:
            raise PolicyError(f"{plan.value}.limits.max_concurrent_operations must be >= 1")
        if self.max_output_tokens > self.max_context_tokens:
            raise PolicyError(
                f"{plan.value}.limits.max_output_tokens must not exceed max_context_tokens"
            )
        if self.monthly_budget_microusd <= 0:
            raise PolicyError(
                f"{plan.value}.monthly_budget_microusd must be greater than 0: every plan, "
                f"including free, has a Daemon-funded recurring budget"
            )

        has_extended = Capability.EXTENDED_AGENTS in capabilities
        if self.extended_agents_per_period > 0 and not has_extended:
            raise PolicyError(
                f"{plan.value}: limits.extended_agents_per_period is set without the "
                f"extended_agents capability"
            )
        if self.extended_agent_budget_microusd > 0 and not has_extended:
            raise PolicyError(
                f"{plan.value}: limits.extended_agent_budget_microusd is set without the "
                f"extended_agents capability"
            )
        if has_extended and self.extended_agents_per_period > 0:
            if self.extended_agent_budget_microusd <= 0:
                raise PolicyError(
                    f"{plan.value}: a per-period extended agent allowance needs an "
                    f"extended_agent_budget_microusd amount"
                )


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    """A plan's capabilities, limits and displayed price."""

    plan: Plan
    display_label: str
    display_currency: str
    display_amount_minor: int
    capabilities: frozenset[Capability]
    limits: UsageLimits

    def grants(self, capability: Capability | str) -> bool:
        try:
            wanted = parse_capability(capability)
        except PolicyError:
            return False
        return wanted in self.capabilities

    def public_display(self) -> dict[str, Any]:
        """Displayed price for a public catalog. Never contains internal budgets."""
        return {
            "plan": self.plan.value,
            "label": self.display_label,
            "price": {
                "currency": self.display_currency,
                "amount_minor": self.display_amount_minor,
            },
        }


def parse_capability(value: object) -> Capability:
    """Parse a capability name, rejecting anything outside the closed set."""
    if isinstance(value, Capability):
        return value
    if not isinstance(value, str):
        raise PolicyError(f"capability must be a string, got {type(value).__name__}")
    try:
        return Capability(value.strip().lower())
    except ValueError as exc:
        known = ", ".join(sorted(c.value for c in Capability))
        raise PolicyError(f"unknown capability {value!r}; known capabilities: {known}") from exc


def parse_plan(value: object) -> Plan:
    """Parse a plan name, rejecting anything outside the closed set."""
    if isinstance(value, Plan):
        return value
    if not isinstance(value, str):
        raise PolicyError(f"plan must be a string, got {type(value).__name__}")
    try:
        return Plan(value.strip().lower())
    except ValueError as exc:
        known = ", ".join(sorted(p.value for p in Plan))
        raise PolicyError(f"unknown plan {value!r}; known plans: {known}") from exc


def parse_capabilities(values: object, *, field: str) -> frozenset[Capability]:
    """Parse a list of capability names into a frozenset."""
    if not isinstance(values, (list, tuple)):
        raise PolicyError(f"{field} must be a list of capability names")
    return frozenset(parse_capability(item) for item in values)


def immutable_map(mapping: Mapping[Plan, PlanDefinition]) -> Mapping[Plan, PlanDefinition]:
    return MappingProxyType(dict(mapping))


__all__ = [
    "AccountStatus",
    "Capability",
    "ChargeKind",
    "KNOWN_CAPABILITIES",
    "Plan",
    "PlanDefinition",
    "ReservationStatus",
    "TrialState",
    "UsageLimits",
    "immutable_map",
    "parse_capabilities",
    "parse_capability",
    "parse_plan",
]
