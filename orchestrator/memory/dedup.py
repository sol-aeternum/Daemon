from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from orchestrator.memory.store import MemoryStore
from orchestrator.memory.embedding import embed_text


@dataclass
class DedupResult:
    merged: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    new: list[dict[str, Any]] = field(default_factory=list)


# Thresholds per Spec E
SIMILARITY_MERGE = 0.92
SIMILARITY_SUPERSEDE = 0.75


async def deduplicate_facts(
    store: MemoryStore, user_id: uuid.UUID, facts: list[Any], conversation_id: uuid.UUID
) -> DedupResult:
    """Deduplicate extracted facts against existing memories."""
    result = DedupResult()

    for fact in facts:
        # Generate embedding for the fact
        embedding = await embed_text(fact.content)

        # Search similar memories
        similar = await store.search_memories(
            user_id=user_id,
            query_embedding=embedding,
            limit=5,
            min_similarity=SIMILARITY_SUPERSEDE,
        )

        if not similar:
            # No similar memories - create new
            memory = await store.insert_memory(
                user_id=user_id,
                content=fact.content,
                category=fact.category,
                source_type="extraction",
                embedding=embedding,
                source_conversation_id=conversation_id,
                confidence=fact.confidence,
            )
            result.new.append(memory)
        else:
            # Check similarity with best match
            best_match = similar[0]
            similarity = best_match.get("similarity", 0)

            if similarity >= SIMILARITY_MERGE:
                # High similarity - merge (touch existing)
                await store.touch_memory(best_match["id"])
                result.merged.append(best_match)
            elif similarity >= SIMILARITY_SUPERSEDE:
                # Medium similarity - supersede
                new_memory = await store.supersede_memory(
                    old_memory_id=best_match["id"],
                    new_content=fact.content,
                    new_category=fact.category,
                    new_source_type="extraction",
                    user_id=user_id,
                    embedding=embedding,
                    source_conversation_id=conversation_id,
                    confidence=fact.confidence,
                )
                result.superseded.append(new_memory)
            else:
                # Low similarity - create new
                memory = await store.insert_memory(
                    user_id=user_id,
                    content=fact.content,
                    category=fact.category,
                    source_type="extraction",
                    embedding=embedding,
                    source_conversation_id=conversation_id,
                    confidence=fact.confidence,
                )
                result.new.append(memory)

    return result
