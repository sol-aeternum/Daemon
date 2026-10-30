"""Explicit uncalled C1 mixed journal I/O, not a startup applier or authority.

Container 1 admits only frozen v1 frames. Container 2 admits those plus N0 births.
Production Journal and its startup barrier remain v1-only. Every operation scans
and syncs the whole log under the same persistent lock; views never admit writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from .errors import JournalError
from .model import Command, Entry, command_digest, decode_entry, encode_entry
from .namespace_candidate import (
    MixedNamespaceJournal,
    NamespaceCommand,
    NamespaceEntry,
    decode_candidate_entry,
    encode_frame,
    encode_namespace_entry,
    namespace_command_digest,
)
from .store import Journal, _write_all

Record = Entry | NamespaceEntry
_ZERO = bytes(32)


@dataclass(frozen=True, slots=True)
class MixedView:
    """Immutable observation of verified bytes; not permission to apply effects."""

    entries: tuple[Record, ...]
    container_version: int


class MixedJournal:
    def __init__(self, directory: Path, journal_id: UUID) -> None:
        self._journal = Journal(directory, journal_id)

    def close(self) -> None:
        self._journal.close()

    def __enter__(self) -> MixedJournal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _scan(self) -> tuple[MixedView, MixedNamespaceJournal]:
        journal = self._journal
        meta = journal._small("journal.meta")
        version = meta.get("version")
        if (
            type(version) is not int
            or version not in (1, 2)
            or meta != {"version": version, "journal_id": str(journal.journal_id)}
        ):
            raise JournalError("corrupt")
        state = MixedNamespaceJournal()
        entries: list[Record] = []
        with journal._scan_log() as fd:
            for payload, digest in journal._frames(fd):
                try:
                    entry = (
                        decode_entry(payload, digest)
                        if version == 1
                        else decode_candidate_entry(payload, digest)
                    )
                    if entry.journal_id != journal.journal_id:
                        raise JournalError("corrupt")
                    self._accept(state, entry)
                except JournalError:
                    raise JournalError("corrupt") from None
                entries.append(entry)
            journal._finish_scan(fd, entries)
        # Reopen and check the original metadata pin before syncing a promotion
        # whose replace may have completed before its directory fsync failed.
        if journal._small("journal.meta") != meta:
            raise JournalError("corrupt")
        metadata_fd = journal._open("journal.meta", os.O_RDONLY)
        try:
            info = os.fstat(metadata_fd)
            if (info.st_dev, info.st_ino) != journal._meta_identity:
                raise JournalError("corrupt")
            os.fsync(metadata_fd)
        finally:
            os.close(metadata_fd)
        os.fsync(journal._dir)
        return MixedView(tuple(entries), version), state

    @staticmethod
    def _accept(state: MixedNamespaceJournal, entry: Record) -> None:
        if isinstance(entry, NamespaceEntry):
            state.accept_namespace_entry(entry)
        else:
            state.accept_entry(entry)

    def verify(self) -> MixedView:
        with self._journal._locked():
            view, _ = self._scan()
            return view

    def append(
        self,
        command: Command | NamespaceCommand,
        event_id: UUID,
        expected_account_seq: int | None,
        expected_operation_seq: int | None,
    ) -> Record:
        if type(command) not in (Command, NamespaceCommand) or not isinstance(event_id, UUID):
            raise JournalError("invalid_entry")
        journal = self._journal
        with journal._locked():
            view, state = self._scan()
            if isinstance(command, NamespaceCommand):
                if view.container_version != 2 or expected_operation_seq is not None:
                    raise JournalError("invalid_entry")
                digest = namespace_command_digest(command)
            else:
                digest = command_digest(command)
            for earlier in view.entries:
                if earlier.event_id == event_id:
                    if earlier.command_digest != digest:
                        raise JournalError("conflict")
                    return earlier
            entry: Record
            if isinstance(command, NamespaceCommand):
                entry = NamespaceEntry(
                    journal.journal_id,
                    state.frame_count + 1,
                    event_id,
                    command,
                    expected_account_seq,
                    state.head_digest,
                    digest,
                    _ZERO,
                )
                payload = encode_namespace_entry(entry)
            else:
                entry = Entry(
                    journal.journal_id,
                    state.frame_count + 1,
                    event_id,
                    command,
                    expected_account_seq,
                    expected_operation_seq,
                    state.head_digest,
                    digest,
                    _ZERO,
                )
                payload = encode_entry(entry)
            frame = encode_frame(payload)
            entry = replace(entry, digest=frame[-32:])
            self._accept(state, entry)  # All closed grammar/CAS checks before writes.
            fd = journal._open("entries.log", os.O_WRONLY | os.O_APPEND)
            try:
                try:
                    _write_all(fd, frame)
                    os.fsync(fd)
                    journal._publish_head(entry.seq, entry.digest)
                except OSError:
                    raise JournalError("unknown_append") from None
            finally:
                os.close(fd)
            return entry
