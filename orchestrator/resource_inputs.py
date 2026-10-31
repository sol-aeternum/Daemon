"""Uncalled C1 coordinator: durable input intents join the real task transaction.

The trusted isolated host supplies one actual pool, stores, key and finite limits.
No returned batch ID/observation grants authority. No automatic unknown-COMMIT
retry, runtime startup wiring, cleanup, inference or encryption-key rotation.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from cryptography.fernet import InvalidToken

from orchestrator.artifact_envelope import ArtifactBinding
from orchestrator.artifact_namespaces import _topology
from orchestrator.resource_input_codec import (
    InputLimits,
    InputLookup,
    ReceivedInput,
    received_inputs,
)
from orchestrator.resource_storage import StoredBinding, prepare_verified, read_verified
from orchestrator.resource_store import ResourceError, ResourceStore, _canonical
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    capture_generation,
    guard_content_write,
)
from orchestrator.services.account_control.store import Connection, lock_account_control
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.history_pagination import HistoryBoundary
from orchestrator.services.deletion_journal.barrier import _io
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.tasks.store import (
    _AcceptanceStage,
    CONVERSATION_ACTIVE_INDEX,
    AcceptedTask,
    ConversationBusy,
    IdempotencyConflict,
    TaskStatus,
    TaskStore,
)


class ResourceInputs:
    def __init__(
        self, store: ResourceStore, tasks: TaskStore, encryption_key: bytes, limits: InputLimits
    ) -> None:
        if (
            type(store) is not ResourceStore
            or type(tasks) is not TaskStore
            or tasks._pool is not store._namespaces._pool
            or tasks._memory._pool is not tasks._pool
            or type(limits) is not InputLimits
        ):
            raise ResourceError("invalid_composition")
        self._store, self._tasks = store, tasks
        self._lookup, self._limits = InputLookup(encryption_key), limits
        # Prove injected key matches the actual collaborators without private key access.
        from cryptography.fernet import Fernet

        probe = Fernet(encryption_key).encrypt(b"daemon.resource-input.composition.v1")
        if store._cipher.decrypt(probe) != b"daemon.resource-input.composition.v1":
            raise ResourceError("invalid_composition")
        if tasks._enc.decrypt(probe.decode("ascii")) != "daemon.resource-input.composition.v1":
            raise ResourceError("invalid_composition")
        self._active: dict[UUID, int] = {}
        self._count = 0
        self._verified = False
        self._presentations: Any = None

    async def verify_lookup_identity(self) -> None:
        """Required isolated startup check; unknown DB is never an empty inventory."""
        self._verified = False
        async with self._store._namespaces._transaction() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT account_id,lookup_key_id FROM resource_input_batches"
            )
            for row in rows:
                self._key_identity(row)
        self._verified = True

    async def bind(self) -> None:
        """Explicit isolated composition only. Default application never calls this."""
        if self._tasks._resource_inputs not in (None, self) or getattr(
            self._tasks._memory, "_resource_inputs", None
        ) not in (None, self):
            raise ResourceError("invalid_composition")
        await self.verify_lookup_identity()
        self._tasks._resource_inputs = self
        self._tasks._memory._resource_inputs = self

    async def history(
        self,
        account: UUID,
        conversation: UUID,
        *,
        limit: int,
        offset: int = 0,
        cursor_page: bool = False,
        before: HistoryBoundary | None = None,
        through: HistoryBoundary | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Read-time task discovery, never an authorization token or a stored backfill."""
        if type(limit) is not int or not 1 <= limit <= (100 if cursor_page else 500):
            raise ResourceError("invalid_query")
        async with self._slot(account), self._account(account) as conn:
            expected = await capture_generation(conn, account)
            async with guard_content_write(conn, expected):
                if not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM conversations WHERE id=$1 AND user_id=$2)",
                    conversation,
                    account,
                ):
                    raise ResourceError("not_found")
                memory = self._tasks._memory
                if cursor_page:
                    messages, more = await memory.get_message_history_page(
                        conversation,
                        user_id=account,
                        limit=limit,
                        before=before,
                        through=through,
                        _connection=conn,
                    )
                else:
                    messages = await memory.get_messages(
                        conversation,
                        limit=limit,
                        offset=offset,
                        _connection=conn,
                    )
                    more = False
                # Two per page message suffice to reject ambiguity. Deliberately
                # do not filter candidate owners: a foreign link is corruption,
                # not permission to choose a convenient owned candidate instead.
                candidates = await conn.fetch(
                    "SELECT t.*,m.id AS history_message_id FROM messages m "
                    "CROSS JOIN LATERAL (SELECT * FROM tasks t WHERE "
                    "t.user_message_id=m.id OR t.result_message_id=m.id LIMIT 2) t "
                    "WHERE m.id=ANY($1::uuid[]) ORDER BY m.id,t.id",
                    [m["id"] for m in messages],
                )
                grouped: dict[UUID, list[Any]] = {}
                for row in candidates:
                    grouped.setdefault(row["history_message_id"], []).append(row)
                for message in messages:
                    message["task_id"] = None
                    if message["user_id"] != account or message["conversation_id"] != conversation:
                        raise ResourceError()
                    rows = grouped.get(message["id"], [])
                    if not rows:
                        continue
                    if len(rows) != 1 or message["role"] not in ("user", "assistant"):
                        raise ResourceError()
                    row = rows[0]
                    field = "user_message_id" if message["role"] == "user" else "result_message_id"
                    if (
                        row["user_id"] != account
                        or row["conversation_id"] != conversation
                        or row[field] != message["id"]
                    ):
                        raise ResourceError()
                    try:
                        await self._tasks._references(conn, row)
                        origin = self._tasks._origin(row)
                    except ObservationBlocked as exc:
                        if exc.code == "task_origin_missing":
                            continue  # Legacy tasks have no qualified original identity.
                        raise ResourceError() from None
                    except ValueError:
                        raise ResourceError() from None
                    if (origin.account_id, origin.generation) != (account, expected.generation):
                        raise ResourceError()
                    message["task_id"] = row["id"]
                return messages, more

    def _key_identity(self, row: Any) -> None:
        if not hmac.compare_digest(
            bytes(row["lookup_key_id"]), self._lookup.identity(row["account_id"])
        ):
            raise ResourceError("lookup_key_changed")

    @asynccontextmanager
    async def _slot(self, account: UUID) -> AsyncIterator[None]:
        if not self._verified:
            raise ResourceError("composition_not_verified")
        if (
            self._count >= self._limits.concurrent
            or self._active.get(account, 0) >= self._limits.per_account
        ):
            raise ResourceError("capacity")
        self._count += 1
        self._active[account] = self._active.get(account, 0) + 1
        try:
            yield
        finally:
            self._count -= 1
            remaining = self._active[account] - 1
            if remaining:
                self._active[account] = remaining
            else:
                del self._active[account]

    @asynccontextmanager
    async def _account(self, account: UUID) -> AsyncIterator[Connection]:
        try:
            async with self._store._namespaces._transaction() as conn:
                async with lock_account_control(conn, account):
                    await self._store._namespaces._inventory(
                        conn, await self._store._namespaces._scan()
                    )
                    # Do not filter by the NEW key ID: inspect retained old identities.
                    for row in await conn.fetch(
                        "SELECT DISTINCT account_id,lookup_key_id FROM resource_input_batches WHERE account_id=$1",
                        account,
                    ):
                        self._key_identity(row)
                    yield conn
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, JournalError):
            raise ResourceError() from None

    def _request_seal(self, canonical: str) -> str:
        return self._store._cipher.encrypt(canonical.encode("utf-8")).decode("ascii")

    def _request_open(self, row: Any) -> str:
        if row["state"] == "source_deleted":
            raise IdempotencyConflict("submission source deleted")
        token = row["request_ciphertext"]
        if type(token) is not str or len(token) > 4 * ((self._limits.request_bytes + 128) // 3 + 1):
            raise ResourceError()
        try:
            raw = self._store._cipher.decrypt(token.encode("ascii"))
            if len(raw) > self._limits.request_bytes:
                raise ResourceError()
            return raw.decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            raise ResourceError() from None

    def _descriptor(self, row: Any, item: ReceivedInput, batch: Any) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "account_id": str(batch["account_id"]),
            "batch_id": str(batch["batch_id"]),
            "task_id": str(batch["task_id"]),
            "generation": batch["generation"],
            "intent_id": str(row["intent_id"]),
            "resource_id": str(row["resource_id"]),
            "version_id": str(row["version_id"]),
            "ordinal": row["ordinal"],
            "representation": item.representation,
            "kind": item.kind,
            "mime": item.mime,
            "measured_bytes": item.measured_bytes,
            "sha256": item.sha256,
            "supplied": item.metadata,
        }

    async def _batch(
        self, conn: Connection, account: UUID, batch_id: UUID, canonical: str
    ) -> tuple[Any, tuple[ReceivedInput, ...], list[Any]]:
        batch = await conn.fetchrow(
            "SELECT * FROM resource_input_batches WHERE account_id=$1 AND batch_id=$2 FOR UPDATE",
            account,
            batch_id,
        )
        if batch is None:
            raise ResourceError("not_found")
        self._key_identity(batch)
        if self._request_open(batch) != canonical:
            raise IdempotencyConflict("idempotency key reused for a different request")
        items = received_inputs(canonical, self._limits)
        rows = await conn.fetch(
            "SELECT * FROM resource_input_intents WHERE account_id=$1 AND batch_id=$2 ORDER BY ordinal FOR UPDATE",
            account,
            batch_id,
        )
        if len(rows) != len(items):
            raise ResourceError()
        for ordinal, (row, item) in enumerate(zip(rows, items, strict=True)):
            if (
                row["generation"],
                row["ordinal"],
                row["representation"],
                row["kind"],
                row["mime"],
                row["measured_bytes"],
            ) != (
                batch["generation"],
                ordinal,
                item.representation,
                item.kind,
                item.mime,
                item.measured_bytes,
            ) or _canonical(self._store._open(row["descriptor_ciphertext"])) != _canonical(
                self._descriptor(row, item, batch)
            ):
                raise ResourceError()
        return batch, items, list(rows)

    async def reserve(self, expected: CapturedGeneration, canonical: str, key: str | None) -> UUID:
        """Known committed tracking before any namespace/stage effects; no task ACK."""
        if type(expected) is not CapturedGeneration:
            raise ResourceError("invalid")
        CapturedGeneration(expected.account_id, expected.generation)
        async with self._slot(expected.account_id):
            return await self._reserve(expected, canonical, key)

    async def _reserve(self, expected: CapturedGeneration, canonical: str, key: str | None) -> UUID:
        if type(expected) is not CapturedGeneration:
            raise ResourceError("invalid")
        CapturedGeneration(expected.account_id, expected.generation)
        items = received_inputs(canonical, self._limits)
        digest = self._lookup.digest(expected.account_id, key)
        async with self._account(expected.account_id) as conn:
            if digest is not None:
                existing = await conn.fetchval(
                    "SELECT batch_id FROM resource_input_batches WHERE account_id=$1 AND key_digest=$2",
                    expected.account_id,
                    digest,
                )
                if existing is not None:
                    batch, _, _ = await self._batch(conn, expected.account_id, existing, canonical)
                    if batch["generation"] != expected.generation:
                        raise ResourceError("refused")
                    return existing
            async with guard_content_write(conn, expected):
                batch = {
                    "batch_id": uuid4(),
                    "account_id": expected.account_id,
                    "generation": expected.generation,
                    "task_id": uuid4(),
                }
                await conn.execute(
                    "INSERT INTO resource_input_batches(batch_id,account_id,generation,key_digest,lookup_key_id,task_id,request_ciphertext,state) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,'preparing')",
                    batch["batch_id"],
                    expected.account_id,
                    expected.generation,
                    digest,
                    self._lookup.identity(expected.account_id),
                    batch["task_id"],
                    self._request_seal(canonical),
                )
                for ordinal, item in enumerate(items):
                    row = {
                        "intent_id": uuid4(),
                        "resource_id": uuid4(),
                        "version_id": uuid4(),
                        "ordinal": ordinal,
                    }
                    await conn.execute(
                        "INSERT INTO resource_input_intents(intent_id,account_id,batch_id,generation,ordinal,resource_id,version_id,representation,kind,mime,measured_bytes,descriptor_ciphertext) "
                        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                        row["intent_id"],
                        expected.account_id,
                        batch["batch_id"],
                        expected.generation,
                        ordinal,
                        row["resource_id"],
                        row["version_id"],
                        item.representation,
                        item.kind,
                        item.mime,
                        item.measured_bytes,
                        self._store._seal(self._descriptor(row, item, batch)),
                    )
        return batch["batch_id"]

    def _stored(self, batch: Any, row: Any, item: ReceivedInput, namespace: str) -> StoredBinding:
        if item.measured_bytes is None or item.sha256 is None:
            raise ResourceError()
        return StoredBinding(
            ArtifactBinding(
                str(batch["account_id"]),
                str(row["resource_id"]),
                str(row["version_id"]),
                batch["generation"],
                item.representation,
            ),
            namespace,
            row["intent_id"].hex,
            row["version_id"].hex,
            item.measured_bytes,
            item.sha256,
        )

    async def _namespace(self, conn: Connection, expected: CapturedGeneration) -> str:
        row = await conn.fetchrow(
            "SELECT namespace,state FROM artifact_namespaces WHERE account_id=$1 AND generation=$2",
            expected.account_id,
            expected.generation,
        )
        if row is None or row["state"] != "ready":
            raise ResourceError()
        return row["namespace"]

    async def prepare(self, expected: CapturedGeneration, batch_id: UUID, canonical: str) -> None:
        if type(expected) is not CapturedGeneration:
            raise ResourceError("invalid")
        CapturedGeneration(expected.account_id, expected.generation)
        async with self._slot(expected.account_id):
            await self._prepare(expected, batch_id, canonical)

    async def _prepare(self, expected: CapturedGeneration, batch_id: UUID, canonical: str) -> None:
        if type(expected) is not CapturedGeneration:
            raise ResourceError("invalid")
        CapturedGeneration(expected.account_id, expected.generation)
        # Reserve is independently known committed before this method may provision.
        async with self._account(expected.account_id) as conn:
            batch, _, _ = await self._batch(conn, expected.account_id, batch_id, canonical)
            if batch["generation"] != expected.generation:
                raise ResourceError("refused")
            if batch["state"] == "accepted":
                return
            if batch["state"] != "preparing":
                raise ResourceError("refused")
            async with guard_content_write(conn, expected):
                pass
        await self._store._namespaces.ensure_namespace(expected)
        async with self._account(expected.account_id) as conn:
            async with guard_content_write(conn, expected):
                batch, items, rows = await self._batch(
                    conn, expected.account_id, batch_id, canonical
                )
                if batch["generation"] != expected.generation:
                    raise ResourceError("refused")
                if batch["state"] == "accepted":
                    return
                if batch["state"] != "preparing":
                    raise ResourceError("refused")
                namespace = await self._namespace(conn, expected)
                await _io(lambda: _topology(self._store._root, namespace, create=False))
                for row, item in zip(rows, items, strict=True):
                    if item.content is not None:
                        stored = self._stored(batch, row, item, namespace)
                        await _io(
                            lambda s=stored, b=item.content: prepare_verified(
                                self._store._root, self._store._envelope, s, b
                            )
                        )

    def _metadata(self, batch: Any, row: Any, item: ReceivedInput) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "account_id": str(batch["account_id"]),
            "resource_id": str(row["resource_id"]),
            "version_id": str(row["version_id"]),
            "generation": batch["generation"],
            "representation": item.representation,
            "kind": item.kind,
            "mime": item.mime,
            "measured_bytes": item.measured_bytes,
            "sha256": item.sha256,
            "supplied": item.metadata,
        }

    def _provenance(self, batch: Any, row: Any, task: AcceptedTask) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "account_id": str(batch["account_id"]),
            "resource_id": str(row["resource_id"]),
            "version_id": str(row["version_id"]),
            "generation": batch["generation"],
            "original_task_id": str(task.task_id),
            "original_conversation_id": str(task.conversation_id),
            "original_message_id": str(task.user_message_id),
            "role": "input",
            "ordinal": row["ordinal"],
            "source_deleted": False,
        }

    async def _task(self, conn: Connection, batch: Any, canonical: str) -> AcceptedTask:
        row = await conn.fetchrow(
            "SELECT * FROM tasks WHERE id=$1 AND user_id=$2 FOR UPDATE",
            batch["task_id"],
            batch["account_id"],
        )
        if row is None or batch["accepted_task_id"] != row["id"]:
            raise ResourceError()
        value = self._tasks._open(row["input_ciphertext"])
        if type(value.get("origin_account_generation")) is not int:
            raise ResourceError()
        if (
            value.get("_request"),
            value.get("origin_account_id"),
            value.get("origin_account_generation"),
        ) != (canonical, str(batch["account_id"]), batch["generation"]):
            raise ResourceError()
        for message, role in (
            (row["user_message_id"], "user"),
            (row["result_message_id"], "assistant"),
        ):
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE m.id=$1 AND m.role=$2 AND m.conversation_id=$3 AND m.user_id=$4 AND c.user_id=$4)",
                message,
                role,
                row["conversation_id"],
                batch["account_id"],
            ):
                raise ResourceError()
        return AcceptedTask(
            row["id"],
            row["conversation_id"],
            row["user_message_id"],
            row["result_message_id"],
            TaskStatus(row["status"]),
            False,
        )

    async def _publish(
        self,
        conn: Connection,
        batch: Any,
        items: tuple[ReceivedInput, ...],
        rows: list[Any],
        task: AcceptedTask,
        namespace: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO resource_catalog_heads(account_id,generation) VALUES($1,$2) ON CONFLICT DO NOTHING",
            batch["account_id"],
            batch["generation"],
        )
        head = await conn.fetchrow(
            "SELECT * FROM resource_catalog_heads WHERE account_id=$1 FOR UPDATE",
            batch["account_id"],
        )
        if head is None or head["generation"] != batch["generation"]:
            raise ResourceError("refused")
        # Complete current private verification/resync before immutable SQL writes.
        for row, item in zip(rows, items, strict=True):
            if item.content is not None:
                stored = self._stored(batch, row, item, namespace)
                await _io(
                    lambda s=stored, b=item.content: prepare_verified(
                        self._store._root, self._store._envelope, s, b
                    )
                )
        counter = head["insertion_counter"]
        for row, item in zip(rows, items, strict=True):
            counter += 1
            await conn.execute(
                "INSERT INTO resources(resource_id,account_id,insertion_ordinal) VALUES($1,$2,$3)",
                row["resource_id"],
                batch["account_id"],
                counter,
            )
            await conn.execute(
                "INSERT INTO resource_versions(version_id,account_id,resource_id,generation,representation,kind,mime,measured_bytes,metadata_ciphertext,insertion_ordinal) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                row["version_id"],
                batch["account_id"],
                row["resource_id"],
                batch["generation"],
                item.representation,
                item.kind,
                item.mime,
                item.measured_bytes,
                self._store._seal(self._metadata(batch, row, item)),
                counter,
            )
            if item.content is not None:
                stored = self._stored(batch, row, item, namespace)
                await conn.execute(
                    "INSERT INTO resource_publications(version_id,account_id,resource_id,generation,namespace,intent_id,source_kind,representation,measured_bytes,binding_ciphertext,insertion_ordinal) "
                    "VALUES($1,$2,$3,$4,$5,$6,'input',$7,$8,$9,$10)",
                    row["version_id"],
                    batch["account_id"],
                    row["resource_id"],
                    batch["generation"],
                    namespace,
                    row["intent_id"],
                    item.representation,
                    item.measured_bytes,
                    self._store._seal(self._store._binding_fields(stored, "input")),
                    counter,
                )
            await conn.execute(
                "INSERT INTO resource_references(reference_id,account_id,resource_id,version_id,generation,role,ordinal,task_id,conversation_id,message_id,provenance_ciphertext) "
                "VALUES($1,$2,$3,$4,$5,'input',$6,$7,$8,$9,$10)",
                uuid4(),
                batch["account_id"],
                row["resource_id"],
                row["version_id"],
                batch["generation"],
                row["ordinal"],
                task.task_id,
                task.conversation_id,
                task.user_message_id,
                self._store._seal(self._provenance(batch, row, task)),
            )
        await conn.execute(
            "UPDATE resource_catalog_heads SET insertion_counter=$2 WHERE account_id=$1",
            batch["account_id"],
            counter,
        )

    async def _accepted(
        self,
        conn: Connection,
        batch: Any,
        items: tuple[ReceivedInput, ...],
        rows: list[Any],
        canonical: str,
    ) -> AcceptedTask:
        task = await self._task(conn, batch, canonical)
        expected = CapturedGeneration(batch["account_id"], batch["generation"])
        namespace = await self._namespace(conn, expected)
        for row, item in zip(rows, items, strict=True):
            await self._verify_version(conn, batch, row, item, task, namespace)
        refs = await conn.fetchval(
            "SELECT count(*) FROM resource_references WHERE account_id=$1 AND task_id=$2 AND role='input'",
            batch["account_id"],
            task.task_id,
        )
        if refs != len(rows):
            raise ResourceError()
        return task

    async def _verify_version(
        self,
        conn: Connection,
        batch: Any,
        row: Any,
        item: ReceivedInput,
        task: AcceptedTask,
        namespace: str,
    ) -> None:
        version = await conn.fetchrow(
            "SELECT * FROM resource_versions WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND generation=$4",
            batch["account_id"],
            row["resource_id"],
            row["version_id"],
            batch["generation"],
        )
        if version is None or not self._store._record_matches(
            version["metadata_ciphertext"], self._metadata(batch, row, item)
        ):
            raise ResourceError()
        if (
            version["representation"],
            version["kind"],
            version["mime"],
            version["measured_bytes"],
        ) != (item.representation, item.kind, item.mime, item.measured_bytes):
            raise ResourceError()
        ref = await conn.fetchrow(
            "SELECT * FROM resource_references WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND role='input'",
            batch["account_id"],
            row["resource_id"],
            row["version_id"],
        )
        if (
            ref is None
            or (
                ref["task_id"],
                ref["conversation_id"],
                ref["message_id"],
                ref["ordinal"],
                ref["generation"],
            )
            != (
                task.task_id,
                task.conversation_id,
                task.user_message_id,
                row["ordinal"],
                batch["generation"],
            )
            or not self._store._record_matches(
                ref["provenance_ciphertext"], self._provenance(batch, row, task)
            )
        ):
            raise ResourceError()
        publication = await conn.fetchrow(
            "SELECT * FROM resource_publications WHERE account_id=$1 AND version_id=$2",
            batch["account_id"],
            row["version_id"],
        )
        if item.content is None:
            if publication is not None:
                raise ResourceError()
        else:
            stored = self._stored(batch, row, item, namespace)
            if (
                publication is None
                or (
                    publication["resource_id"],
                    publication["generation"],
                    publication["namespace"],
                    publication["intent_id"],
                    publication["source_kind"],
                    publication["representation"],
                    publication["measured_bytes"],
                    publication["insertion_ordinal"],
                )
                != (
                    row["resource_id"],
                    batch["generation"],
                    namespace,
                    row["intent_id"],
                    "input",
                    item.representation,
                    item.measured_bytes,
                    version["insertion_ordinal"],
                )
                or (
                    not self._store._record_matches(
                        publication["binding_ciphertext"],
                        self._store._binding_fields(stored, "input"),
                    )
                )
            ):
                raise ResourceError()
            await _io(lambda: read_verified(self._store._root, self._store._envelope, stored))

    async def find_by_key(
        self, account: UUID, key: str, request_hash: str, canonical: str | None
    ) -> AcceptedTask | None:
        """Native pre-admission replay shares the actual batch/deletion authority."""
        async with self._slot(account), self._account(account) as conn:
            _, task = await self._replay(conn, account, key, request_hash, canonical)
            return task

    async def _replay(
        self,
        conn: Connection,
        account: UUID,
        key: str | None,
        request_hash: str,
        canonical: str | None,
        hint: UUID | None = None,
    ) -> tuple[UUID | None, AcceptedTask | None]:
        if self._tasks._composition is not self._tasks._memory._task_composition:
            raise ResourceError("invalid_composition")
        if self._tasks._composition is not None:
            self._tasks._composition.validate()
        digest = self._lookup.digest(account, key)
        found = (
            await conn.fetchval(
                "SELECT batch_id FROM resource_input_batches WHERE account_id=$1 AND key_digest=$2",
                account,
                digest,
            )
            if digest is not None
            else hint
        )
        if hint is not None and found != hint:
            raise ResourceError("conflict")
        if found is not None:
            if canonical is None:
                raise IdempotencyConflict("canonical request required by resource composition")
            batch, items, rows = await self._batch(conn, account, found, canonical)
            if batch["state"] == "accepted":
                return found, await self._accepted(conn, batch, items, rows, canonical)
            return found, None
        if key is None:
            return None, None
        # Same connection and account serialization; never misclassify a C1 task
        # with an inconsistent digest as a legacy task lacking resource coverage.
        legacy = await conn.fetchrow(
            "SELECT * FROM tasks WHERE user_id=$1 AND idempotency_key=$2 FOR UPDATE", account, key
        )
        if legacy is None:
            return None, None
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM resource_input_batches WHERE account_id=$1 AND task_id=$2)",
            account,
            legacy["id"],
        ):
            raise ResourceError()
        if legacy["request_hash"] != request_hash:
            stored = self._tasks._open(legacy["input_ciphertext"]).get("_request")
            if canonical is None or stored != canonical:
                raise IdempotencyConflict("idempotency key reused for a different request")
            await conn.execute(
                "UPDATE tasks SET request_hash=$3 WHERE user_id=$1 AND id=$2",
                account,
                legacy["id"],
                request_hash,
            )
        return None, AcceptedTask(
            legacy["id"],
            legacy["conversation_id"],
            legacy["user_message_id"],
            legacy["result_message_id"],
            TaskStatus(legacy["status"]),
            False,
        )

    async def accept(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID | None,
        new_conversation_title: str | None,
        pipeline: str,
        user_message: str,
        task_input: dict[str, Any],
        request_hash: str,
        idempotency_key: str | None,
        assistant_model: str | None,
        request_canonical: str,
        captured_generation: CapturedGeneration | None,
        max_attempts: int = 2,
        batch_id: UUID | None = None,
    ) -> AcceptedTask:
        """Actual atomic task/input acceptance. No-key recovery needs original batch ID."""
        return await self._accept_with_stage(
            stage=None,
            user_id=user_id,
            conversation_id=conversation_id,
            new_conversation_title=new_conversation_title,
            pipeline=pipeline,
            user_message=user_message,
            task_input=task_input,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            assistant_model=assistant_model,
            request_canonical=request_canonical,
            captured_generation=captured_generation,
            max_attempts=max_attempts,
            batch_id=batch_id,
        )

    async def _accept_with_stage(
        self,
        *,
        stage: _AcceptanceStage | None,
        user_id: UUID,
        conversation_id: UUID | None,
        new_conversation_title: str | None,
        pipeline: str,
        user_message: str,
        task_input: dict[str, Any],
        request_hash: str,
        idempotency_key: str | None,
        assistant_model: str | None,
        request_canonical: str,
        captured_generation: CapturedGeneration | None,
        max_attempts: int = 2,
        batch_id: UUID | None = None,
    ) -> AcceptedTask:
        """Actual atomic task/input acceptance. No-key recovery needs original batch ID."""
        async with self._slot(user_id):
            # Replay is account-serialized against composed deletion, before NEW effects.
            async with self._account(user_id) as conn:
                found, replay = await self._replay(
                    conn, user_id, idempotency_key, request_hash, request_canonical, batch_id
                )
                if replay is not None:
                    return replay
            sealed = await self._tasks._accept_input(
                user_id, task_input, request_canonical, captured_generation
            )
            # The accepted processing snapshot can contain history not present in
            # the replay fingerprint. Bound it independently before preparation.
            from orchestrator.tasks.inputs import canonical_json

            if len(canonical_json(sealed).encode("utf-8")) > self._limits.request_bytes:
                raise ResourceError("too_large")
            assert captured_generation is not None
            expected = captured_generation
            batch_id = (
                found
                if found is not None
                else await self._reserve(expected, request_canonical, idempotency_key)
            )
            if not isinstance(batch_id, UUID):
                raise ResourceError()
            await self._prepare(expected, batch_id, request_canonical)
            stage_attempted = False
            for retry in (False, True):
                try:
                    async with self._account(user_id) as conn:
                        async with guard_content_write(conn, expected):
                            batch, items, rows = await self._batch(
                                conn, user_id, batch_id, request_canonical
                            )
                            if batch["generation"] != expected.generation:
                                raise ResourceError("refused")
                            if batch["state"] == "accepted":
                                return await self._accepted(
                                    conn, batch, items, rows, request_canonical
                                )
                            if batch["state"] != "preparing":
                                raise ResourceError()
                            namespace = await self._namespace(conn, expected)
                            pending = await self._tasks._insert_accepted(
                                conn,
                                user_id=user_id,
                                conversation_id=conversation_id,
                                new_conversation_title=new_conversation_title,
                                pipeline=pipeline,
                                user_message=user_message,
                                sealed_input=sealed,
                                request_hash=request_hash,
                                idempotency_key=idempotency_key,
                                assistant_model=assistant_model,
                                max_attempts=max_attempts,
                                generation=expected.generation,
                                task_id=batch["task_id"],
                            )
                            await self._publish(conn, batch, items, rows, pending, namespace)
                            # Same-account actual task/message/origin validation BEFORE pointer.
                            provisional = dict(batch)
                            provisional["accepted_task_id"] = pending.task_id
                            await self._task(conn, provisional, request_canonical)
                            await conn.execute(
                                "UPDATE resource_input_batches SET state='accepted',accepted_task_id=task_id WHERE account_id=$1 AND batch_id=$2",
                                user_id,
                                batch_id,
                            )
                            if stage is not None:
                                stage_attempted = True
                                await stage(conn, pending)
                    return pending
                except ResourceError as exc:
                    if stage_attempted:
                        raise
                    # _account has already discarded/released the exact failed channel.
                    # Inspect only a definite unique-index failure, never unknown COMMIT.
                    cause = exc.__context__
                    if not isinstance(cause, asyncpg.UniqueViolationError):
                        raise
                    async with self._account(user_id) as conn:
                        batch, items, rows = await self._batch(
                            conn, user_id, batch_id, request_canonical
                        )
                        if batch["state"] == "accepted":
                            return await self._accepted(conn, batch, items, rows, request_canonical)
                        if (
                            getattr(cause, "constraint_name", None) != CONVERSATION_ACTIVE_INDEX
                            or conversation_id is None
                        ):
                            raise exc
                        active = await conn.fetchval(
                            "SELECT id FROM tasks WHERE conversation_id=$1 AND user_id=$2 AND status IN ('queued','running')",
                            conversation_id,
                            user_id,
                        )
                        if active is not None:
                            raise ConversationBusy(active)
                        if retry:
                            raise exc
            raise AssertionError("unreachable")

    async def delete_conversation(
        self, account: UUID, conversation: UUID
    ) -> tuple[bool, UUID | None]:
        if (
            self._tasks._resource_inputs is not self
            or self._tasks._memory._resource_inputs is not self
        ):
            raise ResourceError("invalid_composition")
        async with self._slot(account), self._account(account) as conn:
            # This is a NEW owner deletion request, not a refresh of an old producer.
            expected = await capture_generation(conn, account)
            async with guard_content_write(conn, expected):
                return await self._tasks._delete_conversation_rows(conn, account, conversation)

    async def _detach(self, conn: Connection, account: UUID, conversation: UUID) -> None:
        """Private same-transaction join, reached only after account/source locks.

        Authenticate retained provenance before any mutation. Never copy the chat
        text into tombstones, and never remove resources or touch private files.
        """
        tasks = await conn.fetch(
            "SELECT id FROM tasks WHERE user_id=$1 AND conversation_id=$2 FOR UPDATE",
            account,
            conversation,
        )
        task_ids = [row["id"] for row in tasks]
        # A busy/rolled-back submission can still name this conversation before
        # any task exists. Do not retain that canonical chat after source deletion.
        preparing = await conn.fetch(
            "SELECT * FROM resource_input_batches WHERE account_id=$1 AND state IN ('preparing','uncertain') FOR UPDATE",
            account,
        )
        detached_batches = []
        for batch in preparing:
            try:
                request = json.loads(self._request_open(batch))
                if type(request) is not dict:
                    raise ResourceError()
                if request.get("conversation_id") == str(conversation):
                    detached_batches.append(batch["batch_id"])
            except (ValueError, TypeError, RecursionError):
                raise ResourceError() from None
        refs = await conn.fetch(
            "SELECT * FROM resource_references WHERE account_id=$1 AND (conversation_id=$2 "
            "OR task_id=ANY($3::uuid[]) OR message_id IN (SELECT id FROM messages WHERE conversation_id=$2)) FOR UPDATE",
            account,
            conversation,
            task_ids,
        )
        if self._presentations is not None:
            await self._presentations.detachment(conn, account, conversation)
        if refs:
            head = await conn.fetchrow(
                "SELECT * FROM resource_catalog_heads WHERE account_id=$1 FOR UPDATE", account
            )
            if head is None:
                raise ResourceError()
            expected = CapturedGeneration(account, head["generation"])
            namespace = await self._namespace(conn, expected)
            for ref in refs:
                if ref["role"] == "input":
                    pub, _, _ = await self._store._input_publication(
                        conn, expected, ref["resource_id"], ref["version_id"], namespace
                    )
                else:
                    pub, _, _, _, _ = await self._store._publication(
                        conn, expected, ref["resource_id"], ref["version_id"], namespace
                    )
                if pub.reference_id != ref["reference_id"]:
                    raise ResourceError()
        if self._presentations is not None:
            await self._presentations.detachment(conn, account, conversation, apply=True)
        if refs:
            for ref in refs:
                provenance = self._store._open(ref["provenance_ciphertext"])
                provenance["source_deleted"] = True
                await conn.execute(
                    "UPDATE resource_references SET task_id=NULL,conversation_id=NULL,message_id=NULL,provenance_ciphertext=$3 "
                    "WHERE account_id=$1 AND reference_id=$2",
                    account,
                    ref["reference_id"],
                    self._store._seal(provenance),
                )
            await conn.execute(
                "UPDATE resource_catalog_heads SET filter_revision=filter_revision+1 WHERE account_id=$1",
                account,
            )
        # Includes accepted batches with zero attachments. Original IDs survive,
        # but the full canonical chat request and live task pointer do not.
        await conn.execute(
            "UPDATE resource_input_batches SET state='source_deleted',request_ciphertext=NULL,accepted_task_id=NULL "
            "WHERE account_id=$1 AND (accepted_task_id=ANY($2::uuid[]) OR batch_id=ANY($3::uuid[]))",
            account,
            task_ids,
            detached_batches,
        )
