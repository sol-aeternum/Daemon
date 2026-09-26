"""Turn one account row plus the commercial policy into a resolved policy.

This is deliberately a pure function: it is the only place that decides which
allowance an account is charged against and which capabilities it has, so the
rules are stated once and are directly testable.

The rules
---------
* Every plan, ``free`` included, has a **recurring Daemon-funded budget**.
  Routine compute (``premium=False``) always draws on it, so a free account
  keeps working after the trial is gone.
* The **trial is an independent entitlement**. It owns its own capabilities and
  its own premium limit overlay, and the resolver unions them in only while the
  allowance lasts. A free account therefore stops reporting ``premium_routing``
  the moment the trial is exhausted, with no subtraction by the caller.
* Only ``premium=True`` can draw the finite trial, and only while the trial
  funds this account. Routine compute can never spend the premium trial.
* ``byok_enabled`` grants the ``byok`` *capability* and nothing else. It never
  produces :attr:`ChargeKind.EXTERNAL`: a capability flag is not a funding
  adapter, and BYOK must not bypass the Daemon-funded budget. EXTERNAL is
  reserved for a credential-funding adapter that does not exist yet.
* A suspended account has no capabilities, no limits and no budget.
"""

from __future__ import annotations

from orchestrator.entitlements.ledger import trial_extended_remaining, trial_remaining
from orchestrator.entitlements.models import AccountRecord, ResolvedPolicy, TrialStatus
from orchestrator.entitlements.plans import (
    TRIAL_SOURCE_ACTIVE,
    TRIAL_SOURCE_DISABLED,
    TRIAL_SOURCE_PRESERVED,
    AccountStatus,
    Capability,
    Plan,
    TrialState,
    UsageLimits,
)
from orchestrator.entitlements.policy import CommercialPolicy, TrialPolicy

#: Ceilings for a suspended account: nothing is admitted.
ZERO_LIMITS = UsageLimits(
    max_concurrent_operations=0,
    max_context_tokens=0,
    max_output_tokens=0,
    max_tool_loop_iterations=0,
    requests_per_minute=0,
    extended_agents_per_period=0,
    extended_agent_budget_microusd=0,
    monthly_budget_microusd=0,
)


def trial_counters(record: AccountRecord) -> tuple[int, int]:
    """Unspent lifetime trial allowances as ``(microusd, extended_agents)``.

    Counters are authoritative: the denormalised ``trial_state`` column is only
    a hint, because a released hold returns allowance and a stored "exhausted"
    must never outlive the money it referred to.
    """
    remaining_money = trial_remaining(
        budget_microusd=record.trial_budget_microusd,
        consumed_microusd=record.trial_consumed_microusd,
        reserved_microusd=record.trial_reserved_microusd,
    )
    remaining_agents = trial_extended_remaining(
        granted=record.trial_extended_agents,
        used=record.trial_extended_agents_used,
        reserved=record.trial_extended_agents_reserved,
    )
    return remaining_money, remaining_agents


def trial_has_allowance(record: AccountRecord) -> bool:
    """Whether the account's lifetime trial still has anything left."""
    remaining_money, remaining_agents = trial_counters(record)
    return remaining_money > 0


def trial_funds_premium(record: AccountRecord, trial: TrialPolicy) -> bool:
    """Whether the trial is currently the funding source for premium work.

    A paid plan pays for its own premium work from the plan budget, so the
    trial stops funding at that point. Its unspent remainder is preserved, not
    destroyed, and becomes usable again if the account later returns to free.
    """
    if not trial.enabled:
        return False
    if record.status is not AccountStatus.ACTIVE:
        return False
    if record.plan is not Plan.FREE:
        return False
    return trial_has_allowance(record)


