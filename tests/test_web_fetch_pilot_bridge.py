"""In-memory supervisor bridge checks; no child processes or kernel transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import cast

import pytest

from scripts.web_fetch_pilot_bridge import BridgeOutcome, BridgeReason, IPCBridge
from scripts.web_fetch_pilot_core import MAX_PAYLOAD, Frame, FrameType
from scripts.web_fetch_pilot_results import ResultCollector, ResultError

URL = "https://example.com/article"


def final(status: str = "success") -> Frame:
    record = {
        "status": status,
        "original_url": URL,
        "final_url": URL,
        "title": "Fixture",
        "extraction_version": "v1",
    }
    return Frame(FrameType.RESULT, 0, b"\x02" + json.dumps(record).encode())


async def settle(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


class Pipe:
    def __init__(self) -> None:
        self.input: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=16)
        self.sent: list[Frame] = []
        self.closed = False
        self.commit_mode = "once"
        self.send_gate: asyncio.Event | None = None
        self.send_entered = asyncio.Event()
        self.close_error = False

    async def receive(self) -> Frame | None:
        return await self.input.get()

    async def send(self, frame: Frame, commit: Callable[[], None]) -> None:
        self.send_entered.set()
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.commit_mode != "missing":
            commit()
        if self.commit_mode == "twice":
            commit()
        self.sent.append(frame)

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise OSError("fixture close failed")


class Harness:
    def __init__(self, deadline: float = 1, grace: float = 0.02) -> None:
        self.browser = Pipe()
        self.gateway = Pipe()
        self.collector = ResultCollector(URL, ("example.com",), "v1")
        self.bridge = IPCBridge(
            self.browser,
            self.gateway,
            self.collector,
            expires_at=asyncio.get_running_loop().time() + deadline,
            cleanup_grace=grace,
        )
        self.task = asyncio.create_task(self.bridge.run())

    async def finish(self) -> BridgeOutcome:
        await self.browser.input.put(final("blocked"))
        await self.browser.input.put(None)
        return await asyncio.wait_for(self.task, 1)


@pytest.mark.asyncio
async def test_result_observed_before_forward_and_never_sent_to_gateway() -> None:
    h = Harness()
    opening = Frame(FrameType.OPEN, 1, b"example.com:443")
    await h.browser.input.put(opening)
    await settle(lambda: len(h.gateway.sent) == 1)
    assert h.collector.counters["frames"] == 1
    assert h.gateway.sent == [opening]
    reply = Frame(FrameType.OPEN_OK, 1, b"")
    await h.gateway.input.put(reply)
    await settle(lambda: h.browser.sent == [reply])
    await h.browser.input.put(Frame(FrameType.RESULT, 0, b"\x01article"))
    await h.browser.input.put(final())
    await h.browser.input.put(None)
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is BridgeReason.BROWSER_FINAL_EOF
    assert outcome.candidate is not None and outcome.candidate.content == b"article"
    assert outcome.candidate.metadata.status == "success"  # child claim, not bridge success
    assert h.gateway.sent == [opening] and h.browser.sent == [reply]
    assert (outcome.browser_frames, outcome.gateway_frames) == (3, 1)
    assert outcome.browser_wire_bytes == h.collector.counters["wire_bytes"]
    assert not outcome.pending_tasks and not outcome.cleanup_failed
    assert h.browser.closed and h.gateway.closed
    with pytest.raises(RuntimeError):
        await h.bridge.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["browser", "gateway"])
async def test_eof_without_complete_browser_result_discards(side: str) -> None:
    h = Harness()
    await h.browser.input.put(Frame(FrameType.RESULT, 0, b"\x01article"))
    await settle(lambda: h.collector.counters["frames"] == 1)
    await getattr(h, side).input.put(None)
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is (
        BridgeReason.PROTOCOL if side == "browser" else BridgeReason.GATEWAY_EOF
    )
    assert outcome.candidate is None and h.gateway.sent == []
    with pytest.raises(ResultError):
        h.collector.candidate()


@pytest.mark.asyncio
@pytest.mark.parametrize("later", [Frame(FrameType.OPEN, 1, b"example.com:443"), final("blocked")])
async def test_final_blocks_new_open_and_duplicate_result_before_forward(later: Frame) -> None:
    h = Harness()
    await h.browser.input.put(final("blocked"))
    await h.browser.input.put(later)
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is BridgeReason.PROTOCOL and outcome.candidate is None
    assert not h.gateway.sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        Frame(FrameType.RESULT, 0, b"\x01x"),
        Frame(FrameType.OPEN, 1, b"example.com:443"),
        Frame(cast(FrameType, 2), 1, b""),
        Frame(FrameType.DATA, True, b"x"),
        Frame(FrameType.DATA, 1, b"x" * (MAX_PAYLOAD + 1)),
    ],
)
async def test_gateway_invalid_sender_or_shape_never_forwarded(bad: Frame) -> None:
    h = Harness()
    await h.gateway.input.put(bad)
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is BridgeReason.PROTOCOL and not h.browser.sent
    assert outcome.candidate is None


@pytest.mark.asyncio
async def test_aggregate_data_ceiling_shared_across_directions_and_streams() -> None:
    h = Harness(deadline=3)
    chunk = b"x" * MAX_PAYLOAD
    for _ in range(1024):
        await h.browser.input.put(Frame(FrameType.DATA, 1, chunk))
        await h.gateway.input.put(Frame(FrameType.DATA, 2, chunk))
    await settle(lambda: len(h.gateway.sent) == len(h.browser.sent) == 1024)
    await h.gateway.input.put(Frame(FrameType.DATA, 3, b"x"))
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is BridgeReason.LIMIT and outcome.data_bytes == 32 * 1024 * 1024
    assert outcome.candidate is None and len(h.browser.sent) == 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["browser", "gateway"])
async def test_frame_limits_include_browser_result_and_gateway_controls(side: str) -> None:
    h = Harness(deadline=3)
    for _ in range(4096):
        await getattr(h, side).input.put(Frame(FrameType.WINDOW, 1, b"\x00\x00\x00\x01"))
    destination = h.gateway if side == "browser" else h.browser
    await settle(lambda: len(destination.sent) == 4096)
    await getattr(h, side).input.put(
        final("blocked") if side == "browser" else Frame(FrameType.CLOSE, 1, b"")
    )
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is (BridgeReason.PROTOCOL if side == "browser" else BridgeReason.LIMIT)
    assert outcome.candidate is None and len(destination.sent) == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "twice"])
async def test_forward_publication_contract_failures(mode: str) -> None:
    h = Harness()
    h.gateway.commit_mode = mode
    await h.browser.input.put(Frame(FrameType.OPEN, 1, b"example.com:443"))
    assert (await asyncio.wait_for(h.task, 1)).reason is BridgeReason.PROTOCOL


@pytest.mark.asyncio
async def test_backpressure_retains_only_one_frame_per_direction_then_cancels() -> None:
    h = Harness()
    h.gateway.send_gate = asyncio.Event()
    h.browser.send_gate = asyncio.Event()
    for _ in range(4):
        await h.browser.input.put(Frame(FrameType.DATA, 1, b"x"))
        await h.gateway.input.put(Frame(FrameType.DATA, 1, b"y"))
    await h.gateway.send_entered.wait()
    await h.browser.send_entered.wait()
    assert h.browser.input.qsize() == h.gateway.input.qsize() == 3
    assert len(h.bridge.pending_tasks) == 2
    h.task.cancel()
    outcome = await asyncio.wait_for(h.task, 1)
    assert outcome.reason is BridgeReason.CANCELLED and outcome.candidate is None
    assert not h.bridge.pending_tasks and h.browser.closed and h.gateway.closed


@pytest.mark.asyncio
async def test_final_eof_cannot_hide_unknown_forward_cancel() -> None:
    h = Harness()
    h.browser.send_gate = asyncio.Event()
    await h.gateway.input.put(Frame(FrameType.DATA, 1, b"remote"))
    await h.browser.send_entered.wait()
    outcome = await h.finish()
    assert outcome.reason is BridgeReason.IO and outcome.candidate is None
    with pytest.raises(ResultError):
        h.collector.candidate()


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [-1, 0.02, 46])
async def test_absolute_deadline_expired_bounded_and_invalid(deadline: float) -> None:
    h = Harness(deadline=deadline)
    outcome = await asyncio.wait_for(h.task, 0.5)
    assert outcome.reason is (BridgeReason.PROTOCOL if deadline == 46 else BridgeReason.DEADLINE)
    assert outcome.candidate is None and not outcome.pending_tasks
    assert h.browser.closed and h.gateway.closed


@pytest.mark.asyncio
async def test_cleanup_close_failure_discards_candidate_and_closes_other_pipe() -> None:
    h = Harness()
    h.browser.close_error = True
    outcome = await h.finish()
    assert outcome.cleanup_failed and outcome.candidate is None
    assert h.browser.closed and h.gateway.closed


@pytest.mark.asyncio
async def test_slow_cleanup_retains_tasks_and_discards_candidate() -> None:
    h = Harness()
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_receive() -> Frame | None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise
        return None

    # No pump can start until this test yields to the loop.
    h.gateway.receive = slow_receive
    try:
        await entered.wait()
        outcome = await h.finish()
        assert outcome.cleanup_failed and outcome.pending_tasks == 1
        assert outcome.candidate is None and len(h.bridge.pending_tasks) == 1
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*h.bridge.pending_tasks, return_exceptions=True), 1)
        if not h.task.done():
            h.task.cancel()
            await h.task
    assert not h.bridge.pending_tasks
