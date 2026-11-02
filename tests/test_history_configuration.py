"""Synthetic individual POSIX file checks, not two-UID deployment qualification."""

import os
import time

import pytest

from orchestrator.config import Settings
from orchestrator.services.restore_history import configuration
from orchestrator.services.restore_history.model import HistoryError, Identity, canonical, key_check
from tests.restore_history_support import HISTORY, KEY


def deadline():
    return time.monotonic() + 5


@pytest.fixture
def files(tmp_path):
    folder = tmp_path / "operator-config"
    folder.mkdir(mode=0o750)
    expected = Identity(HISTORY, key_check(KEY, HISTORY))
    metadata, key = folder / "expected", folder / "key"
    metadata.write_bytes(canonical(expected.wire()))
    metadata.chmod(0o440)
    key.write_bytes(KEY)
    key.chmod(0o640)
    return folder, metadata, key, expected


def identity(path):
    return configuration.load_expected_identity(
        path, os.geteuid(), os.getegid(), deadline=deadline()
    )


def secret(path, expected):
    return configuration.load_backend_key(
        path, os.geteuid(), os.getegid(), expected, deadline=deadline()
    )


def test_reads_independent_identity_and_raw_key_with_short_reads(files, monkeypatch):
    _, metadata, key, expected = files
    read = os.read
    monkeypatch.setattr(os, "read", lambda fd, size: read(fd, min(size, 3)))
    assert identity(metadata) == expected
    assert secret(key, expected) == KEY


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o660, 0o400])
def test_key_permission_drift_refuses_without_rewriting(files, mode):
    _, _, key, expected = files
    key.chmod(mode)
    with pytest.raises(HistoryError):
        secret(key, expected)
    assert key.read_bytes() == KEY and key.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "hardlink", "writable_parent"])
def test_unsafe_name_or_parent_refuses(files, tmp_path, kind):
    folder, _, key, expected = files
    if kind == "file_symlink":
        original = folder / "original"
        key.rename(original)
        key.symlink_to(original)
    elif kind == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(folder, target_is_directory=True)
        key = alias / key.name
    elif kind == "hardlink":
        os.link(key, folder / "linked")
    else:
        folder.chmod(0o770)
    with pytest.raises(HistoryError):
        secret(key, expected)


@pytest.mark.parametrize("size", [0, 31, 33])
def test_wrong_key_length_refuses(files, size):
    _, _, key, expected = files
    key.write_bytes(bytes(size))
    with pytest.raises(HistoryError):
        secret(key, expected)


@pytest.mark.parametrize("data", [b"{}", b'{"v":1,"v":1}', b" " * 257])
def test_bad_independent_metadata_is_not_learned_from_history(files, data):
    _, metadata, _, _ = files
    metadata.chmod(0o640)
    metadata.write_bytes(data)
    metadata.chmod(0o440)
    with pytest.raises(HistoryError):
        identity(metadata)


def test_changed_key_refuses_without_exposing_key_material(files):
    _, _, key, expected = files
    key.write_bytes(bytes(32))
    with pytest.raises(HistoryError) as caught:
        secret(key, expected)
    assert caught.value.code == "key_mismatch"
    assert KEY.hex() not in str(caught.value)


def test_in_place_key_change_during_read_refuses(files, monkeypatch):
    _, _, key, expected = files
    read = os.read
    changed = False

    def mutate(fd, size):
        nonlocal changed
        chunk = read(fd, size)
        if chunk and not changed:
            changed = True
            key.write_bytes(bytes(32))
        return chunk

    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(HistoryError):
        secret(key, expected)


def test_expired_read_opens_no_descriptor(files, monkeypatch):
    _, metadata, _, _ = files

    def forbidden(*args, **kwargs):
        pytest.fail("expired configuration read started I/O")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(HistoryError):
        configuration.load_expected_identity(
            metadata, os.geteuid(), os.getegid(), deadline=time.monotonic() - 1
        )


def test_replaced_key_name_refuses(files, monkeypatch):
    folder, _, key, expected = files
    read = os.read
    replaced = False

    def replace(fd, size):
        nonlocal replaced
        chunk = read(fd, size)
        if chunk and not replaced:
            replaced = True
            other = folder / "replacement"
            other.write_bytes(KEY)
            other.chmod(0o640)
            other.replace(key)
        return chunk

    monkeypatch.setattr(os, "read", replace)
    with pytest.raises(HistoryError):
        secret(key, expected)


def test_key_exception_contains_no_source_path(files):
    folder, _, _, expected = files
    with pytest.raises(HistoryError) as caught:
        secret(folder / "missing", expected)
    assert str(folder) not in str(caught.value)


def test_late_descriptor_cleanup_cannot_return_a_key(files, monkeypatch):
    _, _, key, expected = files
    close, check = os.close, configuration._check_time
    late = False

    def closing(fd):
        nonlocal late
        is_key = os.readlink(f"/proc/self/fd/{fd}") == str(key)
        close(fd)
        late = late or is_key

    def clock(limit):
        if late:
            raise HistoryError("deadline")
        check(limit)

    monkeypatch.setattr(os, "close", closing)
    monkeypatch.setattr(configuration, "_check_time", clock)
    with pytest.raises(HistoryError, match="deadline"):
        secret(key, expected)


def test_descriptor_close_failure_cannot_return_material_or_skip_other_closes(files, monkeypatch):
    _, _, key, expected = files
    close = os.close
    key_closed = False
    later_closed = []

    def closing(fd):
        nonlocal key_closed
        is_key = os.readlink(f"/proc/self/fd/{fd}") == str(key)
        close(fd)
        if is_key:
            key_closed = True
            raise OSError("synthetic key close failure")
        if key_closed:
            later_closed.append(fd)

    monkeypatch.setattr(os, "close", closing)
    with pytest.raises(HistoryError, match="configuration_cleanup_uncertain"):
        secret(key, expected)
    assert key_closed and later_closed


def test_owner_key_permissions_require_worker_uid_separation(files, monkeypatch):
    folder, metadata, key, _ = files
    worker_uid = os.geteuid()
    history_gid = os.getegid()
    key_gid = history_gid + 1
    # Actual private fixture permissions give this UID owner read/write access;
    # excluding a modeled service from the key group cannot remove owner access.
    assert key.stat().st_uid == worker_uid
    assert key.stat().st_mode & 0o777 == 0o640
    with key.open("r+b") as stream:
        assert stream.read() == KEY
    value = Settings.model_construct(
        daemon_restore_history_dir=str(folder / "history"),
        daemon_restore_history_expected_file=str(metadata),
        daemon_restore_history_operator_uid=str(worker_uid),
        daemon_restore_history_backend_uid=str(worker_uid + 2),
        daemon_restore_history_worker_uid=str(worker_uid),
        daemon_restore_history_group_gid=str(history_gid),
        daemon_restore_history_key_gid=str(key_gid),
        daemon_restore_history_key_file=None,
    )
    monkeypatch.setattr(os, "getuid", lambda: worker_uid)
    monkeypatch.setattr(os, "geteuid", lambda: worker_uid)
    monkeypatch.setattr(os, "getegid", lambda: history_gid)
    monkeypatch.setattr(os, "getgroups", lambda: [history_gid])
    monkeypatch.setattr(os, "open", lambda *a, **kw: pytest.fail("collision started file I/O"))
    with pytest.raises(HistoryError, match="history_configuration_required"):
        configuration.configured_history(value, "worker")
