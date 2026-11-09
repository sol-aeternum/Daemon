"""Same-connection revision boundary and conservative committed-cutoff observation.

The database trigger rejects semantic DML without this boundary. This is not a
global account fence and does not confer authority on callers of raw SQL.
"""

from __future__ import annotations

import inspect
import logging
import asyncio
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any
from uuid import UUID

from orchestrator.memory.temporal import KnowledgeWatermark, TemporalReadScope, TemporalRefusal
from orchestrator.services.account_control.guard import (
    CapturedGeneration,
    capture_generation,
    guard_content_write,
    WriteGuardRefusal,
)


@dataclass(frozen=True)
class Capture:
    origin: CapturedGeneration
    erasure_epoch: int


@dataclass(frozen=True)
class ActiveMutation:
    store: Any
    conn: Any
    capture: Capture
    task: Any


_active: ContextVar[ActiveMutation | None] = ContextVar("memory_revision_transaction", default=None)
_capture: ContextVar[Capture | None] = ContextVar("memory_original_capture", default=None)
_read_scope: ContextVar[TemporalReadScope | None] = ContextVar(
    "memory_temporal_scope", default=None
)


@asynccontextmanager
async def read_scope(scope: TemporalReadScope | None):
    token = _read_scope.set(scope)
    try:
        yield
    finally:
        _read_scope.reset(token)


def current_read_scope() -> TemporalReadScope | None:
    return _read_scope.get()


