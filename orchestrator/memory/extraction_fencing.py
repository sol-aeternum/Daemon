"""Queued extraction's source/commit boundary, not a generic memory fence.

Provider work never runs in these transactions. Each fact and each checkpoint
owns a separate account-first transaction; committed prefixes remain durable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from orchestrator.memory.entity_projection import RowSnapshot
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    Connection,
    guard_content_write,
)


class ExtractionRefused(Exception):
    """Terminal unsafe source/plan; never cure by recapturing an account."""


MESSAGE_FIELDS = "id,user_id,conversation_id,created_at,role,status,content,tool_calls,tool_results"
CONVERSATION_FIELDS = "id,user_id,pipeline"
ELIGIBLE = """
 m.user_id=$1 AND m.local_only=FALSE AND m.status='active' AND m.valid_to IS NULL
 AND m.source_type!='dream'
 AND ((m.source_conversation_id IS NULL AND m.source_type IN ('manual','import','user_created'))
      OR (c.user_id=$1 AND c.pipeline='cloud'))
"""


@dataclass(frozen=True)
class ExtractionInputs:
    origin: CapturedGeneration
    conversation: RowSnapshot
    messages: tuple[RowSnapshot, ...]
    summary: tuple[Any, ...] | None = None

    def subset(self, ids: set[UUID]) -> ExtractionInputs:
        return ExtractionInputs(
            self.origin, self.conversation, tuple(m for m in self.messages if m.id in ids)
        )


async def _conversation(conn: Connection, origin: CapturedGeneration, cid: UUID):
    row = await conn.fetchrow(
        "SELECT " + CONVERSATION_FIELDS + " FROM conversations "
        "WHERE id=$1 AND user_id=$2 AND pipeline='cloud' FOR SHARE",
        cid,
        origin.account_id,
    )
    if row is None:
        raise ExtractionRefused("source_unavailable")
    return row


async def read_page(store: Any, origin: CapturedGeneration, cid: UUID):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, origin):
            conversation = await _conversation(conn, origin, cid)
            cursor = await conn.fetchrow(
                "SELECT COALESCE(last_message_observed_at,created_at) AS watermark,"
                "dedup_results->>'last_message_id' AS message_id FROM memory_extraction_log "
                "WHERE conversation_id=$1 AND user_id=$2 "
                "AND dedup_results->>'cursor_checkpoint' IS DISTINCT FROM 'false' "
                "ORDER BY COALESCE(last_message_observed_at,created_at) DESC,"
                "dedup_results->>'last_message_id' DESC NULLS LAST,created_at DESC LIMIT 1",
                cid,
                origin.account_id,
            )
            at, mid = (cursor["watermark"], cursor["message_id"]) if cursor else (None, None)
            rows = await conn.fetch(
                "SELECT " + MESSAGE_FIELDS + " FROM messages WHERE conversation_id=$1 "
                "AND user_id=$2 AND ($3::timestamptz IS NULL OR "
                "($4::text IS NULL AND created_at >= $3) OR created_at > $3 "
                "OR (created_at=$3 AND id::text>$4)) ORDER BY created_at,id::text LIMIT 250",
                cid,
                origin.account_id,
                at,
                mid,
            )
            rows = _finalized_prefix(rows)
            snapshot = ExtractionInputs(
                origin, RowSnapshot.read(conversation), tuple(RowSnapshot.read(r) for r in rows)
            )
            messages = [_decode(store, row) for row in rows]
    return snapshot, messages


def _finalized_prefix(rows):
    result = []
    for row in rows:
        if row["status"] not in (None, "complete", "error", "cancelled"):
            break
        result.append(row)
    return result


def _decode(store, row):
    message = dict(row)
    if message["status"] in ("error", "cancelled"):
        message.update(content="", tool_calls=[], tool_results=[], _extraction_skip=True)
    else:
        message["content"] = store.encryption.decrypt(message["content"])
        store._decrypt_message_tool_traces(message)
    return message


async def read_references(store: Any, origin: CapturedGeneration, cid: UUID, ids: list[UUID]):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, origin):
            conversation = await _conversation(conn, origin, cid)
            # The owner prefix, rather than just each referenced row, proves that
            # a reference cannot jump a mutable hole and checkpoint past it.
            rows = await conn.fetch(
                "SELECT " + MESSAGE_FIELDS + " FROM messages WHERE conversation_id=$1 "
                "AND user_id=$2 AND id=ANY($3::uuid[]) ORDER BY created_at,id::text",
                cid,
                origin.account_id,
                ids,
            )
            hole = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM messages h WHERE h.conversation_id=$1 "
                "AND h.user_id=$2 AND h.status IS NOT NULL "
                "AND h.status NOT IN ('complete','error','cancelled') "
                "AND (h.created_at,h.id::text) <= (SELECT r.created_at,r.id::text "
                "FROM messages r WHERE r.id=ANY($3::uuid[]) AND r.conversation_id=$1 "
                "AND r.user_id=$2 ORDER BY r.created_at DESC,r.id::text DESC LIMIT 1))",
                cid,
                origin.account_id,
                ids,
            )
            if hole:
                raise ExtractionRefused("source_unavailable")
            cursor = await conn.fetchrow(
                "SELECT COALESCE(last_message_observed_at,created_at) AS at,"
                "dedup_results->>'last_message_id' AS mid FROM memory_extraction_log "
                "WHERE conversation_id=$1 AND user_id=$2 "
                "AND dedup_results->>'cursor_checkpoint' IS DISTINCT FROM 'false' "
                "ORDER BY COALESCE(last_message_observed_at,created_at) DESC,"
                "dedup_results->>'last_message_id' DESC NULLS LAST,created_at DESC LIMIT 1",
                cid,
                origin.account_id,
            )
            covered = await conn.fetch(
                "SELECT id FROM messages WHERE conversation_id=$1 AND user_id=$2 "
                "AND ($3::timestamptz IS NULL OR created_at>$3 "
                "OR (created_at=$3 AND ($4::text IS NULL OR id::text>$4))) "
                "AND (created_at,id::text) <= (SELECT created_at,id::text FROM messages "
                "WHERE conversation_id=$1 AND user_id=$2 AND id=ANY($5::uuid[]) "
                "ORDER BY created_at DESC,id::text DESC LIMIT 1) "
                "ORDER BY created_at,id::text LIMIT 251",
                cid,
                origin.account_id,
                cursor["at"] if cursor else None,
                cursor["mid"] if cursor else None,
                ids,
            )
            covered_ids = {r["id"] for r in covered}
            if not covered_ids <= set(ids) or {r["id"] for r in rows} != set(ids):
                raise ExtractionRefused("source_gap")
            prefix = {row["id"]: row for row in _finalized_prefix(rows) if row["id"] in covered_ids}
            if not covered_ids <= prefix.keys():
                raise ExtractionRefused("source_unavailable")
            selected = [prefix[mid] for mid in ids if mid in covered_ids]
            snapshot = ExtractionInputs(
                origin, RowSnapshot.read(conversation), tuple(RowSnapshot.read(r) for r in selected)
            )
            messages = [_decode(store, row) for row in selected]
    return snapshot, messages


async def validate(conn: Connection, inputs: ExtractionInputs):
    origin = inputs.origin
    current = await _conversation(conn, origin, inputs.conversation.id)
    if RowSnapshot.read(current) != inputs.conversation:
        raise ExtractionRefused("source_changed")
    rows = await conn.fetch(
        "SELECT " + MESSAGE_FIELDS + " FROM messages WHERE user_id=$1 "
        "AND conversation_id=$2 AND id=ANY($3::uuid[]) ORDER BY id FOR SHARE",
        origin.account_id,
        inputs.conversation.id,
        sorted(m.id for m in inputs.messages),
    )
    if {r["id"]: RowSnapshot.read(r) for r in rows} != {m.id: m for m in inputs.messages}:
        raise ExtractionRefused("source_changed")
    if inputs.summary is not None:
        row = await conn.fetchrow(
            "SELECT summary,summary_updated_at FROM conversations WHERE id=$1",
            inputs.conversation.id,
        )
        if row is None or tuple(row.values()) != inputs.summary:
            raise ExtractionRefused("source_changed")


async def check(store: Any, inputs: ExtractionInputs):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            await validate(conn, inputs)


async def with_summary(store: Any, inputs: ExtractionInputs):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            await validate(conn, inputs)
            row = await conn.fetchrow(
                "SELECT summary,summary_updated_at FROM conversations WHERE id=$1",
                inputs.conversation.id,
            )
            values = tuple(row.values())
            # Conversation summaries retain their existing storage representation.
            summary = row["summary"]
    return ExtractionInputs(inputs.origin, inputs.conversation, inputs.messages, values), summary


async def discover(store: Any, inputs: ExtractionInputs, incoming: Any, embedding: Any):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            await validate(conn, inputs)
            rows = await conn.fetch(
                "SELECT m.* FROM memories m LEFT JOIN conversations c "
                "ON c.id=m.source_conversation_id WHERE " + ELIGIBLE + " "
                "AND m.category=$2 AND m.tier='l1' AND ("
                "($4::text IS NOT NULL AND split_part(lower(m.memory_slot),'.',1)=$4) "
                "OR m.content_tsv @@ plainto_tsquery('english',$3) "
                "OR ($5::vector IS NOT NULL AND m.embedding IS NOT NULL AND m.embedding_model=$6)) "
                "ORDER BY COALESCE(m.content_tsv @@ plainto_tsquery('english',$3),FALSE) DESC,"
                "ts_rank(m.content_tsv,plainto_tsquery('english',$3)) DESC NULLS LAST,"
                "CASE WHEN m.embedding_model=$6 THEN m.embedding <=> $5::vector END ASC NULLS LAST,"
                "m.updated_at DESC,m.id LIMIT 6",
                incoming.user_id,
                incoming.category,
                incoming.content,
                incoming.slot.strip().lower().split(".")[0] if incoming.slot else None,
                json.dumps(embedding.embeddings[0]) if embedding else None,
                embedding.storage_model if embedding else None,
            )
            # Capture only privacy/provenance here; full semantic state belongs
            # to the existing CandidateSnapshot and its conservative fallback.
            privacy = {row["id"]: _privacy(row) for row in rows}
            dependencies = await _provenance(conn, inputs.origin, rows)
            decoded = [store._memory_row_to_dict(row) for row in rows]
    return decoded, privacy, dependencies


def _privacy(row):
    return tuple(row[k] for k in ("user_id", "local_only", "source_type", "source_conversation_id"))


async def _provenance(conn, origin, rows, *, nowait=False):
    ids = sorted({r["source_conversation_id"] for r in rows if r["source_conversation_id"]})
    result = {}
    for cid in ids:
        row = await conn.fetchrow(
            "SELECT " + CONVERSATION_FIELDS + " FROM conversations "
            "WHERE id=$1 AND user_id=$2 AND pipeline='cloud' FOR SHARE"
            + (" NOWAIT" if nowait else ""),
            cid,
            origin.account_id,
        )
        if row is None:
            raise ExtractionRefused("canonical_unavailable")
        result[cid] = RowSnapshot.read(row)
    return result


async def commit_fact(
    store: Any,
    inputs: ExtractionInputs,
    plan: Any,
    embedding: Any,
    privacy: dict,
    dependencies: dict,
):
    from orchestrator.memory.equivalence import eligible, may_merge_sources

    incoming = plan.incoming
    result = None
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            # Conversation dependencies precede all message/memory locks.
            conversations = {**dependencies, inputs.conversation.id: inputs.conversation}
            for cid, expected in sorted(conversations.items()):
                if RowSnapshot.read(await _conversation(conn, inputs.origin, cid)) != expected:
                    raise ExtractionRefused("canonical_changed")
            await validate(conn, inputs)
            locked = {}
            for mid in sorted(privacy):
                row = await conn.fetchrow(
                    "SELECT * FROM memories WHERE id=$1 AND user_id=$2 FOR UPDATE",
                    mid,
                    inputs.origin.account_id,
                )
                if row is None or _privacy(row) != privacy[mid]:
                    raise ExtractionRefused("canonical_changed")
                locked[mid] = row
            selected = plan.selected
            row = locked.get(selected.memory_id) if selected else None
            if row is not None:
                decoded = store._memory_row_to_dict(row)
                if (
                    eligible(decoded, incoming)
                    and selected.matches(decoded)
                    and may_merge_sources(incoming.source_type, decoded["source_type"])
                ):
                    await store.touch_memory(selected.memory_id, conn=conn)
                    result = decoded, False
                else:
                    row = None
            if row is None:

                async def qualify(raw):
                    # Late conflicts never decrypt first. NOWAIT avoids reverse
                    # provenance/content lock waits; ARQ retries the suffix.
                    current = await conn.fetchrow(
                        "SELECT m.* FROM memories m LEFT JOIN conversations c "
                        "ON c.id=m.source_conversation_id WHERE m.id=$2 AND "
                        + ELIGIBLE
                        + " FOR UPDATE OF m NOWAIT",
                        inputs.origin.account_id,
                        raw["id"],
                    )
                    if current is None:
                        raise ExtractionRefused("canonical_unavailable")
                    if current["content_hash"] != raw["content_hash"]:
                        # A concurrent edit after exact lookup is not a reuse
                        # verdict. Retry the uncheckpointed suffix with its same
                        # original capture; never suppress the incoming fact.
                        raise RuntimeError("exact conflict changed")
                    await _provenance(conn, inputs.origin, [current], nowait=True)
                    return current

                result = await store._insert_memory_with_outcome(
                    incoming.user_id,
                    incoming.content,
                    incoming.category,
                    incoming.source_type,
                    source_conversation_id=incoming.conversation_id,
                    confidence=incoming.confidence,
                    status=incoming.status,
                    local_only=False,
                    memory_slot=incoming.slot,
                    embedding=embedding.embeddings[0] if embedding else None,
                    embedding_model=embedding.storage_model if embedding else None,
                    conn=conn,
                    row_qualifier=qualify,
                )
    if result is None:
        raise RuntimeError("fact disposition unavailable")
    return result  # Only a committed fact can become prefix evidence.


async def checkpoint(store: Any, inputs: ExtractionInputs, **kwargs):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            await validate(conn, inputs)
            result = await store.log_extraction(conn=conn, **kwargs)
    return result


async def committed_projection_ids(store: Any, inputs: ExtractionInputs, ids: list[UUID]):
    """Qualify genuinely committed facts, not a failed suffix's message snapshot.

    A later source edit cannot uncommit an earlier fact. Its current owner/cloud
    provenance and original account currency still must authorize projection.
    No contents are decrypted and no providers/Redis run under these locks.
    """
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, inputs.origin):
            await _conversation(conn, inputs.origin, inputs.conversation.id)
            rows = await conn.fetch(
                "SELECT m.id,m.source_conversation_id FROM memories m LEFT JOIN conversations c "
                "ON c.id=m.source_conversation_id WHERE m.id=ANY($2::uuid[]) AND " + ELIGIBLE,
                inputs.origin.account_id,
                ids,
            )
            await _provenance(conn, inputs.origin, rows)
            # Re-read eligibility after provenance locks without decrypting.
            eligible_ids = await conn.fetch(
                "SELECT m.id FROM memories m LEFT JOIN conversations c ON c.id=m.source_conversation_id "
                "WHERE m.id=ANY($2::uuid[]) AND " + ELIGIBLE + " ORDER BY m.id FOR SHARE OF m",
                inputs.origin.account_id,
                [r["id"] for r in rows],
            )
    return {r["id"] for r in eligible_ids}
