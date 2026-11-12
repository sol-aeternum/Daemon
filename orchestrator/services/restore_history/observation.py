"""Qualified historical reads, never acceptance acknowledgement or send authority.

Explicitly supplied to the by-key HTTP boundary; no initializer or startup host.
Ownership is H -> auth -> account -> task. Actual SQL and filesystem cleanup
settles before returning even after cancellation or a logical deadline.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import asyncpg

from orchestrator.auth import AuthenticatedDevice
from orchestrator.auth_runtime_state import lock_auth_runtime_state
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    WriteGuardRefusal,
    guard_content_write,
)
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks.store import TaskSnapshot, TaskStore

from .coordination import HistoryCoordinator
from .effects_projection import HistoryProjection
from .model import MAX_RECORDS, AcceptPrepare, HistoryError, commitments
from .projection import compare_projections
from .store import _check_time
from .terminal_projection import TerminalHistoryProjection


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    kind: Literal["task", "unknown", "recovery", "unavailable", "denied"]
    snapshot: TaskSnapshot | None = None
    task_id: UUID | None = None


class HistoryObservation:
    """Borrow the actual W store, opened H owner, profile and backend-only K1.

    Constructor is inert. The supplied AuthenticatedDevice is trusted request
    context, not bearer proof; only the authenticated route may supply it in a
    runtime. Each read revalidates its actual device/session on the W connection.
    No binding, grant, mutation, dispatch receipt or recovery disposition is made.
    """

    def __init__(
        self,
        tasks: TaskStore,
        history: HistoryCoordinator,
        projection: HistoryProjection,
        key: bytes,
    ) -> None:
        self._tasks, self._history, self._projection, self._key = tasks, history, projection, key
        self._validate()

    def _validate(self) -> None:
        tasks, history, projection = self._tasks, self._history, self._projection
        if (
            type(tasks) is not TaskStore
            or type(history) is not HistoryCoordinator
            or type(projection) not in (HistoryProjection, TerminalHistoryProjection)
            or projection.profile
            != ("terminal-v3" if type(projection) is TerminalHistoryProjection else "h1")
            or history.profile != projection.profile
            or type(tasks._pool) is not asyncpg.Pool
            or tasks._pool is not tasks._memory._pool
            or history._expected != projection.expected
        ):
            raise HistoryError("observation_composition_refused")
        projection.expected.verify_key(self._key)

    async def lookup(
        self, auth: AuthenticatedDevice, client_key: str, *, deadline: float
    ) -> HistoricalObservation:
        self._validate()
        self._history._bind()
        _check_time(deadline)
        self._projection.expected.verify_key(self._key)
        if (
            self._history._expected != self._projection.expected
            or type(self._tasks._pool) is not asyncpg.Pool
            or self._tasks._pool is not self._tasks._memory._pool
            or type(auth) is not AuthenticatedDevice
            or any(not isinstance(v, UUID) for v in (auth.user_id, auth.device_id, auth.session_id))
        ):
            raise HistoryError("observation_composition_refused")
        # Copy mutable request context before scheduling the supervised owner.
        owner = AuthenticatedDevice(auth.user_id, auth.device_id, auth.session_id)
        # Lookup uses the exact approved account/key namespace. No request body
        # is inferred; the unused request commitment is not replay comparison.
        lookup, _ = commitments(self._key, owner.user_id, client_key, b"")
        child = asyncio.create_task(self._lookup(owner, client_key, lookup, deadline))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                result = await asyncio.shield(child)
            _check_time(deadline)
            if self._history._blocked:
                raise HistoryError("observation_unavailable")
            return result
        except BaseException as original:
            self._history._blocked = True
            if not child.done():
                child.cancel()
            while not child.done():
                try:
                    await asyncio.shield(child)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                child.result()
            except BaseException as secondary:
                if secondary is not original:
                    original.add_note("Observation cleanup completion: " + type(secondary).__name__)
            raise

    async def _lookup(
        self, auth: AuthenticatedDevice, client_key: str, lookup: str, deadline: float
    ) -> HistoricalObservation:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await operation.verify()
            accepted = next(
                (
                    e.command.data
                    for e in state.entries
                    if type(e.command.data) is AcceptPrepare
                    and e.command.data.account_id == auth.user_id
                    and hmac.compare_digest(e.command.data.key_commitment, lookup)
                ),
                None,
            )
            async with pool_connection(self._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("observation_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    await self._projection._limits(conn, deadline)
                    if (
                        await conn.fetchval(
                            "SELECT current_schemas(true)=ARRAY['pg_catalog','public']::name[]"
                        )
                        is not True
                    ):
                        raise HistoryError("observation_namespace_refused")
                    await lock_auth_runtime_state(conn)
                    authorized = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM public.sessions s JOIN public.devices d ON d.id=s.device_id "
                        "WHERE s.id=$1 AND d.id=$2 AND s.user_id=$3 AND d.user_id=$3 "
                        "AND s.revoked_at IS NULL AND d.revoked_at IS NULL "
                        "AND s.access_expires_at>clock_timestamp())",
                        auth.session_id,
                        auth.device_id,
                        auth.user_id,
                    )
                    if authorized is not True:
                        return HistoricalObservation("denied")
                    await self._projection.qualify(conn, writing=False, deadline=deadline)
                    if await conn.fetchval("SELECT CURRENT_USER::text") != self._projection.worker:
                        raise HistoryError("observation_principal_refused")
                    facts = []
                    cursor = 0
                    while True:
                        page = await self._projection.page(
                            conn, after_seq=cursor, deadline=deadline
                        )
                        facts.extend(page)
                        if len(facts) > MAX_RECORDS:
                            raise HistoryError("observation_unavailable")
                        if len(page) < 32:
                            break
                        cursor = page[-1].seq
                    report = compare_projections(
                        self._projection.expected.history_id,
                        state,
                        tuple(facts),
                        profile=self._projection.profile,
                    )
                    parity = not (
                        report.missing
                        or report.extra
                        or report.mismatched
                        or report.restore_required
                    )
                    if accepted is None:
                        if not parity:
                            return HistoricalObservation("unavailable")
                        sql_only = await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM public.tasks WHERE user_id=$1 AND idempotency_key=$2)",
                            auth.user_id,
                            client_key,
                        )
                        return HistoricalObservation("recovery" if sql_only else "unknown")
                    try:
                        async with guard_content_write(
                            conn, CapturedGeneration(auth.user_id, accepted.account_generation)
                        ):
                            # This reuses the native generation/lifecycle guard
                            # for a read, not a grant to write. Account SHARE
                            # precedes task SHARE; no fresh generation is captured.
                            if not parity:
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            row = await conn.fetchrow(
                                "SELECT t.*,m.content AS result_content,"
                                "(SELECT count(*) FROM public.task_attempts a WHERE a.task_id=t.id "
                                "AND a.epoch<t.lease_epoch AND a.execution_started_at IS NOT NULL "
                                "AND a.outcome IN ('lost','failed_retryable')) AS regeneration_count "
                                "FROM public.tasks t JOIN public.messages m ON m.id=t.result_message_id "
                                "WHERE t.id=$1 AND t.user_id=$2 FOR SHARE OF t,m",
                                accepted.task_id,
                                auth.user_id,
                            )
                            if row is None or (
                                row["conversation_id"],
                                row["user_message_id"],
                                row["result_message_id"],
                            ) != (
                                accepted.conversation_id,
                                accepted.user_message_id,
                                accepted.result_message_id,
                            ):
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            if self._tasks._origin(row).generation != accepted.account_generation:
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            canonical = self._tasks._open(row["input_ciphertext"]).get("_request")
                            if row["idempotency_key"] != client_key or type(canonical) is not str:
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            _, request = commitments(
                                self._key, auth.user_id, client_key, canonical.encode("utf-8")
                            )
                            if not hmac.compare_digest(request, accepted.request_commitment):
                                return HistoricalObservation("recovery", task_id=accepted.task_id)
                            return HistoricalObservation("task", self._tasks._snapshot_from(row))
                    except WriteGuardRefusal:
                        # No old content or original ID after lifecycle/generation
                        # refusal. Negative key identity still cannot become NEW.
                        return HistoricalObservation("recovery")
