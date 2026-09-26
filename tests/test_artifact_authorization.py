from __future__ import annotations

from collections.abc import AsyncIterator
import hashlib
from pathlib import Path
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


@pytest.mark.asyncio
async def test_cached_tts_is_denied_even_to_its_owner(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
) -> None:
    client, current_owner = switching_owner_client
    text = "same text"
    cache_key = hashlib.sha256(
        f"eleven_flash_v2_5|Xb7hH8MSUJpSbSDYk0k2|1.0|mp3|{text}".encode()
    ).hexdigest()
    filename = f"{cache_key}.mp3"
    owner_file = (
        user_artifact_directory(artifact_roots["TTS_CACHE_DIR"], USER_A, create=True) / filename
    )
    owner_file.write_bytes(b"legacy cached audio")

    for owner in (USER_A, USER_B):
        current_owner["user_id"] = owner
        response = await client.post("/tts", json={"text": text})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "route_unavailable"
    assert owner_file.read_bytes() == b"legacy cached audio"
    assert list(artifact_roots["TTS_CACHE_DIR"].iterdir()) == [owner_file.parent]


@pytest.mark.asyncio
async def test_sound_effects_denied_for_both_owners_without_cache_write(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
) -> None:
    client, current_owner = switching_owner_client
    for owner in (USER_A, USER_B):
        current_owner["user_id"] = owner
        response = await client.post(
            "/sound-effects", data={"text": "same effect", "duration_seconds": "2"}
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "route_unavailable"
    assert not artifact_roots["TTS_CACHE_DIR"].exists()


@pytest.mark.asyncio
async def test_sound_effect_denial_does_not_follow_cached_symlink(
    artifact_roots: dict[str, Path],
    switching_owner_client: tuple[AsyncClient, dict[str, uuid.UUID]],
    tmp_path: Path,
) -> None:
    client, current_owner = switching_owner_client
    text = "symlink-safe effect"
    cache_key = hashlib.sha256(f"{text}|2.0".encode()).hexdigest()
    filename = f"{cache_key}.mp3"
    owner_dir = user_artifact_directory(artifact_roots["TTS_CACHE_DIR"], USER_A, create=True)
    outside_file = tmp_path / "outside-audio.mp3"
    outside_file.write_bytes(b"do-not-overwrite")
    (owner_dir / filename).symlink_to(outside_file)
    current_owner["user_id"] = USER_A

    response = await client.post("/sound-effects", data={"text": text, "duration_seconds": "2.0"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "route_unavailable"
    assert outside_file.read_bytes() == b"do-not-overwrite"
    assert (owner_dir / filename).is_symlink()
