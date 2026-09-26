"""Pure ledger rules for reservation admission and settlement.

Everything in this module is a deterministic function of its arguments: no
database, no clock reads, no I/O. The SQL in
:mod:`orchestrator.entitlements.store` enforces the *same* predicates inside a
transaction so admission is atomic; the service calls the SQL and, when the
SQL refuses, re-reads state and calls :func:`admit` again to report the exact
reason. Keeping the rules here means the reason codes and ceilings are unit
testable without a database.

A period is one UTC calendar month, keyed by :func:`period_key`. A reservation
is charged to the period in which it was *created*; settlement never moves
spend into a later period, so an operation in flight across a month boundary
cannot consume the new month's allowance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.money import Microusd, require_microusd
from orchestrator.entitlements.plans import ChargeKind, UsageLimits

#: Length of the fixed rate window used by ``requests_per_minute``.
RATE_WINDOW_SECONDS: Final[int] = 60

DenialCode = Literal[
    "rate_limited",
    "concurrency_exceeded",
    "extended_agents_exceeded",
    "extended_budget_exceeded",
    "budget_exceeded",
    "trial_exhausted",
    "trial_extended_agents_exhausted",
]


@dataclass(frozen=True, slots=True)
class PeriodState:
    """Mirror of one ``entitlement_usage_periods`` row.

    ``*_reserved`` counters are holds placed by admitted reservations that have
    not settled yet; ``*_used``/``spent`` counters are settled usage only.
    A ceiling is enforced against ``used + reserved`` so an in-flight operation
    already owns its allowance.

    The period row owns money only for plan-funded work. Trial-funded work is
    accounted on the account row, because the trial is a lifetime allowance
    that does not reset at a period boundary.
    """

    period_key: str
    spent_microusd: Microusd = 0
    reserved_microusd: Microusd = 0
    extended_spent_microusd: Microusd = 0
    extended_reserved_microusd: Microusd = 0
    extended_agents_used: int = 0
    extended_agents_reserved: int = 0
    open_reservations: int = 0
    requests_in_window: int = 0
    window_started_at: datetime | None = None

    def remaining(self, ceiling: Microusd) -> Microusd:
        """Microusd still available under ``ceiling`` (never negative)."""
        return max(0, ceiling - self.spent_microusd - self.reserved_microusd)

    def open_rate_window(self, now: datetime) -> bool:
        """True when the fixed rate window containing ``now`` has no requests."""
        return _window_is_stale(self, now)

    def as_dict(self) -> dict[str, int | str | None]:
        """Counters only, for diagnostics and limit-encounter records.

        Deliberately no user content: this is safe to log or aggregate.
        """
        return {
            "period_key": self.period_key,
            "spent_microusd": self.spent_microusd,
            "reserved_microusd": self.reserved_microusd,
            "extended_spent_microusd": self.extended_spent_microusd,
            "extended_reserved_microusd": self.extended_reserved_microusd,
            "extended_agents_used": self.extended_agents_used,
            "extended_agents_reserved": self.extended_agents_reserved,
            "open_reservations": self.open_reservations,
            "requests_in_window": self.requests_in_window,
            "window_started_at": (
                self.window_started_at.isoformat() if self.window_started_at else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    """The admission request for one billable operation."""

    amount_microusd: Microusd
    extended: bool = False
    charge_kind: ChargeKind = ChargeKind.PLAN

    def validate(self) -> None:
        try:
            require_microusd(self.amount_microusd, field="amount_microusd")
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    """Ceilings and account facts that apply to one admission decision."""

    limits: UsageLimits
    budget_ceiling_microusd: Microusd
    charge_kind: ChargeKind
    now: datetime
    trial_exhausted: bool = False
    trial_extended_agents_remaining: int = 0

    def money_applies(self) -> bool:
        """BYOK accounts pay their own provider, so no internal money ceiling.

        Currently unreachable: the resolver never produces
        :attr:`ChargeKind.EXTERNAL`. Kept so a future credential-funding
        adapter cannot accidentally inherit a budget it never paid for.
        """
        return self.charge_kind is not ChargeKind.EXTERNAL

    def extended_uses_period_allowance(self) -> bool:
        """Per-period extended ceilings apply to plan-funded work only.

        Trial-funded extended work is bounded by the trial's own lifetime count,
        which is independent of the plan's per-period quota.
        """
        return self.charge_kind is ChargeKind.PLAN

    def period_owns_money(self) -> bool:
        """Whether the period row accounts the money for this operation.

        Only plan-funded work is accounted per period. Trial-funded work is
        accounted exclusively by the account's lifetime counters: if the period
        row also counted it, the trial remainder would be subtracted twice.
        """
        return self.charge_kind is ChargeKind.PLAN

    def money_available(self, state: PeriodState) -> Microusd:
        """Microusd available to this operation right now.

        For trial-funded work the ceiling already comes from the account's
        lifetime counters, so the period counters must not be subtracted again.
        """
        if not self.period_owns_money():
            return self.budget_ceiling_microusd
        return state.remaining(self.budget_ceiling_microusd)


@dataclass(frozen=True, slots=True)
class Admission:
    """Result of :func:`admit`. ``denial_code`` is stable and machine readable."""

    admitted: bool
    denial_code: DenialCode | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.admitted


ADMITTED: Final[Admission] = Admission(admitted=True)


def period_key(now: datetime) -> str:
    """UTC calendar-month key (``YYYY-MM``) for ``now``."""
    if now.tzinfo is None:
        raise PolicyError("period_key requires a timezone-aware datetime")
    return now.astimezone(timezone.utc).strftime("%Y-%m")


def next_period_key(period: str) -> str:
    """The period key following ``period`` (used to test rollover)."""
    if len(period) != 7 or period[4] != "-":
        raise PolicyError(f"invalid period key: {period!r}")
    year, month = (int(part) for part in period.split("-"))
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _window_is_stale(state: PeriodState, now: datetime) -> bool:
    if state.window_started_at is None:
        return True
    return now - state.window_started_at >= timedelta(seconds=RATE_WINDOW_SECONDS)


def effective_rate_requests(state: PeriodState, now: datetime) -> int:
    """Requests counted in the fixed window that contains ``now``."""
    if _window_is_stale(state, now):
        return 0
    return state.requests_in_window


def admit(
    state: PeriodState,
    request: ReservationRequest,
    context: AdmissionContext,
) -> Admission:
    """Decide whether ``request`` may be admitted against ``state``.

    Check order is deterministic so denials are reproducible:
    rate window, concurrency, extended-run count, extended-run money, budget.
    """
    request.validate()
    limits = context.limits

    if effective_rate_requests(state, context.now) >= limits.requests_per_minute:
        return Admission(
            admitted=False,
            denial_code="rate_limited",
            reason=(
                f"rate window already holds {state.requests_in_window} of "
                f"{limits.requests_per_minute} requests"
            ),
        )

    if state.open_reservations >= limits.max_concurrent_operations:
        return Admission(
            admitted=False,
            denial_code="concurrency_exceeded",
            reason=(
                f"{state.open_reservations} open reservations of "
                f"{limits.max_concurrent_operations} allowed"
            ),
        )

    if request.extended and context.extended_uses_period_allowance():
        extended_total = state.extended_agents_used + state.extended_agents_reserved
        if extended_total >= limits.extended_agents_per_period:
            return Admission(
                admitted=False,
                denial_code="extended_agents_exceeded",
                reason=(
                    f"{extended_total} extended agents of "
                    f"{limits.extended_agents_per_period} allowed this period"
                ),
            )
        if context.money_applies():
            extended_total_money = state.extended_spent_microusd + state.extended_reserved_microusd
            if (
                extended_total_money + request.amount_microusd
                > limits.extended_agent_budget_microusd
            ):
                return Admission(
                    admitted=False,
                    denial_code="extended_budget_exceeded",
                    reason=(
                        f"extended budget {extended_total_money} of "
                        f"{limits.extended_agent_budget_microusd} used, "
                        f"requested {request.amount_microusd}"
                    ),
                )

    if request.extended and context.charge_kind is ChargeKind.TRIAL:
        if context.trial_extended_agents_remaining <= 0:
            return Admission(
                admitted=False,
                denial_code="trial_extended_agents_exhausted",
                reason="the lifetime trial extended agent count is used up",
            )

    if context.money_applies():
        available = context.money_available(state)
        if request.amount_microusd > 0 and request.amount_microusd > available:
            if context.charge_kind is ChargeKind.TRIAL:
                return Admission(
                    admitted=False,
                    denial_code="trial_exhausted",
                    reason=(
                        f"trial allowance exhausted; requested {request.amount_microusd} of "
                        f"ceiling {context.budget_ceiling_microusd}"
                    ),
                )
            return Admission(
                admitted=False,
                denial_code="budget_exceeded",
                reason=(
                    f"requested {request.amount_microusd} exceeds remaining {available} "
                    f"of ceiling {context.budget_ceiling_microusd}"
                ),
            )

    return ADMITTED


def apply_reservation(
    state: PeriodState,
    request: ReservationRequest,
    now: datetime,
    *,
    period_money: bool = True,
) -> PeriodState:
    """Return ``state`` after admitting ``request`` (the held-allowance view).

    ``period_money=False`` for trial-funded work: the hold still occupies a
    concurrency slot and a rate slot, but the period row must not take the
    money, because the trial counters on the account own it.
    """
    if _window_is_stale(state, now):
        window_started_at: datetime | None = now
        requests_in_window = 1
    else:
        window_started_at = state.window_started_at
        requests_in_window = state.requests_in_window + 1

    return replace(
        state,
        reserved_microusd=(
            state.reserved_microusd + request.amount_microusd
            if period_money
            else state.reserved_microusd
        ),
        extended_reserved_microusd=(
            state.extended_reserved_microusd + request.amount_microusd
            if request.extended and period_money
            else state.extended_reserved_microusd
        ),
        extended_agents_reserved=(
            state.extended_agents_reserved + 1
            if request.extended and context_uses_period_extended(request)
            else state.extended_agents_reserved
        ),
        open_reservations=state.open_reservations + 1,
        requests_in_window=requests_in_window,
        window_started_at=window_started_at,
    )


def context_uses_period_extended(request: ReservationRequest) -> bool:
    """Whether the period row tracks the extended allowance for this request."""
    return request.charge_kind is ChargeKind.PLAN


def settle_state(
    state: PeriodState,
    *,
    reserved_microusd: Microusd,
    actual_microusd: Microusd,
    extended: bool,
    now: datetime,
    period_money: bool = True,
    ceiling_microusd: Microusd | None = None,
    consumed: bool = True,
) -> PeriodState:
    """Return ``state`` after a reservation settles at ``actual_microusd``.

    The full reserved amount is released and only ``actual_microusd`` becomes
    settled spend, so an operation that used less than it held gives the
    difference back.

    An operation that used more than it reserved is still closed: the period
    counters stop at ``ceiling_microusd`` and the excess is the caller's
    overage to record. Leaving the reservation open instead would hold a
    concurrency slot forever.

    ``reserved_microusd`` is only meaningful when ``period_money`` is true; a
    trial-funded hold never touched the period money columns, so it must not be
    released from them either.
    """
    require_microusd(reserved_microusd, field="reserved_microusd")
    require_microusd(actual_microusd, field="actual_microusd")
    if period_money and reserved_microusd > state.reserved_microusd:
        raise PolicyError(
            f"cannot settle {reserved_microusd} against {state.reserved_microusd} reserved"
        )

    if period_money and ceiling_microusd is not None:
        applied = min(actual_microusd, max(0, ceiling_microusd - state.spent_microusd))
    else:
        applied = actual_microusd

    return replace(
        state,
        spent_microusd=state.spent_microusd + applied,
        reserved_microusd=(
            state.reserved_microusd - reserved_microusd if period_money else state.reserved_microusd
        ),
        extended_spent_microusd=(
            state.extended_spent_microusd + applied
            if extended and period_money
            else state.extended_spent_microusd
        ),
        extended_reserved_microusd=(
            state.extended_reserved_microusd - reserved_microusd
            if extended and period_money
            else state.extended_reserved_microusd
        ),
        extended_agents_used=state.extended_agents_used
        + (1 if extended and period_money and consumed else 0),
        extended_agents_reserved=state.extended_agents_reserved
        - (1 if extended and period_money else 0),
        open_reservations=max(0, state.open_reservations - 1),
        requests_in_window=state.requests_in_window,
        window_started_at=state.window_started_at,
    )


def release_state(
    state: PeriodState,
    *,
    reserved_microusd: Microusd,
    extended: bool,
    now: datetime,
    charge_kind: ChargeKind = ChargeKind.PLAN,
) -> PeriodState:
    """Return ``state`` after a reservation is abandoned at zero cost.

    A release returns the whole hold, so a trial account is never left looking
    exhausted because of an operation that never happened.
    """
    return settle_state(
        state,
        reserved_microusd=reserved_microusd,
        actual_microusd=0,
        extended=extended,
        now=now,
        period_money=charge_kind is ChargeKind.PLAN,
        consumed=False,
    )


def trial_remaining(
    *,
    budget_microusd: Microusd,
    consumed_microusd: Microusd,
    reserved_microusd: Microusd,
) -> Microusd:
    """Microusd of the finite trial allowance still unspent (never negative)."""
    for name, value in (
        ("budget_microusd", budget_microusd),
        ("consumed_microusd", consumed_microusd),
        ("reserved_microusd", reserved_microusd),
    ):
        require_microusd(value, field=name)
    if consumed_microusd + reserved_microusd > budget_microusd:
        raise PolicyError("trial consumed + reserved exceeds the trial budget")
    return budget_microusd - consumed_microusd - reserved_microusd


def trial_extended_remaining(
    *,
    granted: int,
    used: int,
    reserved: int,
) -> int:
    """Lifetime trial extended-agent count still unspent (never negative)."""
    for name, value in (("granted", granted), ("used", used), ("reserved", reserved)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PolicyError(f"trial extended agent {name} must be a non-negative integer")
    if used + reserved > granted:
        raise PolicyError("trial extended agents used + reserved exceeds the granted count")
    return granted - used - reserved


__all__ = [
    "ADMITTED",
    "Admission",
    "AdmissionContext",
    "DenialCode",
    "PeriodState",
    "RATE_WINDOW_SECONDS",
    "ReservationRequest",
    "admit",
    "apply_reservation",
    "effective_rate_requests",
    "next_period_key",
    "period_key",
    "release_state",
    "settle_state",
    "trial_extended_remaining",
    "trial_remaining",
]
