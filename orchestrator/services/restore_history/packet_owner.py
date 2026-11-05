"""Uncalled RAM packet ownership with synthetic effects, never dispatch authority.

Admission is a trusted backend operation, absent from the worker wire protocol.
Assignment provenance must be independently authorized before this boundary.
Nothing here creates/replaces an H owner, clears its latch, or performs I/O.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import math
import os
import time
from typing import Protocol, cast
from uuid import UUID

from .publication_protocol import (
    PHASES,
    MAX_RESULT,
    Phase,
    Status,
    Request,
    Reply,
    ProtocolError,
    identifier,
    encode_request,
)


def _now() -> float:
    return time.monotonic()


def _seconds(value: object, maximum: float) -> float:
    number = _finite(value)
    if not 0 < number <= maximum:
        raise ProtocolError()
    return number


def _finite(value: object) -> float:
    if type(value) not in (int, float):
        raise ProtocolError()
    try:
        number = float(cast(int | float, value))
    except (ValueError, TypeError, OverflowError):
        raise ProtocolError() from None
    if not math.isfinite(number):
        raise ProtocolError()
    return number


def _count(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ProtocolError()
    return value


@dataclass(frozen=True, slots=True)
class Budgets:
    entries: int
    live_packets: int
    inflight: int
    packet_bytes: int
    total_packet_bytes: int
    retention_seconds: float
    effect_seconds: float

    def validate(self) -> None:
        _count(self.entries, 64)
        _count(self.live_packets, 8)
        _count(self.inflight, 4)
        _count(self.packet_bytes, 8192)
        _count(self.total_packet_bytes, 65536)
        _seconds(self.retention_seconds, 60)
        _seconds(self.effect_seconds, 2)
        if self.live_packets > self.entries or self.packet_bytes > self.total_packet_bytes:
            raise ProtocolError()


@dataclass(frozen=True, slots=True, repr=False)
class AssignmentAuthorization:
    """Synthetic trusted input, not worker-supplied authentication or a permit.

    assignment_id is the immutable ORIGINAL assignment identity. binding covers
    its original native account/generation/attempt/job/slot facts. origin_session
    is immutable issuance provenance, not a value a worker may refresh.
    """

    origin_session: UUID
    assignment_id: UUID
    worker_id: UUID
    operation_id: UUID
    binding: bytes
    expires_at: float


@dataclass(frozen=True, slots=True)
class AuthenticatedWorker:
    """Out-of-band synthetic authentication fact; never decoded from a request."""

    identity: UUID


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPacket:
    binding: bytes
    command: bytes
    content: bytes


@dataclass(frozen=True, slots=True, repr=False)
class Established:
    """Trusted effect returned only after known COMMIT and physical cleanup."""

    result: bytes


class SyntheticEffects(Protocol):
    """Fixed fake-effect surface, not generic callback or production composition.

    Awaitable completion must include ALL underlying work/cleanup. This prototype
    cannot discover a library's detached physical workers or establish DB fences.
    """

    @property
    def blocked(self) -> bool: ...
    async def prepare(
        self, assignment: AssignmentAuthorization, body: bytes, *, deadline: float
    ) -> tuple[PreparedPacket, Established]: ...
    async def reserve(
        self, assignment: AssignmentAuthorization, packet: PreparedPacket, *, deadline: float
    ) -> Established: ...
    async def catalog(
        self, assignment: AssignmentAuthorization, packet: PreparedPacket, *, deadline: float
    ) -> Established: ...
    async def present(
        self, assignment: AssignmentAuthorization, packet: PreparedPacket, *, deadline: float
    ) -> Established: ...
    async def terminal(
        self, assignment: AssignmentAuthorization, packet: PreparedPacket, *, deadline: float
    ) -> Established: ...


@dataclass(slots=True)
class _Entry:
    authorization: AssignmentAuthorization
    handle: UUID
    expires_at: float
    packet: PreparedPacket | None = None
    results: dict[Phase, Established] = field(default_factory=dict)
    inputs: dict[Phase, bytes] = field(default_factory=dict)
    work: asyncio.Task[tuple[PreparedPacket | None, Established]] | None = None
    caller: asyncio.Task[object] | None = None
    pending: Phase | None = None
    abandoned: bool = False
    retired: bool = False
    retire_requested: bool = False
    reserved: bool = False
    effect_started: bool = False


class PacketOwner:
    """One trusted backend incarnation; bounded, monotonic, loop/PID-owned RAM.

    No reconstruction API exists. A future host must create one fresh incarnation
    and must never reissue old assignment provenance. Replacing an H/writer owner
    to evade failure remains forbidden. These synthetic facts do not enforce an
    untrusted in-process Python caller or authenticate actual service identities.
    """

    def __init__(self, session: UUID, effects: SyntheticEffects, budgets: Budgets) -> None:
        identifier(session)
        if type(budgets) is not Budgets:
            raise ProtocolError()
        budgets.validate()
        self._session, self._effects, self._budgets = session, effects, budgets
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._entries: dict[UUID, _Entry] = {}
        self._assignments: dict[UUID, _Entry] = {}
        self._inflight = 0
        self._blocked = self._closing = self._closed = False

    @property
    def session(self) -> UUID:
        return self._session

    @property
    def effects(self) -> SyntheticEffects:
        return self._effects

    @property
    def budgets(self) -> Budgets:
        return self._budgets

    def _unavailable(self) -> bool:
        try:
            if self.effects.blocked is not False:
                self._blocked = True
        except BaseException:
            self._blocked = True
            raise
        return self._blocked

    def _bind(self) -> None:
        if self._pid != os.getpid():
            raise ProtocolError("packet_owner_forked")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ProtocolError("packet_owner_wrong_loop")

    @property
    def held_bytes(self) -> int:
        """Reserved packet bytes only; bounded result/tombstone metadata is separate."""
        self._bind()
        return sum(self.budgets.packet_bytes for e in self._entries.values() if e.reserved)

    def admit(self, authorization: AssignmentAuthorization, handle: UUID) -> None:
        """Trusted backend-only admission. There is deliberately no wire opcode."""
        self._bind()
        if self._closing or self._unavailable():
            raise ProtocolError("packet_owner_blocked")
        if type(authorization) is not AssignmentAuthorization:
            raise ProtocolError()
        for value in (
            authorization.origin_session,
            authorization.assignment_id,
            authorization.worker_id,
            authorization.operation_id,
            handle,
        ):
            identifier(value)
        if (
            authorization.origin_session != self.session
            or type(authorization.binding) is not bytes
            or len(authorization.binding) != 32
        ):
            raise ProtocolError("assignment_refused")
        if _finite(authorization.expires_at) <= _now():
            raise ProtocolError("assignment_refused")
        previous = self._assignments.get(authorization.assignment_id)
        if previous is not None:
            if previous.authorization != authorization or previous.handle != handle:
                raise ProtocolError("assignment_conflict")
            return  # never renew expiry or discard the original tombstone
        if handle in self._entries:
            raise ProtocolError("assignment_conflict")
        if len(self._entries) >= self.budgets.entries:
            raise ProtocolError("packet_tombstones_exhausted")
        entry = _Entry(
            authorization,
            handle,
            min(authorization.expires_at, _now() + self.budgets.retention_seconds),
        )
        self._entries[handle] = self._assignments[authorization.assignment_id] = entry

    def _drop(self, entry: _Entry) -> None:
        if entry.pending is not None or entry.work is not None:
            raise ProtocolError("packet_work_owned")
        entry.packet = None
        entry.results.clear()
        entry.reserved = False
        entry.retired = True

    def expire(self) -> None:
        self._bind()
        for entry in self._entries.values():
            if _now() >= entry.expires_at:
                if entry.pending is not None:
                    entry.abandoned = self._blocked = True
                    entry.retire_requested = True
                else:
                    self._drop(entry)

    def _reply(
        self,
        request: Request,
        status: Status,
        phase: Phase | None = None,
        result: bytes | None = None,
    ) -> Reply:
        return Reply(
            self.session, request.handle, request.request_id, request.action, status, phase, result
        )

    async def handle(
        self, peer: AuthenticatedWorker, request: Request, *, deadline: float
    ) -> Reply:
        self._bind()
        encode_request(request)  # identical validation for direct and framed callers
        deadline = _finite(deadline)
        if deadline <= _now():
            raise ProtocolError("packet_deadline")
        if type(peer) is not AuthenticatedWorker:
            raise ProtocolError("assignment_refused")
        identifier(peer.identity)
        entry = self._entries.get(request.handle)
        if (
            request.session != self.session
            or entry is None
            or entry.authorization.worker_id != peer.identity
            or entry.authorization.operation_id != request.request_id
        ):
            raise ProtocolError("assignment_refused")
        if entry.caller is not None and entry.caller.cancelling():
            entry.abandoned = self._blocked = True
        self.expire()
        if request.action == "retire":
            entry.retire_requested = True
            if entry.pending is not None:
                entry.abandoned = self._blocked = True
                return self._reply(request, "pending")
            self._drop(entry)
            return self._reply(request, "retired")
        if request.action == "observe":
            if entry.pending is not None:
                return self._reply(request, "pending")
            if entry.abandoned or self._unavailable():
                return self._reply(request, "needs_attention")
            if entry.retired:
                return self._reply(request, "retired")
            if entry.results:
                phase = PHASES[len(entry.results) - 1]
                return self._reply(request, "established", phase, entry.results[phase].result)
            return self._reply(request, "pending")
        if self._closing or entry.abandoned or self._unavailable():
            raise ProtocolError("packet_owner_blocked")
        if entry.retired:
            raise ProtocolError("packet_retired")
        phase = cast(Phase, request.action)
        digest = hashlib.sha256(request.payload).digest()
        if phase in entry.inputs and entry.inputs[phase] != digest:
            raise ProtocolError("assignment_conflict")
        if entry.pending is not None:
            if entry.pending != phase:
                raise ProtocolError("packet_phase_order")
            return self._reply(request, "pending")
        if phase in entry.results:
            return self._reply(request, "established", phase, entry.results[phase].result)
        if len(entry.results) >= len(PHASES) or PHASES[len(entry.results)] != phase:
            raise ProtocolError("packet_phase_order")
        if self._inflight >= self.budgets.inflight:
            raise ProtocolError("packet_busy")
        limit = min(deadline, entry.expires_at, _now() + self.budgets.effect_seconds)
        if limit <= _now():
            raise ProtocolError("packet_deadline")
        if phase == "prepare":
            live = sum(e.reserved for e in self._entries.values())
            if (
                live >= self.budgets.live_packets
                or self.held_bytes + self.budgets.packet_bytes > self.budgets.total_packet_bytes
            ):
                raise ProtocolError("packet_capacity_exhausted")
        caller = asyncio.current_task()
        if caller is None or caller.cancelling():
            raise ProtocolError("packet_caller_cancelled")
        ready = asyncio.Event()
        entry.pending, entry.inputs[phase] = phase, digest
        entry.caller = caller
        entry.effect_started = False
        if phase == "prepare":
            entry.reserved = True  # worst-case bytes reserved BEFORE physical work
        self._inflight += 1
        call = None
        try:
            call = self._drive(entry, request, phase, limit, ready)
            work = asyncio.create_task(call)
        except BaseException as original:
            entry.abandoned = self._blocked = True
            if call is not None:
                try:
                    call.close()
                except BaseException as cleanup:
                    original.add_note("Unstarted packet operation: " + type(cleanup).__name__)
            self._finish(entry, original)
            raise
        entry.work = work
        failure: BaseException | None = None
        try:
            ready.set()  # eager task factories must also yield before any effect
            async with asyncio.timeout_at(limit):
                packet, result = await asyncio.shield(work)
            if _now() >= limit or self._closing or self._unavailable() or caller.cancelling():
                raise ProtocolError("packet_outcome_uncertain")
            entry.packet, entry.results[phase] = packet, result
            return self._reply(request, "established", phase, result.result)
        except BaseException as original:
            failure = original
            entry.abandoned = self._blocked = True
            if not entry.effect_started and not work.done():
                work.cancel()  # only a logical start gate, never running physical work
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                work.result()
            except BaseException as secondary:
                if secondary is not original:
                    original.add_note("Packet cleanup: " + type(secondary).__name__)
            raise
        finally:
            self._finish(entry, failure)

    def _finish(self, entry: _Entry, original: BaseException | None) -> None:
        self._inflight -= 1
        entry.work, entry.pending, entry.caller = None, None, None
        try:
            if entry.retire_requested or self._closing or _now() >= entry.expires_at:
                self._drop(entry)
            elif entry.packet is None:
                entry.reserved = False
        except BaseException as cleanup:
            entry.abandoned = self._blocked = True
            if original is None:
                raise
            original.add_note("Packet settlement: " + type(cleanup).__name__)

    async def _drive(
        self, entry: _Entry, request: Request, phase: Phase, deadline: float, ready: asyncio.Event
    ) -> tuple[PreparedPacket | None, Established]:
        try:
            await ready.wait()
            if (
                entry.abandoned
                or self._closing
                or self._unavailable()
                or _now() >= deadline
                or (entry.caller is not None and entry.caller.cancelling())
            ):
                raise ProtocolError("packet_outcome_uncertain")
            entry.effect_started = True
            packet = entry.packet
            if phase == "prepare":
                packet, result = await self.effects.prepare(
                    entry.authorization, request.payload, deadline=deadline
                )
                if (
                    type(packet) is not PreparedPacket
                    or type(packet.binding) is not bytes
                    or packet.binding != entry.authorization.binding
                    or type(packet.command) is not bytes
                    or type(packet.content) is not bytes
                    or len(packet.command) + len(packet.content) > self.budgets.packet_bytes
                ):
                    raise ProtocolError("packet_result_refused")
            else:
                if packet is None:
                    raise ProtocolError("packet_phase_order")
                if phase == "reserve":
                    result = await self.effects.reserve(
                        entry.authorization, packet, deadline=deadline
                    )
                elif phase == "catalog":
                    result = await self.effects.catalog(
                        entry.authorization, packet, deadline=deadline
                    )
                elif phase == "present":
                    result = await self.effects.present(
                        entry.authorization, packet, deadline=deadline
                    )
                else:
                    result = await self.effects.terminal(
                        entry.authorization, packet, deadline=deadline
                    )
            if (
                type(result) is not Established
                or type(result.result) is not bytes
                or len(result.result) > MAX_RESULT
            ):
                raise ProtocolError("packet_result_refused")
            if (
                entry.abandoned
                or self._closing
                or self._unavailable()
                or _now() >= deadline
                or (entry.caller is not None and entry.caller.cancelling())
            ):
                raise ProtocolError("packet_outcome_uncertain")
            return packet, result  # caller must still settle before establishing it
        except BaseException:
            entry.abandoned = self._blocked = True
            raise

    def close_admission(self) -> None:
        """Call synchronously before scheduling shutdown; never evict owned work."""
        self._bind()
        self._closing = True
        for entry in self._entries.values():
            if entry.pending is not None:
                entry.abandoned = self._blocked = True

    async def aclose(self) -> None:
        self.close_admission()
        if any(entry.work is asyncio.current_task() for entry in self._entries.values()):
            raise ProtocolError("packet_owner_reentrant")
        original: BaseException | None = None
        works = []
        for entry in self._entries.values():
            if entry.work is not None:
                entry.abandoned = self._blocked = True
                works.append((entry.work, entry))
        for work, entry in works:
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError as exc:
                    if original is None:
                        original = exc
                except BaseException as exc:
                    if original is None:
                        original = exc
            try:
                work.result()
            except BaseException as exc:
                if original is None:
                    original = exc
            # Physical work is already done. Allow its caller to finish the
            # non-awaiting bookkeeping; no fallible notification can strand us.
            while entry.pending is not None:
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError as exc:
                    if original is None:
                        original = exc
        for entry in self._entries.values():
            try:
                self._drop(entry)
            except BaseException as cleanup:
                entry.abandoned = self._blocked = True
                if original is None:
                    original = cleanup
                elif cleanup is not original:
                    original.add_note("Packet shutdown: " + type(cleanup).__name__)
        self._closed = True
        if original is not None:
            raise original
