#!/usr/bin/env python3
"""
Embedding Alignment Diagnostic Script

Tests whether asymmetric embedding (voyage-4-large for docs, voyage-4-lite for queries)
is causing low similarity scores in memory retrieval.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from typing import Any

sys.path.insert(0, "/app")

import asyncpg
from orchestrator.config import get_settings
from orchestrator.database_url import resolve_database_url
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.embedding import _embed_texts


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    if len(vec1) != len(vec2):
        raise ValueError(f"Vector dimension mismatch: {len(vec1)} vs {len(vec2)}")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def parse_pgvector_embedding(embedding_val: Any) -> list[float]:
    if isinstance(embedding_val, list):
        return embedding_val
    if isinstance(embedding_val, str):
        content = embedding_val.strip("[]")
        if not content:
            return []
        return [float(x) for x in content.split(",")]
    if hasattr(embedding_val, "tolist"):
        return embedding_val.tolist()
    raise ValueError(f"Unknown embedding type: {type(embedding_val)}")


async def get_test_memory(
    db_pool: asyncpg.Pool, encryption: ContentEncryption
) -> dict[str, Any] | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, content, embedding, embedding_model, category
            FROM memories
            WHERE status = 'active'
              AND valid_to IS NULL
              AND (content ILIKE '%project%' OR content ILIKE '%work%')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )

        if not row:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, content, embedding, embedding_model, category
                FROM memories
                WHERE status = 'active'
                  AND valid_to IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            )

        if not row:
            return None

        embedding_list = parse_pgvector_embedding(row["embedding"])

        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "content": encryption.decrypt(row["content"]),
            "embedding": embedding_list,
            "embedding_model": row["embedding_model"],
            "category": row["category"],
        }


async def run_alignment_test():
    print("=" * 70)
    print("EMBEDDING ALIGNMENT DIAGNOSTIC")
    print("=" * 70)
    print()

    settings = get_settings()
    print(f"Document model (storage): {settings.embedding_document_model}")
    print(f"Query model (retrieval):  {settings.embedding_query_model}")
    print(f"Embedding dimensions:     {settings.embedding_dimensions}")
    print()

    db_url = resolve_database_url(settings.database_url)
    if not db_url:
        raise RuntimeError("DATABASE_URL or complete POSTGRES_* settings are required")
    print("Connecting to database...")

    db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    encryption = ContentEncryption(os.environ.get("DAEMON_ENCRYPTION_KEY"))

    try:
        print("Fetching test memory...")
        memory = await get_test_memory(db_pool, encryption)

        if not memory:
            print("ERROR: No active memories found in database.")
            return

        print(f"Found memory: {memory['id'][:8]}...")
        print(f"  Category: {memory['category']}")
        print(f"  Model: {memory['embedding_model']}")
        print(
            f"  Content: {memory['content'][:100]}{'...' if len(memory['content']) > 100 else ''}"
        )
        print()

        memory_content = memory["content"]

        if "project" in memory_content.lower():
            query = "What projects is the user working on?"
        elif "work" in memory_content.lower():
            query = "What does the user do for work?"
        elif "tesla" in memory_content.lower() or "car" in memory_content.lower():
            query = "What car does the user drive?"
        elif "live" in memory_content.lower() or "city" in memory_content.lower():
            query = "Where does the user live?"
        else:
            query = f"Tell me about: {memory_content[:50]}"

        print(f'Test query: "{query}"')
        print()

        stored_embedding = memory["embedding"]
        print(f"Stored embedding dimension: {len(stored_embedding)}")

        # 1. Embed query with voyage-4-lite
        print("\n[1/3] Embedding query with voyage-4-lite (query model)...")
        query_embedding_lite = await _embed_texts(
            [query],
            model=settings.embedding_query_model,
            input_type="query",
            max_tokens=1_000_000,
        )
        query_vec_lite = query_embedding_lite[0]
        print(f"  Query embedding dimension: {len(query_vec_lite)}")

        # 2. Embed memory text with voyage-4-lite (same model baseline)
        print("\n[2/3] Embedding memory text with voyage-4-lite (same model baseline)...")
        memory_embedding_lite = await _embed_texts(
            [memory_content],
            model=settings.embedding_query_model,
            input_type="document",
            max_tokens=120_000,
        )
        memory_vec_lite = memory_embedding_lite[0]
        print(f"  Memory embedding dimension: {len(memory_vec_lite)}")

        # 3. Embed memory text with voyage-4-large (document model)
        print("\n[3/3] Embedding memory text with voyage-4-large (document model)...")
        memory_embedding_large = await _embed_texts(
            [memory_content],
            model=settings.embedding_document_model,
            input_type="document",
            max_tokens=120_000,
        )
        memory_vec_large = memory_embedding_large[0]
        print(f"  Memory embedding dimension: {len(memory_vec_large)}")

        # Calculate similarities
        print("\n" + "=" * 70)
        print("RESULTS")
        print("=" * 70)

        # Same-model similarity
        same_model_sim = cosine_similarity(query_vec_lite, memory_vec_lite)
        print("\n1. SAME-MODEL SIMILARITY (baseline)")
        print("   Query (voyage-4-lite) vs Memory (voyage-4-lite)")
        print(f"   Similarity: {same_model_sim:.4f}")

        # Cross-model similarity: query-lite vs stored-large
        cross_model_sim = cosine_similarity(query_vec_lite, stored_embedding)
        print("\n2. CROSS-MODEL SIMILARITY (actual retrieval)")
        print("   Query (voyage-4-lite) vs Stored Memory (voyage-4-large)")
        print(f"   Similarity: {cross_model_sim:.4f}")

        # Fresh cross-model similarity
        fresh_cross_sim = cosine_similarity(query_vec_lite, memory_vec_large)
        print("\n3. FRESH CROSS-MODEL SIMILARITY")
        print("   Query (voyage-4-lite) vs Fresh Memory (voyage-4-large)")
        print(f"   Similarity: {fresh_cross_sim:.4f}")

        # Analysis
        print("\n" + "=" * 70)
        print("ANALYSIS")
        print("=" * 70)

        print("\nExpected behavior:")
        print("  - Same-model similarity should be HIGH (0.70+) for semantically related texts")
        print("  - Cross-model similarity should be similar to same-model if models are aligned")
        print("  - Large gap (>0.15) suggests embedding space misalignment")

        print("\nGap analysis:")
        gap_same_vs_cross = abs(same_model_sim - cross_model_sim)
        gap_same_vs_fresh = abs(same_model_sim - fresh_cross_sim)

        print(f"  - Same vs Cross (stored):  {gap_same_vs_cross:.4f}")
        print(f"  - Same vs Cross (fresh):   {gap_same_vs_fresh:.4f}")

        if same_model_sim < 0.5:
            print(
                "\n  ⚠️  LOW same-model similarity suggests query/memory are not semantically related"
            )
            print("      Consider using a different test memory/query pair")

        if gap_same_vs_cross > 0.15 or gap_same_vs_fresh > 0.15:
            print("\n  ❌ LARGE GAP detected - asymmetric embedding may be misaligned!")
            print("      The query and document models may not share compatible embedding spaces")
        else:
            print(
                "\n  ✅ Similarity scores are aligned - asymmetric embedding appears to work correctly"
            )

        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"""
Configuration:
  - Document model: {settings.embedding_document_model}
  - Query model:    {settings.embedding_query_model}
  
Similarity Scores:
  - Same-model (query-lite vs memory-lite):     {same_model_sim:.4f}
  - Cross-model (query-lite vs stored-large):   {cross_model_sim:.4f}
  - Cross-model (query-lite vs fresh-large):    {fresh_cross_sim:.4f}
  
Conclusion:
  {"ASYMETRIC EMBEDDING MISALIGNED" if (gap_same_vs_cross > 0.15 or gap_same_vs_fresh > 0.15) and same_model_sim >= 0.5 else "ASYMETRIC EMBEDDING WORKING" if same_model_sim >= 0.5 else "INCONCLUSIVE - low same-model similarity"}
""")

    finally:
        await db_pool.close()


if __name__ == "__main__":
    asyncio.run(run_alignment_test())
