"""Memory consolidation and clustering module for Tier 2 memory upgrades.

Implements memory clustering to find groups of related memories
within the same slot family for potential consolidation.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


# Similarity threshold for clustering memories within the same slot family
# Raised from 0.60 to 0.65 to reduce over-aggregation of unrelated memories
CLUSTER_SIMILARITY_THRESHOLD = 0.65
# Minimum cluster size to return
MIN_CLUSTER_SIZE = 3


@dataclass
class MemoryCluster:
    """A cluster of related memories within the same slot family."""

    slot_family: str
    members: list[dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.members)


def _get_slot_family(memory_slot: str | None) -> str | None:
    """Extract slot family from memory_slot (e.g., 'vehicle.car.model' -> 'vehicle.car').

    Uses first TWO segments for more specific grouping, preventing over-aggregation
    of unrelated memories under overly broad categories like 'project' or 'preference'.

    Args:
        memory_slot: The memory slot string (e.g., 'project.tech.software')

    Returns:
        The slot family (first two segments) or None if invalid
    """
    if not memory_slot:
        return None
    parts = memory_slot.split(".")
    # Use first two segments for more specific grouping
    # 'project.tech.software' -> 'project.tech'
    # 'preference.food.cuisine' -> 'preference.food'
    # 'vehicle.car.model' -> 'vehicle.car'
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return parts[0] if parts else None


def _parse_embedding(embedding_data: Any) -> list[float] | None:
    """Parse embedding from database format to list of floats.

    Args:
        embedding_data: Raw embedding data from database (could be string or list)

    Returns:
        List of floats or None if parsing fails
    """
    if embedding_data is None:
        return None

    # If it's already a list, return it
    if isinstance(embedding_data, list):
        return [float(x) for x in embedding_data]

    # If it's a string (asyncpg sometimes returns vectors as strings), parse it
    if isinstance(embedding_data, str):
        # Remove brackets and split
        cleaned = embedding_data.strip("[]")
        if not cleaned:
            return None
        try:
            return [float(x) for x in cleaned.split(",")]
        except ValueError:
            return None

    return None


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity (-1 to 1)
    """
    if len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


