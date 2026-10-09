"""Deterministic injected actor tests; no OS sockets, pipes, browser or DNS."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

import pytest

from scripts.web_fetch_pilot_core import Frame, FrameParser, FrameType, encode_frame
from scripts.web_fetch_pilot_gateway import Connection, EndReason, Gateway
from scripts.web_fetch_pilot_relay import (
    CREDIT,
    HTTP_BAD,
    HTTP_BUSY,
    HTTP_OK,
    HTTP_REFUSED,
    Relay,
    RelayFailure,
    RelayOutcome,
    RelayStatus,
)
from scripts.web_fetch_pilot_results import ResultCollector


def header(host: str = "openai.com", tail: bytes = b"") -> bytes:
    return f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii") + tail


def frame(kind: FrameType, payload: bytes = b"", stream: int = 1) -> Frame:
    return Frame(kind, stream, payload)


def window(amount: int, stream: int = 1) -> Frame:
    return frame(FrameType.WINDOW, struct.pack("!I", amount), stream)


async def settle(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


class Conn:
    def __init__(self, reads: Sequence[bytes] = ()) -> None:
        self.reads: deque[bytes] = deque(reads)
        self.read_gate = asyncio.Event()
        self.read_entered = asyncio.Event()
        self.write_entered = asyncio.Event()
        self.write_gate: asyncio.Event | None = None
        self.shutdown_gate: asyncio.Event | None = None
        self.read_sizes: list[int] = []
        self.writes: list[bytes] = []
        self.written = bytearray()
        self.partial: int | None = None
        self.write_result: int | None = None
        self.read_result: bytes | None = None
        self.peer: tuple[str, int] = ("127.0.0.1", 23456)
        self.closed = False
        self.shutdowns = 0
        self.write_error = False
        self.read_error = False
        self.unknown_partial = False
        self.trace: list[str] = []

    def getpeername(self) -> tuple[str, int]:
        return self.peer

    @property
    def peer_ip(self) -> str:
        return self.peer[0]

    async def read(self, maxsize: int) -> bytes:
        self.read_sizes.append(maxsize)
        self.read_entered.set()
        if self.read_error:
            raise OSError("injected read")
        if self.read_result is not None:
            return self.read_result
        if not self.reads:
            await self.read_gate.wait()
        if not self.reads:
            return b""
        data = self.reads.popleft()
        if len(data) > maxsize:
            self.reads.appendleft(data[maxsize:])
            data = data[:maxsize]
        return data

    async def write(self, data: bytes) -> int:
        self.writes.append(data)
        self.write_entered.set()
        if self.write_gate is not None:
            if self.unknown_partial:
                self.written.extend(data[:1])
            await self.write_gate.wait()
        if self.write_error:
            raise OSError("injected write")
        if self.write_result is not None:
            return self.write_result
        actual = min(len(data), self.partial) if self.partial is not None else len(data)
        self.written.extend(data[:actual])
        if bytes(self.written) == HTTP_OK:
            self.trace.append("200 complete")
        return actual

    async def shutdown_write(self) -> None:
        self.shutdowns += 1
        if self.shutdown_gate is not None:
            await self.shutdown_gate.wait()

    def close(self) -> None:
        self.closed = True


class Acceptor:
    def __init__(self) -> None:
        self.input: asyncio.Queue[Connection | None] = asyncio.Queue(maxsize=40)
        self.closed = False
        self.accepts = 0

    async def accept(self) -> Connection | None:
        connection = await self.input.get()
        self.accepts += 1
        return connection

    def close(self) -> None:
        self.closed = True


class Pipe:
    """Commit precedes local receiver scheduling, not remote physical reception."""

    def __init__(self) -> None:
        self.input: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=16)
        self.output: asyncio.Queue[Frame] = asyncio.Queue(maxsize=4096)
        self.sent: list[Frame] = []
        self.hook: Callable[[Frame], Awaitable[None]] | None = None
        self.gate: asyncio.Event | None = None
        self.gated_kind: FrameType | None = None
        self.commit_mode = "once"
        self.closed = False
        self.send_error = False
        self.receive_error = False

    async def receive(self) -> Frame | None:
        row = await self.input.get()
        if self.receive_error:
            raise OSError("injected receive")
        return row

    async def send(self, row: Frame, commit: Callable[[], None]) -> None:
        if self.send_error:
            raise OSError("injected send")
        if self.gate is not None and (self.gated_kind is None or row.frame_type is self.gated_kind):
            await self.gate.wait()
        # Materialize the whole encoded frame before commit, just as a bounded
        # local publisher would. Do not expose output to a receiver before commit.
        parser = FrameParser()
        decoded = list(parser.feed(encode_frame(row.frame_type, row.stream_id, row.payload)))
        parser.end_of_stream()
        assert decoded == [row]
        if self.commit_mode != "missing":
            commit()
        if self.commit_mode == "twice":
            commit()
        self.sent.append(row)
        self.output.put_nowait(row)
        if self.hook is not None:
            await self.hook(row)

    def close(self) -> None:
        self.closed = True

    async def feed(self, row: Frame | None) -> None:
        await self.input.put(row)

    async def until(self, kind: FrameType, stream: int = 1) -> Frame:
        async with asyncio.timeout(1):
            while True:
                row = await self.output.get()
                if row.frame_type is kind and row.stream_id == stream:
                    return row


class Harness:
    def __init__(self, *, deadline: float = 1, limit: int | None = None) -> None:
        self.pipe = Pipe()
        self.acceptor = Acceptor()
        self.relay = Relay(self.pipe, self.acceptor, deadline=deadline, test_only_data_limit=limit)

    def start(self) -> asyncio.Task[RelayOutcome]:
        return asyncio.create_task(self.relay.run())

    async def connect(self, conn: Conn | None = None, stream: int = 1) -> Conn:
        conn = conn if conn is not None else Conn((header(),))
        await self.acceptor.input.put(conn)
        await self.pipe.until(FrameType.OPEN, stream)
        await self.pipe.feed(frame(FrameType.OPEN_OK, stream=stream))
        await settle(lambda: bytes(conn.written).startswith(HTTP_OK))
        return conn

    async def stop(self, task: asyncio.Task[RelayOutcome]) -> RelayOutcome:
        await self.pipe.feed(None)
        result = await asyncio.wait_for(task, 1)
        assert result.status is RelayStatus.INCOMPLETE
        assert self.pipe.closed and self.acceptor.closed
        assert not self.relay.pending_tasks and not result.cleanup_failed
        return result


@pytest.mark.asyncio
async def test_exact_order_split_header_prefetched_tls_and_partial_200() -> None:
    h = Harness()
    preface = header(tail=b"prefetched TLS")
    conn = Conn((preface[:9], preface[9:]))
    conn.partial = 3
    conn.write_gate = asyncio.Event()
    task = h.start()
    await h.acceptor.input.put(conn)
    opening = await h.pipe.until(FrameType.OPEN)
    assert opening.payload == b"openai.com:443" and not conn.written
    assert not conn.write_entered.is_set()
    await h.pipe.feed(frame(FrameType.OPEN_OK))
    await conn.write_entered.wait()
    await h.pipe.feed(frame(FrameType.DATA, b"remote TLS"))
    await settle(lambda: h.relay._streams[1].queued_receive == 10)
    assert not h.pipe.sent[1:] and not conn.written
    conn.write_gate.set()
    data = await h.pipe.until(FrameType.DATA)
    assert data.payload == b"prefetched TLS"
    await settle(lambda: bytes(conn.written) == HTTP_OK + b"remote TLS")
    assert conn.trace == ["200 complete"]
    assert conn.read_sizes[:2] == [8192, 8183]
    assert all(size <= 16384 for size in conn.read_sizes)
    await h.stop(task)


@pytest.mark.asyncio
async def test_unlisted_host_is_submitted_and_gateway_refusal_has_one_502() -> None:
    h = Harness()
    conn = Conn((header("not-in-manifest.example"),))
    conn.partial = 2
    task = h.start()
    await h.acceptor.input.put(conn)
    assert (await h.pipe.until(FrameType.OPEN)).payload == b"not-in-manifest.example:443"
    await h.pipe.feed(frame(FrameType.OPEN_ERROR, b"policy refused"))
    await settle(lambda: conn.closed)
    assert conn.written == HTTP_REFUSED and not h.relay._locals
    assert h.relay._terminal_ids == {1}
    await h.pipe.feed(frame(FrameType.OPEN_OK))
    result = await task
    assert result.reason is EndReason.PROTOCOL and result.submitted_opens == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        b"GET / HTTP/1.1\r\nHost: openai.com:443\r\n\r\n",
        b"CONNECT openai.com:443 HTTP/1.1\r\n\r\n",
        header().replace(b"\r\n\r\n", b"\r\nContent-Length: 0\r\n\r\n"),
        header().replace(b"Host: openai.com", b"Host: other.example"),
        header("127.0.0.1"),
        b"x" * 8192,
        b"",
        header().replace(b":443", b":80"),
    ],
)
async def test_malformed_local_attempt_rejected_without_ipc_identity(data: bytes) -> None:
    h = Harness()
    conn = Conn((data,))
    task = h.start()
    await h.acceptor.input.put(conn)
    await settle(lambda: conn.closed)
    assert conn.written == HTTP_BAD and not h.pipe.sent
    result = await h.stop(task)
    assert (result.local_attempts, result.submitted_opens) == (1, 0)


@pytest.mark.asyncio
async def test_two_second_header_deadline_and_shared_run_deadline() -> None:
    h = Harness(deadline=3)
    conn = Conn((b"CONNECT ",))
    task = h.start()
    await h.acceptor.input.put(conn)
    async with asyncio.timeout(2.5):
        while not conn.closed:
            await asyncio.sleep(0.01)
    assert conn.written == HTTP_BAD and not h.pipe.sent
    await h.stop(task)
    h = Harness(deadline=0.03)
    conn = Conn((b"CONNECT ",))
    task = h.start()
    await h.acceptor.input.put(conn)
    result = await task
    assert result.reason is EndReason.DEADLINE and conn.closed and not conn.written


@pytest.mark.asyncio
async def test_four_pending_including_header_and_capacity_attempt_cap_forty() -> None:
    h = Harness()
    task = h.start()
    held = [Conn((b"CONNECT ",)) for _ in range(2)] + [Conn((header(),)) for _ in range(2)]
    for conn in held:
        await h.acceptor.input.put(conn)
    await h.pipe.until(FrameType.OPEN, 2)
    assert len(h.relay._locals) == 4 and len(h.relay._streams) == 2
    refused = []
    for _ in range(36):
        conn = Conn((header(),))
        refused.append(conn)
        await h.acceptor.input.put(conn)
        await settle(lambda: conn.closed)
        assert conn.written == HTTP_BUSY and not conn.read_sizes
    await settle(lambda: h.acceptor.closed)
    assert h.acceptor.accepts == h.relay._attempts == 40
    assert len(h.pipe.sent) == 2 and h.relay._opens == 2
    never_accepted = Conn((header(),))
    await h.acceptor.input.put(never_accepted)
    result = await h.stop(task)
    assert result.local_attempts == 40 and all(conn.closed for conn in held + refused)
    assert not never_accepted.closed  # still owned by fake acceptor, not relay


@pytest.mark.asyncio
async def test_forty_malformed_connections_do_not_reset_attempt_count() -> None:
    h = Harness()
    task = h.start()
    for _ in range(40):
        conn = Conn((b"",))
        await h.acceptor.input.put(conn)
        await settle(lambda: conn.closed and not h.relay._locals)
    assert h.relay._attempts == 40 and h.acceptor.closed
    assert not h.pipe.sent
    assert (await h.stop(task)).submitted_opens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer",
    [
        ("127.0.0.2", 10),
        ("10.0.0.1", 10),
        ("::1", 10),
        ("127.0.0.1", 0),
        ("127.0.0.1", 65536),
        ("127.0.0.1", True),
    ],
)
async def test_actual_local_peer_required(peer: tuple[str, int]) -> None:
    h = Harness()
    conn = Conn((header(),))
    conn.peer = peer
    task = h.start()
    await h.acceptor.input.put(conn)
    await settle(lambda: conn.closed)
    assert not conn.read_sizes and not conn.writes and not h.pipe.sent
    assert (await h.stop(task)).local_attempts == 1


@pytest.mark.asyncio
async def test_missing_getter_cannot_be_replaced_by_claimed_peer_ip() -> None:
    h = Harness()
    conn = Conn((header(),))
    conn.getpeername = None  # type: ignore[assignment]
    task = h.start()
    await h.acceptor.input.put(conn)
    await settle(lambda: conn.closed)
    assert not h.pipe.sent and not conn.writes
    await h.stop(task)


@pytest.mark.asyncio
async def test_reply_before_open_publication_is_not_an_admission() -> None:
    h = Harness()
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.OPEN
    task = h.start()
    conn = Conn((header(),))
    await h.acceptor.input.put(conn)
    await settle(lambda: 1 in h.relay._streams)
    await h.pipe.feed(frame(FrameType.OPEN_OK))
    result = await task
    assert result.reason is EndReason.PROTOCOL and not conn.written
    assert result.sent_frames == 0 and conn.closed


@pytest.mark.asyncio
async def test_full_bounded_prefetch_is_preserved_and_not_header_only_counted() -> None:
    h = Harness()
    tail = b"x" * (8192 - len(header()))
    task = h.start()
    conn = await h.connect(Conn((header(tail=tail), b"")))
    assert (await h.pipe.until(FrameType.DATA)).payload == tail
    await h.pipe.until(FrameType.HALF_CLOSE)
    assert conn.read_sizes == [8192, 16384]
    await h.stop(task)


@pytest.mark.asyncio
async def test_hostile_id_subclass_rejected_before_hashing_or_lookup() -> None:
    class HostileInt(int):
        def __hash__(self) -> int:
            pytest.fail("subclass hash must not run")

    h = Harness()
    task = h.start()
    await h.connect()
    await h.pipe.feed(Frame(FrameType.OPEN_OK, HostileInt(1), b""))
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_bidirectional_partial_writes_credit_and_eof_order() -> None:
    h = Harness()
    conn = Conn((header(tail=b"hello"), b""))
    conn.partial = 2
    task = h.start()
    await h.connect(conn)
    assert (await h.pipe.until(FrameType.DATA)).payload == b"hello"
    await h.pipe.until(FrameType.HALF_CLOSE)
    assert [f.frame_type for f in h.pipe.sent[:3]] == [
        FrameType.OPEN,
        FrameType.DATA,
        FrameType.HALF_CLOSE,
    ]
    await h.pipe.feed(frame(FrameType.DATA, b"reply"))
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await settle(lambda: conn.shutdowns == 1)
    assert conn.written == HTTP_OK + b"reply" and not conn.closed
    assert [
        struct.unpack("!I", f.payload)[0] for f in h.pipe.sent if f.frame_type is FrameType.WINDOW
    ] == [2, 2, 1]
    assert h.relay._streams[1].send_credit == CREDIT - 5
    await h.pipe.feed(window(5))
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.relay._streams)
    assert conn.closed and h.relay._terminal_ids == {1}
    result = await h.stop(task)
    assert (result.data_received, result.data_emitted, result.unknown_write_bytes) == (5, 5, 0)


@pytest.mark.asyncio
async def test_early_ack_during_send_completion_observes_commit() -> None:
    h = Harness()
    seen = asyncio.Event()

    async def hook(row: Frame) -> None:
        if row.frame_type is FrameType.DATA:
            await h.pipe.feed(window(len(row.payload)))
            await settle(lambda: h.relay._streams[1].acked == 3)
            seen.set()

    h.pipe.hook = hook
    task = h.start()
    conn = await h.connect(Conn((header(tail=b"abc"), b"")))
    await h.pipe.until(FrameType.HALF_CLOSE)
    assert seen.is_set() and h.relay._streams[1].send_credit == CREDIT
    await h.stop(task)
    assert conn.closed


@pytest.mark.asyncio
async def test_ack_for_queued_not_published_data_is_fatal() -> None:
    h = Harness()
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.DATA
    task = h.start()
    conn = await h.connect(Conn((header(tail=b"abc"),)))
    await settle(lambda: h.relay._streams[1].queued_send == 3)
    await h.pipe.feed(window(3))
    result = await task
    assert result.reason is EndReason.PROTOCOL and conn.closed
    assert result.data_emitted == 0 and len(h.pipe.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, 1, 65537])
async def test_unsolicited_or_overcredit_window(amount: int) -> None:
    h = Harness()
    task = h.start()
    await h.connect()
    await h.pipe.feed(window(amount))
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_duplicate_ack_cannot_restore_credit_twice() -> None:
    h = Harness()
    task = h.start()
    await h.connect(Conn((header(tail=b"abc"),)))
    await h.pipe.until(FrameType.DATA)
    await h.pipe.feed(window(3))
    await h.pipe.feed(window(3))
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_send_credit_sixty_four_kib_withheld_ack_and_deadline() -> None:
    h = Harness(deadline=0.04)
    task = h.start()
    conn = await h.connect(Conn((header(), b"x" * 65536)))
    await settle(lambda: h.relay._streams[1].emitted == CREDIT)
    stream = h.relay._streams[1]
    assert stream.send_credit == stream.queued_send == 0 and stream.acked == 0
    assert len(conn.read_sizes) == 5
    assert (await task).reason is EndReason.DEADLINE


@pytest.mark.asyncio
async def test_receive_queue_credit_cap_and_no_ack_before_consumption() -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect()
    conn.write_gate = asyncio.Event()
    conn.write_entered.clear()
    for _ in range(4):
        await h.pipe.feed(frame(FrameType.DATA, b"x" * 16384))
    await conn.write_entered.wait()
    await settle(lambda: h.relay._streams[1].queued_receive == CREDIT)
    stream = h.relay._streams[1]
    assert stream.receive_credit == 0 and stream.consumed == stream.granted == 0
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)
    await h.pipe.feed(frame(FrameType.DATA, b"x"))
    result = await task
    assert result.reason is EndReason.PROTOCOL and conn.closed
    assert result.data_received == CREDIT and result.unknown_write_bytes == 16384


@pytest.mark.asyncio
async def test_receive_credit_only_granted_on_window_commit() -> None:
    h = Harness()
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.WINDOW
    task = h.start()
    conn = await h.connect()
    await h.pipe.feed(frame(FrameType.DATA, b"abc"))
    await settle(lambda: h.relay._streams[1].consumed == 3)
    stream = h.relay._streams[1]
    assert conn.written == HTTP_OK + b"abc"
    assert stream.receive_credit == CREDIT - 3 and stream.granted == 0
    assert stream.queued_receive == 0
    h.pipe.gate.set()
    await h.pipe.until(FrameType.WINDOW)
    assert stream.granted == 3 and stream.receive_credit == CREDIT
    await h.stop(task)


@pytest.mark.asyncio
async def test_receive_eof_drains_before_shutdown_opposite_direction_remains_live() -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect()
    conn.write_gate = asyncio.Event()
    await h.pipe.feed(frame(FrameType.DATA, b"abc"))
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await settle(lambda: h.relay._streams[1].remote_half)
    assert not conn.shutdowns
    conn.write_gate.set()
    await settle(lambda: conn.shutdowns == 1)
    conn.reads.extend((b"opposite", b""))
    conn.read_gate.set()
    assert (await h.pipe.until(FrameType.DATA)).payload == b"opposite"
    await h.pipe.until(FrameType.HALF_CLOSE)
    assert conn.written == HTTP_OK + b"abc" and not conn.closed
    await h.stop(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("last", [FrameType.WINDOW, FrameType.HALF_CLOSE])
async def test_normal_close_during_published_inflight_final_emission(last: FrameType) -> None:
    h = Harness()
    close_seen = asyncio.Event()
    release = asyncio.Event()
    conn = Conn((header(), b""))
    conn.shutdown_gate = asyncio.Event()  # CLOSE need not wait for shutdown_write.

    async def hook(row: Frame) -> None:
        if row.frame_type is last:
            await h.pipe.feed(frame(FrameType.CLOSE))
            await settle(lambda: conn.closed)
            assert h.relay._streams[1].emissions > 0
            close_seen.set()
            await release.wait()

    task = h.start()
    if last is FrameType.HALF_CLOSE:
        # Inject remote half before admitting; recv will process OPEN_OK first.
        h.pipe.hook = hook
        await h.acceptor.input.put(conn)
        await h.pipe.until(FrameType.OPEN)
        await h.pipe.feed(frame(FrameType.OPEN_OK))
        await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    else:
        await h.connect(conn)
        await h.pipe.until(FrameType.HALF_CLOSE)
        h.pipe.hook = hook
        await h.pipe.feed(frame(FrameType.DATA, b"abc"))
        await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await asyncio.wait_for(close_seen.wait(), 1)
    assert not task.done() and 1 in h.relay._streams
    release.set()
    await settle(lambda: not h.relay._streams)
    assert not h.relay._locals
    assert (await h.stop(task)).reason is EndReason.EOF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        frame(FrameType.OPEN, b"openai.com:443"),
        frame(FrameType.RESULT, b"x", 0),
        Frame(cast(FrameType, 2), 1, b""),
        Frame(FrameType.OPEN_OK, True, b""),
        Frame(FrameType.DATA, 1, cast(bytes, bytearray(b"x"))),
        frame(FrameType.WINDOW, b"x"),
        frame(FrameType.HALF_CLOSE, b"x"),
        frame(FrameType.OPEN_ERROR, b"\xff"),
        frame(FrameType.OPEN_OK, stream=0),
        frame(FrameType.DATA, b"x" * 16385),
        frame(FrameType.OPEN_OK, stream=41),
        frame(FrameType.DATA),
    ],
)
async def test_forged_core_frame_shape_or_unsupported_type_is_fatal(bad: Frame) -> None:
    h = Harness()
    task = h.start()
    await h.connect()
    await h.pipe.feed(bad)
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [frame(FrameType.DATA, b"x"), window(1), frame(FrameType.HALF_CLOSE), frame(FrameType.CLOSE)],
)
async def test_pending_stream_cannot_receive_data_or_controls_before_reply(bad: Frame) -> None:
    h = Harness()
    task = h.start()
    conn = Conn((header(),))
    await h.acceptor.input.put(conn)
    await h.pipe.until(FrameType.OPEN)
    await h.pipe.feed(bad)
    assert (await task).reason is EndReason.PROTOCOL and conn.closed
    assert not conn.written


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        frame(FrameType.DATA, b"x"),
        frame(FrameType.HALF_CLOSE),
        frame(FrameType.OPEN_OK),
    ],
)
async def test_half_closed_data_duplicate_half_or_reply_fails(bad: Frame) -> None:
    h = Harness()
    task = h.start()
    await h.connect()
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.feed(bad)
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_early_gateway_close_is_abort_with_one_reply_and_stale_close_fails() -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect(Conn((header(tail=b"abc"), b"")))
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.feed(frame(FrameType.CLOSE))  # Unacknowledged DATA: an abort now.
    await h.pipe.until(FrameType.CLOSE)  # Exactly one reply.
    await settle(lambda: not h.relay._streams)
    assert conn.closed and not task.done() and not h.relay._aborting
    assert [f.frame_type for f in h.pipe.sent].count(FrameType.CLOSE) == 1
    await h.pipe.feed(frame(FrameType.CLOSE))  # Stale identity after the reply.
    assert (await task).reason is EndReason.PROTOCOL
    h = Harness()
    task = h.start()
    await h.connect(Conn((header(), b"")))
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.relay._streams)
    await h.pipe.feed(frame(FrameType.CLOSE))
    assert (await task).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["in", "out"])
async def test_aggregate_data_ceiling_both_directions(direction: str) -> None:
    h = Harness(limit=5)
    task = h.start()
    conn = await h.connect(Conn((header(tail=b"abc"),)))
    await h.pipe.until(FrameType.DATA)
    if direction == "in":
        await h.pipe.feed(frame(FrameType.DATA, b"123"))
    else:
        conn.reads.append(b"123")
        conn.read_gate.set()
    result = await task
    assert result.reason is EndReason.BUDGET
    assert result.data_received + result.data_emitted == 3 and conn.closed


@pytest.mark.asyncio
async def test_budget_includes_unpublished_reservations_and_never_resets() -> None:
    h = Harness(limit=5)
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.DATA
    task = h.start()
    await h.connect(Conn((header(tail=b"abc"),)))
    await settle(lambda: h.relay._data_reserved == 3)
    await h.pipe.feed(frame(FrameType.DATA, b"123"))
    result = await task
    assert result.reason is EndReason.BUDGET and result.data_emitted == 0
    h = Harness(limit=5)
    task = h.start()
    await h.connect(Conn((header(tail=b"abc"), b"")))
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(window(3))
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.relay._locals)
    await h.connect(Conn((header(tail=b"123"),)), stream=2)
    result = await task
    assert result.reason is EndReason.BUDGET and result.data_emitted == 3
    assert result.submitted_opens == 2 and result.local_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "twice"])
async def test_send_contract_missing_or_duplicate_commit(kind: str) -> None:
    h = Harness()
    h.pipe.commit_mode = kind
    task = h.start()
    conn = Conn((header(),))
    await h.acceptor.input.put(conn)
    assert (await task).reason is EndReason.PROTOCOL and conn.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["200", "tunnel"])
async def test_cancel_unknown_partial_write_no_window_or_false_zero(stage: str) -> None:
    h = Harness()
    task = h.start()
    conn = Conn((header(),))
    if stage == "tunnel":
        await h.connect(conn)
    else:
        await h.acceptor.input.put(conn)
        await h.pipe.until(FrameType.OPEN)
    conn.write_gate = asyncio.Event()
    conn.unknown_partial = True
    conn.write_entered.clear()
    if stage == "200":
        await h.pipe.feed(frame(FrameType.OPEN_OK))
    else:
        await h.pipe.feed(frame(FrameType.DATA, b"abc"))
    await conn.write_entered.wait()
    task.cancel()
    result = await task
    assert result.reason is EndReason.CANCELLED and result.unknown_write_bytes > 0
    assert conn.written == (b"H" if stage == "200" else HTTP_OK + b"a")
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)
    assert conn.closed and not h.relay.pending_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("actual", [0, -1, True, 100])
async def test_invalid_local_partial_count_fail_closed(actual: int) -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect()
    conn.write_result = actual
    await h.pipe.feed(frame(FrameType.DATA, b"abc"))
    assert (await task).reason is EndReason.IO and conn.closed
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [cast(bytes, bytearray(b"x")), b"x" * 16385])
async def test_untrusted_reader_count_or_mutable_bytes_fail(data: bytes) -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect()
    # First pump read may already be blocked; release it then return bad result.
    conn.reads.append(b"ok")
    conn.read_result = data
    conn.read_gate.set()
    assert (await task).reason is EndReason.IO and conn.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["eof", "accept_eof", "deadline", "cancel", "send", "receive"])
async def test_terminal_paths_own_and_close_every_task(end: str) -> None:
    h = Harness(deadline=0.03 if end == "deadline" else 1)
    conn = Conn((b"CONNECT ",))
    task = h.start()
    await h.acceptor.input.put(conn)
    await conn.read_entered.wait()
    if end == "eof":
        await h.pipe.feed(None)
    elif end == "accept_eof":
        await h.acceptor.input.put(None)
    elif end == "cancel":
        task.cancel()
    elif end == "receive":
        h.pipe.receive_error = True
        await h.pipe.feed(None)
    elif end == "send":
        h.pipe.send_error = True
        await h.acceptor.input.put(Conn((header(),)))
    result = await task
    expected = {
        "eof": EndReason.EOF,
        "accept_eof": EndReason.EOF,
        "deadline": EndReason.DEADLINE,
        "cancel": EndReason.CANCELLED,
    }.get(end, EndReason.IO)
    assert result.reason is expected and result.status is RelayStatus.INCOMPLETE
    assert conn.closed and h.pipe.closed and h.acceptor.closed
    assert not h.relay.pending_tasks and not result.cleanup_failed
    with pytest.raises(RuntimeError):
        await h.relay.run()


@pytest.mark.asyncio
async def test_frame_count_ceiling_includes_controls_independently() -> None:
    h = Harness(deadline=3)
    task = h.start()
    await h.connect()
    # One-byte DATA, actually consumed/credited, gives legal traffic until cap.
    for _ in range(4096):
        if task.done():
            break
        await h.pipe.feed(frame(FrameType.DATA, b"x"))
        await asyncio.sleep(0)
    result = await task
    assert result.reason is EndReason.PROTOCOL and result.received_frames == 4097
    assert result.sent_frames <= 4096


@pytest.mark.asyncio
async def test_output_frame_cap_includes_open_halfclose_and_windows() -> None:
    h = Harness(deadline=3)
    conn = Conn((header(),) + (b"x",) * 4096)

    async def ack(row: Frame) -> None:
        if row.frame_type is FrameType.DATA:
            await h.pipe.feed(window(1))

    h.pipe.hook = ack
    task = h.start()
    await h.acceptor.input.put(conn)
    await h.pipe.until(FrameType.OPEN)
    await h.pipe.feed(frame(FrameType.OPEN_OK))
    result = await task
    assert result.reason is EndReason.PROTOCOL and result.sent_frames == 4096
    assert len(h.pipe.sent) == 4096 and conn.closed


@pytest.mark.asyncio
async def test_four_stream_output_round_robin_and_bounded_owned_tasks() -> None:
    h = Harness()
    task = h.start()
    conns = [await h.connect(stream=i) for i in range(1, 5)]
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.DATA
    for conn in conns:
        conn.reads.extend((b"x" * 16384, b"x" * 16384))
        conn.read_gate.set()
    await settle(lambda: sum(s.queued_send for s in h.relay._streams.values()) == CREDIT)
    assert len(h.relay.pending_tasks) <= 11  # accept + recv + send + 2 per stream
    assert all(s.output.qsize() <= 4 and s.emissions <= 2 for s in h.relay._streams.values())
    h.pipe.gate.set()
    await settle(lambda: sum(f.frame_type is FrameType.DATA for f in h.pipe.sent) == 8)
    ids = [f.stream_id for f in h.pipe.sent if f.frame_type is FrameType.DATA]
    assert len(set(ids[:4])) == 4
    await h.stop(task)
    assert all(conn.closed for conn in conns)


@pytest.mark.asyncio
async def test_slow_cooperative_cleanup_is_bounded_and_survivor_retained() -> None:
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowConn(Conn):
        async def read(self, maxsize: int) -> bytes:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                raise
            return b""

    h = Harness()
    relay = Relay(h.pipe, h.acceptor, deadline=0.03, cleanup_grace=0.02)
    conn = SlowConn()
    task = asyncio.create_task(relay.run())
    try:
        await h.acceptor.input.put(conn)
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(task, 0.5)
        assert result.reason is EndReason.DEADLINE and cleaning.is_set()
        assert result.cleanup_failed and result.pending_tasks == 1
        assert len(relay.pending_tasks) == 1 and conn.closed
        assert h.pipe.closed and h.acceptor.closed
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*relay.pending_tasks, return_exceptions=True), 1)
        if not task.done():
            task.cancel()
            await task
    assert not relay.pending_tasks


@pytest.mark.asyncio
async def test_retained_write_reports_unknown_before_cancellation_finishes() -> None:
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowWriter(Conn):
        async def write(self, data: bytes) -> int:
            self.written.extend(data[:1])
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                raise
            return 0

    h = Harness()
    relay = Relay(h.pipe, h.acceptor, deadline=0.03, cleanup_grace=0.02)
    conn = SlowWriter((header(),))
    task = asyncio.create_task(relay.run())
    try:
        await h.acceptor.input.put(conn)
        await h.pipe.until(FrameType.OPEN)
        await h.pipe.feed(frame(FrameType.OPEN_OK))
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(task, 0.5)
        assert cleaning.is_set() and result.cleanup_failed and result.pending_tasks == 1
        assert result.unknown_write_bytes == len(HTTP_OK) and conn.written == b"H"
        assert len(h.pipe.sent) == 1 and conn.closed
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*relay.pending_tasks, return_exceptions=True), 1)
        if not task.done():
            task.cancel()
            await task
    assert not relay.pending_tasks


@pytest.mark.asyncio
async def test_accept_returning_after_cleanup_cannot_spawn_or_leak_connection() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    conn = Conn((header(),))

    class LateAcceptor(Acceptor):
        async def accept(self) -> Connection | None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                return conn
            return None

    acceptor, pipe = LateAcceptor(), Pipe()
    relay = Relay(pipe, acceptor, deadline=0.03, cleanup_grace=0.02)
    task = asyncio.create_task(relay.run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(task, 0.5)
        assert result.cleanup_failed and result.pending_tasks == 1
        assert not conn.closed
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*relay.pending_tasks, return_exceptions=True), 1)
        if not task.done():
            task.cancel()
            await task
    assert conn.closed and not conn.read_sizes and not pipe.sent
    assert not relay.pending_tasks and not relay._locals


@pytest.mark.asyncio
async def test_accepted_connection_closed_if_stream_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected construction failure")

    monkeypatch.setattr("scripts.web_fetch_pilot_relay._Stream", fail)
    h = Harness()
    conn = Conn((header(),))
    task = h.start()
    await h.acceptor.input.put(conn)
    result = await task
    assert result.reason is EndReason.IO and conn.closed
    assert not h.relay.pending_tasks and not h.pipe.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("refuse_first", [False, True])
async def test_gateway_and_relay_independent_state_interoperate_in_memory(
    refuse_first: bool,
) -> None:
    browser_pipe, gateway_pipe = Pipe(), Pipe()
    acceptor = Acceptor()
    local = Conn((header(tail=b"hello"), b""))
    remote = Conn((b"reply", b""))
    remote.peer = ("8.8.8.8", 443)

    async def resolve(host: str) -> Sequence[str]:
        assert host == "openai.com"
        return ("8.8.8.8",)

    async def connect(ip: str, port: int) -> Connection:
        assert (ip, port) == remote.peer
        return remote

    browser_pipe.hook = gateway_pipe.feed
    gateway_pipe.hook = browser_pipe.feed
    relay = Relay(browser_pipe, acceptor, deadline=1)
    gateway = Gateway(("openai.com",), (), gateway_pipe, resolve, connect, deadline=1)
    relay_task = asyncio.create_task(relay.run())
    gateway_task = asyncio.create_task(gateway.run())
    if refuse_first:
        refused = Conn((header("unlisted.example"),))
        await acceptor.input.put(refused)
        await settle(lambda: refused.closed)
        assert refused.written == HTTP_REFUSED
        assert gateway._refused == {1} and relay._terminal_ids == {1}
        assert sum(f.frame_type is FrameType.OPEN_ERROR for f in gateway_pipe.sent) == 1
    await acceptor.input.put(local)
    await settle(lambda: local.closed and remote.closed)
    assert local.written == HTTP_OK + b"reply" and remote.written == b"hello"
    expected = {1, 2} if refuse_first else {1}
    # The relay retires after its single reply to the gateway's CLOSE is published.
    await settle(lambda: relay._terminal_ids == expected and not gateway._streams)
    assert not gateway._closed_ids  # The reply was consumed exactly once.
    assert len(gateway.ledger.snapshot().attempts) == (2 if refuse_first else 1)
    assert [f.stream_id for f in browser_pipe.sent if f.frame_type is FrameType.OPEN] == (
        [1, 2] if refuse_first else [1]
    )
    await browser_pipe.feed(None)
    await gateway_pipe.feed(None)
    relay_result, gateway_result = await asyncio.gather(relay_task, gateway_task)
    assert relay_result.reason is gateway_result.reason is EndReason.EOF
    assert relay_result.data_received == gateway_result.read_bytes == 5
    assert relay_result.data_emitted == gateway_result.written_bytes == 5
    assert not relay.pending_tasks and not gateway.pending_tasks


def test_fixed_defaults_and_only_lowered_named_offline_budget_seam() -> None:
    h = Harness()
    assert h.relay._data_limit == 32 * 1024 * 1024
    assert Relay(h.pipe, h.acceptor)._deadline == 45
    assert Relay(h.pipe, h.acceptor)._cleanup_grace == 2
    for limit in (0, True, 33554433):
        with pytest.raises(ValueError):
            Relay(h.pipe, h.acceptor, test_only_data_limit=limit)


FINAL = (
    b'{"extraction_version":"pilot-1","final_url":"https://openai.com/","original_url":'
    b'"https://openai.com/","status":"success","title":"t"}'
)


def results(pipe: Pipe) -> list[Frame]:
    return [row for row in pipe.sent if row.frame_type is FrameType.RESULT]


@pytest.mark.asyncio
async def test_finish_result_chunks_in_order_then_final_and_collector_accepts() -> None:
    h = Harness()
    task = h.start()
    await asyncio.sleep(0)  # run() has started; calling earlier is refused.
    content = ("é" * 20_000).encode("utf-8")  # 40 KB; chunk edges split code points.
    await asyncio.wait_for(h.relay.finish_result(content, FINAL), 1)
    outcome = await asyncio.wait_for(task, 1)
    assert outcome.status is RelayStatus.RESULT_COMMITTED and outcome.reason is EndReason.EOF
    rows = results(h.pipe)
    assert all(row.stream_id == 0 for row in rows)
    assert [row.payload[0] for row in rows] == [1, 1, 1, 2]
    assert b"".join(row.payload[1:] for row in rows[:-1]) == content
    assert rows[-1].payload[1:] == FINAL
    assert h.acceptor.closed and h.pipe.closed and not h.relay.pending_tasks
    collector = ResultCollector("https://openai.com/", ("openai.com",), "pilot-1")
    assert all(collector.observe(row) for row in rows)
    collector.end_of_stream()
    assert collector.candidate().content == content


@pytest.mark.asyncio
async def test_finish_result_waits_for_pending_tunnel_and_no_open_follows() -> None:
    h = Harness()
    task = h.start()
    conn = Conn((header(),))
    await h.acceptor.input.put(conn)
    await h.pipe.until(FrameType.OPEN)
    finishing = asyncio.create_task(h.relay.finish_result(b"", FINAL))
    await asyncio.sleep(0.05)
    assert not finishing.done() and not results(h.pipe)  # Local stream still live.
    await h.pipe.feed(frame(FrameType.OPEN_ERROR, b"refused"))
    await asyncio.wait_for(finishing, 1)
    outcome = await asyncio.wait_for(task, 1)
    assert outcome.status is RelayStatus.RESULT_COMMITTED
    kinds = [row.frame_type for row in h.pipe.sent]
    assert kinds == [FrameType.OPEN, FrameType.RESULT]
    assert bytes(conn.written) == HTTP_REFUSED and conn.closed


@pytest.mark.asyncio
async def test_handshake_completing_after_result_start_is_busy_without_open() -> None:
    h = Harness()
    task = h.start()
    conn = Conn()  # Header withheld: accepted, no IPC identity yet.
    await h.acceptor.input.put(conn)
    await conn.read_entered.wait()
    finishing = asyncio.create_task(h.relay.finish_result(b"", FINAL))
    await asyncio.sleep(0.05)
    assert not finishing.done()
    conn.reads.append(header())
    conn.read_gate.set()
    await asyncio.wait_for(finishing, 1)
    await asyncio.wait_for(task, 1)
    assert bytes(conn.written) == HTTP_BUSY and conn.closed
    assert [row.frame_type for row in h.pipe.sent] == [FrameType.RESULT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "final"),
    [
        ("text", FINAL),
        (b"", "final"),
        (b"", b""),
        (b"", b"x" * 4097),
        (b"\xff", FINAL),
        (b"x" * (1024 * 1024), FINAL),
    ],
)
async def test_finish_result_shape_refused_before_any_state_change(
    content: object, final: object
) -> None:
    h = Harness()
    task = h.start()
    with pytest.raises((ValueError, UnicodeDecodeError)):
        await h.relay.finish_result(content, final)  # type: ignore[arg-type]
    assert not h.acceptor.closed and not h.relay._result_started
    await h.connect()  # Tunnels still admitted.
    await h.stop(task)
    assert not results(h.pipe)


@pytest.mark.asyncio
async def test_finish_result_before_run_and_twice_refused() -> None:
    h = Harness()
    with pytest.raises(RelayFailure):
        await h.relay.finish_result(b"", FINAL)
    task = h.start()
    await asyncio.sleep(0)
    await asyncio.wait_for(h.relay.finish_result(b"", FINAL), 1)
    with pytest.raises(RelayFailure):
        await h.relay.finish_result(b"", FINAL)
    assert (await asyncio.wait_for(task, 1)).status is RelayStatus.RESULT_COMMITTED
    assert len(results(h.pipe)) == 1


@pytest.mark.asyncio
async def test_deadline_before_final_commit_raises_and_stays_incomplete() -> None:
    h = Harness(deadline=0.2)
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.RESULT
    task = h.start()
    try:
        with pytest.raises(RelayFailure):
            await asyncio.wait_for(h.relay.finish_result(b"partial", FINAL), 2)
        outcome = await asyncio.wait_for(task, 2)
    finally:
        h.pipe.gate.set()
    assert outcome.status is RelayStatus.INCOMPLETE
    assert outcome.reason is EndReason.DEADLINE
    assert not results(h.pipe)


class ResetConn(Conn):
    """Local client connection whose read/write syscall raises a peer reset."""

    def __init__(
        self,
        reads: Sequence[bytes] = (),
        *,
        read_reset: OSError | None = None,
        write_reset: OSError | None = None,
        reset_gate: asyncio.Event | None = None,
    ) -> None:
        super().__init__(reads)
        self.read_reset = read_reset
        self.write_reset = write_reset
        self.reset_gate = reset_gate

    async def read(self, maxsize: int) -> bytes:
        if self.read_reset is not None and not self.reads:
            self.read_entered.set()
            if self.reset_gate is not None:
                await self.reset_gate.wait()
            raise self.read_reset
        return await super().read(maxsize)

    async def write(self, data: bytes) -> int:
        if self.write_reset is not None and bytes(self.written).startswith(HTTP_OK):
            raise self.write_reset
        return await super().write(data)


def sent_kinds(h: Harness, stream: int = 1) -> list[FrameType]:
    return [f.frame_type for f in h.pipe.sent if f.stream_id == stream]


@pytest.mark.asyncio
async def test_local_read_reset_aborts_only_that_stream() -> None:
    h = Harness(deadline=3)
    task = h.start()
    conn = await h.connect(ResetConn((header(),), read_reset=ConnectionResetError(104, "reset")))
    await h.pipe.until(FrameType.CLOSE)
    assert sent_kinds(h) == [FrameType.OPEN, FrameType.CLOSE] and conn.closed
    await settle(lambda: h.relay._aborting == {1} and not h.relay._streams)
    await h.pipe.feed(frame(FrameType.DATA, b"stale"))  # Counted and dropped.
    await h.pipe.feed(window(1))
    await h.pipe.feed(frame(FrameType.CLOSE))  # The gateway's reply.
    await settle(lambda: not h.relay._aborting)
    await h.connect(stream=2)  # The run is alive.
    result = await h.stop(task)
    assert result.data_received == 5 and result.unknown_write_bytes == 0


@pytest.mark.asyncio
async def test_local_write_reset_aborts_without_window() -> None:
    h = Harness(deadline=3)
    task = h.start()
    conn = await h.connect(ResetConn((header(),), write_reset=BrokenPipeError(32, "pipe")))
    await h.pipe.feed(frame(FrameType.DATA, b"abc"))
    await h.pipe.until(FrameType.CLOSE)
    assert FrameType.WINDOW not in sent_kinds(h) and conn.closed
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.relay._aborting and not h.relay._streams)
    result = await h.stop(task)
    assert result.unknown_write_bytes == 0


@pytest.mark.asyncio
async def test_reset_before_open_is_local_only() -> None:
    h = Harness(deadline=3)
    task = h.start()
    conn = ResetConn(read_reset=ConnectionResetError(104, "reset"))  # Dies mid-header.
    await h.acceptor.input.put(conn)
    await settle(lambda: conn.closed and not h.relay._locals)
    assert h.pipe.sent == [] and not h.relay._aborting
    await h.connect()  # Stream 1 is still the first IPC identity.
    await h.stop(task)


class GoneConn(Conn):
    """Client that vanished while OPEN was pending: the 200 write is reset."""

    async def write(self, data: bytes) -> int:
        raise ConnectionResetError(104, "reset")


@pytest.mark.asyncio
async def test_client_gone_during_open_aborts_and_discards_late_gateway_frames() -> None:
    h = Harness(deadline=3)
    task = h.start()
    conn = GoneConn((header(),))
    await h.acceptor.input.put(conn)
    await h.pipe.until(FrameType.OPEN)
    await h.pipe.feed(frame(FrameType.OPEN_OK))
    await h.pipe.until(FrameType.CLOSE)  # The 200 write was reset: abort.
    assert sent_kinds(h) == [FrameType.OPEN, FrameType.CLOSE] and conn.closed
    await settle(lambda: h.relay._aborting == {1})
    await h.pipe.feed(frame(FrameType.DATA, b"late"))  # In flight before our CLOSE.
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.relay._aborting and not h.relay._locals)
    result = await h.stop(task)
    assert result.unknown_write_bytes == 0 and result.data_received == 4


@pytest.mark.asyncio
async def test_crossing_abort_and_gateway_close_answer_each_other() -> None:
    h = Harness(deadline=3)
    task = h.start()
    release = asyncio.Event()
    await h.connect(
        ResetConn((header(),), read_reset=ConnectionResetError(104, "reset"), reset_gate=release)
    )
    h.pipe.gate = asyncio.Event()
    h.pipe.gated_kind = FrameType.CLOSE  # Hold our abort CLOSE unpublished.
    release.set()
    await settle(lambda: 1 in h.relay._streams and h.relay._streams[1].resetting)
    await h.pipe.feed(frame(FrameType.CLOSE))  # Gateway CLOSE crosses ours.
    await settle(lambda: h.relay._streams[1].peer_closed)
    h.pipe.gate.set()
    await settle(lambda: not h.relay._streams)
    assert not h.relay._aborting  # Its CLOSE was the reply; nothing to wait for.
    await h.stop(task)


@pytest.mark.asyncio
async def test_generic_local_errors_remain_fatal() -> None:
    h = Harness()
    task = h.start()
    conn = await h.connect()
    conn.read_error = True
    conn.read_gate.set()
    assert (await task).reason is EndReason.IO


@pytest.mark.asyncio
async def test_finish_result_waits_for_outstanding_aborts() -> None:
    h = Harness(deadline=3)
    task = h.start()
    await h.connect(ResetConn((header(),), read_reset=ConnectionResetError(104, "reset")))
    await settle(lambda: h.relay._aborting == {1})
    finishing = asyncio.create_task(h.relay.finish_result(b"", FINAL))
    await asyncio.sleep(0.05)
    assert not finishing.done() and FrameType.RESULT not in [f.frame_type for f in h.pipe.sent]
    await h.pipe.feed(frame(FrameType.CLOSE))  # Reply arrives; RESULT may follow.
    await asyncio.wait_for(finishing, 1)
    assert (await asyncio.wait_for(task, 1)).status is RelayStatus.RESULT_COMMITTED
    assert h.pipe.sent[-1].frame_type is FrameType.RESULT


@pytest.mark.asyncio
async def test_unanswered_relay_aborts_are_bounded() -> None:
    h = Harness(deadline=5)
    task = h.start()
    for stream in range(1, 5):
        await h.connect(
            ResetConn((header(),), read_reset=ConnectionResetError(104, "reset")), stream=stream
        )
        await settle(lambda stream=stream: stream in h.relay._aborting)
    await h.connect(ResetConn((header(),), read_reset=ConnectionResetError(104, "reset")), stream=5)
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
