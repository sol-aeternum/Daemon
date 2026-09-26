"""asyncpg persistence for entitlements.

All admission and settlement SQL lives here. Two invariants make it atomic:

1. ``entitlement_accounts`` row for the user is locked with ``FOR UPDATE`` at
   the start of every reserve and settle transaction. That row is the
   per-user serialization point, so two concurrent operations for the same
   account cannot both observe the same counters.
2. The hold itself is a single guarded ``UPDATE ... WHERE`` that re-checks the
   budget, extended-run, rate-window and concurrency ceilings in the same
   statement. If it updates zero rows the operation is refused: there is no
   read-then-write window in which a ceiling could be passed.

The predicates in the ``WHERE`` clause mirror
:func:`orchestrator.entitlements.ledger.admit` one-for-one. The service calls
:func:`admit` before the write for a fast, specific denial, and again after a
refused write to report the exact reason, so the SQL and the pure rules stay
in step.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Final, Mapping, TypeAlias

import asyncpg
import asyncpg.pool

from orchestrator.entitlements.ledger import RATE_WINDOW_SECONDS, PeriodState
from orchestrator.entitlements.models import AccountRecord
from orchestrator.entitlements.plans import (
    AccountStatus,
    ChargeKind,
    Plan,
    ReservationStatus,
    TrialState,
)

#: A pooled connection. ``pool.acquire()`` yields a proxy rather than a raw
#: ``Connection``; the query methods are identical either way.
Connection: TypeAlias = asyncpg.pool.PoolConnectionProxy

_ACCOUNT_COLUMNS: Final[str] = """
    user_id, plan, plan_source, status, byok_enabled, trial_state,
    trial_budget_microusd, trial_consumed_microusd, trial_reserved_microusd,
    trial_extended_agents, trial_extended_agents_used, trial_extended_agents_reserved,
    plan_changed_at, created_at, updated_at
"""

_RESERVATION_COLUMNS: Final[str] = """
    id, user_id, period_key, plan, operation, charge_kind, premium, extended,
    extended_run, background, scope_id, status, reserved_microusd, actual_microusd, overage_microusd, provider, model, route_id,
    usage, created_at, settled_at
"""

_PERIOD_STATE_SQL: Final[str] = """
    SELECT
        p.spent_microusd,
        p.reserved_microusd,
        p.extended_spent_microusd,
        p.extended_reserved_microusd,
        p.extended_agents_used,
        p.extended_agents_reserved,
        p.requests_in_window,
        p.window_started_at,
        (
            SELECT count(DISTINCT COALESCE(r.scope_id, r.id)) FROM entitlement_reservations r
            WHERE r.user_id = a.user_id AND r.status = 'open' AND NOT r.background
        ) AS open_reservations
    FROM entitlement_accounts a
    LEFT JOIN entitlement_usage_periods p ON p.user_id = a.user_id AND p.period_key = $2
    WHERE a.user_id = $1
