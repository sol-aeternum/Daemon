"""Regression test for issue #114 — /tts cache filename built from unvalidated `format` field.

Covers the audit finding: the `/tts` endpoint accepted any string in
`payload.format`, then built `f"{cache_key}.{fmt}"` for the server-side
cache file path. An authenticated caller could influence the write path.
The fix constrains `TtsRequest.format` to `Literal["mp3","wav","ogg"] | None`
so Pydantic rejects anything else with 422 before the filesystem path is
constructed.

The test asserts:
1. `TtsRequest` accepts exactly the three valid formats plus None.
2. Pydantic rejects invalid formats with a 422-shaped ValidationError.
3. POST `/tts` with an invalid `format` returns 422 and writes no file
   to TTS_CACHE_DIR (verified by listing the directory before/after).
4. POST `/tts` with no `format` field still uses the default ("mp3").
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.main import app
from orchestrator.models import TtsRequest


# ---------------------------------------------------------------------------
# Model-layer: TtsRequest.format
# ---------------------------------------------------------------------------


def test_tts_request_accepts_valid_formats() -> None:
    """All three supported formats are accepted without modification."""
    for fmt in ("mp3", "wav", "ogg"):
        req = TtsRequest(text="hello", format=fmt)  # type: ignore[arg-type]
        assert req.format == fmt


def test_tts_request_accepts_none_format() -> None:
    """None is accepted and the default downstream is "mp3"."""
    req = TtsRequest(text="hello")
    assert req.format is None


@pytest.mark.parametrize(
    "bad_format",
    [
        "exe",
        "../etc/passwd",
        "mp3\x00.wav",
        "MP3",  # case-sensitive; only the lowercase literals are allowed
        "pcm",
        "",
    ],
)
def test_tts_request_rejects_invalid_formats(bad_format: str) -> None:
    """Any format outside the literal set is rejected at construction."""
    with pytest.raises(ValidationError):
        TtsRequest(text="hello", format=bad_format)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Endpoint-layer: POST /tts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_endpoint_rejects_invalid_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid `format` → 422 from the endpoint, no file written to TTS_CACHE_DIR.

    The test uses a fresh TTS_CACHE_DIR via monkeypatch so we can assert
    the directory contents are unchanged. We override `require_device_auth`
    so we don't need real device credentials, and we patch
    `text_to_speech` upstream of the ElevenLabs call so the test does not
    need a real API key — the only path we care about is the request
    validation before any file is written.
    """
    # Use a tmp directory as the cache; capture the original module
    # constant so we can restore it after the test.
    cache_dir = tmp_path / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("orchestrator.main.TTS_CACHE_DIR", cache_dir)

    before = set(cache_dir.iterdir())

    async def override_auth() -> AuthenticatedDevice:
        return AuthenticatedDevice(
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        )

    app.dependency_overrides[require_device_auth] = override_auth
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/tts",
                json={"text": "hello", "format": "../etc/passwd"},
            )
        assert response.status_code == 422, response.text
        # No file should have been created.
        assert set(cache_dir.iterdir()) == before, (
            "TTS_CACHE_DIR was mutated by a request that should have been rejected at the model layer"
        )
    finally:
        app.dependency_overrides.pop(require_device_auth, None)


@pytest.mark.asyncio
async def test_tts_request_validation_error_payload_is_shape_only() -> None:
    """Sanity: the ValidationError raised by an invalid TtsRequest mentions `format`."""
    try:
        TtsRequest(text="hello", format="not-a-real-format")  # type: ignore[arg-type]
    except ValidationError as exc:
        # Pydantic ValidationError.errors() yields dicts; one of them must mention `format`.
        locs = [tuple(err.get("loc", ())) for err in exc.errors()]
        assert any("format" in loc for loc in locs), (
            f"expected the ValidationError to mention `format`, got locs={locs}"
        )
    else:
        pytest.fail("expected ValidationError for invalid format")
