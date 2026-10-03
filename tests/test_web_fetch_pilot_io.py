"""Transport fixtures: fake I/O tests and explicit, owned local-kernel checks.

Tests named real_* need boundary review before their first execution. They only
create owned anonymous pipes or ephemeral loopback sockets, never public egress,
DNS, browser/container launches or probes of existing local services.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections import deque
from contextlib import ExitStack, contextmanager
from collections.abc import Iterator

import pytest

from scripts.web_fetch_pilot_core import Frame, FrameCodecError, FrameType, encode_frame
from scripts.web_fetch_pilot_io import (
    AsyncFD,
    FDFrameIO,
    NumericConnector,
    SocketConnection,
    TransportError,
)


class BytesIO:
    def __init__(self, chunks: tuple[bytes, ...] = ()) -> None:
        self.chunks = deque(chunks)
        self.output = bytearray()
        self.closed = False
        self.partial = 3
        self.bad_count: object | None = None

    async def read(self, size: int) -> bytes:
        return self.chunks.popleft() if self.chunks else b""

    async def write(self, data: bytes) -> int:
        if self.bad_count is not None:
            return self.bad_count  # type: ignore[return-value]
        count = min(self.partial, len(data))
        self.output.extend(data[:count])
        return count

    def close(self) -> None:
        self.closed = True


@contextmanager
def owned_duplex_pipes() -> Iterator[tuple[FDFrameIO, FDFrameIO]]:
    """Transfer fd ownership only after successful adapter construction."""
    unclaimed: set[int] = set()
    with ExitStack() as stack:

        def close_unclaimed(fd: int) -> None:
            if fd in unclaimed:
                unclaimed.remove(fd)
                os.close(fd)

        def pipe() -> tuple[int, int]:
            pair = os.pipe()
            for fd in pair:
                unclaimed.add(fd)
                stack.callback(close_unclaimed, fd)
            return pair

        def own(fd: int) -> AsyncFD:
            adapter = AsyncFD(fd)
            unclaimed.remove(fd)
            stack.callback(adapter.close)
            return adapter

        ab_read, ab_write = pipe()
        ba_read, ba_write = pipe()
        a = FDFrameIO(own(ba_read), own(ab_write))
        stack.callback(a.close)
        b = FDFrameIO(own(ab_read), own(ba_write))
        stack.callback(b.close)
        yield a, b


@pytest.mark.asyncio
async def test_fake_partial_writes_commit_once_after_complete_frame() -> None:
    writer = BytesIO()
    io = FDFrameIO(BytesIO(), writer)
    row = Frame(FrameType.DATA, 1, b"abcde")
    commits: list[bytes] = []
    await io.send(row, lambda: commits.append(bytes(writer.output)))
    assert commits == [encode_frame(row.frame_type, row.stream_id, row.payload)]
    io.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, -1, True, 99, "1"])
async def test_fake_bad_write_count_latches_closed(count: object) -> None:
    writer = BytesIO()
    writer.bad_count = count
    reader = BytesIO()
    io = FDFrameIO(reader, writer)
    called: list[bool] = []
    with pytest.raises(TransportError):
        await io.send(Frame(FrameType.OPEN_OK, 1, b""), lambda: called.append(True))
    assert not called and reader.closed and writer.closed
    with pytest.raises(TransportError):
        await io.send(Frame(FrameType.OPEN_OK, 1, b""), lambda: None)


@pytest.mark.asyncio
async def test_fake_split_frames_and_truncated_eof() -> None:
    raw = encode_frame(FrameType.DATA, 1, b"x")
    io = FDFrameIO(BytesIO((raw[:7], raw[7:])), BytesIO())
    assert await io.receive() == Frame(FrameType.DATA, 1, b"x")
    assert await io.receive() is None
    io.close()
    bad = FDFrameIO(BytesIO((raw[:-1],)), BytesIO())
    with pytest.raises(FrameCodecError):
        await bad.receive()
    with pytest.raises(TransportError):
        await bad.receive()


@pytest.mark.asyncio
async def test_fake_commit_exception_closes_transport() -> None:
    reader, writer = BytesIO(), BytesIO()
    io = FDFrameIO(reader, writer)

    def fail() -> None:
        raise ValueError("commit failed")

    with pytest.raises(ValueError):
        await io.send(Frame(FrameType.OPEN_OK, 1, b""), fail)
    assert reader.closed and writer.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", ["127.0.0.1", "openai.com", "::1", "8.8.8.08"])
async def test_fake_numeric_policy_denied_before_socket_creation(
    candidate: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.web_fetch_pilot_core import DestinationPolicyError

    def forbidden(*args: object) -> None:
        raise AssertionError("socket must not be created")

    monkeypatch.setattr(socket, "socket", forbidden)
    with pytest.raises(DestinationPolicyError):
        await NumericConnector(())(candidate, 443)


@pytest.mark.asyncio
async def test_real_pipe_roundtrip_and_publication_order() -> None:
    with owned_duplex_pipes() as (a, b):
        marks: list[str] = []
        payload = b"x" * 16384
        sending = asyncio.create_task(
            a.send(Frame(FrameType.DATA, 1, payload), lambda: marks.append("committed"))
        )
        try:
            row = await asyncio.wait_for(b.receive(), 1)
            assert row == Frame(FrameType.DATA, 1, payload)
            marks.append("received")
            await asyncio.wait_for(sending, 1)
            assert marks == ["committed", "received"]
            await b.send(Frame(FrameType.WINDOW, 1, (16384).to_bytes(4, "big")), lambda: None)
            reply = await asyncio.wait_for(a.receive(), 1)
            assert reply is not None and reply.frame_type is FrameType.WINDOW
        finally:
            a.close()
            b.close()
            if not sending.done():
                sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_pipe_close_wakes_pending_reader() -> None:
    with owned_duplex_pipes() as (a, b):
        task = asyncio.create_task(a.receive())
        try:
            await asyncio.sleep(0)
            a.close()
            with pytest.raises(TransportError):
                await asyncio.wait_for(task, 1)
        finally:
            a.close()
            b.close()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_loopback_socket_counts_peer_and_half_close() -> None:
    loop = asyncio.get_running_loop()
    with ExitStack() as stack:
        listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        client = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        listener.setblocking(False)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client.setblocking(False)
        address = listener.getsockname()
        await asyncio.wait_for(loop.sock_connect(client, address), 1)
        accepted, _ = await asyncio.wait_for(loop.sock_accept(listener), 1)
        stack.enter_context(accepted)
        adapter = SocketConnection(client)
        stack.callback(adapter.close)
        assert adapter.getpeername() == address
        assert adapter.peer_ip == "127.0.0.1"
        assert await adapter.write(b"fixture") == 7
        assert await asyncio.wait_for(loop.sock_recv(accepted, 7), 1) == b"fixture"
        await loop.sock_sendall(accepted, b"reply")
        assert await asyncio.wait_for(adapter.read(5), 1) == b"reply"
        await adapter.shutdown_write()
        assert await asyncio.wait_for(loop.sock_recv(accepted, 1), 1) == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "connect", "peer", "cancel"])
async def test_fake_numeric_sockaddr_and_owned_cleanup(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import scripts.web_fetch_pilot_io as adapters

    addresses: list[tuple[str, int]] = []
    wrappers: list[DoubleFD] = []
    entered = asyncio.Event()

    class DoubleSocket:
        family = socket.AF_INET
        type = socket.SOCK_STREAM
        closed = False

        def fileno(self) -> int:
            return 999999

        def connect_ex(self, address: tuple[str, int]) -> int:
            addresses.append(address)
            if failure == "connect":
                return errno.ECONNREFUSED
            return errno.EINPROGRESS if failure == "cancel" else 0

        def getpeername(self) -> tuple[str, int]:
            return ("127.0.0.1" if failure == "peer" else "8.8.8.8", 443)

        def close(self) -> None:
            self.closed = True

    class DoubleFD:
        def __init__(self, fd: int, *, owns_fd: bool) -> None:
            assert fd == 999999 and not owns_fd
            self.closed = False
            wrappers.append(self)

        async def ready(self, *, writing: bool) -> None:
            assert writing
            entered.set()
            await asyncio.Event().wait()

        def close(self) -> None:
            self.closed = True

    sock = DoubleSocket()

    def create(family: int, kind: int) -> DoubleSocket:
        assert (family, kind) == (socket.AF_INET, socket.SOCK_STREAM)
        return sock

    def forbidden_dns(*args: object, **kwargs: object) -> None:
        raise AssertionError("numeric dialing must not resolve hostnames")

    monkeypatch.setattr(socket, "socket", create)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)
    monkeypatch.setattr(adapters, "AsyncFD", DoubleFD)
    connector = NumericConnector(())
    if failure == "cancel":
        task = asyncio.create_task(connector("8.8.8.8", 443))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    elif failure == "connect":
        with pytest.raises(OSError):
            await connector("8.8.8.8", 443)
    elif failure == "peer":
        with pytest.raises(TransportError):
            await connector("8.8.8.8", 443)
    else:
        connection = await connector("8.8.8.8", 443)
        assert not sock.closed
        assert connection.getpeername() == ("8.8.8.8", 443)
        connection.close()
    assert addresses == [("8.8.8.8", 443)]
    assert sock.closed and all(wrapper.closed for wrapper in wrappers)
