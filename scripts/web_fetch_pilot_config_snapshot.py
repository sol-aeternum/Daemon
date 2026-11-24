"""Run-owned Crawl4AI config snapshot and bounded, non-extracting archive check.

The host user and local Docker daemon are trusted. Restrictive modes and inode
checks prevent accidental substitution; they are not a sandbox against root or
another adversarial process with the same UID. No I/O happens on import.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import stat
import tarfile

from scripts.web_fetch_pilot_container_policy import C4AI_SANDBOXED_CONFIG_SHA256, PreflightError

MAX_ARCHIVE = 64 * 1024  # Also bounded by CommandExecutor's stdout capture.
FILENAME = "config.yml"


def _refuse() -> PreflightError:
    return PreflightError("pilot config snapshot refused")


def _directory(path: str) -> int:
    """Walk every component without following symlinks; caller owns returned fd."""
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise _refuse()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.split("/")[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_uid


class ConfigSnapshot:
    """Owned by one allocation, retained until its container is verified absent."""

    def __init__(self, parent: str, content: bytes) -> None:
        if (
            type(content) is not bytes
            or hashlib.sha256(content).hexdigest() != C4AI_SANDBOXED_CONFIG_SHA256
        ):
            raise _refuse()
        self.parent = parent
        self.content = content
        self.name = "daemon-c4ai-input-" + secrets.token_hex(16)
        self.path = os.path.join(parent, self.name, FILENAME)
        self._parent: int | None = None
        self._directory: int | None = None
        self._file: int | None = None
        self._made = False
        self._file_made = False

    def create(self) -> None:
        self._parent = _directory(self.parent)
        info = os.fstat(self._parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise _refuse()
        os.mkdir(self.name, 0o700, dir_fd=self._parent)
        self._made = True
        self._directory = os.open(
            self.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self._parent,
        )
        self._file = os.open(
            FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._directory,
        )
        self._file_made = True
        offset = 0
        while offset < len(self.content):
            written = os.write(self._file, self.content[offset:])
            if written <= 0:
                raise _refuse()
            offset += written
        os.fchmod(self._file, 0o444)  # appuser must read it; host directory remains 0700.
        os.fsync(self._file)
        self.verify()

    def _verify_directory(self) -> None:
        if self._parent is None or self._directory is None:
            raise _refuse()
        current = _directory(self.parent)
        try:
            info = os.fstat(current)
            if (
                _identity(info) != _identity(os.fstat(self._parent))
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise _refuse()
        finally:
            os.close(current)
        info = os.stat(self.name, dir_fd=self._parent, follow_symlinks=False)
        if (
            _identity(info) != _identity(os.fstat(self._directory))
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
        ):
            raise _refuse()

    def verify(self) -> None:
        self._verify_directory()
        assert self._directory is not None and self._file is not None
        info = os.stat(FILENAME, dir_fd=self._directory, follow_symlinks=False)
        if (
            _identity(info) != _identity(os.fstat(self._file))
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_size != len(self.content)
            or os.pread(self._file, len(self.content) + 1, 0) != self.content
        ):
            raise _refuse()

    def cleanup(self) -> None:
        """Never delete a replacement inode, unexpected entry or uncertain input."""
        if self._made:
            self._verify_directory()
            assert self._directory is not None and self._parent is not None
            expected = [FILENAME] if self._file_made else []
            os.lseek(self._directory, 0, os.SEEK_SET)
            if os.listdir(self._directory) != expected:
                raise _refuse()
            if self._file_made:
                # Content drift is a qualification failure, not foreign ownership.
                assert self._file is not None
                info = os.stat(FILENAME, dir_fd=self._directory, follow_symlinks=False)
                if _identity(info) != _identity(os.fstat(self._file)) or not stat.S_ISREG(
                    info.st_mode
                ):
                    raise _refuse()
                os.unlink(FILENAME, dir_fd=self._directory)
                self._file_made = False
            os.rmdir(self.name, dir_fd=self._parent)
            self._made = False
        for field in ("_file", "_directory", "_parent"):
            fd = getattr(self, field)
            if fd is not None:
                os.close(fd)
                setattr(self, field, None)


def require_config_archive(archive: bytes, expected: bytes) -> None:
    """Verify exactly one normal config.yml, without extracting daemon paths.

    Docker cp FROM a created container uses its mount-aware filesystem view;
    export does not. Reject extended headers, links, extra entries and missing
    end markers, even if a permissive tar reader would accept them.
    """
    if not archive or len(archive) > MAX_ARCHIVE or len(archive) % 512:
        raise _refuse()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            members = stream.getmembers()
            if len(members) != 1:
                raise _refuse()
            member = members[0]
            end = 512 + ((len(expected) + 511) // 512) * 512
            if (
                member.name != FILENAME
                or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                or member.linkname
                or member.pax_headers
                or member.offset_data != 512
                or member.size != len(expected)
                or archive[:100].rstrip(b"\0") != FILENAME.encode("ascii")
                or len(archive) < end + 1024
                or any(archive[end:])
            ):
                raise _refuse()
            source = stream.extractfile(member)
            if source is None or source.read(len(expected) + 1) != expected:
                raise _refuse()
    except (tarfile.TarError, OSError, ValueError):
        raise _refuse() from None
