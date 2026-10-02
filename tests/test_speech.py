"""Provider contract and public API regressions; no paid providers or model assets."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import uuid
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from orchestrator import main
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from orchestrator.models import TtsRequest
from orchestrator.speech import cache
from orchestrator.speech.contracts import (
    SpeechAudio,
    SpeechCapabilities,
    SpeechError,
    SpeechRequest,
    canonical_voice,
)
from orchestrator.speech.provider import InternalSpeechProvider
from orchestrator.speech.service import get_speech_provider, synthesize


class ReplacementProvider:
    name = "replacement"
    model = "another-small-model"

    def capabilities(self):
        return SpeechCapabilities()

    async def health(self):
        return True

    async def synthesize(self, request):
        return SpeechAudio(b"fake-contract-audio", 1, 24000, 0.1)


@pytest.mark.parametrize(
    "text,code",
    [
        (" ", "text_required"),
        ("x" * 3001, "text_too_long"),
        ("abc\0", "invalid_text"),
        ("hello " * 90, "repetitive_text"),
    ],
)
def test_request_limits(text, code):
    with pytest.raises(SpeechError, match=code):
        SpeechRequest(text)


def test_voice_and_configuration():
    assert canonical_voice("rachel") == "daemon-default"
    assert canonical_voice("Xb7hH8MSUJpSbSDYk0k2") == "daemon-default"
    with pytest.raises(SpeechError, match="voice_unavailable"):
        canonical_voice("../../voice")
    settings = cast(Any, Settings)(_env_file=None)
    provider = get_speech_provider(settings)
    assert provider.name == "kokoro"
    assert provider.model == "kokoro-82m-v1.0"
    assert provider.capabilities().streaming is False
    for kwargs in (
        {"tts_provider": "unknown"},
        {"tts_service_url": "file:///secret"},
        {"tts_timeout_seconds": 0},
    ):
        with pytest.raises(ValidationError):
            cast(Any, Settings)(_env_file=None, **kwargs)
    for speed in (0, float("nan"), float("inf"), 10):
        with pytest.raises(ValidationError):
            TtsRequest(text="hello", speed=speed)


@pytest.mark.asyncio
async def test_replacement_contract_and_telemetry(caplog):
    with caplog.at_level("INFO"):
        audio = await synthesize(ReplacementProvider(), SpeechRequest("private sentence"), 1)
    assert audio.duration == 1
    assert "provider=replacement" in caplog.text
    assert "characters=16" in caplog.text
    assert "private sentence" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_and_failure():
    class Slow(ReplacementProvider):
        async def synthesize(self, request):
            await asyncio.sleep(10)
            return SpeechAudio(b"audio", 1, 24000, 0.1)

    with pytest.raises(SpeechError, match="speech_timeout"):
        await synthesize(Slow(), SpeechRequest("hello"), 0.01)

    class Failed(ReplacementProvider):
        async def synthesize(self, request):
            raise SpeechError("speech_unavailable")

    with pytest.raises(SpeechError, match="speech_unavailable"):
        await synthesize(Failed(), SpeechRequest("hello"), 1)


@pytest.mark.asyncio
async def test_http_adapter_identity_limits_and_health(monkeypatch):
    original = httpx.AsyncClient
    responses = [
        httpx.Response(200, json={"ready": True, "provider": "kokoro", "model": "model"}),
        httpx.Response(
            200,
            content=b"audio",
            headers={
                "x-speech-provider": "kokoro",
                "x-speech-model": "model",
                "x-audio-duration": "1",
                "x-audio-sample-rate": "24000",
                "x-synthesis-seconds": "0.2",
            },
        ),
        httpx.Response(429),
        httpx.Response(504),
        httpx.Response(503),
        httpx.Response(200, content=b"audio", headers={"x-speech-provider": "wrong"}),
    ]

    def handler(request):
        return responses.pop(0)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = InternalSpeechProvider(name="kokoro", model="model", url="http://private", timeout=1)
    assert await provider.health()
    assert (await provider.synthesize(SpeechRequest("hello"))).duration == 1
    for code in ("speech_busy", "speech_timeout", "speech_unavailable", "speech_identity_mismatch"):
        with pytest.raises(SpeechError, match=code):
            await provider.synthesize(SpeechRequest("hello"))


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_code", ["speech_output_too_long", "speech_output_too_large"])
async def test_http_adapter_preserves_output_limit_status(monkeypatch, runtime_code):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(413, json={"detail": {"code": runtime_code}})
            ),
            **kwargs,
        ),
    )
    provider = InternalSpeechProvider(name="kokoro", model="model", url="http://private", timeout=1)
    with pytest.raises(SpeechError, match="speech_output_limit") as caught:
        await provider.synthesize(SpeechRequest("hello"))
    assert caught.value.status == 413


def test_cache_bounds_and_owner_isolation(tmp_path, monkeypatch):
    owner, other = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(cache, "CACHE_FILES", 2)
    for i in range(3):
        cache.store_audio(tmp_path, owner, f"{i}.wav", b"audio")
    assert cache.cached_audio(tmp_path, owner, "0.wav") is None
    assert cache.cached_audio(tmp_path, owner, "2.wav") is not None
    assert cache.cached_audio(tmp_path, other, "2.wav") is None
    request = SpeechRequest("hello")
    assert cache.audio_filename("a", "b", request) != cache.audio_filename("a", "c", request)
    assert cache.audio_filename("a", "b", request) != cache.audio_filename(
        "a", "b", replace(request, speed=2)
    )
    assert cache.audio_filename("a", "b", replace(request, speed=1)) == cache.audio_filename(
        "a", "b", replace(request, speed=1.0)
    )
    monkeypatch.setattr(cache, "CACHE_BYTES", 10)
    cache.store_audio(tmp_path, owner, "new.wav", b"123456789")
    assert cache.cached_audio(tmp_path, owner, "2.wav") is None
    with pytest.raises(ValueError):
        cache.store_audio(tmp_path, owner, "big.wav", b"x" * 11)


@pytest.mark.asyncio
async def test_api_compatible_and_account_independent(tmp_path: Path, monkeypatch):
    owner = uuid.uuid4()
    current = {"owner": owner}

    async def auth():
        return AuthenticatedDevice(
            user_id=current["owner"], device_id=uuid.uuid4(), session_id=uuid.uuid4()
        )

    calls = []

    async def check(endpoint, scope, raw, policy):
        calls.append((endpoint, scope))
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(
        main,
        "get_rate_limiter",
        lambda request: SimpleNamespace(is_redis_available=True, check=check),
    )
    monkeypatch.setattr(main, "get_speech_provider", lambda settings: ReplacementProvider())
    monkeypatch.setattr(main, "TTS_CACHE_DIR", tmp_path)
    main.app.dependency_overrides[require_device_auth] = auth
    main.app.dependency_overrides[get_settings] = lambda: cast(Any, Settings)(_env_file=None)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/tts",
                json={
                    "text": "hello",
                    "voice": "rachel",
                    "model": "/secret/model",
                    "format": "wav",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["voice"] == "daemon-default"
            assert data["model"] == "another-small-model"
            assert data["format"] == "wav"
            assert (await client.get(data["audio_path"])).content == b"fake-contract-audio"
            assert (await client.post("/tts", json={"text": "hello", "format": "wav"})).json()[
                "cached"
            ]
            for _ in range(2):
                fresh = await client.post(
                    "/tts", json={"text": "hello", "format": "wav", "cache": False}
                )
                assert fresh.status_code == 200
                assert fresh.json()["cached"] is False
            current["owner"] = uuid.uuid4()
            assert (await client.get(data["audio_path"])).status_code == 404
            assert (
                await client.post("/tts", json={"text": "hello", "voice": "invalid"})
            ).status_code == 422
            assert (await client.post("/tts", json={"text": " "})).status_code == 400
            assert (await client.get("/tts/health")).json()["ready"]
            assert (await client.get("/audio/token")).status_code == 503

            def failed_store(*args):
                raise OSError("private storage detail")

            monkeypatch.setattr(main, "store_audio", failed_store)
            failed = await client.post("/tts", json={"text": "not cached"})
            assert failed.status_code == 503
            assert failed.json()["detail"]["code"] == "speech_storage_unavailable"
            assert "private storage detail" not in failed.text
        # No account_compute/entitlement/funded reservation dependency exists.
        assert calls and set(calls) == {("speech:tts", "user_id")}
    finally:
        main.app.dependency_overrides.pop(require_device_auth, None)
        main.app.dependency_overrides.pop(get_settings, None)


@pytest.mark.asyncio
async def test_api_requires_authentication():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        response = await client.post("/tts", json={"text": "hello"})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_rate_admission_fails_closed_after_redis_connection_loss(monkeypatch):
    from orchestrator.services.identity.rate_limiter import RateLimitUnavailableError

    async def auth():
        return AuthenticatedDevice(
            user_id=uuid.uuid4(), device_id=uuid.uuid4(), session_id=uuid.uuid4()
        )

    async def check(*args):
        raise RateLimitUnavailableError()

    monkeypatch.setattr(
        main,
        "get_rate_limiter",
        lambda request: SimpleNamespace(is_redis_available=True, check=check),
    )
    main.app.dependency_overrides[require_device_auth] = auth
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            response = await client.post("/tts", json={"text": "hello"})
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "speech_admission_unavailable"
    finally:
        main.app.dependency_overrides.pop(require_device_auth, None)


def test_expired_cache_is_not_served(tmp_path, monkeypatch):
    owner = uuid.uuid4()
    cache.store_audio(tmp_path, owner, "old.wav", b"audio")
    monkeypatch.setattr(cache, "CACHE_TTL_SECONDS", -1)
    assert cache.cached_audio(tmp_path, owner, "old.wav") is None
