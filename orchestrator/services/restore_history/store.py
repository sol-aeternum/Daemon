"""Uncalled synchronous POSIX H1 prototype; no initializer or runtime adapter.

Absolute deadlines bound admission, not kernel syscall duration. Interrupted
bytes remain untouched. A result is history, never authority to execute work.
"""

from __future__ import annotations

import fcntl
import math
import os
import stat
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .model import (
    MAX_LOG,
    MAX_PAYLOAD,
    MAX_RECORDS,
    Command,
    Entry,
    HistoryError,
    HistoryState,
    Identity,
    canonical,
    decode_entry,
    encode_entry,
    parse,
    validate_chain,
)

FILES = frozenset({"IDENTITY", "LOG", "HEAD", "OP_LOCK", "APPEND_LOCK", "RUNTIME_LOCK"})
PENDING = "HEAD.pending"
PINNED = FILES - {"HEAD"}


@dataclass(frozen=True, slots=True)
class Ownership:
    operator_uid: int
    publisher_uids: frozenset[int]
    group_gid: int

    def validate(self) -> None:
        values = (self.operator_uid, self.group_gid, *self.publisher_uids)
        if not self.publisher_uids or any(type(v) is not int or v < 0 for v in values):
            raise HistoryError("invalid_config")


@dataclass(frozen=True, slots=True)
class AppendResult:
    entry: Entry
    created: bool


@dataclass(slots=True)
class _AppendOutcome:
    attempted: bool = False
    durable: bool = False


def _check_time(deadline: float) -> None:
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise HistoryError("invalid_deadline")
    if time.monotonic() >= deadline:
        raise HistoryError("deadline")


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


