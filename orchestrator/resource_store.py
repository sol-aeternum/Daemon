"""Uncalled trusted C1 registry/publication service, not a production principal.

R0 is reserved separately through its unchanged restricted-role wrapper. This
service reads actual committed intents and rechecks SQL lifecycle authority in
its own trusted publication transaction. No receipts or supplied views grant I/O.
No automatic unknown-effect retry, runtime composition, cleanup or lifecycle replay.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

from orchestrator.artifact_envelope import ArtifactBinding, ArtifactEnvelopeCodec, MAX_CONTENT_BYTES
from orchestrator.artifact_namespaces import NamespaceController, _topology
from orchestrator.artifact_reservations import (
    DescriptorCodec,
    IntentBinding,
    IntentCommand,
    JobBinding,
    ReservationError,
)
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_errors import ResourceError as ResourceError
from orchestrator.resource_storage import (
    StorageError,
    StoredBinding,
    prepare_verified,
    read_verified,
    repair_verified,
)
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write
from orchestrator.services.account_control.store import Connection, lock_account_control
from orchestrator.services.deletion_journal.barrier import _io
from orchestrator.services.deletion_journal.errors import JournalError


@dataclass(frozen=True, slots=True)
class Publication:
    resource_id: UUID
    version_id: UUID
    reference_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class ResourceContent:
    publication: Publication
    representation: str
    mime: str
    name: str | None
    content: bytes = field(repr=False)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ResourceError()
        value[key] = item
    return value


class ResourceStore:
    """Trusted injected registry pool; no R0 wrapper calls or reservation writes."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        root: Path,
        journal_path: Path,
        journal_id: UUID,
        cipher: Fernet,
    ) -> None:
        if not isinstance(cipher, Fernet):
            raise ResourceError("invalid")
        self._namespaces = NamespaceController(pool, root, journal_path, journal_id)
        self._root = root
        self._cipher = cipher
        self._envelope = ArtifactEnvelopeCodec(cipher)
        self._descriptors = DescriptorCodec(cipher)

    def _seal(self, value: dict[str, Any]) -> str:
        raw = _canonical(value)
        if len(raw) > 65536:
            raise ResourceError("invalid")
        return self._cipher.encrypt(raw).decode("ascii")

    def _open(self, token: str) -> dict[str, Any]:
        try:
            if type(token) is not str or not 1 <= len(token) <= 131072 or not token.isascii():
                raise ResourceError()
            encoded = token.encode("ascii")
            raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw) != encoded:
                raise ResourceError()
            plaintext = self._cipher.decrypt(encoded)
            if len(plaintext) > 65536:
                raise ResourceError()
            value = json.loads(plaintext, object_pairs_hook=_pairs)
            if type(value) is not dict or _canonical(value) != plaintext:
                raise ResourceError()
            return value
        except (InvalidToken, ValueError, UnicodeError, TypeError, RecursionError):
            raise ResourceError() from None

    @asynccontextmanager
    async def _scope(
        self,
        expected: CapturedGeneration,
        owned: tuple[UUID, UUID] | None = None,
    ) -> AsyncIterator[tuple[Connection, str]]:
        if type(expected) is not CapturedGeneration:
            raise ResourceError("invalid")
        CapturedGeneration(expected.account_id, expected.generation)
        try:
            async with self._namespaces._transaction() as conn:
                async with (
                    lock_account_control(conn, expected.account_id),
                    guard_content_write(conn, expected),
                ):
                    if (
                        owned is not None
                        and await conn.fetchval(
                            "SELECT version_id FROM resource_versions WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND generation=$4",
                            expected.account_id,
                            *owned,
                            expected.generation,
                        )
                        is None
                    ):
                        raise ResourceError("not_found")
                    states = await self._namespaces._inventory(conn, await self._namespaces._scan())
                    row = await conn.fetchrow(
                        "SELECT namespace FROM artifact_namespaces WHERE account_id=$1 AND generation=$2",
                        expected.account_id,
                        expected.generation,
                    )
                    if row is None or states.get(row["namespace"]) != "ready":
                        raise ResourceError()
                    yield conn, row["namespace"]
        except (
            asyncpg.PostgresError,
            asyncpg.InterfaceError,
            OSError,
            JournalError,
            StorageError,
            ReservationError,
        ):
            raise ResourceError() from None

    def _record_matches(self, token: str, expected: dict[str, Any]) -> bool:
        # Canonical bytes distinguish booleans from integers (Python dict equality
        # does not), as well as preserving the exact authenticated closed shape.
        return _canonical(self._open(token)) == _canonical(expected)

    @staticmethod
    async def _live(conn: Connection, identity: AttemptIdentity) -> None:
        # Separate trusted publication path, NOT the restricted Python wrapper.
        await conn.execute(
            "SELECT public.attempt_lifecycle_assert_open($1::jsonb)", identity.wire()
        )

    async def _intent(
        self,
        conn: Connection,
        expected: CapturedGeneration,
        intent_id: UUID,
    ) -> tuple[IntentCommand, str]:
        rows = []
        for table, kind in (
            ("artifact_output_intents", "output"),
            ("resource_version_intents", "append"),
        ):
            row = await conn.fetchrow(
                f"SELECT * FROM {table} WHERE account_id=$1 AND intent_id=$2",
                expected.account_id,
                intent_id,
            )
            if row is not None:
                rows.append((row, kind))
        if not rows:
            raise ResourceError("not_found")
        if len(rows) != 1:
            raise ResourceError()
        row, kind = rows[0]
        other_table = "resource_version_intents" if kind == "output" else "artifact_output_intents"
        if (
            await conn.fetchval(
                f"SELECT intent_id FROM {other_table} WHERE account_id=$1 AND job_id=$2 AND output_slot=$3",
                expected.account_id,
                row["job_id"],
                row["output_slot"],
            )
            is not None
        ):
            raise ResourceError("conflict")
        job = await conn.fetchrow(
            "SELECT * FROM artifact_output_jobs WHERE account_id=$1 AND job_id=$2",
            expected.account_id,
            row["job_id"],
        )
        if job is None or (job["account_generation"], job["task_id"]) != (
            expected.generation,
            row["task_id"],
        ):
            raise ResourceError()
        generation = row["account_generation"] if kind == "output" else row["generation"]
        if generation != expected.generation or (
            kind == "append" and row["operation_id"] != job["operation_id"]
        ):
            raise ResourceError()
        command = IntentCommand(
            JobBinding(
                job["job_id"], expected.account_id, generation, job["task_id"], job["operation_id"]
            ),
            IntentBinding(
                row["intent_id"],
                row["output_slot"],
                row["resource_id"],
                row["version_id"],
                row["lifecycle_id"],
                row["attempt_id"],
                row["attempt_epoch"],
                row["measured_bytes"],
            ),
            row["descriptor_ciphertext"],
        )
        self._descriptors.validate(command)
        if row["representation"] != "generated_file" or row["mime"] != "text/csv":
            raise ResourceError()
        return command, kind

    @staticmethod
    def _matches_identity(command: IntentCommand, identity: AttemptIdentity) -> None:
        j, b = command.job, command.binding
        if (j.account_id, j.account_generation, j.task_id, b.lifecycle_id, b.attempt_epoch) != (
            identity.account_id,
            identity.generation,
            identity.task_id,
            identity.lifecycle_id,
            identity.epoch,
        ):
            raise ResourceError("refused")

    async def _sources(self, conn: Connection, command: IntentCommand) -> tuple[UUID, UUID]:
        task = await conn.fetchrow(
            "SELECT * FROM tasks WHERE id=$1 FOR UPDATE", command.job.task_id
        )
        if task is None or task["user_id"] != command.job.account_id:
            raise ResourceError("refused")
        conversation, message = task["conversation_id"], task["result_message_id"]
        if (
            await conn.fetchval(
                "SELECT id FROM conversations WHERE id=$1 AND user_id=$2 FOR SHARE",
                conversation,
                command.job.account_id,
            )
            is None
            or await conn.fetchval(
                "SELECT id FROM messages WHERE id=$1 AND conversation_id=$2 AND user_id=$3 AND role='assistant' FOR SHARE",
                message,
                conversation,
                command.job.account_id,
            )
            is None
        ):
            raise ResourceError("refused")
        if (
            await conn.fetchval(
                "SELECT id FROM task_attempts WHERE id=$1 AND task_id=$2 AND epoch=$3 FOR UPDATE",
                command.binding.attempt_id,
                command.job.task_id,
                command.binding.attempt_epoch,
            )
            is None
        ):
            raise ResourceError("refused")
        return conversation, message

    def _stored(self, command: IntentCommand, namespace: str) -> StoredBinding:
        descriptor = self._descriptors.decode(command)
        j, b = command.job, command.binding
        return StoredBinding(
            ArtifactBinding(
                str(j.account_id),
                str(b.resource_id),
                str(b.version_id),
                j.account_generation,
                "generated_file",
            ),
            namespace,
            b.intent_id.hex,
            b.version_id.hex,
            b.measured_bytes,
            descriptor["sha256"],
        )

    @staticmethod
    def _binding_fields(stored: StoredBinding, source: str) -> dict[str, Any]:
        a = stored.artifact
        return {
            "schema_version": 1,
            "account_id": a.account_id,
            "resource_id": a.resource_id,
            "version_id": a.version_id,
            "generation": a.generation,
            "representation": a.representation,
            "namespace": stored.namespace,
            "intent_id": str(UUID(hex=stored.intent)),
            "storage_name": stored.public_name,
            "measured_bytes": stored.measured_bytes,
            "sha256": stored.sha256,
            "source_kind": source,
        }

    async def _publication(
        self,
        conn: Connection,
        expected: CapturedGeneration,
        resource: UUID,
        version: UUID,
        namespace: str,
    ) -> tuple[Publication, StoredBinding, dict[str, Any], IntentCommand, str]:
        row = await conn.fetchrow(
            "SELECT v.*,p.namespace,p.intent_id,p.source_kind,p.binding_ciphertext "
            "FROM resource_versions v JOIN resource_publications p USING(version_id) "
            "WHERE v.account_id=$1 AND v.resource_id=$2 AND v.version_id=$3 AND v.generation=$4",
            expected.account_id,
            resource,
            version,
            expected.generation,
        )
        if row is None:
            raise ResourceError("not_found")
        command, kind = await self._intent(conn, expected, row["intent_id"])
        if (command.binding.resource_id, command.binding.version_id, row["namespace"], kind) != (
            resource,
            version,
            namespace,
            row["source_kind"],
        ):
            raise ResourceError()
        stored = self._stored(command, namespace)
        if _canonical(self._open(row["binding_ciphertext"])) != _canonical(
            self._binding_fields(stored, kind)
        ):
            raise ResourceError()
        descriptor = self._descriptors.decode(command)
        metadata = self._metadata(command, descriptor)
        if _canonical(self._open(row["metadata_ciphertext"])) != _canonical(metadata):
            raise ResourceError()
        if (row["representation"], row["kind"], row["mime"], row["measured_bytes"]) != (
            "generated_file",
            "file",
            "text/csv",
            stored.measured_bytes,
        ):
            raise ResourceError()
        refs = await conn.fetch(
            "SELECT * FROM resource_references WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND role='output'",
            expected.account_id,
            resource,
            version,
        )
        if len(refs) != 1:
            raise ResourceError()
        ref = refs[0]
        provenance = self._open(ref["provenance_ciphertext"])
        original = self._provenance(
            command,
            provenance.get("original_conversation_id"),
            provenance.get("original_message_id"),
            ref["ordinal"],
            provenance.get("source_deleted"),
        )
        if _canonical(provenance) != _canonical(original):
            raise ResourceError()
        if (
            type(provenance["source_deleted"]) is not bool
            or ref["job_id"] != command.job.job_id
            or ref["generation"] != expected.generation
        ):
            raise ResourceError()
        if provenance["source_deleted"]:
            if any(ref[k] is not None for k in ("task_id", "conversation_id", "message_id")):
                raise ResourceError()
        elif (str(ref["task_id"]), str(ref["conversation_id"]), str(ref["message_id"])) != (
            str(command.job.task_id),
            provenance["original_conversation_id"],
            provenance["original_message_id"],
        ):
            raise ResourceError()
        else:
            conversation, message = await self._sources(conn, command)
            if (str(conversation), str(message)) != (
                provenance["original_conversation_id"],
                provenance["original_message_id"],
            ):
                raise ResourceError()
        return (
            Publication(resource, version, ref["reference_id"], row["created_at"]),
            stored,
            metadata,
            command,
            kind,
        )

    @staticmethod
    def _metadata(command: IntentCommand, descriptor: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "account_id": str(command.job.account_id),
            "resource_id": str(command.binding.resource_id),
            "version_id": str(command.binding.version_id),
            "generation": command.job.account_generation,
            "representation": "generated_file",
            "kind": "file",
            "mime": "text/csv",
            "measured_bytes": command.binding.measured_bytes,
            "name": descriptor["filename"],
            "title": None,
            "sha256": descriptor["sha256"],
            "provenance": descriptor["provenance"],
        }

    @staticmethod
    def _provenance(
        command: IntentCommand,
        conversation: Any,
        message: Any,
        ordinal: int,
        deleted: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "account_id": str(command.job.account_id),
            "resource_id": str(command.binding.resource_id),
            "version_id": str(command.binding.version_id),
            "generation": command.job.account_generation,
            "original_task_id": str(command.job.task_id),
            "original_conversation_id": str(conversation),
            "original_message_id": str(message),
            "original_job_id": str(command.job.job_id),
            "original_attempt_id": str(command.binding.attempt_id),
            "role": "output",
            "ordinal": ordinal,
            "source_deleted": deleted,
        }

    async def publish_output(
        self,
        identity: AttemptIdentity,
        intent_id: UUID,
        content: bytes,
    ) -> Publication:
        """Commit pinned catalog records only; explicit repair is a separate call."""
        identity.wire()
        if (
            not isinstance(intent_id, UUID)
            or type(content) is not bytes
            or len(content) > MAX_CONTENT_BYTES
        ):
            raise ResourceError("invalid")
        expected = CapturedGeneration(identity.account_id, identity.generation)
        async with self._scope(expected) as (conn, namespace):
            command, kind = await self._intent(conn, expected, intent_id)
            self._matches_identity(command, identity)
            stored = self._stored(command, namespace)
            if (
                len(content) != stored.measured_bytes
                or hashlib.sha256(content).hexdigest() != stored.sha256
            ):
                raise ResourceError("conflict")
            exists = await conn.fetchval(
                "SELECT version_id FROM resource_publications WHERE account_id=$1 AND intent_id=$2",
                expected.account_id,
                intent_id,
            )
            if exists is not None:
                pub, verified, _, _, _ = await self._publication(
                    conn,
                    expected,
                    command.binding.resource_id,
                    command.binding.version_id,
                    namespace,
                )
                await _io(lambda: read_verified(self._root, self._envelope, verified))
                return pub  # Historical replay, no new effects/lease refresh/repair.
            await self._live(conn, identity)
            # No app-pool transaction or supplied stage observation is held here.
            await conn.fetchrow(
                "SELECT job_id FROM artifact_output_jobs WHERE job_id=$1 FOR UPDATE",
                command.job.job_id,
            )
            table = "artifact_output_intents" if kind == "output" else "resource_version_intents"
            await conn.fetchrow(
                f"SELECT intent_id FROM {table} WHERE intent_id=$1 FOR UPDATE", intent_id
            )
            existing_resource = await conn.fetchrow(
                "SELECT resource_id FROM resources WHERE account_id=$1 AND resource_id=$2 FOR UPDATE",
                expected.account_id,
                command.binding.resource_id,
            )
            if (kind == "append") != (existing_resource is not None):
                raise ResourceError("conflict")
            await conn.execute(
                "INSERT INTO resource_catalog_heads(account_id,generation) VALUES($1,$2) ON CONFLICT DO NOTHING",
                expected.account_id,
                expected.generation,
            )
            head = await conn.fetchrow(
                "SELECT * FROM resource_catalog_heads WHERE account_id=$1 FOR UPDATE",
                expected.account_id,
            )
            if head is None or head["generation"] != expected.generation:
                raise ResourceError("refused")
            conversation, message = await self._sources(conn, command)
            ordinal = await conn.fetchval(
                "SELECT COALESCE(max(ordinal),-1)+1 FROM resource_references WHERE account_id=$1 AND task_id=$2 AND role='output'",
                expected.account_id,
                command.job.task_id,
            )
            if type(ordinal) is not int or not 0 <= ordinal <= 2**31 - 1:
                raise ResourceError()
            await self._live(conn, identity)
            await _io(lambda: _topology(self._root, namespace, create=False))
            await _io(lambda: prepare_verified(self._root, self._envelope, stored, content))
            counter = head["insertion_counter"] + 1
            if kind == "output":
                await conn.execute(
                    "INSERT INTO resources(resource_id,account_id,insertion_ordinal) VALUES($1,$2,$3)",
                    command.binding.resource_id,
                    expected.account_id,
                    counter,
                )
            created = await conn.fetchval(
                "INSERT INTO resource_versions(version_id,account_id,resource_id,generation,representation,kind,mime,measured_bytes,metadata_ciphertext,insertion_ordinal) "
                "VALUES($1,$2,$3,$4,'generated_file','file','text/csv',$5,$6,$7) RETURNING created_at",
                command.binding.version_id,
                expected.account_id,
                command.binding.resource_id,
                expected.generation,
                stored.measured_bytes,
                self._seal(self._metadata(command, self._descriptors.decode(command))),
                counter,
            )
            if not isinstance(created, datetime) or created.utcoffset() is None:
                raise ResourceError()
            await conn.execute(
                "INSERT INTO resource_publications(version_id,account_id,resource_id,generation,namespace,intent_id,source_kind,representation,measured_bytes,binding_ciphertext,insertion_ordinal) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,'generated_file',$8,$9,$10)",
                command.binding.version_id,
                expected.account_id,
                command.binding.resource_id,
                expected.generation,
                namespace,
                intent_id,
                kind,
                stored.measured_bytes,
                self._seal(self._binding_fields(stored, kind)),
                counter,
            )
            reference = uuid4()
            await conn.execute(
                "INSERT INTO resource_references(reference_id,account_id,resource_id,version_id,generation,role,ordinal,task_id,conversation_id,message_id,job_id,provenance_ciphertext) "
                "VALUES($1,$2,$3,$4,$5,'output',$6,$7,$8,$9,$10,$11)",
                reference,
                expected.account_id,
                command.binding.resource_id,
                command.binding.version_id,
                expected.generation,
                ordinal,
                command.job.task_id,
                conversation,
                message,
                command.job.job_id,
                self._seal(self._provenance(command, conversation, message, ordinal, False)),
            )
            await conn.execute(
                "UPDATE resource_catalog_heads SET insertion_counter=$2 WHERE account_id=$1",
                expected.account_id,
                counter,
            )
            await self._live(conn, identity)  # Includes FS duration and all INSERT/FK/unique waits.
            result = Publication(
                command.binding.resource_id, command.binding.version_id, reference, created
            )
        return result  # Owned transaction commit must be known successful first.

    async def read(
        self, expected: CapturedGeneration, resource: UUID, version: UUID
    ) -> ResourceContent:
        if not isinstance(resource, UUID) or not isinstance(version, UUID):
            raise ResourceError("not_found")
        async with self._scope(expected, (resource, version)) as (conn, namespace):
            representation = await conn.fetchval(
                "SELECT representation FROM resource_versions WHERE account_id=$1 AND version_id=$2",
                expected.account_id,
                version,
            )
            if representation != "generated_file":
                publication, stored, metadata = await self._input_publication(
                    conn, expected, resource, version, namespace
                )
                if stored is None:
                    raise ResourceError("original_not_received")
            else:
                publication, stored, metadata, _, _ = await self._publication(
                    conn, expected, resource, version, namespace
                )
            content = await _io(lambda: read_verified(self._root, self._envelope, stored))
            result = ResourceContent(
                publication,
                metadata["representation"],
                metadata["mime"],
                metadata.get("name", metadata.get("supplied", {}).get("name")),
                content,
            )
        return result

    async def _input_publication(
        self,
        conn: Connection,
        expected: CapturedGeneration,
        resource: UUID,
        version: UUID,
        namespace: str,
    ) -> tuple[Publication, StoredBinding | None, dict[str, Any]]:
        """Authenticate committed input using retained intents, never a batch receipt.

        Canonical chat input is deliberately unnecessary after source deletion.
        The trusted same-account writer is the source association boundary, not FK.
        """
        row = await conn.fetchrow(
            "SELECT * FROM resource_input_intents WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND generation=$4",
            expected.account_id,
            resource,
            version,
            expected.generation,
        )
        if row is None:
            raise ResourceError()
        batch = await conn.fetchrow(
            "SELECT * FROM resource_input_batches WHERE account_id=$1 AND batch_id=$2 AND generation=$3",
            expected.account_id,
            row["batch_id"],
            expected.generation,
        )
        if batch is None or batch["state"] not in ("accepted", "source_deleted"):
            raise ResourceError()
        descriptor = self._open(row["descriptor_ciphertext"])
        expected_descriptor = {
            "schema_version": 1,
            "account_id": str(expected.account_id),
            "batch_id": str(batch["batch_id"]),
            "task_id": str(batch["task_id"]),
            "generation": expected.generation,
            "intent_id": str(row["intent_id"]),
            "resource_id": str(resource),
            "version_id": str(version),
            "ordinal": row["ordinal"],
            "representation": row["representation"],
            "kind": row["kind"],
            "mime": row["mime"],
            "measured_bytes": row["measured_bytes"],
            "sha256": descriptor.get("sha256"),
            "supplied": descriptor.get("supplied"),
        }
        if (
            _canonical(descriptor) != _canonical(expected_descriptor)
            or type(descriptor["supplied"]) is not dict
        ):
            raise ResourceError()
        metadata = {
            k: descriptor[k]
            for k in (
                "schema_version",
                "account_id",
                "resource_id",
                "version_id",
                "generation",
                "representation",
                "kind",
                "mime",
                "measured_bytes",
                "sha256",
                "supplied",
            )
        }
        v = await conn.fetchrow(
            "SELECT * FROM resource_versions WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND generation=$4",
            expected.account_id,
            resource,
            version,
            expected.generation,
        )
        if (
            v is None
            or not self._record_matches(v["metadata_ciphertext"], metadata)
            or any(v[k] != row[k] for k in ("representation", "kind", "mime", "measured_bytes"))
        ):
            raise ResourceError()
        refs = await conn.fetch(
            "SELECT * FROM resource_references WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND role='input'",
            expected.account_id,
            resource,
            version,
        )
        if len(refs) != 1:
            raise ResourceError()
        ref = refs[0]
        provenance = self._open(ref["provenance_ciphertext"])
        original = {
            "schema_version": 1,
            "account_id": str(expected.account_id),
            "resource_id": str(resource),
            "version_id": str(version),
            "generation": expected.generation,
            "original_task_id": str(batch["task_id"]),
            "original_conversation_id": provenance.get("original_conversation_id"),
            "original_message_id": provenance.get("original_message_id"),
            "role": "input",
            "ordinal": row["ordinal"],
            "source_deleted": batch["state"] == "source_deleted",
        }
        if (
            _canonical(provenance) != _canonical(original)
            or ref["ordinal"] != row["ordinal"]
            or ref["generation"] != expected.generation
            or ref["job_id"] is not None
        ):
            raise ResourceError()
        try:
            conversation = UUID(original["original_conversation_id"])
            message = UUID(original["original_message_id"])
        except (ValueError, TypeError, AttributeError):
            raise ResourceError() from None
        if original["source_deleted"]:
            if (
                batch["accepted_task_id"] is not None
                or batch["request_ciphertext"] is not None
                or any(ref[k] is not None for k in ("task_id", "conversation_id", "message_id"))
            ):
                raise ResourceError()
        else:
            if (
                batch["accepted_task_id"] != batch["task_id"]
                or batch["request_ciphertext"] is None
                or (ref["task_id"], ref["conversation_id"], ref["message_id"])
                != (batch["task_id"], conversation, message)
                or not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM tasks t JOIN conversations c ON c.id=t.conversation_id "
                    "JOIN messages m ON m.id=t.user_message_id WHERE t.id=$1 AND t.user_id=$2 AND c.user_id=$2 "
                    "AND m.user_id=$2 AND t.conversation_id=$3 AND m.conversation_id=$3 AND t.user_message_id=$4 AND m.role='user')",
                    batch["task_id"],
                    expected.account_id,
                    conversation,
                    message,
                )
            ):
                raise ResourceError()
        p = await conn.fetchrow(
            "SELECT * FROM resource_publications WHERE account_id=$1 AND version_id=$2",
            expected.account_id,
            version,
        )
        stored = None
        if row["representation"] == "metadata_only":
            if (
                p is not None
                or row["measured_bytes"] is not None
                or descriptor["sha256"] is not None
            ):
                raise ResourceError()
        else:
            stored = StoredBinding(
                ArtifactBinding(
                    str(expected.account_id),
                    str(resource),
                    str(version),
                    expected.generation,
                    row["representation"],
                ),
                namespace,
                row["intent_id"].hex,
                version.hex,
                row["measured_bytes"],
                descriptor["sha256"],
            )
            stored.validate()
            if (
                p is None
                or (
                    p["resource_id"],
                    p["generation"],
                    p["namespace"],
                    p["intent_id"],
                    p["source_kind"],
                    p["representation"],
                    p["measured_bytes"],
                    p["insertion_ordinal"],
                )
                != (
                    resource,
                    expected.generation,
                    namespace,
                    row["intent_id"],
                    "input",
                    row["representation"],
                    row["measured_bytes"],
                    v["insertion_ordinal"],
                )
                or not self._record_matches(
                    p["binding_ciphertext"], self._binding_fields(stored, "input")
                )
            ):
                raise ResourceError()
        return (
            Publication(resource, version, ref["reference_id"], v["created_at"]),
            stored,
            metadata,
        )

    async def repair(self, expected: CapturedGeneration, resource: UUID, version: UUID) -> None:
        if not isinstance(resource, UUID) or not isinstance(version, UUID):
            raise ResourceError("not_found")
        async with self._scope(expected, (resource, version)) as (conn, namespace):
            representation = await conn.fetchval(
                "SELECT representation FROM resource_versions WHERE account_id=$1 AND version_id=$2",
                expected.account_id,
                version,
            )
            if representation != "generated_file":
                _, stored, _ = await self._input_publication(
                    conn, expected, resource, version, namespace
                )
                if stored is None:
                    raise ResourceError("original_not_received")
            else:
                _, stored, _, _, _ = await self._publication(
                    conn, expected, resource, version, namespace
                )
            await _io(lambda: repair_verified(self._root, self._envelope, stored))

    async def reserve_version(
        self,
        identity: AttemptIdentity,
        job: JobBinding,
        resource: UUID,
        output_slot: int,
        content: bytes,
        filename: str,
    ) -> UUID:
        """Resolve stable append operation before allocating a new version identity."""
        identity.wire()
        job.wire()
        if (job.account_id, job.account_generation, job.task_id) != (
            identity.account_id,
            identity.generation,
            identity.task_id,
        ):
            raise ResourceError("refused")
        if (
            not isinstance(resource, UUID)
            or type(output_slot) is not int
            or not 0 <= output_slot <= 2**31 - 1
            or type(content) is not bytes
            or len(content) > MAX_CONTENT_BYTES
        ):
            raise ResourceError("invalid")
        expected = CapturedGeneration(identity.account_id, identity.generation)
        digest = hashlib.sha256(content).hexdigest()
        async with self._scope(expected) as (conn, _):
            # No FS or identity allocation on stable-key lookup/replay.
            existing = await conn.fetchrow(
                "SELECT intent_id FROM resource_version_intents WHERE account_id=$1 AND operation_id=$2 AND output_slot=$3",
                expected.account_id,
                job.operation_id,
                output_slot,
            )
            if existing:
                command, _ = await self._intent(conn, expected, existing["intent_id"])
                self._matches_identity(command, identity)
                d = self._descriptors.decode(command)
                if (
                    command.job != job
                    or command.binding.resource_id != resource
                    or command.binding.measured_bytes != len(content)
                    or d["filename"] != filename
                    or d["sha256"] != digest
                ):
                    raise ResourceError("conflict")
                return command.binding.intent_id
            await self._live(conn, identity)
            actual_job = await conn.fetchrow(
                "SELECT * FROM artifact_output_jobs WHERE account_id=$1 AND job_id=$2 FOR UPDATE",
                expected.account_id,
                job.job_id,
            )
            if actual_job is None or any(
                actual_job[k] != v
                for k, v in (
                    ("task_id", job.task_id),
                    ("operation_id", job.operation_id),
                    ("account_generation", job.account_generation),
                )
            ):
                raise ResourceError("refused")
            if (
                await conn.fetchval(
                    "SELECT intent_id FROM artifact_output_intents WHERE account_id=$1 AND job_id=$2 AND output_slot=$3 FOR UPDATE",
                    expected.account_id,
                    job.job_id,
                    output_slot,
                )
                is not None
            ):
                raise ResourceError("conflict")
            if (
                await conn.fetchval(
                    "SELECT resource_id FROM resources WHERE account_id=$1 AND resource_id=$2 FOR UPDATE",
                    expected.account_id,
                    resource,
                )
                is None
            ):
                raise ResourceError("not_found")
            attempt = await conn.fetchval(
                "SELECT id FROM task_attempts WHERE task_id=$1 AND epoch=$2 FOR UPDATE",
                job.task_id,
                identity.epoch,
            )
            if attempt is None:
                raise ResourceError("refused")
            binding = IntentBinding(
                uuid4(),
                output_slot,
                resource,
                uuid4(),
                identity.lifecycle_id,
                attempt,
                identity.epoch,
                len(content),
            )
            command = self._descriptors.encode(job, binding, filename=filename, sha256=digest)
            await conn.execute(
                "INSERT INTO resource_version_intents(intent_id,account_id,generation,resource_id,version_id,task_id,job_id,operation_id,output_slot,lifecycle_id,attempt_id,attempt_epoch,representation,mime,measured_bytes,descriptor_ciphertext) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'generated_file','text/csv',$13,$14)",
                binding.intent_id,
                job.account_id,
                job.account_generation,
                resource,
                binding.version_id,
                job.task_id,
                job.job_id,
                job.operation_id,
                output_slot,
                identity.lifecycle_id,
                attempt,
                identity.epoch,
                len(content),
                command.descriptor_ciphertext,
            )
            await self._live(conn, identity)
        return binding.intent_id
