"""Trust score management module for Daemon memory layer.

Implements trust score boost/penalize functions and retrieval tracking
with bulk UPDATE queries and floor/ceiling enforcement.
"""

from __future__ import annotations

import uuid
from datetime import datetime


from orchestrator.memory.store import MemoryStore


# Trust score floor and ceiling constants
TRUST_FLOOR = 0.1
TRUST_CEILING = 1.0
TRUST_BOOST_AMOUNT = 0.05
TRUST_PENALTY_AMOUNT = 0.10


async def boost_trust(
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> int:
    """Boost trust score for retrieved memories.

    Increments trust_score by 0.05, capped at 1.0.
    Uses bulk UPDATE query for efficiency.

    Args:
        memory_ids: List of memory UUIDs to boost
        store: MemoryStore instance

    Returns:
        Number of rows updated
    """
    if not memory_ids:
        return 0

    query = """
        UPDATE memories
        SET trust_score = LEAST(trust_score + $2, $3),
            updated_at = NOW()
        WHERE id = ANY($1::uuid[])
        AND trust_score < $3
        RETURNING id
    """

    try:
        rows = await store._pool.fetch(
            query,
            memory_ids,
            TRUST_BOOST_AMOUNT,
            TRUST_CEILING,
        )
        return len(rows)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"boost_trust failed: {e}")
        return 0


async def penalize_trust(
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> int:
    """Penalize trust score for incorrect/outdated memories.

    Decrements trust_score by 0.10, floored at 0.1.
    Uses bulk UPDATE query for efficiency.

    Args:
        memory_ids: List of memory UUIDs to penalize
        store: MemoryStore instance

    Returns:
        Number of rows updated
    """
    if not memory_ids:
        return 0

    query = """
        UPDATE memories
        SET trust_score = GREATEST(trust_score - $2, $3),
            updated_at = NOW()
        WHERE id = ANY($1::uuid[])
        AND trust_score > $3
        RETURNING id
    """

    try:
        rows = await store._pool.fetch(
            query,
            memory_ids,
            TRUST_PENALTY_AMOUNT,
            TRUST_FLOOR,
        )
        return len(rows)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"penalize_trust failed: {e}")
        return 0


async def record_retrieval(
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> int:
    """Record retrieval timestamp for memories.

    Updates last_retrieved_at to current timestamp.
    Uses bulk UPDATE query for efficiency.

    Args:
        memory_ids: List of memory UUIDs to update
        store: MemoryStore instance

    Returns:
        Number of rows updated
    """
    if not memory_ids:
        return 0

    query = """
        UPDATE memories
        SET last_retrieved_at = NOW(),
            updated_at = NOW()
        WHERE id = ANY($1::uuid[])
        RETURNING id
    """

    try:
        rows = await store._pool.fetch(query, memory_ids)
        return len(rows)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"record_retrieval failed: {e}")
        return 0


async def get_trust_scores(
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> dict[uuid.UUID, float]:
    """Get current trust scores for memory IDs.

    Helper for verification and testing.

    Args:
        memory_ids: List of memory UUIDs to query
        store: MemoryStore instance

    Returns:
        Dict mapping memory_id to trust_score
    """
    if not memory_ids:
        return {}

    query = """
        SELECT id, trust_score
        FROM memories
        WHERE id = ANY($1::uuid[])
    """

    try:
        rows = await store._pool.fetch(query, memory_ids)
        return {row["id"]: row["trust_score"] for row in rows}
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"get_trust_scores failed: {e}")
        return {}


async def get_last_retrieved_at(
    memory_ids: list[uuid.UUID],
    store: MemoryStore,
) -> dict[uuid.UUID, datetime | None]:
    """Get last retrieval timestamps for memory IDs.

    Helper for verification and testing.

    Args:
        memory_ids: List of memory UUIDs to query
        store: MemoryStore instance

    Returns:
        Dict mapping memory_id to last_retrieved_at
    """
    if not memory_ids:
        return {}

    query = """
        SELECT id, last_retrieved_at
        FROM memories
        WHERE id = ANY($1::uuid[])
    """

    try:
        rows = await store._pool.fetch(query, memory_ids)
        return {row["id"]: row["last_retrieved_at"] for row in rows}
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"get_last_retrieved_at failed: {e}")
        return {}
