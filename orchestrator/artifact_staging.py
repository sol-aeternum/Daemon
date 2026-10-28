"""Uncalled POSIX staging mechanics, not intent admission or publication authority.

Only cooperating store code may mutate the injected root. No namespace creation,
repair, deletion, public reads or runtime composition is provided. See the S1
contract for the future journal, registry, retirement and handle-drain prerequisites.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - supported deployment/test platform is POSIX
    fcntl = None

from orchestrator.artifact_envelope import (
    MAX_TOKEN_BYTES,
    ArtifactBinding,
    ArtifactEnvelopeCodec,
    ArtifactEnvelopeError,
)

_OPAQUE_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOCK = ".prepare.lock"
StageCode = Literal[
    "invalid_input", "unsupported", "unavailable", "busy", "needs_reconciliation", "conflict"
]


class ArtifactStageError(ValueError):
    """Closed, content-free outcomes; no filesystem exception or path disclosure."""

    def __init__(self, code: StageCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class StagedArtifact:
    """Uncommitted observations only; forgeable data, never a publication receipt."""

    namespace_id: str
    intent_id: str
    binding: ArtifactBinding
    encrypted_size: int
    outcome: Literal["prepared_uncommitted", "duplicate_uncommitted"]


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _directory(info: os.stat_result, device: int | None = None) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or (device is not None and info.st_dev != device)
    ):
        raise ArtifactStageError("unavailable")


def _file(info: os.stat_result, links: int, device: int) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_dev != device
        or info.st_nlink != links
        or not 0 <= info.st_size <= MAX_TOKEN_BYTES
    ):
        raise ArtifactStageError("needs_reconciliation")


class _Handles:
    """Own every fd, including on close failure; never retry an ambiguous close."""

    def __init__(self) -> None:
        self.fds: list[int] = []
        self.edges: list[tuple[int, str, int, bool]] = []
        self.device: int | None = None

    def __enter__(self) -> _Handles:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        failed = False
        for fd in reversed(self.fds.copy()):
            try:
                self.close(fd)
            except OSError:
                failed = True
        if failed and kind is None:
            raise OSError("close_failed")

    def open(self, name: str, flags: int, parent: int | None = None) -> int:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        self.fds.append(fd)
        return fd

    def close(self, fd: int) -> None:
        self.fds.remove(fd)
        os.close(fd)

    def directory(self, parent: int, name: str, private: bool = True) -> int:
        fd = self.open(name, os.O_RDONLY | os.O_DIRECTORY, parent)
        if private:
            _directory(os.fstat(fd), self.device)
        self.edges.append((parent, name, fd, private))
        return fd

    def root(self, path: Path) -> int:
        fd = self.open("/", os.O_RDONLY | os.O_DIRECTORY)
        # Ancestors are operator-trusted; refuse symlinks but do not require that
        # system directories such as / are owned by the application or mode 0700.
        for part in path.parts[1:]:
            fd = self.directory(fd, part, private=False)
        _directory(os.fstat(fd))
        self.device = os.fstat(fd).st_dev
        return fd

    def revalidate(self, root: int, private: int, lock: int) -> None:
        for parent, name, fd, restricted in self.edges:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(os.fstat(fd)):
                raise ArtifactStageError("needs_reconciliation")
            if restricted:
                _directory(current, self.device)
        _directory(os.fstat(root), self.device)
        current_lock = os.stat(_LOCK, dir_fd=private, follow_symlinks=False)
        _file(current_lock, 1, os.fstat(private).st_dev)
        if current_lock.st_size or _identity(current_lock) != _identity(os.fstat(lock)):
            raise ArtifactStageError("needs_reconciliation")


def _exists(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _write_all(fd: int, token: bytes) -> None:
    remaining = memoryview(token)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0 or written > len(remaining):
            raise OSError("short_write")
        remaining = remaining[written:]


def _verify_pair(
    handles: _Handles,
    staging: int,
    private: int,
    intent: str,
    codec: ArtifactEnvelopeCodec,
    binding: ArtifactBinding,
    content: bytes,
    created: tuple[int, int] | None = None,
) -> int:
    stage = os.stat(intent, dir_fd=staging, follow_symlinks=False)
    fd = handles.open(intent, os.O_RDONLY | os.O_NONBLOCK, private)
    try:
        saved = os.fstat(fd)
        device = os.fstat(staging).st_dev
        _file(stage, 2, device)
        _file(saved, 2, device)
        if _identity(stage) != _identity(saved) or (
            created is not None and _identity(saved) != created
        ):
            raise ArtifactStageError("needs_reconciliation")
        chunks: list[bytes] = []
        remaining = saved.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise ArtifactStageError("needs_reconciliation")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ArtifactStageError("needs_reconciliation")
        try:
            decoded = codec.decode(binding, b"".join(chunks))
        except ArtifactEnvelopeError:
            raise ArtifactStageError("needs_reconciliation") from None
        if decoded != content:
            raise ArtifactStageError("conflict")
        # A path substitution or added hardlink invalidates the observation.
        for parent in (staging, private):
            current = os.stat(intent, dir_fd=parent, follow_symlinks=False)
            _file(current, 2, device)
            if _identity(current) != _identity(saved) or current.st_size != saved.st_size:
                raise ArtifactStageError("needs_reconciliation")
        return saved.st_size
    finally:
        handles.close(fd)


def prepare_artifact(
    root: Path,
    codec: ArtifactEnvelopeCodec,
    binding: ArtifactBinding,
    namespace_id: str,
    intent_id: str,
    content: bytes,
) -> StagedArtifact:
    """Prepare one encrypted stage/private link under a preprovisioned namespace.

    Immediate nonblocking lock admission; caller must not retry uncertain effects.
    No dirs/locks are created here. Future namespace retirement MUST coordinate
    on the same private lock; identity checks alone are not a filesystem fence.
    """
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or ".." in root.parts
        or len(root.parts) > 128
        or len(str(root)) > 4096
        or type(namespace_id) is not str
        or _OPAQUE_ID.fullmatch(namespace_id) is None
        or type(intent_id) is not str
        or _OPAQUE_ID.fullmatch(intent_id) is None
    ):
        raise ArtifactStageError("invalid_input")
    if fcntl is None or any(
        not hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")
    ):
        raise ArtifactStageError("unsupported")
    try:
        token = codec.encode(binding, content)
    except ArtifactEnvelopeError:
        raise ArtifactStageError("invalid_input") from None
    effects_possible = False
    try:
        with _Handles() as handles:
            base = handles.root(root)
            live = handles.directory(base, "live")
            namespace = handles.directory(live, namespace_id)
            staging = handles.directory(namespace, "staging")
            private_root = handles.directory(base, "private")
            private = handles.directory(private_root, namespace_id)
            lock = handles.open(_LOCK, os.O_RDWR | os.O_NONBLOCK, private)
            _file(os.fstat(lock), 1, os.fstat(private).st_dev)
            if os.fstat(lock).st_size:
                raise ArtifactStageError("needs_reconciliation")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ArtifactStageError("busy") from None
            handles.revalidate(base, private, lock)
            stage_exists, private_exists = _exists(staging, intent_id), _exists(private, intent_id)
            if stage_exists or private_exists:
                if not (stage_exists and private_exists):
                    raise ArtifactStageError("needs_reconciliation")
                size = _verify_pair(handles, staging, private, intent_id, codec, binding, content)
                handles.revalidate(base, private, lock)
                return StagedArtifact(
                    namespace_id, intent_id, binding, size, "duplicate_uncommitted"
                )
            # Set before create: an interrupted syscall can have an uncertain outcome.
            effects_possible = True
            fd = handles.open(intent_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL, staging)
            _file(os.fstat(fd), 1, os.fstat(staging).st_dev)
            created = _identity(os.fstat(fd))
            _write_all(fd, token)
            os.fsync(fd)
            handles.close(fd)
            os.fsync(staging)
            handles.revalidate(base, private, lock)
            source = os.stat(intent_id, dir_fd=staging, follow_symlinks=False)
            _file(source, 1, os.fstat(staging).st_dev)
            if _identity(source) != created or source.st_size != len(token):
                raise ArtifactStageError("needs_reconciliation")
            os.link(
                intent_id, intent_id, src_dir_fd=staging, dst_dir_fd=private, follow_symlinks=False
            )
            size = _verify_pair(
                handles, staging, private, intent_id, codec, binding, content, created
            )
            os.fsync(private)
            handles.revalidate(base, private, lock)
            return StagedArtifact(namespace_id, intent_id, binding, size, "prepared_uncommitted")
    except OSError:
        raise ArtifactStageError(
            "needs_reconciliation" if effects_possible else "unavailable"
        ) from None
    except ArtifactStageError as error:
        if effects_possible and error.code != "conflict":
            raise ArtifactStageError("needs_reconciliation") from None
        raise
