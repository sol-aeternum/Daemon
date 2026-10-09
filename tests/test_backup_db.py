from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import backup_db as backup

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def dump(directory: Path, age: timedelta, content: bytes = b"existing") -> Path:
    name = f"daemon_backup_{(NOW - age).strftime('%Y%m%d_%H%M%S')}.dump"
    path = directory / name
    path.write_bytes(content)
    return path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("BACKUP_DIR", str(directory))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture.invalid/test")
    monkeypatch.setattr(backup, "utc_now", lambda: NOW)

    def run(command, *, stdout, stderr, check):
        assert command == [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "postgresql://fixture.invalid/test",
        ]
        stdout.write(b"mock custom dump")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(backup.subprocess, "run", run)
    return directory


def inventory(store: Path) -> dict:
    return json.loads((store / backup.INVENTORY).read_text())


def previous_inventory(store: Path) -> None:
    with backup.locked_directory(store) as directory:
        backup.write_inventory(directory, [])


def test_rotation_cutoff_and_inventory(store: Path):
    expired = dump(store, timedelta(days=30, seconds=1))
    boundary = dump(store, timedelta(days=30))
    recent = dump(store, timedelta(days=1))
    assert backup.main() == 0
    assert not expired.exists()
    assert boundary.read_bytes() == recent.read_bytes() == b"existing"
    data = inventory(store)
    assert data["retention_verified"] is True
    assert data["oldest_retained_at"] == (NOW - timedelta(days=30)).isoformat()
    assert len(data["dumps"]) == 3
    assert data["journal_applied_markers"] is None
    assert data["journal_pruning_permitted"] is False
    assert (store / backup.INVENTORY).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["exit", "empty", "missing_binary", "missing_config"])
def test_failed_dump_still_expires_and_invalidates_old_inventory(
    store: Path, monkeypatch: pytest.MonkeyPatch, failure: str
):
    expired = dump(store, timedelta(days=31))
    previous_inventory(store)

    def fail(command, *, stdout, stderr, check):
        assert not (store / backup.INVENTORY).exists()
        if failure == "missing_binary":
            raise FileNotFoundError("pg_dump")
        if failure == "exit":
            stdout.write(b"partial")
        return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0)

    monkeypatch.setattr(backup.subprocess, "run", fail)
    if failure == "missing_config":
        monkeypatch.delenv("DATABASE_URL")
    assert backup.main() == 1
    assert not expired.exists()
    assert not list(store.glob("*.dump"))
    assert not list(store.glob("*.tmp"))
    assert inventory(store)["retention_verified"] is False


@pytest.mark.parametrize(
    "kind", ["unknown", "invalid", "future", "symlink", "hardlink", "directory", "interrupted"]
)
def test_ambiguous_entries_preserved_and_retention_unverified(store: Path, kind: str):
    expired = dump(store, timedelta(days=31))
    if kind == "future":
        ambiguous = dump(store, timedelta(days=-1))
    elif kind == "invalid":
        ambiguous = store / "daemon_backup_20269999_999999.dump"
        ambiguous.write_bytes(b"unknown")
    elif kind in {"symlink", "hardlink"}:
        target = store.parent / "outside"
        target.write_bytes(b"do not delete")
        ambiguous = store / "daemon_backup_20200101_000000.dump"
        if kind == "symlink":
            ambiguous.symlink_to(target)
        else:
            os.link(target, ambiguous)
    elif kind == "directory":
        ambiguous = store / "daemon_backup_20200101_000000.dump"
        ambiguous.mkdir()
    else:
        ambiguous = store / (
            ".backup-dump-interrupted.tmp" if kind == "interrupted" else "operator.dump"
        )
        ambiguous.write_bytes(b"unknown")
    assert backup.main() == 1
    assert ambiguous.exists()
    assert not expired.exists()
    assert inventory(store)["retention_verified"] is False
    assert "unrecognized_entry" in inventory(store)["errors"]


def test_collision_never_overwrites(store: Path):
    existing = dump(store, timedelta(0), b"precious")
    assert backup.main() == 1
    assert existing.read_bytes() == b"precious"
    assert not list(store.glob("*.tmp"))
    assert inventory(store)["retention_verified"] is False


def test_deletion_failure_is_recorded(store: Path, monkeypatch: pytest.MonkeyPatch):
    expired = dump(store, timedelta(days=31))
    real_unlink = os.unlink

    def unlink(path, *, dir_fd=None):
        if path == expired.name:
            raise PermissionError("fixture denied")
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(backup.os, "unlink", unlink)
    assert backup.main() == 1
    assert expired.exists()
    assert "expiry_failed" in inventory(store)["errors"]
    assert "expired_dump_remains" in inventory(store)["errors"]


