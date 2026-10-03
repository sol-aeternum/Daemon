"""Injected, offline CONNECT relay actor; no listener, browser or OS adapter.

The gateway alone admits destinations. This endpoint owns bounded local queues
and credits, not a second TunnelLedger or the gateway's TCP ByteBudget. Counters
here bound aggregate IPC DATA, never decoded content or TCP-wire traffic.

FrameIO has the gateway's publication contract: commit exactly once after full
publication, before LOCAL incoming ACK processing can run; return after local
buffer consumption. This is not a remote physical-observation guarantee.
Connection operations report actual partial counts and cooperate with cancel;
close/getpeername and acceptor.close must be synchronous and nonblocking. The
future kernel adapter must qualify getpeername and assert network-none and an
actual loopback-only listener before construction. This module cannot prove them.

Unknown partial writes fail closed, without WINDOW. Cleanup is time-bounded;
surviving tasks remain owned in pending_tasks for supervisor teardown. Every run
ends INCOMPLETE: browser extraction and supervisor completion are separate work.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Protocol

from scripts.web_fetch_pilot_core import (
    CONNECT_HEADER_LIMIT,
    MAX_FRAMES,
    MAX_PAYLOAD,
    MAX_STREAM_ID,
    TOTAL_NETWORK_LIMIT,
    ConnectHeaderError,
    Frame,
    FrameCodecError,
    FrameParser,
    FrameType,
    encode_frame,
    parse_connect_headers,
)
from scripts.web_fetch_pilot_gateway import Connection, EndReason, FrameIO

CREDIT = 64 * 1024
RESULT_LIMIT = 1024 * 1024  # Matches the supervisor collector's RESULT quotas.
FINAL_LIMIT = 4096
HTTP_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def _http_error(code: int, phrase: bytes) -> bytes:
    return (
        b"HTTP/1.1 "
        + str(code).encode("ascii")
        + b" "
        + phrase
        + b"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
    )


HTTP_BAD = _http_error(400, b"Bad Request")
HTTP_BUSY = _http_error(503, b"Service Unavailable")
HTTP_REFUSED = _http_error(502, b"Bad Gateway")


class LocalAcceptor(Protocol):
    """Injected accept; cancellation reclaims connections not yet returned."""

    async def accept(self) -> Connection | None: ...

    def close(self) -> None: ...


class RelayStatus(Enum):
    INCOMPLETE = "incomplete"
    # The final RESULT frame was committed and the run ended in order. Never
    # article success: the supervisor still classifies RESULT, exits and cleanup.
    RESULT_COMMITTED = "result_committed"


@dataclass(frozen=True)
class RelayOutcome:
    status: RelayStatus
    reason: EndReason
    local_attempts: int
    submitted_opens: int
    received_frames: int
    sent_frames: int
    data_received: int
    data_emitted: int
    unknown_write_bytes: int
    cleanup_failed: bool
    pending_tasks: int


class RelayFailure(Exception):
    def __init__(self, reason: EndReason = EndReason.PROTOCOL) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass
class _Output:
    frame: Frame
    done: asyncio.Future[None]
    committed: bool = False


@dataclass(eq=False)
class _Stream:
    connection: Connection | None
    reply: asyncio.Future[bool]
    stream_id: int = 0  # No IPC identity until complete valid CONNECT.
    open_emitted: bool = False
    replied: bool = False
    admitted: bool = False
    terminal: bool = False
    retired: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    output: asyncio.Queue[_Output] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    emissions: int = 0
    send_credit: int = CREDIT
    queued_send: int = 0
    emitted: int = 0
    acked: int = 0
    receive_credit: int = CREDIT
    chunks: deque[bytes] = field(default_factory=deque)
    queued_receive: int = 0
    consumed: int = 0
    granted: int = 0
    local_half: bool = False
    remote_half: bool = False


class Relay:
    """Single-use actor; constructor performs no I/O.

    test_only_data_limit lowers the IPC DATA ceiling for deterministic offline
    boundary tests. There is no CLI/env switch; future live construction must
    assert the default and qualified adapters, after independent boundary review.
    """

    def __init__(
        self,
        frame_io: FrameIO,
        acceptor: LocalAcceptor,
        *,
        deadline: float = 45.0,
        cleanup_grace: float = 2.0,
        test_only_data_limit: int | None = None,
    ) -> None:
        if not math.isfinite(deadline) or not 0 < deadline <= 45:
            raise ValueError("deadline must be within the pilot ceiling")
        if not math.isfinite(cleanup_grace) or not 0 < cleanup_grace <= 2:
            raise ValueError("cleanup grace must be within two seconds")
        limit = TOTAL_NETWORK_LIMIT if test_only_data_limit is None else test_only_data_limit
        if type(limit) is not int or not 0 < limit <= TOTAL_NETWORK_LIMIT:
            raise ValueError("invalid offline DATA limit")
        self._io = frame_io
        self._acceptor = acceptor
        self._deadline = deadline
        self._cleanup_grace = cleanup_grace
        self._data_limit = limit
        self._locals: set[_Stream] = set()
        self._streams: dict[int, _Stream] = {}
        self._terminal_ids: set[int] = set()
        self._order: deque[int] = deque()
        self._ready = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._end: asyncio.Future[EndReason] | None = None
        self._used = False
        self._stopping = False
        self._attempts = self._opens = self._received = self._sent = 0
        self._data_received = self._data_emitted = self._data_reserved = 0
        self._unknown_writes = 0
        self._inflight_writes = 0
        self._result_started = False
        self._result_committed = False
        self._idle = asyncio.Event()  # Set when no local stream remains, or on stop.
        # Covers an accepted socket before stream construction or during refusal.
        self._accept_connection: Connection | None = None

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(task for task in self._tasks if not task.done())

    def _finish(self, reason: EndReason) -> None:
        if self._end is not None and not self._end.done():
            self._stopping = True
            self._end.set_result(reason)
        self._idle.set()  # Wake finish_result so it observes the stop.

    def _spawn(self, work: Callable[[], Awaitable[None]], stream: _Stream | None = None) -> None:
        if self._stopping or (stream is not None and stream.terminal):
            return

        async def guarded() -> None:
            try:
                await work()
            except asyncio.CancelledError:
                if not self._stopping and (stream is None or not stream.terminal):
                    self._finish(EndReason.IO)
                raise
            except (FrameCodecError, RelayFailure) as exc:
                if stream is None or not stream.terminal:
                    self._finish(
                        exc.reason if isinstance(exc, RelayFailure) else EndReason.PROTOCOL
                    )
            except Exception:
                if stream is None or not stream.terminal:
                    self._finish(EndReason.IO)

        task = asyncio.create_task(guarded(), name="pilot-relay")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if stream is not None:
            stream.tasks.add(task)
            task.add_done_callback(stream.tasks.discard)

    async def run(self) -> RelayOutcome:
        if self._used:
            raise RuntimeError("relay actors cannot be reused")
        self._used = True
        self._end = asyncio.get_running_loop().create_future()
        self._spawn(self._accept)
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
        cleanup_failed = False
        for closer in (self._io.close, self._acceptor.close):
            try:
                closer()
            except Exception:
                cleanup_failed = True
        if self._accept_connection is not None:
            try:
                self._accept_connection.close()
            except Exception:
                cleanup_failed = True
            self._accept_connection = None
        for stream in tuple(self._locals):
            try:
                self._close(stream)
            except Exception:
                cleanup_failed = True
            if not stream.reply.done():
                stream.reply.cancel()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=self._cleanup_grace)
            except asyncio.CancelledError:
                cleanup_failed = True
        for stream in self._locals:
            while not stream.output.empty():
                stream.output.get_nowait().done.cancel()
        pending = len(self.pending_tasks)
        return RelayOutcome(
            RelayStatus.RESULT_COMMITTED if self._result_committed else RelayStatus.INCOMPLETE,
            reason,
            self._attempts,
            self._opens,
            self._received,
            self._sent,
            self._data_received,
            self._data_emitted,
            self._unknown_writes + self._inflight_writes,
            cleanup_failed or pending > 0,
            pending,
        )

    @staticmethod
    def _verify_peer(connection: Connection) -> None:
        getter = getattr(connection, "getpeername", None)
        if not callable(getter):
            raise RelayFailure(EndReason.IO)
        peer = getter()
        if (
            type(peer) is not tuple
            or len(peer) != 2
            or type(peer[0]) is not str
            or peer[0] != "127.0.0.1"
            or type(peer[1]) is not int
            or not 1 <= peer[1] <= 65535
        ):
            raise RelayFailure(EndReason.IO)

    def _connection(self, stream: _Stream) -> Connection:
        if stream.connection is None or stream.terminal or self._stopping:
            raise RelayFailure(EndReason.IO)
        self._verify_peer(stream.connection)
        return stream.connection

    async def _write_count(self, connection: Connection, data: bytes) -> int:
        self._inflight_writes += len(data)
        try:
            self._verify_peer(connection)
            actual = await connection.write(data)
            self._verify_peer(connection)
            if type(actual) is not int or not 0 <= actual <= len(data):
                raise RelayFailure(EndReason.IO)
        except BaseException:
            self._unknown_writes += len(data)
            raise
        finally:
            self._inflight_writes -= len(data)
        if not actual:
            raise RelayFailure(EndReason.IO)
        return actual

    async def _write_all(self, connection: Connection, data: bytes) -> None:
        while data:
            actual = await self._write_count(connection, data)
            data = data[actual:]

    async def _read_count(self, connection: Connection, amount: int) -> bytes:
        self._verify_peer(connection)
        data = await connection.read(amount)
        self._verify_peer(connection)
        if type(data) is not bytes or len(data) > amount:
            raise RelayFailure(EndReason.IO)
        return data

    async def _reject(self, connection: Connection, response: bytes) -> None:
        try:
            async with asyncio.timeout(2):
                await self._write_all(connection, response)
        finally:
            connection.close()

    async def _accept(self) -> None:
        while not self._stopping and self._attempts < MAX_STREAM_ID:
            connection = await self._acceptor.accept()
            if connection is None:
                if not self._result_started:  # finish_result closed it deliberately.
                    self._finish(EndReason.EOF)
                return
            self._accept_connection = connection
            self._attempts += 1
            try:
                if self._stopping:
                    return
                try:
                    self._verify_peer(connection)
                except Exception:
                    # Unqualified peers are denied, never written to or assigned IDs.
                    connection.close()
                    continue
                if len(self._locals) >= 4:
                    await self._reject(connection, HTTP_BUSY)
                    continue
                stream = _Stream(connection, asyncio.get_running_loop().create_future())
                self._locals.add(stream)
                self._accept_connection = None  # stream owns cleanup before spawning
                self._spawn(partial(self._handshake, stream), stream)
            finally:
                if self._accept_connection is not None:
                    self._accept_connection.close()
                    self._accept_connection = None
        # No 41st accept: bound malformed/capacity work as well as gateway OPENs.
        self._acceptor.close()

    async def _handshake(self, stream: _Stream) -> None:
        connection = self._connection(stream)
        data = b""
        try:
            async with asyncio.timeout(2):
                while b"\r\n\r\n" not in data:
                    if len(data) == CONNECT_HEADER_LIMIT:
                        raise ConnectHeaderError("header limit")
                    part = await self._read_count(connection, CONNECT_HEADER_LIMIT - len(data))
                    if not part:
                        raise ConnectHeaderError("header EOF")
                    data += part
                request = parse_connect_headers(data)
        except (ConnectHeaderError, TimeoutError):
            await self._reject(connection, HTTP_BAD)
            self._retire(stream)
            return
        if self._stopping:
            return
        if self._result_started:
            # No OPEN may follow RESULT; refuse before any IPC identity exists.
            await self._reject(connection, HTTP_BUSY)
            self._retire(stream)
            return
        self._opens += 1
        stream.stream_id = self._opens
        self._streams[stream.stream_id] = stream
        self._order.append(stream.stream_id)
        await self._emit(stream, FrameType.OPEN, f"{request.host}:443".encode("ascii"))
        admitted = await stream.reply
        if not admitted:
            await self._reject(connection, HTTP_REFUSED)
            self._retire(stream)
            return
        await self._write_all(connection, HTTP_OK)
        if self._stopping or stream.terminal:
            return
        # DATA may have queued since OPEN_OK, but writer starts only after full 200.
        self._spawn(partial(self._read, stream, request.leftover), stream)
        self._spawn(partial(self._write, stream), stream)

    @staticmethod
    def _shape(frame: Frame) -> None:
        if (
            type(frame) is not Frame
            or type(frame.frame_type) is not FrameType
            or type(frame.stream_id) is not int
            or type(frame.payload) is not bytes
        ):
            raise RelayFailure()
        try:
            parser = FrameParser()
            list(parser.feed(encode_frame(frame.frame_type, frame.stream_id, frame.payload)))
            parser.end_of_stream()
        except (ValueError, TypeError, struct.error, FrameCodecError) as exc:
            raise RelayFailure() from exc

    def _check_data_budget(self, amount: int) -> None:
        if (
            self._data_received + self._data_emitted + self._data_reserved + amount
            > self._data_limit
        ):
            raise RelayFailure(EndReason.BUDGET)

    async def _receive(self) -> None:
        while not self._stopping:
            frame = await self._io.receive()
            if frame is None:
                self._finish(EndReason.EOF)
                return
            self._received += 1
            if self._received > MAX_FRAMES:
                raise RelayFailure()
            self._shape(frame)
            if frame.frame_type not in (
                FrameType.OPEN_OK,
                FrameType.OPEN_ERROR,
                FrameType.DATA,
                FrameType.WINDOW,
                FrameType.HALF_CLOSE,
                FrameType.CLOSE,
            ):
                raise RelayFailure()
            stream = self._streams.get(frame.stream_id)
            if stream is None or stream.terminal or not stream.open_emitted:
                raise RelayFailure()
            kind = frame.frame_type
            if kind in (FrameType.OPEN_OK, FrameType.OPEN_ERROR):
                if stream.replied:
                    raise RelayFailure()
                stream.replied = True
                stream.admitted = kind is FrameType.OPEN_OK
                stream.reply.set_result(stream.admitted)
            else:
                if not stream.admitted:
                    raise RelayFailure()
                if kind is FrameType.DATA:
                    count = len(frame.payload)
                    if not count or stream.remote_half or count > stream.receive_credit:
                        raise RelayFailure()
                    self._check_data_budget(count)
                    self._data_received += count
                    stream.receive_credit -= count
                    stream.queued_receive += count
                    stream.chunks.append(frame.payload)
                elif kind is FrameType.WINDOW:
                    count = struct.unpack("!I", frame.payload)[0]
                    if not 0 < count <= stream.emitted - stream.acked:
                        raise RelayFailure()
                    if stream.send_credit + count > CREDIT:
                        raise RelayFailure()
                    stream.acked += count
                    stream.send_credit += count
                elif kind is FrameType.HALF_CLOSE:
                    if stream.remote_half:
                        raise RelayFailure()
                    stream.remote_half = True
                elif kind is FrameType.CLOSE:
                    if (
                        not stream.local_half
                        or not stream.remote_half
                        or stream.queued_send
                        or stream.queued_receive
                        or stream.emitted != stream.acked
                        or stream.consumed != stream.granted
                    ):
                        raise RelayFailure()
                    # Do not require shutdown_write: CLOSE itself delivers EOF.
                    stream.terminal = True
                    self._close(stream)
                    if not stream.emissions:
                        self._retire(stream)
            stream.wake.set()

    async def _read(self, stream: _Stream, leftover: bytes) -> None:
        while not stream.terminal:
            stream.wake.clear()
            if not stream.send_credit:
                await stream.wake.wait()
                continue
            amount = min(MAX_PAYLOAD, stream.send_credit)
            if leftover:
                data, leftover = leftover[:amount], leftover[amount:]
            else:
                data = await self._read_count(self._connection(stream), amount)
            if not data:
                await self._emit(stream, FrameType.HALF_CLOSE)
                return
            self._check_data_budget(len(data))
            stream.send_credit -= len(data)
            stream.queued_send += len(data)
            self._data_reserved += len(data)
            await self._emit(stream, FrameType.DATA, data)

    async def _write(self, stream: _Stream) -> None:
        while not stream.terminal:
            stream.wake.clear()
            if stream.chunks:
                data = stream.chunks[0]
                actual = await self._write_count(self._connection(stream), data)
                if actual == len(data):
                    stream.chunks.popleft()
                else:
                    stream.chunks[0] = data[actual:]
                stream.queued_receive -= actual
                stream.consumed += actual
                await self._emit(stream, FrameType.WINDOW, struct.pack("!I", actual))
            elif stream.remote_half:
                await self._connection(stream).shutdown_write()
                return
            else:
                await stream.wake.wait()

    async def _emit(self, stream: _Stream, kind: FrameType, payload: bytes = b"") -> None:
        if stream.terminal or self._stopping:
            raise RelayFailure()
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
            if stream.terminal and not stream.emissions:
                self._retire(stream)

    def _commit(self, stream: _Stream, row: _Output) -> None:
        if row.committed or stream.terminal or self._stopping:
            raise RelayFailure()
        kind, payload = row.frame.frame_type, row.frame.payload
        if kind is FrameType.DATA:
            count = len(payload)
            stream.queued_send -= count
            stream.emitted += count
            self._data_reserved -= count
            self._data_emitted += count
        elif kind is FrameType.WINDOW:
            count = struct.unpack("!I", payload)[0]
            if not 0 < count <= stream.consumed - stream.granted:
                raise RelayFailure()
            if stream.receive_credit + count > CREDIT:
                raise RelayFailure()
            stream.granted += count
            stream.receive_credit += count
        elif kind is FrameType.OPEN:
            if stream.open_emitted:
                raise RelayFailure()
            stream.open_emitted = True
        elif kind is FrameType.HALF_CLOSE:
            if stream.local_half or stream.queued_send:
                raise RelayFailure()
            stream.local_half = True
        elif kind is FrameType.RESULT:
            if stream.stream_id != 0 or not self._result_started:
                raise RelayFailure()
        else:
            raise RelayFailure()
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
            try:
                if self._sent >= MAX_FRAMES:
                    raise RelayFailure()
                self._shape(row.frame)
                await self._io.send(row.frame, partial(self._commit, selected, row))
                if not row.committed or row.done.done():
                    raise RelayFailure()
                row.done.set_result(None)
            finally:
                if not row.done.done():
                    row.done.cancel()

    async def finish_result(self, content: bytes, final: bytes) -> None:
        """Emit the private RESULT on control stream 0, then end the run in order.

        Single call by the browser entrypoint after its browser context closed.
        Shapes are refused before any state change. From the call on, no new local
        CONNECT is accepted and no unsent OPEN is emitted; existing local streams
        must retire first, so no OPEN can follow RESULT. Content chunks (subtype 1)
        precede exactly one final record (subtype 2), each committed in order
        through the shared scheduler. Success sets ``RESULT_COMMITTED`` only.
        A stop/deadline/cancellation before the final commit raises RelayFailure.
        """
        if type(content) is not bytes or type(final) is not bytes:
            raise ValueError("RESULT content and final record must be bytes")
        if not 0 < len(final) <= FINAL_LIMIT:
            raise ValueError("final record outside bounded size")
        content.decode("utf-8")  # Strict; the collector also enforces this.
        body = MAX_PAYLOAD - 1
        payloads = [b"\x01" + content[i : i + body] for i in range(0, len(content), body)]
        payloads.append(b"\x02" + final)
        if sum(map(len, payloads)) > RESULT_LIMIT or len(content) + len(final) > RESULT_LIMIT:
            raise ValueError("RESULT payload limit")
        if self._end is None or self._stopping or self._result_started:
            raise RelayFailure()
        self._result_started = True
        try:
            self._acceptor.close()
            while self._locals:
                self._idle.clear()
                await self._idle.wait()
                if self._stopping:
                    raise RelayFailure()
            control = _Stream(None, asyncio.get_running_loop().create_future())
            control.reply.cancel()  # Control stream has no admission reply.
            self._streams[0] = control
            self._order.append(0)
            for payload in payloads:
                await self._emit(control, FrameType.RESULT, payload)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            raise RelayFailure() from None  # Teardown cancelled a queued RESULT row.
        self._result_committed = True
        self._finish(EndReason.EOF)

    @staticmethod
    def _close(stream: _Stream) -> None:
        if stream.connection is not None:
            connection = stream.connection
            stream.connection = None
            connection.close()

    def _retire(self, stream: _Stream) -> None:
        if stream.retired:
            return
        if stream.emissions or not stream.output.empty():
            raise RelayFailure()
        self._close(stream)
        stream.terminal = stream.retired = True
        if not stream.reply.done():
            stream.reply.cancel()
        self._locals.discard(stream)
        if not self._locals:
            self._idle.set()
        if stream.stream_id:
            self._terminal_ids.add(stream.stream_id)
            self._streams.pop(stream.stream_id)
            self._order.remove(stream.stream_id)
        current = asyncio.current_task()
        for task in tuple(stream.tasks):
            if task is not current:
                task.cancel()