class HistoryStore:
    """One pinned store object, serialized threads and cooperating processes.

    Caller supplies independent expected identity and approved ownership facts.
    No keys, environment/config discovery, producer lifecycle or DB authority.
    close() waits for the synchronous owner instead of closing its live FDs.
    """

    def __init__(
        self, path: Path, expected: Identity, ownership: Ownership, *, deadline: float
    ) -> None:
        self._owner_pid = os.getpid()
        self.path = path
        self.expected = expected
        self.ownership = ownership
        self._mutex = threading.Lock()
        self._dir = -1
        self._fds: dict[str, int] = {}
        self._pins: dict[str, tuple[int, int]] = {}
        self._poisoned = False
        try:
            ownership.validate()
            expected.wire()
            _check_time(deadline)
            if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
                raise HistoryError("invalid_config")
            self._dir = self._open_directory(path, deadline)
            self._directory_inode = _inode(os.fstat(self._dir))
            self._check_dir_info(os.fstat(self._dir))
            for name in sorted(PINNED):
                self._fds[name] = self._open(name, os.O_RDWR if name != "IDENTITY" else os.O_RDONLY)
                info = os.fstat(self._fds[name])
                self._pins[name] = _inode(info)
                if name.endswith("LOCK") and info.st_size:
                    raise HistoryError("unsafe_storage")
            with self._locked(deadline):
                self._scan(deadline)
        except BaseException as exc:
            try:
                self.close()
            except BaseException:
                exc.add_note("History descriptor cleanup uncertain")
            if isinstance(exc, OSError):
                raise HistoryError("unavailable") from None
            raise

    def _require_owner(self) -> None:
        # flock is tied to open file descriptions shared across fork. A copied
        # thread mutex cannot serialize those descriptions with the parent.
        if os.getpid() != self._owner_pid:
            raise HistoryError("forked_store")

    def _open_directory(self, path: Path, deadline: float) -> int:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for index, part in enumerate(path.parts[1:]):
                _check_time(deadline)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if index < len(path.parts) - 2:
                    # Root-owned sticky /tmp is safe against other users
                    # replacing our owned child. Other writable ancestors fail.
                    sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
                    if info.st_uid not in (0, self.ownership.operator_uid) or (
                        info.st_mode & 0o022 and not sticky
                    ):
                        raise HistoryError("unsafe_storage")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _check_dir_info(self, info: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o2770
            or info.st_uid != self.ownership.operator_uid
            or info.st_gid != self.ownership.group_gid
        ):
            raise HistoryError("unsafe_storage")

    def _safe(self, name: str, fd: int) -> os.stat_result:
        info = os.fstat(fd)
        owners = (
            ({self.ownership.operator_uid} | set(self.ownership.publisher_uids))
            if name in ("HEAD", PENDING)
            else {self.ownership.operator_uid}
        )
        mode = 0o440 if name == "IDENTITY" else 0o660
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_nlink != 1
            or info.st_uid not in owners
            or info.st_gid != self.ownership.group_gid
        ):
            raise HistoryError("unsafe_storage")
        return info

    def _open(self, name: str, flags: int) -> int:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=self._dir)
        try:
            self._safe(name, fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _check_pins(self, deadline: float) -> set[str]:
        _check_time(deadline)
        # Rewalk ancestors; an open directory alone does not qualify its name.
        fd = self._open_directory(self.path, deadline)
        try:
            info = os.fstat(fd)
            self._check_dir_info(info)
            if _inode(info) != self._directory_inode:
                raise HistoryError("unsafe_storage")
        finally:
            os.close(fd)
        info = os.fstat(self._dir)
        self._check_dir_info(info)
        listing = os.open(
            ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._dir
        )
        try:
            names = set(os.listdir(listing))
        finally:
            os.close(listing)
        if not FILES <= names or names - (FILES | {PENDING}):
            raise HistoryError("unsafe_storage")
        for name, pin in self._pins.items():
            named = os.stat(name, dir_fd=self._dir, follow_symlinks=False)
            if _inode(named) != pin or _inode(self._safe(name, self._fds[name])) != pin:
                raise HistoryError("unsafe_storage")
            if name.endswith("LOCK") and named.st_size:
                raise HistoryError("unsafe_storage")
        _check_time(deadline)
        return names

    @contextmanager
    def _locked(self, deadline: float, outcome: _AppendOutcome | None = None) -> Iterator[None]:
        self._require_owner()
        _check_time(deadline)
        if not self._mutex.acquire(timeout=max(0, deadline - time.monotonic())):
            raise HistoryError("deadline")
        locks = []
        original: BaseException | None = None
        try:
            if self._dir < 0:
                raise HistoryError("closed")
            for name in ("OP_LOCK", "APPEND_LOCK"):
                while True:
                    _check_time(deadline)
                    try:
                        fcntl.flock(self._fds[name], fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locks.append(self._fds[name])
                        break
                    except BlockingIOError:
                        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            self._check_pins(deadline)
            yield
        except BaseException as exc:
            original = exc
            if outcome is not None and outcome.attempted:
                self._poisoned = True
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            # A cancellation is an uncertain unlock too. Keep attempting the
            # other held locks and poison before releasing the local mutex.
            for fd in reversed(locks):
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BaseException as exc:
                    self._poisoned = True
                    cleanup_errors.append(exc)
            if original is None and not cleanup_errors:
                try:
                    _check_time(deadline)
                except BaseException as exc:
                    if outcome is not None and outcome.attempted:
                        self._poisoned = True
                    cleanup_errors.append(exc)
            try:
                self._mutex.release()
            except BaseException as exc:
                self._poisoned = True
                cleanup_errors.append(exc)
            if cleanup_errors:
                if original is not None:
                    original.add_note("History lock cleanup uncertain")
                else:
                    first = cleanup_errors[0]
                    if len(cleanup_errors) > 1:
                        first.add_note("Additional history lock cleanup failures")
                    raise first

    def _read(self, fd: int, size: int, deadline: float) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        remaining = size
        while remaining:
            _check_time(deadline)
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise HistoryError("corrupt")
            chunks.append(chunk)
            remaining -= len(chunk)
        _check_time(deadline)
        return b"".join(chunks)

    def _small(self, name: str, deadline: float) -> dict:
        fd = self._open(name, os.O_RDONLY)
        try:
            size = os.fstat(fd).st_size
            if not 1 <= size <= 256:
                raise HistoryError("corrupt")
            return parse(self._read(fd, size, deadline))
        finally:
            os.close(fd)

    def _scan(self, deadline: float) -> HistoryState:
        names = self._check_pins(deadline)
        if PENDING in names:
            fd = self._open(PENDING, os.O_RDONLY)
            os.close(fd)
            raise HistoryError("recovery_required")
        if canonical(self._small("IDENTITY", deadline)) != canonical(self.expected.wire()):
            raise HistoryError("identity_mismatch")
        size = os.fstat(self._fds["LOG"]).st_size
        if size > MAX_LOG:
            raise HistoryError("capacity")
        raw = self._read(self._fds["LOG"], size, deadline)
        entries = []
        offset = 0
        while offset < len(raw):
            _check_time(deadline)
            if len(entries) >= MAX_RECORDS:
                raise HistoryError("capacity")
            if len(raw) - offset < 4:
                raise HistoryError("corrupt")
            length = struct.unpack(">I", raw[offset : offset + 4])[0]
            end = offset + length + 36
            if not 1 <= length <= MAX_PAYLOAD or end > len(raw):
                raise HistoryError("corrupt")
            entries.append(decode_entry(raw[offset:end]))
            offset = end
        state = validate_chain(self.expected.history_id, entries)
        head = self._small("HEAD", deadline)
        if (
            set(head) != {"v", "history_id", "seq", "digest"}
            or type(head["v"]) is not int
            or head["v"] != 1
            or head["history_id"] != str(self.expected.history_id)
            or type(head["seq"]) is not int
            or not 0 <= head["seq"] <= len(entries)
        ):
            raise HistoryError("head_mismatch")
        seq = head["seq"]
        digest = entries[seq - 1].digest if seq else bytes(32)
        if head["digest"] != digest.hex():
            raise HistoryError("head_mismatch")
        if seq != len(entries):
            raise HistoryError("recovery_required")
        self._check_pins(deadline)
        return state

    def verify(self, *, deadline: float) -> HistoryState:
        self._require_owner()
        try:
            with self._locked(deadline):
                return self._scan(deadline)
        except OSError:
            raise HistoryError("unavailable") from None

    def _write(self, fd: int, data: bytes, deadline: float) -> None:
        remaining = memoryview(data)
        while remaining:
            _check_time(deadline)
            count = os.write(fd, remaining)
            if not 0 < count <= len(remaining):
                raise OSError("short_write")
            remaining = remaining[count:]
            _check_time(deadline)

    def append(self, command: Command, *, deadline: float) -> AppendResult:
        self._require_owner()
        if self._poisoned:
            raise HistoryError("store_poisoned")
        # Every append has a native outcome; no resumption after uncertainty.
        outcome = _AppendOutcome()
        try:
            with self._locked(deadline, outcome):
                if self._poisoned:
                    raise HistoryError("store_poisoned")
                state = self._scan(deadline)
                for existing in state.entries:
                    if existing.command.record_id == command.record_id:
                        frame = encode_entry(
                            existing.history_id, existing.seq, existing.prev_digest, command
                        )
                        if decode_entry(frame) != existing:
                            raise HistoryError("conflict")
                        self._check_pins(deadline)
                        return AppendResult(existing, False)
                frame = encode_entry(
                    self.expected.history_id, len(state.entries) + 1, state.digest, command
                )
                entry = decode_entry(frame)
                validate_chain(self.expected.history_id, (*state.entries, entry))
                if (
                    len(state.entries) >= MAX_RECORDS
                    or os.fstat(self._fds["LOG"]).st_size + len(frame) > MAX_LOG
                ):
                    raise HistoryError("capacity")
                _check_time(deadline)
                fd = self._fds["LOG"]
                os.lseek(fd, 0, os.SEEK_END)
                outcome.attempted = True
                self._write(fd, frame, deadline)
                os.fsync(fd)
                _check_time(deadline)
                temporary = os.open(
                    PENDING,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o660,
                    dir_fd=self._dir,
                )
                try:
                    os.fchmod(temporary, 0o660)
                    self._safe(PENDING, temporary)
                    self._write(
                        temporary,
                        canonical(
                            {
                                "v": 1,
                                "history_id": str(self.expected.history_id),
                                "seq": entry.seq,
                                "digest": entry.digest.hex(),
                            }
                        ),
                        deadline,
                    )
                    os.fsync(temporary)
                    _check_time(deadline)
                finally:
                    os.close(temporary)
                os.replace(PENDING, "HEAD", src_dir_fd=self._dir, dst_dir_fd=self._dir)
                _check_time(deadline)
                os.fsync(self._dir)
                outcome.durable = True
                expected_head = {
                    "v": 1,
                    "history_id": str(self.expected.history_id),
                    "seq": entry.seq,
                    "digest": entry.digest.hex(),
                }
                if canonical(self._small("HEAD", deadline)) != canonical(expected_head):
                    raise HistoryError("head_mismatch")
                self._check_pins(deadline)
                return AppendResult(entry, True)
        except (OSError, HistoryError) as exc:
            if outcome.attempted:
                self._poisoned = True
                code = "durable_result_unavailable" if outcome.durable else "unknown_append"
                raise HistoryError(code, uncertain=True) from None
            if isinstance(exc, OSError):
                raise HistoryError("unavailable") from None
            raise
        except BaseException as exc:
            if outcome.attempted:
                self._poisoned = True
                exc.add_note("History append outcome uncertain; invocation must not resume")
            raise

    def _close_descriptors(self) -> None:
        # Detach ownership before attempting each close once. Retrying an
        # uncertain close can close a reused descriptor on Linux.
        descriptors = list(self._fds.values())
        self._fds.clear()
        if self._dir >= 0:
            descriptors.append(self._dir)
            self._dir = -1
        first_error: BaseException | None = None
        for fd in descriptors:
            try:
                os.close(fd)
            except BaseException as exc:
                self._poisoned = True
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            if isinstance(first_error, OSError):
                raise HistoryError("cleanup_unavailable") from None
            first_error.add_note("History descriptor cleanup uncertain")
            raise first_error

    def close(self) -> None:
        if os.getpid() != self._owner_pid:
            # Drop only this child's descriptor references. LOCK_UN would
            # unlock the parent's shared description, and its copied mutex
            # may be permanently locked by a thread absent in the child.
            self._poisoned = True
            self._close_descriptors()
            return
        with self._mutex:
            self._close_descriptors()

    def __enter__(self) -> HistoryStore:
        self._require_owner()
        return self

    def __exit__(self, _kind: object, original: object, _traceback: object) -> None:
        try:
            self.close()
        except BaseException:
            if isinstance(original, BaseException):
                original.add_note("History descriptor cleanup uncertain")
            else:
                raise