def test_inventory_publish_failure_leaves_no_stale_success(
    store: Path, monkeypatch: pytest.MonkeyPatch
):
    previous_inventory(store)

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(backup.os, "replace", fail)
    assert backup.main() == 1
    assert not (store / backup.INVENTORY).exists()
    assert len(list(store.glob("*.dump"))) == 1


@pytest.mark.parametrize("name", [backup.LOCK, backup.INVENTORY])
def test_control_symlink_fails_before_deletion(store: Path, name: str):
    target = store.parent / "operator-file"
    target.write_text("do not touch")
    (store / name).symlink_to(target)
    old = dump(store, timedelta(days=31))
    assert backup.main() == 1
    assert old.exists()
    assert target.read_text() == "do not touch"


def test_directory_replacement_does_not_redirect_mutations(
    store: Path, monkeypatch: pytest.MonkeyPatch
):
    old = dump(store, timedelta(days=31))
    moved = store.parent / "moved"
    real_invalidate = backup.invalidate_inventory

    def replace_directory(directory):
        store.rename(moved)
        store.mkdir(mode=0o700)
        dump(store, timedelta(days=31), b"different directory")
        real_invalidate(directory)

    monkeypatch.setattr(backup, "invalidate_inventory", replace_directory)
    assert backup.main() == 0
    assert old.read_bytes() == b"different directory"
    assert not (moved / old.name).exists()
    assert (moved / backup.INVENTORY).exists()
    assert not (store / backup.INVENTORY).exists()


def test_interrupted_dump_leaves_inventory_absent(store: Path, monkeypatch: pytest.MonkeyPatch):
    previous_inventory(store)

    def interrupt(command, *, stdout, stderr, check):
        stdout.write(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(backup.subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        backup.main()
    assert not (store / backup.INVENTORY).exists()
    assert not list(store.glob("*.dump"))


def test_lock_excludes_another_process_and_keeps_inode(store: Path):
    code = """
import fcntl, sys
with open(sys.argv[1], 'r+b') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(42)
"""
    with backup.locked_directory(store):
        inode = (store / backup.LOCK).stat().st_ino
        process = subprocess.Popen([sys.executable, "-c", code, str(store / backup.LOCK)])
        assert process.wait(timeout=10) == 42
    with backup.locked_directory(store):
        assert (store / backup.LOCK).stat().st_ino == inode


def test_inventory_directory_fsync_failure_removes_verified_file(
    store: Path, monkeypatch: pytest.MonkeyPatch
):
    real_fsync = os.fsync

    def fsync(descriptor):
        if (store / backup.INVENTORY).exists():
            raise OSError("fsync failed after publication")
        real_fsync(descriptor)

    monkeypatch.setattr(backup.os, "fsync", fsync)
    assert backup.main() == 1
    assert not (store / backup.INVENTORY).exists()


@pytest.mark.parametrize(
    "content",
    [
        b'{"operator_notes": "unrelated file, preserve"}',
        b"not JSON",
        b"{",
        b"[]",
        b"null",
        b'{"retention_verified": true}',
        b"\xff",
        b"[" * 2000 + b"]" * 2000,
        b" " * (8 * 1024 * 1024 + 1),
    ],
)
def test_unrecognized_inventory_preserved_before_any_expiry(store: Path, content: bytes):
    existing = store / backup.INVENTORY
    existing.write_bytes(content)
    old = dump(store, timedelta(days=31))
    assert backup.main() == 1
    assert existing.read_bytes() == content
    assert old.read_bytes() == b"existing"
    assert list(store.glob("*.dump")) == [old]


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("directory_inode", -1),
        ("directory_device", -1),
        ("scanned_at", "invalid"),
        ("scanned_at", "2026-10-09T12:00:00"),
        ("dumps", [None]),
        ("errors", "wrong"),
        ("journal_pruning_permitted", True),
        ("journal_applied_markers", []),
        ("oldest_retained_at", "invented"),
    ],
)
def test_invalid_inventory_shape_or_identity_preserved(store: Path, field: str, value: object):
    previous_inventory(store)
    data = inventory(store)
    data[field] = value
    content = json.dumps(data)
    existing = store / backup.INVENTORY
    existing.write_text(content)
    old = dump(store, timedelta(days=31))
    assert backup.main() == 1
    assert existing.read_text() == content
    assert old.exists()


def test_valid_previous_inventory_allows_next_invocation(
    store: Path, monkeypatch: pytest.MonkeyPatch
):
    assert backup.main() == 0
    first = inventory(store)
    monkeypatch.setattr(backup, "utc_now", lambda: NOW + timedelta(seconds=1))
    assert backup.main() == 0
    second = inventory(store)
    assert second["retention_verified"] is True
    assert second["scanned_at"] != first["scanned_at"]
    assert len(second["dumps"]) == 2