"""

# Single guarded statement: place the hold only if every ceiling still holds.
# $11 is period_money: only plan-funded work is accounted in the period row, so
# a trial-funded hold occupies a concurrency/rate slot without taking money that
# the account's lifetime counters already own.
# $4 charges the extended budget; $13 additionally takes an extended-run slot.
# $14 marks background work: charged, but it takes no rate or concurrency slot.
# $15 is the account scope: a scope with an open reservation already holds its
# concurrency slot, so its later calls (tool loops, council roles) share it.
_HOLD_SQL: Final[str] = """
    UPDATE entitlement_usage_periods AS p
    SET reserved_microusd = p.reserved_microusd
            + CASE WHEN $11::boolean THEN $3 ELSE 0 END,
        extended_reserved_microusd = p.extended_reserved_microusd
            + CASE WHEN $11::boolean AND $4::boolean THEN $3 ELSE 0 END,
        extended_agents_reserved = p.extended_agents_reserved
            + CASE WHEN $11::boolean AND $13::boolean THEN 1 ELSE 0 END,
        requests_in_window = CASE
            WHEN $14::boolean THEN p.requests_in_window
            WHEN p.window_started_at IS NULL
                 OR $5 - p.window_started_at >= $12::interval
            THEN 1
            ELSE p.requests_in_window + 1
        END,
        window_started_at = CASE
            WHEN $14::boolean THEN p.window_started_at
            WHEN p.window_started_at IS NULL
                 OR $5 - p.window_started_at >= $12::interval
            THEN $5
            ELSE p.window_started_at
        END,
        updated_at = $5
    WHERE p.user_id = $1
      AND p.period_key = $2
      AND (
            $3 = 0 OR NOT $11::boolean
            OR (p.spent_microusd + p.reserved_microusd + $3) <= $6
      )
      AND (
            NOT ($11::boolean AND $4::boolean)
            OR (p.extended_spent_microusd + p.extended_reserved_microusd + $3) <= $7
      )
      AND (
            NOT ($11::boolean AND $13::boolean)
            OR (p.extended_agents_used + p.extended_agents_reserved + 1) <= $10
      )
      AND (
            $14::boolean
            OR p.window_started_at IS NULL
            OR $5 - p.window_started_at >= $12::interval
            OR p.requests_in_window < $8
      )
      AND (
            $14::boolean
            OR EXISTS (
                SELECT 1 FROM entitlement_reservations r
                WHERE r.user_id = p.user_id AND r.status = 'open'
                  AND NOT r.background AND r.scope_id = $15::uuid
            )
            OR (
                SELECT count(DISTINCT COALESCE(r.scope_id, r.id))
                FROM entitlement_reservations r
                WHERE r.user_id = p.user_id AND r.status = 'open' AND NOT r.background
            ) < $9
      )
    RETURNING
        p.spent_microusd,
        p.reserved_microusd,
        p.extended_spent_microusd,
        p.extended_reserved_microusd,
        p.extended_agents_used,
        p.extended_agents_reserved,
        p.requests_in_window,
        p.window_started_at
"""

# Release the hold and record settled spend for the reservation's own period.
# Mirrors ledger.settle_state:
#   - a completed request remains counted in its fixed rate window;
#   - the reserved counters are decremented without GREATEST, so a
#     release-without-hold fails loudly instead of silently clamping;
#   - money stops at the ceiling ($7) and the excess is the caller's overage.
_SETTLE_PERIOD_SQL: Final[str] = """
    UPDATE entitlement_usage_periods AS p
    SET spent_microusd = p.spent_microusd
            + CASE WHEN $8::boolean
                   THEN LEAST($3, GREATEST($7 - p.spent_microusd, 0))
                   ELSE 0 END,
        reserved_microusd = p.reserved_microusd
            - CASE WHEN $8::boolean THEN $4 ELSE 0 END,
        extended_spent_microusd = p.extended_spent_microusd
            + CASE WHEN $8::boolean AND $5::boolean
                   THEN LEAST($3, GREATEST($7 - p.spent_microusd, 0))
                   ELSE 0 END,
        extended_reserved_microusd = p.extended_reserved_microusd
            - CASE WHEN $8::boolean AND $5::boolean THEN $4 ELSE 0 END,
        extended_agents_used = p.extended_agents_used
            + CASE WHEN $8::boolean AND $10::boolean AND $9::boolean THEN 1 ELSE 0 END,
        extended_agents_reserved = p.extended_agents_reserved
            - CASE WHEN $8::boolean AND $10::boolean THEN 1 ELSE 0 END,
        updated_at = $6
    WHERE p.user_id = $1 AND p.period_key = $2
      AND (NOT $8::boolean OR p.reserved_microusd >= $4)
      AND (NOT ($8::boolean AND $5::boolean) OR p.extended_reserved_microusd >= $4)
      AND (NOT ($8::boolean AND $10::boolean) OR p.extended_agents_reserved >= 1)
    RETURNING p.spent_microusd, p.reserved_microusd
