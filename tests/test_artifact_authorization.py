from __future__ import annotations

from collections.abc import AsyncIterator
import hashlib
from pathlib import Path
from typing import Any
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator import main as main_module
from orchestrator.artifacts import (
    ArtifactOwnerError,
    artifact_owner_namespace,
    resolve_owned_artifact,
    user_artifact_directory,
    write_owned_artifact,
)
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.tools.builtin import create_default_registry


USER_A = uuid.UUID("a0000000-0000-0000-0000-000000000001")
USER_B = uuid.UUID("b0000000-0000-0000-0000-000000000002")


def _authenticated_device(user_id: uuid.UUID) -> AuthenticatedDevice:
    return AuthenticatedDevice(
        user_id=user_id,
        device_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


@pytest.fixture
def artifact_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    roots = {
        "GENERATED_IMAGES_DIR": tmp_path / "images",
        "GENERATED_AUDIO_DIR": tmp_path / "audio",
        "GENERATED_FILES_DIR": tmp_path / "files",
        "TTS_CACHE_DIR": tmp_path / "tts",
    }
    for attribute, root in roots.items():
        monkeypatch.setattr(main_module, attribute, root)
    return roots


@pytest_asyncio.fixture
async def switching_owner_client() -> AsyncIterator[tuple[AsyncClient, dict[str, uuid.UUID]]]:
    current_owner = {"user_id": USER_A}

    async def authenticated() -> AuthenticatedDevice:
        return _authenticated_device(current_owner["user_id"])

    app.dependency_overrides[require_device_auth] = authenticated
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, current_owner
    finally:
        app.dependency_overrides.pop(require_device_auth, None)


@pytest.mark.asyncio
async def test_generated_artifact_downloads_are_scoped_to_authenticated_owner(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
) -> None:
    client, current_owner = switching_owner_client
    cases = [
        ("/generated-images/owned.png", artifact_roots["GENERATED_IMAGES_DIR"]),
        ("/generated-audio/owned.mp3", artifact_roots["GENERATED_AUDIO_DIR"]),
        ("/generated-audio/owned-tts.mp3", artifact_roots["TTS_CACHE_DIR"]),
        ("/generated-files/owned.csv", artifact_roots["GENERATED_FILES_DIR"]),
    ]

    for route, root in cases:
        filename = route.rsplit("/", maxsplit=1)[-1]
        owner_dir = user_artifact_directory(root, USER_A, create=True)
        (owner_dir / filename).write_bytes(b"owner-a")
        (root / filename).write_bytes(b"legacy-ownerless")

        current_owner["user_id"] = USER_A
        owner_response = await client.get(route)
        assert owner_response.status_code == 200
        assert owner_response.content == b"owner-a"

        current_owner["user_id"] = USER_B
        wrong_owner_response = await client.get(route)
        assert wrong_owner_response.status_code == 404


def test_artifact_namespace_is_opaque_and_server_derived(tmp_path: Path) -> None:
    namespace = artifact_owner_namespace(USER_A)
    owner_dir = user_artifact_directory(tmp_path, USER_A, create=True)

    assert owner_dir.name == namespace
    assert len(namespace) == 64
    assert str(USER_A) not in str(owner_dir)
    assert namespace != artifact_owner_namespace(USER_B)


@pytest.mark.parametrize(
    "filename",
    ("", ".", "..", "../outside.bin", "nested/artifact.bin", "nested/../artifact.bin"),
)
def test_artifact_helpers_reject_non_leaf_names(tmp_path: Path, filename: str) -> None:
    artifact_root = tmp_path / "artifacts"

    assert resolve_owned_artifact(artifact_root, USER_A, filename) is None
    with pytest.raises(ArtifactOwnerError, match="filename is unsafe"):
        write_owned_artifact(artifact_root, USER_A, filename, b"untrusted")


def test_artifact_helpers_reject_absolute_names(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")

    assert resolve_owned_artifact(artifact_root, USER_A, str(outside)) is None
    with pytest.raises(ArtifactOwnerError, match="filename is unsafe"):
        write_owned_artifact(artifact_root, USER_A, str(outside), b"untrusted")
    assert outside.read_bytes() == b"outside"


def test_artifact_resolver_rejects_symlink_leaf(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    owner_dir = user_artifact_directory(artifact_root, USER_A, create=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (owner_dir / "linked.bin").symlink_to(outside)

    assert resolve_owned_artifact(artifact_root, USER_A, "linked.bin") is None


def test_default_tool_registry_propagates_authenticated_owner() -> None:
    registry = create_default_registry(user_id=USER_A)

    for tool_name in ("spawn_agent", "spawn_multiple", "generate_document"):
        tool = registry.get(tool_name)
        assert tool is not None
        assert getattr(tool, "_user_id") == USER_A


class _FakeAudioResponse:
    status_code = 200
    text = ""

    def __init__(self, content: bytes) -> None:
        self.content = content


@pytest.mark.asyncio
async def test_tts_cache_isolated_per_user(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, current_owner = switching_owner_client
    provider_calls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            pass

        async def post(self, url: str, **_kwargs: Any) -> _FakeAudioResponse:
            provider_calls.append(url)
            return _FakeAudioResponse(f"tts-{len(provider_calls)}".encode())

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = get_settings()

    async def override_settings():
        return settings

    app.dependency_overrides[main_module.get_settings] = override_settings
    try:
        current_owner["user_id"] = USER_A
        first = await client.post("/tts", json={"text": "same text"})
        cached = await client.post("/tts", json={"text": "same text"})
        current_owner["user_id"] = USER_B
        second_owner = await client.post("/tts", json={"text": "same text"})
    finally:
        app.dependency_overrides.pop(main_module.get_settings, None)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert second_owner.status_code == 200
    assert second_owner.json()["cached"] is False
    assert len(provider_calls) == 2

    filename = first.json()["audio_path"].rsplit("/", maxsplit=1)[-1]
    assert second_owner.json()["audio_path"].endswith(filename)
    user_a_file = (
        user_artifact_directory(artifact_roots["TTS_CACHE_DIR"], USER_A, create=False) / filename
    )
    user_b_file = (
        user_artifact_directory(artifact_roots["TTS_CACHE_DIR"], USER_B, create=False) / filename
    )
    assert user_a_file.read_bytes() == b"tts-1"
    assert user_b_file.read_bytes() == b"tts-2"


@pytest.mark.asyncio
async def test_sound_effect_cache_isolated_per_user(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = artifact_roots
    client, current_owner = switching_owner_client
    provider_calls = 0

    class FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            pass

        async def post(self, _url: str, **_kwargs: Any) -> _FakeAudioResponse:
            nonlocal provider_calls
            provider_calls += 1
            return _FakeAudioResponse(f"sfx-{provider_calls}".encode())

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    current_owner["user_id"] = USER_A
    first = await client.post(
        "/sound-effects",
        data={"text": "same effect", "duration_seconds": "2"},
    )
    cached = await client.post(
        "/sound-effects",
        data={"text": "same effect", "duration_seconds": "2"},
    )
    current_owner["user_id"] = USER_B
    second_owner = await client.post(
        "/sound-effects",
        data={"text": "same effect", "duration_seconds": "2"},
    )

    assert first.status_code == 200
    assert first.content == b"sfx-1"
    assert cached.status_code == 200
    assert cached.content == b"sfx-1"
    assert second_owner.status_code == 200
    assert second_owner.content == b"sfx-2"
    assert provider_calls == 2


@pytest.mark.asyncio
async def test_sound_effect_cache_write_replaces_symlink_without_following_it(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, current_owner = switching_owner_client

    class FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            pass

        async def post(self, _url: str, **_kwargs: Any) -> _FakeAudioResponse:
            return _FakeAudioResponse(b"safe-audio")

    text = "symlink-safe effect"
    duration = 2.0
    cache_key = hashlib.sha256(f"{text}|{duration}".encode()).hexdigest()
    filename = f"{cache_key}.mp3"
    owner_dir = user_artifact_directory(artifact_roots["TTS_CACHE_DIR"], USER_A, create=True)
    outside_file = tmp_path / "outside-audio.mp3"
    outside_file.write_bytes(b"do-not-overwrite")
    (owner_dir / filename).symlink_to(outside_file)

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    current_owner["user_id"] = USER_A

    response = await client.post(
        "/sound-effects",
        data={"text": text, "duration_seconds": str(duration)},
    )

    assert response.status_code == 200
    assert response.content == b"safe-audio"
    assert outside_file.read_bytes() == b"do-not-overwrite"
    assert not (owner_dir / filename).is_symlink()
    assert (owner_dir / filename).read_bytes() == b"safe-audio"
