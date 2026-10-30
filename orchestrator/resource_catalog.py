"""Bounded account-owned C1 catalog observations; explicit isolated composition only.

Cursors bind observations, never grant reads. Availability is reverified, not a
caller boolean or a promise of an atomic snapshot against external disk corruption.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from orchestrator.resource_inputs import ResourceInputs
from orchestrator.resource_storage import StorageError, read_verified
from orchestrator.resource_store import ResourceError, _canonical
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write
from orchestrator.services.deletion_journal.barrier import _io

MIMES = {"image/png", "text/plain", "text/csv", "application/octet-stream"}
AVAILABILITY = {"available", "original_not_received", "uncertain"}


def safe_display(value: Any) -> str | None:
    if type(value) is not str:
        return None
    return re.sub(r"[\x00-\x1f\x7f/\\]", "_", value[:200]) or None


def uuid_text(value: Any) -> str:
    if type(value) is not str or len(value) != 36:
        raise ResourceError("invalid_query")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ResourceError("invalid_query") from None
    return value


def instant(value: str) -> str:
    try:
        if len(value) > 40:
            raise ValueError
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise ResourceError("invalid_query") from None


def normalize_query(params: dict[str, str]) -> dict[str, Any]:
    if set(params) - {
        "role",
        "kind",
        "mime",
        "created_after",
        "created_before",
        "availability",
        "task_id",
        "job_id",
        "limit",
        "cursor",
    }:
        raise ResourceError("invalid_query")
    result: dict[str, Any] = {}
    for key, values in (
        ("role", {"input", "output"}),
        ("kind", {"image", "text", "file"}),
        ("mime", MIMES),
        ("availability", AVAILABILITY),
    ):
        value = params.get(key)
        if value is not None:
            if value not in values:
                raise ResourceError("invalid_query")
            result[key] = value
    for key in ("task_id", "job_id"):
        if key in params:
            result[key] = uuid_text(params[key])
    for key in ("created_after", "created_before"):
        if key in params:
            result[key] = instant(params[key])
    raw = params.get("limit", "20")
    if not re.fullmatch(r"[0-9]{1,3}", raw) or not 1 <= int(raw) <= 100:
        raise ResourceError("invalid_query")
    result["limit"] = int(raw)
    if (
        "created_after" in result
        and "created_before" in result
        and result["created_after"] >= result["created_before"]
    ):
        raise ResourceError("invalid_query")
    return result


class ResourceCatalog:
    def __init__(self, inputs: ResourceInputs, *, scan_limit: int) -> None:
        if (
            type(inputs) is not ResourceInputs
            or type(scan_limit) is not int
            or not 1 <= scan_limit <= 10000
        ):
            raise ResourceError("invalid_limits")
        self.inputs, self.store, self.scan_limit = inputs, inputs._store, scan_limit

    def _cursor(
        self,
        token: str | None,
        expected: CapturedGeneration,
        query: dict[str, Any],
        task: UUID | None,
    ) -> dict[str, Any] | None:
        if token is None:
            return None
        try:
            if type(token) is not str or len(token) > 8192:
                raise ResourceError()
            obj = self.store._open(token)
            if set(obj) != {
                "purpose",
                "schema_version",
                "account",
                "generation",
                "query",
                "task",
                "watermark",
                "revision",
                "last",
            }:
                raise ResourceError()
            if (obj["purpose"], obj["account"], obj["task"]) != (
                "daemon.resource.catalog.cursor.v1",
                str(expected.account_id),
                str(task) if task else None,
            ):
                raise ResourceError()
            if (
                type(obj["schema_version"]) is not int
                or obj["schema_version"] != 1
                or type(obj["generation"]) is not int
                or obj["generation"] != expected.generation
            ):
                raise ResourceError()
            if (
                _canonical(obj["query"]) != _canonical(query)
                or type(obj["watermark"]) is not int
                or not 0 <= obj["watermark"] <= 2**63 - 1
            ):
                raise ResourceError()
            if (
                type(obj["revision"]) is not list
                or len(obj["revision"]) != 2
                or type(obj["revision"][0]) is not int
                or obj["revision"][0] < 0
                or re.fullmatch(r"[0-9a-f]{64}", obj["revision"][1]) is None
            ):
                raise ResourceError()
            last = obj["last"]
            if type(last) is not list or len(last) != 2:
                raise ResourceError()
            uuid_text(last[1])
            if task:
                if type(last[0]) is not int or last[0] < 0:
                    raise ResourceError()
            elif instant(last[0]) != last[0]:
                raise ResourceError()
            return obj
        except (ResourceError, ValueError, TypeError, KeyError):
            raise ResourceError("invalid_cursor") from None

    async def _task_owned(self, conn: Any, expected: CapturedGeneration, task: UUID) -> Any:
        row = await conn.fetchrow(
            "SELECT * FROM tasks WHERE id=$1 AND user_id=$2 FOR UPDATE", task, expected.account_id
        )
        if row is None:
            raise ResourceError("not_found")
        try:
            origin = self.inputs._tasks._origin(row)
        except ValueError:
            raise ResourceError() from None
        if (origin.account_id, origin.generation) != (expected.account_id, expected.generation):
            raise ResourceError()
        return row

    async def _observe(self, conn: Any, expected: CapturedGeneration, row: Any) -> dict[str, Any]:
        item = {
            "resource_id": str(row["resource_id"]),
            "version_id": str(row["version_id"]),
            "representation": row["representation"],
            "kind": row["kind"],
            "mime": row["mime"],
            "measured_bytes": row["measured_bytes"],
            "created_at": row["created_at"].isoformat(),
            "availability": "uncertain",
            "display": {"name": None, "title": None},
            "reference": None,
        }
        try:
            ns = await conn.fetchval(
                "SELECT namespace FROM artifact_namespaces WHERE account_id=$1 AND generation=$2 AND state='ready'",
                expected.account_id,
                expected.generation,
            )
            if ns is None:
                raise ResourceError()
            if row["representation"] == "generated_file":
                _, stored, meta, _, _ = await self.store._publication(
                    conn, expected, row["resource_id"], row["version_id"], ns
                )
            else:
                _, stored, meta = await self.store._input_publication(
                    conn, expected, row["resource_id"], row["version_id"], ns
                )
            item["display"] = {
                "name": safe_display(meta.get("name", meta.get("supplied", {}).get("name"))),
                "title": safe_display(meta.get("title")),
            }
            if stored is None:
                item["availability"] = "original_not_received"
            else:
                await _io(lambda: read_verified(self.store._root, self.store._envelope, stored))
                item["availability"] = "available"
        except (ResourceError, StorageError):
            pass
        return item

    def _reference(self, ref: Any, expected: CapturedGeneration) -> dict[str, Any]:
        p = self.store._open(ref["provenance_ciphertext"])
        if (p.get("account_id"), p.get("resource_id"), p.get("version_id"), p.get("role")) != (
            str(expected.account_id),
            str(ref["resource_id"]),
            str(ref["version_id"]),
            ref["role"],
        ):
            raise ResourceError()
        if (
            type(p.get("generation")) is not int
            or p["generation"] != expected.generation
            or type(p.get("ordinal")) is not int
            or p["ordinal"] != ref["ordinal"]
            or type(p.get("source_deleted")) is not bool
        ):
            raise ResourceError()
        for field, original in (
            ("task_id", "original_task_id"),
            ("conversation_id", "original_conversation_id"),
            ("message_id", "original_message_id"),
        ):
            uuid_text(p.get(original))
            if p["source_deleted"]:
                if ref[field] is not None:
                    raise ResourceError()
            elif str(ref[field]) != p[original]:
                raise ResourceError()
        return {
            "reference_id": str(ref["reference_id"]),
            "role": ref["role"],
            "ordinal": ref["ordinal"],
            "task_id": str(ref["task_id"]) if ref["task_id"] else None,
            "conversation_id": str(ref["conversation_id"]) if ref["conversation_id"] else None,
            "message_id": str(ref["message_id"]) if ref["message_id"] else None,
            "source_deleted": p["source_deleted"],
        }

    async def page(
        self, expected: CapturedGeneration, params: dict[str, str], *, task: UUID | None = None
    ) -> dict[str, Any]:
        query = normalize_query(params)
        if task and set(query) - {"limit"}:
            raise ResourceError("invalid_query")
        cursor = self._cursor(params.get("cursor"), expected, query, task)
        async with (
            self.inputs._account(expected.account_id) as conn,
            guard_content_write(conn, expected),
        ):
            if task:
                await self._task_owned(conn, expected, task)
            head = await conn.fetchrow(
                "SELECT * FROM resource_catalog_heads WHERE account_id=$1", expected.account_id
            )
            if head and head["generation"] != expected.generation:
                raise ResourceError()
            watermark = cursor["watermark"] if cursor else head["insertion_counter"] if head else 0
            if watermark > (head["insertion_counter"] if head else 0):
                raise ResourceError("invalid_cursor")
            rows = await conn.fetch(
                "SELECT v.* FROM resource_versions v WHERE v.account_id=$1 AND v.generation=$2 AND v.insertion_ordinal<=$3 "
                "AND ($4::text IS NULL OR v.kind=$4) AND ($5::text IS NULL OR v.mime=$5) "
                "AND ($6::timestamptz IS NULL OR v.created_at>$6) AND ($7::timestamptz IS NULL OR v.created_at<$7) "
                "AND (($8::text IS NULL AND $9::uuid IS NULL AND $10::uuid IS NULL AND $11::uuid IS NULL) OR EXISTS "
                "(SELECT 1 FROM resource_references r WHERE r.account_id=v.account_id AND r.version_id=v.version_id "
                "AND ($8::text IS NULL OR r.role=$8) AND ($9::uuid IS NULL OR r.task_id=$9) AND ($10::uuid IS NULL OR r.job_id=$10) "
                "AND ($11::uuid IS NULL OR (r.task_id=$11 AND r.role='input')))) "
                "ORDER BY v.created_at DESC,v.version_id DESC LIMIT $12",
                expected.account_id,
                expected.generation,
                watermark,
                query.get("kind"),
                query.get("mime"),
                datetime.fromisoformat(query["created_after"])
                if "created_after" in query
                else None,
                datetime.fromisoformat(query["created_before"])
                if "created_before" in query
                else None,
                query.get("role"),
                UUID(query["task_id"]) if "task_id" in query else None,
                UUID(query["job_id"]) if "job_id" in query else None,
                task,
                self.scan_limit + 1,
            )
            if len(rows) > self.scan_limit:
                raise ResourceError("capacity")
            observed = []
            digest = hashlib.sha256(b"daemon.resource.availability.revision.v1\x00")
            for row in rows:
                item = await self._observe(conn, expected, row)
                digest.update(_canonical([str(row["version_id"]), item["availability"]]))
                if task:
                    refs = await conn.fetch(
                        "SELECT * FROM resource_references WHERE account_id=$1 AND task_id=$2 AND version_id=$3 AND role='input'",
                        expected.account_id,
                        task,
                        row["version_id"],
                    )
                    if len(refs) != 1:
                        raise ResourceError()
                    item["reference"] = self._reference(refs[0], expected)
                if query.get("availability", item["availability"]) == item["availability"]:
                    observed.append(item)
            revision = [head["filter_revision"] if head else 0, digest.hexdigest()]
            if cursor and cursor["revision"] != revision:
                raise ResourceError("restart_required")

            def sort_key(item: dict[str, Any]) -> tuple[Any, str]:
                return (
                    (item["reference"]["ordinal"], item["reference"]["reference_id"])
                    if task
                    else (item["created_at"], item["version_id"])
                )

            observed.sort(key=sort_key, reverse=task is None)
            if cursor:
                pair = tuple(cursor["last"])
                observed = [
                    i for i in observed if (sort_key(i) > pair if task else sort_key(i) < pair)
                ]
            page = observed[: query["limit"]]
            token = None
            if len(observed) > len(page):
                token = self.store._seal(
                    {
                        "purpose": "daemon.resource.catalog.cursor.v1",
                        "schema_version": 1,
                        "account": str(expected.account_id),
                        "generation": expected.generation,
                        "query": query,
                        "task": str(task) if task else None,
                        "watermark": watermark,
                        "revision": revision,
                        "last": list(sort_key(page[-1])),
                    }
                )
            return {"schema_version": 1, "items": page, "next_cursor": token}

    async def metadata(
        self, expected: CapturedGeneration, resource: UUID, version: UUID
    ) -> dict[str, Any]:
        async with (
            self.inputs._account(expected.account_id) as conn,
            guard_content_write(conn, expected),
        ):
            row = await conn.fetchrow(
                "SELECT * FROM resource_versions WHERE account_id=$1 AND resource_id=$2 AND version_id=$3 AND generation=$4",
                expected.account_id,
                resource,
                version,
                expected.generation,
            )
            if row is None:
                raise ResourceError("not_found")
            return {"schema_version": 1, "item": await self._observe(conn, expected, row)}