"""

# Guarded trial hold: the account's lifetime counters own trial money, and the
# hold must not flip the stored state. A reservation that is released or
# underspends has to give the allowance back, and a stored "exhausted" that
# outlived the money it referred to would be a lie.
_HOLD_TRIAL_SQL: Final[str] = """
    UPDATE entitlement_accounts
    SET trial_reserved_microusd = trial_reserved_microusd + $2,
        trial_extended_agents_reserved = trial_extended_agents_reserved
            + CASE WHEN $3::boolean THEN 1 ELSE 0 END,
        updated_at = $4
    WHERE user_id = $1
      AND trial_budget_microusd - trial_consumed_microusd - trial_reserved_microusd >= $2
      AND (
            NOT $3::boolean
            OR trial_extended_agents - trial_extended_agents_used
                 - trial_extended_agents_reserved >= 1
      )
    RETURNING trial_reserved_microusd, trial_extended_agents_reserved
"""

# Settle a trial hold: release the hold, consume what was really spent, capped at
# the remaining allowance so the account CHECK can never be violated, and
# recompute the stored state from the resulting counters in both directions.
_SETTLE_TRIAL_SQL: Final[str] = """
    UPDATE entitlement_accounts
    SET trial_reserved_microusd = trial_reserved_microusd - $2,
        trial_consumed_microusd = trial_consumed_microusd
            + LEAST($3, GREATEST(trial_budget_microusd - trial_consumed_microusd
                                   - trial_reserved_microusd + $2, 0)),
        trial_extended_agents_used = trial_extended_agents_used
            + CASE WHEN $4::boolean THEN 1 ELSE 0 END,
        trial_extended_agents_reserved = trial_extended_agents_reserved
            - CASE WHEN $6::boolean THEN 1 ELSE 0 END,
        trial_state = CASE
            WHEN trial_budget_microusd - trial_consumed_microusd
                 - LEAST($3, GREATEST(trial_budget_microusd - trial_consumed_microusd
                                      - trial_reserved_microusd + $2, 0))
                 - trial_reserved_microusd + $2 > 0
            THEN 'active'
            ELSE 'exhausted'
        END,
        updated_at = $5
    WHERE user_id = $1
      AND trial_reserved_microusd >= $2
      AND (NOT $6::boolean OR trial_extended_agents_reserved >= 1)
    RETURNING trial_consumed_microusd, trial_reserved_microusd, trial_state
