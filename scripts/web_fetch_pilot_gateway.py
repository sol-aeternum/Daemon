"""Offline injected-transport gateway actor; NOT a live/network adapter.

Admission is gateway-authoritative. Inventory parsing checks shape, not snapshot
freshness/completeness: the future reviewed supervisor must establish those before
constructing this actor. There is no preflight, CLI, socket, DNS or process adapter.

FrameIO.send must call its synchronous commit callback exactly once, immediately
after the COMPLETE frame is published, BEFORE local incoming-ACK processing can
run. It cannot prevent a remote process from observing already written bytes.
Returning means the local output buffer was consumed. This local linearization
lets early WINDOWs observe emitted DATA without acknowledging merely queued DATA.
Adapters unable to provide it need a separately reviewed deferred-ACK adapter.
All injected operations must cooperate with cancellation; connector cancellation
must reclaim connections not yet returned. Connection.close is synchronous,
nonblocking and idempotent. read/write report actual TCP payload counts, not buffer
acceptance. Cancellation/errors with unknown counts never refund reservations.
Cleanup waits at most two seconds after cancellation. Any surviving tasks remain
owned and visible in pending_tasks, and the outcome requires supervisor teardown.
This actor cannot kill Python tasks or certify container removal. Synchronous
close/accessor/callback methods must not block the event loop.

Queues are bounded (four slots per active stream, 16 KiB per DATA); a round-robin
sender services one frame per stream per turn. Each stream has at most one reader,
one writer and one admission task. No mutation lock spans an I/O await. Real pipe,
numeric sockaddr, DNS-thread cleanup, TLS and containment remain unqualified.
"""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Protocol

from scripts.web_fetch_pilot_core import (
    MAX_FRAMES,
    MAX_PAYLOAD,
    BudgetError,
    ByteBudget,
    DestinationPolicyError,
    Frame,
    FrameCodecError,
    FrameParser,
    FrameType,
    Reservation,
    encode_frame,
    parse_owned_inventory,
    validate_dns_answers,
)
from scripts.web_fetch_pilot_tunnels import (
    Peer,
    TunnelLedger,
    TunnelLedgerError,
    TunnelSnapshot,
    TunnelStatus,
)


class FrameIO(Protocol):
    async def receive(self) -> Frame | None: ...

    async def send(self, frame: Frame, commit: Callable[[], None], /) -> None: ...

    def close(self) -> None: ...


class Connection(Protocol):
    @property
    def peer_ip(self) -> str: ...

    def getpeername(self) -> tuple[str, int]:
        """Actual connected IPv4 sockaddr, derived from the socket by the adapter."""
        ...

    async def read(self, maxsize: int) -> bytes: ...

    async def write(self, data: bytes) -> int: ...

    async def shutdown_write(self) -> None: ...

    def close(self) -> None: ...


Resolver = Callable[[str], Awaitable[Sequence[str]]]
Connector = Callable[[str, int], Awaitable[Connection]]


class EndReason(Enum):
    EOF = "peer_eof"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"
    PROTOCOL = "protocol"
    IO = "transport_error"
    BUDGET = "byte_budget"


@dataclass(frozen=True)
class GatewayOutcome:
    """Terminal, incomplete run outcome; never a browser/result success marker."""

    reason: EndReason
    received_frames: int
    sent_frames: int
    read_bytes: int
    written_bytes: int
    unresolved_bytes: int
    cleanup_failed: bool
    pending_tasks: int


