"""Private runtime API; one model/process and zero queued native synthesis jobs."""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import asdict
import logging
import threading
import time
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from orchestrator.models import TtsRequest
from orchestrator.request_body_limit import RequestBodyLimitMiddleware
from orchestrator.speech.contracts import SpeechCapabilities, SpeechError, SpeechRequest
from tts.runtime import KokoroRuntime, SynthesizeFunction

logger = logging.getLogger(__name__)


class DisconnectMonitor(Protocol):
    async def is_disconnected(self) -> bool: ...


class SynthesisSlot:
    def __init__(self, synthesize: SynthesizeFunction, timeout: float = 120):
        self.synthesize, self.timeout = synthesize, timeout
        self.active = False

    async def run(self, speech: SpeechRequest, request: DisconnectMonitor):
        if self.active:
            raise SpeechError("speech_busy", 429)
        self.active = True
        cancel = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self.synthesize, speech, cancel))

        def completed(future: asyncio.Task) -> None:
            # Native work is finished now, not merely when its HTTP waiter left.
            self.active = False
            if not future.cancelled():
                future.exception()  # Consume abandoned failures, never log text.

        task.add_done_callback(completed)
        started = time.monotonic()
        try:
            while not task.done():
                if time.monotonic() - started >= self.timeout:
                    raise SpeechError("speech_timeout", 504)
                if await request.is_disconnected():
                    raise SpeechError("speech_cancelled", 499)
                await asyncio.wait({task}, timeout=0.05)
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
        return JSONResponse(
            {
                "ready": ready,
                "provider": runtime.name,
                "model": runtime.model,
                "capabilities": asdict(SpeechCapabilities()),
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

    return app


app = create_app()
