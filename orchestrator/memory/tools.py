"""Memory tools for Daemon tool system."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.dedup import dedup_and_store, check_contradiction
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
# `memory_write` tool. The counter tracks attempted embedding-billed
# writes rather than inserted rows, because deduplication can merge many
# billed attempts into one database row. An OrderedDict provides LRU
# order so total process memory remains bounded even during a burst of
# entirely fresh users.
_attempt_log: OrderedDict[uuid.UUID, deque[datetime]] = OrderedDict()
_attempt_log_last_seen: dict[uuid.UUID, datetime] = {}
_attempt_log_lock: asyncio.Lock | None = None

# Bound the per-process ledger independently of timestamp freshness.
# LRU eviction is an abuse-dampener tradeoff: an evicted user's next
# write starts a new local window. Exact cross-process enforcement is a
# separate shared-store concern.
_ATTEMPT_LOG_MAX_USERS: int = 1024
_ATTEMPT_LOG_INACTIVITY_SECONDS: int = 600
_ATTEMPT_LOG_SWEEP_EVERY_N: int = 256
_attempt_log_call_count: int = 0


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
    ) -> str | None:
        """Enforce the per-user write-rate limit and active-row cap.

        Returns `None` when the write may proceed, or a caller-facing
        refusal string when it may not. Called at the top of the
        mutating branches of `execute()` — crucially **before** any
        embedding call, because the embedding request is the billed
        operation the cost-amplification half of issue #62 is about.

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
        `close_memory()` silently updates zero rows for an
        already-closed target (it returns `True` because the row
        physically exists), so without this check a caller at the cap
        could bypass the cap by passing any UUID — the close would be
        a no-op, the dedup/insert would add a row, and the active
        count would silently exceed the cap. Round-3 Codex review
        flagged exactly this path; the fix restricts the exemption to
        rows the close call will actually close.

        Fails **open** on a counting error. The quota is an abuse
        dampener, not an authorization boundary (the `user_id`
        ownership checks below are the security control), so a
        transient database error must not silently disable the user's
        memory. The failure is logged at warning level so operators can
        see it.

        The **rate limit** (issue #62's per-user writes-per-minute
        guard) is enforced via an in-process per-user sliding-window
        counter — see `_attempt_log` above for the design rationale.
        The active-row cap remains a row-count predicate via
        `count_active_memories`; that one counts rows correctly
        because it does not need to track billed embedding calls.
        """
        # Lazy init: the module-level lock must be created inside a
        # running event loop, so it is allocated on first use.
        global _attempt_log_lock
        if _attempt_log_lock is None:
            _attempt_log_lock = asyncio.Lock()

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=MEMORY_WRITE_WINDOW_SECONDS)
        try:
            async with _attempt_log_lock:
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
                    return (
                        f"Memory write rate limit reached "
                        f"({MEMORY_WRITE_MAX_PER_WINDOW} writes per "
                        f"{MEMORY_WRITE_WINDOW_SECONDS}s). Wait before writing again, "
                        f"or summarise several facts into a single memory."
                    )
                return None
        except Exception as error:
            logger.warning(
                "memory_write quota check failed; allowing write user_id=%s action=%s error=%s",
                self.user_id,
                action,
                type(error).__name__,
            )
            return None

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
            return (
                f"Memory write rate limit reached "
                f"({MEMORY_WRITE_MAX_PER_WINDOW} writes per "
                f"{MEMORY_WRITE_WINDOW_SECONDS}s). Wait before writing again, "
                f"or summarise several facts into a single memory."
            )

        if active_rows >= MEMORY_WRITE_MAX_ACTIVE_ROWS:
            # A net-neutral `update` is exempt: the close+insert pair
            # leaves the active count unchanged, so refusing it at the
            # cap would make the cap terminal (the user couldn't even
            # correct an existing memory once hit). Only exempt when
            # `replace_memory_active` is True — that is, the row the
            # caller named is actually closable (status='active' AND
            # valid_to IS NULL). Without this check, a caller at the
            # cap can pass any UUID: `close_memory` returns True for an
            # already-closed row (it physically exists) but updates 0
            # rows, then `dedup_and_store` inserts a new memory and
            # the active count silently exceeds the cap. Round-3
            # Codex review surfaced this path; the fix restricts the
            # exemption to rows the close call will actually close.
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
                return None

            logger.warning(
                "memory_write_cap_exceeded user_id=%s action=%s active_rows=%s cap=%s",
                self.user_id,
                action,
                active_rows,
                MEMORY_WRITE_MAX_ACTIVE_ROWS,
            )
            return (
                f"Memory storage cap reached ({MEMORY_WRITE_MAX_ACTIVE_ROWS} "
                f"active memories). Delete an existing memory before writing "
                f"a new one."
            )

        return None

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
            refusal = await self._check_write_quota(action)
            if refusal is not None:
                return refusal
            effective_slot = slot if isinstance(slot, str) else None
            memory_id = await dedup_and_store(
                store=self.store,
                user_id=self.user_id,
                content=content,
                source_type="user_created",
                category=category,
                conversation_id=None,
                slot=effective_slot,
            )
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
            # `close_memory()` silently updates zero rows for an
            # already-closed row (it physically exists), so without
            # this check a caller at the cap could pass any UUID,
            # the close would be a no-op, and the dedup/insert would
            # silently raise the active count above the cap. Round-3
            # Codex review surfaced this path; `old_memory` is the
            # row we already fetched and authorized, so its
            # `valid_to` is the authoritative signal.
            replace_active = (
                old_memory.get("status") == "active" and old_memory.get("valid_to") is None
            )
            refusal = await self._check_write_quota(
                action,
                replace_memory_id=memory_id,
                replace_memory_active=replace_active,
            )
            if refusal is not None:
                return refusal

            # Inherit category and slot if not provided
            content = kwargs.get("content", old_memory.get("content", ""))
            category = kwargs.get("category", old_memory.get("category", "fact"))
            slot = kwargs.get("slot", old_memory.get("memory_slot"))

            # Close the old memory (defense-in-depth: store-layer
            # `user_id` filter also constrains the UPDATE).
            await self.store.close_memory(memory_id, user_id=self.user_id)

            # Insert new memory with fresh embedding
            new_memory_id = await dedup_and_store(
                store=self.store,
                user_id=self.user_id,
                content=content,
                source_type="user_created",
                category=category,
                conversation_id=old_memory.get("source_conversation_id"),
                slot=slot,
            )
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
