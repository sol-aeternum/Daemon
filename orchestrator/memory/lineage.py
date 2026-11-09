"""Scoped memory-derived erasure. Original conversation bodies are never rewritten."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from orchestrator.memory.temporal import TemporalRefusal


async def record_sources(conn: Any, owner: UUID, memory_id: UUID, message_ids: list[UUID]):
    for message_id in message_ids:
        await conn.execute(
            "INSERT INTO memory_sources(user_id,memory_id,source_kind,source_id) "
            "VALUES($1,$2,'message',$3) ON CONFLICT DO NOTHING",
            owner,
            memory_id,
            message_id,
        )


async def depend(conn: Any, owner: UUID, parents: list[dict], kind: str, child: UUID):
    """Validate and record exact parent revisions on the publication connection."""
    for parent in sorted(parents, key=lambda row: str(row["id"])):
        row = await conn.fetchrow(
            "SELECT head_revision_id,status FROM memories WHERE user_id=$1 AND id=$2 FOR SHARE",
            owner,
            parent["id"],
        )
        if (
            row is None
            or row["status"] == "deleted"
            or row["head_revision_id"] != parent.get("head_revision_id")
        ):
            raise TemporalRefusal("memory_dependency_changed")
        await conn.execute(
            "INSERT INTO memory_dependencies VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
            owner,
            parent["id"],
            row["head_revision_id"],
            kind,
            child,
        )


async def erase(store: Any, conn: Any, owner: UUID, roots: list[UUID], *, hard: bool) -> int:
    if not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memories WHERE user_id=$1 AND id=ANY($2::uuid[]))",
        owner,
        roots,
    ):
        return 0
    ids = await conn.fetch(
        "WITH RECURSIVE erased(id) AS (SELECT id FROM memories WHERE user_id=$1 AND "
        "(id=ANY($2::uuid[]) OR (source_type IN ('dream','consolidation') AND NOT EXISTS "
        "(SELECT 1 FROM memory_dependencies d WHERE d.user_id=$1 AND d.dependent_kind='memory' AND d.dependent_id=memories.id))) "
        "UNION SELECT d.dependent_id FROM memory_dependencies d JOIN erased e ON e.id=d.parent_memory_id "
        "WHERE d.user_id=$1 AND d.dependent_kind='memory') SELECT id FROM erased ORDER BY id",
        owner,
        roots,
    )
    mids = [r["id"] for r in ids]
    if not mids:
        return 0
    # Source locks precede memory locks. Legacy lineage can over-suppress old
    # source spans, never permanently ban matching content from a new statement.
    sources = await conn.fetch(
        "SELECT DISTINCT c.id FROM conversations c JOIN memories m ON m.source_conversation_id=c.id "
        "WHERE m.user_id=$1 AND m.id=ANY($2::uuid[]) AND c.user_id=$1 ORDER BY c.id",
        owner,
        mids,
    )
    for source in sources:
        await conn.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR SHARE", source["id"])
    await conn.fetch(
        "SELECT id FROM memories WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id FOR UPDATE",
        owner,
        mids,
    )
    epoch = await conn.fetchval(
        "UPDATE memory_knowledge_clock SET erasure_epoch=erasure_epoch+1 WHERE user_id=$1 RETURNING erasure_epoch",
        owner,
    )
    await conn.execute(
        "INSERT INTO memory_erased_sources SELECT user_id,source_kind,source_id,memory_id "
        "FROM memory_sources WHERE user_id=$1 AND memory_id=ANY($2::uuid[]) ON CONFLICT DO NOTHING",
        owner,
        mids,
    )
    await conn.execute(
        "INSERT INTO memory_erased_sources(user_id,source_kind,source_id,memory_id) "
        "SELECT $1,'message',msg.id,m.id FROM memories m JOIN messages msg "
        "ON msg.conversation_id=m.source_conversation_id AND msg.user_id=$1 "
        "WHERE m.user_id=$1 AND m.id=ANY($2::uuid[]) AND NOT EXISTS "
        "(SELECT 1 FROM memory_sources s WHERE s.user_id=$1 AND s.memory_id=m.id) ON CONFLICT DO NOTHING",
        owner,
        mids,
    )
    await conn.execute(
        "INSERT INTO memory_tombstones(user_id,memory_id,erasure_epoch,erased_at,purge_after,hard) "
        "SELECT $1,unnest($2::uuid[]),$3,clock_timestamp(),"
        "CASE WHEN $4 THEN NULL ELSE clock_timestamp()+interval '30 days' END,$4 "
        "ON CONFLICT(user_id,memory_id) DO UPDATE SET hard=memory_tombstones.hard OR EXCLUDED.hard",
        owner,
        mids,
        epoch,
        hard,
    )
    await conn.execute(
        "UPDATE memories SET status='deleted',valid_to=COALESCE(valid_to,clock_timestamp()),"
        "updated_at=clock_timestamp(),content_tsv=NULL,embedding=NULL,embedding_model=NULL "
        "WHERE user_id=$1 AND id=ANY($2::uuid[])",
        owner,
        mids,
    )
    await conn.execute(
        "UPDATE memory_revision_segments s SET content_tsv=NULL,embedding=NULL,embedding_model=NULL "
        "FROM memory_revisions r WHERE s.revision_id=r.id AND r.user_id=$1 AND r.memory_id=ANY($2::uuid[])",
        owner,
        mids,
    )
    # Mixed derived records may be invalidated in full. They are not originals.
    await conn.execute(
        "DELETE FROM entities WHERE user_id=$1 AND (source_memory_id=ANY($2::uuid[]) "
        "OR linked_memory_ids && $2::uuid[] OR source_memory_id IS NULL)",
        owner,
        mids,
    )
    await conn.execute(
        "UPDATE conversations SET last_retrieved_memory_ids='[]'::jsonb,summary=NULL,summary_updated_at=NULL "
        "WHERE user_id=$1",
        owner,
    )
    await conn.execute(
        "UPDATE memory_extraction_log SET input_snippet=NULL,extracted_facts='[]'::jsonb,dedup_results='{}'::jsonb "
        "WHERE user_id=$1",
        owner,
    )
    await conn.execute(
        "UPDATE retrieval_log SET query_text='',query_embedding=NULL,candidate_memory_ids='{}'::uuid[],"
        "selected_memory_ids='{}'::uuid[],candidate_scores='{}'::jsonb,retrieval_context=NULL "
        "WHERE user_id=$1",
        owner,
    )
    await conn.execute(
        "UPDATE dream_log SET observation_memory_ids='{}'::uuid[],eligible_families='[]'::jsonb,"
        "skipped_families='[]'::jsonb,error_message=NULL "
        "WHERE user_id=$1",
        owner,
    )
    # Traces are encrypted envelopes: scrub only memory tool payloads in Python.
    rows = await conn.fetch(
        "SELECT id,tool_calls,tool_results FROM messages WHERE user_id=$1 "
        "AND (tool_results IS NOT NULL OR tool_calls IS NOT NULL)",
        owner,
    )
    for row in rows:
        for trace_field in ("tool_calls", "tool_results"):
            traces = store._decrypt_tool_trace(row[trace_field])
            changed = False
            for item in traces:
                if not isinstance(item, dict):
                    continue
                nested = item.get("function")
                name = (
                    item.get("name")
                    or item.get("tool")
                    or item.get("tool_name")
                    or (nested.get("name") if isinstance(nested, dict) else "")
                )
                if str(name).startswith("memory_"):
                    for field in ("result", "content", "output", "arguments"):
                        if field in item:
                            item[field] = "[Memory-derived payload erased]"
                            changed = True
                    if isinstance(nested, dict) and "arguments" in nested:
                        nested["arguments"] = "[Memory-derived payload erased]"
                        changed = True
            if changed:
                if trace_field == "tool_calls":
                    await conn.execute(
                        "UPDATE messages SET tool_calls=$2::jsonb WHERE id=$1",
                        row["id"],
                        store._encrypt_tool_trace(traces),
                    )
                else:
                    await conn.execute(
                        "UPDATE messages SET tool_results=$2::jsonb WHERE id=$1",
                        row["id"],
                        store._encrypt_tool_trace(traces),
                    )
    if hard:
        await conn.execute(
            "DELETE FROM memories WHERE user_id=$1 AND id=ANY($2::uuid[])", owner, mids
        )
        await conn.execute(
            "DELETE FROM memory_dependencies WHERE user_id=$1 AND "
            "(parent_memory_id=ANY($2::uuid[]) OR (dependent_kind='memory' AND dependent_id=ANY($2::uuid[])))",
            owner,
            mids,
        )
    return len(mids)
