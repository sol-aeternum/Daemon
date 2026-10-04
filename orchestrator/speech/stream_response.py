"""Response-owned speech delivery, independent of native worker lifetime."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
import math
import time
from typing import Any

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from orchestrator.speech.contracts import SpeechError
from orchestrator.speech.stream_protocol import (
    AUDIO,
    COMPLETE,
    CONTENT_TYPE,
    ERROR,
    HEARTBEAT,
    HEARTBEAT_SECONDS,
    MAGIC,
    MAX_HEARTBEATS,
    METADATA,
    SEND_DRAIN_SECONDS,
    StreamParser,
    encode_frame,
    safe_error_code,
    validate_metadata,
)

_OWNED_TASKS: set[asyncio.Task[Any]] = set()


def _own(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _OWNED_TASKS.add(task)

    def done(future: asyncio.Task[Any]) -> None:
        _OWNED_TASKS.discard(future)
        if not future.cancelled():
            future.exception()

    task.add_done_callback(done)
    return task


class OwnedSpeechResponse(Response):
    """One receive consumer and one frame writer; cleanup also covers no invocation.

    ``cleanup`` must signal native/writer tails, not wait for uninterruptible work.
    ``source`` yields whole encoded frames without magic or metadata. The response
    waits for source EOF before forwarding its success terminal.
    """

    def __init__(
        self,
        metadata: dict,
        source: AsyncIterator[bytes],
        *,
        cleanup: Callable[[], Awaitable[None]],
        deadline: float,
        checkpoint: Callable[[], Awaitable[None]] | None = None,
        failure: Awaitable[None] | None = None,
    ):
        super().__init__(
            content=b"",
            status_code=200,
            media_type=CONTENT_TYPE,
            headers={
                "Cache-Control": "private, no-store, no-transform",
                "X-Accel-Buffering": "no",
                "Content-Encoding": "identity",
            },
        )
        self.raw_headers = [
            (key, value) for key, value in self.raw_headers if key != b"content-length"
        ]
        self.metadata = validate_metadata(dict(metadata))
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError("Speech response requires a finite absolute deadline")
        self.source = source
        self.deadline = deadline
        self.checkpoint = checkpoint
        self.failure = failure
        self._cleanup = cleanup
        self._cleanup_task: asyncio.Task[None] | None = None
        self._source_close_task: asyncio.Task[None] | None = None
        self._called = False
        self._expired = False
        self._metadata_sent = False
        self._terminal_sent = False
        self._watchdog = _own(asyncio.create_task(self._expire()))

    def _begin_cleanup(self) -> asyncio.Task[None]:
        if self._cleanup_task is None:

            async def run_cleanup() -> None:
                await self._cleanup()

            self._cleanup_task = _own(asyncio.create_task(run_cleanup()))
        return self._cleanup_task

    async def cleanup(self) -> None:
        """Endpoint may call this on a failed prehandoff setup; it is idempotent."""
        await asyncio.shield(self._begin_cleanup())

    def _retire_source(self, pending: asyncio.Task[bytes] | None = None) -> None:
        if self._source_close_task is not None:
            return
        if pending is not None:
            pending.cancel()

        async def close_source() -> None:
            if pending is not None:
                # A source may already own an uninterruptible native/cache tail.
                # Track its cancelled reader to real exit, never wait in HTTP cleanup.
                await asyncio.gather(pending, return_exceptions=True)
            closer = getattr(self.source, "aclose", None)
            if closer is not None:
                await closer()

        self._source_close_task = _own(asyncio.create_task(close_source()))

    async def _expire(self) -> None:
        await asyncio.sleep(max(0, self.deadline - time.monotonic()))
        self._expired = True
        await self.cleanup()
        if not self._called:
            self._retire_source()
        # A failure coroutine handed over with an uninvoked response still belongs
        # to us. Close a fresh coroutine rather than leaving it unawaited.
        if not self._called and hasattr(self.failure, "close"):
            self.failure.close()  # type: ignore[union-attr]
        elif not self._called and isinstance(self.failure, asyncio.Future):
            self.failure.cancel()

    async def _disconnect(self, receive: Receive) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise SpeechError("speech_cancelled", 499)

    async def _send(self, send: Send, message: dict) -> None:
        if self.checkpoint is not None:
            await self.checkpoint()
        try:
            async with asyncio.timeout(SEND_DRAIN_SECONDS):
                await send(message)
        except TimeoutError as exc:
            raise SpeechError("speech_backpressure_timeout") from exc

    async def _body(self, send: Send) -> None:
        await self._send(
            send,
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            },
        )
        prefix = MAGIC + encode_frame(METADATA, self.metadata)
        await self._send(send, {"type": "http.response.body", "body": prefix, "more_body": True})
        self._metadata_sent = True
        parser = StreamParser(self.metadata)
        for _ in parser.feed(prefix):
            pass
        last_write = time.monotonic()
        heartbeats = 0
        pending: asyncio.Task[bytes] | None = None

        async def next_frame() -> bytes:
            return await anext(self.source)

        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(next_frame())
                done, _ = await asyncio.wait(
                    {pending}, timeout=max(0, last_write + HEARTBEAT_SECONDS - time.monotonic())
                )
                if not done:
                    if heartbeats >= MAX_HEARTBEATS:
                        raise SpeechError("speech_timeout")
                    heartbeat = encode_frame(HEARTBEAT, b"")
                    for _ in parser.feed(heartbeat):
                        pass
                    await self._send(
                        send, {"type": "http.response.body", "body": heartbeat, "more_body": True}
                    )
                    heartbeats += 1
                    last_write = time.monotonic()
                    continue
                try:
                    frame = pending.result()
                except StopAsyncIteration as exc:
                    raise SpeechError("speech_protocol_error") from exc
                pending = None
                if (
                    len(frame) < 5
                    or frame[0] not in (AUDIO, COMPLETE, ERROR)
                    or (int.from_bytes(frame[1:5], "big") != len(frame) - 5)
                ):
                    raise SpeechError("speech_protocol_error")
                # The source contract is exactly ONE whole frame per yield.
                events = list(parser.feed(frame))
                if len(events) != 1 or parser.pending_bytes:
                    raise SpeechError("speech_protocol_error")
                if frame[0] == COMPLETE:
                    # No heartbeat beyond terminal; no premature terminal on an
                    # upstream reset, trailing bytes or cache/publication failure.
                    pending = asyncio.create_task(next_frame())
                    try:
                        await asyncio.shield(pending)
                    except StopAsyncIteration:
                        pending = None
                        parser.finish()
                    else:
                        raise SpeechError("speech_protocol_error")
                    await self._send(
                        send, {"type": "http.response.body", "body": frame, "more_body": True}
                    )
                    self._terminal_sent = True
                    await self._send(
                        send, {"type": "http.response.body", "body": b"", "more_body": False}
                    )
                    return
                await self._send(
                    send, {"type": "http.response.body", "body": frame, "more_body": True}
                )
                last_write = time.monotonic()
        finally:
            self._retire_source(pending)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._called:
            raise RuntimeError("Speech response is single-use")
        self._called = True
        tasks: set[asyncio.Future[Any]] = set()
        body: asyncio.Task[None] | None = None
        try:
            if self._expired or time.monotonic() >= self.deadline:
                raise SpeechError("speech_timeout", 504)
            body = asyncio.create_task(self._body(send))
            tasks = {body, asyncio.create_task(self._disconnect(receive))}
            if self.failure is not None:
                tasks.add(asyncio.ensure_future(self.failure))
            done, _ = await asyncio.wait(
                tasks,
                timeout=max(0, self.deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise SpeechError("speech_timeout", 504)
            # A concurrent authority/receive failure wins over successful body.
            for task in done - {body}:
                task.result()
                raise SpeechError("speech_authorization_unavailable")
            body.result()
        except Exception as exc:
            self._begin_cleanup()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._metadata_sent and not self._terminal_sent:
                with contextlib.suppress(Exception):
                    # Safe control-only failure does not wait on a denied payload
                    # checkpoint. Never allow an unwritable error to prolong life.
                    async with asyncio.timeout(
                        min(SEND_DRAIN_SECONDS, max(0.01, self.deadline - time.monotonic()))
                    ):
                        await send(
                            {
                                "type": "http.response.body",
                                "body": encode_frame(ERROR, {"code": safe_error_code(exc)}),
                                "more_body": False,
                            }
                        )
        finally:
            self._begin_cleanup()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._retire_source()
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
            await self.cleanup()
