"""Snapshot/commit boundary for queued entity projection, not all memory writers.

Recorded lineage is a conservative cloud-eligibility prerequisite, not proof of
complete historical provenance. All provider work belongs outside these APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    Connection,
    guard_content_write,
)

MAX_SOURCE_IDS = 1000
MAX_LINEAGE_IDS = 10000


class EntityProjectionStale(Exception):
    """Optional projection cannot use this plan; never refresh or replan it."""


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RowSnapshot:
    id: UUID
    values: tuple[tuple[str, Any], ...]

    @classmethod
    def read(cls, row: asyncpg.Record) -> RowSnapshot:
        return cls(row["id"], tuple(sorted((key, _freeze(value)) for key, value in row.items())))


@dataclass(frozen=True)
class ProjectionSnapshot:
    origin: CapturedGeneration
    source_ids: tuple[UUID, ...]
    memories: tuple[RowSnapshot, ...]
    conversations: tuple[RowSnapshot, ...]
    canonicals: tuple[RowSnapshot, ...]


@dataclass(frozen=True)
class ProjectionInputs:
    snapshot: ProjectionSnapshot
    memory_contents: tuple[tuple[str, UUID, str | None], ...]
    canonical_names: tuple[tuple[UUID, str], ...]


@dataclass(frozen=True)
class EntityMutation:
    canonical_id: UUID | None
    canonical_name: str
    lookup_key: str
    source_id: UUID
    alias: str | None = None
    alias_lookup_key: str | None = None


# A conversation-derived row with missing provenance is not made cloud-eligible
# by a NULL FK. Explicit nonlocal manual/import rows can legitimately have none.
_ELIGIBLE_MEMORY = """
    m.user_id = $1 AND m.status = 'active' AND m.valid_to IS NULL
    AND m.local_only = FALSE AND m.source_type != 'dream'
    AND ((m.source_conversation_id IS NULL
          AND m.source_type IN ('manual', 'import', 'user_created'))
         OR (c.user_id = $1 AND c.pipeline = 'cloud'))
