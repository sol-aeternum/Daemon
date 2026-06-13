from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from orchestrator.config import Settings
from orchestrator.daemon import SSE_KEEPALIVE_FRAME, sse, stream_with_keepalives


@pytest.mark.asyncio
async def test_idle_sse_stream_emits_keepalive_before_next_frame() -> None:
    async def idle_stream() -> AsyncIterator[str]:
        await asyncio.sleep(0.035)
        yield sse("done", {"ok": True})

    frames: list[str] = []
    async for frame in stream_with_keepalives(idle_stream(), ping_interval_s=0.01):
        frames.append(frame)

    assert frames.count(SSE_KEEPALIVE_FRAME) >= 2
    assert frames[-1].startswith("event: done\n")


@pytest.mark.asyncio
async def test_sse_keepalive_disabled_when_interval_is_zero() -> None:
    async def stream() -> AsyncIterator[str]:
        yield sse("done", {"ok": True})

    frames = [frame async for frame in stream_with_keepalives(stream(), ping_interval_s=0)]

    assert frames == [sse("done", {"ok": True})]


def test_daemon_sse_keepalive_interval_overrides_legacy_stream_ping() -> None:
    settings = Settings(
        daemon_environment="development",
        stream_ping_interval_s=99,
        daemon_sse_keepalive_interval_s=7,
    )

    assert settings.sse_keepalive_interval_s == 7


def test_legacy_stream_ping_interval_remains_default_keepalive() -> None:
    settings = Settings(daemon_environment="development", stream_ping_interval_s=11)

    assert settings.sse_keepalive_interval_s == 11
