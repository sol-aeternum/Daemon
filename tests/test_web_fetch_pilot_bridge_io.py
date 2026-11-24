"""Owned anonymous-pipe fixtures: review before first execution.

No subprocesses, sockets, DNS, browsers, Docker or public egress are used. These
are same-process kernel-pipe checks, not cross-process/attach qualification.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

import pytest

from scripts.web_fetch_pilot_bridge import BridgeReason, IPCBridge
from scripts.web_fetch_pilot_core import MAX_PAYLOAD, Frame, FrameType
from scripts.web_fetch_pilot_io import AsyncFD, FDFrameIO
from scripts.web_fetch_pilot_results import ResultCollector

URL = "https://example.com/synthetic"


@contextmanager
def pipes() -> Iterator[tuple[FDFrameIO, FDFrameIO, AsyncFD]]:
    """Exception-safe raw-fd ownership, following the qualified I/O fixtures."""
    unclaimed: set[int] = set()
    with ExitStack() as stack:

        def release(fd: int) -> None:
            if fd in unclaimed:
                unclaimed.remove(fd)
                os.close(fd)

        def pair() -> tuple[int, int]:
            fds = os.pipe()
            for fd in fds:
                unclaimed.add(fd)
                stack.callback(release, fd)
            # Reduce only this owned anonymous pipe, not a host sysctl. A maximum
            # DATA frame then requires partial writes, even on large-pipe hosts.
            capacity = fcntl.fcntl(fds[1], fcntl.F_SETPIPE_SZ, 4096)
            assert capacity < MAX_PAYLOAD
            return fds

        def own(fd: int) -> AsyncFD:
            adapter = AsyncFD(fd)
            unclaimed.remove(fd)
            stack.callback(adapter.close)
            return adapter

        a_read, a_write = pair()
        b_read, b_write = pair()
        raw_writer = own(a_write)
        child = FDFrameIO(own(b_read), raw_writer)
        stack.callback(child.close)
        supervisor = FDFrameIO(own(a_read), own(b_write))
        stack.callback(supervisor.close)
        yield child, supervisor, raw_writer


def collector() -> ResultCollector:
    return ResultCollector(URL, ("example.com",), "fixture-v1")


def final() -> Frame:
    record = {
        "status": "success",
        "original_url": URL,
        "final_url": URL,
        "title": "Synthetic",
        "extraction_version": "fixture-v1",
    }
    return Frame(FrameType.RESULT, 0, b"\x02" + json.dumps(record).encode())


async def transfer(sender: FDFrameIO, receiver: FDFrameIO, frame: Frame) -> None:
    receiving = asyncio.create_task(receiver.receive())
    commits: list[bool] = []
    try:
        await asyncio.wait_for(sender.send(frame, lambda: commits.append(True)), 1)
        assert await asyncio.wait_for(receiving, 1) == frame
        assert commits == [True]
    finally:
        if not receiving.done():
            receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)


async def stop(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)


@pytest.mark.asyncio
async def test_real_bridge_duplex_result_privacy_and_owned_cleanup() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    with pipes() as (browser, browser_supervisor, _), pipes() as (gateway, gateway_supervisor, _):
        results = collector()
        bridge = IPCBridge(
            browser_supervisor,
            gateway_supervisor,
            results,
            expires_at=asyncio.get_running_loop().time() + 2,
        )
        task = asyncio.create_task(bridge.run())
        try:
            await transfer(browser, gateway, Frame(FrameType.OPEN, 1, b"example.com:443"))
            await transfer(gateway, browser, Frame(FrameType.OPEN_OK, 1, b""))
            await transfer(browser, gateway, Frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD))
            await transfer(
                gateway, browser, Frame(FrameType.WINDOW, 1, MAX_PAYLOAD.to_bytes(4, "big"))
            )
            await transfer(gateway, browser, Frame(FrameType.DATA, 1, b"y" * MAX_PAYLOAD))
            await transfer(
                browser, gateway, Frame(FrameType.WINDOW, 1, MAX_PAYLOAD.to_bytes(4, "big"))
            )
            await transfer(browser, gateway, Frame(FrameType.HALF_CLOSE, 1, b""))
            await transfer(gateway, browser, Frame(FrameType.HALF_CLOSE, 1, b""))
            await transfer(gateway, browser, Frame(FrameType.CLOSE, 1, b""))
            await browser.send(Frame(FrameType.RESULT, 0, b"\x01synthetic source"), lambda: None)
            await browser.send(final(), lambda: None)
            browser.close()  # Actual write-fd closure produces framed EOF.
            outcome = await asyncio.wait_for(task, 1)
            assert outcome.reason is BridgeReason.BROWSER_FINAL_EOF
            assert (
                outcome.candidate is not None and outcome.candidate.content == b"synthetic source"
            )
            assert outcome.browser_frames == 6 and outcome.gateway_frames == 5
            assert outcome.data_bytes == 2 * MAX_PAYLOAD
            assert not outcome.cleanup_failed and not bridge.pending_tasks
            # Supervisor closed its gateway output without forwarding RESULT.
            assert await asyncio.wait_for(gateway.receive(), 1) is None
        finally:
            await stop(task)
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_bridge_truncated_frame_eof_discards_content() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    with pipes() as (browser, browser_supervisor, raw), pipes() as (_, gateway_supervisor, _):
        results = collector()
        bridge = IPCBridge(
            browser_supervisor,
            gateway_supervisor,
            results,
            expires_at=asyncio.get_running_loop().time() + 1,
        )
        task = asyncio.create_task(bridge.run())
        try:
            await browser.send(Frame(FrameType.RESULT, 0, b"\x01synthetic source"), lambda: None)
            assert await raw.write(b"\x08\x00") == 2  # Incomplete next frame header.
            browser.close()
            outcome = await asyncio.wait_for(task, 1)
            assert outcome.reason is not BridgeReason.BROWSER_FINAL_EOF
            assert outcome.candidate is None and not results.final_received
            assert not outcome.cleanup_failed and not bridge.pending_tasks
        finally:
            await stop(task)
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_bridge_unread_destination_deadline_closes_owned_fds() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    with pipes() as (browser, browser_supervisor, _), pipes() as (_, gateway_supervisor, _):
        bridge = IPCBridge(
            browser_supervisor,
            gateway_supervisor,
            collector(),
            expires_at=asyncio.get_running_loop().time() + 0.2,
        )
        task = asyncio.create_task(bridge.run())
        sending = asyncio.create_task(
            browser.send(Frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD), lambda: None)
        )
        try:
            outcome = await asyncio.wait_for(task, 1)
            assert outcome.reason is BridgeReason.DEADLINE and outcome.candidate is None
            assert outcome.data_bytes == MAX_PAYLOAD and bridge._forward_failed
            assert not outcome.cleanup_failed and not bridge.pending_tasks
        finally:
            await stop(sending)
            await stop(task)
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks
