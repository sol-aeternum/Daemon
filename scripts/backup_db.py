#!/usr/bin/env python3

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

RETENTION = timedelta(days=30)
INVENTORY = "backup_inventory.json"
LOCK = ".backup.lock"
DUMP_NAME = re.compile(r"daemon_backup_([0-9]{8}_[0-9]{6})\.dump", re.ASCII)


def utc_now() -> datetime:
    return datetime.now(UTC)


def dump_time(name: str) -> datetime | None:
    match = DUMP_NAME.fullmatch(name)
    if match is None:
        return None
    try:
        value = datetime.strptime(match[1], "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return value if value.strftime("%Y%m%d_%H%M%S") == match[1] else None


def regular_owned(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()


@contextmanager
def locked_directory(output_dir: Path):
    # All subsequent operations are relative to this pinned directory, never
    # a freshly resolved path. Operators must exclude uncooperative writers.
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise OSError(
                "backup directory must be owned by this user and not group/world writable"
            )
        lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            if not regular_owned(os.fstat(lock)):
                raise OSError("invalid backup lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            # The persistent lock inode must never be removed by cleanup.
            yield directory
        finally:
            os.close(lock)
    finally:
        os.close(directory)


def recognized_inventory(data: object, directory: int) -> bool:
    """Recognize this script's v1 output, not arbitrary same-named JSON."""
    if not isinstance(data, dict) or set(data) != {
        "version",
        "scanned_at",
        "retention_days",
        "retention_verified",
        "errors",
        "oldest_retained_at",
        "dumps",
        "journal_applied_markers",
        "journal_pruning_permitted",
        "directory_device",
        "directory_inode",
    }:
        return False
    info = os.fstat(directory)
    for field, expected in (
        ("version", 1),
        ("retention_days", RETENTION.days),
        ("directory_device", info.st_dev),
        ("directory_inode", info.st_ino),
    ):
        if type(data[field]) is not int or data[field] != expected:
            return False
    if (
        data["journal_applied_markers"] is not None
        or data["journal_pruning_permitted"] is not False
    ):
        return False
    if type(data["retention_verified"]) is not bool or not isinstance(data["errors"], list):
        return False
    if any(not isinstance(error, str) for error in data["errors"]):
        return False
    if data["retention_verified"] != (not data["errors"]):
        return False
    if not isinstance(data["scanned_at"], str) or not isinstance(data["dumps"], list):
        return False
    try:
        scanned = datetime.fromisoformat(data["scanned_at"])
    except ValueError:
        return False
    if scanned.utcoffset() != timedelta(0):
        return False
    dates: list[str] = []
    names: set[str] = set()
    for dump in data["dumps"]:
        if not isinstance(dump, dict) or set(dump) != {"name", "created_at", "size_bytes"}:
            return False
        if not isinstance(dump["name"], str):
            return False
        created = dump_time(dump["name"])
        if created is None or dump["created_at"] != created.isoformat() or created > scanned:
            return False
        if type(dump["size_bytes"]) is not int or dump["size_bytes"] < 0 or dump["name"] in names:
            return False
        names.add(dump["name"])
        dates.append(created.isoformat())
    return data["oldest_retained_at"] == min(dates, default=None)


def invalidate_inventory(directory: int) -> None:
    try:
        info = os.stat(INVENTORY, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not regular_owned(info):
        raise OSError("invalid backup inventory file")
    descriptor = os.open(INVENTORY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as previous:
        opened = os.fstat(previous.fileno())
        if not regular_owned(opened) or (opened.st_dev, opened.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise OSError("backup inventory changed")
        # Bound corrupt/operator input. Larger inventories require operator
        # reconciliation, never silent deletion based on the reserved name.
        payload = previous.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise OSError("backup inventory exceeds recognition limit")
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise OSError("unrecognized backup inventory") from exc
        if not recognized_inventory(data, directory):
            raise OSError("unrecognized backup inventory")
    os.unlink(INVENTORY, dir_fd=directory)
    os.fsync(directory)


def scan(directory: int, now: datetime) -> tuple[list[dict], list[str]]:
    dumps: list[dict] = []
    errors: list[str] = []
    for name in sorted(os.listdir(directory)):
        if name == LOCK:
            continue
        created = dump_time(name)
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError:
            errors.append("entry_stat_failed")
            continue
        if created is None or not regular_owned(info) or created > now:
            # Includes unknown operator files, symlinks and interrupted temp
            # files. Do not recurse, follow, rename or remove these entries.
            errors.append("unrecognized_entry")
            continue
        dumps.append({"name": name, "created_at": created.isoformat(), "size_bytes": info.st_size})
    return dumps, errors


def rotate(directory: int, now: datetime) -> list[str]:
    dumps, errors = scan(directory, now)
    for dump in dumps:
        created = datetime.fromisoformat(dump["created_at"])
        if created < now - RETENTION:
            try:
                os.unlink(dump["name"], dir_fd=directory)
            except OSError:
                errors.append("expiry_failed")
    os.fsync(directory)
    return errors


def private_file(directory: int, kind: str) -> tuple[str, int]:
    name = f".backup-{kind}-{uuid4().hex}.tmp"
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    return name, descriptor


def create_dump(directory: int, database_url: str) -> None:
    name = f"daemon_backup_{utc_now().strftime('%Y%m%d_%H%M%S')}.dump"
    temporary, descriptor = private_file(directory, "dump")
    try:
        with os.fdopen(descriptor, "wb") as output:
            # stdout writes only to our already-open private file; database
            # credentials and provider stderr are never copied into inventory.
            result = subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl", database_url],
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            output.flush()
            if result.returncode != 0 or os.fstat(output.fileno()).st_size == 0:
                raise OSError("database dump failed or empty")
            os.fsync(output.fileno())
        # Atomic no-clobber publication: unlike rename/replace, a collision
        # cannot overwrite an existing successful dump or a symlink.
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)


def write_inventory(directory: int, errors: list[str]) -> bool:
    now = utc_now()
    dumps, scan_errors = scan(directory, now)
    errors = errors + scan_errors
    if any(datetime.fromisoformat(dump["created_at"]) < now - RETENTION for dump in dumps):
        errors.append("expired_dump_remains")
    verified = not errors
    inventory = {
        "version": 1,
        "scanned_at": now.isoformat(),
        "retention_days": RETENTION.days,
        "retention_verified": verified,
        "errors": sorted(set(errors)),
        "oldest_retained_at": min((dump["created_at"] for dump in dumps), default=None),
        "dumps": dumps,
        "journal_applied_markers": None,
        "journal_pruning_permitted": False,
        "directory_device": os.fstat(directory).st_dev,
        "directory_inode": os.fstat(directory).st_ino,
    }
    temporary, descriptor = private_file(directory, "inventory")
    published = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(inventory, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, INVENTORY, src_dir_fd=directory, dst_dir_fd=directory)
        published = True
        os.fsync(directory)
    except OSError:
        if published:
            os.unlink(INVENTORY, dir_fd=directory)
        raise
    finally:
        # A failed write must not make yesterday's inventory look current.
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
    return verified


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    output_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    try:
        with locked_directory(output_dir) as directory:
            invalidate_inventory(directory)
            errors = rotate(directory, utc_now())
            try:
                if not database_url:
                    raise OSError("DATABASE_URL is required")
                create_dump(directory, database_url)
            except OSError:
                errors.append("backup_failed")
            # A long-running or failed dump cannot bypass expiry. Rescan only
            # after mutations; successful inventory is a point-in-time fact.
            errors.extend(rotate(directory, utc_now()))
            if not write_inventory(directory, errors):
                print("Backup retention unverified; inspect backup inventory", file=sys.stderr)
                return 1
        return 0
    except OSError:
        print("Backup failed; retention unverified", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
