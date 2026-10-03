"""Pilot-only Linux pipe and numeric TCP adapters; no entrypoint or DNS resolver.

Only explicitly supplied descriptors/sockets are owned. A real supervisor must
provide scrubbed processes, qualified inventory and an external teardown bound.
Publication callbacks run after the final nonblocking write, with no intervening
await: this orders local actor ACK processing, not remote physical observation.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import stat
from collections import deque
from collections.abc import Callable, Sequence
from typing import Protocol

from scripts.web_fetch_pilot_core import (
    MAX_FRAMES,
    MAX_PAYLOAD,
    Frame,
    FrameCodecError,
    FrameParser,
    FrameType,
    encode_frame,
    parse_owned_inventory,
    validate_dns_answers,
)


class TransportError(Exception):
    """Terminal adapter failure, including loss of frame synchronization."""


class ByteReader(Protocol):
    async def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class ByteWriter(Protocol):
    async def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class AsyncFD:
    """Nonblocking pipe/socket I/O; construction transfers explicit ownership.

    Borrowed descriptors are permitted only for an owning socket adapter.
    Regular files/TTYs are rejected; no path is opened and no descriptor is duped.
    All operations must stay on the construction event loop.
    """

    def __init__(self, fd: int, *, owns_fd: bool = True) -> None:
        if type(fd) is not int or fd < 0:
            raise ValueError("descriptor must be a nonnegative integer")
        self._loop = asyncio.get_running_loop()
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            raise ValueError("only pipes and sockets are permitted")
        os.set_blocking(fd, False)
        self._fd = fd
        self._owns = owns_fd
        self._closed = False
        self._waiting: dict[bool, asyncio.Future[None]] = {}
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    def _check(self) -> None:
        if self._closed:
            raise TransportError("descriptor closed")
        if asyncio.get_running_loop() is not self._loop:
            raise TransportError("descriptor used from another event loop")

    async def ready(self, *, writing: bool) -> None:
        self._check()
        if writing in self._waiting:
            raise TransportError("overlapping readiness registration")
        future: asyncio.Future[None] = self._loop.create_future()
        self._waiting[writing] = future

        def wake() -> None:
            if not future.done():
                future.set_result(None)

        try:
            if writing:
                self._loop.add_writer(self._fd, wake)
            else:
                self._loop.add_reader(self._fd, wake)
            await future
            self._check()
        finally:
            self._waiting.pop(writing, None)
            # close() removed registrations before releasing the descriptor.
            # Do not remove a registration on a potentially reused fd number.
            if not self._closed:
                if writing:
                    self._loop.remove_writer(self._fd)
                else:
                    self._loop.remove_reader(self._fd)

    async def read(self, size: int) -> bytes:
        if type(size) is not int or not 0 < size <= MAX_PAYLOAD:
            raise ValueError("read size outside bounded range")
        async with self._read_lock:
            while True:
                self._check()
                try:
                    return os.read(self._fd, size)
                except BlockingIOError:
                    await self.ready(writing=False)
                except InterruptedError:
                    continue

    async def write(self, data: bytes) -> int:
        if type(data) is not bytes or not 0 < len(data) <= MAX_PAYLOAD + 12:
            raise ValueError("write size/type outside bounded range")
        async with self._write_lock:
            while True:
                self._check()
                try:
                    return os.write(self._fd, data)
                except BlockingIOError:
                    await self.ready(writing=True)
                except InterruptedError:
                    continue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._loop.is_closed():
            self._loop.remove_reader(self._fd)
            self._loop.remove_writer(self._fd)
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(TransportError("descriptor closed during wait"))
        if self._owns:
            os.close(self._fd)


class FDFrameIO:
    """One framed duplex pair with bounded reads and serialized publication.

    A receive batch contains at most one staged frame plus a 16 KiB input;
    pending decoded payload is under 32 KiB. Drain that batch before another read.
    Failure closes both endpoints; cancellation mid-frame cannot be resumed.
    """

    def __init__(self, reader: ByteReader, writer: ByteWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._parser = FrameParser()
        self._pending: deque[Frame] = deque()
        self._closed = False
        self._eof = False
        self._sent = 0
        self._receive_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()

    async def receive(self) -> Frame | None:
        try:
            async with self._receive_lock:
                if self._closed:
                    raise TransportError("frame transport closed")
                while not self._pending:
                    if self._eof:
                        return None
                    data = await self._reader.read(MAX_PAYLOAD)
                    if type(data) is not bytes or len(data) > MAX_PAYLOAD:
                        raise TransportError("reader exceeded chunk/type contract")
                    if not data:
                        self._parser.end_of_stream()
                        self._eof = True
                        return None
                    self._pending.extend(self._parser.feed(data))
                return self._pending.popleft()
        except BaseException:
            self.close()
            raise

    async def send(self, frame: Frame, commit: Callable[[], None], /) -> None:
        try:
            async with self._send_lock:
                if self._closed or self._sent >= MAX_FRAMES:
                    raise TransportError("frame transport closed/exhausted")
                if (
                    type(frame) is not Frame
                    or type(frame.frame_type) is not FrameType
                    or type(frame.stream_id) is not int
                    or type(frame.payload) is not bytes
                ):
                    raise FrameCodecError("invalid outbound frame type")
                raw = encode_frame(frame.frame_type, frame.stream_id, frame.payload)
                check = FrameParser()
                list(check.feed(raw))
                check.end_of_stream()
                offset = 0
                while offset < len(raw):
                    count = await self._writer.write(raw[offset:])
                    if type(count) is not int or not 0 < count <= len(raw) - offset:
                        raise TransportError("invalid partial-write count")
                    offset += count
                # AsyncFD's final successful write and this callback do not yield
                # to another local task. No ACK handler can overtake publication.
                commit()
                self._sent += 1
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending.clear()
        try:
            self._reader.close()
        finally:
            self._writer.close()


class SocketConnection:
    """Own an already connected IPv4 TCP socket; counts actual syscall bytes."""

    def __init__(self, sock: socket.socket) -> None:
        if sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM:
            raise ValueError("connection requires IPv4 TCP")
        self._socket = sock
        self._fd = AsyncFD(sock.fileno(), owns_fd=False)
        self._closed = False

    @property
    def peer_ip(self) -> str:
        return self.getpeername()[0]

    def getpeername(self) -> tuple[str, int]:
        host, port = self._socket.getpeername()
        return host, port

    async def read(self, maxsize: int) -> bytes:
        return await self._fd.read(maxsize)

    async def write(self, data: bytes) -> int:
        return await self._fd.write(data)

    async def shutdown_write(self) -> None:
        self._socket.shutdown(socket.SHUT_WR)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._fd.close()
        finally:
            self._socket.close()


class NumericConnector:
    """Dial only a validated numeric IPv4 sockaddr; never call getaddrinfo.

    DNS and fresh-inventory discovery are deliberately not implemented here.
    The owning actor supplies its three-second admission-wide connect deadline.
    On any error/cancellation this callable closes the socket before returning.
    """

    def __init__(self, inventory: Sequence[str] | None) -> None:
        self._inventory = parse_owned_inventory(inventory)

    async def __call__(self, ip: str, port: int) -> SocketConnection:
        if type(ip) is not str or type(port) is not int or port != 443:
            raise ValueError("numeric candidate and port 443 required")
        candidates = validate_dns_answers((ip,), self._inventory)
        if candidates != [ip]:
            raise ValueError("candidate must be canonical numeric IPv4")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        pending: AsyncFD | None = None
        try:
            pending = AsyncFD(sock.fileno(), owns_fd=False)
            result = sock.connect_ex((ip, port))
            if result in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                await pending.ready(writing=True)
                result = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if result:
                raise OSError(result, "numeric TCP connect failed")
            if sock.getpeername() != (ip, port):
                raise TransportError("connected socket peer mismatch")
            pending.close()
            pending = None
            return SocketConnection(sock)
        except BaseException:
            if pending is not None:
                pending.close()
            sock.close()
            raise
