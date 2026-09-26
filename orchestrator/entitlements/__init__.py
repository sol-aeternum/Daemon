"""Commercial plan and entitlement foundation.

This package replaces the legacy global tier default with a per-account model.
It is deliberately standalone: it owns no routes, touches no existing memory or
video-credit table, and has no billing-provider integration.

Design in one screen
--------------------
*Plans and capabilities* (:mod:`.plans`, ``config/commercial.json``)
    Three plans -- ``free``, ``pro``, ``power``. Capabilities (``chat``,
    ``web_research``, ``image_generation``, ``extended_agents``,
    ``premium_routing``, ``byok``, ...) are a closed vocabulary, and an unknown
    name in configuration is a hard error, not a silent grant.

*Money* (:mod:`.money`)
    Internal budgets are integer **microusd** (1 USD = 1_000_000 microusd).
    Displayed prices are configured in AUD minor units and are never derived
    from budgets, so no FX rate is invented at runtime.

*Trial* (:mod:`.ledger`, :mod:`.resolver`)
    A new account is ``free`` with a finite trial allowance. It is consumed
    only by settled usage and has no calendar expiry, so it is independent of
    any period. A paid activation preserves the unspent remainder.

*Ledger* (:mod:`.ledger`, :mod:`.store`)
    ``reserve``/``settle`` hold and release real spend against hard account
    ceilings: budget, extended-run allowance, rate window and concurrency. The
    per-user account row is the serialization point and the hold is a single
    guarded ``UPDATE``, so concurrent operations cannot pass a ceiling. A
    reservation is charged to the period it was created in, which makes month
    rollover safe for operations in flight.

*Inference policy* (:mod:`.policy`, ``config/inference_policy.json``)
    Kept separate from commercial config on purpose. A provider/model route is
    usable only when it has a pinned https endpoint, the full pinned transport
    privacy block (``zdr``, ``data_collection: deny``, ``allow_fallbacks:
    false``, ``require_parameters: true``, single provider slug in
    ``provider.only``/``order``), account-level prompt/completion logging
    disabled, a free-model training opt-out, a pinned maximum price, a dated
    approval, and a dated operator review with evidence. Billable tool services
    (search, embeddings, ...) carry their own ceiling and are deny-by-default.
    As shipped, nothing is approved.

*No public endpoint* (:mod:`.service`)
    A plan changes only via :meth:`EntitlementService.apply_subscription_event`
    or the explicit per-user
    :meth:`EntitlementService.import_legacy_tier`, both idempotent per
    ``event_id`` and restricted to allowlisted sources. The legacy global
    ``DEFAULT_TIER`` is never a source: it can neither grant a plan nor be
    mapped in bulk.

Quick start::

    from orchestrator.entitlements import EntitlementService

    service = EntitlementService(pool)
    snapshot = await service.public_snapshot(user_id)
    reservation = await service.reserve(user_id, 1_500, operation="chat")
    await service.settle(reservation, 1_200, usage={"input_tokens": 900})
"""

from __future__ import annotations

from orchestrator.entitlements.errors import (
    AccountSuspended,
    BudgetExceeded,
    CapabilityDenied,
    ConcurrencyExceeded,
    EntitlementsError,
    ExtendedRunExceeded,
    InvalidReservationAmount,
    InvalidSubscriptionEvent,
    InvalidUsageMetadata,
    LimitExceeded,
    PolicyError,
    RateLimitExceeded,
    ReservationNotFound,
    RouteNotApproved,
    SubscriptionEventConflict,
    TrialExhausted,
    UnknownAccount,
    UnknownCapability,
    UnknownOperation,
    UntrustedSubscriptionSource,
)
from orchestrator.entitlements.ledger import (
    Admission,
    AdmissionContext,
    PeriodState,
    ReservationRequest,
    admit,
    next_period_key,
    period_key,
    trial_remaining,
)
from orchestrator.entitlements.models import (
    AccountRecord,
    Reservation,
    ResolvedPolicy,
    Settlement,
    SubscriptionEvent,
    SubscriptionResult,
    TrialStatus,
    coerce_user_id,
    validate_usage,
)
from orchestrator.entitlements.money import MICRO_USD_PER_USD, Microusd, require_microusd
from orchestrator.entitlements.plans import (
    AccountStatus,
    Capability,
    ChargeKind,
    Plan,
    PlanDefinition,
    ReservationStatus,
    TrialState,
    UsageLimits,
)
from orchestrator.entitlements.policy import (
    CommercialPolicy,
    InferencePolicy,
    OperatorReview,
    PolicyRequirements,
    PriceCeiling,
    RoutePolicy,
    ToolServicePolicy,
    TransportPrivacy,
    TrialPolicy,
    load_inference_policy,
    load_policy,
    parse_commercial_policy,
    parse_inference_policy,
)
from orchestrator.entitlements.resolver import resolve_account_policy, trial_status
from orchestrator.entitlements.service import EntitlementService
from orchestrator.entitlements.store import EntitlementStore

__all__ = [
    "MICRO_USD_PER_USD",
    "AccountRecord",
    "AccountStatus",
    "AccountSuspended",
    "Admission",
    "AdmissionContext",
    "BudgetExceeded",
    "Capability",
    "CapabilityDenied",
    "ChargeKind",
    "CommercialPolicy",
    "ConcurrencyExceeded",
    "EntitlementService",
    "EntitlementStore",
    "EntitlementsError",
    "ExtendedRunExceeded",
    "InferencePolicy",
    "InvalidReservationAmount",
    "InvalidSubscriptionEvent",
    "InvalidUsageMetadata",
    "LimitExceeded",
    "Microusd",
    "OperatorReview",
    "PeriodState",
    "Plan",
    "PlanDefinition",
    "PolicyError",
    "PolicyRequirements",
    "PriceCeiling",
    "RateLimitExceeded",
    "Reservation",
    "ReservationNotFound",
    "ReservationRequest",
    "ReservationStatus",
    "ResolvedPolicy",
    "RouteNotApproved",
    "RoutePolicy",
    "Settlement",
    "SubscriptionEvent",
    "SubscriptionEventConflict",
    "SubscriptionResult",
    "ToolServicePolicy",
    "TransportPrivacy",
    "TrialExhausted",
    "TrialPolicy",
    "TrialState",
    "TrialStatus",
    "UnknownAccount",
    "UnknownCapability",
    "UnknownOperation",
    "UntrustedSubscriptionSource",
    "UsageLimits",
    "admit",
    "coerce_user_id",
    "load_inference_policy",
    "load_policy",
    "next_period_key",
    "parse_commercial_policy",
    "parse_inference_policy",
    "period_key",
    "require_microusd",
    "resolve_account_policy",
    "trial_remaining",
    "trial_status",
    "validate_usage",
]
