"""The entitlement service: the stable API other subsystems call.

Contract (see the package docstring for the full list)::

    service = EntitlementService(pool)                  # pool: asyncpg.Pool
    policy  = service.policy                            # CommercialPolicy
    resolved = await service.resolve(user_id)           # ResolvedPolicy
    snapshot = await service.public_snapshot(user_id)   # JSON-safe dict
    catalog  = service.public_catalog()                 # displayed plans/prices
    reservation = await service.reserve(
        user_id, amount_microusd, operation="chat", premium=False, extended=False
    )
    settlement = await service.settle(reservation, actual_microusd, usage={...})
    released   = await service.release(reservation)
    result = await service.apply_subscription_event(SubscriptionEvent(...))
    result = await service.import_legacy_tier(user_id=..., legacy_tier="max", event_id=...)

``user_id`` is a ``uuid.UUID`` (UUID-shaped strings are accepted too). Amounts
are integer **microusd** — 1 USD = 1_000_000 microusd — and must be
non-negative. No float money ever enters the ledger.

There is no billing-provider integration and no HTTP surface: a plan changes
only through :meth:`apply_subscription_event` (or the explicit per-user
:meth:`import_legacy_tier`) with an allowlisted source, and both are idempotent
per ``event_id``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import asyncpg

from orchestrator.entitlements.errors import (
    AccountSuspended,
    BudgetExceeded,
    CapabilityDenied,
    ConcurrencyExceeded,
    EntitlementsError,
    ExtendedBudgetExceeded,
    ExtendedRunExceeded,
    InvalidReservationAmount,
    InvalidSubscriptionEvent,
    LimitExceeded,
    RateLimitExceeded,
    ReservationNotFound,
    SettlementConflict,
    SubscriptionEventConflict,
    TrialExhausted,
    TrialExtendedAgentsExhausted,
    UnknownAccount,
    UnknownOperation,
    UntrustedSubscriptionSource,
)
from orchestrator.entitlements.ledger import (
    Admission,
    AdmissionContext,
    PeriodState,
    ReservationRequest,
    admit,
    period_key,
)
from orchestrator.entitlements.models import (
    AccountRecord,
    Reservation,
    ResolvedPolicy,
    Settlement,
    SubscriptionEvent,
    SubscriptionResult,
    coerce_user_id,
    validate_usage,
)
from orchestrator.entitlements.plans import (
    AccountStatus,
    Capability,
    ChargeKind,
    Plan,
    ReservationStatus,
)
from orchestrator.entitlements.policy import CommercialPolicy, load_policy
from orchestrator.entitlements.resolver import resolve_account_policy
from orchestrator.entitlements.store import Connection, EntitlementStore

#: Default source for the explicit legacy-tier import helper.
LEGACY_IMPORT_SOURCE = "legacy_import"

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_json_mapping(value: Any) -> dict[str, Any]:
    """asyncpg returns ``jsonb`` as text unless a codec is installed."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SubscriptionEventConflict("unreadable event payload") from exc
        return dict(decoded) if isinstance(decoded, dict) else {}
    return dict(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_amount(amount: object, *, field: str) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InvalidReservationAmount(
            f"{field} must be an integer microusd amount, got {type(amount).__name__}"
        )
    if amount < 0:
        raise InvalidReservationAmount(f"{field} must not be negative, got {amount}")
    return amount


def _reservation_id(reservation: Reservation | uuid.UUID | str) -> uuid.UUID:
    if isinstance(reservation, Reservation):
        return reservation.id
    if isinstance(reservation, uuid.UUID):
        return reservation
    if isinstance(reservation, str):
        try:
            return uuid.UUID(reservation.strip())
        except ValueError as exc:
            raise ReservationNotFound(reservation) from exc
    raise ReservationNotFound(str(reservation))


def _reservation_from_row(row: Mapping[str, Any]) -> Reservation:
    return Reservation(
        id=row["id"],
        user_id=row["user_id"],
        period_key=row["period_key"],
        plan=Plan(row["plan"]),
        operation=row["operation"],
        charge_kind=ChargeKind(row["charge_kind"]),
        premium=bool(row["premium"]),
        extended=bool(row["extended"]),
        reserved_microusd=int(row["reserved_microusd"]),
        status=ReservationStatus(row["status"]),
        overage_microusd=int(row["overage_microusd"]),
        created_at=row["created_at"],
    )


def _limit_error(
    denial: str,
    *,
    state: PeriodState,
    request: ReservationRequest,
    context: AdmissionContext,
) -> EntitlementsError:
    """Map a denial code onto the matching typed error."""
    if denial == "rate_limited":
        return RateLimitExceeded(
            requests_in_window=state.requests_in_window,
            ceiling=context.limits.requests_per_minute,
        )
    if denial == "concurrency_exceeded":
        return ConcurrencyExceeded(
            open_reservations=state.open_reservations,
            ceiling=context.limits.max_concurrent_operations,
        )
    if denial == "extended_agents_exceeded":
        return ExtendedRunExceeded(
            used=state.extended_agents_used,
            reserved=state.extended_agents_reserved,
            ceiling=context.limits.extended_agents_per_period,
        )
    if denial == "extended_budget_exceeded":
        return ExtendedBudgetExceeded(
            requested=request.amount_microusd,
            spent=state.extended_spent_microusd,
            reserved=state.extended_reserved_microusd,
            ceiling=context.limits.extended_agent_budget_microusd,
        )
    if denial == "trial_extended_agents_exhausted":
        return TrialExtendedAgentsExhausted(remaining=context.trial_extended_agents_remaining)
    if denial == "trial_exhausted":
        return TrialExhausted(
            f"trial allowance exhausted: requested {request.amount_microusd} of "
            f"ceiling {context.budget_ceiling_microusd}"
        )
    return BudgetExceeded(
        requested=request.amount_microusd,
        spent=state.spent_microusd,
        reserved=state.reserved_microusd,
        ceiling=context.budget_ceiling_microusd,
    )


class EntitlementService:
    """Resolve, charge and audit one account's commercial entitlements."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        policy: CommercialPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._pool = pool
        self._policy = policy or load_policy()
        self._store = EntitlementStore(pool)
        self._clock: Clock = clock or _utcnow

    @property
    def policy(self) -> CommercialPolicy:
        """The validated commercial policy this service enforces."""
        return self._policy

    @property
    def store(self) -> EntitlementStore:
        return self._store

    def now(self) -> datetime:
        return self._clock()

    def current_period_key(self) -> str:
        """The UTC calendar-month key the current time falls in."""
        return period_key(self.now())

    def public_catalog(self) -> list[dict[str, Any]]:
        """Plan catalog for display: labels and configured prices only."""
        return self._policy.public_catalog()

    # ---------------------------------------------------------------- resolve
    async def resolve(self, user_id: uuid.UUID | str) -> ResolvedPolicy:
        """Resolve the account's current plan, capabilities, limits and trial.

        Idempotent and safe on a first-touch account: the row is created with
        ``plan='free'`` and a finite trial allowance. The legacy global tier
        default is never consulted.
        """
        uid = coerce_user_id(user_id)
        now = self.now()
        period = period_key(now)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                record = await self._ensure_account(conn, uid)
                state = await self._store.read_period_state(conn, user_id=uid, period_key=period)
        return resolve_account_policy(
            record,
            self._policy,
            period=period,
            period_spent_microusd=state.spent_microusd,
            period_reserved_microusd=state.reserved_microusd,
        )

    async def public_snapshot(self, user_id: uuid.UUID | str) -> dict[str, Any]:
        """A JSON-safe view for clients.

        Shape::

            {
              "plan": "free" | "pro" | "power",
              "capabilities": ["chat", ...],
              "trial": {"state": "active" | "exhausted", ...},
              "limits": {...}
            }

        Contains integer microusd budgets so a client can render an allowance,
        and no per-unit price, provider, model or route name.
        """
        resolved = await self.resolve(user_id)
        return {
            "plan": resolved.plan.value,
            "capabilities": sorted(capability.value for capability in resolved.capabilities),
            "trial": resolved.trial.as_dict(),
            "limits": resolved.limits_dict(),
        }

    # ----------------------------------------------------------------- charge
    async def reserve(
        self,
        user_id: uuid.UUID | str,
        amount_microusd: int,
        *,
        operation: str,
        premium: bool = False,
        extended: bool = False,
        provider: str | None = None,
        model: str | None = None,
        route_id: str | None = None,
        extended_run: bool | None = None,
        background: bool = False,
        scope_id: uuid.UUID | None = None,
    ) -> Reservation:
        """Hold ``amount_microusd`` for one in-flight operation.

        The hold is admitted only if every ceiling still holds at commit time:
        remaining budget, extended-run allowance, rate window and concurrency.
        ``premium`` requires premium routing. ``extended`` charges the extended
        budget; ``extended_run`` (default: same as ``extended``) also takes an
        extended-run slot and requires extended agents, so every call of one
        extended run is charged while only its first counts as a run.

        ``scope_id`` groups the calls of one operation: while any of them is
        open, the others share its concurrency slot. ``background`` work is
        charged to the budget but takes no rate or concurrency slot.

        ``provider``/``model``/``route_id`` are optional unit-economics labels
        recorded with the reservation. They are never interpreted here: which
        routes are usable at all is the inference policy's decision.

        A refused operation raises a
        :class:`~orchestrator.entitlements.errors.LimitExceeded` or
        :class:`~orchestrator.entitlements.errors.CapabilityDenied` and is
        recorded as a limit encounter for plan tuning.
        """
        uid = coerce_user_id(user_id)
        amount = _require_amount(amount_microusd, field="amount_microusd")
        if operation not in self._policy.operations:
            raise UnknownOperation(operation)
        now = self.now()
        period = period_key(now)

        try:
            return await self._reserve(
                uid,
                amount,
                operation=operation,
                premium=premium,
                extended=extended,
                extended_run=extended if extended_run is None else extended_run,
                background=background,
                scope_id=scope_id,
                provider=provider,
                model=model,
                route_id=route_id,
                now=now,
                period=period,
            )
        except (LimitExceeded, CapabilityDenied) as exc:
            await self._record_encounter(
                uid,
                period=period,
                operation=operation,
                requested_microusd=amount,
                error=exc,
            )
            raise

    async def _reserve(
        self,
        uid: uuid.UUID,
        amount: int,
        *,
        operation: str,
        premium: bool,
        extended: bool,
        extended_run: bool,
        background: bool,
        scope_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
        route_id: str | None,
        now: datetime,
        period: str,
    ) -> Reservation:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_account(conn, uid)
                record = await self._store.lock_account(conn, uid)
                if record is None:  # pragma: no cover - locked row just ensured
                    raise UnknownAccount(str(uid))
                resolved = resolve_account_policy(record, self._policy, period=period)
                self._require_usable(
                    record, resolved=resolved, premium=premium, extended=extended_run
                )
                kind = resolved.charge_kind_for(premium)
                limits = resolved.limits_for(premium)
                context = AdmissionContext(
                    limits=limits,
                    budget_ceiling_microusd=resolved.budget_ceiling_for(premium),
                    charge_kind=kind,
                    now=now,
                    trial_extended_agents_remaining=resolved.trial.extended_agents_remaining,
                )
                request = ReservationRequest(
                    amount_microusd=amount,
                    extended=extended,
                    charge_kind=kind,
                    extended_run=extended_run,
                    background=background,
                    joins_open_operation=await self._store.operation_is_open(
                        conn, user_id=uid, scope_id=scope_id
                    ),
                )

                state = await self._store.read_period_state(conn, user_id=uid, period_key=period)
                early = admit(state, request, context)
                if not early.admitted:
                    raise _limit_error(
                        early.denial_code or "budget_exceeded",
                        state=state,
                        request=request,
                        context=context,
                    )

                await self._store.ensure_period(conn, user_id=uid, period_key=period)
                held = await self._store.hold(
                    conn,
                    user_id=uid,
                    period_key=period,
                    amount=amount,
                    extended=extended,
                    now=now,
                    budget_ceiling=resolved.budget_ceiling_for(premium),
                    extended_budget_ceiling=limits.extended_agent_budget_microusd,
                    rate_limit=limits.requests_per_minute,
                    concurrency_limit=limits.max_concurrent_operations,
                    extended_run_limit=limits.extended_agents_per_period,
                    money_applies=context.period_owns_money(),
                    extended_run=extended_run,
                    background=background,
                    scope_id=scope_id,
                )
                if held is None:
                    # Lost a race: re-read and report the reason the SQL used.
                    state = await self._store.read_period_state(
                        conn, user_id=uid, period_key=period
                    )
                    admission: Admission = admit(state, request, context)
                    raise _limit_error(
                        admission.denial_code or "budget_exceeded",
                        state=state,
                        request=request,
                        context=context,
                    )

                row = await self._store.insert_reservation(
                    conn,
                    user_id=uid,
                    period_key=period,
                    plan=resolved.plan,
                    operation=operation,
                    charge_kind=kind,
                    premium=premium,
                    extended=extended,
                    reserved=amount,
                    now=now,
                    provider=provider,
                    model=model,
                    route_id=route_id,
                    extended_run=extended_run,
                    background=background,
                    scope_id=scope_id,
                )
                if kind is ChargeKind.TRIAL:
                    await self._store.hold_trial(
                        conn, user_id=uid, amount=amount, extended=extended_run, now=now
                    )
                return _reservation_from_row(row)

    async def settle(
        self,
        reservation: Reservation | uuid.UUID | str,
        actual_microusd: int,
        *,
        usage: Mapping[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        route_id: str | None = None,
    ) -> Settlement:
        return await self._finish(
            reservation,
            actual_microusd,
            usage=usage,
            provider=provider,
            model=model,
            route_id=route_id,
            cancelled=False,
        )

    async def _finish(
        self,
        reservation: Reservation | uuid.UUID | str,
        actual_microusd: int,
        *,
        usage: Mapping[str, Any] | None,
        provider: str | None = None,
        model: str | None = None,
        route_id: str | None = None,
        cancelled: bool,
    ) -> Settlement:
        """Record the real cost of a reservation and release the hold.

        Idempotent: settling an already-settled reservation returns the stored
        outcome with ``applied=False`` and never charges twice.
        """
        rid = _reservation_id(reservation)
        actual = _require_amount(actual_microusd, field="actual_microusd")
        metadata = validate_usage(usage)
        now = self.now()
        labels = {
            "provider": provider or metadata.get("provider"),
            "model": model or metadata.get("model"),
            "route_id": route_id or metadata.get("route_id"),
        }

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await self._store.get_reservation(conn, rid)
                if existing is None:
                    raise ReservationNotFound(str(rid))
                account = await self._store.lock_account(conn, existing["user_id"])
                assert account is not None
                current = await self._store.get_reservation(conn, rid, for_update=True)
                if current is None:  # pragma: no cover - row read a moment ago
                    raise ReservationNotFound(str(rid))
                if ReservationStatus(current["status"]) is not ReservationStatus.OPEN:
                    if usage is not None and _as_json_mapping(current["usage"]) != metadata:
                        raise SettlementConflict(str(rid), int(current["actual_microusd"]), actual)
                    return self._replayed_settlement(current, actual, cancelled)

                kind = ChargeKind(current["charge_kind"])
                period_state = await self._store.read_period_state(
                    conn, user_id=current["user_id"], period_key=current["period_key"]
                )
                # Admission uses the current plan's ceiling, but settlement must
                # honor all already-funded holds across upgrades/downgrades and
                # configuration changes. Using this reservation's older, smaller
                # plan here could silently discard spend after an upgrade.
                ceiling = max(
                    self._policy.plan(account.plan).limits.monthly_budget_microusd,
                    period_state.spent_microusd + period_state.reserved_microusd,
                )
                available = (
                    max(
                        0,
                        ceiling
                        - period_state.spent_microusd
                        - period_state.reserved_microusd
                        + int(current["reserved_microusd"]),
                    )
                    if kind is ChargeKind.PLAN
                    else max(
                        0,
                        account.trial_budget_microusd
                        - account.trial_consumed_microusd
                        - account.trial_reserved_microusd
                        + int(current["reserved_microusd"]),
                    )
                )
                overage = max(0, actual - available)

                closed = await self._store.mark_reservation(
                    conn,
                    reservation_id=rid,
                    status=ReservationStatus.RELEASED if cancelled else ReservationStatus.SETTLED,
                    actual=actual,
                    overage=overage,
                    usage=metadata,
                    now=now,
                    provider=_as_optional_str(labels["provider"]),
                    model=_as_optional_str(labels["model"]),
                    route_id=_as_optional_str(labels["route_id"]),
                )
                if closed is None:  # pragma: no cover - row is locked and open
                    current = await self._store.get_reservation(conn, rid, for_update=True)
                    assert current is not None
                    return self._replayed_settlement(current, actual, cancelled)

                await self._store.settle_period(
                    conn,
                    user_id=current["user_id"],
                    period_key=current["period_key"],
                    actual=actual,
                    reserved=int(current["reserved_microusd"]),
                    extended=bool(current["extended"]),
                    now=now,
                    ceiling=(
                        period_state.spent_microusd + available
                        if kind is ChargeKind.PLAN
                        else ceiling
                    ),
                    period_money=kind is ChargeKind.PLAN,
                    consumed=not cancelled,
                    extended_run=bool(current["extended_run"]),
                )
                if kind is ChargeKind.TRIAL:
                    await self._store.settle_trial(
                        conn,
                        user_id=current["user_id"],
                        reserved=int(current["reserved_microusd"]),
                        actual=actual,
                        extended=bool(current["extended_run"]),
                        consumed=not cancelled,
                        now=now,
                    )
                if overage or actual > int(current["reserved_microusd"]):
                    # A quote breach invalidates the execution bound even when
                    # this account happens to have spare recurring allowance.
                    await conn.execute(
                        "UPDATE entitlement_accounts SET status = 'suspended', updated_at = $2 WHERE user_id = $1",
                        current["user_id"],
                        now,
                    )
                reservation_value = _reservation_from_row(closed)

        return Settlement(
            reservation=reservation_value,
            applied=True,
            actual_microusd=actual,
            released_microusd=max(0, reservation_value.reserved_microusd - actual),
            period_key=reservation_value.period_key,
            status=ReservationStatus.RELEASED if cancelled else ReservationStatus.SETTLED,
            overage_microusd=overage,
        )

    async def release(
        self,
        reservation: Reservation | uuid.UUID | str,
        *,
        usage: Mapping[str, Any] | None = None,
    ) -> Settlement:
        """Abandon a reservation at zero cost, returning the whole hold."""
        return await self._finish(reservation, 0, usage=usage, cancelled=True)

    async def reconcile_expired_reservations(
        self, user_id: uuid.UUID | str, *, before: datetime
    ) -> int:
        """Conservatively close holds whose enforced execution deadline passed.

        The execution layer must enforce a whole-call deadline (including stream
        consumption) shorter than the supplied age cutoff. Recovery never refunds
        unknown provider work: it charges the original full reservation. This
        also recovers a process killed before its normal scope-finally cleanup.
        """
        uid = coerce_user_id(user_id)
        if before.tzinfo is None or before.utcoffset() is None or before > self.now():
            raise ValueError("recovery cutoff must be an aware past timestamp")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, reserved_microusd FROM entitlement_reservations
                   WHERE user_id = $1 AND status = 'open' AND created_at < $2
                   ORDER BY created_at""",
                uid,
                before,
            )
        recovered = 0
        for row in rows:
            try:
                result = await self.settle(
                    row["id"],
                    int(row["reserved_microusd"]),
                    usage={"estimated_cost": True, "recovered_after_timeout": True},
                )
            except (SettlementConflict, ReservationNotFound):
                # A live finalizer or another recovery process closed it after
                # our read, or account deletion removed it. Never charge twice.
                continue
            recovered += int(result.applied)
        return recovered

    # ----------------------------------------------------------------- events
    async def apply_subscription_event(self, event: SubscriptionEvent) -> SubscriptionResult:
        """Apply a trusted plan change. Idempotent per ``event_id``.

        There is no public or unauthenticated route to this method. The source
        must be in ``trusted_subscription_sources``, which never includes a
        global default such as ``DEFAULT_TIER``.
        """
        if not isinstance(event.event_id, str) or not event.event_id.strip():
            raise InvalidSubscriptionEvent("event_id must be a non-empty string")
        source = event.source.strip().lower()
        if not self._policy.is_trusted_source(source):
            raise UntrustedSubscriptionSource(event.source)
        if not isinstance(event.plan, Plan):
            raise InvalidSubscriptionEvent("plan must be a CommercialPlan member")

        uid = coerce_user_id(event.user_id)
        now = self.now()
        target = event.plan if event.active else Plan.FREE

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_account(conn, uid)
                record = await self._store.lock_account(conn, uid)
                if record is None:  # pragma: no cover - locked row just ensured
                    raise UnknownAccount(str(uid))

                existing = await self._store.get_event(conn, event.event_id.strip())
                if existing is not None:
                    return await self._replayed_event(conn, event, record)
                newest = await self._store.newest_event_time(conn, uid)
                effective_time = event.occurred_at or now
                if newest is not None and newest > effective_time:
                    return SubscriptionResult(
                        user_id=uid,
                        plan=record.plan,
                        previous_plan=record.plan,
                        applied=False,
                        duplicate=False,
                        byok_enabled=record.byok_enabled,
                        plan_source=record.plan_source,
                    )

                inserted = await self._store.insert_event(
                    conn,
                    event_id=event.event_id.strip(),
                    user_id=uid,
                    source=source,
                    plan=target,
                    active=event.active,
                    byok=event.byok,
                    previous_plan=record.plan,
                    payload=event.payload_fingerprint(),
                    occurred_at=event.occurred_at or now,
                    metadata=event.payload_fingerprint()["metadata"],
                )
                if inserted is None:
                    return await self._replayed_event(conn, event, record)

                updated = await self._store.set_plan(
                    conn,
                    user_id=uid,
                    plan=target,
                    plan_source=source,
                    byok=event.byok,
                    now=now,
                )
                result = SubscriptionResult(
                    user_id=uid,
                    plan=updated.plan,
                    previous_plan=record.plan,
                    applied=True,
                    duplicate=False,
                    byok_enabled=updated.byok_enabled,
                    plan_source=updated.plan_source,
                )

        return result

    async def import_legacy_tier(
        self,
        *,
        user_id: uuid.UUID | str,
        legacy_tier: str,
        event_id: str,
        source: str = LEGACY_IMPORT_SOURCE,
        byok: bool = False,
    ) -> SubscriptionResult:
        """Migrate one account from a retired tier name, explicitly.

        The mapping is deterministic: ``starter -> pro``, ``max -> power``,
        ``byok -> pro``. A legacy ``byok`` tier does **not** grant the BYOK
        capability: pass ``byok=True`` only when a trusted import says the
        account really does pay its own provider. Nothing here runs in bulk and
        nothing infers state from the global ``DEFAULT_TIER``.
        """
        uid = coerce_user_id(user_id)
        target = self._policy.legacy_target(legacy_tier)
        event = SubscriptionEvent(
            event_id=event_id,
            user_id=uid,
            plan=target,
            source=source,
            active=target is not Plan.FREE,
            byok=byok,
            metadata={"legacy_tier": legacy_tier.strip().lower()},
        )
        return await self.apply_subscription_event(event)

    async def reinstate(self, user_id: uuid.UUID | str, *, reason: str) -> bool:
        """Operator reconciliation: return a suspended account to ``active``.

        Settling above a reservation's quote suspends the account (see
        :meth:`settle`) until an operator has reconciled the broken quote. This
        is that operator step; it is never reachable from a request path.
        Returns whether the status changed.
        """
        uid = coerce_user_id(user_id)
        note = reason.strip() if isinstance(reason, str) else ""
        if not note:
            raise ValueError("a reconciliation reason is required")
        now = self.now()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                record = await self._store.lock_account(conn, uid)
                if record is None:
                    raise UnknownAccount(str(uid))
                if record.status is AccountStatus.ACTIVE:
                    return False
                await self._store.set_status(
                    conn, user_id=uid, status=AccountStatus.ACTIVE, now=now
                )
        logger.warning("Entitlement account %s reinstated by operator: %s", uid, note)
        return True

    # ---------------------------------------------------------------- helpers
    async def _record_encounter(
        self,
        uid: uuid.UUID,
        *,
        period: str,
        operation: str,
        requested_microusd: int,
        error: LimitExceeded | CapabilityDenied,
    ) -> None:
        """Record a refused operation outside the failed transaction.

        A denial rolls its transaction back, so the encounter is written in its
        own transaction. Failures here are logged and swallowed: analytics must
        never turn a correct refusal into a different error.
        """
        if isinstance(error, CapabilityDenied):
            denial_code = "capability_denied"
            capability: str | None = error.capability
        else:
            denial_code = error.code
            capability = None
        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    record = await self._store.lock_account(conn, uid)
                    if record is None:
                        return
                    resolved = resolve_account_policy(record, self._policy, period=period)
                    state = await self._store.read_period_state(
                        conn, user_id=uid, period_key=period
                    )
                    await self._store.insert_encounter(
                        conn,
                        user_id=uid,
                        period_key=period,
                        plan=resolved.plan,
                        denial_code=denial_code,
                        operation=operation,
                        capability=capability,
                        requested_microusd=requested_microusd,
                        limits_snapshot=resolved.limits.as_dict(),
                        observed=state.as_dict(),
                    )
        except Exception:
            logger.warning(
                "failed to record entitlement limit encounter for user %s", uid, exc_info=True
            )

    async def _ensure_account(self, conn: Connection, uid: uuid.UUID) -> AccountRecord:
        try:
            return await self._store.ensure_account(
                conn,
                uid,
                trial_budget_microusd=self._policy.trial.budget_for_new_account(),
                trial_extended_agents=self._policy.trial.extended_agents_for_new_account(),
            )
        except asyncpg.ForeignKeyViolationError as exc:
            raise UnknownAccount(str(uid)) from exc

    def _require_usable(
        self, record: AccountRecord, *, resolved: ResolvedPolicy, premium: bool, extended: bool
    ) -> None:
        if record.status is not AccountStatus.ACTIVE:
            raise AccountSuspended(
                f"account {record.user_id} is {record.status.value}; no operation is admitted"
            )
        if premium:
            resolved.require(Capability.PREMIUM_ROUTING)
        if extended:
            resolved.require(Capability.EXTENDED_AGENTS)

    def _replayed_settlement(
        self, row: Mapping[str, Any], actual_offered: int, cancelled: bool
    ) -> Settlement:
        stored_actual = row["actual_microusd"]
        actual = int(stored_actual) if stored_actual is not None else 0
        if (
            actual != actual_offered
            or (row["status"] == ReservationStatus.RELEASED.value) != cancelled
        ):
            raise SettlementConflict(str(row["id"]), actual, actual_offered)
        reservation = _reservation_from_row(row)
        return Settlement(
            reservation=reservation,
            applied=False,
            actual_microusd=actual,
            released_microusd=0,
            period_key=reservation.period_key,
            status=reservation.status,
            overage_microusd=reservation.overage_microusd,
        )

    async def _replayed_event(
        self,
        conn: Connection,
        event: SubscriptionEvent,
        record: AccountRecord,
    ) -> SubscriptionResult:
        existing = await self._store.get_event(conn, event.event_id.strip())
        if existing is None:  # pragma: no cover - ON CONFLICT implies the row exists
            raise SubscriptionEventConflict(event.event_id)
        stored = _as_json_mapping(existing["payload"])
        if stored != dict(event.payload_fingerprint()):
            raise SubscriptionEventConflict(event.event_id)
        stored_plan = existing["plan"]
        stored_previous = existing["previous_plan"]
        return SubscriptionResult(
            user_id=record.user_id,
            plan=Plan(stored_plan) if stored_plan else record.plan,
            previous_plan=Plan(stored_previous) if stored_previous else record.plan,
            applied=False,
            duplicate=True,
            byok_enabled=record.byok_enabled,
            plan_source=record.plan_source,
        )


__all__ = ["EntitlementService", "LEGACY_IMPORT_SOURCE"]