async def find_memory_clusters(
    user_id: uuid.UUID,
    store: MemoryStore,
    similarity_threshold: float = CLUSTER_SIMILARITY_THRESHOLD,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> list[MemoryCluster]:
    """Find clusters of related memories within the same slot family.

    Fetches all active L1 memories for the user, groups them by slot family,
    and finds clusters where pairwise similarity >= threshold.

    Args:
        user_id: UUID of the user
        store: MemoryStore instance
        similarity_threshold: Minimum similarity for clustering (default 0.65)
        min_cluster_size: Minimum number of memories in a cluster (default 3)

    Returns:
        List of MemoryCluster objects with 3+ members each
    """
    # Fetch all active L1 memories with embeddings
    rows = await store._pool.fetch(
        """
        SELECT id, content, category, memory_slot, embedding, confidence, 
               created_at, access_count, trust_score
        FROM memories
        WHERE user_id = $1
          AND status = 'active'
          AND tier = 'l1'
          AND embedding IS NOT NULL
        ORDER BY memory_slot, created_at DESC
        """,
        user_id,
    )

    if not rows:
        return []

    # Parse memories and group by slot family
    memories_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        memory = dict(row)
        memory_slot = memory.get("memory_slot")
        slot_family = _get_slot_family(memory_slot)

        if not slot_family:
            continue

        # Parse embedding
        embedding = _parse_embedding(memory.get("embedding"))
        if embedding is None:
            continue

        memory["_embedding"] = embedding  # Store parsed embedding
        memories_by_family[slot_family].append(memory)

    # Find clusters within each slot family
    clusters: list[MemoryCluster] = []

    for slot_family, memories in memories_by_family.items():
        if len(memories) < min_cluster_size:
            continue

        # Build similarity graph
        # Two memories are in the same cluster if their similarity >= threshold
        n = len(memories)
        visited = [False] * n

        for i in range(n):
            if visited[i]:
                continue

            # Start a new cluster with memory i
            cluster_members = [memories[i]]
            visited[i] = True

            # Find all memories similar to any member of this cluster
            # Use union-find style: check against all current cluster members
            changed = True
            while changed:
                changed = False
                for j in range(n):
                    if visited[j]:
                        continue

                    # Check if memory j is similar to any member in cluster
                    for member in cluster_members:
                        sim = _cosine_similarity(memories[j]["_embedding"], member["_embedding"])
                        if sim >= similarity_threshold:
                            cluster_members.append(memories[j])
                            visited[j] = True
                            changed = True
                            break

            # Only keep clusters with sufficient members
            if len(cluster_members) >= min_cluster_size:
                # Clean up internal fields before returning
                for mem in cluster_members:
                    mem.pop("_embedding", None)

                clusters.append(
                    MemoryCluster(
                        slot_family=slot_family,
                        members=cluster_members,
                    )
                )

    return clusters


async def get_cluster_candidates_count(
    user_id: uuid.UUID,
    store: MemoryStore,
) -> dict[str, int]:
    """Get count of candidate memories for clustering by slot family.

    Helper function for verification and monitoring.

    Args:
        user_id: UUID of the user
        store: MemoryStore instance

    Returns:
        Dict mapping slot family to count of L1 memories
    """
    rows = await store._pool.fetch(
        """
        SELECT memory_slot, COUNT(*) as count
        FROM memories
        WHERE user_id = $1
          AND status = 'active'
          AND tier = 'l1'
          AND embedding IS NOT NULL
        GROUP BY memory_slot
        ORDER BY count DESC
        """,
        user_id,
    )

    result: dict[str, int] = defaultdict(int)
    for row in rows:
        slot_family = _get_slot_family(row["memory_slot"])
        if slot_family:
            result[slot_family] += row["count"]

    return dict(result)


# Consolidation constants
CONSOLIDATION_PROMPT = """Synthesize these related facts into 1-2 concise summary statements.

Input facts (plaintext - already readable):
{facts}

CRITICAL INSTRUCTIONS:
- The facts above are already decrypted and readable plaintext
- Synthesize them DIRECTLY - do NOT mention encryption, decryption, or tokens
- NEVER say facts are encrypted or that you cannot access them
- Just create 1-2 clear summary statements based on the facts provided

Guidelines:
- Preserve specifics — don't over-generalize
- Include concrete details, numbers, dates, and specific names
- Create 1-2 clear summary statements that capture the essence
- Maintain the user's voice and perspective
- Keep summaries focused and coherent

Output format:
Provide only the synthesized summary statements, no additional commentary."""


def _get_orchestrator_model() -> str:
    """Get the orchestrator-tier model from settings."""
    settings = get_settings()
    tier_config = settings.get_tier_config(settings.default_tier)
    return tier_config.orchestrator.model


def _extract_content(response: Any) -> str:
    """Extract content from litellm response (handles multiple response types including Pydantic models)."""
    content: Any = None

    # First try: response.choices[0].message.content (most common)

    try:
        # First try: response.choices[0].message.content (most common)
        choices = getattr(response, "choices", None)
        if choices and len(choices) > 0:
            choice = choices[0]
            # Handle both dict and Pydantic model
            if hasattr(choice, "message"):
                message = choice.message
                if hasattr(message, "content"):
                    content = message.content
                    if content:
                        return str(content)
            elif isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if content:
                        return str(content)

        # Second try: model_dump() for Pydantic v2
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            try:
                data = model_dump()
                if isinstance(data, dict):
                    choices = data.get("choices")
                    if choices and len(choices) > 0:
                        message = choices[0].get("message")
                        if isinstance(message, dict):
                            content = message.get("content")
                            if content:
                                return str(content)
            except Exception:
                pass

        # Third try: dict() for Pydantic v1
        dict_method = getattr(response, "dict", None)
        if callable(dict_method):
            try:
                data = dict_method()
                if isinstance(data, dict):
                    choices = data.get("choices")
                    if choices and len(choices) > 0:
                        message = choices[0].get("message")
                        if isinstance(message, dict):
                            content = message.get("content")
                            if content:
                                return str(content)
            except Exception:
                pass

        # Fourth try: direct attributes on response
        if hasattr(response, "content"):
            content = response.content
            if content:
                return str(content)

        return ""

    except Exception:
        return ""


async def consolidate_cluster(
    cluster: MemoryCluster,
    store: MemoryStore,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Synthesize cluster memories into summary memories.

    Takes a cluster of related memories, generates 1-2 summary statements
    using the orchestrator-tier model, creates new summary memories,
    and demotes source memories to tier='l2'.

    Args:
        cluster: MemoryCluster containing related memories
        store: MemoryStore instance
        user_id: UUID of the user

    Returns:
        List of created summary memory dicts
    """
    if not cluster.members:
        return []

    # Get orchestrator-tier model from settings
    model = _get_orchestrator_model()
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

    # Initialize encryption for decrypting source content
    encryption = ContentEncryption(settings.daemon_encryption_key)

    # Collect source memory IDs
    source_memory_ids = [str(m.get("id")) for m in cluster.members if m.get("id")]

    # Format facts as numbered list - DECRYPT content for LLM
    facts_list = []
    for i, mem in enumerate(cluster.members, 1):
        content = mem.get("content", "")
        # Decrypt if content appears to be encrypted
        if content.startswith("gAAAA"):
            try:
                content = encryption.decrypt(content)
            except Exception:
                pass  # Keep original if decryption fails
        category = mem.get("category", "fact")
        slot = mem.get("memory_slot", "")
        facts_list.append(f"{i}. [{category}] {content} (slot: {slot})")

    facts_text = "\n".join(facts_list)

    # Build prompt
    prompt = CONSOLIDATION_PROMPT.format(facts=facts_text)

    # Build call params with provider config
    call_params: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
        "timeout": provider_config.timeout_s,
    }

    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    # Call LLM for synthesis
    try:
        response = await litellm.acompletion(**call_params)

        # Extract synthesized text using robust extraction
        synthesized_text = _extract_content(response)

        # Debug logging (only if logger available)
        try:
            import logging

            _logger = logging.getLogger(__name__)
            _logger.debug(f"Synthesis response type: {type(response)}")
            _logger.debug(f"Extracted text length: {len(synthesized_text)}")
            _logger.debug(f"Extracted text preview: {synthesized_text[:100]}...")
        except:  # noqa: E722
            pass

        stripped = synthesized_text.strip()
        if len(stripped) < 10:  # Require at least 10 chars
            import logging

            logging.getLogger(__name__).warning(
                f"Synthesized text too short or empty: '{stripped}'"
            )
            return []

        # Check for encryption hallucinations - reject and log if found
        lower_text = stripped.lower()
        if any(
            phrase in lower_text
            for phrase in ["encrypted", "decrypt", "fernet", "cannot access", "cannot synthesize"]
        ):
            import logging

            logging.getLogger(__name__).warning(
                f"Consolidation produced encryption-related output - rejecting: {stripped[:100]}"
            )
            return []

        # Split into 1-2 summary statements
        summaries = [s.strip() for s in synthesized_text.split("\n") if s.strip()]
        summaries = summaries[:2]  # Take at most 2

        # Create summary memories
        created_memories = []
        for summary_text in summaries:
            # Create memory with correct metadata
            memory = await store.insert_memory(
                user_id=user_id,
                content=summary_text,
                category="summary",
                source_type="consolidation",
                memory_slot=f"{cluster.slot_family}.consolidated",
                confidence=0.85,
                local_only=False,
            )

            # Update tier to L1 (insert_memory defaults might differ)
            await store.update_memory_tier(
                memory_id=uuid.UUID(str(memory["id"])),
                tier="l1",
            )

            # Add metadata with source IDs
            metadata_json = json.dumps(
                {"source_memory_ids": source_memory_ids, "consolidated_from": len(cluster.members)}
            )
            await store._pool.execute(
                """
                UPDATE memories
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
                WHERE id = $1
                """,
                uuid.UUID(str(memory["id"])),
                metadata_json,
            )

            created_memories.append(memory)

        # Demote source memories to tier L2
        for mem in cluster.members:
            mem_id = mem.get("id")
            if mem_id:
                try:
                    await store.update_memory_tier(
                        memory_id=uuid.UUID(str(mem_id)),
                        tier="l2",
                    )
                except Exception:
                    pass  # Best effort

        return created_memories

    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Cluster consolidation failed: {e}")
        return []
