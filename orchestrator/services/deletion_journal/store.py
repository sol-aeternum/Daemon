"""Private, single-host POSIX journal. Advisory locks require cooperating writers.

The log is authoritative intent, not proof of a database or external effect.
Interrupted metadata and partial frames are preserved, never truncated/repaired.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Protocol, Sequence, cast
from uuid import UUID

from .errors import JournalError
from .model import Command, Entry, JournalState, command_digest, decode_entry, encode_entry

MAX_FRAME = 64 * 1024
LOCK_WAIT_S = 5.0
_FILES = {"journal.meta", ".journal.lock", "entries.log", "HEAD"}
_TEMP = ".HEAD.pending"
_ZERO = bytes(32)


class _Digested(Protocol):
    @property
    def digest(self) -> bytes: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("corrupt")
        result[key] = value
    return result


def _json(data: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(data, object_pairs_hook=_unique_pairs)
        if not isinstance(value, dict) or _canonical(value) != data:
            raise JournalError("corrupt")
        return cast(dict[str, object], value)
    except (ValueError, TypeError, UnicodeError):
        raise JournalError("corrupt") from None


def _safe_file(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise JournalError("corrupt")
    return info


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short_write")
        remaining = remaining[written:]


def _read_exact(fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise JournalError("corrupt")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class Journal:
    """A pinned directory; no persistent process-local authoritative index."""

    def __init__(self, directory: Path, journal_id: UUID) -> None:
        self.path = directory
        self.journal_id = journal_id
        self._dir = -1
        self._lock = -1
        self._local_lock = threading.Lock()
        try:
            if (
                not isinstance(directory, Path)
                or not directory.is_absolute()
                or not isinstance(journal_id, UUID)
            ):
                raise JournalError("invalid_config")
            self._dir = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._dir)
            if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                raise JournalError("corrupt")
            self._identity = (info.st_dev, info.st_ino)
            self._lock = self._open(".journal.lock", os.O_RDWR)
            lock_info = _safe_file(self._lock)
            if lock_info.st_size:
                raise JournalError("corrupt")
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
            self._log_identity: tuple[int, int] | None = None
            self._meta_identity: tuple[int, int] | None = None
        except JournalError:
            self.close()
            raise
        except OSError:
            self.close()
            raise JournalError("unavailable") from None

    def close(self) -> None:
        for name in ("_lock", "_dir"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _open(self, name: str, flags: int) -> int:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._dir)
        try:
            _safe_file(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _check_identity(self) -> None:
        info = os.stat(self.path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != self._identity
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise JournalError("corrupt")
        lock = os.stat(".journal.lock", dir_fd=self._dir, follow_symlinks=False)
        if (lock.st_dev, lock.st_ino) != self._lock_identity:
            raise JournalError("corrupt")
        _safe_file(self._lock)
        names = self._names()
        if not _FILES <= names or names - (_FILES | {_TEMP}):
            raise JournalError("corrupt")

    def _names(self) -> set[str]:
        # A reused directory open description can retain stale enumeration
        # state across rename, even when listdir restores its offset to zero.
        # Open relative to the pinned inode, not the replaceable pathname;
        # dup() would share the same open description and is insufficient.
        fd = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self._dir,
        )
        try:
            return set(os.listdir(fd))
        finally:
            os.close(fd)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        deadline = time.monotonic() + LOCK_WAIT_S
        locked = False
        local = self._local_lock.acquire(timeout=LOCK_WAIT_S)
        try:
            if not local:
                raise JournalError("unavailable")
            while not locked:
                try:
                    fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise JournalError("unavailable") from None
                    time.sleep(0.01)
            self._check_identity()
            yield
        except OSError:
            raise JournalError("unavailable") from None
        finally:
            if locked:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
            if local:
                self._local_lock.release()

    def _small(self, name: str) -> dict[str, object]:
        fd = self._open(name, os.O_RDONLY)
        try:
            info = os.fstat(fd)
            if info.st_size > 1024:
                raise JournalError("corrupt")
            if name == "journal.meta":
                identity = (info.st_dev, info.st_ino)
                if self._meta_identity is not None and identity != self._meta_identity:
                    raise JournalError("corrupt")
                self._meta_identity = identity
            return _json(_read_exact(fd, info.st_size))
        finally:
            os.close(fd)

    def _head(self, value: dict[str, object], state: JournalState) -> int:
        return self._head_entries(value, state.entries)

    def _head_entries(self, value: dict[str, object], entries: Sequence[_Digested]) -> int:
        if set(value) != {"journal_id", "seq", "digest"}:
            raise JournalError("corrupt")
        seq = value["seq"]
        if value["journal_id"] != str(self.journal_id) or type(seq) is not int:
            raise JournalError("corrupt")
        index = cast(int, seq)
        if index < 0 or index > len(entries):
            raise JournalError("corrupt")
        digest = entries[index - 1].digest if index else _ZERO
        if value["digest"] != digest.hex():
            raise JournalError("corrupt")
        return index

    def _publish_head(self, seq: int, digest: bytes) -> None:
        fd = os.open(
            _TEMP,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._dir,
        )
        try:
            _write_all(
                fd,
                _canonical(
                    {"journal_id": str(self.journal_id), "seq": seq, "digest": digest.hex()}
                ),
            )
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(_TEMP, "HEAD", src_dir_fd=self._dir, dst_dir_fd=self._dir)
        os.fsync(self._dir)

    def _scan(self) -> JournalState:
        meta = self._small("journal.meta")
        if type(meta.get("version")) is not int or meta != {
            "version": 1,
            "journal_id": str(self.journal_id),
        }:
            raise JournalError("corrupt")
        state = JournalState()
        with self._scan_log() as fd:
            for payload, digest in self._frames(fd):
                try:
                    entry = decode_entry(payload, digest)
                    if entry.journal_id != self.journal_id:
                        raise JournalError("corrupt")
                    state.accept(entry)
                except JournalError:
                    raise JournalError("corrupt") from None
            self._finish_scan(fd, state.entries)
            return state

    @contextmanager
    def _scan_log(self) -> Iterator[int]:
        """Owned, pinned log descriptor; caller holds the persistent lock."""
        fd = self._open("entries.log", os.O_RDWR)
        try:
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            if self._log_identity is not None and identity != self._log_identity:
                raise JournalError("corrupt")
            self._log_identity = identity
            yield fd
        finally:
            os.close(fd)

    def _frames(self, fd: int) -> Iterator[tuple[bytes, bytes]]:
        """Bounded authenticated framing only; closed decoding stays with caller."""
        while header := os.read(fd, 4):
            if len(header) != 4:
                header += _read_exact(fd, 4 - len(header))
            size = struct.unpack(">I", header)[0]
            if size == 0 or size + 36 > MAX_FRAME:
                raise JournalError("corrupt")
            payload = _read_exact(fd, size)
            digest = _read_exact(fd, 32)
            if hashlib.sha256(header + payload).digest() != digest:
                raise JournalError("corrupt")
            yield payload, digest

    def _finish_scan(self, fd: int, entries: Sequence[_Digested]) -> None:
        head = self._head_entries(self._small("HEAD"), entries)
        pending = _TEMP in self._names()
        if pending:
            if self._head_entries(self._small(_TEMP), entries) < head:
                raise JournalError("corrupt")
            # Sync authoritative bytes before removing valid derived metadata.
            os.fsync(fd)
            os.unlink(_TEMP, dir_fd=self._dir)
            os.fsync(self._dir)
        if head != len(entries) or pending:
            os.fsync(fd)
            self._publish_head(len(entries), entries[-1].digest if entries else _ZERO)
        # Duplicate retries also reconfirm durability after uncertain syncs.
        os.fsync(fd)
        head_fd = self._open("HEAD", os.O_RDONLY)
        try:
            os.fsync(head_fd)
        finally:
            os.close(head_fd)
        os.fsync(self._dir)

    def verify(self) -> JournalState:
        with self._locked():
            return self._scan()

    def append(
        self,
        command: Command,
        event_id: UUID,
        expected_account_seq: int | None,
        expected_operation_seq: int | None,
    ) -> Entry:
        if not isinstance(command, Command) or not isinstance(event_id, UUID):
            raise JournalError("invalid_entry")
        with self._locked():
            state = self._scan()
            digest = command_digest(command)
            if event_id in state.events:
                earlier = state.events[event_id]
                if earlier.command_digest != digest:
                    raise JournalError("conflict")
                return earlier
            entry = Entry(
                self.journal_id,
                len(state.entries) + 1,
                event_id,
                command,
                expected_account_seq,
                expected_operation_seq,
                state.entries[-1].digest if state.entries else _ZERO,
                digest,
                _ZERO,
            )
            payload = encode_entry(entry)
            header = struct.pack(">I", len(payload))
            if len(payload) + 36 > MAX_FRAME:
                raise JournalError("invalid_entry")
            entry = replace(entry, digest=hashlib.sha256(header + payload).digest())
            state.accept(entry)  # All conflicts/invalid fields before any write.
            fd = self._open("entries.log", os.O_WRONLY | os.O_APPEND)
            try:
                try:
                    _write_all(fd, header + payload + entry.digest)
                    os.fsync(fd)
                    self._publish_head(entry.seq, entry.digest)
                except OSError:
                    raise JournalError("unknown_append") from None
            finally:
                os.close(fd)
            return entry


def initialize(directory: Path, journal_id: UUID) -> None:
    """Create NEW storage only, offline. Interrupted creation is never removed."""
    parent = -1
    target = -1
    try:
        if not directory.is_absolute() or not isinstance(journal_id, UUID):
            raise JournalError("invalid_config")
        parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        os.mkdir(directory.name, 0o700, dir_fd=parent)
        target = os.open(
            directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        values = {
            "journal.meta": _canonical({"version": 1, "journal_id": str(journal_id)}),
            ".journal.lock": b"",
            "entries.log": b"",
            "HEAD": _canonical({"journal_id": str(journal_id), "seq": 0, "digest": _ZERO.hex()}),
        }
        for name, data in values.items():
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target
            )
            try:
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(target)
        os.fsync(parent)
    except OSError:
        raise JournalError("unavailable") from None
    finally:
        for fd in (target, parent):
            if fd >= 0:
                os.close(fd)
