"""Injected async fixtures only: no sockets, resolver, processes or actual pipes."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

import pytest

from scripts.web_fetch_pilot_core import ByteBudget, DestinationPolicyError, Frame, FrameType
from scripts.web_fetch_pilot_gateway import Connection, EndReason, Gateway, GatewayOutcome
from scripts.web_fetch_pilot_tunnels import DirectionSnapshot

PUBLIC = "8.8.8.8"


def frame(kind: FrameType, stream: int = 1, payload: bytes = b"") -> Frame:
    return Frame(kind, stream, payload)


def opening(stream: int = 1, host: str = "openai.com") -> Frame:
    return frame(FrameType.OPEN, stream, f"{host}:443".encode("ascii"))


def window(amount: int, stream: int = 1) -> Frame:
    return frame(FrameType.WINDOW, stream, struct.pack("!I", amount))


async def settle(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def gateway_half(h: Harness) -> DirectionSnapshot:
    half = h.gateway.ledger.snapshot().tunnels[0].gateway
    assert half is not None
    return half


def browser_half(h: Harness) -> DirectionSnapshot:
    half = h.gateway.ledger.snapshot().tunnels[0].browser
    assert half is not None
    return half


class Pipe:
    def __init__(self) -> None:
        self.input: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=16)
        self.output: asyncio.Queue[Frame] = asyncio.Queue(maxsize=4096)
        self.sent: list[Frame] = []
        self.closed = False
        self.hook: Callable[[Frame], Awaitable[None]] | None = None
        self.send_error = False
        self.receive_error = False
        self.commit_mode = "once"
        self.send_gate: asyncio.Event | None = None

    async def receive(self) -> Frame | None:
        row = await self.input.get()
        if self.receive_error:
            raise OSError("fake receive")
        return row

    async def send(self, row: Frame, commit: Callable[[], None]) -> None:
        if self.send_error:
            raise OSError("fake send")
        if self.send_gate is not None:
            await self.send_gate.wait()
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


class Socket:
    def getpeername(self) -> tuple[str, int]:
        return self.peer_ip, 443

    def __init__(self, reads: Sequence[bytes] = (), *, peer: str = PUBLIC) -> None:
        self.peer_ip = peer
        self.reads = deque(reads)
        self.read_gate = asyncio.Event()
        self.read_entered = asyncio.Event()
        self.write_entered = asyncio.Event()
        self.write_gate: asyncio.Event | None = None
        self.closed = False
        self.shutdowns = 0
        self.read_sizes: list[int] = []
        self.write_sizes: list[int] = []
        self.written = bytearray()
        self.partial: int | None = None
        self.write_result: int | None = None
        self.read_error = False
        self.write_error = False
        self.mutate_peer = False

    async def read(self, maxsize: int) -> bytes:
        self.read_sizes.append(maxsize)
        self.read_entered.set()
        if self.read_error:
            raise OSError("fake read")
        if self.mutate_peer:
            self.peer_ip = "1.1.1.1"
        if self.reads:
            return self.reads.popleft()
        await self.read_gate.wait()
        return self.reads.popleft() if self.reads else b""

    async def write(self, data: bytes) -> int:
        self.write_sizes.append(len(data))
        self.write_entered.set()
        if self.write_gate is not None:
            # Simulate unknown partial transfer before cancellation.
            self.written.extend(data[:1])
            await self.write_gate.wait()
        if self.write_error:
            raise OSError("fake write")
        if self.write_result is not None:
            return self.write_result
        actual = min(len(data), self.partial) if self.partial is not None else len(data)
        self.written.extend(data[:actual])
        return actual

    async def shutdown_write(self) -> None:
        self.shutdowns += 1

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(
        self,
        sock: Socket | None = None,
        *,
        answers: Sequence[str] = (PUBLIC,),
        inventory: Sequence[str] | None = (),
        budget: ByteBudget | None = None,
        deadline: float = 1,
    ) -> None:
        self.pipe = Pipe()
        self.sock = sock if sock is not None else Socket()
        self.answers = answers
        self.resolutions: list[str] = []
        self.dials: list[tuple[str, int]] = []
        self.resolver_gate: asyncio.Event | None = None
        self.connector_gate: asyncio.Event | None = None
        self.resolver_entered = asyncio.Event()
        self.connector_entered = asyncio.Event()
        self.resolve_error = False
        self.connect_errors: set[str] = set()
        self.resolve_active = 0
        self.resolve_peak = 0
        self.cancelled_resolutions = 0
        self.cancelled_connections = 0
        self.gateway = Gateway(
            ("openai.com",),
            inventory,
            self.pipe,
            self.resolve,
            self.connect,
            deadline=deadline,
            budget=budget,
        )

    async def resolve(self, host: str) -> Sequence[str]:
        self.resolutions.append(host)
        self.resolve_active += 1
        self.resolve_peak = max(self.resolve_peak, self.resolve_active)
        self.resolver_entered.set()
        try:
            if self.resolver_gate is not None:
                await self.resolver_gate.wait()
            if self.resolve_error:
                raise OSError("fake DNS")
            return self.answers
        except asyncio.CancelledError:
            self.cancelled_resolutions += 1
            raise
        finally:
            self.resolve_active -= 1

    async def connect(self, ip: str, port: int) -> Connection:
        self.dials.append((ip, port))
        self.connector_entered.set()
        try:
            if self.connector_gate is not None:
                await self.connector_gate.wait()
        except asyncio.CancelledError:
            self.cancelled_connections += 1
            raise
        if ip in self.connect_errors:
            raise OSError("fake connect")
        return self.sock

    def start(self) -> asyncio.Task[GatewayOutcome]:
        return asyncio.create_task(self.gateway.run())

    async def stop(self, task: asyncio.Task[GatewayOutcome]) -> GatewayOutcome:
        await self.pipe.feed(None)
        result = await asyncio.wait_for(task, 1)
        assert self.pipe.closed
        assert not self.gateway._tasks  # final-state ownership evidence
        return result


@pytest.mark.asyncio
async def test_full_tunnel_numeric_dial_partial_writes_and_normal_close() -> None:
    h = Harness(Socket((b"reply", b"")))
    h.sock.partial = 2
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    assert h.resolutions == ["openai.com"]
    assert h.dials == [(PUBLIC, 443)]
    await h.pipe.until(FrameType.DATA)
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"hello"))
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.feed(window(5))
    await h.pipe.until(FrameType.CLOSE)
    assert bytes(h.sock.written) == b"hello"
    assert h.sock.shutdowns == 1
    assert h.sock.closed
    assert [
        struct.unpack("!I", f.payload)[0] for f in h.pipe.sent if f.frame_type is FrameType.WINDOW
    ] == [2, 2, 1]
    assert sum(f.frame_type is FrameType.HALF_CLOSE for f in h.pipe.sent) == 1
    result = await h.stop(task)
    assert result.reason is EndReason.EOF
    assert (result.read_bytes, result.written_bytes, result.unresolved_bytes) == (5, 5, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "::1",
        "2606:4700::1111",
        "192.88.99.1",
        "8.8.8.08",
        "openai.com",
    ],
)
async def test_all_answers_validated_before_any_dial(bad: str) -> None:
    h = Harness(answers=(PUBLIC, bad))
    task = h.start()
    await h.pipe.feed(opening())
    error = await h.pipe.until(FrameType.OPEN_ERROR)
    assert 0 < len(error.payload) <= 255 and error.payload.isascii()
    assert not h.dials
    assert await h.stop(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [(), (PUBLIC,) * 9, ("1.1.1.1", PUBLIC)])
async def test_empty_excess_owned_answers(answers: Sequence[str]) -> None:
    h = Harness(answers=answers, inventory=(PUBLIC,))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_ERROR)
    assert not h.dials
    await h.stop(task)


def test_missing_inventory_closed_and_default_limit() -> None:
    with pytest.raises(DestinationPolicyError):
        Harness(inventory=None)
    h = Harness()
    assert h.gateway.budget.limit == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_validated_candidates_only_and_single_resolve() -> None:
    h = Harness(answers=("1.1.1.1", PUBLIC))
    h.connect_errors.add("1.1.1.1")
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    assert h.dials == [("1.1.1.1", 443), (PUBLIC, 443)]
    assert h.resolutions == ["openai.com"]
    await h.stop(task)


@pytest.mark.asyncio
async def test_peer_mismatch_refused_and_closed() -> None:
    h = Harness(Socket(peer="1.1.1.1"))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_ERROR)
    assert h.sock.closed
    assert not any(f.frame_type is FrameType.OPEN_OK for f in h.pipe.sent)
    await h.stop(task)


@pytest.mark.asyncio
async def test_four_parallel_pending_capacity_counts_refusal() -> None:
    h = Harness()
    h.resolver_gate = asyncio.Event()
    task = h.start()
    for stream in range(1, 6):
        await h.pipe.feed(opening(stream))
    await h.pipe.until(FrameType.OPEN_ERROR, 5)
    assert h.resolve_active == h.resolve_peak == 4
    assert len(h.gateway.ledger.snapshot().attempts) == 5
    assert h.gateway._refused == {5}
    assert not h.dials
    await h.stop(task)
    assert h.cancelled_resolutions == 4


@pytest.mark.asyncio
async def test_failed_dns_and_policy_refusal_single_reply() -> None:
    h = Harness()
    h.resolve_error = True
    task = h.start()
    await h.pipe.feed(opening(1, "other.com"))
    await h.pipe.until(FrameType.OPEN_ERROR)
    await h.pipe.feed(opening(2))
    await h.pipe.until(FrameType.OPEN_ERROR, 2)
    assert len(h.gateway.ledger.snapshot().attempts) == 2
    assert h.gateway._refused == {1}
    assert h.resolutions == ["openai.com"]
    assert len(h.pipe.sent) == 2
    await h.pipe.feed(opening(1, "other.com"))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.PROTOCOL
    assert len(h.gateway.ledger.snapshot().attempts) == 2
    assert len(h.pipe.sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [FrameType.OPEN_OK, FrameType.OPEN_ERROR, FrameType.RESULT])
async def test_unsupported_browser_frames(kind: FrameType) -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(
        frame(
            kind,
            0 if kind is FrameType.RESULT else 1,
            b"x" if kind is not FrameType.OPEN_OK else b"",
        )
    )
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
    assert not h.resolutions and not h.dials


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        Frame(cast(FrameType, 1), 1, b"openai.com:443"),
        Frame(FrameType.OPEN, True, b"openai.com:443"),
        Frame(FrameType.OPEN, 1, cast(bytes, bytearray(b"openai.com:443"))),
        frame(FrameType.HALF_CLOSE, payload=b"bad"),
    ],
)
async def test_decoded_input_shape_independently_validated(bad: Frame) -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(bad)
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
    assert not h.gateway.ledger.snapshot().attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, 1, 65537])
async def test_forged_ack_before_any_emission(amount: int) -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    await h.pipe.feed(window(amount))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.PROTOCOL and h.sock.closed


@pytest.mark.asyncio
async def test_early_ack_during_async_send_completion() -> None:
    h = Harness(Socket((b"abc", b"")))
    race_seen = asyncio.Event()

    async def hook(row: Frame) -> None:
        if row.frame_type is FrameType.DATA:
            await h.pipe.feed(window(3))
            await settle(lambda: gateway_half(h).credited == 3)
            race_seen.set()

    h.pipe.hook = hook
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    assert race_seen.is_set()
    assert (await h.stop(task)).reason is EndReason.EOF


@pytest.mark.asyncio
async def test_ack_cannot_credit_data_waiting_before_publication() -> None:
    h = Harness(Socket((b"abc",)))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    h.pipe.send_gate = asyncio.Event()
    await settle(lambda: gateway_half(h).queued_bytes == 3)
    await h.pipe.feed(window(3))
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
    assert not any(f.frame_type is FrameType.DATA for f in h.pipe.sent)


@pytest.mark.asyncio
async def test_ack_wrong_direction_and_overcredit() -> None:
    h = Harness(Socket((b"",)))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"abc"))
    await h.pipe.until(FrameType.WINDOW)
    await h.pipe.feed(window(3))  # cannot ACK browser's own consumed direction
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "connect"])
async def test_pending_close_cancels_no_late_success(stage: str) -> None:
    h = Harness()
    if stage == "resolve":
        h.resolver_gate = asyncio.Event()
        entered = h.resolver_entered
    else:
        h.connector_gate = asyncio.Event()
        entered = h.connector_entered
    task = h.start()
    await h.pipe.feed(opening())
    await entered.wait()
    await h.pipe.feed(frame(FrameType.CLOSE))
    await settle(lambda: not h.gateway._streams)
    assert not h.pipe.sent
    assert h.cancelled_resolutions + h.cancelled_connections == 1
    assert (await h.stop(task)).reason is EndReason.EOF


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["oversize", "mutable", "changed_peer", "read_error"])
async def test_untrusted_connection_read_values(problem: str) -> None:
    sock = Socket((b"x" * 16385 if problem == "oversize" else b"x",))
    if problem == "mutable":
        sock.reads = deque((cast(bytes, bytearray(b"x")),))
    sock.mutate_peer = problem == "changed_peer"
    sock.read_error = problem == "read_error"
    h = Harness(sock)
    task = h.start()
    await h.pipe.feed(opening())
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.IO and sock.closed
    assert result.read_bytes == 0 and result.unresolved_bytes > 0
    assert not any(f.frame_type is FrameType.DATA for f in h.pipe.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("actual", [0, -1, True, 4])
async def test_zero_invalid_or_oversize_partial_write(actual: int) -> None:
    h = Harness(Socket((b"",)))
    h.sock.write_result = actual
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"abc"))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.IO and h.sock.closed
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)
    assert result.unresolved_bytes == (0 if actual == 0 and type(actual) is int else 3)


@pytest.mark.asyncio
async def test_cancel_unknown_partial_write_never_refunds_or_acks() -> None:
    h = Harness(Socket((b"",)), budget=ByteBudget(8))
    h.sock.write_gate = asyncio.Event()
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"abcd"))
    await h.sock.write_entered.wait()
    task.cancel()
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.CANCELLED
    assert result.unresolved_bytes == 4 and result.written_bytes == 0
    assert h.sock.written == b"a" and h.sock.closed
    assert h.gateway.budget.is_closed
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)
    assert not h.gateway._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["read", "write"])
async def test_capped_operation_exact_exhaustion(direction: str) -> None:
    h = Harness(Socket((b"abc",) if direction == "read" else (b"",)), budget=ByteBudget(3))
    task = h.start()
    await h.pipe.feed(opening())
    if direction == "write":
        await h.pipe.until(FrameType.HALF_CLOSE)
        await h.pipe.feed(frame(FrameType.DATA, payload=b"abcdef"))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.BUDGET
    assert result.read_bytes + result.written_bytes == 3
    assert h.sock.read_sizes == [3]
    assert h.sock.write_sizes == ([3] if direction == "write" else [])
    assert result.unresolved_bytes == 0 and h.sock.closed


@pytest.mark.asyncio
async def test_overlapping_reservations_do_not_overspend() -> None:
    h = Harness(budget=ByteBudget(16385))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    await h.sock.read_entered.wait()
    h.sock.write_gate = asyncio.Event()
    await h.pipe.feed(frame(FrameType.DATA, payload=b"abc"))
    await h.sock.write_entered.wait()
    assert h.gateway.budget.outstanding == 16385
    assert h.sock.write_sizes == [1]
    result = await h.stop(task)
    assert result.unresolved_bytes == 16385
    assert result.read_bytes + result.written_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["eof", "deadline", "cancel", "receive_error", "send_error"])
async def test_run_termination_cleans_every_owned_task(end: str) -> None:
    h = Harness(deadline=0.02 if end == "deadline" else 1)
    h.resolver_gate = asyncio.Event()
    task = h.start()
    await h.pipe.feed(opening())
    await h.resolver_entered.wait()
    if end == "eof":
        await h.pipe.feed(None)
    elif end == "cancel":
        task.cancel()
    elif end == "receive_error":
        h.pipe.receive_error = True
        await h.pipe.feed(None)
    elif end == "send_error":
        h.pipe.send_error = True
        await h.pipe.feed(opening(2, "other.com"))
    result = await asyncio.wait_for(task, 1)
    expected = {
        "eof": EndReason.EOF,
        "deadline": EndReason.DEADLINE,
        "cancel": EndReason.CANCELLED,
    }.get(end, EndReason.IO)
    assert result.reason is expected
    assert h.cancelled_resolutions == 1 and h.pipe.closed
    assert not h.gateway._tasks
    with pytest.raises(RuntimeError):
        await h.gateway.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "twice"])
async def test_frameio_publication_contract_enforced(mode: str) -> None:
    h = Harness()
    h.pipe.commit_mode = mode
    task = h.start()
    await h.pipe.feed(opening())
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
    assert h.sock.closed


@pytest.mark.asyncio
async def test_independent_input_frame_count_limit() -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_OK)
    for _ in range(4096):
        await h.pipe.feed(frame(FrameType.DATA))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.PROTOCOL and result.received_frames == 4097


@pytest.mark.asyncio
async def test_independent_output_frame_count_limit() -> None:
    h = Harness(Socket((b"x",) * 4096))
    # Only 4 KiB is needed under the initial 64 KiB credit. Do not replenish:
    # this isolates the output cap from input-frame processing and its own cap.
    task = h.start()
    await h.pipe.feed(opening())
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.PROTOCOL
    assert result.sent_frames == 4096 and len(h.pipe.sent) == 4096
    assert result.received_frames == 1
    assert h.pipe.closed and h.sock.closed
    assert not result.cleanup_failed and result.pending_tasks == 0
    assert not h.gateway.pending_tasks


@pytest.mark.asyncio
async def test_credit_queue_bound_and_withheld_ack_deadline() -> None:
    h = Harness(Socket((b"x" * 16384,) * 4), deadline=0.04)
    task = h.start()
    await h.pipe.feed(opening())
    await settle(lambda: sum(f.frame_type is FrameType.DATA for f in h.pipe.sent) == 4)
    snap = gateway_half(h)
    assert snap.send_credit == 0 and snap.drained - snap.credited == 65536
    assert snap.queued_bytes == 0 and len(h.sock.read_sizes) == 4
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.DEADLINE


@pytest.mark.asyncio
async def test_refused_stale_data_is_fatal() -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(opening(host="other.com"))
    await h.pipe.until(FrameType.OPEN_ERROR)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"x"))
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_forty_refusals_are_counted_and_emitted_once() -> None:
    h = Harness()
    task = h.start()
    for stream in range(1, 41):
        await h.pipe.feed(opening(stream, "other.com"))
        await h.pipe.until(FrameType.OPEN_ERROR, stream)
    assert len(h.gateway.ledger.snapshot().attempts) == len(h.gateway._refused) == 40
    assert len(h.pipe.sent) == 40 and not h.dials and not h.resolutions
    await h.pipe.feed(opening(41))
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL
    assert len(h.pipe.sent) == 40


@pytest.mark.asyncio
async def test_rebinding_new_admission_does_not_reuse_previous_answers() -> None:
    h = Harness(Socket((b"",)))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.until(FrameType.CLOSE)
    h.answers = ("127.0.0.1",)
    await h.pipe.feed(opening(2))
    await h.pipe.until(FrameType.OPEN_ERROR, 2)
    assert h.resolutions == ["openai.com", "openai.com"]
    assert h.dials == [(PUBLIC, 443)]
    await h.stop(task)


@pytest.mark.asyncio
async def test_budget_does_not_reset_after_normal_close() -> None:
    h = Harness(Socket((b"abc", b"")), budget=ByteBudget(8))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(window(3))
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await h.pipe.until(FrameType.CLOSE)
    second = Socket((b"12345",))

    async def connect(ip: str, port: int) -> Connection:
        assert (ip, port) == (PUBLIC, 443)
        return second

    h.gateway._connector = connect
    await h.pipe.feed(opening(2))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.BUDGET and result.read_bytes == 8
    assert second.read_sizes == [5] and second.closed


@pytest.mark.asyncio
async def test_four_sockets_bounded_fair_output_and_cleanup() -> None:
    sockets = [Socket() for _ in range(4)]
    h = Harness()
    next_socket = iter(sockets)

    async def connect(ip: str, port: int) -> Connection:
        assert (ip, port) == (PUBLIC, 443)
        return next(next_socket)

    h.gateway._connector = connect
    task = h.start()
    for stream in range(1, 5):
        await h.pipe.feed(opening(stream))
        await h.pipe.until(FrameType.OPEN_OK, stream)
    await settle(lambda: all(s.read_entered.is_set() for s in sockets))
    gate = asyncio.Event()
    h.pipe.send_gate = gate
    for sock in sockets:
        sock.reads.extend((b"x" * 16384, b"x" * 16384))
        sock.read_gate.set()
    await settle(
        lambda: (
            sum(
                t.gateway.queued_bytes
                for t in h.gateway.ledger.snapshot().tunnels
                if t.gateway is not None
            )
            == 65536
        )
    )
    assert all(s.output.qsize() <= 1 for s in h.gateway._streams.values())
    gate.set()
    await settle(lambda: sum(f.frame_type is FrameType.DATA for f in h.pipe.sent) == 8)
    data_ids = [f.stream_id for f in h.pipe.sent if f.frame_type is FrameType.DATA]
    assert len(set(data_ids[:4])) == 4
    result = await h.stop(task)
    assert result.read_bytes == 131072 and all(s.closed for s in sockets)
    assert not h.gateway._tasks


@pytest.mark.asyncio
async def test_browser_receive_credit_bounds_pending_socket_writes() -> None:
    h = Harness(Socket((b"",)))
    h.sock.write_gate = asyncio.Event()
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    for _ in range(4):
        await h.pipe.feed(frame(FrameType.DATA, payload=b"x" * 16384))
    await h.sock.write_entered.wait()
    await settle(lambda: browser_half(h).queued_bytes == 65536)
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"x"))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.PROTOCOL
    assert result.unresolved_bytes == 16384 and h.sock.closed


@pytest.mark.asyncio
async def test_both_half_closed_wait_for_ack_and_then_close() -> None:
    h = Harness(Socket((b"abc", b"")))
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.HALF_CLOSE))
    await settle(lambda: h.sock.shutdowns == 1)
    assert not h.sock.closed
    assert not any(f.frame_type is FrameType.CLOSE for f in h.pipe.sent)
    await h.pipe.feed(window(3))
    await h.pipe.until(FrameType.CLOSE)
    assert h.sock.closed
    await h.pipe.feed(window(3))
    assert (await asyncio.wait_for(task, 1)).reason is EndReason.PROTOCOL


@pytest.mark.asyncio
async def test_cancelled_socket_read_keeps_unknown_reservation() -> None:
    h = Harness()
    task = h.start()
    await h.pipe.feed(opening())
    await h.sock.read_entered.wait()
    task.cancel()
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.CANCELLED
    assert result.unresolved_bytes == 16384 and result.read_bytes == 0
    assert h.sock.closed and not h.gateway._tasks


@pytest.mark.asyncio
async def test_write_error_is_terminal_without_window() -> None:
    h = Harness(Socket((b"",)))
    h.sock.write_error = True
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.HALF_CLOSE)
    await h.pipe.feed(frame(FrameType.DATA, payload=b"abc"))
    result = await asyncio.wait_for(task, 1)
    assert result.reason is EndReason.IO and result.unresolved_bytes == 3
    assert not any(f.frame_type is FrameType.WINDOW for f in h.pipe.sent)


@pytest.mark.asyncio
async def test_optional_actual_sockaddr_mismatch_refused() -> None:
    class WrongSockaddr(Socket):
        def getpeername(self) -> tuple[str, int]:
            return PUBLIC, 80

    h = Harness(WrongSockaddr())
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_ERROR)
    assert h.sock.closed
    assert not any(f.frame_type is FrameType.OPEN_OK for f in h.pipe.sent)
    await h.stop(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolve", "connect"])
async def test_three_second_operation_deadlines(stage: str) -> None:
    h = Harness(deadline=4)
    if stage == "resolve":
        h.resolver_gate = asyncio.Event()
    else:
        h.connector_gate = asyncio.Event()
        h.answers = (PUBLIC, "1.1.1.1")
    task = h.start()
    await h.pipe.feed(opening())
    async with asyncio.timeout(3.5):
        while not any(f.frame_type is FrameType.OPEN_ERROR for f in h.pipe.sent):
            await asyncio.sleep(0.01)
    assert len(h.pipe.sent) == 1
    assert len(h.dials) == (0 if stage == "resolve" else 1)
    assert h.cancelled_resolutions + h.cancelled_connections == 1
    await h.stop(task)


@pytest.mark.asyncio
async def test_missing_actual_peer_accessor_refused() -> None:
    sock = Socket()
    sock.getpeername = None  # type: ignore[assignment]
    h = Harness(sock)
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_ERROR)
    assert sock.closed
    assert not sock.read_sizes and not sock.write_sizes
    assert not any(f.frame_type is FrameType.OPEN_OK for f in h.pipe.sent)
    await h.stop(task)


@pytest.mark.asyncio
async def test_claimed_public_peer_cannot_hide_private_sockaddr() -> None:
    class WrongPeer(Socket):
        def getpeername(self) -> tuple[str, int]:
            return "127.0.0.1", 443

    h = Harness(WrongPeer())
    task = h.start()
    await h.pipe.feed(opening())
    await h.pipe.until(FrameType.OPEN_ERROR)
    assert h.sock.closed and not h.sock.read_sizes
    await h.stop(task)


@pytest.mark.asyncio
async def test_cleanup_grace_reports_and_retains_slow_cooperative_task() -> None:
    h = Harness()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    async def slow_resolve(host: str) -> Sequence[str]:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            raise
        return (PUBLIC,)

    gateway = Gateway(
        ("openai.com",),
        (),
        h.pipe,
        slow_resolve,
        h.connect,
        deadline=0.03,
        cleanup_grace=0.02,
    )
    task = asyncio.create_task(gateway.run())
    try:
        await h.pipe.feed(opening())
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(task, 0.5)
        assert cleaning.is_set() and not release.is_set()
        assert result.reason is EndReason.DEADLINE
        assert result.cleanup_failed and result.pending_tasks == 1
        owned = gateway.pending_tasks
        assert len(owned) == 1 and not owned[0].done()
        assert h.pipe.closed and gateway.budget.is_closed
        assert not h.dials and not h.pipe.sent
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*gateway.pending_tasks, return_exceptions=True), 1)
        if not task.done():
            task.cancel()
            await task
    assert not gateway.pending_tasks