"""


async def read_inputs(
    pool: asyncpg.Pool,
    encryption: ContentEncryption,
    captured: CapturedGeneration,
    source_ids: list[UUID],
) -> ProjectionInputs:
    if len(source_ids) > MAX_SOURCE_IDS:
        raise EntityProjectionStale("input_bound")
    async with pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, captured):
            sources = await conn.fetch(
                "SELECT m.* FROM memories m LEFT JOIN conversations c "
                "ON c.id=m.source_conversation_id WHERE m.id=ANY($2::uuid[]) AND "
                + _ELIGIBLE_MEMORY
                + " ORDER BY m.id",
                captured.account_id,
                source_ids,
            )
            if not sources:
                result = ProjectionInputs(ProjectionSnapshot(captured, (), (), (), ()), (), ())
                return result
            # Preserve enqueue order for the resolver's first-source grouping.
            # Lock order during revalidation is independently sorted by UUID.
            source_by_id = {row["id"]: row for row in sources}
            sources = [
                source_by_id[mid] for mid in dict.fromkeys(source_ids) if mid in source_by_id
            ]
            canonicals = await conn.fetch(
                """
                SELECT e.* FROM entities e WHERE e.user_id=$1
                  AND EXISTS (
                    SELECT 1 FROM memories m LEFT JOIN conversations c
                    ON c.id=m.source_conversation_id
                    WHERE m.id=e.source_memory_id AND
                """
                + _ELIGIBLE_MEMORY
                + """
                  ) AND NOT EXISTS (
                    SELECT 1 FROM UNNEST(e.linked_memory_ids) AS linked(id)
                    WHERE NOT EXISTS (
                      SELECT 1 FROM memories m LEFT JOIN conversations c
                      ON c.id=m.source_conversation_id WHERE m.id=linked.id AND
                """
                + _ELIGIBLE_MEMORY
                + """
                    )
                  ) ORDER BY e.created_at DESC, e.id LIMIT 1000
                """,
                captured.account_id,
            )
            memory_ids = {row["id"] for row in sources}
            for row in canonicals:
                memory_ids.add(row["source_memory_id"])
                memory_ids.update(row["linked_memory_ids"])
            if len(memory_ids) > MAX_LINEAGE_IDS:
                raise EntityProjectionStale("lineage_bound")
            memories = await conn.fetch(
                "SELECT m.* FROM memories m LEFT JOIN conversations c "
                "ON c.id=m.source_conversation_id WHERE m.id=ANY($2::uuid[]) AND "
                + _ELIGIBLE_MEMORY
                + " ORDER BY m.id",
                captured.account_id,
                sorted(memory_ids),
            )
            if {row["id"] for row in memories} != memory_ids:
                raise EntityProjectionStale("stale_plan")
            # The first source read and the canonical eligibility query were
            # separate snapshots. Refuse changed inputs, do not replace them.
            by_id = {row["id"]: RowSnapshot.read(row) for row in memories}
            if any(by_id[row["id"]] != RowSnapshot.read(row) for row in sources):
                raise EntityProjectionStale("stale_plan")
            conversation_ids = {
                row["source_conversation_id"]
                for row in memories
                if row["source_conversation_id"] is not None
            }
            conversations = await conn.fetch(
                "SELECT id,user_id,pipeline FROM conversations "
                "WHERE id=ANY($2::uuid[]) AND user_id=$1 AND pipeline='cloud' ORDER BY id",
                captured.account_id,
                sorted(conversation_ids),
            )
            if {row["id"] for row in conversations} != conversation_ids:
                raise EntityProjectionStale("stale_plan")
            snapshot = ProjectionSnapshot(
                captured,
                tuple(row["id"] for row in sources),
                tuple(RowSnapshot.read(row) for row in memories),
                tuple(RowSnapshot.read(row) for row in conversations),
                tuple(RowSnapshot.read(row) for row in canonicals),
            )
            # Only SQL-qualified source/canonical rows can reach the cipher.
            contents = tuple(
                (encryption.decrypt(row["content"]), row["id"], row["memory_slot"])
                for row in sources
            )
            names = tuple(
                (row["id"], encryption.decrypt(row["canonical_name"])) for row in canonicals
            )
            result = ProjectionInputs(snapshot, contents, names)
    return result


async def _validate(
    conn: Connection,
    snapshot: ProjectionSnapshot,
    captured: CapturedGeneration,
    *,
    writing: bool,
) -> dict[UUID, asyncpg.Record]:
    if snapshot.origin != captured:
        raise EntityProjectionStale("stale_plan")
    owner = captured.account_id
    conversations = await conn.fetch(
        "SELECT id,user_id,pipeline FROM conversations "
        "WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id FOR SHARE",
        owner,
        sorted(row.id for row in snapshot.conversations),
    )
    memories = await conn.fetch(
        "SELECT * FROM memories WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id FOR SHARE",
        owner,
        sorted(row.id for row in snapshot.memories),
    )
    # All consumed canonical names are plan inputs, including nonwinning ones.
    # Final writers acquire UPDATE directly, not a competing lock upgrade.
    lock = "FOR UPDATE" if writing else "FOR SHARE"
    canonicals = await conn.fetch(
        "SELECT * FROM entities WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id " + lock,
        owner,
        sorted(row.id for row in snapshot.canonicals),
    )
    for expected, actual in (
        (snapshot.conversations, conversations),
        (snapshot.memories, memories),
        (snapshot.canonicals, canonicals),
    ):
        if {row.id: row for row in expected} != {
            row["id"]: RowSnapshot.read(row) for row in actual
        }:
            raise EntityProjectionStale("stale_plan")
    return {row["id"]: row for row in canonicals}


async def check_inputs(
    pool: asyncpg.Pool, snapshot: ProjectionSnapshot, captured: CapturedGeneration
) -> None:
    async with pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, captured):
            await _validate(conn, snapshot, captured, writing=False)


def _aliases(encryption: ContentEncryption, value: str) -> list[str]:
    raw = json.loads(value)
    # The original DDL's empty default carries no content. Nonempty plaintext
    # arrays are not an alternate legacy decryption/eligibility bypass.
    if raw == []:
        return []
    decoded = json.loads(encryption.decrypt(raw))
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("Invalid entity aliases")
    return decoded


async def save_plan(
    pool: asyncpg.Pool,
    encryption: ContentEncryption,
    inputs: ProjectionInputs,
    captured: CapturedGeneration,
    mutations: list[EntityMutation],
) -> list[UUID]:
    saved: dict[int, UUID] = {}
    async with pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, captured):
            canonicals = await _validate(conn, inputs.snapshot, captured, writing=True)
            expected_names = dict(inputs.canonical_names)
            inserted: dict[str, UUID] = {}
            ordered = sorted(
                enumerate(mutations),
                key=lambda item: (str(item[1].canonical_id or ""), item[1].lookup_key, item[0]),
            )
            for index, mutation in ordered:
                if mutation.source_id not in inputs.snapshot.source_ids:
                    raise EntityProjectionStale("stale_plan")
                entity_id = mutation.canonical_id
                if entity_id is not None:
                    row = canonicals.get(entity_id)
                    if row is None or expected_names.get(entity_id) != mutation.canonical_name:
                        raise EntityProjectionStale("stale_plan")
                    aliases = _aliases(encryption, row["aliases"])
                    keys = list(row["alias_lookup_keys"])
                    if mutation.alias and mutation.alias != mutation.canonical_name:
                        if mutation.alias not in aliases:
                            aliases.append(mutation.alias)
                        if mutation.alias_lookup_key not in keys:
                            keys.append(mutation.alias_lookup_key)
                    await conn.execute(
                        "UPDATE entities SET aliases=$3::jsonb,alias_lookup_keys=$4::text[],"
                        "updated_at=NOW() WHERE id=$1 AND user_id=$2",
                        entity_id,
                        captured.account_id,
                        json.dumps(encryption.encrypt(json.dumps(aliases))),
                        keys,
                    )
                else:
                    entity_id = inserted.get(mutation.lookup_key)
                    if entity_id is None:
                        entity_id = await conn.fetchval(
                            """
                            INSERT INTO entities
                              (user_id,canonical_name,lookup_key,aliases,source_memory_id)
                            VALUES($1,$2,$3,$4::jsonb,$5)
                            ON CONFLICT (user_id,lookup_key) DO NOTHING RETURNING id
                            """,
                            captured.account_id,
                            encryption.encrypt(mutation.canonical_name),
                            mutation.lookup_key,
                            json.dumps(encryption.encrypt("[]")),
                            mutation.source_id,
                        )
                        if entity_id is None:
                            raise EntityProjectionStale("stale_plan")
                        inserted[mutation.lookup_key] = entity_id
                await conn.execute(
                    """
                    UPDATE entities SET linked_memory_ids=(
                      SELECT ARRAY(SELECT DISTINCT elem FROM
                        UNNEST(linked_memory_ids || ARRAY[$3::uuid]) AS elem)
                    ),updated_at=NOW() WHERE id=$1 AND user_id=$2
                    """,
                    entity_id,
                    captured.account_id,
                    mutation.source_id,
                )
                saved[index] = entity_id
    # An exception (including cancellation/failed commit) never returns success.
    return [saved[index] for index in range(len(mutations))]