def scoped_stream(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        store, owner = kwargs.get("memory_store"), kwargs.get("user_id")
        if _capture.get() is None and store is not None and isinstance(owner, UUID):
            async with original_capture(store, owner), aclosing(wrapped(*args, **kwargs)) as stream:
                async for frame in stream:
                    yield frame
            return
        scope = kwargs.get("temporal_scope")
        if scope is None:
            async with aclosing(function(*args, **kwargs)) as stream:
                async for frame in stream:
                    yield frame
            return
        if store is None or not isinstance(owner, UUID):
            raise TemporalRefusal("historical_memory_store_required")
        if not isinstance(scope, TemporalReadScope):
            scope = TemporalReadScope.model_validate(scope)
        scope = await resolve(store, owner, scope)
        async with read_scope(scope), aclosing(function(*args, **kwargs)) as stream:
            async for frame in stream:
                yield frame

    return wrapped


def trace_write(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        args_bound = signature.bind(self, *args, **kwargs).arguments
        traces = [*(args_bound.get("tool_results") or []), *(args_bound.get("tool_calls") or [])]
        if not any(
            isinstance(t, dict)
            and str(t.get("name") or (t.get("function") or {}).get("name") or "").startswith(
                "memory_"
            )
            for t in traces
        ):
            return await function(self, *args, **kwargs)
        original = _capture.get()
        if original is None:
            raise TemporalRefusal("memory_trace_original_capture_required")
        owner = args_bound.get("user_id")
        if owner is None and args_bound.get("message_id") is not None:
            owner = await self._pool.fetchval(
                "SELECT user_id FROM messages WHERE id=$1", args_bound["message_id"]
            )
        if owner != original.origin.account_id:
            raise TemporalRefusal("memory_trace_owner_mismatch")
        async with mutation(self, original, conn=args_bound.get("conn")):
            return await function(self, *args, **kwargs)

    return wrapped


def temporal_tool(function):
    @wraps(function)
    async def wrapped(self, **kwargs):
        parent = current_read_scope()
        requested = {
            k: kwargs[k] for k in ("event_at", "known_at", "watermark") if kwargs.get(k) is not None
        }
        try:
            scope = parent
            if requested:
                child = TemporalReadScope.model_validate(requested)
                scope = await resolve(self.store, self.user_id, child)
                if parent is not None:
                    if parent.event_at is not None and child.event_at not in (
                        None,
                        parent.event_at,
                    ):
                        raise TemporalRefusal("parent_event_scope_cannot_be_widened")
                    if (
                        parent.watermark is None
                        or scope.watermark is None
                        or scope.watermark.sequence > parent.watermark.sequence
                    ):
                        raise TemporalRefusal("parent_knowledge_scope_cannot_be_widened")
                    scope = TemporalReadScope(
                        event_at=parent.event_at or scope.event_at, watermark=scope.watermark
                    )
            async with read_scope(scope):
                return await function(self, **kwargs)
        except (TemporalRefusal, ValueError) as exc:
            return f"Memory temporal scope unavailable: {exc}"

    return wrapped


async def capture(store: Any, owner: UUID) -> Capture:
    origin = await capture_generation(store._database_pool, owner)
    epoch = await store._database_pool.fetchval(
        "SELECT erasure_epoch FROM memory_knowledge_clock WHERE user_id=$1", owner
    )
    return Capture(origin, int(epoch or 0))


async def capture_for(store: Any, owner: UUID) -> Capture:
    value = _capture.get()
    if value is not None:
        if value.origin.account_id != owner:
            raise TemporalRefusal("memory_capture_owner_mismatch")
        return value
    return await capture(store, owner)


@asynccontextmanager
async def original_capture(store: Any, owner: UUID):
    # Nested tools retain the request's original epoch, even after erasure.
    # capture_for also rejects a foreign owner instead of replacing its origin.
    value = await capture_for(store, owner)
    token = _capture.set(value)
    try:
        yield value
    finally:
        _capture.reset(token)


async def enter(conn: Any, value: Capture, *, ordered: bool = False) -> int:
    """Call BEFORE cap/source/content locks. No provider work or retry here."""
    owner = value.origin.account_id
    # Dedicated two-int namespace; fail rather than wait backwards on a legacy
    # caller that has already acquired a content lock. Hash collisions only refuse.
    if ordered:
        await conn.execute("SELECT pg_advisory_xact_lock(601065,hashtext($1))", str(owner))
    else:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(601065,hashtext($1))", str(owner)
        )
        if acquired is not True:
            raise TemporalRefusal("memory_mutation_busy")
    now = await conn.fetchval("SELECT clock_timestamp()")
    await conn.execute(
        "INSERT INTO memory_knowledge_clock(user_id,account_generation,last_clock_at) "
        "VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
        owner,
        value.origin.generation,
        now,
    )
    clock = await conn.fetchrow(
        "SELECT * FROM memory_knowledge_clock WHERE user_id=$1 FOR UPDATE NOWAIT", owner
    )
    if clock["account_generation"] != value.origin.generation:
        raise TemporalRefusal("memory_generation_changed")
    if clock["erasure_epoch"] != value.erasure_epoch:
        raise TemporalRefusal("memory_erasure_changed")
    regressed = now < clock["last_clock_at"]
    # Separate statement after serialization: only OTHER transactions can be
    # observed committed. Never seal the current uncommitted transaction.
    await conn.execute(
        "UPDATE memory_mutations SET observed_committed_by=$2 "
        "WHERE user_id=$1 AND observed_committed_by IS NULL "
        "AND transaction_id<>pg_current_xact_id() AND write_lower_at<=$2",
        owner,
        now,
    )
    existing = await conn.fetchval(
        "SELECT sequence FROM memory_mutations WHERE user_id=$1 "
        "AND transaction_id=pg_current_xact_id()",
        owner,
    )
    if existing is not None:
        return existing
    seq = await conn.fetchval(
        "UPDATE memory_knowledge_clock SET sequence=sequence+1,last_clock_at=GREATEST(last_clock_at,$2),"
        "clock_regressed=clock_regressed OR $3 WHERE user_id=$1 RETURNING sequence",
        owner,
        now,
        regressed,
    )
    await conn.execute(
        "INSERT INTO memory_mutations(user_id,sequence,account_generation,transaction_id,write_lower_at) "
        "VALUES($1,$2,$3,pg_current_xact_id(),$4)",
        owner,
        seq,
        value.origin.generation,
        now,
    )
    return seq


@asynccontextmanager
async def mutation(store: Any, value: Capture, *, conn: Any = None, ordered: bool = False):
    bound = _active.get()
    if bound is not None:
        if bound.task is not asyncio.current_task():
            raise TemporalRefusal("memory_transaction_task_mismatch")
        if bound.store is not store or bound.capture != value:
            raise TemporalRefusal("memory_mutation_scope_mismatch")
        yield bound.conn
        return
    if conn is None:
        async with store._database_pool.acquire() as connection:
            async with connection.transaction(isolation="read_committed"):
                async with mutation(store, value, conn=connection, ordered=True) as active:
                    yield active
        await observe_committed(store, value.origin.account_id)
        return
    async with guard_content_write(conn, value.origin):
        await enter(conn, value, ordered=ordered)
        token = _active.set(ActiveMutation(store, conn, value, asyncio.current_task()))
        try:
            yield conn
        finally:
            _active.reset(token)


class _Borrowed:
    def __init__(self, conn: Any):
        self.conn = conn

    def __await__(self):
        async def get():
            return self.conn

        return get().__await__()

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        return False


class BoundPool:
    """Task-local facade; never mutate the shared store's pool during a write."""

    def __init__(self, conn: Any):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def acquire(self):
        return _Borrowed(self.conn)

    async def release(self, _conn):
        return None


def pool_for(store: Any):
    bound = _active.get()
    if bound is not None and bound.task is not asyncio.current_task():
        raise TemporalRefusal("memory_transaction_task_mismatch")
    return (
        BoundPool(bound.conn)
        if bound is not None and bound.store is store
        else store._database_pool
    )


def semantic_write(function):
    """Explicitly inventoried store writers; nested calls retain one transaction."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        bound = _active.get()
        if bound is not None and bound.store is self:
            if bound.task is not asyncio.current_task():
                raise TemporalRefusal("memory_transaction_task_mismatch")
            return await function(self, *args, **kwargs)
        arguments = signature.bind(self, *args, **kwargs).arguments
        owner = arguments.get("user_id")
        executor = arguments.get("conn") or self._database_pool
        if owner is None and arguments.get("memory_id") is not None:
            owner = await executor.fetchval(
                "SELECT user_id FROM memories WHERE id=$1", arguments["memory_id"]
            )
        if owner is None and arguments.get("old_memory_id") is not None:
            owner = await executor.fetchval(
                "SELECT user_id FROM memories WHERE id=$1", arguments["old_memory_id"]
            )
        if owner is None and arguments.get("source_conversation_id") is not None:
            owner = await executor.fetchval(
                "SELECT user_id FROM conversations WHERE id=$1", arguments["source_conversation_id"]
            )
        if not isinstance(owner, UUID):
            raise TemporalRefusal("memory_mutation_owner_required")
        value = _capture.get()
        if value is None:
            value = await capture(self, owner)
        if value.origin.account_id != owner:
            raise TemporalRefusal("memory_mutation_scope_mismatch")
        async with mutation(self, value, conn=arguments.get("conn")):
            return await function(self, *args, **kwargs)

    return wrapped


def captured_operation(function):
    """Capture before reading inputs/provider work, without retaining SQL locks."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs).arguments
        if _capture.get() is not None:
            return await function(*args, **kwargs)
        store = arguments.get("store")
        owner = arguments.get("user_id")
        if store is None or not isinstance(owner, UUID):
            raise TemporalRefusal("memory_original_capture_required")
        async with original_capture(store, owner):
            return await function(*args, **kwargs)

    return wrapped


async def _observe(store: Any, owner: UUID, *, include_bounds: bool = False):
    """Commit observation independently of the requested cutoff's disposition."""
    async with store._database_pool.acquire() as conn, conn.transaction(isolation="read_committed"):
        origin = await capture_generation(conn, owner)
        async with guard_content_write(conn, origin):
            if not await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(601065,hashtext($1))", str(owner)
            ):
                raise TemporalRefusal("knowledge_cutoff_busy")
            # Fresh snapshot AFTER the barrier, not the lock statement's snapshot.
            clock = await conn.fetchrow(
                "SELECT *,clock_timestamp() AS observed_at FROM memory_knowledge_clock "
                "WHERE user_id=$1 FOR UPDATE NOWAIT",
                owner,
            )
            if clock is None:
                now = await conn.fetchval("SELECT clock_timestamp()")
                seq, regressed = 0, False
                bounds = []
            else:
                if clock["account_generation"] != origin.generation:
                    raise TemporalRefusal("memory_generation_changed")
                now = clock["observed_at"]
                regressed = clock["clock_regressed"] or now < clock["last_clock_at"]
                await conn.execute(
                    "UPDATE memory_knowledge_clock SET last_clock_at=GREATEST(last_clock_at,$2),"
                    "clock_regressed=$3 WHERE user_id=$1",
                    owner,
                    now,
                    regressed,
                )
                await conn.execute(
                    "UPDATE memory_mutations SET observed_committed_by=$2 WHERE user_id=$1 "
                    "AND observed_committed_by IS NULL AND transaction_id<>pg_current_xact_id() "
                    "AND write_lower_at<=$2",
                    owner,
                    now,
                )
                seq = clock["sequence"]
                bounds = (
                    await conn.fetch(
                        "SELECT sequence,write_lower_at,observed_committed_by FROM memory_mutations "
                        "WHERE user_id=$1 AND sequence<=$2 ORDER BY sequence",
                        owner,
                        seq,
                    )
                    if include_bounds
                    else []
                )
    return origin, seq, now, regressed, bounds


