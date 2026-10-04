"""Real root-flock contention must not starve the ASGI event loop."""

import asyncio
import threading
import gc
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import uuid

from fastapi import Request
import pytest

from orchestrator import main
from orchestrator.auth import AuthenticatedDevice
from orchestrator.speech import cache
from orchestrator.speech.contracts import SpeechRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["get", "post"])
async def test_buffered_routes_leave_loop_live_while_root_lock_is_contended(
    tmp_path, monkeypatch, route
):
    owner = uuid.uuid4()
    auth = AuthenticatedDevice(owner, uuid.uuid4(), uuid.uuid4())
    provider = SimpleNamespace(name="kokoro", model="fictional")
    filename = cache.audio_filename(provider.name, provider.model, SpeechRequest("Fictional."))
    root = tmp_path / "self-hosted"
    cache.store_audio(root, owner, filename, b"complete")
    monkeypatch.setattr(main, "TTS_CACHE_DIR", tmp_path)
    monkeypatch.setattr(main, "get_speech_provider", lambda _: provider)
    monkeypatch.setattr(
        main,
        "get_rate_limiter",
        lambda _: SimpleNamespace(
            is_redis_available=True, check=AsyncMock(return_value=SimpleNamespace(allowed=True))
        ),
    )
    acquired, release, released = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with cache.cache_lock(root):
            acquired.set()
            # Independent escape hatch: a broken synchronous route cannot hang
            # the test runner or require the blocked event loop for cleanup.
            release.wait(0.5)
        released.set()

    thread = threading.Thread(target=holder)
    thread.start()
    await asyncio.to_thread(acquired.wait)
    observed = []

    async def heartbeat():
        await asyncio.sleep(0.02)
        observed.append(not released.is_set())
        release.set()

    pulse = asyncio.create_task(heartbeat())
    try:
        if route == "get":
            response = await main.serve_generated_audio(filename, auth)
            assert response.status_code == 200
        else:
            result = await main.text_to_speech(
                main.TtsRequest(text="Fictional."),
                Request({"type": "http"}),
                cast(Any, SimpleNamespace()),
                auth,
            )
            assert result["cached"] is True
        await pulse
        assert observed == [True], "Root flock blocked the event loop and authority monitor"
    finally:
        release.set()
        await pulse
        await asyncio.to_thread(thread.join)


@pytest.mark.asyncio
async def test_cancelled_cache_tail_remains_owned_and_consumes_late_failure():
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))

    def blocked():
        entered.set()
        release.wait(2)
        raise OSError("fictional late disk failure")

    waiter = asyncio.create_task(cache.run_cache_io(blocked))
    try:
        await asyncio.to_thread(entered.wait)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert len(cache._io_tails) == 1
        release.set()
        async with asyncio.timeout(1):
            while cache._io_tails:
                await asyncio.sleep(0.001)
        gc.collect()
        await asyncio.sleep(0)
        assert not unhandled
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        loop.set_exception_handler(previous)
