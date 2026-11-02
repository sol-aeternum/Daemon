"""Read existing operator-owned H metadata and backend key files; never create.

These synchronous calls own every descriptor until completion. Runtime callers
must supervise/drain the physical call through timeout and cancellation. No
production path, identity, group, key, fallback or initialization is supplied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import stat
from typing import Literal
from uuid import UUID

from orchestrator.config import Settings

from .model import HistoryError, Identity, parse
from .store import Ownership, _check_time

SETTING_FIELDS = (
    "daemon_restore_history_dir",
    "daemon_restore_history_expected_file",
    "daemon_restore_history_operator_uid",
    "daemon_restore_history_backend_uid",
    "daemon_restore_history_worker_uid",
    "daemon_restore_history_group_gid",
    "daemon_restore_history_key_gid",
    "daemon_restore_history_key_file",
)


def history_selected(settings: Settings) -> bool:
    """Any supplied part selects fail-closed H behavior, never legacy fallback."""
    return any(getattr(settings, name, None) not in (None, "") for name in SETTING_FIELDS)


def _identifier(value: str | None, *, root_allowed: bool = False) -> int:
    if type(value) is not str or re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is None:
        raise HistoryError("history_configuration_required")
    parsed = int(value)
    if not (0 if root_allowed else 1) <= parsed < 2**32 - 1:
        raise HistoryError("history_configuration_required")
    return parsed


def _path(value: str | None) -> Path:
    if type(value) is not str:
        raise HistoryError("history_configuration_required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise HistoryError("history_configuration_required")
    return path


@dataclass(frozen=True, slots=True)
class HistoryConfiguration:
    directory: Path
    expected_file: Path
    operator_uid: int
    backend_uid: int
    worker_uid: int
    group_gid: int
    key_gid: int
    key_file: Path | None = field(repr=False)

    def __post_init__(self) -> None:
        # Owner permissions bypass group exclusion, including for the K1 file.
        if len({self.operator_uid, self.backend_uid, self.worker_uid}) != 3:
            raise HistoryError("history_configuration_required")

    @property
    def ownership(self) -> Ownership:
        return Ownership(
            self.operator_uid, frozenset({self.backend_uid, self.worker_uid}), self.group_gid
        )

    def check_service(self, service: Literal["http", "worker"]) -> None:
        if service not in ("http", "worker"):
            raise HistoryError("history_configuration_required")
        expected = self.backend_uid if service == "http" else self.worker_uid
        groups = {os.getegid(), *os.getgroups()}
        if os.getuid() != expected or os.geteuid() != expected or self.group_gid not in groups:
            raise HistoryError("history_service_identity_refused")
        if (service == "http" and self.key_gid not in groups) or (
            service == "worker" and self.key_gid in groups
        ):
            raise HistoryError("history_key_access_refused")


def configured_history(
    settings: Settings, service: Literal["http", "worker"]
) -> HistoryConfiguration | None:
    """Pure complete selection; blank defaults preserve unselected hosts."""
    if service not in ("http", "worker"):
        raise HistoryError("history_configuration_required")
    if service == "worker" and settings.daemon_restore_history_key_file:
        raise HistoryError("history_worker_key_forbidden")
    if not history_selected(settings):
        return None
    result = HistoryConfiguration(
        _path(settings.daemon_restore_history_dir),
        _path(settings.daemon_restore_history_expected_file),
        _identifier(settings.daemon_restore_history_operator_uid, root_allowed=True),
        _identifier(settings.daemon_restore_history_backend_uid),
        _identifier(settings.daemon_restore_history_worker_uid),
        _identifier(settings.daemon_restore_history_group_gid),
        _identifier(settings.daemon_restore_history_key_gid),
        _path(settings.daemon_restore_history_key_file) if service == "http" else None,
    )
    if (
        result.group_gid == result.key_gid
        or result.expected_file.is_relative_to(result.directory)
        or (result.key_file is not None and result.key_file.is_relative_to(result.directory))
        or result.expected_file == result.key_file
    ):
        raise HistoryError("history_configuration_required")
    return result


@dataclass(frozen=True, slots=True)
class HistoryMaterial:
    expected: Identity
    key: bytes | None = field(repr=False)


def load_material(
    configuration: HistoryConfiguration, service: Literal["http", "worker"], *, deadline: float
) -> HistoryMaterial:
    """Only called on a retained filesystem worker; no initializer or fallback."""
    if service not in ("http", "worker"):
        raise HistoryError("history_configuration_required")
    if service == "worker" and configuration.key_file is not None:
        raise HistoryError("history_worker_key_forbidden")
    configuration.check_service(service)
    expected = load_expected_identity(
        configuration.expected_file,
        configuration.operator_uid,
        configuration.group_gid,
        deadline=deadline,
    )
    key = None
    if service == "http":
        if configuration.key_file is None:
            raise HistoryError("history_configuration_required")
        key = load_backend_key(
            configuration.key_file,
            configuration.operator_uid,
            configuration.key_gid,
            expected,
            deadline=deadline,
        )
    _check_time(deadline)
    return HistoryMaterial(expected, key)


def _close(fd: int, descriptors: list[int]) -> None:
    try:
        os.close(fd)
    finally:
        # Never retry an uncertain close, including descriptor-number reuse.
        descriptors.remove(fd)


def _parent(
    path: Path, operator_uid: int, group_gid: int, deadline: float, descriptors: list[int]
) -> int:
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    descriptors.append(fd)
    for index, component in enumerate(path.parent.parts[1:]):
        _check_time(deadline)
        child = os.open(
            component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
        )
        descriptors.append(child)
        old, fd = fd, child
        _close(old, descriptors)
        info = os.fstat(fd)
        final = index == len(path.parent.parts) - 2
        if final:
            if (
                info.st_uid != operator_uid
                or info.st_gid != group_gid
                or stat.S_IMODE(info.st_mode) != 0o750
            ):
                raise HistoryError("unsafe_configuration")
        else:
            sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, operator_uid) or (info.st_mode & 0o022 and not sticky):
                raise HistoryError("unsafe_configuration")
    return fd


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read(
    path: Path, operator_uid: int, group_gid: int, mode: int, bound: int, deadline: float
) -> bytes:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.parent == Path("/")
        or ".." in path.parts
        or any(type(value) is not int or value < 0 for value in (operator_uid, group_gid))
    ):
        raise HistoryError("unsafe_configuration")
    descriptors: list[int] = []
    original: BaseException | None = None
    try:
        _check_time(deadline)
        parent = _parent(path, operator_uid, group_gid, deadline, descriptors)
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent
        )
        descriptors.append(fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != operator_uid
            or before.st_gid != group_gid
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or not 0 < before.st_size <= bound
        ):
            raise HistoryError("unsafe_configuration")
        data = bytearray()
        while True:
            _check_time(deadline)
            chunk = os.read(fd, bound + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > bound:
                raise HistoryError("unsafe_configuration")
        named_parent = _parent(path, operator_uid, group_gid, deadline, descriptors)
        if (
            _signature(os.fstat(parent)) != _signature(os.fstat(named_parent))
            or _signature(before) != _signature(os.fstat(fd))
            or _signature(before)
            != _signature(os.stat(path.name, dir_fd=named_parent, follow_symlinks=False))
            or len(data) != before.st_size
        ):
            raise HistoryError("unsafe_configuration")
        _check_time(deadline)
        return bytes(data)
    except OSError:
        original = HistoryError("configuration_unavailable")
        raise original from None
    except BaseException as error:
        original = error
        raise
    finally:
        cleanup: BaseException | None = None
        for fd in reversed(tuple(descriptors)):
            try:
                _close(fd, descriptors)
            except BaseException as error:
                if cleanup is None:
                    cleanup = error
        if cleanup is not None:
            if original is not None:
                original.add_note("Configuration descriptor cleanup uncertain")
            else:
                raise HistoryError("configuration_cleanup_uncertain", uncertain=True) from None
        if original is None:
            _check_time(deadline)


def load_expected_identity(
    path: Path, operator_uid: int, group_gid: int, *, deadline: float
) -> Identity:
    """Independent canonical metadata, outside the history writer directory."""
    value = parse(_read(path, operator_uid, group_gid, 0o440, 256, deadline))
    if (
        set(value) != {"v", "history_id", "key_id", "key_check"}
        or type(value["v"]) is not int
        or value["v"] != 1
        or type(value["key_id"]) is not int
        or value["key_id"] != 1
        or type(value["history_id"]) is not str
        or type(value["key_check"]) is not str
    ):
        raise HistoryError("unsafe_configuration")
    try:
        identity = Identity(UUID(value["history_id"]), value["key_check"])
        if identity.wire() != value:
            raise HistoryError("unsafe_configuration")
        _check_time(deadline)
        return identity
    except (TypeError, ValueError):
        raise HistoryError("unsafe_configuration") from None


def load_backend_key(
    path: Path, operator_uid: int, key_group_gid: int, expected: Identity, *, deadline: float
) -> bytes:
    """Exactly 32 raw bytes; no text/base64/pepper fallback and no key logging."""
    key = _read(path, operator_uid, key_group_gid, 0o640, 32, deadline)
    if len(key) != 32:
        raise HistoryError("unsafe_configuration")
    expected.verify_key(key)
    _check_time(deadline)
    return key
