#!/usr/bin/env python3
"""Test retrieval quality by querying memories and checking similarity scores."""

import asyncio
import os
import sys
import uuid

# Add orchestrator to path
sys.path.insert(0, "/home/sol/daemon")

import asyncpg
from orchestrator.config import get_settings
from orchestrator.database_url import resolve_database_url
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.encryption import ContentEncryption


TEST_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


async def main():
    """Run diagnostic retrieval test."""
    # Get connection settings from environment
    db_url = resolve_database_url(get_settings().database_url)
    if not db_url:
        raise RuntimeError("DATABASE_URL or complete POSTGRES_* settings are required")
    encryption_key = os.getenv("ENCRYPTION_KEY", "test-key-for-development-only")

    print("Connecting to database...")
    pool = await asyncpg.create_pool(db_url)

    try:
        encryption = ContentEncryption(encryption_key)
        store = MemoryStore(pool, encryption)

        # Test query
        test_query = "What projects am I working on?"
        print(f"\n{'=' * 60}")
        print(f"Test Query: '{test_query}'")
        print(f"{'=' * 60}\n")

        # Get embedding for query
        print("Generating query embedding...")
        query_embedding = await embed_query(test_query)
        print(f"Embedding length: {len(query_embedding)}\n")

        # Search memories directly
        print("Running vector search...")
        vector_results = await store.search_memories(
            user_id=TEST_USER_ID,
            query_embedding=query_embedding,
            limit=10,
        )
        print(f"Found {len(vector_results)} vector results\n")

        # Print top results with similarity scores
        print("=" * 60)
        print("TOP 10 VECTOR SEARCH RESULTS (by similarity)")
        print("=" * 60)

        for i, memory in enumerate(vector_results[:10], 1):
            similarity = memory.get("similarity", 0.0)
            category = memory.get("category", "unknown")
            content = str(memory.get("content", ""))[:80] + "..."

            print(f"\n{i}. Similarity: {similarity:.4f} | Category: {category}")
            print(f"   Content: {content}")

        # Run hybrid search via retrieve_memories
        print("\n" + "=" * 60)
        print("HYBRID RETRIEVAL RESULTS (final_score)")
        print("=" * 60)

        from orchestrator.memory.retrieval import retrieve_memories

        hybrid_results = await retrieve_memories(
            store=store,
            query_embedding=query_embedding,
            query_text=test_query,
            user_id=TEST_USER_ID,
            limit=5,
        )

        print(f"Found {len(hybrid_results)} hybrid results\n")

        for i, memory in enumerate(hybrid_results, 1):
            vector_sim = memory.get("vector_sim", 0.0)
            bm25_score = memory.get("bm25_normalized", 0.0)
            final_score = memory.get("final_score", 0.0)
            source = memory.get("source", "unknown")
            category = memory.get("category", "unknown")
            content = str(memory.get("content", ""))[:80] + "..."

            print(f"\n{i}. Final Score: {final_score:.4f}")
            print(f"   Vector Sim: {vector_sim:.4f} | BM25: {bm25_score:.4f} | Source: {source}")
            print(f"   Category: {category}")
            print(f"   Content: {content}")

        # Summary statistics
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        if vector_results:
            sims: list[float] = []
            for m in vector_results:
                sim = m.get("similarity", 0.0)
                if isinstance(sim, (int, float)):
                    sims.append(float(sim))
                else:
                    sims.append(0.0)
            print(f"Vector similarity range: {min(sims):.4f} - {max(sims):.4f}")
            print(f"Vector similarity average: {sum(sims) / len(sims):.4f}")
            print(f"Scores > 0.30: {sum(1 for s in sims if s > 0.30)}/{len(sims)}")

            # Check for concerning low scores
            if max(sims) < 0.30:
                print("\n⚠️  WARNING: All similarity scores below 0.30 threshold!")
            elif max(sims) < 0.50:
                print("\n⚠️  NOTE: Best match only at moderate similarity (<0.50)")

        if hybrid_results:
            finals: list[float] = []
            for m in hybrid_results:
                score = m.get("final_score", 0.0)
                if isinstance(score, (int, float)):
                    finals.append(float(score))
                else:
                    finals.append(0.0)
            print(f"\nFinal score range: {min(finals):.4f} - {max(finals):.4f}")
            print(f"Final score average: {sum(finals) / len(finals):.4f}")

        print(
            f"\nTotal memories for user: {len(await store.search_memories(TEST_USER_ID, query_embedding, limit=1000))}"
        )

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
