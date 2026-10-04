"""Bounded, owner-scoped cache for NEW speech only; legacy artifacts untouched."""

from contextlib import contextmanager
import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from orchestrator.speech.contracts import SpeechRequest

CACHE_BYTES = 64 * 1024 * 1024
CACHE_FILES = 128
CACHE_TTL_SECONDS = 3600

_T = TypeVar("_T")
_io_tails: set[asyncio.Task[Any]] = set()


async def run_cache_io(work: Callable[..., _T], *args: Any) -> _T:
    """Keep synchronous cache locks off-loop and own cancelled work until exit.

    Only use for operations returning values, not live file descriptors. The
    synchronous operation owns every descriptor/lock through its own finally.
    """
    task = asyncio.create_task(asyncio.to_thread(work, *args))
    _io_tails.add(task)

    def finished(done: asyncio.Task[Any]) -> None:
        _io_tails.discard(done)
        if not done.cancelled():
            done.exception()  # Consume failure even after the HTTP waiter retires.

    task.add_done_callback(finished)
    # wait never propagates waiter cancellation into the owned task. Unlike
    # shield, it also does not report a late failure from a retired waiter on
    # Python 3.14; the explicit completion callback above consumes that failure.
    await asyncio.wait({task})
    return task.result()


def audio_filename(
    provider: str, model: str, request: SpeechRequest, *, rendering: str = "speech-v1"
) -> str:
    # Contract version invalidates old rendering/encoding behavior.
    values = (
        rendering,
        provider,
        model,
        request.voice,
        float(request.speed),
        request.format,
        request.text,
    )
    return hashlib.sha256(repr(values).encode()).hexdigest() + "." + request.format


@contextmanager
def cache_lock(root: Path) -> Iterator[int]:
    # Lazy import keeps the old API while sharing the progressive secure dirfd walk.
    from orchestrator.speech.stream_cache import _inode, _open_directory, _regular

    directory = _open_directory(root, create=True)
    descriptor = None
    try:
        descriptor = os.open(
            ".lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        _regular(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = os.stat(".lock", dir_fd=directory, follow_symlinks=False)
        _regular(current)
        if _inode(os.fstat(descriptor)) != _inode(current):
            raise OSError("speech cache lock changed")
        current_directory = _open_directory(root)
        try:
            if _inode(os.fstat(current_directory)) != _inode(os.fstat(directory)):
                raise OSError("speech cache root changed")
        finally:
            os.close(current_directory)
        yield directory
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def store_audio(root: Path, owner: object, filename: str, content: bytes) -> None:
    if len(content) > CACHE_BYTES:
        raise ValueError("audio exceeds cache capacity")
    from orchestrator.speech.stream_cache import _store_buffered

    try:
        _store_buffered(root, owner, filename, content)
    except OSError:
        from orchestrator.speech.stream_cache import _warn_cache_unavailable

        _warn_cache_unavailable()
        raise


def cached_audio(root: Path, owner: object, filename: str) -> Path | None:
    from orchestrator.speech.stream_cache import _cached_buffered_path

    return _cached_buffered_path(root, owner, filename)
