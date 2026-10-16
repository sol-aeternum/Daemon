"""Run-owned input and non-extracting filesystem readback checks.

Adapted from #431's ConfigSnapshot/require_config_archive. The host UID and
daemon are trusted; this is not protection against adversarial same-UID/root.
Filesystem readback does not attest the daemon's parsed/effective configuration.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import stat
import tarfile

MAX_INPUT = 16 * 1024
MAX_ARCHIVE = 64 * 1024
FILENAME = "config.yml"
# Exact unmodified deploy/docker/config.yml at upstream commit 838869bdda93f25e6d50b3f50ae9577d9b19a586.
STOCK_CONFIG_SHA256 = "bb332d560b613aec9084bf752e886b070dba33b080f74f2b8d2701258b1f4cfc"


class Refused(ValueError):
    """Fixed-message refusal; never include input, credentials or service bodies."""


def require(condition: bool) -> None:
    if not condition:
        raise Refused("Crawl4AI qualification refused")


def stock_config(content: bytes) -> bytes:
    """Provenance check only: the stock defaults are NOT an approved runtime profile."""
    require(type(content) is bytes and 0 < len(content) <= MAX_INPUT)
    require(hashlib.sha256(content).hexdigest() == STOCK_CONFIG_SHA256)
    return content


def _directory(path: str) -> int:
    require(type(path) is str and os.path.isabs(path) and os.path.normpath(path) == path)
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
    """Generic byte staging, NOT config approval; the lifecycle calls stock_config.

    Call cleanup only after authoritative allocation absence. On substitution it
    refuses and retains descriptors/input for reconciliation rather than deleting
    a replacement. Partial creation also remains owned by this object.
    """

    def __init__(self, parent: str, content: bytes) -> None:
        require(type(content) is bytes and 0 < len(content) <= MAX_INPUT)
        self.parent, self.content = parent, content
        self.name = "daemon-c4ai-input-" + secrets.token_hex(16)
        self.path = os.path.join(parent, self.name, FILENAME)
        self._parent: int | None = None
        self._directory: int | None = None
        self._file: int | None = None
        self._made = False
        self._file_made = False

    def create(self) -> None:
        require(self._parent is None)
        self._parent = _directory(self.parent)
        info = os.fstat(self._parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
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
            require(written > 0)
            offset += written
        os.fchmod(self._file, 0o444)
        os.fsync(self._file)
        self.verify()

    def _verify_directory(self) -> None:
        require(self._parent is not None and self._directory is not None)
        assert self._parent is not None and self._directory is not None
        current = _directory(self.parent)
        try:
            info = os.fstat(current)
            require(_identity(info) == _identity(os.fstat(self._parent)))
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        finally:
            os.close(current)
        info = os.stat(self.name, dir_fd=self._parent, follow_symlinks=False)
        require(_identity(info) == _identity(os.fstat(self._directory)))
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)

    def verify(self) -> None:
        self._verify_directory()
        assert self._directory is not None and self._file is not None
        info = os.stat(FILENAME, dir_fd=self._directory, follow_symlinks=False)
        require(_identity(info) == _identity(os.fstat(self._file)))
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o444)
        require(info.st_size == len(self.content))
        require(os.pread(self._file, len(self.content) + 1, 0) == self.content)

    def cleanup(self) -> None:
        if self._made:
            self._verify_directory()
            assert self._directory is not None and self._parent is not None
            os.lseek(self._directory, 0, os.SEEK_SET)
            require(os.listdir(self._directory) == ([FILENAME] if self._file_made else []))
            if self._file_made:
                assert self._file is not None
                info = os.stat(FILENAME, dir_fd=self._directory, follow_symlinks=False)
                require(_identity(info) == _identity(os.fstat(self._file)))
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
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
    """Exactly one bounded regular config.yml; never extract daemon-supplied paths."""
    require(type(expected) is bytes and 0 < len(expected) <= MAX_INPUT)
    require(type(archive) is bytes and 0 < len(archive) <= MAX_ARCHIVE and len(archive) % 512 == 0)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            members = stream.getmembers()
            require(len(members) == 1)
            member = members[0]
            end = 512 + ((len(expected) + 511) // 512) * 512
            require(
                member.name == FILENAME
                and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                and not member.linkname
                and not member.pax_headers
                and member.offset_data == 512
                and member.size == len(expected)
                and archive[:100].rstrip(b"\0") == FILENAME.encode("ascii")
                and len(archive) >= end + 1024
                and not any(archive[end:])
            )
            source = stream.extractfile(member)
            require(source is not None and source.read(len(expected) + 1) == expected)
    except (tarfile.TarError, OSError, ValueError):
        raise Refused("Crawl4AI qualification refused") from None
