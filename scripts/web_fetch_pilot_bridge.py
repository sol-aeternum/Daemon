"""Injected supervisor IPC bridge, not a child/container completion authority.

No subprocess, container, browser, sockets or real descriptors are constructed.
The caller owns child identity, immutable policy, deadline origin and force
teardown. Final plus framed EOF yields only a bounded provisional candidate.
Any subsequent process/containment/cleanup failure must discard that candidate.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from scripts.web_fetch_pilot_core import (
    HEADER_LEN,
    MAX_FRAMES,
    TOTAL_NETWORK_LIMIT,
    Frame,
    FrameParser,
    FrameType,
    encode_frame,
)
from scripts.web_fetch_pilot_gateway import FrameIO
from scripts.web_fetch_pilot_results import ResultCandidate, ResultCollector, ResultError

_GATEWAY_TYPES = frozenset(
    {
        FrameType.OPEN_OK,
        FrameType.OPEN_ERROR,
        FrameType.DATA,
        FrameType.WINDOW,
        FrameType.HALF_CLOSE,
        FrameType.CLOSE,
    }
)


class BridgeReason(Enum):
    BROWSER_FINAL_EOF = "browser_final_eof"
    GATEWAY_EOF = "gateway_eof"
    PROTOCOL = "protocol"
    IO = "transport_error"
    LIMIT = "limit"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"


class BridgeFailure(Exception):
    def __init__(self, reason: BridgeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class BridgeOutcome:
    """No success flag: process exit, classification and teardown remain unproven."""

    reason: BridgeReason
    candidate: ResultCandidate | None
    browser_frames: int
    gateway_frames: int
    browser_wire_bytes: int
    gateway_wire_bytes: int
    data_bytes: int
    cleanup_failed: bool
    pending_tasks: int


class IPCBridge:
    """Single-use, two bounded pumps with no forwarding queue or detached tasks.

    expires_at is a TRUSTED absolute event-loop monotonic deadline from child
    start, not 45 seconds renewed on bridge start. Caller must establish that
    origin; only a <=45-second remaining ceiling can be checked here. Each pump
    retains at most one bounded frame while its destination applies backpressure.
    Real FrameIO qualification and executable boundary review remain prerequisites.
    """

    def __init__(
        self,
        browser: FrameIO,
        gateway: FrameIO,
        results: ResultCollector,
        *,
        expires_at: float,
        cleanup_grace: float = 2.0,
    ) -> None:
        if type(expires_at) not in (int, float) or not math.isfinite(expires_at):
            raise ValueError("finite absolute run deadline required")
        if not math.isfinite(cleanup_grace) or not 0 < cleanup_grace <= 2:
            raise ValueError("cleanup grace outside pilot ceiling")
        self._browser = browser
        self._gateway = gateway
        self._results = results
        self._expires_at = expires_at
        self._cleanup_grace = cleanup_grace
        self._tasks: set[asyncio.Task[None]] = set()
        self._end: asyncio.Future[BridgeReason] | None = None
        self._used = self._stopping = False
        self._forward_failed = False
        self._background_failure: BridgeReason | None = None
        self._browser_frames = self._gateway_frames = 0
        self._browser_wire = self._gateway_wire = self._data = 0

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(task for task in self._tasks if not task.done())

    def _finish(self, reason: BridgeReason) -> None:
        if self._end is not None and not self._end.done():
            self._stopping = True
            self._end.set_result(reason)

    def _spawn(self, operation: Callable[[], Awaitable[None]]) -> None:
        async def guarded() -> None:
            try:
                await operation()
            except asyncio.CancelledError:
                if not self._stopping:
                    self._finish(BridgeReason.IO)
                raise
            except ResultError:
                self._background_failure = BridgeReason.PROTOCOL
                self._finish(BridgeReason.PROTOCOL)
            except BridgeFailure as exc:
                self._background_failure = exc.reason
                self._finish(exc.reason)
            except Exception:
                self._background_failure = BridgeReason.IO
                self._finish(BridgeReason.IO)

        task = asyncio.create_task(guarded(), name="pilot-bridge")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _shape(frame: Frame) -> None:
        if (
            type(frame) is not Frame
            or type(frame.frame_type) is not FrameType
            or type(frame.stream_id) is not int
            or type(frame.payload) is not bytes
        ):
            raise BridgeFailure(BridgeReason.PROTOCOL)
        try:
            parser = FrameParser()
            list(parser.feed(encode_frame(frame.frame_type, frame.stream_id, frame.payload)))
            parser.end_of_stream()
        except Exception:
            raise BridgeFailure(BridgeReason.PROTOCOL) from None

    def _count_data(self, frame: Frame) -> None:
        if frame.frame_type is FrameType.DATA:
            if self._data + len(frame.payload) > TOTAL_NETWORK_LIMIT:
                raise BridgeFailure(BridgeReason.LIMIT)
            self._data += len(frame.payload)

    async def _forward(self, destination: FrameIO, frame: Frame) -> None:
        committed = False

        def commit() -> None:
            nonlocal committed
            if committed or self._stopping:
                raise BridgeFailure(BridgeReason.PROTOCOL)
            committed = True

        try:
            await destination.send(frame, commit)
            if not committed:
                raise BridgeFailure(BridgeReason.PROTOCOL)
        except BaseException:
            # Even if final RESULT/EOF wins the end race, cancellation of a
            # forwarding operation cannot silently imply a complete frame.
            self._forward_failed = True
            raise

    async def _from_browser(self) -> None:
        while not self._stopping:
            frame = await self._browser.receive()
            if self._stopping:
                return
            if frame is None:
                self._results.end_of_stream()
                self._finish(BridgeReason.BROWSER_FINAL_EOF)
                return
            self._shape(frame)
            # Observe BEFORE forwarding: RESULT is private to this supervisor.
            consumed = self._results.observe(frame)
            self._browser_frames += 1
            self._browser_wire += HEADER_LEN + len(frame.payload)
            self._count_data(frame)
            if not consumed:
                await self._forward(self._gateway, frame)

    async def _from_gateway(self) -> None:
        while not self._stopping:
            frame = await self._gateway.receive()
            if self._stopping:
                return
            if frame is None:
                self._finish(BridgeReason.GATEWAY_EOF)
                return
            self._shape(frame)
            if frame.frame_type not in _GATEWAY_TYPES:
                raise BridgeFailure(BridgeReason.PROTOCOL)
            if self._gateway_frames >= MAX_FRAMES:
                raise BridgeFailure(BridgeReason.LIMIT)
            self._gateway_frames += 1
            self._gateway_wire += HEADER_LEN + len(frame.payload)
            self._count_data(frame)
            await self._forward(self._browser, frame)

    async def run(self) -> BridgeOutcome:
        if self._used:
            raise RuntimeError("bridge cannot be reused")
        self._used = True
        remaining = self._expires_at - asyncio.get_running_loop().time()
        self._end = asyncio.get_running_loop().create_future()
        if remaining > 45:
            reason = BridgeReason.PROTOCOL
        elif remaining <= 0:
            reason = BridgeReason.DEADLINE
        else:
            self._spawn(self._from_browser)
            self._spawn(self._from_gateway)
            try:
                async with asyncio.timeout_at(self._expires_at):
                    reason = await asyncio.shield(self._end)
            except TimeoutError:
                reason = BridgeReason.DEADLINE
            except asyncio.CancelledError:
                reason = BridgeReason.CANCELLED
        self._stopping = True
        cleanup_failed = False
        for closer in (self._browser.close, self._gateway.close):
            try:
                closer()
            except Exception:
                cleanup_failed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=self._cleanup_grace)
            except asyncio.CancelledError:
                cleanup_failed = True
        pending = len(self.pending_tasks)
        cleanup_failed = cleanup_failed or pending > 0
        if reason is BridgeReason.BROWSER_FINAL_EOF:
            if self._background_failure is not None:
                reason = self._background_failure
            elif self._forward_failed:
                reason = BridgeReason.IO
        candidate = None
        if reason is BridgeReason.BROWSER_FINAL_EOF and not cleanup_failed:
            try:
                candidate = self._results.candidate()
            except ResultError:
                reason = BridgeReason.PROTOCOL
        if candidate is None:
            self._results.abort()
        return BridgeOutcome(
            reason,
            candidate,
            self._browser_frames,
            self._gateway_frames,
            self._browser_wire,
            self._gateway_wire,
            self._data,
            cleanup_failed,
            pending,
        )
