"""Uncalled H/native joins. No transport, observer, host or reusable permit.

Every new send still needs its original fresh native mark. Historical sends only
constrain admission; they never grant it. SQL and filesystem cleanup finish under
H ownership, even after logical timeout. Kernel cleanup has no promised bound.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Coroutine
from typing import Any, Literal, TypeVar
from uuid import UUID, uuid4

import asyncpg

from orchestrator.artifact_reservations import (
    OutputReservations,
    IntentCommand,
    JobBinding,
    IntentBinding,
)
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_csv import encode_csv
from orchestrator.resource_store import ReservedOutputPublisher, Publication
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.tasks._invocation import InferenceInvocation, block
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.store import CancelOutcome, Claim, TaskStore
from orchestrator.tasks.states import TaskStatus

from .coordination import HistoryCoordinator, HistoryOperation
from .effects_projection import HistoryProjection
from .model import AcceptPrepare, Command, HistoryError, SendPossible, StopIntent
from .projection import compare_projections
from .store import _check_time

T = TypeVar("T")


class HistoryEffects:
    """Explicit trusted synthetic host only; constructor does no I/O.

    W never receives A credentials or K1. Backend Stop supplies K1 only for that
    call; worker sends compare independently supplied identity/key-check metadata.
    Successful mark returns None, matching native mark. Provider work belongs to
    the original invocation AFTER this call has released H, never in a callback.
    """

    def __init__(
        self, tasks: TaskStore, history: HistoryCoordinator, projection: HistoryProjection
    ):
        if (
            type(tasks) is not TaskStore
            or type(history) is not HistoryCoordinator
            or type(projection) is not HistoryProjection
            or type(tasks._inference) is not InferenceRecovery
        ):
            raise HistoryError("effects_composition_refused")
        self._tasks, self._history, self._projection = tasks, history, projection
        self._blocked = False
        # Strong references prevent object-id reuse. This negative filter permits
        # continued distinct calls only within the SAME original live invocation;
        # reconstruction/restart cannot adopt historical sends. Not send authority.
        self._owned_sends: dict[int, tuple[InferenceInvocation, set[UUID]]] = {}
        self._check()

    def _check(self) -> None:
        recovery = self._tasks._inference
        if (
            recovery is None
            or type(recovery) is not InferenceRecovery
            or recovery.pool is not self._tasks._pool
            or self._tasks._pool is not self._tasks._memory._pool
            or type(self._tasks._pool) is not asyncpg.Pool
            or self._tasks._cover_new_inference is not False
            or recovery.principals != (self._projection.worker, self._projection.acceptor)
            or self._history._expected != self._projection.expected
        ):
            raise HistoryError("effects_composition_refused")

    def _admission_deadline(self, deadline: float) -> float:
        # Freeze the total budget before caller hooks/key work. Primitive clock
        # checks precede PID/loop binding; no inherited owner or callback is used.
        _check_time(deadline)
        deadline = min(deadline, time.monotonic() + 2.0)
        self._history._bind()
        self._check()
        if self._blocked or self._history._blocked:
            raise HistoryError("effects_composition_blocked")
        _check_time(deadline)
        return deadline

    async def _run(self, call: Callable[[float], Coroutine[Any, Any, T]], deadline: float) -> T:
        deadline = self._admission_deadline(deadline)
        child = asyncio.create_task(call(deadline))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                value = await asyncio.shield(child)
            _check_time(deadline)
            if self._history._blocked or self._blocked:
                raise HistoryError("effects_composition_blocked")
            return value
        except BaseException as original:
            self._blocked = self._history._blocked = True
            block("history_effect_unavailable")
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
                    original.add_note("Effect cleanup completion: " + type(secondary).__name__)
            original.add_note("Effect blocked; no retry, readback or late-result promotion")
            raise

    async def _parity(self, operation: HistoryOperation, deadline: float):
        state = await operation.verify()
        facts = []
        async with pool_connection(self._tasks._pool) as conn:
            if conn.is_in_transaction():
                raise HistoryError("effects_transaction_owner_refused")
            async with conn.transaction(isolation="read_committed"):
                await self._projection.qualify(conn, writing=False, deadline=deadline)
                cursor = 0
                while True:
                    page = await self._projection.page(conn, after_seq=cursor, deadline=deadline)
                    facts.extend(page)
                    if len(page) < 32:
                        break
                    cursor = page[-1].seq
        report = compare_projections(self._projection.expected.history_id, state, tuple(facts))
        if report.missing or report.extra or report.mismatched or report.restore_required:
            raise HistoryError("effects_reconciliation_required", uncertain=True)
        return state

    @staticmethod
    def _accepted(state: Any, account: UUID, task: UUID) -> AcceptPrepare:
        matches = [
            e.command.data
            for e in state.entries
            if type(e.command.data) is AcceptPrepare
            and e.command.data.task_id == task
            and e.command.data.account_id == account
        ]
        if len(matches) != 1:
            raise HistoryError("effects_acceptance_refused")
        return matches[0]

    @staticmethod
    def _no_stop(state: Any, task: UUID) -> None:
        if any(
            type(e.command.data) is StopIntent and e.command.data.task_id == task
            for e in state.entries
        ):
            raise HistoryError("historical_stop", uncertain=True)

    async def prepare_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        job: JobBinding,
        slot: int,
        rows: list[list[str]],
        filename: str,
        *,
        deadline: float,
    ) -> tuple[IntentCommand, bytes]:
        """Prepare once; retain this packet before any separate reservation call."""
        if (
            type(publisher) is not ReservedOutputPublisher
            or publisher._pool is self._tasks._pool
            or publisher._presentation_record_limit is None
            or type(identity) is not AttemptIdentity
            or type(job) is not JobBinding
            or type(slot) is not int
            or not 0 <= slot <= 2**31 - 1
            or type(filename) is not str
            or len(filename) > 1024
        ):
            raise HistoryError("artifact_composition_refused")
        deadline = self._admission_deadline(deadline)
        identity.wire()
        job.wire()
        if (job.account_id, job.account_generation, job.task_id) != (
            identity.account_id,
            identity.generation,
            identity.task_id,
        ):
            raise HistoryError("effects_origin_refused")
        try:
            if len(filename.encode("utf-8")) > 1024:
                raise HistoryError("artifact_composition_refused")
        except UnicodeError:
            raise HistoryError("artifact_composition_refused") from None
        content = encode_csv(rows, byte_limit=8388608)
        _check_time(deadline)
        return await self._run(
            lambda limit: self._prepare_output(
                publisher, identity, job, slot, content, filename, limit
            ),
            deadline,
        )

    async def _prepare_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        job: JobBinding,
        slot: int,
        content: bytes,
        filename: str,
        deadline: float,
    ) -> tuple[IntentCommand, bytes]:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, identity.account_id, identity.task_id)
            if accepted.account_generation != identity.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, identity.task_id)
            async with publisher._reserved_transaction() as conn:
                observed = await publisher._preparation_snapshot(conn, identity)
                origin = self._tasks._origin(observed["task"])
                if (
                    origin.account_id,
                    origin.generation,
                    observed["task"]["conversation_id"],
                    observed["task"]["result_message_id"],
                ) != (
                    accepted.account_id,
                    accepted.account_generation,
                    accepted.conversation_id,
                    accepted.result_message_id,
                ):
                    raise HistoryError("effects_origin_refused")
                command = publisher._descriptors.encode(
                    job,
                    IntentBinding(
                        uuid4(),
                        slot,
                        uuid4(),
                        uuid4(),
                        identity.lifecycle_id,
                        observed["attempt_id"],
                        identity.epoch,
                        len(content),
                    ),
                    filename=filename,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
                _check_time(deadline)
            _check_time(deadline)
        _check_time(deadline)
        return command, content

    async def reserve_output(
        self,
        publisher: ReservedOutputPublisher,
        reservations: OutputReservations,
        r0_pool: asyncpg.Pool,
        identity: AttemptIdentity,
        command: IntentCommand,
        *,
        deadline: float,
    ) -> None:
        """Uncalled first-output reservation; retain this exact packet on failure."""
        if (
            type(publisher) is not ReservedOutputPublisher
            or type(reservations) is not OutputReservations
            or type(r0_pool) is not asyncpg.Pool
            or type(identity) is not AttemptIdentity
            or type(command) is not IntentCommand
            or r0_pool is self._tasks._pool
            or r0_pool is publisher._pool
            or publisher._pool is self._tasks._pool
            or publisher._presentation_record_limit is None
        ):
            raise HistoryError("artifact_composition_refused")
        deadline = self._admission_deadline(deadline)
        identity.wire()
        # Authenticate with the independently supplied backend content cipher,
        # not merely SQL's opaque-token checks or a caller-created receipt.
        publisher._descriptors.validate(command)
        if (
            command.job.account_id,
            command.job.account_generation,
            command.job.task_id,
            command.binding.lifecycle_id,
            command.binding.attempt_epoch,
        ) != (
            identity.account_id,
            identity.generation,
            identity.task_id,
            identity.lifecycle_id,
            identity.epoch,
        ):
            raise HistoryError("effects_origin_refused")
        _check_time(deadline)
        await self._run(
            lambda limit: self._reserve_output(
                publisher, reservations, r0_pool, identity, command, limit
            ),
            deadline,
        )

    async def _reserve_output(
        self,
        publisher: ReservedOutputPublisher,
        reservations: OutputReservations,
        r0_pool: asyncpg.Pool,
        identity: AttemptIdentity,
        command: IntentCommand,
        deadline: float,
    ) -> None:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, identity.account_id, identity.task_id)
            if accepted.account_generation != identity.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, identity.task_id)
            async with publisher._reserved_transaction() as conn:
                observed = await publisher._presentation_snapshot(conn, identity)
                publication_principal = await conn.fetchval("SELECT SESSION_USER::text")
                if type(publication_principal) is not str:
                    raise HistoryError("artifact_principal_refused")
                origin = self._tasks._origin(observed["task"])
                if (
                    origin.account_id,
                    origin.generation,
                    observed["task"]["conversation_id"],
                    observed["task"]["result_message_id"],
                ) != (
                    accepted.account_id,
                    accepted.account_generation,
                    accepted.conversation_id,
                    accepted.result_message_id,
                ):
                    raise HistoryError("effects_origin_refused")
                _check_time(deadline)
            # P's account/source locks and actual pool release have settled before
            # R0 acquisition. Never nest the two native account transactions.
            _check_time(deadline)
            async with pool_connection(r0_pool) as conn:
                try:
                    if conn.is_in_transaction():
                        raise HistoryError("effects_transaction_owner_refused")
                    tx = conn.transaction(isolation="read_committed")
                    await tx.start()
                    # Different pool objects can still authenticate as A/W/P.
                    # Bind this connection to a distinct direct login as well
                    # as retaining the unchanged native R0 capability preflight.
                    if (
                        await conn.fetchval(
                            "SELECT CURRENT_USER=SESSION_USER AND "
                            "NOT pg_catalog.pg_has_role(SESSION_USER,$1::name,'MEMBER') AND "
                            "NOT pg_catalog.pg_has_role(SESSION_USER,$2::name,'MEMBER') AND "
                            "NOT pg_catalog.pg_has_role(SESSION_USER,$3::name,'MEMBER')",
                            self._projection.acceptor,
                            self._projection.worker,
                            publication_principal,
                        )
                        is not True
                    ):
                        raise HistoryError("artifact_principal_refused")
                    await reservations.register_output_job(conn, identity, command.job)
                    await reservations.reserve_output_intent(conn, identity, command)
                    _check_time(deadline)
                    await tx.commit()
                except BaseException as original:
                    # Pinned asyncpg can own a separate CancelRequest even after
                    # the query raises. Drain that physical work before terminate,
                    # which otherwise cancels and detaches it. Do not send another
                    # SQL command or promote a lost COMMIT acknowledgement.
                    try:
                        native = conn._con
                        for cancellation in tuple(native._cancellations):
                            while not cancellation.done():
                                try:
                                    await asyncio.shield(cancellation)
                                except asyncio.CancelledError:
                                    continue
                                except BaseException:
                                    break
                            try:
                                cancellation.result()
                            except BaseException as secondary:
                                original.add_note(
                                    "Native reservation cancellation cleanup: "
                                    + type(secondary).__name__
                                )
                    except BaseException as secondary:
                        original.add_note(
                            "Native reservation cancellation cleanup failed: "
                            + type(secondary).__name__
                        )
                    try:
                        conn.terminate()
                    except BaseException:
                        original.add_note("Native reservation termination also failed")
                    raise
            _check_time(deadline)
        _check_time(deadline)

    async def mark(
        self,
        identity: AttemptIdentity,
        reservation: UUID,
        state: InferenceInvocation,
        *,
        effect_kind: Literal["completion", "embedding", "web_search"],
        deadline: float,
    ) -> None:
        if (
            type(identity) is not AttemptIdentity
            or type(state) is not InferenceInvocation
            or type(effect_kind) is not str
            or effect_kind not in ("completion", "embedding", "web_search")
        ):
            raise HistoryError("effects_request_refused")
        try:
            deadline = self._admission_deadline(deadline)
            state.require_send()
            record = await self._run(
                lambda limit: self._mark(identity, reservation, state, effect_kind, limit), deadline
            )
            state.require_send()
            owned = self._owned_sends.setdefault(id(state), (state, set()))
            owned[1].add(record)
            _check_time(deadline)
        except BaseException:
            state.block("history_send_unavailable")
            raise

    async def _mark(
        self,
        identity: AttemptIdentity,
        reservation: UUID,
        invocation_state: InferenceInvocation,
        effect_kind: Literal["completion", "embedding", "web_search"],
        deadline: float,
    ) -> UUID:
        record: UUID | None = None
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, identity.account_id, identity.task_id)
            if accepted.account_generation != identity.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, identity.task_id)
            owned = self._owned_sends.get(id(invocation_state))
            for e in state.entries:
                if (
                    type(e.command.data) is SendPossible
                    and e.command.data.task_id == identity.task_id
                    and (owned is None or e.command.record_id not in owned[1])
                ):
                    raise HistoryError("historical_send", uncertain=True)

            async def stage(conn: Any, result: dict[str, Any]) -> None:
                nonlocal record
                if record is not None:
                    raise HistoryError("effects_stage_refused", uncertain=True)
                prepared = await operation.append(
                    Command(
                        uuid4(),
                        SendPossible(
                            identity.account_id,
                            identity.generation,
                            identity.task_id,
                            identity.epoch,
                            identity.lifecycle_id,
                            identity.financial_scope_id,
                            identity.metadata_scope_id,
                            identity.producer_instance_id,
                            reservation,
                            UUID(result["runtime_identity"]),
                            UUID(result["dispatch_nonce"]),
                            effect_kind,
                        ),
                    )
                )
                if (
                    not prepared.created
                    or not (
                        await self._projection.stage_effect(conn, prepared.entry, deadline=deadline)
                    ).inserted
                ):
                    raise HistoryError("historical_send", uncertain=True)
                record = prepared.entry.command.record_id

            recovery = self._tasks._inference
            assert recovery is not None
            await recovery._mark_with_stage(identity, reservation, invocation_state, stage=stage)
            if record is None:
                raise HistoryError("effects_stage_refused", uncertain=True)
            invocation_state.require_send()
            _check_time(deadline)
        _check_time(deadline)
        return record

    async def request_cancel(
        self, account: UUID, task: UUID, *, key: bytes, deadline: float
    ) -> CancelOutcome:
        deadline = self._admission_deadline(deadline)
        self._projection.expected.verify_key(key)
        return await self._run(lambda limit: self._cancel(account, task, limit), deadline)

    async def _cancel(self, account: UUID, task: UUID, deadline: float) -> CancelOutcome:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, account, task)
            self._no_stop(state, task)

            async def stage(conn: Any, row: Any, outcome: CancelOutcome) -> None:
                origin = self._tasks._origin(row)
                if (
                    origin.account_id != accepted.account_id
                    or origin.generation != accepted.account_generation
                    or not outcome.accepted
                ):
                    raise HistoryError("effects_origin_refused")
                prepared = await operation.append(
                    Command(
                        uuid4(), StopIntent(account, accepted.account_generation, task, "owner")
                    )
                )
                if (
                    not prepared.created
                    or not (
                        await self._projection.stage_effect(conn, prepared.entry, deadline=deadline)
                    ).inserted
                ):
                    raise HistoryError("historical_stop", uncertain=True)

            result = await self._tasks._request_cancel_with_stage(account, task, stage=stage)
            _check_time(deadline)
        _check_time(deadline)
        return result

    async def present_reserved_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        intent_id: UUID,
        *,
        deadline: float,
    ) -> UUID:
        """Uncalled card phase for an existing publication, never file repair."""
        if (
            type(publisher) is not ReservedOutputPublisher
            or type(identity) is not AttemptIdentity
            or publisher._pool is self._tasks._pool
            or publisher._presentation_item_limit is None
            or publisher._presentation_record_limit is None
        ):
            raise HistoryError("artifact_composition_refused")
        deadline = self._admission_deadline(deadline)
        return await self._run(
            lambda limit: self._present_reserved_output(publisher, identity, intent_id, limit),
            deadline,
        )

    async def _present_reserved_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        intent_id: UUID,
        deadline: float,
    ) -> UUID:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, identity.account_id, identity.task_id)
            if accepted.account_generation != identity.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, identity.task_id)
            async with publisher._reserved_transaction() as conn:
                observed = await publisher._reserved_snapshot(conn, identity, intent_id)
                origin = self._tasks._origin(observed["task"])
                if (
                    origin.account_id,
                    origin.generation,
                    observed["task"]["conversation_id"],
                    observed["task"]["result_message_id"],
                ) != (
                    accepted.account_id,
                    accepted.account_generation,
                    accepted.conversation_id,
                    accepted.result_message_id,
                ):
                    raise HistoryError("effects_origin_refused")
                result = await publisher._present_reserved(conn, identity, intent_id)
                _check_time(deadline)
            _check_time(deadline)
        _check_time(deadline)
        return result

    async def write_partial(
        self,
        task: UUID,
        epoch: int,
        *,
        binding: Claim,
        content: str,
        delta_seq: int,
        deadline: float,
    ) -> None:
        """Guard native durable text publication, not live transport delivery."""
        deadline = self._admission_deadline(deadline)
        await self._run(
            lambda limit: self._write_partial(task, epoch, binding, content, delta_seq, limit),
            deadline,
        )

    async def _write_partial(
        self, task: UUID, epoch: int, binding: Claim, content: str, delta_seq: int, deadline: float
    ) -> None:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, binding.origin.account_id, task)
            if accepted.account_generation != binding.origin.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, task)
            # Preserve the original claim/account/lease/Stop fences and top-level
            # COMMIT. The native body owns its transaction; no result readback.
            await self._tasks.write_partial(
                task, epoch, binding=binding, content=content, delta_seq=delta_seq
            )
            _check_time(deadline)
        _check_time(deadline)

    async def complete(
        self,
        task: UUID,
        epoch: int,
        *,
        binding: Claim,
        content: str,
        deadline: float,
        message_fields: dict[str, Any] | None = None,
    ) -> TaskStatus:
        """Native terminal text/fields under H; no artifact or transport install."""
        deadline = self._admission_deadline(deadline)
        return await self._run(
            lambda limit: self._complete(task, epoch, binding, content, message_fields, limit),
            deadline,
        )

    async def _complete(
        self,
        task: UUID,
        epoch: int,
        binding: Claim,
        content: str,
        message_fields: dict[str, Any] | None,
        deadline: float,
    ) -> TaskStatus:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, binding.origin.account_id, task)
            if accepted.account_generation != binding.origin.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, task)
            result = await self._tasks.complete(
                task, epoch, content=content, message_fields=message_fields, binding=binding
            )
            _check_time(deadline)
        _check_time(deadline)
        return result

    async def publish_reserved_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        intent_id: UUID,
        content: bytes,
        *,
        deadline: float,
    ) -> Publication:
        """Uncalled backend catalog phase only; no reservation/card/transport."""
        if (
            type(publisher) is not ReservedOutputPublisher
            or type(identity) is not AttemptIdentity
            or publisher._pool is self._tasks._pool
        ):
            raise HistoryError("artifact_composition_refused")
        deadline = self._admission_deadline(deadline)
        return await self._run(
            lambda limit: self._publish_reserved_output(
                publisher, identity, intent_id, content, limit
            ),
            deadline,
        )

    async def _publish_reserved_output(
        self,
        publisher: ReservedOutputPublisher,
        identity: AttemptIdentity,
        intent_id: UUID,
        content: bytes,
        deadline: float,
    ) -> Publication:
        async with self._history.operation(deadline=deadline) as operation, retain_pool_cleanup():
            state = await self._parity(operation, deadline)
            accepted = self._accepted(state, identity.account_id, identity.task_id)
            if accepted.account_generation != identity.generation:
                raise HistoryError("effects_origin_refused")
            self._no_stop(state, identity.task_id)
            async with publisher._reserved_transaction() as conn:
                observed = await publisher._reserved_snapshot(conn, identity, intent_id)
                origin = self._tasks._origin(observed["task"])
                if (
                    origin.account_id,
                    origin.generation,
                    observed["task"]["conversation_id"],
                    observed["task"]["result_message_id"],
                ) != (
                    accepted.account_id,
                    accepted.account_generation,
                    accepted.conversation_id,
                    accepted.result_message_id,
                ):
                    raise HistoryError("effects_origin_refused")
                # Same owned P transaction retains source/account locks. The
                # publisher independently reads/admit-checks again; no supplied
                # snapshot grants filesystem effects.
                result = await publisher._publish_reserved(conn, identity, intent_id, content)
                _check_time(deadline)
            _check_time(deadline)
        _check_time(deadline)
        return result