async def observe_committed(store: Any, owner: UUID) -> None:
    """Best-effort receipt after an ACKNOWLEDGED owned commit; never replay it.

    Receipt failure does not turn an acknowledged memory write into a failed
    write. Caller-owned transactions are sealed only by a later observer.
    """
    import asyncpg

    try:
        await _observe(store, owner)
    except (
        TemporalRefusal,
        WriteGuardRefusal,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
    ):
        logging.getLogger(__name__).warning(
            "Memory commit receipt deferred; visibility remains bounded/unknown"
        )


async def resolve(store: Any, owner: UUID, requested: TemporalReadScope) -> TemporalReadScope:
    """Observe/seal durably first; only then adjudicate the requested cutoff."""
    origin, seq, now, regressed, bounds = await _observe(
        store, owner, include_bounds=requested.known_at is not None
    )
    if requested.known_at is not None:
        cutoff = requested.known_at
        if (
            regressed
            or cutoff > now
            or any(
                row["write_lower_at"] <= cutoff
                and (row["observed_committed_by"] is None or row["observed_committed_by"] > cutoff)
                for row in bounds
            )
        ):
            raise TemporalRefusal("knowledge_cutoff_indeterminate")
        selected = [
            row["sequence"]
            for row in bounds
            if row["observed_committed_by"] is not None and row["observed_committed_by"] <= cutoff
        ]
        if not selected and seq > 0:
            raise TemporalRefusal("knowledge_before_available_history")
        seq = max(selected, default=0)
    mark = KnowledgeWatermark(user_id=owner, generation=origin.generation, sequence=seq)
    supplied = requested.watermark
    if supplied is not None:
        if (
            supplied.user_id != owner
            or supplied.generation != origin.generation
            or supplied.sequence > mark.sequence
        ):
            raise TemporalRefusal("invalid_knowledge_watermark")
        mark = supplied
    return TemporalReadScope(event_at=requested.event_at, watermark=mark)


