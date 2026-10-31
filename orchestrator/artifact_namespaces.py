"""Uncalled C1 namespace controller; no production readiness or lifecycle applier.

Trusted callers supply authenticated original account generation, not a view or
receipt. Two owned transactions commit inventory before effects, then revalidate
and verify physical readiness. Unknown commit is never automatically retried.
All v1 lifecycle history blocks this deliberately incomplete lifecycle composition.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg

from orchestrator.services.connection_cleanup import pool_connection
from orchestrator.artifact_staging import ArtifactStageError, _Handles
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write
from orchestrator.services.account_control.store import Connection, lock_account_control
from orchestrator.services.deletion_journal.barrier import _io
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedJournal, MixedView
from orchestrator.services.deletion_journal.model import Entry
from orchestrator.services.deletion_journal.namespace_candidate import (
    NamespaceCommand,
    NamespaceCreateFields,
    NamespaceEntry,
)


@dataclass(frozen=True, slots=True)
class NamespaceObservation:
    """Known committed readiness observation, never a transferable write grant."""

    account_id: UUID
    generation: int
    namespace: str


def _topology(root: Path, namespace: str, *, create: bool) -> None:
    """Owned synchronous descriptors; called only after committed birth inventory."""
    try:
        with _Handles() as handles:
            root_fd = handles.root(root)
            live = handles.directory(root_fd, "live")
            private = handles.directory(root_fd, "private")

            def directory(parent: int, name: str) -> int:
                if create:
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass  # Existing topology is fully checked below, never replaced.
                return handles.directory(parent, name)

            live_ns = directory(live, namespace)
            private_ns = directory(private, namespace)
            if create and (
                set(os.listdir(live_ns)) - {"staging"}
                or set(os.listdir(private_ns)) - {".prepare.lock"}
            ):
                raise JournalError("recovery_required")
            staging = directory(live_ns, "staging")
            if create and os.listdir(staging):
                raise JournalError("recovery_required")
            if create:
                try:
                    lock = handles.open(
                        ".prepare.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL, private_ns
                    )
                except FileExistsError:
                    lock = handles.open(".prepare.lock", os.O_RDWR, private_ns)
            else:
                lock = handles.open(".prepare.lock", os.O_RDWR, private_ns)
            handles.revalidate(root_fd, private_ns, lock)
            # Cooperates with staging. No database query/wait while this is held.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                handles.revalidate(root_fd, private_ns, lock)
                os.fsync(lock)
                for fd in (staging, live_ns, private_ns, live, private, root_fd):
                    os.fsync(fd)
                handles.revalidate(root_fd, private_ns, lock)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except (OSError, ArtifactStageError):
        raise JournalError("unavailable") from None


class NamespaceController:
    """Explicit trusted source composition only; no settings lookup or auto-init."""

    def __init__(self, pool: asyncpg.Pool, root: Path, journal_path: Path, journal_id: UUID):
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or not isinstance(journal_path, Path)
            or not journal_path.is_absolute()
            or not isinstance(journal_id, UUID)
        ):
            raise JournalError("invalid_config")
        self._pool, self._root = pool, root
        self._journal_path, self._journal_id = journal_path, journal_id

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[Connection]:
        """Discard an uncertain owned connection before pool cancellation/reset waits.

        No rollback/readback is sent on a failed channel. Termination releases the
        transaction server-side; a later call independently reconciles its outcome.
        Body cancellation reaches here only after any _io work has drained.
        """
        async with pool_connection(self._pool) as conn:
            try:
                if conn.is_in_transaction():
                    raise JournalError("conflict")
                transaction = conn.transaction(isolation="read_committed")
                await transaction.start()
                yield conn
                await transaction.commit()
            except BaseException:
                conn.terminate()
                raise

    async def _scan(self) -> MixedView:
        def scan() -> MixedView:
            with MixedJournal(self._journal_path, self._journal_id) as journal:
                return journal.verify()

        view = await _io(scan)
        if view.container_version != 2:
            raise JournalError("recovery_required")
        return view

    async def _inventory(self, conn: Connection, view: MixedView) -> dict[str, str]:
        if (
            await conn.fetchval(
                "SELECT journal_id FROM deletion_journal_binding WHERE singleton=true"
            )
            != self._journal_id
        ):
            raise JournalError("marker_mismatch")
        entries = {entry.seq: entry for entry in view.entries}
        marked: set[int] = set()
        for row in await conn.fetch("SELECT * FROM deletion_journal_applied"):
            entry = entries.get(row["seq"])
            operation = entry.command.operation_id if isinstance(entry, Entry) else None
            if (
                entry is None
                or row["journal_id"] != entry.journal_id
                or row["event_id"] != entry.event_id
                or row["account_id"] != entry.command.account_id
                or row["operation_id"] != operation
                or bytes(row["entry_digest"]) != entry.digest
            ):
                raise JournalError("marker_mismatch")
            marked.add(entry.seq)
        # Complete history is inspected before any DB projection or physical effect.
        if any(not isinstance(entry, NamespaceEntry) for entry in view.entries):
            raise JournalError("recovery_required")
        represented: set[int] = set()
        states: dict[str, str] = {}
        for row in await conn.fetch("SELECT * FROM artifact_namespaces"):
            birth = entries.get(row["birth_seq"])
            if not isinstance(birth, NamespaceEntry) or (
                row["journal_id"] != birth.journal_id
                or row["birth_event_id"] != birth.event_id
                or row["account_id"] != birth.command.account_id
                or row["generation"] != birth.command.fields.generation
                or row["namespace"] != birth.command.fields.namespace
                or bytes(row["birth_digest"]) != birth.digest
                or bytes(row["command_digest"]) != birth.command_digest
                or birth.seq not in marked
            ):
                raise JournalError("marker_mismatch")
            represented.add(birth.seq)
            states[row["namespace"]] = row["state"]
        if represented != marked:
            raise JournalError("marker_mismatch")
        return states

    async def ensure_namespace(self, expected: CapturedGeneration) -> NamespaceObservation:
        if type(expected) is not CapturedGeneration:
            raise JournalError("invalid_entry")
        # Validate even a mutated/forged dataclass before acquiring account locks.
        CapturedGeneration(expected.account_id, expected.generation)
        try:
            return await self._ensure(expected)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
            # Includes unknown COMMIT. No mutation retry or second identity here.
            raise JournalError("unavailable") from None

    async def _ensure(self, expected: CapturedGeneration) -> NamespaceObservation:
        async with self._transaction() as conn:
            async with (
                lock_account_control(conn, expected.account_id),
                guard_content_write(conn, expected),
            ):
                view = await self._scan()
                states = await self._inventory(conn, view)
                births = [
                    e
                    for e in view.entries
                    if isinstance(e, NamespaceEntry) and e.command.account_id == expected.account_id
                ]
                if any(e.command.fields.generation != expected.generation for e in births):
                    raise JournalError("conflict")
                if births:
                    birth = births[0]
                else:
                    command = NamespaceCommand(
                        expected.account_id,
                        "namespace_create_intended",
                        datetime.now(timezone.utc),
                        NamespaceCreateFields(expected.generation, uuid4().hex),
                    )
                    event = uuid4()

                    def append() -> NamespaceEntry:
                        with MixedJournal(self._journal_path, self._journal_id) as journal:
                            result = journal.append(command, event, None, None)
                            if not isinstance(result, NamespaceEntry):
                                raise JournalError("corrupt")
                            return result

                    birth = await _io(append)
                namespace = birth.command.fields.namespace
                if namespace not in states:
                    await conn.execute(
                        "INSERT INTO artifact_namespaces(namespace,account_id,generation,journal_id,"
                        "birth_seq,birth_event_id,birth_digest,command_digest,state) "
                        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,'provisioning')",
                        namespace,
                        expected.account_id,
                        expected.generation,
                        birth.journal_id,
                        birth.seq,
                        birth.event_id,
                        birth.digest,
                        birth.command_digest,
                    )
                    await conn.execute(
                        "INSERT INTO deletion_journal_applied(journal_id,seq,event_id,account_id,"
                        "operation_id,entry_digest) VALUES($1,$2,$3,$4,NULL,$5)",
                        birth.journal_id,
                        birth.seq,
                        birth.event_id,
                        expected.account_id,
                        birth.digest,
                    )
        # No physical effect until the inventory/marker transaction is known committed.
        async with self._transaction() as conn:
            async with (
                lock_account_control(conn, expected.account_id),
                guard_content_write(conn, expected),
            ):
                states = await self._inventory(conn, await self._scan())
                state = states.get(namespace)
                if state not in ("provisioning", "ready"):
                    raise JournalError("recovery_required")
                await _io(lambda: _topology(self._root, namespace, create=state == "provisioning"))
                await conn.execute(
                    "UPDATE artifact_namespaces SET state='ready' WHERE namespace=$1", namespace
                )
        return NamespaceObservation(expected.account_id, expected.generation, namespace)

    async def reconcile_all(self) -> tuple[NamespaceObservation, ...]:
        # No effects before the complete history and inventory have been checked.
        async with self._transaction() as conn:
            view = await self._scan()
            await self._inventory(conn, view)
        result: list[NamespaceObservation] = []
        for birth in view.entries:
            if not isinstance(birth, NamespaceEntry):
                raise JournalError("recovery_required")
            result.append(
                await self.ensure_namespace(
                    CapturedGeneration(birth.command.account_id, birth.command.fields.generation)
                )
            )
        # Re-scan to refuse a concurrently appended unsupported operation or birth;
        # never report a stale global recovery view as completed.
        async with self._transaction() as conn:
            final = await self._scan()
            await self._inventory(conn, final)
            if final.entries != view.entries:
                raise JournalError("recovery_required")
        return tuple(result)
