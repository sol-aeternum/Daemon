"""Authenticated progressive route contracts, fictional payloads and ASGI failures."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import uuid

from fastapi import FastAPI
import httpx
import pytest

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings
from orchestrator.routes import speech_stream as routes
from orchestrator.speech.contracts import SpeechCapabilities, SpeechError
from orchestrator.speech.stream_protocol import StreamParser, metadata
from orchestrator.speech import service


class Authority:
    def __init__(self):
        self.closed = False
        self.refreshes = 0
        self.error = None

    async def checkpoint(self):
        if self.error:
            raise self.error

    async def refresh(self):
        self.refreshes += 1
        await self.checkpoint()

    async def wait_failure(self):
        await asyncio.Future()

    async def close(self):
        self.closed = True


class Upstream:
    def __init__(self, speech, *, truncate=False, fail=False):
        self.metadata = metadata("kokoro", "fictional-model", speech)
        self.closed = False
        self.truncate, self.fail = truncate, fail

    async def events(self):
        yield SimpleNamespace(kind=1, payload=b"fictional-audio")
        if self.fail:
            raise SpeechError("speech_unavailable")
        yield SimpleNamespace(
            kind=2,
            payload={
                "frames": 1,
                "bytes": len(b"fictional-audio"),
                "source_seconds": 1.0,
                "encoded_seconds": 1.048,
                "synthesis_seconds": 0.1,
                "audio_path": None,
                "cache_available": False,
            },
        )
        if self.truncate:
            raise SpeechError("speech_protocol_error")

    async def close(self):
        self.closed = True


class Provider:
    name, model = "kokoro", "fictional-model"

    def __init__(self):
        self.calls = []
        self.streams = []
        self.truncate, self.fail = False, False

    def capabilities(self):
        return SpeechCapabilities()

    async def health(self):
        return True

    async def open_stream(self, speech):
        self.calls.append(speech)
        stream = Upstream(speech, truncate=self.truncate, fail=self.fail)
        self.streams.append(stream)
        return stream


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    provider, authorities, admissions = Provider(), [], []
    owner = {"id": uuid.uuid4()}

    async def auth():
        return AuthenticatedDevice(owner["id"], uuid.uuid4(), uuid.uuid4())

    async def from_request(request, authenticated):
        authority = Authority()
        authorities.append(authority)
        return authority

    async def check(*arguments, owner_id):
        assert owner_id == str(owner["id"]) == arguments[2]
        admissions.append(arguments)
        return SimpleNamespace(allowed=True)

    app.dependency_overrides[require_device_auth] = auth
    app.dependency_overrides[get_settings] = lambda: cast(Any, Settings)(_env_file=None)
    monkeypatch.setattr(routes, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "get_speech_provider", lambda settings: provider)
    monkeypatch.setattr(routes.SpeechAuthority, "from_request", from_request)
    monkeypatch.setattr(
        routes,
        "get_rate_limiter",
        lambda request: SimpleNamespace(
            is_redis_available=True,
            check=check,
        ),
    )
    return app, provider, authorities, admissions, owner, tmp_path


def parse(response) -> list[Any]:
    assert response.status_code == 200
    parser = StreamParser()
    events = list(parser.feed(response.content))
    parser.finish()
    return events


@pytest.mark.asyncio
async def test_gated_capability_get_never_admits_or_synthesizes(setup):
    app, provider, authorities, admissions, _, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/tts/capabilities")
    assert response.json()["streams"] == []
    assert response.json()["formats"] == ["mp3", "opus", "wav"]
    assert response.json()["limits"]["max_source_seconds"] == 300
    assert "no-store" in response.headers["cache-control"]
    assert not provider.calls and not authorities and not admissions


@pytest.mark.asyncio
async def test_uncached_stream_one_admission_one_post_no_published_path(setup):
    app, provider, authorities, admissions, _, root = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/tts/stream/v1",
            json={
                "text": "A fictional sentence.",
                "voice": "rachel",
                "speed": 1.25,
                "model": "ignored-client-model",
                "cache": False,
            },
        )
    events = parse(response)
    assert [event.kind for event in events] == [0, 1, 2]
    assert events[0].payload["voice"] == "daemon-default"
    assert events[0].payload["speed"] == 1.25
    assert events[2].payload["audio_path"] is None
    assert events[2].payload["cache_available"] is False
    assert len(provider.calls) == len(admissions) == 1
    assert admissions[0][:2] == ("speech:tts", "user_id")
    assert authorities[0].closed and provider.streams[0].closed
    assert not list(root.iterdir())
    assert service._active == 0


@pytest.mark.asyncio
async def test_complete_cache_hit_same_framing_no_native_call_and_owner_isolation(setup):
    app, provider, authorities, _, owner, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = parse(await client.post("/tts/stream/v1", json={"text": "Fictional cache clip."}))
        assert first[-1].payload["cache_available"] is True
        second = parse(await client.post("/tts/stream/v1", json={"text": "Fictional cache clip."}))
        assert second[0].payload["cached"] is True
        assert second[-1].payload["synthesis_seconds"] == 0
        assert len(provider.calls) == 1
        owner["id"] = uuid.uuid4()
        other = parse(await client.post("/tts/stream/v1", json={"text": "Fictional cache clip."}))
        assert other[0].payload["cached"] is False
        assert len(provider.calls) == 2
    assert all(authority.closed for authority in authorities)
    assert service._active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail", "truncate"])
async def test_failure_after_audio_or_terminal_never_publishes_or_replays(setup, failure):
    app, provider, authorities, admissions, _, root = setup
    setattr(provider, failure, True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/tts/stream/v1", json={"text": "Fictional failing clip."})
    parser = StreamParser()
    with pytest.raises(SpeechError):
        list(parser.feed(response.content))
        parser.finish()
    for _ in range(50):
        if not routes._disk_tails:
            break
        await asyncio.sleep(0.01)
    assert not list(root.glob("*/*.mp3"))
    assert len(provider.calls) == len(admissions) == 1
    assert authorities[0].closed and provider.streams[0].closed
    assert service._active == 0


@pytest.mark.asyncio
async def test_optional_storage_failure_does_not_discard_valid_live_audio(setup, monkeypatch):
    app, provider, _, _, _, _ = setup
    monkeypatch.setattr(routes, "reserve_audio", lambda *args: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        events = parse(
            await client.post("/tts/stream/v1", json={"text": "Fictional uncached clip."})
        )
    assert events[1].payload == b"fictional-audio"
    assert events[-1].payload["audio_path"] is None
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_append_failure_skips_cache_for_all_later_chunks_without_losing_audio(
    setup, monkeypatch
):
    from unittest.mock import Mock

    app, provider, _, _, _, _ = setup
    reservation = SimpleNamespace(
        append=Mock(side_effect=OSError("fictional disk full")), abort=Mock()
    )
    monkeypatch.setattr(routes, "reserve_audio", lambda *args: reservation)

    async def upstream_events(self):
        for part in (b"one", b"two", b"three"):
            yield SimpleNamespace(kind=1, payload=part)
        yield SimpleNamespace(
            kind=2,
            payload={
                "frames": 3,
                "bytes": 11,
                "source_seconds": 1.0,
                "encoded_seconds": 1.048,
                "synthesis_seconds": 0.1,
                "audio_path": None,
                "cache_available": False,
            },
        )

    monkeypatch.setattr(Upstream, "events", upstream_events)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        events = parse(
            await client.post("/tts/stream/v1", json={"text": "Fictional failing disk."})
        )
    assert b"".join(event.payload for event in events if event.kind == 1) == b"onetwothree"
    assert events[-1].payload["cache_available"] is False
    assert len(provider.calls) == 1
    reservation.append.assert_called_once()
    for _ in range(100):
        if not routes._disk_tails:
            break
        await asyncio.sleep(0.01)
    reservation.abort.assert_called_once()


@pytest.mark.asyncio
async def test_cached_hit_evicted_before_terminal_does_not_advertise_missing_path(
    setup, monkeypatch
):
    app, _, _, _, _, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        parse(await client.post("/tts/stream/v1", json={"text": "Fictional cached clip."}))
        monkeypatch.setattr(routes, "_same_cached_file", lambda *args: False)
        events = parse(await client.post("/tts/stream/v1", json={"text": "Fictional cached clip."}))
    assert events[0].payload["cached"] is True
    assert events[-1].payload["cache_available"] is False
    assert events[-1].payload["audio_path"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        ({"text": " "}, 400),
        ({"text": "x" * 3001}, 422),
        ({"text": "fictional", "format": "opus"}, 422),
        ({"text": "fictional", "format": "wav"}, 422),
        ({"text": "fictional", "voice": "unavailable"}, 422),
    ],
)
async def test_invalid_or_other_format_rejects_before_admission(setup, payload, status):
    app, provider, authorities, admissions, _, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/tts/stream/v1", json=payload)
    assert response.status_code == status
    assert not provider.calls and not authorities and not admissions


@pytest.mark.asyncio
async def test_authority_failure_wins_completed_private_open_and_closes_resource():
    class Failed(Authority):
        async def wait_failure(self):
            raise SpeechError("speech_authorization_lost", 401)

    resource = SimpleNamespace(close=AsyncMock())

    async def open_stream():
        return resource

    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        await routes._authorized_setup(cast(Any, Failed()), open_stream())
    resource.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_disk_constructor_keeps_and_cleans_actual_tail():
    import threading
    from unittest.mock import Mock

    began, release = threading.Event(), threading.Event()
    resource = SimpleNamespace(abort=Mock())

    def reserve():
        began.set()
        assert release.wait(2)
        return resource

    disk = routes.DiskOwner()
    task = asyncio.create_task(disk.call(reserve))
    await asyncio.to_thread(began.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    disk.close()
    resource.abort.assert_not_called()
    release.set()
    for _ in range(100):
        if not routes._disk_tails:
            break
        await asyncio.sleep(0.01)
    resource.abort.assert_called_once()
    assert not routes._disk_tails


@pytest.mark.asyncio
async def test_unregistered_device_cannot_discover_or_start_speech(monkeypatch):
    from unittest.mock import Mock

    app = FastAPI()
    app.include_router(routes.router)
    provider = Mock()
    monkeypatch.setattr(routes, "get_speech_provider", provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/tts/capabilities")).status_code == 401
        assert (
            await client.post("/tts/stream/v1", json={"text": "Fictional denied speech."})
        ).status_code == 401
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_four_admission_leases_span_stream_bodies_and_fifth_never_starts_native(
    setup, monkeypatch
):
    app, provider, authorities, _, _, _ = setup
    release = asyncio.Event()
    original = Upstream.events

    async def blocked(self):
        await release.wait()
        async for event in original(self):
            yield event

    monkeypatch.setattr(Upstream, "events", blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = [
            asyncio.create_task(
                client.post(
                    "/tts/stream/v1",
                    json={
                        "text": "Fictional bounded concurrent speech.",
                        "cache": False,
                    },
                )
            )
            for _ in range(4)
        ]
        try:
            async with asyncio.timeout(2):
                while len(provider.calls) < 4:
                    await asyncio.sleep(0.005)
            assert service._active == 4
            fifth = await client.post(
                "/tts/stream/v1", json={"text": "Fictional fifth request.", "cache": False}
            )
            assert fifth.status_code == 429
            assert len(provider.calls) == 4
        finally:
            release.set()
            results = await asyncio.gather(*pending)
        for result in results:
            parse(result)
    assert service._active == 0
    assert all(authority.closed for authority in authorities)
