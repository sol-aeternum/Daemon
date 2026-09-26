"""Errors raised by the entitlements layer.

Every error is an :class:`EntitlementsError`, so a caller that only wants to
map a denial onto an HTTP status can catch the base class without importing
the individual subclasses.
"""

from __future__ import annotations


class EntitlementsError(Exception):
    """Base class for every entitlements failure."""


class PolicyError(EntitlementsError):
    """Commercial or inference policy configuration is invalid.

    Raised by the loaders, never at request time: invalid configuration must
    fail closed at startup rather than degrade silently.
    """


class AccountError(EntitlementsError):
    """The account cannot be used at all."""


class AccountSuspended(AccountError):
    """The account exists but its status is not ``active``."""


class UnknownAccount(AccountError):
    """The user has no entitlement account and none could be created."""


class AccessDenied(EntitlementsError):
    """A capability is required but the resolved policy does not grant it."""


class CapabilityDenied(AccessDenied):
    """The resolved plan does not grant the requested capability."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"capability not granted: {capability}")
        self.capability = capability


class UnknownCapability(AccessDenied):
    """The caller asked for a capability that is not part of the known set."""


class LimitExceeded(EntitlementsError):
    """A hard account ceiling refused the operation."""

    #: Stable machine-readable code for callers and tests.
    code = "limit_exceeded"


class BudgetExceeded(LimitExceeded):
    code = "budget_exceeded"

    def __init__(self, *, requested: int, spent: int, reserved: int, ceiling: int) -> None:
        super().__init__(
            f"budget exceeded: requested={requested} spent={spent} "
            f"reserved={reserved} ceiling={ceiling}"
        )
        self.requested = requested
        self.spent = spent
        self.reserved = reserved
        self.ceiling = ceiling


class ConcurrencyExceeded(LimitExceeded):
    code = "concurrency_exceeded"

    def __init__(self, *, open_reservations: int, ceiling: int) -> None:
        super().__init__(
            f"concurrency ceiling exceeded: open={open_reservations} ceiling={ceiling}"
        )
        self.open_reservations = open_reservations
        self.ceiling = ceiling


class RateLimitExceeded(LimitExceeded):
    code = "rate_limited"

    def __init__(self, *, requests_in_window: int, ceiling: int) -> None:
        super().__init__(
            f"rate ceiling exceeded: requests_in_window={requests_in_window} ceiling={ceiling}"
        )
        self.requests_in_window = requests_in_window
        self.ceiling = ceiling


class ExtendedRunExceeded(LimitExceeded):
    code = "extended_agents_exceeded"

    def __init__(self, *, used: int, reserved: int, ceiling: int) -> None:
        super().__init__(
            f"extended agent ceiling exceeded: used={used} reserved={reserved} ceiling={ceiling}"
        )
        self.used = used
        self.reserved = reserved
        self.ceiling = ceiling


class TrialExhausted(LimitExceeded):
    code = "trial_exhausted"


class TrialExtendedAgentsExhausted(LimitExceeded):
    """The trial's lifetime extended-agent count is used up.

    Separate from the money trial: an account can still have trial money left
    while its extended allowance is gone, and vice versa.
    """

    code = "trial_extended_agents_exhausted"

    def __init__(self, *, remaining: int) -> None:
        super().__init__(f"trial extended agent allowance exhausted; remaining={remaining}")
        self.remaining = remaining


class ExtendedBudgetExceeded(BudgetExceeded):
    """The extended-run allowance was the ceiling that bound, not the budget."""

    code = "extended_budget_exceeded"


class UnknownOperation(EntitlementsError):
    """The requested operation is not a known billable operation."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"unknown operation: {operation}")
        self.operation = operation


class ReservationNotFound(EntitlementsError):
    """No reservation exists for the supplied identifier."""

    def __init__(self, reservation_id: str) -> None:
        super().__init__(f"reservation not found: {reservation_id}")
        self.reservation_id = reservation_id


class SettlementConflict(EntitlementsError):
    """A settlement was replayed with different numbers than the first one.

    Replaying a settlement with the same values is idempotent and returns the
    stored outcome. Replaying it with a *different* amount is not a retry, it is
    a second claim on the same reservation, and it is refused rather than
    silently ignored.
    """

    def __init__(self, reservation_id: str, stored: int, offered: int) -> None:
        super().__init__(
            f"reservation {reservation_id} already settled at {stored}, "
            f"cannot re-settle at {offered}"
        )
        self.reservation_id = reservation_id
        self.stored_microusd = stored
        self.offered_microusd = offered


class InvalidReservationAmount(EntitlementsError):
    """A reservation or settlement amount was negative or not an integer."""


class InvalidUsageMetadata(EntitlementsError):
    """Usage metadata was not a flat mapping of allowed scalar keys."""


class RouteNotApproved(EntitlementsError):
    """An inference route or tool service failed the policy requirements.

    Raised instead of returning a partially qualified transport payload, so a
    caller cannot send a request with the privacy or price pins missing.
    """

    def __init__(self, route_id: str, reasons: tuple[str, ...]) -> None:
        super().__init__(f"route {route_id!r} is not approved: {', '.join(reasons)}")
        self.route_id = route_id
        self.reasons = reasons


class InvalidSubscriptionEvent(EntitlementsError):
    """A subscription event is structurally invalid (for example blank event_id)."""


class UntrustedSubscriptionSource(EntitlementsError):
    """The event source is not in the policy's trusted source allowlist.

    There is no public or unauthenticated way to submit a plan change: the
    service helper is the only writer, and it only accepts allowlisted
    sources. In particular ``DEFAULT_TIER`` is never an accepted source.
    """

    def __init__(self, source: str) -> None:
        super().__init__(f"untrusted subscription source: {source!r}")
        self.source = source


class SubscriptionEventConflict(EntitlementsError):
    """An ``event_id`` was reused with a different payload.

    Replaying the same event is idempotent; reusing its id for a different
    event is a conflict and is rejected rather than silently overwritten.
    """

    def __init__(self, event_id: str) -> None:
        super().__init__(f"event_id already recorded with a different payload: {event_id}")
        self.event_id = event_id


class StaleSubscriptionEvent(EntitlementsError):
    """An event is older than the newest event already applied to the account.

    Plan changes are ordered by effective event time, so a late-delivered
    webhook cannot regress an account to a superseded plan.
    """

    def __init__(self, event_id: str, occurred_at: str, newest: str) -> None:
        super().__init__(
            f"event {event_id} occurred at {occurred_at}, older than the newest "
            f"applied event at {newest}"
        )
        self.event_id = event_id
        self.occurred_at = occurred_at
        self.newest_occurred_at = newest


__all__ = [
    "AccessDenied",
    "AccountError",
    "AccountSuspended",
    "BudgetExceeded",
    "CapabilityDenied",
    "ConcurrencyExceeded",
    "EntitlementsError",
    "ExtendedBudgetExceeded",
    "ExtendedRunExceeded",
    "InvalidReservationAmount",
    "InvalidSubscriptionEvent",
    "InvalidUsageMetadata",
    "LimitExceeded",
    "PolicyError",
    "RateLimitExceeded",
    "ReservationNotFound",
    "RouteNotApproved",
    "SettlementConflict",
    "StaleSubscriptionEvent",
    "SubscriptionEventConflict",
    "TrialExhausted",
    "TrialExtendedAgentsExhausted",
    "UnknownAccount",
    "UnknownCapability",
    "UnknownOperation",
    "UntrustedSubscriptionSource",
]