async def search(
    store: Any,
    owner: UUID,
    scope: TemporalReadScope,
    *,
    query: str | None = None,
    vector: str | None = None,
    embedding_model: str | None = None,
    limit: int = 10,
    include_local: bool = False,
    category: str | None = None,
    slot: str | None = None,
    source_ids: list[UUID] | None = None,
    tier: str | None = None,
    memory_id: UUID | None = None,
    match_any_term: bool = False,
):
    """All qualification precedes ranking/LIMIT; no current-text historical join."""
    if scope.watermark is None:
        scope = await resolve(store, owner, scope)
    mark = scope.watermark
    if mark is None or mark.user_id != owner:
        raise TemporalRefusal("invalid_knowledge_watermark")
    event_at = scope.event_at
    sql = """
        WITH lexical_query AS (
            SELECT CASE WHEN $15::bool
                THEN (SELECT string_agg(quote_literal(lexeme), ' | ')
                      FROM unnest(tsvector_to_array(to_tsvector('english',$11))) AS lexeme)::tsquery
                ELSE plainto_tsquery('english',$11) END AS tsq
        ), chosen AS (
            SELECT DISTINCT ON(memory_id) r.* FROM memory_revisions r
            WHERE r.user_id=$1 AND r.sequence<=$2 AND ($14::uuid IS NULL OR r.memory_id=$14)
            ORDER BY memory_id,sequence DESC,ordinal DESC
        ), eligible AS (
            SELECT r.*,s.content,s.content_hash,s.content_tsv,s.embedding,s.embedding_model,
                   s.event_start,s.event_end,s.precision,s.start_kind,s.end_kind,s.id AS segment_id
            FROM chosen r JOIN memories current ON current.id=r.memory_id AND current.user_id=$1
            JOIN memory_revision_segments s ON s.revision_id=r.id
            JOIN entitlement_accounts a ON a.user_id=$1 AND a.status='active' AND a.content_generation=$3
            LEFT JOIN conversations c ON r.source_origin_kind='conversation' AND c.id=r.source_origin_id
            WHERE current.status!='deleted'
              AND NOT EXISTS(SELECT 1 FROM memory_tombstones t WHERE t.user_id=$1 AND t.memory_id=r.memory_id)
              AND NOT EXISTS(SELECT 1 FROM account_deletions d WHERE d.account_id=$1 AND d.closed_at IS NULL)
              AND NOT EXISTS(SELECT 1 FROM account_suspension_causes ac WHERE ac.account_id=$1)
              AND r.state->>'status'='active' AND NOT r.provenance_unknown
              AND ($4 OR (current.local_only=FALSE AND r.state->>'local_only'='false'))
              AND ((r.source_origin_kind IN ('manual','import','user_created') AND r.source_origin_id IS NULL)
                   OR (c.user_id=$1 AND ($4 OR c.pipeline='cloud')))
              AND ($5::timestamptz IS NULL OR (s.precision='exact'
                   AND s.start_kind!='unknown' AND s.end_kind!='unknown'
                   AND (s.event_start IS NULL OR s.event_start<=$5)
                   AND (s.event_end IS NULL OR s.event_end>$5)))
              AND ($6::text IS NULL OR r.state->>'category'=$6)
              AND ($7::text IS NULL OR r.state->>'memory_slot'=$7)
              AND ($8::uuid[] IS NULL OR r.source_origin_id=ANY($8))
              AND ($9::text IS NULL OR r.state->>'tier'=$9)
        )
        SELECT (jsonb_populate_record(NULL::memories,e.state||jsonb_build_object(
                    'content',e.content,'content_hash',e.content_hash,'content_tsv',e.content_tsv,
                    'embedding',e.embedding,'embedding_model',e.embedding_model))).*,
               e.id AS revision_id,e.segment_id,e.sequence AS knowledge_sequence,
               e.event_start,e.event_end,e.precision,
               CASE WHEN $10::vector IS NULL THEN 0 ELSE 1-(e.embedding<=>$10::vector) END AS similarity,
               CASE WHEN $11::text IS NULL THEN 0 ELSE ts_rank(e.content_tsv,q.tsq) END AS bm25_score
        FROM eligible e CROSS JOIN lexical_query q
        WHERE ($10::vector IS NULL OR (e.embedding IS NOT NULL AND e.embedding_model=$12))
          AND ($11::text IS NULL OR e.content_tsv @@ q.tsq)
        ORDER BY CASE WHEN $10::vector IS NOT NULL THEN e.embedding<=>$10::vector END ASC NULLS LAST,
                 CASE WHEN $11::text IS NOT NULL THEN ts_rank(e.content_tsv,q.tsq) END DESC,
                 e.memory_id,e.segment_id
        LIMIT $13
    """
    rows = await store._pool.fetch(
        sql,
        owner,
        mark.sequence,
        mark.generation,
        include_local,
        event_at,
        category,
        slot,
        source_ids,
        tier,
        vector,
        query,
        embedding_model,
        max(1, min(limit, 500)),
        memory_id,
        match_any_term,
    )
    return [store._memory_row_to_dict(row) for row in rows]


