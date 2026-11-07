"""Uninstalled backend-only native profile for the existing packet owner.

No listener, issuer, wire codec, pool/settings creation or H owner replacement.
Binding hashes match supplied facts; they are not authentication or permits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from uuid import UUID

import asyncpg

from orchestrator.artifact_reservations import (
    IntentCommand,
    IntentBinding,
    JobBinding,
    OutputReservations,
)
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_csv import encode_csv
from orchestrator.resource_store import Publication, ReservedOutputPublisher
from orchestrator.tasks._origin import OriginalTaskOrigin
from orchestrator.tasks.store import Claim
from orchestrator.tasks.states import TaskStatus

from .effects import HistoryEffects
from .packet_owner import (
    AssignmentAuthorization,
    Established,
    NativeBudgets,
    PreparedPacket,
    _NativeProfile,
)
from .publication_protocol import ACTIONS, Action, Phase, ProtocolError, Request, Status, identifier


def _uuid(value: object) -> None:
    if not isinstance(value, UUID) or not value.int:
        raise ProtocolError("assignment_refused")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _utf8(value: str, maximum: int) -> bytes:
    if type(value) is not str or len(value) > maximum:
        raise ProtocolError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ProtocolError() from None
    if len(encoded) > maximum:
        raise ProtocolError()
    return encoded


def native_binding(
    identity: AttemptIdentity,
    job: JobBinding,
    claim: Claim,
    output_slot: int,
    worker_id: UUID,
    origin_session: UUID,
    assignment_id: UUID,
) -> bytes:
    """Pure negative fact binding, not a MAC, issuer or current native check."""
    if (
        type(identity) is not AttemptIdentity
        or type(job) is not JobBinding
        or type(claim) is not Claim
        or type(claim.origin) is not OriginalTaskOrigin
        or type(claim.task_input) is not dict
        or type(output_slot) is not int
        or not 0 <= output_slot <= 2**31 - 1
        or type(claim.worker_id) is not str
        or not 1 <= len(claim.worker_id) <= 256
        or type(claim.inference_covered) is not bool
    ):
        raise ProtocolError("assignment_refused")
    _utf8(claim.worker_id, 256)
    for value in (
        worker_id,
        origin_session,
        assignment_id,
        claim.task_id,
        claim.user_id,
        claim.conversation_id,
        claim.user_message_id,
        claim.result_message_id,
    ):
        _uuid(value)
    if (
        (job.account_id, job.account_generation, job.task_id)
        != (identity.account_id, identity.generation, identity.task_id)
        or (claim.origin.account_id, claim.origin.generation, claim.task_id, claim.epoch)
        != (identity.account_id, identity.generation, identity.task_id, identity.epoch)
        or claim.user_id != identity.account_id
        or type(claim.epoch) is not int
        or type(claim.attempt_count) is not int
        or not 0 <= claim.attempt_count <= 2**31 - 1
        or type(claim.max_attempts) is not int
        or not 1 <= claim.max_attempts <= 2**31 - 1
    ):
        raise ProtocolError("assignment_refused")
    for value in (
        identity.lifecycle_id,
        identity.financial_scope_id,
        identity.metadata_scope_id,
        identity.producer_instance_id,
        job.job_id,
        job.operation_id,
    ):
        _uuid(value)
    facts = {
        "domain": "native-publication-context-v1",
        "identity": json.loads(identity.wire()),
        "job": job.wire(),
        "slot": output_slot,
        "peer": str(worker_id),
        "session": str(origin_session),
        "assignment": str(assignment_id),
        "claim": {
            "task": str(claim.task_id),
            "epoch": claim.epoch,
            "user": str(claim.user_id),
            "conversation": str(claim.conversation_id),
            "user_message": str(claim.user_message_id),
            "result_message": str(claim.result_message_id),
            "attempt_count": claim.attempt_count,
            "max_attempts": claim.max_attempts,
            "worker": claim.worker_id,
            "covered": claim.inference_covered,
        },
    }
    return hashlib.sha256(_canonical(facts)).digest()


@dataclass(frozen=True, slots=True, repr=False)
class NativeContext:
    authorization: AssignmentAuthorization
    identity: AttemptIdentity
    job: JobBinding
    claim: Claim
    output_slot: int
    _token: object = field(default_factory=object, init=False, compare=False, repr=False)

    def binding(self) -> bytes:
        if type(self.authorization) is not AssignmentAuthorization:
            raise ProtocolError("assignment_refused")
        a = self.authorization
        return native_binding(
            self.identity,
            self.job,
            self.claim,
            self.output_slot,
            a.worker_id,
            a.origin_session,
            a.assignment_id,
        )


@dataclass(frozen=True, slots=True, repr=False)
class NativePreparation:
    rows: tuple[tuple[str, ...], ...]
    filename: str


@dataclass(frozen=True, slots=True, repr=False)
class NativeTerminal:
    content: str


@dataclass(frozen=True, slots=True, repr=False)
class NativeRequest:
    session: UUID
    handle: UUID
    request_id: UUID
    action: Action
    payload: NativePreparation | NativeTerminal | None = None


@dataclass(frozen=True, slots=True, repr=False)
class NativeInput:
    payload: NativePreparation | NativeTerminal | None
    encoded: bytes


@dataclass(frozen=True, slots=True, repr=False)
class NativePacket:
    binding: bytes
    command: IntentCommand
    content: bytes


@dataclass(frozen=True, slots=True, repr=False)
class NativeOutcome:
    phase: Phase
    value: Publication | UUID | TaskStatus | None = None


@dataclass(frozen=True, slots=True, repr=False)
class NativeReply:
    session: UUID
    handle: UUID
    request_id: UUID
    action: Action
    status: Status
    phase: Phase | None = None
    result: NativeOutcome | None = None


class NativeProfile(_NativeProfile):
    """Fixed existing H calls; no callbacks, native reconstruction or new authority."""

    def __init__(
        self,
        effects: HistoryEffects,
        publisher: ReservedOutputPublisher,
        reservations: OutputReservations,
        reservation_pool: asyncpg.Pool,
    ) -> None:
        if (
            type(effects) is not HistoryEffects
            or type(publisher) is not ReservedOutputPublisher
            or type(reservations) is not OutputReservations
            or type(reservation_pool) is not asyncpg.Pool
            or len({id(effects._tasks._pool), id(publisher._pool), id(reservation_pool)}) != 3
        ):
            raise ProtocolError("assignment_refused")
        self._effects, self._publisher = effects, publisher
        self._reservations, self._reservation_pool = reservations, reservation_pool

    @property
    def blocked(self) -> bool:
        return self._effects._blocked or self._effects._history._blocked

    @staticmethod
    def check_context(context: NativeContext) -> None:
        if type(context) is not NativeContext:
            raise ProtocolError("assignment_refused")
        authorization = context.authorization
        if (
            type(authorization) is not AssignmentAuthorization
            or type(authorization.binding) is not bytes
            or len(authorization.binding) != 32
        ):
            raise ProtocolError("assignment_refused")
        for value in (
            authorization.origin_session,
            authorization.assignment_id,
            authorization.worker_id,
            authorization.operation_id,
        ):
            identifier(value)
        if (
            context.binding() != context.authorization.binding
            or context.authorization.operation_id != context.job.operation_id
        ):
            raise ProtocolError("assignment_refused")

    @staticmethod
    def check_header(request: NativeRequest) -> None:
        if (
            type(request) is not NativeRequest
            or type(request.action) is not str
            or request.action not in ACTIONS
        ):
            raise ProtocolError()
        for value in (request.session, request.handle, request.request_id):
            identifier(value)
        expected = (
            NativePreparation
            if request.action == "prepare"
            else NativeTerminal
            if request.action == "terminal"
            else type(None)
        )
        if type(request.payload) is not expected:
            raise ProtocolError()

    @staticmethod
    def input(
        request: Request | NativeRequest, budgets: NativeBudgets
    ) -> tuple[NativeInput, bytes]:
        if type(request) is not NativeRequest:
            raise ProtocolError()
        payload = request.payload
        if type(payload) is NativePreparation:
            if (
                type(payload.rows) is not tuple
                or len(payload.rows) > 10000
                or type(payload.filename) is not str
                or len(payload.filename) > 1024
            ):
                raise ProtocolError()
            filename = _utf8(payload.filename, 1024)
            cells = characters = 0
            for row in payload.rows:
                if type(row) is not tuple or len(row) > 1000:
                    raise ProtocolError()
                cells += len(row)
                if cells > budgets.input_cells:
                    raise ProtocolError()
                for value in row:
                    if type(value) is not str:
                        raise ProtocolError()
                    characters += len(value)
                    if characters > budgets.csv_bytes:
                        raise ProtocolError()
            rows = [list(row) for row in payload.rows]
            try:
                encoded = encode_csv(rows, byte_limit=budgets.csv_bytes)
            except UnicodeError:
                raise ProtocolError() from None
            digest = hashlib.sha256(
                b"native-prepare-v1\0" + len(filename).to_bytes(4, "big") + filename + encoded
            ).digest()
        elif type(payload) is NativeTerminal:
            if type(payload.content) is not str or len(payload.content) > budgets.terminal_bytes:
                raise ProtocolError()
            encoded = _utf8(payload.content, budgets.terminal_bytes)
            digest = hashlib.sha256(b"native-terminal-v1\0" + encoded).digest()
        else:
            encoded = b""
            digest = hashlib.sha256(request.action.encode("ascii")).digest()
        return NativeInput(payload, encoded), digest

    def check_packet(
        self, context: NativeContext, packet: NativePacket, budgets: NativeBudgets
    ) -> None:
        if (
            type(packet) is not NativePacket
            or type(packet.command) is not IntentCommand
            or type(packet.command.job) is not JobBinding
            or type(packet.command.binding) is not IntentBinding
            or type(packet.binding) is not bytes
            or packet.binding != context.authorization.binding
            or type(packet.content) is not bytes
            or len(packet.content) > budgets.csv_bytes
            or packet.command.job != context.job
            or (
                packet.command.binding.lifecycle_id,
                packet.command.binding.attempt_epoch,
                packet.command.binding.output_slot,
            )
            != (context.identity.lifecycle_id, context.identity.epoch, context.output_slot)
            or packet.command.binding.measured_bytes != len(packet.content)
        ):
            raise ProtocolError("packet_result_refused")
        size = len(_canonical(packet.command.wire()))
        if size > budgets.command_bytes or size + len(packet.content) > budgets.packet_bytes:
            raise ProtocolError("packet_result_refused")
        descriptor = self._publisher._descriptors.decode(packet.command)
        if descriptor["sha256"] != hashlib.sha256(packet.content).hexdigest():
            raise ProtocolError("packet_result_refused")

    @staticmethod
    def check_outcome(result: NativeOutcome) -> None:
        if type(result) is not NativeOutcome:
            raise ProtocolError("packet_result_refused")
        value = result.value
        if result.phase in ("prepare", "reserve"):
            valid = value is None
        elif result.phase == "catalog":
            valid = False
            if type(value) is Publication:
                valid = all(
                    isinstance(key, UUID) and bool(key.int)
                    for key in (value.resource_id, value.version_id, value.reference_id)
                ) and (
                    type(value.created_at) is datetime and value.created_at.utcoffset() is not None
                )
        elif result.phase == "present":
            valid = isinstance(value, UUID) and bool(value.int)
        else:
            valid = (
                result.phase == "terminal"
                and type(value) is TaskStatus
                and value
                in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                    TaskStatus.NEEDS_ATTENTION,
                }
            )
        if not valid:
            raise ProtocolError("packet_result_refused")

    @staticmethod
    def reply(
        request: Request | NativeRequest,
        status: Status,
        phase: Phase | None,
        result: Established | NativeOutcome | None,
    ) -> NativeReply:
        if (
            type(request) is not NativeRequest
            or type(status) is not str
            or status not in {"pending", "established", "needs_attention", "retired"}
        ):
            raise ProtocolError()
        if status == "established":
            if (
                type(result) is not NativeOutcome
                or result.phase != phase
                or request.action not in ("observe", phase)
            ):
                raise ProtocolError("packet_result_refused")
            NativeProfile.check_outcome(result)
        elif result is not None or phase is not None:
            raise ProtocolError("packet_result_refused")
        return NativeReply(
            request.session,
            request.handle,
            request.request_id,
            request.action,
            status,
            phase,
            result,
        )

    async def perform(
        self,
        context: NativeContext | None,
        phase: Phase,
        data: NativeInput | None,
        packet: PreparedPacket | NativePacket | None,
        budgets: NativeBudgets,
        *,
        deadline: float,
    ) -> tuple[NativePacket, NativeOutcome]:
        if context is None or data is None:
            raise ProtocolError("assignment_refused")
        self.check_context(context)
        identity = context.identity
        if phase == "prepare":
            if type(data.payload) is not NativePreparation or packet is not None:
                raise ProtocolError()
            rows = [list(row) for row in data.payload.rows]
            command, content = await self._effects.prepare_output(
                self._publisher,
                identity,
                context.job,
                context.output_slot,
                rows,
                data.payload.filename,
                deadline=deadline,
            )
            if content != data.encoded:
                raise ProtocolError("packet_result_refused")
            packet = NativePacket(context.authorization.binding, command, content)
            result = NativeOutcome(phase)
        else:
            if type(packet) is not NativePacket:
                raise ProtocolError("packet_phase_order")
            self.check_packet(context, packet, budgets)
            if phase == "reserve":
                await self._effects.reserve_output(
                    self._publisher,
                    self._reservations,
                    self._reservation_pool,
                    identity,
                    packet.command,
                    deadline=deadline,
                )
                result = NativeOutcome(phase)
            elif phase == "catalog":
                publication = await self._effects.publish_reserved_output(
                    self._publisher,
                    identity,
                    packet.command.binding.intent_id,
                    packet.content,
                    deadline=deadline,
                )
                result = NativeOutcome(phase, publication)
            elif phase == "present":
                presentation = await self._effects.present_reserved_output(
                    self._publisher,
                    identity,
                    packet.command.binding.intent_id,
                    deadline=deadline,
                )
                result = NativeOutcome(phase, presentation)
            else:
                if type(data.payload) is not NativeTerminal:
                    raise ProtocolError()
                status = await self._effects.complete(
                    context.claim.task_id,
                    context.claim.epoch,
                    binding=context.claim,
                    content=data.payload.content,
                    deadline=deadline,
                )
                result = NativeOutcome(phase, status)
        self.check_packet(context, packet, budgets)
        self.check_outcome(result)
        if type(result.value) is Publication and (
            result.value.resource_id != packet.command.binding.resource_id
            or result.value.version_id != packet.command.binding.version_id
        ):
            raise ProtocolError("packet_result_refused")
        return packet, result
