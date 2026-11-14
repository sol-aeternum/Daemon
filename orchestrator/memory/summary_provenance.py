"""Versioned summary direct-source closure; no inferred assistant derivation.

No provider work belongs in this module. Legacy plaintext is never a parent.
Original assistant bodies are sources under Scope A, not certified memory copies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from orchestrator.memory.revisions import Capture, enter
from orchestrator.memory.temporal import TemporalRefusal
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write


@dataclass(frozen=True, order=True)
class MessageSource:
    conversation_id: UUID
    message_id: UUID
    source_revision: int


@dataclass(frozen=True)
class SummaryReference:
    conversation_id: UUID
    head_id: UUID | None
    state_revision: int
    sources: tuple[MessageSource, ...]


@dataclass(frozen=True)
class SummarySnapshot:
    capture: Capture
    reference: SummaryReference
    parent_id: UUID | None
    expected_cursor: str
    checkpoint: tuple[tuple[str, int, str, str | None], ...]
    rendered: tuple[MessageSource, ...]
    baseline: int
    claimed: int
    snapshot_at: datetime
    render_limit: int | None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _cursor(row) -> str:
    meta = _json(row["metadata"]) or {}
    return json.dumps(
        {
            k: meta.get(k)
            for k in (
                "last_summarized_msg_count",
                "summary_continuation_pending",
                "last_summarized_at_time",
            )
        },
        sort_keys=True,
    )


def _member(row):
    return (
        str(row["id"]),
        int(row["source_revision"]),
        row["created_at"].isoformat(),
        row["status"],
    )


async def _rows(conn, owner, conversation, snapshot_at):
    return await conn.fetch(
        "SELECT id,conversation_id,user_id,source_revision,created_at,status,role FROM messages "
        "WHERE user_id=$1 AND conversation_id=$2 AND created_at<=$3 ORDER BY created_at,id",
        owner,
        conversation,
        snapshot_at,
    )


async def _qualified(conn, owner: UUID, version: UUID | None, *, cloud: bool):
    """Qualify complete flattened source identities BEFORE returning payloads."""
    row = await conn.fetchrow(
        "SELECT v.*,p.ciphertext,(SELECT jsonb_agg(jsonb_build_array(d.conversation_id,d.message_id,d.source_revision) "
        "ORDER BY d.conversation_id,d.message_id) FROM conversation_summary_sources d WHERE d.version_id=v.id) AS sources "
        "FROM conversation_summary_versions v JOIN conversations c ON c.id=v.conversation_id "
        "LEFT JOIN conversation_summary_payloads p ON p.version_id=v.id AND p.user_id=v.user_id AND p.format='fernet-v1' "
        "JOIN entitlement_accounts a ON a.user_id=v.user_id "
        "WHERE v.id=$1 AND v.user_id=$2 AND c.user_id=$2 AND ($3::bool=FALSE OR c.pipeline='cloud') "
        "AND a.status='active' AND a.content_generation=v.account_generation "
        "AND v.format=1 AND v.sealed AND v.revoked_at IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM account_deletions d WHERE d.account_id=$2 AND d.closed_at IS NULL) "
        "AND NOT EXISTS(SELECT 1 FROM account_suspension_causes s WHERE s.account_id=$2) "
        "AND v.source_count=(SELECT count(*) FROM conversation_summary_sources d WHERE d.version_id=v.id) "
        "AND NOT EXISTS(SELECT 1 FROM conversation_summary_sources d "
        "LEFT JOIN messages m ON m.id=d.message_id LEFT JOIN conversations sc ON sc.id=d.conversation_id "
        "WHERE d.version_id=v.id AND (d.user_id!=$2 OR m.id IS NULL OR m.user_id!=$2 "
        "OR m.conversation_id!=d.conversation_id OR m.source_revision!=d.source_revision "
        "OR (m.status IS NOT NULL AND m.status!='complete') OR sc.user_id IS DISTINCT FROM $2 "
        "OR ($3 AND sc.pipeline IS DISTINCT FROM 'cloud') "
        "OR EXISTS(SELECT 1 FROM memory_erased_sources e WHERE e.user_id=$2 "
        "AND e.source_kind='message' AND e.source_id=d.message_id)))",
        version,
        owner,
        cloud,
    )
    if row is None:
        return None
    return row, tuple(
        MessageSource(UUID(r[0]), UUID(r[1]), r[2]) for r in (_json(row["sources"]) or [])
    )


async def qualify(
    conn, owner: UUID, version: UUID | None, *, cloud: bool
) -> tuple[MessageSource, ...] | None:
    result = await _qualified(conn, owner, version, cloud=cloud)
    return result[1] if result is not None else None


async def read_on_connection(store, conn, owner: UUID, conversation: UUID, *, cloud: bool = True):
    row = await conn.fetchrow(
        "SELECT id,summary_head_version_id,summary_state_revision FROM conversations "
        "WHERE id=$1 AND user_id=$2 AND (NOT $3::bool OR pipeline='cloud')",
        conversation,
        owner,
        cloud,
    )
    if row is None:
        raise TemporalRefusal("summary_source_unavailable")
    head = row["summary_head_version_id"]
    qualified = await _qualified(conn, owner, head, cloud=cloud) if head else None
    sources = qualified[1] if qualified is not None else None
    ref = SummaryReference(conversation, head, row["summary_state_revision"], sources or ())
    if sources is None:
        return ref, None
    payload = qualified[0]["ciphertext"] if qualified is not None else None
    return ref, store.encryption.decrypt(payload) if payload else None


async def read(store, owner: UUID, conversation: UUID, *, cloud: bool = True):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        return await read_on_connection(store, conn, owner, conversation, cloud=cloud)


async def materialize(store, rows):
    """Owner management projection; never expose the unqualified legacy cache."""
    result = []
    for raw in rows:
        row = dict(raw)
        row["summary"] = None
        if row.get("summary_head_version_id") is not None:
            try:
                _, row["summary"] = await read(store, row["user_id"], row["id"], cloud=False)
            except TemporalRefusal:
                pass
        result.append(row)
    return result


async def prepare(
    store, conversation: UUID, origin: CapturedGeneration, *, limit: int, render_limit: int | None
):
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, origin):
            row = await conn.fetchrow(
                "SELECT * FROM conversations WHERE id=$1 AND user_id=$2 AND pipeline='cloud' FOR SHARE",
                conversation,
                origin.account_id,
            )
            if row is None:
                return None
            ref, previous = await read_on_connection(store, conn, origin.account_id, conversation)
            prior = (
                await conn.fetchrow(
                    "SELECT checkpoint_count,checkpoint FROM conversation_summary_versions WHERE id=$1",
                    ref.head_id,
                )
                if ref.head_id is not None
                and await qualify(conn, origin.account_id, ref.head_id, cloud=True) is not None
                else None
            )
            baseline = int(prior["checkpoint_count"]) if prior else 0
            now = await conn.fetchval("SELECT clock_timestamp()")
            rows = await _rows(conn, origin.account_id, conversation, now)
            # Old prefix membership is a cursor proof, NOT a model input closure.
            prior_members = _json(prior["checkpoint"]) if prior else []
            if (
                prior_members
                and [list(_member(r)) for r in rows[: len(prior_members)]] != prior_members
            ):
                previous, baseline, prior = None, 0, None
            finalized = [r for r in rows if r["status"] in (None, "complete")]
            prefix = []
            for r in rows:
                if r["status"] not in (None, "complete", "error", "cancelled"):
                    break
                prefix.append(r)
            eligible = [r for r in prefix if r["status"] in (None, "complete")]
            selected = eligible[baseline : baseline + min(100, max(1, limit))]
            checkpoint = (
                prefix[: next(i + 1 for i, r in enumerate(prefix) if r["id"] == selected[-1]["id"])]
                if selected
                else prefix
            )
            suppressed = {
                r["source_id"]
                for r in await conn.fetch(
                    "SELECT source_id FROM memory_erased_sources WHERE user_id=$1 AND source_kind='message' "
                    "AND source_id=ANY($2::uuid[])",
                    origin.account_id,
                    [r["id"] for r in selected],
                )
            }
            visible = [r for r in selected if r["id"] not in suppressed]
            contents = await conn.fetch(
                "SELECT id,source_revision,content FROM messages WHERE user_id=$1 AND conversation_id=$2 "
                "AND id=ANY($3::uuid[])",
                origin.account_id,
                conversation,
                [r["id"] for r in visible],
            )
            by_id = {r["id"]: r for r in contents}
            if any(
                r["id"] not in by_id or by_id[r["id"]]["source_revision"] != r["source_revision"]
                for r in visible
            ):
                raise TemporalRefusal("summary_source_changed")
            messages = []
            for r in selected:
                content = (
                    store.encryption.decrypt(by_id[r["id"]]["content"])
                    if r["id"] not in suppressed
                    else ""
                )
                messages.append(dict(r, content=content))
            epoch = int(
                await conn.fetchval(
                    "SELECT erasure_epoch FROM memory_knowledge_clock WHERE user_id=$1",
                    origin.account_id,
                )
                or 0
            )
            snapshot = SummarySnapshot(
                Capture(origin, epoch),
                ref,
                ref.head_id if prior is not None else None,
                _cursor(row),
                tuple(_member(r) for r in checkpoint),
                tuple(MessageSource(conversation, r["id"], r["source_revision"]) for r in visible),
                baseline,
                baseline + len(selected),
                now,
                render_limit,
            )
            result = dict(row)
            result.update(
                summary=previous,
                persisted_baseline=baseline,
                snapshot_at=now,
                contiguous_count=len(eligible),
                finalized_count=len(finalized),
                has_hole=len(prefix) < len(rows),
                current_message_count=len(finalized),
                messages=messages,
                memory_erasure_epoch=epoch,
                summary_snapshot=snapshot,
            )
            return result


async def validate_reference(conn, owner: UUID, reference: SummaryReference):
    row = await conn.fetchrow(
        "SELECT summary_head_version_id,summary_state_revision FROM conversations "
        "WHERE id=$1 AND user_id=$2 AND pipeline='cloud' FOR SHARE",
        reference.conversation_id,
        owner,
    )
    if row is None or (row["summary_head_version_id"], row["summary_state_revision"]) != (
        reference.head_id,
        reference.state_revision,
    ):
        raise TemporalRefusal("summary_head_changed")
    if reference.sources:
        # A summary can depend on messages outside the current extraction chunk.
        # Keep every source stable through the caller's publication transaction;
        # generic message edits do not acquire the account/conversation guard.
        await conn.fetch(
            "SELECT id FROM messages WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id FOR SHARE",
            owner,
            sorted(source.message_id for source in reference.sources),
        )
        # Recheck after any lock wait, not against the pre-lock statement snapshot.
        if await qualify(conn, owner, reference.head_id, cloud=True) != reference.sources:
            raise TemporalRefusal("summary_source_changed")


async def _validate(conn, snapshot: SummarySnapshot, *, lock: bool):
    origin = snapshot.capture.origin
    await validate_reference(conn, origin.account_id, snapshot.reference)
    row = await conn.fetchrow(
        "SELECT * FROM conversations WHERE id=$1", snapshot.reference.conversation_id
    )
    if _cursor(row) != snapshot.expected_cursor:
        raise TemporalRefusal("summary_cursor_changed")
    epoch = int(
        await conn.fetchval(
            "SELECT erasure_epoch FROM memory_knowledge_clock WHERE user_id=$1", origin.account_id
        )
        or 0
    )
    if epoch != snapshot.capture.erasure_epoch:
        raise TemporalRefusal("memory_erasure_changed")
    if (
        snapshot.parent_id
        and await qualify(conn, origin.account_id, snapshot.parent_id, cloud=True)
        != snapshot.reference.sources
    ):
        raise TemporalRefusal("summary_source_changed")
    if snapshot.checkpoint:
        last = snapshot.checkpoint[-1]
        rows = await conn.fetch(
            "SELECT id,source_revision,created_at,status FROM messages WHERE user_id=$1 AND conversation_id=$2 "
            "AND (created_at,id)<=($3::timestamptz,$4::uuid) ORDER BY created_at,id"
            + (" FOR SHARE" if lock else ""),
            origin.account_id,
            snapshot.reference.conversation_id,
            datetime.fromisoformat(last[2]),
            UUID(last[0]),
        )
        if tuple(_member(r) for r in rows) != snapshot.checkpoint:
            raise TemporalRefusal("summary_checkpoint_changed")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memory_erased_sources WHERE user_id=$1 AND source_kind='message' "
        "AND source_id=ANY($2::uuid[]))",
        origin.account_id,
        [s.message_id for s in snapshot.rendered],
    ):
        raise TemporalRefusal("summary_source_erased")


async def check(store, snapshot: SummarySnapshot) -> bool:
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, snapshot.capture.origin):
            try:
                await _validate(conn, snapshot, lock=False)
            except TemporalRefusal:
                return False
            return True


async def publish(store, snapshot: SummarySnapshot, text: str, *, continuation: bool) -> bool:
    origin = snapshot.capture.origin
    sources = {s.message_id: s for s in snapshot.reference.sources} if snapshot.parent_id else {}
    for source in snapshot.rendered:
        if source.message_id in sources and sources[source.message_id] != source:
            raise TemporalRefusal("summary_source_version_conflict")
        sources[source.message_id] = source
    if sources and not text.strip():
        return False
    ciphertext = store.encryption.encrypt(text) if sources else None
    version = uuid4()
    async with store._pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        async with guard_content_write(conn, origin):
            await enter(conn, snapshot.capture, ordered=True)
            try:
                await _validate(conn, snapshot, lock=True)
            except TemporalRefusal:
                return False
            await conn.execute(
                "INSERT INTO conversation_summary_versions(id,user_id,conversation_id,format,account_generation,"
                "erasure_epoch,parent_version_id,source_count,checkpoint_count,checkpoint,rendered_members,render_limit,checkpoint_only) "
                "VALUES($1,$2,$3,1,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12)",
                version,
                origin.account_id,
                snapshot.reference.conversation_id,
                origin.generation,
                snapshot.capture.erasure_epoch,
                snapshot.parent_id,
                len(sources),
                snapshot.claimed,
                json.dumps(snapshot.checkpoint),
                json.dumps([[str(s.message_id), s.source_revision] for s in snapshot.rendered]),
                snapshot.render_limit,
                not sources,
            )
            for source in sorted(sources.values()):
                await conn.execute(
                    "INSERT INTO conversation_summary_sources VALUES($1,$2,$3,$4,$5)",
                    version,
                    origin.account_id,
                    source.conversation_id,
                    source.message_id,
                    source.source_revision,
                )
            if ciphertext is not None:
                await conn.execute(
                    "INSERT INTO conversation_summary_payloads VALUES($1,$2,'fernet-v1',$3)",
                    version,
                    origin.account_id,
                    ciphertext,
                )
            await conn.execute(
                "UPDATE conversation_summary_versions SET sealed=true WHERE id=$1", version
            )
            row = await conn.fetchrow(
                "UPDATE conversations SET summary=NULL,summary_head_version_id=$3,summary_updated_at=clock_timestamp(),"
                "updated_at=clock_timestamp(),last_activity_at=clock_timestamp(),metadata=COALESCE(metadata,'{}'::jsonb)||"
                "jsonb_build_object('last_summarized_msg_count',$4::bigint,'summary_continuation_pending',$5::bool,"
                "'last_summarized_at_time',$6::text) WHERE id=$1 AND user_id=$2 "
                "AND summary_head_version_id IS NOT DISTINCT FROM $7 AND summary_state_revision=$8 RETURNING id",
                snapshot.reference.conversation_id,
                origin.account_id,
                version,
                snapshot.claimed,
                continuation,
                snapshot.snapshot_at.isoformat(),
                snapshot.reference.head_id,
                snapshot.reference.state_revision,
            )
            if row is None:
                raise TemporalRefusal("summary_head_changed")
            # Replaceable-cache retention: old payload is not a history API.
            await conn.execute(
                "DELETE FROM conversation_summary_payloads WHERE user_id=$1 AND version_id "
                "IN (SELECT id FROM conversation_summary_versions WHERE user_id=$1 AND conversation_id=$2 AND id!=$3)",
                origin.account_id,
                snapshot.reference.conversation_id,
                version,
            )
    return True


async def invalidate(conn, owner: UUID, *, conversation: UUID | None = None):
    """Caller owns account/temporal/source transaction. Scope A uses all owner versions."""
    ids = [
        r["id"]
        for r in await conn.fetch(
            "SELECT id FROM conversation_summary_versions v WHERE user_id=$1 AND ($2::uuid IS NULL "
            "OR conversation_id=$2 OR EXISTS(SELECT 1 FROM conversation_summary_sources s "
            "WHERE s.version_id=v.id AND s.conversation_id=$2))",
            owner,
            conversation,
        )
    ]
    await conn.execute(
        "UPDATE conversation_summary_versions SET revoked_at=COALESCE(revoked_at,clock_timestamp()) "
        "WHERE user_id=$1 AND id=ANY($2::uuid[])",
        owner,
        ids,
    )
    await conn.execute(
        "DELETE FROM conversation_summary_payloads WHERE user_id=$1 AND version_id=ANY($2::uuid[])",
        owner,
        ids,
    )
    await conn.execute(
        "UPDATE conversations SET summary=NULL,summary_updated_at=NULL,summary_head_version_id=NULL,"
        "metadata=COALESCE(metadata,'{}'::jsonb)-'last_summarized_msg_count'-'last_summarized_at_time' "
        "WHERE user_id=$1 AND ($2::uuid IS NULL OR id=$2 OR summary_head_version_id=ANY($3::uuid[]))",
        owner,
        conversation,
        ids,
    )