async def history(store: Any, owner: UUID, memory_id: UUID):
    """Owner UI/export history; current revocation/ownership still applies."""
    rows = await store._pool.fetch(
        "SELECT r.id AS revision_id,r.sequence AS knowledge_sequence,r.ordinal,r.relationship,"
        "r.provenance_unknown,r.source_origin_kind,r.source_origin_id,r.state,"
        "s.id AS segment_id,s.content,s.event_start,s.event_end,s.precision,s.start_kind,s.end_kind,"
        "s.time_expression_ciphertext,s.timezone,k.write_lower_at,k.observed_committed_by "
        "FROM memory_revisions r JOIN memories m ON m.id=r.memory_id AND m.user_id=$1 "
        "JOIN memory_revision_segments s ON s.revision_id=r.id "
        "JOIN memory_mutations k ON k.user_id=r.user_id AND k.sequence=r.sequence "
        "LEFT JOIN conversations c ON c.id=r.source_origin_id AND r.source_origin_kind='conversation' "
        "WHERE r.user_id=$1 AND r.memory_id=$2 AND m.status!='deleted' "
        "AND NOT EXISTS(SELECT 1 FROM memory_tombstones t WHERE t.user_id=$1 AND t.memory_id=$2) "
        "AND (r.source_origin_kind!='conversation' OR c.user_id=$1) "
        "ORDER BY r.sequence,r.ordinal,s.event_start NULLS FIRST,s.id",
        owner,
        memory_id,
    )
    result = []
    for raw in rows:
        row = dict(raw)
        row["content"] = store.encryption.decrypt(row["content"])
        expression = row.pop("time_expression_ciphertext")
        row["time_expression"] = store.encryption.decrypt(expression) if expression else None
        result.append(row)
    return result
