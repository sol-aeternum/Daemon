#!/usr/bin/env python3

import asyncio
import json
import math
import sys

sys.path.insert(0, "/app")

import asyncpg
from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query

TEST_USER_ID = "12345678-1234-5678-1234-567812345678"


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def get_conversation_for_session(conn, session_id):
    row = await conn.fetchrow(
        """
        SELECT DISTINCT conversation_id 
        FROM messages 
        WHERE metadata->>'session_id' = $1 
        AND user_id = $2::uuid
        LIMIT 1
        """,
        session_id,
        TEST_USER_ID,
    )
    return row["conversation_id"] if row else None


async def get_memories_for_conversation(conn, conversation_id):
    rows = await conn.fetch(
        """
        SELECT id, content, embedding, category, memory_slot
        FROM memories 
        WHERE source_conversation_id = $1 
        AND user_id = $2::uuid
        AND status = 'active'
        """,
        conversation_id,
        TEST_USER_ID,
    )
    return rows


def parse_embedding(embedding):
    if not embedding:
        return None
    if isinstance(embedding, str):
        embedding_str = embedding.strip("[]")
        return [float(x.strip()) for x in embedding_str.split(",") if x.strip()]
    if isinstance(embedding, list):
        return [float(x) for x in embedding]
    return None


async def test_alignment():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)

    with open("/tmp/longmemeval_examples.json", "r") as f:
        examples = json.load(f)

    print("=" * 70)
    print("SESSION-MEMORY ALIGNMENT DIAGNOSTIC")
    print("=" * 70)

    for i, ex in enumerate(examples, 1):
        print(f"\n{'=' * 70}")
        print(f"Example {i}: {ex['id']}")
        print(f"{'=' * 70}")
        print(f"Question: {ex['question']}")
        print(f"Expected Answer: {ex['answer']}")

        answer_session = ex["answer_session"][0] if ex.get("answer_session") else None
        if not answer_session:
            print("  ❌ No answer session specified")
            continue

        print(f"\nAnswer Session: {answer_session}")

        async with pool.acquire() as conn:
            conversation_id = await get_conversation_for_session(conn, answer_session)

        if not conversation_id:
            print(f"  ❌ No conversation found for session {answer_session}")
            continue

        print(f"  → Conversation ID: {conversation_id}")

        async with pool.acquire() as conn:
            rows = await get_memories_for_conversation(conn, conversation_id)

        if not rows:
            print("  ❌ ZERO memories for this session")
            continue

        print(f"  ✓ Found {len(rows)} memories from answer session")

        try:
            question_embedding = await embed_query(ex["question"])
        except Exception as e:
            print(f"  ❌ Failed to embed question: {e}")
            continue

        similarities = []
        for row in rows:
            memory_id = row["id"]
            embedding = parse_embedding(row["embedding"])

            if not embedding:
                continue

            sim = cosine_similarity(question_embedding, embedding)
            similarities.append(
                {
                    "id": memory_id,
                    "similarity": sim,
                    "category": row["category"],
                    "slot": row["memory_slot"],
                }
            )

        if not similarities:
            print("  ❌ No valid embeddings found")
            continue

        similarities.sort(key=lambda x: x["similarity"], reverse=True)

        print("\n  Similarity Results (top 3):")
        best_sim = similarities[0]["similarity"]
        for s in similarities[:3]:
            marker = "⭐ BEST" if s == similarities[0] else ""
            print(f"    - {s['similarity']:.4f} [{s['category']}/{s['slot']}] {marker}")

        print("\n  Analysis:")
        if best_sim >= 0.70:
            print("    ✓ HIGH similarity memory exists (0.70+)")
            print("      → Fact was extracted correctly, retrieval may be failing to find it")
        elif best_sim >= 0.50:
            print("    ⚠ MODERATE similarity (0.50-0.70)")
            print("      → Partial alignment - fact exists but not strongly related to question")
        else:
            print("    ❌ LOW similarity (<0.50)")
            print("      → Extraction captured session content but NOT in a form")
            print("        that's semantically close to how the question is phrased")
            print("      → This is the extraction-to-query alignment gap")

    await pool.close()
    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_alignment())