def trial_status(record: AccountRecord, trial: TrialPolicy) -> TrialStatus:
    """Trial facts for an account.

    ``state`` is derived from the lifetime counters, so it is correct after a
    release as well as after a settlement. ``source`` says whether the trial is
    funding premium work now, is preserved while a paid plan pays, or is off.
    """
    if not trial.enabled:
        return TrialStatus(
            state=TrialState.EXHAUSTED,
            source=TRIAL_SOURCE_DISABLED,
            budget_microusd=0,
            consumed_microusd=record.trial_consumed_microusd,
            reserved_microusd=record.trial_reserved_microusd,
            remaining_microusd=0,
            extended_agents=0,
            extended_agents_used=record.trial_extended_agents_used,
            extended_agents_reserved=record.trial_extended_agents_reserved,
            extended_agents_remaining=0,
        )

    remaining_money, remaining_agents = trial_counters(record)
    # An in-flight hold is not consumption and must not persist exhaustion.
    has_allowance = record.trial_consumed_microusd < record.trial_budget_microusd
    source = TRIAL_SOURCE_ACTIVE if trial_funds_premium(record, trial) else TRIAL_SOURCE_PRESERVED
    return TrialStatus(
        state=TrialState.ACTIVE if has_allowance else TrialState.EXHAUSTED,
        source=source,
        budget_microusd=record.trial_budget_microusd,
        consumed_microusd=record.trial_consumed_microusd,
        reserved_microusd=record.trial_reserved_microusd,
        remaining_microusd=remaining_money,
        extended_agents=record.trial_extended_agents,
        extended_agents_used=record.trial_extended_agents_used,
        extended_agents_reserved=record.trial_extended_agents_reserved,
        extended_agents_remaining=remaining_agents,
    )


def premium_limits(plan_limits: UsageLimits, trial: TrialPolicy) -> UsageLimits:
    """Overlay the trial's premium ceilings onto the plan's limits.

    The overlay can only raise a ceiling, never lower it: the plan is the floor
    for what the account may ask for.
    """
    return UsageLimits(
        max_concurrent_operations=plan_limits.max_concurrent_operations,
        max_context_tokens=max(plan_limits.max_context_tokens, trial.limits.max_context_tokens),
        max_output_tokens=max(plan_limits.max_output_tokens, trial.limits.max_output_tokens),
        max_tool_loop_iterations=plan_limits.max_tool_loop_iterations,
        requests_per_minute=plan_limits.requests_per_minute,
        extended_agents_per_period=plan_limits.extended_agents_per_period,
        extended_agent_budget_microusd=plan_limits.extended_agent_budget_microusd,
        monthly_budget_microusd=plan_limits.monthly_budget_microusd,
    )


def resolve_account_policy(
    record: AccountRecord,
    policy: CommercialPolicy,
    *,
    period: str,
    period_spent_microusd: int = 0,
    period_reserved_microusd: int = 0,
) -> ResolvedPolicy:
    """Resolve the effective policy for one account row.

    ``period_spent_microusd``/``period_reserved_microusd`` are the current
    period's counters for plan-funded work; they are only used to report the
    remaining recurring budget and never to change a ceiling.
    """
    definition = policy.plan(record.plan)
    status = trial_status(record, policy.trial)
    funds_premium = trial_funds_premium(record, policy.trial)

    capabilities = set(definition.capabilities)
    if funds_premium:
        # The trial owns these while it lasts, and loses them when it is spent.
        capabilities |= set(policy.trial.capabilities)
        if status.extended_agents_remaining == 0:
            capabilities.discard(Capability.EXTENDED_AGENTS)
    if record.byok_enabled:
        # A capability grant only: it never changes which allowance pays.
        capabilities.add(Capability.BYOK)

    limits = definition.limits
    if record.status is not AccountStatus.ACTIVE:
        capabilities = set()
        limits = ZERO_LIMITS

    return ResolvedPolicy(
        user_id=record.user_id,
        plan=record.plan,
        plan_source=record.plan_source,
        status=record.status,
        byok_enabled=record.byok_enabled,
        capabilities=frozenset(capabilities),
        limits=limits,
        trial_limits=premium_limits(limits, policy.trial) if funds_premium else limits,
        recurring_budget_microusd=definition.limits.monthly_budget_microusd,
        trial=status,
        trial_funds_premium=funds_premium,
        period_key=period,
        period_spent_microusd=period_spent_microusd,
        period_reserved_microusd=period_reserved_microusd,
    )


__all__ = [
    "ZERO_LIMITS",
    "premium_limits",
    "resolve_account_policy",
    "trial_counters",
    "trial_funds_premium",
    "trial_has_allowance",
    "trial_status",
]
