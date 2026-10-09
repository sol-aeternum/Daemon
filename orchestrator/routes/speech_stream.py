"""Authenticated framed speech delivery, separate from LLM compute accounting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
import logging
import os
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from orchestrator.models import TtsRequest
from orchestrator.services.identity import RateLimitPolicy, get_rate_limiter
from orchestrator.services.identity.rate_limiter import RateLimitUnavailableError
from orchestrator.speech.authority import SpeechAuthority
from orchestrator.speech.contracts import SpeechError, SpeechRequest, canonical_voice
from orchestrator.speech.release import PROGRESSIVE_SPEECH_QUALIFIED
from orchestrator.speech.service import get_speech_provider, speech_admission
from orchestrator.speech.stream_cache import open_cached_audio, progressive_filename, reserve_audio
from orchestrator.speech.stream_protocol import encode_audio, encode_frame, metadata
from orchestrator.speech.stream_response import OwnedSpeechResponse
from orchestrator.speech.stream_transport import ProgressiveProvider

logger = logging.getLogger(__name__)
router = APIRouter()
# Strong ownership for disk tails after the HTTP response is cancelled. Never
# release a reservation/descriptor while its actual thread is still writing.
_disk_tails: set[asyncio.Task[Any]] = set()


def cache_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "tts_cache" / "self-hosted"


def _same_cached_file(root: Path, owner: Any, filename: str, identity: dict, pinned: Any) -> bool:
    current = open_cached_audio(root, owner, filename, identity)
    if current is None:
        return False
    try:
        original, verified = os.fstat(pinned.file.fileno()), os.fstat(current.file.fileno())
        return (original.st_dev, original.st_ino) == (verified.st_dev, verified.st_ino)
    finally:
        current.close()


def limits(settings: Settings) -> dict[str, int | float]:
    return {
        "max_text_code_points": 3000,
        "max_request_bytes": 32768,
        "max_audio_bytes": 16_000_000,
        "max_source_seconds": 300,
        "max_padding_seconds": 0.15,
        "max_wire_bytes": 20_000_000,
        "max_audio_frames": 16384,
        "max_heartbeats": 32,
        "max_audio_frame_payload": 65536,
        "max_control_bytes": 4096,
        "queue_bytes": 262144,
        "queue_frames": 64,
        "heartbeat_seconds": 5,
        "idle_seconds": 15,
        "backpressure_seconds": 10,
        "deadline_seconds": settings.tts_timeout_seconds,
    }


@router.get("/tts/capabilities")
async def capabilities(
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> JSONResponse:
    provider = get_speech_provider(settings)
    if not await provider.health():
        raise HTTPException(503, detail={"code": "speech_not_ready"})
    streams = []
    progressive_health = getattr(provider, "progressive_health", None)
    if (
        PROGRESSIVE_SPEECH_QUALIFIED
        and isinstance(provider, ProgressiveProvider)
        and progressive_health is not None
        and await progressive_health()
    ):
        streams = [
            {
                "version": 1,
                "format": "mp3",
                "mime": "audio/mpeg",
                "sample_rate": 24000,
                "rendering": "speech-mp3-progressive-v1",
            }
        ]
    return JSONResponse(
        {
            "ready": True,
            "provider": provider.name,
            "model": provider.model,
            "voices": list(provider.capabilities().voices),
            "formats": list(provider.capabilities().formats),
            "streams": streams,
            "speed_min": 0.5,
            "speed_max": 2,
            "limits": limits(settings),
        },
        headers={"Cache-Control": "private, no-store, no-transform"},
    )


class DiskOwner:
    """Serialize blocking file operations and keep cancelled tails owned."""

    def __init__(self):
        self.resource: Any = None
        self.pending: asyncio.Task[Any] | None = None
        self.closed = False

    async def call(self, function: Callable[..., Any], *arguments: Any) -> Any:
        if self.closed:
            raise SpeechError("speech_cancelled", 499)
        task = asyncio.create_task(asyncio.to_thread(function, *arguments))
        self.pending = task
        _disk_tails.add(task)
        task.add_done_callback(_disk_tails.discard)
        # Retrieve exceptions even if the HTTP waiter leaves. Shield only this
        # owned wrapper; never shield a whole response from cancellation.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        pending, resource = self.pending, self.resource

        async def finish() -> None:
            # A resource-producing reservation/open task can outlive its waiter.
            # Capture that result too, otherwise cancellation leaks the new lease.
            result = None
            if pending is not None:
                with suppress(Exception):
                    result = await asyncio.shield(pending)
            owned = resource if resource is not None else result
            if owned is not None:
                closer = getattr(owned, "abort", None) or getattr(owned, "close", None)
                if closer is not None:
                    await asyncio.to_thread(closer)

        tail = asyncio.create_task(finish())
        _disk_tails.add(tail)
        tail.add_done_callback(_disk_tails.discard)
        tail.add_done_callback(lambda done: None if done.cancelled() else done.exception())


async def _authorized_setup(
    authority: SpeechAuthority, work: Any, *, close_unclaimed: bool = True
) -> Any:
    operation = asyncio.create_task(work)
    failure = asyncio.create_task(authority.wait_failure())
    transferred = False
    try:
        done, _ = await asyncio.wait((operation, failure), return_when=asyncio.FIRST_COMPLETED)
        if failure in done:
            await failure
        result = await operation
        transferred = True
        return result
    finally:
        failure.cancel()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, failure, return_exceptions=True)
        if (
            close_unclaimed
            and not transferred
            and not operation.cancelled()
            and operation.exception() is None
        ):
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await operation.result().close()


@router.post("/tts/stream/v1")
async def stream_speech(
    payload: TtsRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
) -> OwnedSpeechResponse:
    deadline = time.monotonic() + settings.tts_timeout_seconds
    authority: SpeechAuthority | None = None
    upstream = None
    disk = DiskOwner()
    admission = speech_admission()
    admitted, closed = False, False
    started = time.monotonic()
    outcome, cached, byte_count, complete = "failure", False, 0, None
    provider, speech = None, None

    async def cleanup() -> None:
        nonlocal closed, admitted
        if closed:
            return
        closed = True
        disk.close()
        if admitted:
            admitted = False
            await admission.__aexit__(None, None, None)
        if authority is not None:
            await authority.close()
        if upstream is not None:
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await upstream.close()
        if cached and provider is not None and speech is not None:
            logger.info(
                "speech_cache_hit provider=%s model=%s characters=%d",
                provider.name,
                provider.model,
                len(speech.text),
            )
        elif provider is not None and speech is not None:
            logger.info(
                "speech_usage provider=%s model=%s characters=%d audio_bytes=%d "
                "audio_seconds=%.3f synthesis_seconds=%.3f wall_seconds=%.3f outcome=%s",
                provider.name,
                provider.model,
                len(speech.text),
                byte_count,
                complete["source_seconds"] if complete else 0,
                complete["synthesis_seconds"] if complete else 0,
                time.monotonic() - started,
                outcome,
            )

    try:
        async with asyncio.timeout_at(deadline):
            speech = SpeechRequest(
                payload.text.strip(),
                canonical_voice(payload.voice),
                payload.speed if payload.speed is not None else 1,
                payload.format or "mp3",
            )
            if speech.format != "mp3":
                raise SpeechError("speech_stream_unsupported", 422)
            provider = get_speech_provider(settings)
            if not isinstance(provider, ProgressiveProvider):
                raise SpeechError("speech_stream_unsupported", 503)
            limiter = get_rate_limiter(request)
            if not limiter.is_redis_available:
                raise SpeechError("speech_admission_unavailable")
            try:
                decision = await limiter.check(
                    "speech:tts",
                    "user_id",
                    str(auth.user_id),
                    RateLimitPolicy(12, 60),
                    owner_id=str(auth.user_id),
                )
            except RateLimitUnavailableError:
                raise SpeechError("speech_admission_unavailable") from None
            if not decision.allowed:
                raise HTTPException(
                    429,
                    detail={"code": "speech_rate_limited"},
                    headers={"Retry-After": str(decision.retry_after_seconds)},
                )
            authority = await SpeechAuthority.from_request(request, auth)
            await admission.__aenter__()
            admitted = True
            root, filename = (
                cache_root(),
                progressive_filename(provider.name, provider.model, speech),
            )
            identity = metadata(provider.name, provider.model, speech)
            identity = {
                key: value for key, value in identity.items() if key not in ("stream_id", "cached")
            }
            hit = None
            if payload.cache is not False:
                hit = await _authorized_setup(
                    authority,
                    disk.call(open_cached_audio, root, auth.user_id, filename, identity),
                    close_unclaimed=False,
                )
                disk.resource = hit
            cached = hit is not None
            if not cached:
                upstream = await _authorized_setup(authority, provider.open_stream(speech))
                if payload.cache is not False:
                    disk.resource = await _authorized_setup(
                        authority,
                        disk.call(reserve_audio, root, auth.user_id, filename, identity),
                        close_unclaimed=False,
                    )
                public_metadata = dict(upstream.metadata)
            else:
                public_metadata = metadata(provider.name, provider.model, speech, cached=True)
            await authority.checkpoint()

        async def body() -> AsyncIterator[bytes]:
            nonlocal complete, byte_count, outcome
            frames = 0
            if hit is not None:
                while True:
                    chunk = await disk.call(hit.file.read, 65532)
                    if not chunk:
                        break
                    await authority.checkpoint()
                    byte_count += len(chunk)
                    yield encode_audio(frames, chunk)
                    frames += 1
                complete = dict(hit.complete)
                complete.update(frames=frames, bytes=byte_count, synthesis_seconds=0)
            else:
                if upstream is None:
                    raise SpeechError("speech_protocol_error")
                async for event in upstream.events():
                    await authority.checkpoint()
                    if event.kind == 1:
                        chunk = event.payload
                        if not isinstance(chunk, bytes):
                            raise SpeechError("speech_protocol_error")
                        byte_count += len(chunk)
                        if disk.resource is not None and not disk.closed:
                            try:
                                await disk.call(disk.resource.append, chunk)
                            except (OSError, ValueError):
                                disk.close()
                        yield encode_audio(frames, chunk)
                        frames += 1
                    elif event.kind == 2:
                        if not isinstance(event.payload, dict):
                            raise SpeechError("speech_protocol_error")
                        complete = dict(event.payload)
                    # Private heartbeats are consumed; response owns public cadence.
                if complete is None:
                    raise SpeechError("speech_protocol_error")
                available = False
                if disk.resource is not None and not disk.closed:
                    await authority.refresh()
                    await authority.checkpoint()
                    try:
                        available = await disk.call(disk.resource.commit, complete)
                    except (OSError, ValueError):
                        available = False
                complete.update(
                    cache_available=available,
                    audio_path=f"/generated-audio/{filename}" if available else None,
                )
            if cached:
                # A pinned descriptor permits playback after eviction; it does
                # not promise the pathname still exists at the terminal.
                available = await disk.call(
                    _same_cached_file, root, auth.user_id, filename, identity, hit
                )
                complete.update(
                    cache_available=available,
                    audio_path=f"/generated-audio/{filename}" if available else None,
                )
            await authority.checkpoint()
            outcome = "success"
            yield encode_frame(2, complete)

        return OwnedSpeechResponse(
            public_metadata,
            body(),
            cleanup=cleanup,
            deadline=deadline,
            checkpoint=authority.checkpoint,
            failure=authority.wait_failure(),
        )
    except BaseException as error:
        # Preparation failures occur before response ownership and must release
        # admission/reservations even though no body iterator has been constructed.
        disk.close()
        if admitted:
            await admission.__aexit__(None, None, None)
            admitted = False
        if authority is not None:
            await authority.close()
        if upstream is not None:
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await upstream.close()
        if isinstance(error, TimeoutError):
            raise HTTPException(504, detail={"code": "speech_timeout"}) from None
        if isinstance(error, SpeechError):
            raise HTTPException(error.status, detail={"code": error.code}) from None
        raise