"""


def _account_from_row(row: Mapping[str, Any]) -> AccountRecord:
    return AccountRecord(
        user_id=row["user_id"],
        plan=Plan(row["plan"]),
        plan_source=row["plan_source"],
        status=AccountStatus(row["status"]),
        byok_enabled=bool(row["byok_enabled"]),
        trial_state=TrialState(row["trial_state"]),
        trial_budget_microusd=int(row["trial_budget_microusd"]),
        trial_consumed_microusd=int(row["trial_consumed_microusd"]),
        trial_reserved_microusd=int(row["trial_reserved_microusd"]),
        trial_extended_agents=int(row["trial_extended_agents"]),
        trial_extended_agents_used=int(row["trial_extended_agents_used"]),
        trial_extended_agents_reserved=int(row["trial_extended_agents_reserved"]),
        plan_changed_at=row["plan_changed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def period_state_from_row(row: Mapping[str, Any] | None, period_key: str) -> PeriodState:
    """Map a period row to the pure :class:`PeriodState`, or all zeros."""
    if row is None:
        return PeriodState(period_key=period_key)
    return PeriodState(
        period_key=period_key,
        spent_microusd=int(row["spent_microusd"] or 0),
        reserved_microusd=int(row["reserved_microusd"] or 0),
        extended_spent_microusd=int(row["extended_spent_microusd"] or 0),
        extended_reserved_microusd=int(row["extended_reserved_microusd"] or 0),
        extended_agents_used=int(row["extended_agents_used"] or 0),
        extended_agents_reserved=int(row["extended_agents_reserved"] or 0),
        open_reservations=int(row["open_reservations"]),
        requests_in_window=int(row["requests_in_window"] or 0),
        window_started_at=row["window_started_at"],
    )


class EntitlementStore:
    """Every SQL statement the entitlements layer issues."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    # ----------------------------------------------------------------- account
    async def ensure_account(
        self,
        conn: Connection,
        user_id: uuid.UUID,
        *,
        trial_budget_microusd: int,
        trial_extended_agents: int,
    ) -> AccountRecord:
        """Create the account row on first touch. Idempotent and race-safe.

        A brand new account is always ``free`` with a finite trial allowance.
        Nothing here consults a global default tier.
        """
        trial_state = "active" if trial_budget_microusd > 0 else "exhausted"
        row: Mapping[str, Any] | None = await conn.fetchrow(
            f"""
            INSERT INTO entitlement_accounts (
                user_id, plan, plan_source, status, byok_enabled,
                trial_state, trial_budget_microusd,
                trial_consumed_microusd, trial_reserved_microusd, trial_extended_agents
            )
            VALUES ($1, 'free', 'default', 'active', FALSE, $2, $3, 0, 0, $4)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING {_ACCOUNT_COLUMNS}
            """,
            user_id,
            trial_state,
            trial_budget_microusd,
            trial_extended_agents,
        )
        if row is None:
            record = await self.get_account(conn, user_id)
        else:
            record = _account_from_row(row)
        if record is None:  # pragma: no cover - impossible inside a transaction
            raise RuntimeError(f"entitlement account for {user_id} vanished")
        return record

    async def get_account(self, conn: Connection, user_id: uuid.UUID) -> AccountRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_ACCOUNT_COLUMNS} FROM entitlement_accounts WHERE user_id = $1",
            user_id,
        )
        return _account_from_row(row) if row is not None else None

    async def lock_account(self, conn: Connection, user_id: uuid.UUID) -> AccountRecord | None:
        """Read the account ``FOR UPDATE``: the per-user serialization point."""
        row = await conn.fetchrow(
            f"SELECT {_ACCOUNT_COLUMNS} FROM entitlement_accounts WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        return _account_from_row(row) if row is not None else None

    async def set_status(
        self, conn: Connection, *, user_id: uuid.UUID, status: AccountStatus, now: datetime
    ) -> None:
        await conn.execute(
            "UPDATE entitlement_accounts SET status = $2, updated_at = $3 WHERE user_id = $1",
            user_id,
            status.value,
            now,
        )

    async def set_plan(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        plan: Plan,
        plan_source: str,
        byok: bool | None,
        now: datetime,
    ) -> AccountRecord:
        """Apply a plan change while preserving unspent trial allowance."""
        row = await conn.fetchrow(
            f"""
            UPDATE entitlement_accounts
            SET plan = $2,
                plan_source = $3,
                plan_changed_at = $4,
                updated_at = $4,
                byok_enabled = COALESCE($5, byok_enabled)
            WHERE user_id = $1
            RETURNING {_ACCOUNT_COLUMNS}
            """,
            user_id,
            plan.value,
            plan_source,
            now,
            byok,
        )
        if row is None:
            raise RuntimeError(f"cannot set plan for unknown account {user_id}")
        return _account_from_row(row)

    async def hold_trial(
        self, conn: Connection, *, user_id: uuid.UUID, amount: int, extended: bool, now: datetime
    ) -> None:
        """Move trial allowance into the reserved bucket."""
        row = await conn.fetchrow(_HOLD_TRIAL_SQL, user_id, amount, extended, now)
        if row is None:
            raise RuntimeError("trial allowance changed while account was locked")

    async def settle_trial(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        reserved: int,
        actual: int,
        extended: bool,
        consumed: bool,
        now: datetime,
    ) -> None:
        """Release a trial hold and record what was actually spent."""
        row = await conn.fetchrow(
            _SETTLE_TRIAL_SQL, user_id, reserved, actual, extended and consumed, now, extended
        )
        if row is None:
            raise RuntimeError("trial hold missing during settlement")

    # ----------------------------------------------------------------- period
    async def ensure_period(self, conn: Connection, *, user_id: uuid.UUID, period_key: str) -> None:
        await conn.execute(
            """
            INSERT INTO entitlement_usage_periods (user_id, period_key)
            VALUES ($1, $2)
            ON CONFLICT (user_id, period_key) DO NOTHING
            """,
            user_id,
            period_key,
        )

    async def read_period_state(
        self, conn: Connection, *, user_id: uuid.UUID, period_key: str
    ) -> PeriodState:
        row = await conn.fetchrow(_PERIOD_STATE_SQL, user_id, period_key)
        return period_state_from_row(row, period_key)

    async def operation_is_open(
        self, conn: Connection, *, user_id: uuid.UUID, scope_id: uuid.UUID | None
    ) -> bool:
        """Whether ``scope_id`` already holds a concurrency slot for ``user_id``."""
        if scope_id is None:
            return False
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM entitlement_reservations r
                    WHERE r.user_id = $1 AND r.status = 'open'
                      AND NOT r.background AND r.scope_id = $2
                )
                """,
                user_id,
                scope_id,
            )
        )

    async def hold(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        period_key: str,
        amount: int,
        extended: bool,
        now: datetime,
        budget_ceiling: int,
        extended_budget_ceiling: int,
        rate_limit: int,
        concurrency_limit: int,
        extended_run_limit: int,
        money_applies: bool,
        extended_run: bool | None = None,
        background: bool = False,
        scope_id: uuid.UUID | None = None,
    ) -> Mapping[str, Any] | None:
        """Place the hold if every ceiling holds. ``None`` means refused.

        The caller re-reads the period state and calls ``admit`` to report why.
        """
        return await conn.fetchrow(
            _HOLD_SQL,
            user_id,
            period_key,
            amount,
            extended,
            now,
            budget_ceiling,
            extended_budget_ceiling,
            rate_limit,
            concurrency_limit,
            extended_run_limit,
            money_applies,
            timedelta(seconds=RATE_WINDOW_SECONDS),
            extended if extended_run is None else extended_run,
            background,
            scope_id,
        )

    async def settle_period(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        period_key: str,
        actual: int,
        reserved: int,
        extended: bool,
        now: datetime,
        ceiling: int,
        period_money: bool,
        consumed: bool,
        extended_run: bool | None = None,
    ) -> None:
        row = await conn.fetchrow(
            _SETTLE_PERIOD_SQL,
            user_id,
            period_key,
            actual,
            reserved,
            extended,
            now,
            ceiling,
            period_money,
            consumed,
            extended if extended_run is None else extended_run,
        )
        if row is None:
            raise RuntimeError("period hold missing during settlement")

    # ----------------------------------------------------------- reservations
    async def insert_reservation(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        period_key: str,
        plan: Plan,
        operation: str,
        charge_kind: ChargeKind,
        premium: bool,
        extended: bool,
        reserved: int,
        now: datetime,
        provider: str | None = None,
        model: str | None = None,
        route_id: str | None = None,
        extended_run: bool | None = None,
        background: bool = False,
        scope_id: uuid.UUID | None = None,
    ) -> Mapping[str, Any]:
        row = await conn.fetchrow(
            f"""
            INSERT INTO entitlement_reservations (
                user_id, period_key, plan, operation, charge_kind,
                premium, extended, reserved_microusd,
                provider, model, route_id, created_at, updated_at,
                extended_run, background, scope_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12, $13, $14, $15)
            RETURNING {_RESERVATION_COLUMNS}
            """,
            user_id,
            period_key,
            plan.value,
            operation,
            charge_kind.value,
            premium,
            extended,
            reserved,
            provider,
            model,
            route_id,
            now,
            extended if extended_run is None else extended_run,
            background,
            scope_id,
        )
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields a row
            raise RuntimeError("reservation insert returned no row")
        return row

    async def get_reservation(
        self,
        conn: Connection,
        reservation_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        return await conn.fetchrow(
            f"SELECT {_RESERVATION_COLUMNS} FROM entitlement_reservations WHERE id = $1{suffix}",
            reservation_id,
        )

    async def mark_reservation(
        self,
        conn: Connection,
        *,
        reservation_id: uuid.UUID,
        status: ReservationStatus,
        actual: int,
        overage: int,
        usage: Mapping[str, Any],
        now: datetime,
        provider: str | None = None,
        model: str | None = None,
        route_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Close a reservation. Guarded by ``status = 'open'`` for idempotency.

        ``provider``/``model``/``route_id`` are only filled in when the
        reservation does not already carry them, so the admission-time snapshot
        wins over a later correction.
        """
        return await conn.fetchrow(
            f"""
            UPDATE entitlement_reservations
            SET status = $2,
                actual_microusd = $3,
                overage_microusd = $9,
                usage = $4::jsonb,
                provider = COALESCE(entitlement_reservations.provider, $6),
                model = COALESCE(entitlement_reservations.model, $7),
                route_id = COALESCE(entitlement_reservations.route_id, $8),
                settled_at = $5,
                updated_at = $5
            WHERE id = $1 AND status = 'open'
            RETURNING {_RESERVATION_COLUMNS}
            """,
            reservation_id,
            status.value,
            actual,
            json.dumps(usage, sort_keys=True),
            now,
            provider,
            model,
            route_id,
            overage,
        )

    # ------------------------------------------------------------- encounters
    async def insert_encounter(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        period_key: str,
        plan: Plan,
        denial_code: str,
        operation: str | None,
        capability: str | None = None,
        requested_microusd: int | None = None,
        limits_snapshot: Mapping[str, Any] | None = None,
        observed: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a refused operation for plan tuning.

        Counters and codes only: no prompt, context or message content is ever
        written here.
        """
        await conn.execute(
            """
            INSERT INTO entitlement_limit_encounters (
                user_id, period_key, plan, operation, denial_code, capability,
                requested_microusd, limits_snapshot, observed
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            """,
            user_id,
            period_key,
            plan.value,
            operation,
            denial_code,
            capability,
            requested_microusd,
            json.dumps(limits_snapshot or {}, sort_keys=True),
            json.dumps(observed or {}, sort_keys=True),
        )

    # ----------------------------------------------------------------- events
    async def insert_event(
        self,
        conn: Connection,
        *,
        event_id: str,
        user_id: uuid.UUID,
        source: str,
        plan: Plan,
        active: bool,
        byok: bool | None,
        previous_plan: Plan,
        payload: Mapping[str, Any],
        occurred_at: datetime,
        metadata: Mapping[str, str],
    ) -> Mapping[str, Any] | None:
        """Record an event. ``None`` means the ``event_id`` already exists."""
        return await conn.fetchrow(
            """
            INSERT INTO entitlement_subscription_events (
                event_id, user_id, source, plan, active, byok,
                previous_plan, payload, occurred_at, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id, plan, previous_plan, byok, payload
            """,
            event_id,
            user_id,
            source,
            plan.value,
            active,
            byok,
            previous_plan.value,
            json.dumps(payload, sort_keys=True),
            occurred_at,
            json.dumps(metadata, sort_keys=True),
        )

    async def get_event(self, conn: Connection, event_id: str) -> Mapping[str, Any] | None:
        return await conn.fetchrow(
            """
            SELECT event_id, user_id, source, plan, active, byok, previous_plan, payload
            FROM entitlement_subscription_events
            WHERE event_id = $1
            """,
            event_id,
        )

    async def newest_event_time(self, conn: Connection, user_id: uuid.UUID) -> datetime | None:
        return await conn.fetchval(
            "SELECT max(occurred_at) FROM entitlement_subscription_events WHERE user_id = $1",
            user_id,
        )


__all__ = ["EntitlementStore", "period_state_from_row"]
