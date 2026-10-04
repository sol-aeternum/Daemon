"""Unjournalled crash remnants retain data, never guess ownership for deletion."""

import os
import subprocess
import sys
import uuid

import httpx
import pytest

from orchestrator.speech import cache, stream_cache as sc


def test_empty_unlocked_lease_disables_cache_without_deletion_and_logs(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(sc, "_cache_warning_reported", False)
    manager = tmp_path / ".progressive"
    manager.mkdir()
    entry = manager / ("a" * 32 + ".lease")
    entry.write_bytes(b"")
    owner = uuid.uuid4()
    filename = "b" * 64 + ".mp3"
    identity = {
        "provider": "kokoro",
        "model": "fixture",
        "voice": "daemon-default",
        "speed": 1,
        "format": "mp3",
        "mime": "audio/mpeg",
        "sample_rate": 24000,
        "rendering": sc.RENDERING,
    }
    assert sc.reserve_audio(tmp_path, owner, filename, identity) is None
    assert sc.open_cached_audio(tmp_path, owner, filename, identity) is None
    assert entry.read_bytes() == b""
    assert [record.message for record in caplog.records] == ["speech_cache_state_unavailable"]


def test_crashed_buffered_temporary_is_accounted_and_preserved(tmp_path, monkeypatch):
    owner = uuid.uuid4()
    code = """
import os,sys,uuid
from pathlib import Path
from orchestrator.speech import cache,stream_cache as sc
write=sc._write_all
def die(fd,data):
    write(fd,data[:7])
    os._exit(23)
sc._write_all=die
cache.store_audio(Path(sys.argv[1]),uuid.UUID(sys.argv[2]),'fixture.mp3',b'fictional-audio')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(owner)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 23, result.stderr
    temporary = next(tmp_path.glob("*/*.tmp"))
    assert temporary.read_bytes() == b"fiction"
    monkeypatch.setattr(cache, "CACHE_BYTES", 8)
    try:
        cache.store_audio(tmp_path, owner, "new.wav", b"ab")
    except OSError:
        pass
    else:
        raise AssertionError("Unjournalled temporary must not disappear from occupancy")
    assert temporary.read_bytes() == b"fiction"
    assert os.stat(temporary).st_size == 7
    assert cache.cached_audio(tmp_path, owner, temporary.name) is None


@pytest.mark.asyncio
async def test_protected_get_denies_buffered_temporaries_preserving_complete_legacy_names(
    tmp_path, monkeypatch
):
    from orchestrator import main
    from orchestrator.auth import AuthenticatedDevice, require_device_auth
    from orchestrator.artifacts import artifact_owner_namespace

    owner = uuid.uuid4()
    root = tmp_path / "self-hosted"
    directory = root / artifact_owner_namespace(owner)
    directory.mkdir(parents=True)
    temporary = directory / (".fixture.mp3." + "a" * 32 + ".tmp")
    temporary.write_bytes(b"partial")
    for format in ("mp3", "opus", "wav"):
        cache.store_audio(root, owner, f"legacy-clip.{format}", b"complete")
    monkeypatch.setattr(main, "TTS_CACHE_DIR", tmp_path)

    async def auth():
        return AuthenticatedDevice(owner, uuid.uuid4(), uuid.uuid4())

    prior = main.app.dependency_overrides.get(require_device_auth)
    main.app.dependency_overrides[require_device_auth] = auth
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            assert (await client.get(f"/generated-audio/{temporary.name}")).status_code == 404
            for format in ("mp3", "opus", "wav"):
                response = await client.get(f"/generated-audio/legacy-clip.{format}")
                assert response.status_code == 200
                assert response.content == b"complete"
    finally:
        if prior is None:
            main.app.dependency_overrides.pop(require_device_auth, None)
        else:
            main.app.dependency_overrides[require_device_auth] = prior
