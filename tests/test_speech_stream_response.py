"""Actual ASGI send/receive lifecycle, not mobile playback qualification."""

import asyncio
import time

import pytest
from starlette.middleware.gzip import GZipMiddleware

from orchestrator.speech.contracts import SpeechError, SpeechRequest
from orchestrator.speech.stream_protocol import (
    COMPLETE,
    CONTENT_TYPE,
    ERROR,
    HEARTBEAT,
    MAGIC,
    StreamParser,
    encode_audio,
    encode_frame,
    metadata,
)
from orchestrator.speech.stream_response import OwnedSpeechResponse
import orchestrator.speech.stream_response as response_module


def identity():
    return metadata("kokoro", "kokoro-82m-v1.0", SpeechRequest("hello"))


def complete():
    return encode_frame(
        COMPLETE,
        {
            "frames": 1,
            "bytes": 3,
            "source_seconds": 1,
            "encoded_seconds": 1.056,
            "synthesis_seconds": 0.1,
            "audio_path": None,
            "cache_available": False,
        },
    )


async def source():
    yield encode_audio(0, b"mp3")
    yield complete()


class Harness:
    def __init__(self):
        self.sent = []
        self.cleaned = 0
        self.incoming = asyncio.Queue()
        self.receive_calls = 0

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        self.receive_calls += 1
        return await self.incoming.get()

    async def cleanup(self):
        self.cleaned += 1

    def response(self, iterator=None, **kwargs):
        return OwnedSpeechResponse(
            identity(),
            iterator or source(),
            cleanup=self.cleanup,
            deadline=time.monotonic() + 1,
            **kwargs,
        )

    @property
    def body(self):
        return b"".join(item.get("body", b"") for item in self.sent)


@pytest.mark.asyncio
async def test_success_headers_metadata_single_write_and_clean_eof():
    harness = Harness()
    response = harness.response()
    await response({"type": "http"}, harness.receive, harness.send)
    headers = dict(harness.sent[0]["headers"])
    assert headers[b"content-type"].decode() == CONTENT_TYPE
    assert headers[b"cache-control"] == b"private, no-store, no-transform"
    assert headers[b"x-accel-buffering"] == b"no"
    assert b"content-length" not in headers and headers[b"content-encoding"] == b"identity"
    assert harness.sent[1]["body"].startswith(MAGIC)
    assert harness.sent[-1]["more_body"] is False
    parser = StreamParser()
    list(parser.feed(harness.body))
    parser.finish()
    assert harness.cleaned == 1 and harness.receive_calls == 1


@pytest.mark.asyncio
async def test_installed_framework_gzip_cannot_compress_framing():
    harness = Harness()
    middleware = GZipMiddleware(harness.response(), minimum_size=1)
    await middleware(
        {"type": "http", "headers": [(b"accept-encoding", b"gzip")]}, harness.receive, harness.send
    )
    assert dict(harness.sent[0]["headers"])[b"content-encoding"] == b"identity"
    parser = StreamParser()
    list(parser.feed(harness.body))
    parser.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["headers", "metadata", "audio"])
async def test_send_failure_always_cleans(where):
    harness = Harness()
    count = 0

    async def send(message):
        nonlocal count
        count += 1
        if count == {"headers": 1, "metadata": 2, "audio": 3}[where]:
            raise OSError("private diagnostic")
        await harness.send(message)

    await harness.response()({"type": "http"}, harness.receive, send)
    assert harness.cleaned == 1
    assert b"private diagnostic" not in harness.body


@pytest.mark.asyncio
async def test_receive_failure_interrupts_blocked_source():
    harness = Harness()
    started = asyncio.Event()

    async def blocked():
        started.set()
        await asyncio.Event().wait()
        yield b"never"

    async def receive():
        await started.wait()
        raise OSError("private receiver failure")

    await harness.response(blocked())({"type": "http"}, receive, harness.send)
    assert harness.cleaned == 1
    with pytest.raises(SpeechError, match="speech_failed"):
        list(StreamParser().feed(harness.body))


@pytest.mark.asyncio
async def test_disconnect_and_http_task_cancel_signal_cleanup():
    for external_cancel in (False, True):
        harness = Harness()
        source_entered = asyncio.Event()

        async def blocked():
            source_entered.set()
            await asyncio.Event().wait()
            yield b"never"

        task = asyncio.create_task(
            harness.response(blocked())({"type": "http"}, harness.receive, harness.send)
        )
        await source_entered.wait()
        if external_cancel:
            task.cancel()
        else:
            harness.incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.gather(task, return_exceptions=True)
        assert harness.cleaned == 1


