"""Private runtime API; one model/process and zero queued native synthesis jobs."""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
import logging
import threading
import time
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from orchestrator.models import TtsRequest
from orchestrator.request_body_limit import RequestBodyLimitMiddleware
from orchestrator.speech.contracts import SpeechCapabilities, SpeechError, SpeechRequest
from orchestrator.speech.stream_protocol import (
    PROGRESSIVE_LIMITS,
    RENDERING,
    SAMPLE_RATE,
    VERSION,
    metadata,
)
from orchestrator.speech.stream_response import OwnedSpeechResponse
from tts.runtime import KokoroRuntime, SynthesizeFunction
from tts.streaming import ByteQueue

logger = logging.getLogger(__name__)


class DisconnectMonitor(Protocol):
    async def is_disconnected(self) -> bool: ...


class SynthesisSlot:
    def __init__(self, synthesize: SynthesizeFunction, timeout: float = 120):
        self.synthesize, self.timeout = synthesize, timeout
        self.active = False
        self._lease: object | None = None
        self._workers: dict[object, tuple[threading.Thread, asyncio.Future[Any]]] = {}

    def _start(self, work: Callable[[], Any], queue: ByteQueue | None = None):
        if self.active:
            raise SpeechError("speech_busy", 429)
        loop = asyncio.get_running_loop()
        lease = object()
        future = loop.create_future()
        self.active, self._lease = True, lease

        def completed(result: Any, error: BaseException | None) -> None:
            thread, _ = self._workers[lease]
            if thread.is_alive():
                # Exit, not just notification from the thread's finalizer, owns
                # release. No waiter/Task cancellation can make this happen early.
                loop.call_later(0.001, completed, result, error)
                return
            self._workers.pop(lease)
            if self._lease is lease:
                self.active, self._lease = False, None
            if queue is not None:
                queue.finish(error)
            if not future.done():
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(result)

        def worker() -> None:
            result, error = None, None
            try:
                result = work()
            except BaseException as exc:
                error = exc
            finally:
                # The strong handle stays app-owned until actual thread exit.
                try:
                    loop.call_soon_threadsafe(completed, result, error)
                except RuntimeError:
                    pass  # Process/eventloop shutdown cannot admit a successor.

        thread = threading.Thread(target=worker, name="speech-native", daemon=True)
        self._workers[lease] = (thread, future)

        def consume_failure(done: asyncio.Future[Any]) -> None:
            if not done.cancelled():
                done.exception()

        future.add_done_callback(consume_failure)
        try:
            thread.start()
        except BaseException:
            self._workers.pop(lease)
            self.active, self._lease = False, None
            raise
        return future

    def start_stream(self, speech: SpeechRequest, stream: Callable[..., None]) -> ByteQueue:
        cancel = threading.Event()
        deadline = time.monotonic() + self.timeout
        queue = ByteQueue(cancel, deadline)
        self._start(lambda: stream(speech, cancel, queue, deadline), queue)
        return queue

    async def run(self, speech: SpeechRequest, request: DisconnectMonitor):
        cancel = threading.Event()
        task = self._start(lambda: self.synthesize(speech, cancel))
        started = time.monotonic()
        try:
            while not task.done():
                if time.monotonic() - started >= self.timeout:
                    raise SpeechError("speech_timeout", 504)
                if await request.is_disconnected():
                    raise SpeechError("speech_cancelled", 499)
                await asyncio.wait({task}, timeout=0.05)
            if time.monotonic() - started >= self.timeout:
                raise SpeechError("speech_timeout", 504)
            return task.result()
        finally:
            cancel.set()


def create_app(runtime: KokoroRuntime | None = None) -> FastAPI:
    runtime = runtime or KokoroRuntime()
    slot = SynthesisSlot(runtime.synthesize)
    ready = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal ready
        started = time.monotonic()
        await asyncio.to_thread(runtime.load)
        ready = True
        logger.info(
            "speech_ready model=%s load_seconds=%.3f", runtime.model, time.monotonic() - started
        )
        yield
        ready = False
        # Never instantiate additional models to replace busy/timed-out jobs.

    app = FastAPI(lifespan=lifespan)
    app.state.slot = slot
    app.add_middleware(RequestBodyLimitMiddleware, global_limit=32768, route_limits={})

    @app.get("/live")
    async def live():
        return {"alive": True}

    @app.get("/ready")
    async def readiness():
        capabilities = asdict(SpeechCapabilities())
        capabilities["progressive"] = (
            [
                {
                    "version": VERSION,
                    "format": "mp3",
                    "mime": "audio/mpeg",
                    "sample_rate": SAMPLE_RATE,
                    "rendering": RENDERING,
                    "limits": PROGRESSIVE_LIMITS,
                }
            ]
            if ready and runtime.progressive_ready
            else []
        )
        return JSONResponse(
            {
                "ready": ready,
                "provider": runtime.name,
                "model": runtime.model,
                "capabilities": capabilities,
            },
            status_code=200 if ready else 503,
        )

    @app.post("/synthesize")
    async def synthesize(payload: TtsRequest, request: Request):
        try:
            if not ready:
                raise SpeechError("speech_not_ready")
            speech = SpeechRequest(
                payload.text.strip(),
                payload.voice or "daemon-default",
                payload.speed if payload.speed is not None else 1,
                payload.format or "mp3",
            )
            audio = await slot.run(speech, request)
            return Response(
                audio.content,
                media_type={"mp3": "audio/mpeg", "opus": "audio/ogg", "wav": "audio/wav"}[
                    speech.format
                ],
                headers={
                    "X-Speech-Provider": runtime.name,
                    "X-Speech-Model": runtime.model,
                    "X-Audio-Duration": str(audio.duration),
                    "X-Audio-Sample-Rate": str(audio.sample_rate),
                    "X-Synthesis-Seconds": str(audio.synthesis_seconds),
                },
            )
        except SpeechError as exc:
            return JSONResponse({"detail": {"code": exc.code}}, status_code=exc.status)
        except Exception:
            logger.error("speech_runtime_failure")
            return JSONResponse({"detail": {"code": "speech_failed"}}, status_code=503)

    @app.post("/synthesize/stream/v1")
    async def synthesize_stream(payload: TtsRequest):
        queue: ByteQueue | None = None
        try:
            if not ready:
                raise SpeechError("speech_not_ready")
            speech = SpeechRequest(
                payload.text.strip(),
                payload.voice or "daemon-default",
                payload.speed if payload.speed is not None else 1,
                payload.format or "mp3",
            )
            if speech.format != "mp3" or not runtime.progressive_ready:
                raise SpeechError("speech_stream_unsupported", 422)
            # Construct identity before native acquisition, and acquire before200.
            identity = metadata(runtime.name, runtime.model, speech)
            queue = slot.start_stream(speech, runtime.stream)

            async def cleanup() -> None:
                queue.stop()

            return OwnedSpeechResponse(
                identity,
                queue.frames(),
                cleanup=cleanup,
                deadline=queue.deadline,
            )
        except BaseException as exc:
            if queue is not None:
                queue.stop()
            if isinstance(exc, SpeechError):
                return JSONResponse({"detail": {"code": exc.code}}, status_code=exc.status)
            if not isinstance(exc, Exception):
                raise
            logger.error("speech_runtime_failure")
            return JSONResponse({"detail": {"code": "speech_failed"}}, status_code=503)

    return app


app = create_app()
