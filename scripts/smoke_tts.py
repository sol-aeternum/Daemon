"""Isolated real-provider public-API smoke; fictional auth/admission, not a live login test.

Run against a disposable private TTS container. Never patches a live API process.
Uses no paid service, real account or inference funding ledger.
"""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import uuid

import httpx

from orchestrator import main
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings, get_settings


async def smoke(url: str) -> None:
    owner = uuid.uuid4()
    settings = cast(Any, Settings)(_env_file=None, tts_service_url=url)

    async def auth():
        return AuthenticatedDevice(user_id=owner, device_id=uuid.uuid4(), session_id=uuid.uuid4())

    async def check(*args):
        return SimpleNamespace(allowed=True)

    main.get_rate_limiter = lambda request: SimpleNamespace(is_redis_available=True, check=check)
    main.app.dependency_overrides[require_device_auth] = auth
    main.app.dependency_overrides[get_settings] = lambda: settings
    with tempfile.TemporaryDirectory(prefix="daemon-tts-smoke-") as root:
        main.TTS_CACHE_DIR = Path(root)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            assert (await client.get("/tts/health")).status_code == 200
            response = await client.post(
                "/tts",
                json={
                    "text": "Hello from Daemon. This is real self-hosted speech.",
                    "format": "wav",
                    "model": "eleven_flash_v2_5",
                    "voice": "Xb7hH8MSUJpSbSDYk0k2",
                },
            )
            assert response.status_code == 200, response.text
            metadata = response.json()
            audio = await client.get(metadata["audio_path"])
            assert audio.status_code == 200
            assert audio.content.startswith(b"RIFF") and len(audio.content) > 1000
            cached = await client.post(
                "/tts",
                json={
                    "text": "Hello from Daemon. This is real self-hosted speech.",
                    "format": "wav",
                },
            )
            assert cached.json()["cached"] is True
            print(
                json.dumps(
                    {
                        "metadata": metadata,
                        "bytes": len(audio.content),
                        "public_api_real_provider": "passed",
                        "auth_and_admission": "fictional fixture",
                    }
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    asyncio.run(smoke(parser.parse_args().url))
