from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator import main as main_module
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.main import app
from orchestrator.tools import spawn


PNG = b"\x89PNG\r\n\x1a\nvalid-image-data"
JPEG = b"\xff\xd8\xff\xe0valid-jpeg-data"
WEBP = b"RIFF\x0c\x00\x00\x00WEBPvalid-webp-data"
MP3 = b"\xff\xfb\x90\x64valid-mp3-data"
WAV = b"RIFF\x0c\x00\x00\x00WAVEvalid-wav-data"
OGG = b"OggSvalid-ogg-data"
OPUS = b"OggS\x00\x00OpusHeadvalid-opus-data"


def _encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _result(media_kind: str, raw: bytes, claimed_format: str) -> dict[str, Any]:
    return {
        "agent_type": media_kind,
        "success": True,
        "error": None,
        "metadata": {},
        "data": {
            f"{media_kind}_base64": _encoded(raw),
            f"{media_kind}_url": "https://provider.invalid/result",
            "format": claimed_format,
        },
    }


@pytest.mark.parametrize(
    ("claimed_format", "raw", "extension"),
    [
        ("png", PNG, "png"),
        ("jpg", JPEG, "jpg"),
        ("jpeg", JPEG, "jpg"),
        ("webp", WEBP, "webp"),
    ],
)
def test_persist_image_accepts_allowlisted_matching_signatures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    claimed_format: str,
    raw: bytes,
    extension: str,
) -> None:
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", tmp_path)

    persisted = spawn._persist_image_result(_result("image", raw, claimed_format))

    filename = f"{hashlib.sha256(raw).hexdigest()}.{extension}"
    assert persisted["success"] is True
    assert persisted["data"]["image_path"] == f"/generated-images/{filename}"
    assert "image_base64" not in persisted["data"]
    assert "image_url" not in persisted["data"]
    assert (tmp_path / filename).read_bytes() == raw


@pytest.mark.parametrize(
    ("claimed_format", "raw"),
    [
        ("mp3", MP3),
        ("wav", WAV),
        ("ogg", OGG),
        ("opus", OPUS),
    ],
)
def test_persist_audio_accepts_allowlisted_matching_signatures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    claimed_format: str,
    raw: bytes,
) -> None:
    monkeypatch.setattr(spawn, "GENERATED_AUDIO_DIR", tmp_path)

    persisted = spawn._persist_audio_result(_result("audio", raw, claimed_format))

    filename = f"{hashlib.sha256(raw).hexdigest()}.{claimed_format}"
    assert persisted["success"] is True
    assert persisted["data"]["audio_path"] == f"/generated-audio/{filename}"
    assert "audio_base64" not in persisted["data"]
    assert "audio_url" not in persisted["data"]
    assert (tmp_path / filename).read_bytes() == raw


@pytest.mark.parametrize(
    ("payload", "claimed_format"),
    [
        (b"<script>alert('xss')</script>", "png"),
        (PNG, "webp"),
        (PNG, "txt"),
        (PNG, "../../../tmp/payload"),
    ],
)
def test_persist_image_rejects_non_media_mismatches_and_unsafe_formats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
    claimed_format: str,
) -> None:
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", tmp_path)

    persisted = spawn._persist_image_result(_result("image", payload, claimed_format))

    assert persisted["success"] is False
    assert persisted["error"] == "Generated image payload failed validation"
    assert "image_base64" not in persisted["data"]
    assert "image_url" not in persisted["data"]
    assert list(tmp_path.iterdir()) == []


def test_persist_image_rejects_invalid_base64_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", tmp_path)
    result = _result("image", PNG, "png")
    result["data"]["image_base64"] = "not base64!!!"

    persisted = spawn._persist_image_result(result)

    assert persisted["success"] is False
    assert list(tmp_path.iterdir()) == []


def test_persist_audio_rejects_payload_over_the_decoded_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(spawn, "GENERATED_AUDIO_DIR", tmp_path)
    monkeypatch.setattr(spawn, "MAX_PERSISTED_MEDIA_BYTES", 4)

    persisted = spawn._persist_audio_result(_result("audio", MP3, "mp3"))

    assert persisted["success"] is False
    assert list(tmp_path.iterdir()) == []


def test_persist_image_does_not_follow_an_existing_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"do not overwrite")
    filename = f"{hashlib.sha256(PNG).hexdigest()}.png"
    (generated_dir / filename).symlink_to(target)
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", generated_dir)

    persisted = spawn._persist_image_result(_result("image", PNG, "png"))

    assert persisted["success"] is False
    assert target.read_bytes() == b"do not overwrite"


def test_persist_image_reuses_matching_content_addressed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = f"{hashlib.sha256(PNG).hexdigest()}.png"
    (tmp_path / filename).write_bytes(PNG)
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", tmp_path)

    persisted = spawn._persist_image_result(_result("image", PNG, "png"))

    assert persisted["success"] is True
    assert persisted["data"]["image_path"] == f"/generated-images/{filename}"


def test_persist_image_rejects_conflicting_content_addressed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = f"{hashlib.sha256(PNG).hexdigest()}.png"
    (tmp_path / filename).write_bytes(b"unexpected existing content")
    monkeypatch.setattr(spawn, "GENERATED_IMAGES_DIR", tmp_path)

    persisted = spawn._persist_image_result(_result("image", PNG, "png"))

    assert persisted["success"] is False
    assert (tmp_path / filename).read_bytes() == b"unexpected existing content"


@pytest_asyncio.fixture
async def authenticated_client() -> AsyncIterator[AsyncClient]:
    async def authenticated() -> AuthenticatedDevice:
        return AuthenticatedDevice(
            user_id=uuid.uuid4(),
            device_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

    app.dependency_overrides[require_device_auth] = authenticated
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(require_device_auth, None)


@pytest.mark.parametrize(
    ("route", "directory_attribute", "filename", "expected_type"),
    [
        ("generated-images", "GENERATED_IMAGES_DIR", "sample.png", "image/png"),
        ("generated-images", "GENERATED_IMAGES_DIR", "sample.jpg", "image/jpeg"),
        ("generated-images", "GENERATED_IMAGES_DIR", "sample.webp", "image/webp"),
        ("generated-audio", "GENERATED_AUDIO_DIR", "sample.mp3", "audio/mpeg"),
        ("generated-audio", "GENERATED_AUDIO_DIR", "sample.wav", "audio/wav"),
        ("generated-audio", "GENERATED_AUDIO_DIR", "sample.ogg", "audio/ogg"),
        ("generated-audio", "GENERATED_AUDIO_DIR", "sample.opus", "audio/ogg"),
    ],
)
@pytest.mark.asyncio
async def test_generated_media_routes_use_expected_type_and_nosniff(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
    directory_attribute: str,
    filename: str,
    expected_type: str,
) -> None:
    media_dir = tmp_path / directory_attribute.lower()
    media_dir.mkdir()
    (media_dir / filename).write_bytes(b"route-test")
    monkeypatch.setattr(main_module, directory_attribute, media_dir)
    if route == "generated-audio":
        tts_dir = tmp_path / "tts"
        tts_dir.mkdir()
        monkeypatch.setattr(main_module, "TTS_CACHE_DIR", tts_dir)

    response = await authenticated_client.get(f"/{route}/{filename}")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == expected_type
    assert response.headers["X-Content-Type-Options"] == "nosniff"