@pytest.mark.asyncio
async def test_uninvoked_watchdog_and_late_invocation_cleanup_idempotent():
    harness = Harness()
    response = OwnedSpeechResponse(
        identity(), source(), cleanup=harness.cleanup, deadline=time.monotonic() + 0.02
    )
    await asyncio.sleep(0.04)
    assert harness.cleaned == 1
    await response.cleanup()
    await response({"type": "http"}, harness.receive, harness.send)
    assert not harness.sent and harness.cleaned == 1


@pytest.mark.asyncio
async def test_deadline_interrupts_blocked_header_send():
    harness = Harness()
    response = OwnedSpeechResponse(
        identity(), source(), cleanup=harness.cleanup, deadline=time.monotonic() + 0.02
    )

    async def blocked_send(message):
        await asyncio.Event().wait()

    started = time.monotonic()
    await response({"type": "http"}, harness.receive, blocked_send)
    assert time.monotonic() - started < 0.2
    assert harness.cleaned == 1


@pytest.mark.asyncio
async def test_backpressure_bound_and_independent_failure(monkeypatch):
    monkeypatch.setattr(response_module, "SEND_DRAIN_SECONDS", 0.02)
    for authorization_failure in (False, True):
        harness = Harness()
        blocked = asyncio.Event()

        async def send(message):
            if message.get("body") == encode_audio(0, b"mp3"):
                blocked.set()
                await asyncio.Event().wait()
            await harness.send(message)

        async def failure():
            await blocked.wait()
            raise SpeechError("speech_authorization_lost")

        await harness.response(failure=failure() if authorization_failure else None)(
            {"type": "http"}, harness.receive, send
        )
        expected = (
            "speech_authorization_lost" if authorization_failure else "speech_backpressure_timeout"
        )
        with pytest.raises(SpeechError, match=expected):
            list(StreamParser().feed(harness.body))
        assert harness.cleaned == 1


@pytest.mark.asyncio
async def test_checkpoint_failure_sanitized_without_payload():
    harness = Harness()
    count = 0

    async def checkpoint():
        nonlocal count
        count += 1
        if count == 3:
            raise SpeechError("speech_authorization_lost")

    await harness.response(checkpoint=checkpoint)({"type": "http"}, harness.receive, harness.send)
    assert encode_audio(0, b"mp3") not in harness.body
    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        list(StreamParser().feed(harness.body))


@pytest.mark.asyncio
async def test_idle_heartbeats_serial_and_none_after_terminal(monkeypatch):
    monkeypatch.setattr(response_module, "HEARTBEAT_SECONDS", 0.01)
    harness = Harness()

    async def delayed():
        await asyncio.sleep(0.035)
        yield encode_audio(0, b"mp3")
        yield complete()

    await harness.response(delayed())({"type": "http"}, harness.receive, harness.send)
    parser = StreamParser()
    events = list(parser.feed(harness.body))
    parser.finish()
    assert 1 <= sum(event.kind == HEARTBEAT for event in events) <= 4
    assert events[-1].kind == COMPLETE


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", ["extra", "error", "blocked"])
async def test_complete_is_not_forwarded_before_clean_source_eof(tail):
    harness = Harness()

    async def bad_source():
        yield encode_audio(0, b"mp3")
        yield complete()
        if tail == "extra":
            yield encode_frame(ERROR, {"code": "speech_failed"})
        elif tail == "error":
            raise OSError("upstream reset")
        else:
            await asyncio.Event().wait()

    response = OwnedSpeechResponse(
        identity(), bad_source(), cleanup=harness.cleanup, deadline=time.monotonic() + 0.03
    )
    await response({"type": "http"}, harness.receive, harness.send)
    assert complete() not in harness.body
    assert harness.cleaned == 1
    with pytest.raises(SpeechError):
        list(StreamParser().feed(harness.body))


@pytest.mark.asyncio
async def test_uninterruptible_source_tail_does_not_extend_http_cleanup():
    harness = Harness()
    release, entered, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def native_tail():
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            stopped.set()
        yield encode_audio(0, b"mp3")

    response = OwnedSpeechResponse(
        identity(), native_tail(), cleanup=harness.cleanup, deadline=time.monotonic() + 0.03
    )
    started = time.monotonic()
    try:
        await response({"type": "http"}, harness.receive, harness.send)
        assert entered.is_set() and not stopped.is_set()
        assert harness.cleaned == 1 and time.monotonic() - started < 0.2
    finally:
        release.set()
        await asyncio.wait_for(stopped.wait(), 1)
        assert response._source_close_task is not None
        await response._source_close_task
