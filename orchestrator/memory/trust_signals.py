"""Trust signal orchestration module.

Implements implicit positive and explicit negative trust signals
per the Tier 2 memory upgrades plan.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from orchestrator.memory.store import MemoryStore

# Dynamic imports to avoid circular dependency diagnostics
_boost_trust = None
_penalize_trust = None
_record_retrieval = None


def _lazy_import_trust_funcs():
    global _boost_trust, _penalize_trust, _record_retrieval
    if _boost_trust is None:
        import importlib

        trust_module = importlib.import_module("orchestrator.memory.trust")
        _boost_trust = getattr(trust_module, "boost_trust", None)
        _penalize_trust = getattr(trust_module, "penalize_trust", None)
        _record_retrieval = getattr(trust_module, "record_retrieval", None)
    return _boost_trust, _penalize_trust, _record_retrieval


# How recent is "recently retrieved" for explicit penalty
RECENT_RETRIEVAL_WINDOW_MINUTES = 30  # Wall-clock fallback
RECENT_TURN_COUNT = 3  # Number of recent user messages to consider "recent"


async def record_retrieved_memories(
    conversation_id: uuid.UUID,
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> bool:
    """Record retrieved memory IDs on the conversation for trust tracking.

    Called after orchestrator generates a response with memory context.
    Stores the memory IDs in conversations.last_retrieved_memory_ids (JSONB).

    Args:
        conversation_id: UUID of the conversation
        memory_ids: List of retrieved memory UUIDs
        store: MemoryStore instance

    Returns:
        True if recording succeeded
    """
    if not memory_ids:
        return True

    try:
        # Update conversation with retrieved memory IDs (direct SQL to avoid pyright issues)
        # Serialize to JSON for PostgreSQL JSONB column
        memory_ids_json = json.dumps([str(m) for m in memory_ids]) if memory_ids else None
        await store._pool.execute(
            """
            UPDATE conversations
            SET last_retrieved_memory_ids = COALESCE($2::jsonb, last_retrieved_memory_ids),
                updated_at = NOW()
            WHERE id = $1
            """,
            conversation_id,
            memory_ids_json,
        )

        # Also record retrieval timestamps on the memories themselves
        _, _, _record_retrieval = _lazy_import_trust_funcs()
        if _record_retrieval:
            await _record_retrieval(memory_ids, store)

        return True
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to record retrieved memories: {e}")
        return False


async def apply_implicit_positive_signal(
    conversation_id: uuid.UUID,
    store: MemoryStore,
    user_id: uuid.UUID,
    correction_occurred: bool = False,
) -> int:
    """Apply implicit positive trust boost if no correction occurred.

    Called on the NEXT user turn (delayed by one turn).
    Retrieves the last_retrieved_memory_ids from conversation and
    applies boost_trust if no memory_write correction tool was called.

    Args:
        conversation_id: UUID of the conversation
        store: MemoryStore instance
        user_id: UUID of the user (for authorization)
        correction_occurred: Whether a memory_write correction tool was called

    Returns:
        Number of memories boosted
    """
    if correction_occurred:
        # Don't boost if correction occurred - the retrieved memory was wrong
        # But still clear the recorded IDs (one-shot semantics)
        try:
            await store._pool.execute(
                """
                UPDATE conversations
                SET last_retrieved_memory_ids = '[]'::jsonb,
                    updated_at = NOW()
                WHERE id = $1
                """,
                conversation_id,
            )
        except Exception:
            pass
        return 0

    try:
        # Get the conversation to find last retrieved memory IDs
        conversation = await store.get_conversation(conversation_id)
        if not conversation:
            return 0

        raw_ids = conversation.get("last_retrieved_memory_ids", [])
        # JSONB columns come back as strings from asyncpg - parse them
        if isinstance(raw_ids, str):
            try:
                memory_ids = json.loads(raw_ids)
            except json.JSONDecodeError:
                return 0
        else:
            memory_ids = raw_ids
        if not memory_ids:
            return 0

        # Convert to proper UUID objects (handle both string and UUID types)
        uuid_list = []
        for mid in memory_ids:
            try:
                if isinstance(mid, uuid.UUID):
                    uuid_list.append(mid)
                else:
                    uuid_list.append(uuid.UUID(str(mid)))
            except (ValueError, TypeError):
                # Skip invalid UUIDs
                continue

        # Apply boost
        _boost_trust, _, _ = _lazy_import_trust_funcs()
        boosted_count = await _boost_trust(uuid_list, store) if _boost_trust else 0

        # Clear the recorded memory IDs after applying boost (direct SQL)
        # (one-shot per conversation turn)
        await store._pool.execute(
            """
            UPDATE conversations
            SET last_retrieved_memory_ids = '[]'::jsonb,
                updated_at = NOW()
            WHERE id = $1
            """,
            conversation_id,
        )

        return boosted_count

    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to apply implicit positive signal: {e}")
        return 0


async def apply_explicit_negative_signal(
    superseded_memory_id: uuid.UUID,
    store: MemoryStore,
) -> bool:
    """Apply explicit negative trust penalty for superseded memories.

    Called when a memory is superseded via dedup.
    Checks if the superseded memory was recently retrieved
    (within last 3 user turns or 30 minutes).

    Args:
        superseded_memory_id: UUID of the memory being superseded
        store: MemoryStore instance

    Returns:
        True if penalty was applied
    """
    try:
        pool = store._pool

        # Get the memory to check last_retrieved_at and user_id
        row = await pool.fetchrow(
            """
            SELECT last_retrieved_at, user_id
            FROM memories
            WHERE id = $1
            """,
            superseded_memory_id,
        )

        if not row:
            return False

        last_retrieved = row.get("last_retrieved_at")
        if not last_retrieved:
            # Memory was never retrieved - no penalty needed
            return False

        user_id = row.get("user_id")
        is_recent = False

        # Check 1: Wall-clock recency (fallback)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=RECENT_RETRIEVAL_WINDOW_MINUTES)
        if last_retrieved >= cutoff:
            is_recent = True

        # Check 2: Conversation turn recency (primary)
        # Count user messages since last_retrieval for this user
        if user_id and not is_recent:
            turn_row = await pool.fetchrow(
                """
                SELECT COUNT(*) as message_count
                FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE c.user_id = $1
                  AND m.role = 'user'
                  AND m.created_at > $2
                """,
                user_id,
                last_retrieved,
            )
            if turn_row and turn_row["message_count"] <= RECENT_TURN_COUNT:
                is_recent = True

        if not is_recent:
            return False

        # Apply penalty
        _, _penalize_trust, _ = _lazy_import_trust_funcs()
        if _penalize_trust:
            await _penalize_trust([superseded_memory_id], store)
        return True

    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to apply explicit negative signal: {e}")
        return False
