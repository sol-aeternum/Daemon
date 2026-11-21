"""Fixture-only Free-turn accounting integration for the opt-in PostgreSQL test.

This module is deliberately test support only. Nothing under ``orchestrator/``
imports it: the opt-in schema is qualified exclusively by
``test_free_turn_accounting_postgres.py``. It never reads an application DSN,
opens a network listener or starts a container; it composes the existing
owned ``metadata_cluster`` / ``Database`` / ``Bridge`` fixture and the cached
network-none image. Enable it only with ``--compute-metadata-postgres``.

The real :class:`EntitlementService` and :class:`EntitlementStore` remain the
money gate. The only private seam here attaches the opt-in turn lineage after
``_reserve_body`` inserts its row on the same uncommitted transaction, and moves
the fixture-owned Free escrow through the real usage-period reserved counter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from asyncpg.exceptions import (
    InterfaceError as ClientInterfaceError,
    PostgresConnectionError,
)
import pytest_asyncio

from orchestrator.entitlements.ledger import period_key
from orchestrator.entitlements.plans import ChargeKind, Plan
from orchestrator.entitlements.resolver import resolve_account_policy
from orchestrator.entitlements.service import EntitlementService
from orchestrator.entitlements.store import EntitlementStore
from orchestrator.services.account_control.store import account_lock_key
from tests.compute_metadata_support import (
    Bridge,
    Database,
    ROOT,
    close_connection,
    metadata_cluster as metadata_cluster,
)

MIGRATION = "075_free_turn_accounting.sql"
OPT_IN = ROOT / "migrations" / "opt_in" / MIGRATION
OWNER_ROLE = "md_owner"
APP_ROLE = "md_app"
BIGINT_MAX = 2**63 - 1
MAX_BATCH_MEMBERS = 16
_ROLE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}", re.ASCII)


class FreeTurnDenied(Exception):
    """A fixture-integration refusal with a stable machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FreeTurnCommitUncertain(Exception):
    """The local commit acknowledgment was lost; effects are unverified."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__("free_turn_commit_uncertain")


@dataclass(frozen=True, slots=True)
class Allocation:
    user_id: UUID
    turn_root_id: UUID
    period_key: str
    scope_id: UUID
    bound_microusd: int
    funded_microusd: int
    status: str
    revision: int
    created_at: datetime
    sealed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == "open"


@dataclass(frozen=True, slots=True)
class Observation:
    allocation: Allocation
    now: datetime
    period_key: str
    spent_microusd: int
    held_microusd: int
    funded_microusd: int
    period_reserved_microusd: int
    permission_remaining_microusd: int


@dataclass(frozen=True, slots=True)
class BatchMember:
    """One exact member of a fixture batch. Stable ``op_id`` is the replay key."""

    op_id: UUID
    operation: str
    quote_microusd: int
    role: str | None = None


@dataclass(frozen=True, slots=True)
class BatchResult:
    reservation_ids: tuple[UUID, ...]
    applied: bool
    replayed: bool


@dataclass(frozen=True, slots=True)
class SealResult:
    allocation: Allocation
    released_funded_microusd: int
    applied: bool


def _require_uint(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > BIGINT_MAX:
        raise ValueError(f"{field} must be a non-negative integer within bigint")
    return value


def _require_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field} must be a non-zero UUID")
    return value


def _validate_members(
    service: EntitlementService, members: Sequence[BatchMember]
) -> tuple[BatchMember, ...]:
    if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
        raise ValueError("members must be a sequence of BatchMember")
    if not 1 <= len(members) <= MAX_BATCH_MEMBERS:
        raise ValueError("members must contain between 1 and 16 entries")
    seen: set[UUID] = set()
    validated: list[BatchMember] = []
    for item in members:
        if type(item) is not BatchMember:
            raise ValueError("members must contain exact BatchMember values")
        op_id = _require_uuid(item.op_id, "op_id")
        if op_id in seen:
            raise ValueError("duplicate op_id")
        seen.add(op_id)
        quote = _require_uint(item.quote_microusd, "quote_microusd")
        if item.operation not in service.policy.operations:
            raise ValueError("operation is not configured")
        if item.role is not None:
            if not isinstance(item.role, str) or not _ROLE_RE.fullmatch(item.role):
                raise ValueError("role must be a bounded lowercase workload label")
        validated.append(
            BatchMember(op_id=op_id, operation=item.operation, quote_microusd=quote, role=item.role)
        )
    return tuple(validated)


class FreeTurnDatabase:
    """One owned database, marker, cached network-none cluster and real pool."""

    def __init__(
        self,
        cluster,
        database: Database,
        pool: asyncpg.Pool,
        *,
        nonce: str,
    ) -> None:
        self.cluster = cluster
        self.database = database
        self.pool = pool
        self.nonce = nonce

    @classmethod
    @asynccontextmanager
    async def create(cls, cluster):
        name = "fta_" + uuid4().hex
        cluster.prepare(name)
        cluster.sql(
            "BEGIN;\n" + OPT_IN.read_text() + "\nCOMMIT;",
            database=name,
            role=OWNER_ROLE,
        )
        cluster.sql(
            "CREATE TABLE public.free_turn_fixture_marker(nonce text PRIMARY KEY);"
            "GRANT SELECT ON public.free_turn_fixture_marker, public.account_deletions, "
            "public.account_suspension_causes TO md_app;"
            "GRANT SELECT, INSERT, UPDATE ON public.entitlement_accounts, "
            "public.entitlement_usage_periods, public.entitlement_reservations, "
            "public.turn_budget_allocations TO md_app;",
            database=name,
            role=OWNER_ROLE,
        )
        owner = cluster.sql(
            "SELECT tableowner FROM pg_catalog.pg_tables "
            "WHERE schemaname='public' AND tablename='free_turn_fixture_marker'",
            database=name,
            role="postgres",
        )
        if owner != OWNER_ROLE or (
            cluster.sql(
                "SELECT to_regclass('public.turn_budget_allocations') IS NOT NULL "
                "AND EXISTS(SELECT 1 FROM pg_catalog.pg_trigger "
                "WHERE tgname='entitlement_reservations_turn_lineage' "
                "AND NOT tgisinternal)",
                database=name,
                role=OWNER_ROLE,
            )
            != "t"
        ):
            raise RuntimeError("isolated accounting schema did not match")

        resource = cluster.resource / uuid4().hex[:6]
        resource.mkdir(mode=0o700)
        if len(os.fsencode(resource / ".s.PGSQL.5432")) >= 108:
            raise RuntimeError("isolated PostgreSQL socket path is too long")
        bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
        await bridge.start()
        database = Database(cluster, name, resource, bridge)
        nonce = uuid4().hex
        try:
            async with database.connection() as conn:
                await conn.execute(
                    "INSERT INTO public.free_turn_fixture_marker(nonce) VALUES($1)", nonce
                )
            cluster.record("free_turn_schema", database=name)
            async with database.pool(command_timeout=3, max_size=4) as pool:
                yield cls(cluster, database, pool, nonce=nonce)
        finally:
            await bridge.close()
            async with asyncio.timeout(3):
                while (
                    cluster.sql(
                        f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'",
                        role="postgres",
                    )
                    != "0"
                ):
                    await asyncio.sleep(0.01)
            if name not in cluster.databases:
                raise RuntimeError("database cleanup identity is not owned by the fixture")
            cluster.sql(f'DROP DATABASE "{name}"')
            cluster.databases.remove(name)
            cluster.record("free_turn_database_reconciled", database=name, connections=0)

    async def accounting(self, account: UUID | None = None) -> "FreeTurnAccounting":
        """Create or bind one synthetic account on this owned database."""
        async with self.database.connection() as owner:
            if account is None:
                account = await self.database.account(owner)
            generation = await owner.fetchval(
                "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1",
                account,
            )
            if generation is None:
                raise FreeTurnDenied("account_missing")
        return FreeTurnAccounting(self, account, generation)


class FreeTurnAccounting:
    """Bound-owner/account scope; refuses every other account."""

    def __init__(self, database: FreeTurnDatabase, account: UUID, generation: int) -> None:
        self.database = database
        self.cluster = database.cluster
        self.account = account
        self.generation = generation
        self.service = EntitlementService(database.pool)
        self.store = EntitlementStore(database.pool)

    # ---------------------------------------------------------------- private
    async def _private(self, conn) -> None:
        if not conn.is_in_transaction():
            raise FreeTurnDenied("private_transaction_required")
        await conn.execute("SET LOCAL lock_timeout='1500ms'; SET LOCAL statement_timeout='2000ms'")
        row = await conn.fetchrow(
            """SELECT current_database() AS database, current_user AS role,
                (SELECT tableowner FROM pg_catalog.pg_tables
                 WHERE schemaname='public' AND tablename='free_turn_fixture_marker') AS marker_owner,
                EXISTS(SELECT 1 FROM public.free_turn_fixture_marker WHERE nonce=$1) AS marker""",
            self.database.nonce,
        )
        if (
            row is None
            or row["database"] != self.database.database.name
            or row["role"] != APP_ROLE
            or row["marker_owner"] != OWNER_ROLE
            or not row["marker"]
        ):
            raise FreeTurnDenied("fixture_scope_required")

    @asynccontextmanager
    async def _mutation(self, operation: str):
        conn = await self.database.database.connect(APP_ROLE)
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                await self._private(conn)
                yield conn
            except BaseException:
                # COMMIT was never attempted. Closing the exact connection
                # aborts the transaction, even if rollback IO is unavailable.
                conn.terminate()
                raise
            try:
                await tx.commit()
            except (PostgresConnectionError, ClientInterfaceError, OSError, TimeoutError) as exc:
                # A muted socket cannot acknowledge close either. Do not let
                # that second failure replace the uncertain COMMIT result.
                conn.terminate()
                raise FreeTurnCommitUncertain(operation) from exc
            except asyncio.CancelledError:
                conn.terminate()
                raise
        finally:
            if not conn.is_closed():
                await close_connection(conn)

    @asynccontextmanager
    async def _read(self):
        async with self.database.database.connection(APP_ROLE) as conn:
            async with conn.transaction():
                await self._private(conn)
                yield conn

    async def _lock(self, conn):
        await conn.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(self.account))
        record = await self.store.lock_account(conn, self.account)
        if record is None:
            raise FreeTurnDenied("account_missing")
        generation = await conn.fetchval(
            "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1",
            self.account,
        )
        if generation != self.generation:
            raise FreeTurnDenied("account_generation_changed")
        control = await conn.fetchrow(
            "SELECT EXISTS(SELECT 1 FROM account_deletions "
            "WHERE account_id=$1 AND closed_at IS NULL) AS deletion, "
            "EXISTS(SELECT 1 FROM account_suspension_causes "
            "WHERE account_id=$1) AS cause",
            self.account,
        )
        if control is None or control["deletion"] or control["cause"]:
            raise FreeTurnDenied("account_control_pending")
        return record

    def _require_free(self, record, *, now: datetime):
        period = period_key(now)
        resolved = resolve_account_policy(record, self.service.policy, period=period)
        self.service._require_usable(record, resolved=resolved, premium=False, extended=False)
        if record.plan is not Plan.FREE:
            raise FreeTurnDenied("plan_not_free")
        if resolved.charge_kind_for(False) is not ChargeKind.PLAN:
            raise FreeTurnDenied("charge_not_plan")
        if resolved.recurring_budget_microusd <= 0:
            raise FreeTurnDenied("period_ceiling")
        return resolved

    async def _clock(self, conn) -> tuple[datetime, str]:
        now = await conn.fetchval("SELECT clock_timestamp()")
        return now, period_key(now)

    async def _allocation(self, conn, root_id: UUID, *, lock: bool = False) -> Allocation:
        row = await conn.fetchrow(
            "SELECT * FROM turn_budget_allocations "
            "WHERE user_id=$1 AND turn_root_id=$2" + (" FOR UPDATE" if lock else ""),
            self.account,
            root_id,
        )
        if row is None:
            owner = await conn.fetchval(
                "SELECT user_id FROM turn_budget_allocations WHERE turn_root_id=$1",
                root_id,
            )
            if owner is not None and owner != self.account:
                raise FreeTurnDenied("cross_account")
            raise FreeTurnDenied("unknown_root")
        return _allocation_from_row(row)

    async def _s_h(self, conn, root_id: UUID) -> tuple[int, int]:
        row = await conn.fetchrow(
            """SELECT
                COALESCE(sum(actual_microusd) FILTER (WHERE status='settled'),0)::bigint AS spent,
                COALESCE(sum(reserved_microusd) FILTER (WHERE status='open'),0)::bigint AS held
               FROM entitlement_reservations
               WHERE user_id=$1 AND turn_root_id=$2""",
            self.account,
            root_id,
        )
        return int(row["spent"]), int(row["held"])

    async def _period_reserved(self, conn, period: str) -> int:
        value = await conn.fetchval(
            "SELECT reserved_microusd FROM entitlement_usage_periods "
            "WHERE user_id=$1 AND period_key=$2",
            self.account,
            period,
        )
        return int(value) if value is not None else 0

    async def _escrow_add(self, conn, period: str, amount: int, ceiling: int) -> None:
        row = await conn.fetchrow(
            """UPDATE entitlement_usage_periods
               SET reserved_microusd=reserved_microusd+$3,
                   updated_at=pg_catalog.clock_timestamp()
               WHERE user_id=$1 AND period_key=$2
                 AND spent_microusd+reserved_microusd+$3 <= $4
               RETURNING reserved_microusd""",
            self.account,
            period,
            amount,
            ceiling,
        )
        if row is None:
            raise FreeTurnDenied("period_ceiling")

    async def _escrow_release(self, conn, period: str, amount: int) -> None:
        row = await conn.fetchrow(
            """UPDATE entitlement_usage_periods
               SET reserved_microusd=reserved_microusd-$3,
                   updated_at=pg_catalog.clock_timestamp()
               WHERE user_id=$1 AND period_key=$2 AND reserved_microusd >= $3
               RETURNING reserved_microusd""",
            self.account,
            period,
            amount,
        )
        if row is None:
            raise FreeTurnDenied("reserve_underflow")

    async def _attach(
        self,
        conn,
        reservation: UUID,
        root_id: UUID,
        op_id: UUID,
        batch_id: UUID,
        final: bool,
    ) -> None:
        row = await conn.fetchrow(
            """UPDATE entitlement_reservations
               SET turn_root_id=$2, turn_op_id=$3, turn_batch_id=$4, turn_final=$5
               WHERE id=$1 AND user_id=$6 AND turn_root_id IS NULL
               RETURNING id""",
            reservation,
            root_id,
            op_id,
            batch_id,
            final,
            self.account,
        )
        if row is None:
            raise FreeTurnDenied("lineage_attach_refused")

    async def _batch_rows(self, conn, root_id: UUID, batch_id: UUID):
        return await conn.fetch(
            """SELECT id,turn_op_id,turn_batch_id,turn_final,reserved_microusd,
                      operation,workload_profile,status
               FROM entitlement_reservations
               WHERE user_id=$1 AND turn_root_id=$2 AND turn_batch_id=$3
               ORDER BY turn_op_id""",
            self.account,
            root_id,
            batch_id,
        )

    # ---------------------------------------------------------------- public
    async def open_root(
        self,
        *,
        bound_microusd: int,
        funded_microusd: int,
        root_id: UUID | None = None,
    ) -> Allocation:
        """Open one Free root and reserve its unused F in the current period."""
        bound = _require_uint(bound_microusd, "bound_microusd")
        funded = _require_uint(funded_microusd, "funded_microusd")
        root = uuid4() if root_id is None else _require_uuid(root_id, "root_id")
        if funded > bound:
            raise FreeTurnDenied("permission_exceeded")
        async with self._mutation("open_root") as conn:
            record = await self._lock(conn)
            now, period = await self._clock(conn)
            resolved = self._require_free(record, now=now)
            await self.store.ensure_period(conn, user_id=self.account, period_key=period)
            await self._escrow_add(conn, period, funded, resolved.recurring_budget_microusd)
            row = await conn.fetchrow(
                """INSERT INTO turn_budget_allocations
                   (user_id,turn_root_id,period_key,scope_id,bound_microusd,funded_microusd)
                   VALUES($1,$2,$3,pg_catalog.gen_random_uuid(),$4,$5)
                   RETURNING *""",
                self.account,
                root,
                period,
                bound,
                funded,
            )
            return _allocation_from_row(row)

    async def add_funded(
        self,
        root_id: UUID,
        amount_microusd: int,
        *,
        expected_revision: int | None = None,
    ) -> Allocation:
        """Add unused F in the same current period, under the real monthly ceiling."""
        root = _require_uuid(root_id, "root_id")
        amount = _require_uint(amount_microusd, "amount_microusd")
        async with self._mutation("add_funded") as conn:
            record = await self._lock(conn)
            now, period = await self._clock(conn)
            allocation = await self._allocation(conn, root, lock=True)
            if not allocation.is_open:
                raise FreeTurnDenied("root_sealed")
            if allocation.period_key != period:
                raise FreeTurnDenied("period_closed")
            if expected_revision is not None and allocation.revision != expected_revision:
                raise FreeTurnDenied("stale_revision")
            resolved = self._require_free(record, now=now)
            spent, held = await self._s_h(conn, root)
            if spent + held + allocation.funded_microusd + amount > allocation.bound_microusd:
                raise FreeTurnDenied("permission_exceeded")
            await self._escrow_add(conn, period, amount, resolved.recurring_budget_microusd)
            row = await conn.fetchrow(
                """UPDATE turn_budget_allocations
                   SET funded_microusd=funded_microusd+$3, revision=revision+1
                   WHERE user_id=$1 AND turn_root_id=$2 AND status='open'
                   RETURNING *""",
                self.account,
                root,
                amount,
            )
            if row is None:  # pragma: no cover - locked row was just read open
                raise FreeTurnDenied("stale_revision")
            return _allocation_from_row(row)

    async def reserve_one(self, root_id: UUID, member: BatchMember) -> UUID:
        """One optional member still has a stable batch identity."""
        result = await self.reserve_batch(root_id, member.op_id, (member,), final=False)
        return result.reservation_ids[0]

    async def observe_batch(
        self, root_id: UUID, batch_id: UUID, members: Sequence[BatchMember], *, final: bool
    ) -> BatchResult | None:
        """Observe the original batch after uncertain commit; never reserve again."""
        root = _require_uuid(root_id, "root_id")
        batch = _require_uuid(batch_id, "batch_id")
        validated = _validate_members(self.service, members)
        async with self._read() as conn:
            await self._lock(conn)
            await self._allocation(conn, root)
            return await self._observe_batch(conn, root, batch, validated, final=final)

    async def _observe_batch(self, conn, root, batch, members, *, final) -> BatchResult | None:
        existing = await self._batch_rows(conn, root, batch)
        if not existing:
            return None
        by_op = {row["turn_op_id"]: row for row in existing}
        if len(existing) != len(members) or set(by_op) != {item.op_id for item in members}:
            raise FreeTurnDenied("batch_conflict")
        for item in members:
            row = by_op[item.op_id]
            if (
                int(row["reserved_microusd"]) != item.quote_microusd
                or row["operation"] != item.operation
                or row["workload_profile"] != item.role
                or row["turn_final"] is not final
            ):
                raise FreeTurnDenied("batch_conflict")
        return BatchResult(
            tuple(by_op[item.op_id]["id"] for item in members), applied=False, replayed=True
        )

    async def reserve_batch(
        self,
        root_id: UUID,
        batch_id: UUID,
        members: Sequence[BatchMember],
        *,
        final: bool,
    ) -> BatchResult:
        """All-or-none final/optional batch on one connection and one commit."""
        root = _require_uuid(root_id, "root_id")
        batch = _require_uuid(batch_id, "batch_id")
        if not isinstance(final, bool):
            raise ValueError("final must be a boolean")
        validated = _validate_members(self.service, members)
        if final and len(validated) != 1:
            raise ValueError("one final call per batch")
        async with self._mutation("reserve_batch") as conn:
            record = await self._lock(conn)
            now, period = await self._clock(conn)
            allocation = await self._allocation(conn, root, lock=True)

            existing = await self._observe_batch(conn, root, batch, validated, final=final)
            if existing is not None:
                return existing

            if not allocation.is_open:
                raise FreeTurnDenied("root_sealed")
            if allocation.period_key != period:
                raise FreeTurnDenied("period_closed")
            self._require_free(record, now=now)

            op_ids = [item.op_id for item in validated]
            overlap = await conn.fetchval(
                """SELECT EXISTS(
                       SELECT 1 FROM entitlement_reservations
                       WHERE user_id=$1 AND turn_root_id=$2 AND turn_op_id=ANY($3::uuid[]))""",
                self.account,
                root,
                op_ids,
            )
            if overlap:
                raise FreeTurnDenied("batch_mixed")

            spent, held = await self._s_h(conn, root)
            total = sum(item.quote_microusd for item in validated)
            draw = min(allocation.funded_microusd, total) if final else 0
            funded_after = allocation.funded_microusd - draw
            if spent + held + funded_after + total > allocation.bound_microusd:
                raise FreeTurnDenied("permission_exceeded")
            if draw:
                await self._escrow_release(conn, period, draw)
                changed = await conn.fetchrow(
                    """UPDATE turn_budget_allocations
                       SET funded_microusd=funded_microusd-$3, revision=revision+1
                       WHERE user_id=$1 AND turn_root_id=$2 AND status='open'
                         AND funded_microusd >= $3
                       RETURNING turn_root_id""",
                    self.account,
                    root,
                    draw,
                )
                if changed is None:
                    raise FreeTurnDenied("stale_revision")

            reservations: list[UUID] = []
            for item in validated:
                reservation = await self.service._reserve_body(
                    conn,
                    self.account,
                    item.quote_microusd,
                    [],
                    operation=item.operation,
                    premium=False,
                    extended=False,
                    extended_run=False,
                    background=False,
                    scope_id=allocation.scope_id,
                    provider=None,
                    model=None,
                    route_id=None,
                    now=now,
                    period=period,
                    workload_profile=item.role,
                    reasoning_effort=None,
                )
                await self._attach(conn, reservation.id, root, item.op_id, batch, final)
                reservations.append(reservation.id)
            return BatchResult(tuple(reservations), applied=True, replayed=False)

    async def seal(self, root_id: UUID, *, expected_revision: int) -> SealResult:
        """Release only unused F, bounded by owner/revision; stable idempotent readback."""
        root = _require_uuid(root_id, "root_id")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision <= 0
        ):
            raise ValueError("expected_revision must be a positive integer")
        async with self._mutation("seal") as conn:
            await self._lock(conn)
            allocation = await self._allocation(conn, root, lock=True)
            if allocation.status == "sealed":
                if allocation.revision == expected_revision + 1 and allocation.funded_microusd == 0:
                    return SealResult(allocation, 0, applied=False)
                raise FreeTurnDenied("stale_revision")
            if allocation.revision != expected_revision:
                raise FreeTurnDenied("stale_revision")
            funded = allocation.funded_microusd
            await self._escrow_release(conn, allocation.period_key, funded)
            row = await conn.fetchrow(
                """UPDATE turn_budget_allocations
                   SET funded_microusd=0, status='sealed', revision=revision+1,
                       sealed_at=pg_catalog.clock_timestamp()
                   WHERE user_id=$1 AND turn_root_id=$2 AND status='open'
                     AND revision=$3
                   RETURNING *""",
                self.account,
                root,
                expected_revision,
            )
            if row is None:  # pragma: no cover - locked row was just validated
                raise FreeTurnDenied("stale_revision")
            return SealResult(_allocation_from_row(row), funded, applied=True)

    async def observe(self, root_id: UUID) -> Observation:
        root = _require_uuid(root_id, "root_id")
        async with self._read() as conn:
            await self._lock(conn)
            now, period = await self._clock(conn)
            allocation = await self._allocation(conn, root)
            spent, held = await self._s_h(conn, root)
            reserved = await self._period_reserved(conn, allocation.period_key)
            remaining = allocation.bound_microusd - spent - held - allocation.funded_microusd
            return Observation(
                allocation=allocation,
                now=now,
                period_key=period,
                spent_microusd=spent,
                held_microusd=held,
                funded_microusd=allocation.funded_microusd,
                period_reserved_microusd=reserved,
                permission_remaining_microusd=remaining,
            )

    async def period_state(self, period: str | None = None) -> dict[str, Any] | None:
        async with self._read() as conn:
            if period is None:
                _, period = await self._clock(conn)
            row = await conn.fetchrow(
                "SELECT * FROM entitlement_usage_periods WHERE user_id=$1 AND period_key=$2",
                self.account,
                period,
            )
            return dict(row) if row is not None else None

    async def reservation_row(self, reservation: UUID) -> dict[str, Any] | None:
        async with self._read() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM entitlement_reservations WHERE id=$1 AND user_id=$2",
                reservation,
                self.account,
            )
            return dict(row) if row is not None else None

    # --------------------------------------------------------------- service
    async def legacy_reserve(
        self, quote_microusd: int, *, operation: str = "chat", premium: bool = False
    ):
        return await self.service.reserve(
            self.account, quote_microusd, operation=operation, premium=premium
        )

    async def settle(self, reservation, actual_microusd: int, *, usage=None):
        await self._require_owned_reservation(reservation)
        return await self.service.settle(reservation, actual_microusd, usage=usage)

    async def release(self, reservation, *, usage=None):
        await self._require_owned_reservation(reservation)
        return await self.service.release(reservation, usage=usage)

    async def _require_owned_reservation(self, reservation):
        if (
            await self.reservation_row(
                reservation if isinstance(reservation, UUID) else reservation.id
            )
            is None
        ):
            raise FreeTurnDenied("unknown_reservation")

    # ------------------------------------------------------- fixture setup
    async def seed_spend(self, amount_microusd: int) -> None:
        """Explicit fixture consumption; production still sees ordinary spend."""
        amount = _require_uint(amount_microusd, "amount_microusd")
        async with self._mutation("seed_spend") as conn:
            record = await self._lock(conn)
            now, period = await self._clock(conn)
            resolved = self._require_free(record, now=now)
            await self.store.ensure_period(conn, user_id=self.account, period_key=period)
            row = await conn.fetchrow(
                """UPDATE entitlement_usage_periods
                   SET spent_microusd=spent_microusd+$3,
                       updated_at=pg_catalog.clock_timestamp()
                   WHERE user_id=$1 AND period_key=$2
                     AND spent_microusd+reserved_microusd+$3 <= $4
                   RETURNING spent_microusd""",
                self.account,
                period,
                amount,
                resolved.recurring_budget_microusd,
            )
            if row is None:
                raise FreeTurnDenied("period_ceiling")


def _allocation_from_row(row) -> Allocation:
    return Allocation(
        user_id=row["user_id"],
        turn_root_id=row["turn_root_id"],
        period_key=row["period_key"],
        scope_id=row["scope_id"],
        bound_microusd=int(row["bound_microusd"]),
        funded_microusd=int(row["funded_microusd"]),
        status=row["status"],
        revision=int(row["revision"]),
        created_at=row["created_at"],
        sealed_at=row["sealed_at"],
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def free_turn_database(metadata_cluster):
    async with FreeTurnDatabase.create(metadata_cluster) as database:
        yield database


__all__ = [
    "Allocation",
    "BatchMember",
    "BatchResult",
    "FreeTurnAccounting",
    "FreeTurnCommitUncertain",
    "FreeTurnDatabase",
    "FreeTurnDenied",
    "Observation",
    "SealResult",
    "free_turn_database",
    "metadata_cluster",
]
