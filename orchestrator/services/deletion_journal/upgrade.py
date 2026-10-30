"""Offline container promotion; uncalled C1 source, never a startup repair.

The operator must stop ALL producers before calling this function. The journal
lock serializes operations, not process lifetimes, and cannot prove quiescence.
Only synthetic directories are qualified here. Promotion preserves every frame
and makes old readers refuse; there is no downgrade or automatic retry/repair.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import UUID

from .errors import JournalError
from .store import Journal, _canonical, _safe_file, _write_all

_PENDING = ".META.v2.pending"


def _verify_promotion_paths(journal: Journal, pending: os.stat_result) -> None:
    """Check pinned identities without admitting the temporary to normal scans."""
    directory = os.stat(journal.path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or (directory.st_dev, directory.st_ino) != journal._identity
        or stat.S_IMODE(directory.st_mode) != 0o700
        or directory.st_uid != os.geteuid()
        or journal._names() != {"journal.meta", ".journal.lock", "entries.log", "HEAD", _PENDING}
    ):
        raise JournalError("corrupt")
    for name, expected in (
        (".journal.lock", journal._lock_identity),
        ("journal.meta", journal._meta_identity),
        ("entries.log", journal._log_identity),
        (_PENDING, (pending.st_dev, pending.st_ino)),
    ):
        fd = journal._open(name, os.O_RDONLY)
        try:
            info = _safe_file(fd)
            if (info.st_dev, info.st_ino) != expected:
                raise JournalError("corrupt")
        finally:
            os.close(fd)


def promote_container_v2(directory: Path, journal_id: UUID) -> None:
    """Promote a verified v1 container once, under offline operator control.

    A failure after temporary creation is unknown even if the old metadata is
    still visible. Preserve evidence and investigate; never erase a temporary or
    retry against a possibly promoted directory. A new mixed reader must verify
    and sync metadata/directory before admission after a crash past replacement.
    This function neither supplies that reader nor enables runtime composition.
    """
    with Journal(directory, journal_id) as journal, journal._locked():
        journal._scan()  # Exact v1 metadata, all frames, HEAD, and durability.
        journal._check_identity()
        journal._small("journal.meta")  # Recheck pinned original metadata.
        fd = os.open(
            _PENDING,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=journal._dir,
        )
        try:
            try:
                pending = _safe_file(fd)
                _write_all(fd, _canonical({"version": 2, "journal_id": str(journal_id)}))
                os.fsync(fd)
            finally:
                os.close(fd)  # Never retry close on a potentially reused number.
            _verify_promotion_paths(journal, pending)
            os.replace(_PENDING, "journal.meta", src_dir_fd=journal._dir, dst_dir_fd=journal._dir)
            os.fsync(journal._dir)
        except (OSError, JournalError):
            raise JournalError("unknown_upgrade") from None
        # Discard this instance: its metadata pin deliberately remains the old one.
