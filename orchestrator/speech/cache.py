"""Bounded, owner-scoped cache for NEW speech only; legacy artifacts untouched."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import time
from collections.abc import Iterator

from orchestrator.artifacts import (
    is_artifact_owner_namespace,
    resolve_owned_artifact,
    write_owned_artifact,
)
from orchestrator.speech.contracts import SpeechRequest

CACHE_BYTES = 64 * 1024 * 1024
CACHE_FILES = 128
CACHE_TTL_SECONDS = 3600


def audio_filename(provider: str, model: str, request: SpeechRequest) -> str:
    # Contract version invalidates old rendering/encoding behavior.
    values = (
        "speech-v1",
        provider,
        model,
        request.voice,
        float(request.speed),
        request.format,
        request.text,
    )
    return hashlib.sha256(repr(values).encode()).hexdigest() + "." + request.format


@contextmanager
def cache_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def store_audio(root: Path, owner: object, filename: str, content: bytes) -> None:
    if len(content) > CACHE_BYTES:
        raise ValueError("audio exceeds cache capacity")
    with cache_lock(root):
        files: list[tuple[float, int, Path]] = []
        now = time.time()
        for directory in root.iterdir():
            if directory.is_symlink() or not is_artifact_owner_namespace(directory.name):
                continue
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                if path.suffix not in (".mp3", ".opus", ".wav"):
                    continue
                stat = path.stat()
                if now - stat.st_mtime > CACHE_TTL_SECONDS:
                    path.unlink()
                else:
                    files.append((stat.st_mtime, stat.st_size, path))
        total = sum(item[1] for item in files)
        files.sort()
        while files and (total + len(content) > CACHE_BYTES or len(files) >= CACHE_FILES):
            _, size, path = files.pop(0)
            path.unlink()
            total -= size
        write_owned_artifact(root, str(owner), filename, content)


def cached_audio(root: Path, owner: object, filename: str) -> Path | None:
    try:
        path = resolve_owned_artifact(root, str(owner), filename)
        if path is not None and time.time() - path.stat().st_mtime <= CACHE_TTL_SECONDS:
            return path
    except OSError:
        # A concurrent bounded-cache eviction is a miss, not a server error.
        pass
    return None
