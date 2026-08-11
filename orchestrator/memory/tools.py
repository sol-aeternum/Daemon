"""Memory tools for Daemon tool system."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.dedup import (
    check_contradiction,
    dedup_and_store,
    prepare_memory_embedding,
)
from orchestrator.memory.embedding import embed_documents_with_metadata, embed_query_with_metadata
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)


# Per-user quotas for the LLM-callable `memory_write` tool (issue #62).
#
# Without these, a prompt-injected model can call `memory_write` in a
# loop: each call inserts a row AND triggers a billed embedding request,
# so the failure mode is simultaneously storage amplification, retrieval
# degradation (similarity search over thousands of rows on every chat
# turn) and cost amplification.
#
# These are module-level constants rather than `Settings` fields on
# purpose: adding a `daemon_memory_write_*` env var would require
# touching `.env.example`, and the AGENTS.md guardrail on surgical
# config edits plus this repo's "do not add env vars for a bug fix"
# convention both point at hard-coded defaults for the first cut. They
# are named and module-scoped so tests can monkeypatch them and a
# follow-up can promote them to config without changing call sites.
MEMORY_WRITE_MAX_PER_WINDOW: int = 10
MEMORY_WRITE_WINDOW_SECONDS: int = 60
MEMORY_WRITE_MAX_ACTIVE_ROWS: int = 1000


# Per-process, per-user sliding-window counter for the LLM-callable
# `memory_write` tool. The counter reserves prospective embedding-billed
# writes rather than counting inserted rows, because deduplication can
# merge many billed attempts into one database row. Reservations are
# released when a request exits before the embedding path. An OrderedDict
# provides LRU order so total process memory remains bounded even during
# a burst of entirely fresh users.
_attempt_log: OrderedDict[uuid.UUID, deque[datetime]] = OrderedDict()
_attempt_log_last_seen: dict[uuid.UUID, datetime] = {}

# The ledger critical sections contain no awaits, so a process-wide
# thread lock keeps the shared dictionaries safe across event loops and
# threads without ever blocking on I/O. An asyncio.Lock would remain
# bound to the first event loop that contended for it; recreating an
# embedded ASGI lifecycle could then make quota checks fail open.
_attempt_log_lock = threading.Lock()

# Bound the per-process ledger independently of timestamp freshness.
# LRU eviction is an abuse-dampener tradeoff: an evicted user's next
# write starts a new local window. Exact cross-process enforcement is a
# separate shared-store concern.
_ATTEMPT_LOG_MAX_USERS: int = 1024
_ATTEMPT_LOG_INACTIVITY_SECONDS: int = 600
_ATTEMPT_LOG_SWEEP_EVERY_N: int = 256
_attempt_log_call_count: int = 0


@dataclass(frozen=True)
class _WriteQuotaDecision:
    """Result of reserving one prospective embedding-billed write."""

    refusal: str | None
    reservation: datetime | None


def _maybe_sweep_attempt_log(now: datetime, *, current_user_id: uuid.UUID) -> None:
    """Evict stale users periodically and enforce the hard LRU bound.

    Must be called under `_attempt_log_lock`.
    """
    global _attempt_log_call_count
    _attempt_log_call_count += 1

    # The current user must be most-recent before any size eviction.
    if current_user_id in _attempt_log:
        _attempt_log.move_to_end(current_user_id)

    if (
        _attempt_log_call_count % _ATTEMPT_LOG_SWEEP_EVERY_N == 0
        and len(_attempt_log) >= _ATTEMPT_LOG_MAX_USERS
    ):
        cutoff = now - timedelta(seconds=_ATTEMPT_LOG_INACTIVITY_SECONDS)
        stale_users = [
            user_id
            for user_id, last_seen in _attempt_log_last_seen.items()
            if last_seen < cutoff and user_id != current_user_id
        ]
        for user_id in stale_users:
            _attempt_log.pop(user_id, None)
            _attempt_log_last_seen.pop(user_id, None)

    # TTL eviction alone cannot bound a burst where every user is fresh.
    # Drop least-recent users until the fixed cardinality is restored.
    while len(_attempt_log) > _ATTEMPT_LOG_MAX_USERS:
        evicted_user_id, _ = _attempt_log.popitem(last=False)
        _attempt_log_last_seen.pop(evicted_user_id, None)


def _release_attempt_reservation(user_id: uuid.UUID, reservation: datetime | None) -> None:
    """Remove a reservation when a write exits before the embedding path."""
    if reservation is None:
        return

    with _attempt_log_lock:
        log = _attempt_log.get(user_id)
        if log is None:
            return
        try:
            log.remove(reservation)
        except ValueError:
            # A later sweep or window prune may already have removed it.
            return

        if log:
            _attempt_log_last_seen[user_id] = log[-1]
        else:
            _attempt_log.pop(user_id, None)
            _attempt_log_last_seen.pop(user_id, None)


class MemoryReadTool(Tool):
    name = "memory_read"
    description = "Retrieve memories using semantic search"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["semantic", "temporal"],
                "default": "semantic",
            },
            "after": {
                "type": "string",
                "description": "ISO8601 timestamp lower bound",
            },
            "before": {
                "type": "string",
                "description": "ISO8601 timestamp upper bound",
            },
            "limit": {"type": "integer", "default": 5},
            "history": {
                "type": "boolean",
                "default": False,
                "description": "Include closed historical memories",
            },
            "slot": {
                "type": "string",
                "description": "Filter by memory slot",
            },
        },
        "required": [],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        mode = kwargs.get("mode", "semantic")
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 5)
        history = bool(kwargs.get("history", False))
        slot = kwargs.get("slot")
        after_raw = kwargs.get("after")
        before_raw = kwargs.get("before")

        def parse_dt(value: str | None) -> datetime | None:
            if not value:
                return None
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            return datetime.fromisoformat(normalized)

        if mode == "semantic":
            normalized_slot = slot if isinstance(slot, str) and slot.strip() else None
            query_result = await embed_query_with_metadata(query)
            memories = await retrieve_memories_for_text(
                store=self.store,
                query_text=query,
                user_id=self.user_id,
                query_embedding=query_result.embedding,
                limit=limit,
                include_local=True,
                include_historical=history,
                memory_slot=normalized_slot,
                storage_embedding_model=query_result.storage_model,
                query_embedding_model=query_result.model,
            )
        else:
            try:
                created_after = parse_dt(after_raw)
                created_before = parse_dt(before_raw)
            except ValueError:
                return "Invalid 'after' or 'before' timestamp. Use ISO8601."

            effective_limit = limit * 4 if slot else limit
            memories = await self.store.list_memories(
                user_id=self.user_id,
                confirmed=None if history else True,
                status=None,
                include_local=True,
                created_after=created_after,
                created_before=created_before,
                limit=effective_limit,
            )

        if history:
            memories = [m for m in memories if m.get("status") != "deleted"]
        # Slot filtering for temporal mode (semantic handles it via store)
        if mode != "semantic" and isinstance(slot, str) and slot.strip():
            memories = [m for m in memories if m.get("memory_slot") == slot]
        memories = memories[:limit]

        if not memories:
            return "No relevant memories found."

        formatted = []
        for mem in memories:
            content = mem.get("content", "")
            category = str(mem.get("category") or "unknown")
            slot_value = mem.get("memory_slot")
            slot_text = f" slot={slot_value}" if slot_value else ""
            if history:
                valid_from = mem.get("valid_from")
                valid_to = mem.get("valid_to")
                formatted.append(
                    f"- [{category.upper()}]{slot_text} [{valid_from} -> {valid_to}] {content}"
                )
            else:
                formatted.append(f"- [{category.upper()}]{slot_text} {content}")

        return "\n".join(formatted)


class MemoryWriteTool(Tool):
    name = "memory_write"
    description = "Create, update, or delete memories"
    allowed_categories = {"fact", "preference", "project", "summary", "correction"}
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "delete"]},
            "content": {"type": "string"},
            "category": {"type": "string", "default": "fact"},
            "memory_id": {"type": "string"},
            "slot": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def _check_and_set_contradiction(
        self,
        memory_id: uuid.UUID,
        content: str,
        slot: str | None,
    ) -> None:
        """Check for contradiction with same-slot memories and update metadata if found.

        Contradiction detection is advisory - failures are logged but don't block.
        """
        if not isinstance(slot, str) or not slot.strip():
            return

        try:
            embedding_input = f"{slot.strip()}: {content.strip()}"
            embedding_result = await embed_documents_with_metadata([embedding_input])
            embedding = embedding_result.embeddings[0]
            candidates = await self.store.search_memories(
                user_id=self.user_id,
                query_embedding=embedding,
                limit=10,
                min_similarity=0.5,
                memory_slot=slot,
                embedding_model=embedding_result.storage_model,
            )
            for candidate in candidates:
                if candidate.get("id") == memory_id:
                    continue
                if candidate.get("valid_to") is not None:
                    continue
                existing_content = candidate.get("content", "")
                if not existing_content:
                    continue
                contradiction_detected, explanation = await check_contradiction(
                    existing_content, content
                )
                if contradiction_detected:
                    _ = await self.store.update_memory_metadata(
                        memory_id,
                        {
                            "contradiction_detected": True,
                            "contradiction_explanation": explanation,
                        },
                    )
                    break
        except Exception as error:
            logger.warning(
                "Failed to annotate contradiction metadata: %s",
                error,
            )

    async def _check_write_quota(
        self,
        action: str,
        *,
        replace_memory_id: uuid.UUID | None = None,
        replace_memory_active: bool = False,
    ) -> _WriteQuotaDecision:
        """Enforce the per-user write-rate limit and active-row cap.

        Returns a decision containing an optional caller-facing refusal
        and the timestamp reserved for an allowed write. The reservation
        is created before the asynchronous cap query so concurrent calls
        cannot overrun the rate limit. Callers must release it whenever
        they exit before reaching the embedding path.

        `replace_memory_id` (optional): for an `update`, the UUID of
        the memory that will be `close_memory`-d before the new memory
        is inserted. The active-row cap **exempts** a net-neutral
        update — at the cap, an `update` is still permitted because
        the close-then-insert sequence leaves the active count
        unchanged. Without this exemption the cap would be terminal
        (the user could not even correct an existing memory once hit),
        making deletion the only escape route.

        `replace_memory_active` (optional, default `False`): whether
        the row identified by `replace_memory_id` is currently
        `status='active' AND valid_to IS NULL`. The `update`
        caller-passed path already fetches the row through
        `get_memory()`; rather than issue another DB round-trip here,
        the caller passes that fact in. **The cap exemption only
        triggers when the replace target is actually closable.**
        Without this check a caller at the cap could name an already
        closed row and incorrectly obtain the net-neutral exemption.
        `close_memory()` now also reports whether its UPDATE affected a
        row, but this preflight check avoids granting the exemption in
        the first place.

        Fails **open** on a counting error for the active-row cap. The
        rate-limit decision is never overridable — see
        `test_memory_write_rate_refusal_survives_cap_query_failure` in
        `tests/memory/test_tools.py`. The active-row cap is the
        storage-amplification guard and is intentionally fail-open on
        transient query errors because (a) the cap is a soft quota
        rather than an authorization boundary, (b) the rate-limit
        refusal already protects the cost-amplification half of issue
        #62, and (c) a database hiccup must not silently disable the
        user's memory. The cap-query fail-open is enforced by
        `test_memory_write_cap_query_failure_below_rate_limit_fails_open`
        so the design is locked under test. Round-8 Codex review
        (P1, 2026-08-11T02:01:09Z,
        `orchestrator/memory/tools.py:404`) re-flagged the fail-open
        path; the resolver confirms the intent and rebuts with the
        sibling test pair as evidence.

        The **rate limit** (issue #62's per-user writes-per-minute
        guard) is enforced via an in-process per-user sliding-window
        counter — see `_attempt_log` above for the design rationale.
        The active-row cap remains a row-count predicate via
        `count_active_memories`; that one counts rows correctly
        because it does not need to track billed embedding calls.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=MEMORY_WRITE_WINDOW_SECONDS)
        reservation: datetime | None = None
        try:
            with _attempt_log_lock:
                log = _attempt_log.get(self.user_id)
                if log is None:
                    log = deque(maxlen=MEMORY_WRITE_MAX_PER_WINDOW)
                    _attempt_log[self.user_id] = log
                # Prune entries older than the window. `deque` is
                # ordered; the oldest entry is on the left.
                while log and log[0] < window_start:
                    log.popleft()
                # `recent_writes` is the count BEFORE this call is
                # added. The outer check refuses when this count has
                # reached the limit — so the (N+1)th call against a
                # limit of N is refused. The current attempt is not
                # appended when refused — otherwise repeated
                # refusals would lock the user out for a full
                # window even if no real writes happened.
                recent_writes = len(log)
                if recent_writes < MEMORY_WRITE_MAX_PER_WINDOW:
                    log.append(now)
                    reservation = now
                _attempt_log_last_seen[self.user_id] = now
                _maybe_sweep_attempt_log(now, current_user_id=self.user_id)
            try:
                active_rows = int(await self.store.count_active_memories(self.user_id))
            except Exception as active_error:
                logger.warning(
                    "memory_write active-row count failed; allowing write user_id=%s "
                    "action=%s error=%s",
                    self.user_id,
                    action,
                    type(active_error).__name__,
                )
                # Round-5 Codex review (P1, 2026-08-10T10:43:07Z,
                # `orchestrator/memory/tools.py:360`): when the cap
                # query fails after the rate counter is already at
                # the limit, the previous code fell through to
                # `return None`, allowing `execute()` to invoke
                # `dedup_and_store` and trigger a billed embedding
                # request. Honor a rate-limit decision already taken
                # even when the independent active-row cap query
                # fails — the rate limit is the cost-amplification
                # guard, and a transient cap-query failure must not
                # override it.
                if recent_writes >= MEMORY_WRITE_MAX_PER_WINDOW:
                    logger.warning(
                        "memory_write_rate_limited user_id=%s action=%s "
                        "window_seconds=%s writes_in_window=%s limit=%s "
                        "cap_query_failed=true",
                        self.user_id,
                        action,
                        MEMORY_WRITE_WINDOW_SECONDS,
                        recent_writes,
                        MEMORY_WRITE_MAX_PER_WINDOW,
                    )
                    return _WriteQuotaDecision(
                        refusal=(
                            f"Memory write rate limit reached "
                            f"({MEMORY_WRITE_MAX_PER_WINDOW} writes per "
                            f"{MEMORY_WRITE_WINDOW_SECONDS}s). Wait before writing again, "
                            f"or summarise several facts into a single memory."
                        ),
                        reservation=None,
                    )
                return _WriteQuotaDecision(refusal=None, reservation=reservation)
            except BaseException:
                # Round-10 Codex review (P2, 2026-08-11T04:02:59Z,
                # `orchestrator/memory/tools.py:408`): cancellation at
                # the active-row count await bypasses the surrounding
                # `except Exception` handlers — `asyncio.CancelledError`
                # is a `BaseException` in Python 3.8+ — and the
                # reservation has already been appended to the per-user
                # deque. Ten pre-embedding cancellations therefore leave
                # the user rate-limited for the remainder of the window
                # despite no billed work occurring. Release the
                # reservation and re-raise so the cancellation propagates
                # cleanly.
                _release_attempt_reservation(self.user_id, reservation)
                raise
        except Exception as error:
            logger.warning(
                "memory_write quota check failed; allowing write user_id=%s action=%s error=%s",
                self.user_id,
                action,
                type(error).__name__,
            )
            return _WriteQuotaDecision(refusal=None, reservation=reservation)

        if recent_writes >= MEMORY_WRITE_MAX_PER_WINDOW:
            # Structured refusal log line — this is the
            # `memory_writes_per_user` metric surface asked for by the
            # issue's acceptance criteria. Operators alert on the rate of
            # this event; no memory content is logged.
            logger.warning(
                "memory_write_rate_limited user_id=%s action=%s window_seconds=%s "
                "writes_in_window=%s limit=%s",
                self.user_id,
                action,
                MEMORY_WRITE_WINDOW_SECONDS,
                recent_writes,
                MEMORY_WRITE_MAX_PER_WINDOW,
            )
            return _WriteQuotaDecision(
                refusal=(
                    f"Memory write rate limit reached "
                    f"({MEMORY_WRITE_MAX_PER_WINDOW} writes per "
                    f"{MEMORY_WRITE_WINDOW_SECONDS}s). Wait before writing again, "
                    f"or summarise several facts into a single memory."
                ),
                reservation=None,
            )

        if active_rows >= MEMORY_WRITE_MAX_ACTIVE_ROWS:
            # A net-neutral `update` is exempt: the close+insert pair
            # leaves the active count unchanged, so refusing it at the
            # cap would make the cap terminal (the user couldn't even
            # correct an existing memory once hit). Only exempt when
            # `replace_memory_active` is True — that is, the row the
            # caller named is actually closable (status='active' AND
            # valid_to IS NULL). Without this check, a caller at the
            # cap could otherwise name an already-closed row and obtain
            # an exemption for an operation that increases the active
            # count. The store's affected-row result is a second guard
            # against a close race after this preflight check.
            net_neutral_update = bool(
                action == "update"
                and replace_memory_id is not None
                and replace_memory_active
                and active_rows > 0  # sanity: at least one row to close
            )
            if net_neutral_update:
                logger.info(
                    "memory_write_cap_update_net_neutral user_id=%s active_rows=%s "
                    "cap=%s replace_memory_id=%s",
                    self.user_id,
                    active_rows,
                    MEMORY_WRITE_MAX_ACTIVE_ROWS,
                    replace_memory_id,
                )
                return _WriteQuotaDecision(refusal=None, reservation=reservation)

            logger.warning(
                "memory_write_cap_exceeded user_id=%s action=%s active_rows=%s cap=%s",
                self.user_id,
                action,
                active_rows,
                MEMORY_WRITE_MAX_ACTIVE_ROWS,
            )
            _release_attempt_reservation(self.user_id, reservation)
            return _WriteQuotaDecision(
                refusal=(
                    f"Memory storage cap reached ({MEMORY_WRITE_MAX_ACTIVE_ROWS} "
                    f"active memories). Delete an existing memory before writing "
                    f"a new one."
                ),
                reservation=None,
            )

        return _WriteQuotaDecision(refusal=None, reservation=reservation)

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action")

        if action == "create":
            content = kwargs.get("content", "")
            category = kwargs.get("category", "fact")
            slot = kwargs.get("slot")
            if category not in self.allowed_categories:
                allowed = ", ".join(sorted(self.allowed_categories))
                return f"Invalid category '{category}'. Use one of: {allowed}."
            # Quota check runs after cheap input validation and before
            # `dedup_and_store`, which is what issues the billed
            # embedding request.
            quota = await self._check_write_quota(action)
            if quota.refusal is not None:
                return quota.refusal
            effective_slot = slot if isinstance(slot, str) else None
            # External embedding work must complete before BEGIN/advisory
            # lock so a slow provider cannot hold a database transaction.
            embedding_result = await prepare_memory_embedding(content, effective_slot)
            # Issue #221 — atomic active-row cap enforcement.
            #
            # `_check_write_quota` does a non-locked count + a
            # rate-window check on the per-process deque. Two parallel
            # tool calls (e.g. an LLM making two `memory_write` calls
            # concurrently in the same response) could both pass that
            # count and both insert, silently raising the active count
            # above the cap. Acquire the per-user advisory lock, do
            # the cap re-check inside the same transaction, and run
            # `dedup_and_store` on the lock connection so the insert
            # is part of the same transaction as the cap check. The
            # embedding is computed before the lock is acquired (the
            # issue explicitly forbids holding the transaction open
            # across the external embedding call).
            cap_conn: Any | None = None
            try:
                cap_conn, _ = await self.store.acquire_user_cap_lock(self.user_id)
                # Re-check the cap inside the lock transaction. The
                # rate-window check above is still authoritative for
                # the cost-amplification half; this gate is the
                # authoritative storage-amplification half. A
                # concurrent writer is forced to wait at the
                # advisory-lock acquire, so by the time this
                # `count_active_memories_for_user_within_lock` runs
                # the table reflects their commit (or vice-versa).
                active_rows = await self.store.count_active_memories_for_user_within_lock(
                    cap_conn, self.user_id
                )
                if active_rows >= MEMORY_WRITE_MAX_ACTIVE_ROWS:
                    logger.warning(
                        "memory_write_cap_exceeded_atomic user_id=%s action=%s "
                        "active_rows=%s cap=%s",
                        self.user_id,
                        action,
                        active_rows,
                        MEMORY_WRITE_MAX_ACTIVE_ROWS,
                    )
                    _release_attempt_reservation(self.user_id, quota.reservation)
                    # The advisory lock is transaction-scoped. The
                    # refusal path must end the transaction and return
                    # the connection to the pool before returning, or
                    # every later writer for this user would block
                    # forever on the leaked lock.
                    try:
                        await cap_conn.execute("ROLLBACK")
                    finally:
                        await self.store._pool.release(cap_conn)
                        cap_conn = None
                    return (
                        f"Memory storage cap reached ({MEMORY_WRITE_MAX_ACTIVE_ROWS} "
                        f"active memories). Delete an existing memory before writing "
                        f"a new one."
                    )
                memory_id = await dedup_and_store(
                    store=self.store,
                    user_id=self.user_id,
                    content=content,
                    source_type="user_created",
                    category=category,
                    conversation_id=None,
                    slot=effective_slot,
                    lock_conn=cap_conn,
                    embedding_result=embedding_result,
                )
                # Commit the cap-locked transaction so the advisory
                # lock is released (it is transaction-scoped). The
                # actual insert happened on this connection inside
                # `dedup_and_store`; releasing without committing
                # would roll back the insert.
                await cap_conn.execute("COMMIT")
                await self.store._pool.release(cap_conn)
                cap_conn = None
            except BaseException:
                if cap_conn is not None:
                    try:
                        await cap_conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    try:
                        await self.store._pool.release(cap_conn)
                    except Exception:
                        pass
                # Cancellation or store failure before commit must not
                # consume a billed-attempt reservation.
                _release_attempt_reservation(self.user_id, quota.reservation)
                raise
            await self._check_and_set_contradiction(memory_id, content, effective_slot)
            return f"Memory created (ID: {memory_id})."

        elif action == "update":
            memory_id_str = kwargs.get("memory_id")
            if not memory_id_str:
                return "memory_id is required for update"
            try:
                memory_id = uuid.UUID(memory_id_str)
            except ValueError:
                return "Invalid memory_id format"

            # Fetch the existing memory
            old_memory = await self.store.get_memory(memory_id)
            if not old_memory:
                return "Memory not found"

            # Authorization: the target memory must belong to the calling
            # user. Without this check, any user who learns another user's
            # memory UUID (via prompt injection, log leakage, shared exports)
            # could close that user's memory through the LLM-callable tool
            # path. The HTTP route (`orchestrator/routes/memories.py`) and
            # sibling tool (`MemoryPromoteTool`) already enforce the same
            # guard; this fixes the cross-user IDOR for `MemoryWriteTool`.
            # Match the 404 wording used by the HTTP route so the response
            # reveals nothing about whether the ID exists for another user.
            if old_memory.get("user_id") != self.user_id:
                return "Memory not found"

            # Quota check runs after the ownership guard (so an
            # unauthorized caller learns nothing about quota state) but
            # before `close_memory` + `dedup_and_store`. `update` closes
            # one row and inserts another, so an unmetered update path
            # would be an unlimited-insert path. The cap check is
            # additionally exempted for net-neutral updates — see
            # `_check_write_quota` for the reversal rule that lets a
            # user at the cap still correct an existing memory.
            #
            # `replace_memory_active=True` requires the existing row
            # to be `status='active' AND valid_to IS NULL` — that is,
            # the row the close call will actually close.
            # Without this check a caller at the cap could name an
            # already-closed row and obtain an exemption for a write
            # that increases the active count. `close_memory()` now
            # reports affected-row success as a second guard against a
            # concurrent close after this authorized preflight read.
            replace_active = (
                old_memory.get("status") == "active" and old_memory.get("valid_to") is None
            )
            quota = await self._check_write_quota(
                action,
                replace_memory_id=memory_id,
                replace_memory_active=replace_active,
            )
            if quota.refusal is not None:
                return quota.refusal

            # Inherit category and slot if not provided
            content = kwargs.get("content", old_memory.get("content", ""))
            category = kwargs.get("category", old_memory.get("category", "fact"))
            slot = kwargs.get("slot", old_memory.get("memory_slot"))

            # Compute the external embedding before opening the database
            # transaction, then serialize the authoritative count, close,
            # and replacement insert under one per-user advisory lock.
            embedding_result = await prepare_memory_embedding(content, slot)
            cap_conn = None
            try:
                cap_conn, _ = await self.store.acquire_user_cap_lock(self.user_id)
                active_rows = await self.store.count_active_memories_for_user_within_lock(
                    cap_conn, self.user_id
                )
                if active_rows >= MEMORY_WRITE_MAX_ACTIVE_ROWS and not replace_active:
                    _release_attempt_reservation(self.user_id, quota.reservation)
                    await cap_conn.execute("ROLLBACK")
                    await self.store._pool.release(cap_conn)
                    cap_conn = None
                    return (
                        f"Memory storage cap reached ({MEMORY_WRITE_MAX_ACTIVE_ROWS} "
                        "active memories). Delete an existing memory before writing "
                        "a new one."
                    )

                close_took_effect = await self.store.close_memory(
                    memory_id, user_id=self.user_id, conn=cap_conn
                )
                if replace_active and not close_took_effect:
                    _release_attempt_reservation(self.user_id, quota.reservation)
                    await cap_conn.execute("ROLLBACK")
                    await self.store._pool.release(cap_conn)
                    cap_conn = None
                    return (
                        "Memory was modified concurrently and could not be replaced. "
                        "Retry the update."
                    )

                new_memory_id = await dedup_and_store(
                    store=self.store,
                    user_id=self.user_id,
                    content=content,
                    source_type="user_created",
                    category=category,
                    conversation_id=old_memory.get("source_conversation_id"),
                    slot=slot,
                    lock_conn=cap_conn,
                    embedding_result=embedding_result,
                )
                await cap_conn.execute("COMMIT")
                await self.store._pool.release(cap_conn)
                cap_conn = None
            except BaseException:
                if cap_conn is not None:
                    try:
                        await cap_conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    try:
                        await self.store._pool.release(cap_conn)
                    except Exception:
                        pass
                _release_attempt_reservation(self.user_id, quota.reservation)
                raise

            await self._check_and_set_contradiction(new_memory_id, content, slot)
            return f"Memory updated. Old ID: {memory_id}, New ID: {new_memory_id}."

        elif action == "delete":
            memory_id_str = kwargs.get("memory_id")
            if not memory_id_str:
                return "memory_id is required for delete"
            try:
                memory_id = uuid.UUID(memory_id_str)
            except ValueError:
                return "Invalid memory_id format"
            old_memory = await self.store.get_memory(memory_id)
            if not old_memory:
                return "Memory not found"
            # Same authorization guard as `update` above: the target memory
            # must belong to the calling user. Without this check a user
            # who learns another user's memory UUID can soft-delete that
            # memory via the LLM tool path.
            if old_memory.get("user_id") != self.user_id:
                return "Memory not found"
            # Defense-in-depth: also pass `user_id` to the store so the
            # SQL UPDATE itself rejects any future caller that forgets the
            # tool-layer guard.
            await self.store.delete_memory(memory_id, soft=True, user_id=self.user_id)
            return f"Memory {memory_id} deleted."

        return json.dumps({"error": f"Unknown action: {action}"})