class GatewayFailure(Exception):
    def __init__(self, reason: EndReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass
class _Output:
    frame: Frame
    done: asyncio.Future[None]
    committed: bool = False


@dataclass
class _Stream:
    stream_id: int
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    output: asyncio.Queue[_Output] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    connection: Connection | None = None
    approved_ip: str | None = None
    sink_eof: bool = False
    closing: bool = False
    aborted: bool = False
    emissions: int = 0


class Gateway:
    """One non-reusable actor, with production-default fixed shared byte budget.

    ``budget`` injection is solely an offline unit-test seam. A future production
    adapter must assert default limits, qualified transport and fresh inventory;
    this module offers no live flag or fixture-address policy exception.
    """

    def __init__(
        self,
        manifest: Sequence[str],
        inventory: Sequence[str] | None,
        frame_io: FrameIO,
        resolver: Resolver,
        connector: Connector,
        *,
        deadline: float = 45.0,
        budget: ByteBudget | None = None,
        cleanup_grace: float = 2.0,
    ) -> None:
        if not 0 < deadline <= 45:
            raise ValueError("deadline must be within the pilot ceiling")
        if not 0 < cleanup_grace <= 2:
            raise ValueError("cleanup grace must be within two seconds")
        self._cleanup_grace = cleanup_grace
        self.ledger = TunnelLedger(manifest)
        self._inventory = parse_owned_inventory(inventory)
        self._io = frame_io
        self._resolver = resolver
        self._connector = connector
        self._deadline = deadline
        self._budget = budget if budget is not None else ByteBudget()
        self._streams: dict[int, _Stream] = {}
        self._refused: set[int] = set()
        self._ready = asyncio.Event()
        self._order: deque[int] = deque()
        self._tasks: set[asyncio.Task[None]] = set()
        self._end: asyncio.Future[EndReason] | None = None
        self._received = 0
        self._sent = 0
        self._used = False
        self._stopping = False

    @property
    def budget(self) -> ByteBudget:
        return self._budget

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Retained ownership after failed cleanup; never imply no survivors."""
        return tuple(task for task in self._tasks if not task.done())

    def _finish(self, reason: EndReason) -> None:
        if self._end is not None and not self._end.done():
            self._stopping = True
            self._end.set_result(reason)

    def _spawn(self, work: Callable[[], Awaitable[None]], stream: _Stream | None = None) -> None:
        async def guarded() -> None:
            try:
                await work()
            except asyncio.CancelledError:
                if not self._stopping and (stream is None or not stream.aborted):
                    self._finish(EndReason.IO)
                raise
            except (TunnelLedgerError, FrameCodecError):
                self._finish(EndReason.PROTOCOL)
            except BudgetError:
                self._finish(EndReason.BUDGET)
            except GatewayFailure as exc:
                self._finish(exc.reason)
            except Exception:
                self._finish(EndReason.IO)

        task = asyncio.create_task(guarded(), name="pilot-gateway")
        self._tasks.add(task)
        if stream is not None:
            stream.tasks.add(task)
            task.add_done_callback(stream.tasks.discard)
        task.add_done_callback(self._tasks.discard)

    async def run(self) -> GatewayOutcome:
        if self._used:
            raise RuntimeError("gateway actors cannot be reused")
        self._used = True
        self._end = asyncio.get_running_loop().create_future()
        self._spawn(self._receive)
        self._spawn(self._send)
        try:
            async with asyncio.timeout(self._deadline):
                reason = await asyncio.shield(self._end)
        except TimeoutError:
            reason = EndReason.DEADLINE
        except asyncio.CancelledError:
            reason = EndReason.CANCELLED
        self._stopping = True
        self._budget.close(reason.value)
        cleanup_failed = False
        try:
            self._io.close()
        except Exception:
            cleanup_failed = True
        for stream in self._streams.values():
            try:
                self._close_connection(stream)
            except Exception:
                cleanup_failed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        # wait() does not wait indefinitely for cancellation to be acknowledged.
        # Keep pending tasks registered: a timeout is an explicit supervisor
        # teardown requirement, not permission to detach and report success.
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=self._cleanup_grace)
            except asyncio.CancelledError:
                cleanup_failed = True
        pending = len(self.pending_tasks)
        cleanup_failed = cleanup_failed or pending > 0
        for stream in self._streams.values():
            while not stream.output.empty():
                stream.output.get_nowait().done.cancel()
        return GatewayOutcome(
            reason,
            self._received,
            self._sent,
            self._budget.charged_read,
            self._budget.charged_write,
            self._budget.outstanding,
            cleanup_failed,
            pending,
        )

    @staticmethod
    def _shape(frame: Frame) -> None:
        if (
            type(frame) is not Frame
            or type(frame.frame_type) is not FrameType
            or type(frame.stream_id) is not int
            or type(frame.payload) is not bytes
        ):
            raise GatewayFailure(EndReason.PROTOCOL)
        try:
            parser = FrameParser()
            list(parser.feed(encode_frame(frame.frame_type, frame.stream_id, frame.payload)))
            parser.end_of_stream()
        except (ValueError, TypeError, struct.error, FrameCodecError) as exc:
            raise GatewayFailure(EndReason.PROTOCOL) from exc

    def _snapshot(self, stream: _Stream) -> TunnelSnapshot:
        for tunnel in self.ledger.snapshot().tunnels:
            if tunnel.stream_id == stream.stream_id:
                return tunnel
        raise GatewayFailure(EndReason.PROTOCOL)

    def _new_stream(self, stream_id: int) -> _Stream:
        stream = _Stream(stream_id)
        self._streams[stream_id] = stream
        self._order.append(stream_id)
        return stream

    async def _receive(self) -> None:
        while not self._stopping:
            frame = await self._io.receive()
            if frame is None:
                self._finish(EndReason.EOF)
                return
            self._received += 1
            if self._received > MAX_FRAMES:
                raise GatewayFailure(EndReason.PROTOCOL)
            self._shape(frame)
            if frame.frame_type not in (
                FrameType.OPEN,
                FrameType.DATA,
                FrameType.WINDOW,
                FrameType.HALF_CLOSE,
                FrameType.CLOSE,
            ):
                raise GatewayFailure(EndReason.PROTOCOL)
            admitted = self.ledger.handle(Peer.BROWSER, frame)
            if frame.frame_type is FrameType.OPEN:
                stream = self._new_stream(frame.stream_id)
                if not admitted:
                    await self._emit(stream, FrameType.OPEN_ERROR, b"policy refused")
                    self._retire(stream)
                else:
                    self._spawn(
                        partial(self._admit, stream, frame.payload[:-4].decode("ascii")), stream
                    )
                continue
            stream = self._streams.get(frame.stream_id)
            if stream is None:
                raise GatewayFailure(EndReason.PROTOCOL)
            if frame.frame_type is FrameType.CLOSE:
                stream.aborted = True
                self._close_connection(stream)
                for task in tuple(stream.tasks):
                    task.cancel()
                # A pending CLOSE cancels resolver/connect before capacity is
                # recycled; tasks retain their own stream object, never its ID.
                await asyncio.gather(*tuple(stream.tasks), return_exceptions=True)
                if stream.emissions:
                    raise GatewayFailure(EndReason.PROTOCOL)
                self._retire(stream)
            else:
                stream.wake.set()
                self._maybe_close(stream)

    async def _admit(self, stream: _Stream, host: str) -> None:
        connection: Connection | None = None
        try:
            try:
                async with asyncio.timeout(3):
                    answers = await self._resolver(host)
                # Reject exotic/mutable answer containers before policy iteration.
                if type(answers) not in (list, tuple) or any(type(x) is not str for x in answers):
                    raise DestinationPolicyError("invalid resolver answer shape")
                candidates = validate_dns_answers(answers, self._inventory)
                async with asyncio.timeout(3):
                    for ip in candidates:
                        try:
                            connection = await self._connector(ip, 443)
                        except (OSError, TimeoutError):
                            continue
                        self._verify_peer(connection, ip)
                        stream.approved_ip = ip
                        break
                if connection is None:
                    raise OSError("connect failed")
            except (DestinationPolicyError, OSError, TimeoutError):
                if connection is not None:
                    connection.close()
                    connection = None
                if not stream.aborted:
                    await self._emit(stream, FrameType.OPEN_ERROR, b"destination unavailable")
                    self._retire(stream)
                return
            if stream.aborted or self._stopping:
                return
            stream.connection = connection
            connection = None  # stream now owns cleanup
            await self._emit(stream, FrameType.OPEN_OK)
            if not stream.aborted:
                self._spawn(partial(self._read, stream), stream)
                self._spawn(partial(self._write, stream), stream)
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _verify_peer(connection: Connection, ip: str) -> None:
        actual = connection.peer_ip
        if type(actual) is not str or actual != ip:
            raise DestinationPolicyError("connected peer does not match numeric candidate")
        getpeername = getattr(connection, "getpeername", None)
        if not callable(getpeername):
            raise DestinationPolicyError("connected sockaddr accessor is required")
        peer = getpeername()
        if (
            type(peer) is not tuple
            or len(peer) != 2
            or type(peer[0]) is not str
            or peer[0] != ip
            or type(peer[1]) is not int
            or peer[1] != 443
        ):
            raise DestinationPolicyError("peer sockaddr does not match numeric candidate")

    def _connection(self, stream: _Stream) -> Connection:
        connection = stream.connection
        if connection is None or stream.approved_ip is None:
            raise GatewayFailure(EndReason.IO)
        try:
            self._verify_peer(connection, stream.approved_ip)
        except DestinationPolicyError as exc:
            raise GatewayFailure(EndReason.IO) from exc
        return connection

    def _reserve(self, amount: int, direction: str) -> tuple[Reservation, int]:
        amount = min(amount, self._budget.remaining)
        if amount <= 0 or self._budget.is_closed:
            self._budget.close("no reservable bytes")
            raise GatewayFailure(EndReason.BUDGET)
        return self._budget.reserve(amount, direction), amount

    async def _transfer_read(self, stream: _Stream, amount: int) -> bytes:
        connection = self._connection(stream)
        token, amount = self._reserve(amount, "read")
        try:
            data = await connection.read(amount)
            self._connection(stream)
            if type(data) is not bytes or len(data) > amount:
                raise GatewayFailure(EndReason.IO)
        except BaseException:
            self._budget.close("read count unknown")
            self._finish(EndReason.IO)
            raise
        self._budget.reconcile(token, len(data))
        if self._budget.is_closed:
            raise GatewayFailure(EndReason.BUDGET)
        return data

    async def _transfer_write(self, stream: _Stream, data: bytes) -> int:
        connection = self._connection(stream)
        token, amount = self._reserve(len(data), "write")
        try:
            actual = await connection.write(data[:amount])
            self._connection(stream)
            if type(actual) is not int or not 0 <= actual <= amount:
                raise GatewayFailure(EndReason.IO)
        except BaseException:
            self._budget.close("write count unknown")
            self._finish(EndReason.IO)
            raise
        self._budget.reconcile(token, actual)
        if self._budget.is_closed:
            raise GatewayFailure(EndReason.BUDGET)
        if actual == 0:
            raise GatewayFailure(EndReason.IO)
        return actual

    async def _read(self, stream: _Stream) -> None:
        while not stream.aborted:
            stream.wake.clear()
            half = self._snapshot(stream).gateway
            if half is None:
                return
            if not half.send_credit:
                await stream.wake.wait()
                continue
            data = await self._transfer_read(stream, min(MAX_PAYLOAD, half.send_credit))
            if not data:
                await self._emit(stream, FrameType.HALF_CLOSE)
                self._maybe_close(stream)
                return
            # Reserve sender credit synchronously before queued publication.
            self.ledger.handle(Peer.GATEWAY, Frame(FrameType.DATA, stream.stream_id, data))
            await self._emit(stream, FrameType.DATA, data)

    async def _write(self, stream: _Stream) -> None:
        while not stream.aborted:
            stream.wake.clear()
            half = self._snapshot(stream).browser
            if half is None:
                return
            if half.queued_chunks:
                actual = await self._transfer_write(stream, half.queued_chunks[0])
                self.ledger.drain(Peer.BROWSER, stream.stream_id, actual)
                await self._emit(stream, FrameType.WINDOW, struct.pack("!I", actual))
                self._maybe_close(stream)
            elif half.half_closed:
                await self._connection(stream).shutdown_write()
                stream.sink_eof = True
                self._maybe_close(stream)
                return
            else:
                await stream.wake.wait()

    async def _emit(self, stream: _Stream, kind: FrameType, payload: bytes = b"") -> None:
        if stream.aborted or self._stopping:
            raise GatewayFailure(EndReason.PROTOCOL)
        row = _Output(
            Frame(kind, stream.stream_id, payload), asyncio.get_running_loop().create_future()
        )
        stream.emissions += 1
        try:
            await stream.output.put(row)
            self._ready.set()
            await row.done
        finally:
            stream.emissions -= 1

    def _commit(self, stream: _Stream, row: _Output) -> None:
        if row.committed or stream.aborted or self._stopping:
            raise GatewayFailure(EndReason.PROTOCOL)
        frame = row.frame
        if frame.frame_type is FrameType.CLOSE:
            # Release the actual socket BEFORE publishing terminal capacity.
            self._close_connection(stream)
        if frame.frame_type is FrameType.DATA:
            self.ledger.drain(Peer.GATEWAY, stream.stream_id, len(frame.payload))
        elif frame.frame_type is FrameType.OPEN_ERROR and not any(
            t.stream_id == stream.stream_id for t in self.ledger.snapshot().tunnels
        ):
            attempts = self.ledger.snapshot().attempts
            if stream.stream_id in self._refused or not any(
                a.stream_id == stream.stream_id and not a.accepted for a in attempts
            ):
                raise GatewayFailure(EndReason.PROTOCOL)
            self._refused.add(stream.stream_id)
        else:
            self.ledger.handle(Peer.GATEWAY, frame)
        row.committed = True
        self._sent += 1
        stream.wake.set()

    async def _send(self) -> None:
        while not self._stopping:
            self._ready.clear()
            selected: _Stream | None = None
            for _ in range(len(self._order)):
                stream_id = self._order[0]
                self._order.rotate(-1)
                stream = self._streams[stream_id]
                if not stream.output.empty():
                    selected = stream
                    break
            if selected is None:
                await self._ready.wait()
                continue
            row = selected.output.get_nowait()
            if self._sent >= MAX_FRAMES:
                raise GatewayFailure(EndReason.PROTOCOL)
            self._shape(row.frame)
            try:
                await self._io.send(row.frame, partial(self._commit, selected, row))
                if not row.committed:
                    raise GatewayFailure(EndReason.PROTOCOL)
                row.done.set_result(None)
            finally:
                if not row.done.done():
                    row.done.cancel()

    def _maybe_close(self, stream: _Stream) -> None:
        if stream.closing or stream.aborted or stream.emissions:
            return
        snap = self._snapshot(stream)
        if snap.status is not TunnelStatus.OPEN or not stream.sink_eof:
            return
        halves = (snap.browser, snap.gateway)
        if any(
            half is None
            or not half.half_closed
            or half.queued_bytes
            or half.drained != half.credited
            for half in halves
        ):
            return
        stream.closing = True
        self._spawn(partial(self._normal_close, stream), stream)

    async def _normal_close(self, stream: _Stream) -> None:
        await self._emit(stream, FrameType.CLOSE)
        self._close_connection(stream)
        self._retire(stream)

    @staticmethod
    def _close_connection(stream: _Stream) -> None:
        if stream.connection is not None:
            connection = stream.connection
            stream.connection = None
            connection.close()

    def _retire(self, stream: _Stream) -> None:
        if stream.output.qsize():
            raise GatewayFailure(EndReason.PROTOCOL)
        self._streams.pop(stream.stream_id, None)
        self._order.remove(stream.stream_id)
