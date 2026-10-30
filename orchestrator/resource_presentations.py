"""Encrypted, pinned, non-executable file-results envelopes for isolated C1."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_catalog import ResourceCatalog, uuid_text
from orchestrator.resource_store import ResourceError, _canonical
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write


class ResourcePresentations:
    def __init__(self, catalog: ResourceCatalog, *, item_limit: int, record_limit: int) -> None:
        if type(catalog) is not ResourceCatalog or any(
            type(v) is not int or not 1 <= v <= 100 for v in (item_limit, record_limit)
        ):
            raise ResourceError("invalid_limits")
        self.catalog, self.inputs, self.store = catalog, catalog.inputs, catalog.store
        if getattr(self.inputs, "_presentations", None) is not None:
            raise ResourceError("invalid_composition")
        self.item_limit, self.record_limit = item_limit, record_limit
        self.inputs._presentations = self

    def _pins(self, items: Any) -> list[dict[str, str]]:
        if type(items) is not list or len(items) > self.item_limit:
            raise ResourceError()
        result = []
        for i in items:
            if type(i) is not dict or set(i) != {"resource_id", "version_id"}:
                raise ResourceError()
            result.append({k: uuid_text(i[k]) for k in ("resource_id", "version_id")})
        if len({(i["resource_id"], i["version_id"]) for i in result}) != len(result):
            raise ResourceError()
        return result

    def _open(self, row: Any, expected: CapturedGeneration) -> dict[str, Any]:
        value = self.store._open(row["envelope_ciphertext"])
        if set(value) != {
            "schema_version",
            "account_id",
            "generation",
            "original_task_id",
            "original_message_id",
            "source_deleted",
            "envelope",
        }:
            raise ResourceError()
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["account_id"] != str(expected.account_id)
            or type(value["generation"]) is not int
            or value["generation"] != expected.generation
            or row["generation"] != expected.generation
            or type(value["source_deleted"]) is not bool
        ):
            raise ResourceError()
        uuid_text(value["original_task_id"])
        uuid_text(value["original_message_id"])
        if (row["source_message_id"] is None) != value["source_deleted"] or (
            not value["source_deleted"]
            and str(row["source_message_id"]) != value["original_message_id"]
        ):
            raise ResourceError()
        env = value["envelope"]
        if type(env) is not dict or set(env) != {
            "schema_version",
            "component_library_version",
            "snapshot_at",
            "component",
            "items",
        }:
            raise ResourceError()
        if (
            any(
                type(env[k]) is not int or env[k] != 1
                for k in ("schema_version", "component_library_version")
            )
            or env["component"] != "file_results"
            or env["snapshot_at"] != row["created_at"].isoformat()
        ):
            raise ResourceError()
        self._pins(env["items"])
        return value

    async def _check_pins(
        self, conn: Any, expected: CapturedGeneration, task: Any, pins: list[dict[str, str]]
    ) -> None:
        if not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=$1 AND m.user_id=$2 AND c.user_id=$2 AND m.conversation_id=$3 AND m.role='assistant')",
            task["result_message_id"],
            expected.account_id,
            task["conversation_id"],
        ):
            raise ResourceError()
        for pin in pins:
            ref = await conn.fetchrow(
                "SELECT r.* FROM resource_references r JOIN resource_versions v ON v.account_id=r.account_id AND v.version_id=r.version_id "
                "WHERE r.account_id=$1 AND r.generation=$2 AND r.task_id=$3 AND r.message_id=$4 AND r.conversation_id=$5 "
                "AND r.resource_id=$6 AND r.version_id=$7 AND r.role='output'",
                expected.account_id,
                expected.generation,
                task["id"],
                task["result_message_id"],
                task["conversation_id"],
                UUID(pin["resource_id"]),
                UUID(pin["version_id"]),
            )
            if ref is None:
                raise ResourceError()
            self.catalog._reference(ref, expected)

    async def record(self, identity: AttemptIdentity, items: list[dict[str, str]]) -> UUID:
        identity.wire()
        expected = CapturedGeneration(identity.account_id, identity.generation)
        pins = self._pins(items)
        async with (
            self.inputs._account(expected.account_id) as conn,
            guard_content_write(conn, expected),
        ):
            task = await self.catalog._task_owned(conn, expected, identity.task_id)
            await self._check_pins(conn, expected, task, pins)
            rows = await conn.fetch(
                "SELECT * FROM resource_presentations WHERE account_id=$1 AND source_message_id=$2 ORDER BY created_at,presentation_id LIMIT $3 FOR UPDATE",
                expected.account_id,
                task["result_message_id"],
                self.record_limit + 1,
            )
            if len(rows) > self.record_limit:
                raise ResourceError("capacity")
            matches = []
            for row in rows:
                value = self._open(row, expected)
                if value["original_task_id"] != str(identity.task_id):
                    raise ResourceError()
                await self._check_pins(conn, expected, task, value["envelope"]["items"])
                if _canonical(value["envelope"]["items"]) == _canonical(pins):
                    matches.append(row["presentation_id"])
            if len(matches) > 1:
                raise ResourceError()
            if matches:
                return matches[0]
            if len(rows) >= self.record_limit:
                raise ResourceError("capacity")
            await self.store._live(conn, identity)
            created = await conn.fetchval("SELECT clock_timestamp()")
            if not isinstance(created, datetime):
                raise ResourceError()
            presentation = uuid4()
            value = {
                "schema_version": 1,
                "account_id": str(expected.account_id),
                "generation": expected.generation,
                "original_task_id": str(identity.task_id),
                "original_message_id": str(task["result_message_id"]),
                "source_deleted": False,
                "envelope": {
                    "schema_version": 1,
                    "component_library_version": 1,
                    "snapshot_at": created.isoformat(),
                    "component": "file_results",
                    "items": pins,
                },
            }
            await conn.execute(
                "INSERT INTO resource_presentations(presentation_id,account_id,generation,source_message_id,envelope_ciphertext,created_at) VALUES($1,$2,$3,$4,$5,$6)",
                presentation,
                expected.account_id,
                expected.generation,
                task["result_message_id"],
                self.store._seal(value),
                created,
            )
            await self.store._live(conn, identity)
        return presentation

    async def page(self, expected: CapturedGeneration, task_id: UUID) -> dict[str, Any]:
        async with (
            self.inputs._account(expected.account_id) as conn,
            guard_content_write(conn, expected),
        ):
            task = await self.catalog._task_owned(conn, expected, task_id)
            rows = await conn.fetch(
                "SELECT * FROM resource_presentations WHERE account_id=$1 AND source_message_id=$2 ORDER BY created_at,presentation_id LIMIT $3",
                expected.account_id,
                task["result_message_id"],
                self.record_limit + 1,
            )
            if len(rows) > self.record_limit:
                raise ResourceError("capacity")
            envelopes = []
            for row in rows:
                value = self._open(row, expected)
                if value["original_task_id"] != str(task_id):
                    raise ResourceError()
                await self._check_pins(conn, expected, task, value["envelope"]["items"])
                envelopes.append(value["envelope"])
            return {"schema_version": 1, "items": envelopes, "next_cursor": None}

    async def detachment(
        self, conn: Any, account: UUID, conversation: UUID, *, apply: bool = False
    ) -> None:
        """Two bounded passes in the caller's source-locked deleting transaction.

        The caller locks the conversation and all its tasks before either pass;
        record() locks the same task, so cooperating writers cannot change the set.
        Validate all presentations and references before calling with apply=True.
        The second pass revalidates; any failure rolls back the whole deletion.
        """
        after = None
        while True:
            rows = await conn.fetch(
                "SELECT p.*,m.user_id AS message_account FROM resource_presentations p "
                "JOIN messages m ON m.id=p.source_message_id "
                "WHERE p.account_id=$1 AND m.conversation_id=$2 "
                "AND ($3::uuid IS NULL OR p.presentation_id>$3) "
                "ORDER BY p.presentation_id LIMIT $4 FOR UPDATE OF p",
                account,
                conversation,
                after,
                self.record_limit,
            )
            if not rows:
                return
            for row in rows:
                expected = CapturedGeneration(account, row["generation"])
                value = self._open(row, expected)
                task = await self.catalog._task_owned(
                    conn, expected, UUID(value["original_task_id"])
                )
                if (
                    row["message_account"] != account
                    or task["conversation_id"] != conversation
                    or task["result_message_id"] != row["source_message_id"]
                ):
                    raise ResourceError()
                await self._check_pins(conn, expected, task, value["envelope"]["items"])
                if apply:
                    value["source_deleted"] = True
                    changed = await conn.execute(
                        "UPDATE resource_presentations SET source_message_id=NULL,envelope_ciphertext=$3 "
                        "WHERE account_id=$1 AND presentation_id=$2 AND source_message_id=$4",
                        account,
                        row["presentation_id"],
                        self.store._seal(value),
                        row["source_message_id"],
                    )
                    if changed != "UPDATE 1":
                        raise ResourceError()
            after = rows[-1]["presentation_id"]
