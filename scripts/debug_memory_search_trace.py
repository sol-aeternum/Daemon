from __future__ import annotations

# pyright: reportMissingImports=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportImplicitStringConcatenation=false

import asyncio
import uuid

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.retrieval import (
    MIN_FINAL_SCORE,
    _access_boost,
    _as_float,
    _days_since_accessed,
    _hybrid_score,
    _normalize_bm25_scores,
    _recency_score,
    _source_boost,
)
from orchestrator.memory.store import MemoryStore


USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
QUERY = "Where did I buy my new tennis racket from?"
VECTOR_LIMIT = 10


def _short_ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(row.get("id")) for row in rows if row.get("id")]


def _snippet(text: str, length: int = 100) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:length] + ("..." if len(cleaned) > length else "")


async def main() -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    if not settings.daemon_encryption_key:
        raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=4)
    encryption = ContentEncryption(settings.daemon_encryption_key)
    store = MemoryStore(db_pool=pool, encryption=encryption)

    try:
        query_embedding = await embed_query(QUERY)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        async with pool.acquire() as conn:
            user_memory_total = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE user_id = $1",
                USER_ID,
            )
            user_memory_active = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE user_id = $1 AND status <> 'deleted' AND valid_to IS NULL",
                USER_ID,
            )
            user_tier_counts = await conn.fetch(
                "SELECT COALESCE(tier, '<null>') AS tier_label, count(*) AS memory_count FROM memories WHERE user_id = $1 GROUP BY 1 ORDER BY 2 DESC",
                USER_ID,
            )
            user_embedding_stats = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding, count(*) FILTER (WHERE embedding IS NULL) AS without_embedding, count(*) FILTER (WHERE content_tsv IS NOT NULL) AS with_tsv FROM memories WHERE user_id = $1",
                USER_ID,
            )
            user_recent_sample = await conn.fetch(
                "SELECT id, tier, status, valid_to, local_only, content_tsv::text AS content_tsv FROM memories WHERE user_id = $1 ORDER BY created_at DESC LIMIT 5",
                USER_ID,
            )
            all_memory_total = await conn.fetchval("SELECT count(*) FROM memories")
            all_user_count = await conn.fetchval("SELECT count(DISTINCT user_id) FROM memories")
            vector_prefilter_count = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE user_id = $1 AND status != 'deleted' AND tier != 'l0' AND valid_to IS NULL AND local_only = FALSE AND embedding IS NOT NULL",
                USER_ID,
            )
            vector_distance_probe = await conn.fetchrow(
                "SELECT id, embedding <=> $2::vector AS distance, 1 - (embedding <=> $2::vector) AS similarity FROM memories WHERE user_id = $1 AND embedding IS NOT NULL LIMIT 1",
                USER_ID,
                embedding_str,
            )
            raw_vector_rows = await conn.fetch(
                """
                SELECT id, status, tier, valid_to, local_only,
                       1 - (embedding <=> $2::vector) AS similarity,
                       left(content::text, 80) AS encrypted_prefix
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND tier != 'l0'
                  AND valid_to IS NULL
                  AND local_only = FALSE
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $2::vector) >= 0
                ORDER BY embedding <=> $2::vector
                LIMIT $3
                """,
                USER_ID,
                embedding_str,
                VECTOR_LIMIT,
            )
            await conn.execute("SET enable_indexscan = off")
            await conn.execute("SET enable_bitmapscan = off")
            exact_vector_rows = await conn.fetch(
                """
                SELECT id, status, tier, valid_to, local_only,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND tier != 'l0'
                  AND valid_to IS NULL
                  AND local_only = FALSE
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $2::vector) >= 0
                ORDER BY embedding <=> $2::vector
                LIMIT $3
                """,
                USER_ID,
                embedding_str,
                VECTOR_LIMIT,
            )
            await conn.execute("RESET enable_indexscan")
            await conn.execute("RESET enable_bitmapscan")
            unrestricted_vector_rows = await conn.fetch(
                """
                SELECT id, status, tier, valid_to, local_only,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND tier != 'l0'
                  AND valid_to IS NULL
                  AND local_only = FALSE
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $2::vector
                LIMIT $3
                """,
                USER_ID,
                embedding_str,
                VECTOR_LIMIT,
            )

            tsquery = await conn.fetchval(
                "SELECT plainto_tsquery('english', $1)::text",
                QUERY,
            )
            raw_bm25_rows = await conn.fetch(
                """
                SELECT id, status, tier, valid_to, local_only,
                       ts_rank(content_tsv, plainto_tsquery('english', $2)) AS bm25_score,
                       content_tsv::text AS content_tsv
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND valid_to IS NULL
                  AND local_only = FALSE
                  AND content_tsv IS NOT NULL
                  AND content_tsv @@ plainto_tsquery('english', $2)
                ORDER BY bm25_score DESC
                LIMIT $3
                """,
                USER_ID,
                QUERY,
                VECTOR_LIMIT,
            )
            cross_user_vector_rows = await conn.fetch(
                """
                SELECT user_id, id, status, tier, valid_to, local_only,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM memories
                WHERE status != 'deleted'
                  AND tier != 'l0'
                  AND valid_to IS NULL
                  AND local_only = FALSE
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $1::vector) >= 0
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str,
                VECTOR_LIMIT,
            )

        vector_results = await store.search_memories(
            user_id=USER_ID,
            query_embedding=query_embedding,
            limit=VECTOR_LIMIT,
        )
        bm25_results = await store.search_memories_bm25(
            user_id=USER_ID,
            query=QUERY,
            limit=VECTOR_LIMIT,
        )

        print(f"Query: {QUERY}")
        print(f"User ID: {USER_ID}")
        print(
            f"Memory inventory: user_total={user_memory_total} user_active={user_memory_active} all_memories={all_memory_total} distinct_users={all_user_count}"
        )
        print(
            "Tier distribution: "
            + str([(row["tier_label"], row["memory_count"]) for row in user_tier_counts])
        )
        print(
            f"Embedding stats: with_embedding={user_embedding_stats['with_embedding']} without_embedding={user_embedding_stats['without_embedding']} with_tsv={user_embedding_stats['with_tsv']}"
        )
        print(f"Vector prefilter count: {vector_prefilter_count}")
        if vector_distance_probe:
            print(
                f"Vector probe: id={vector_distance_probe['id']} distance={float(vector_distance_probe['distance'] or 0):.6f} similarity={float(vector_distance_probe['similarity'] or 0):.6f}"
            )
        print("Recent memory sample:")
        for row in user_recent_sample:
            print(
                f"  id={row['id']} tier={row['tier']} status={row['status']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
            print(f"     content_tsv={row['content_tsv']}")
        print()

        print(f"Vector search returned: {len(vector_results)} memories")
        print(f"  IDs: {_short_ids(vector_results)}")
        print("Raw SQL vector rows:")
        for idx, row in enumerate(raw_vector_rows, start=1):
            print(
                f"  {idx}. id={row['id']} similarity={float(row['similarity'] or 0):.6f} "
                f"status={row['status']} tier={row['tier']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
        print("Exact vector rows with index scans disabled:")
        for idx, row in enumerate(exact_vector_rows, start=1):
            print(
                f"  {idx}. id={row['id']} similarity={float(row['similarity'] or 0):.6f} "
                f"status={row['status']} tier={row['tier']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
        for idx, row in enumerate(vector_results, start=1):
            print(
                f"  store[{idx}] id={row['id']} similarity={float(row.get('similarity') or 0):.6f} "
                f"content={_snippet(str(row.get('content') or ''))}"
            )
        print("Unrestricted nearest vector rows (no similarity floor):")
        for idx, row in enumerate(unrestricted_vector_rows, start=1):
            print(
                f"  {idx}. id={row['id']} similarity={float(row['similarity'] or 0):.6f} "
                f"status={row['status']} tier={row['tier']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
        print()

        print(f"BM25 search returned: {len(bm25_results)} memories")
        print(f"  IDs: {_short_ids(bm25_results)}")
        print(f"  plainto_tsquery: {tsquery}")
        print("Raw SQL BM25 rows:")
        for idx, row in enumerate(raw_bm25_rows, start=1):
            print(
                f"  {idx}. id={row['id']} bm25_score={float(row['bm25_score'] or 0):.6f} "
                f"status={row['status']} tier={row['tier']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
            print(f"     content_tsv={row['content_tsv']}")
        for idx, row in enumerate(bm25_results, start=1):
            print(
                f"  store[{idx}] id={row['id']} bm25_score={float(row.get('bm25_score') or 0):.6f} "
                f"content={_snippet(str(row.get('content') or ''))}"
            )
        print()

        print("Cross-user nearest vector rows (sanity check):")
        for idx, row in enumerate(cross_user_vector_rows, start=1):
            print(
                f"  {idx}. user_id={row['user_id']} id={row['id']} similarity={float(row['similarity'] or 0):.6f} "
                f"status={row['status']} tier={row['tier']} valid_to={row['valid_to']} local_only={row['local_only']}"
            )
        print()

        candidate_map: dict[str, dict[str, object]] = {}
        for row in vector_results:
            memory_id = str(row.get("id") or "")
            if not memory_id:
                continue
            entry = dict(row)
            entry["vector_sim"] = _as_float(entry.get("similarity"), 0.0)
            entry["bm25_score"] = 0.0
            entry["bm25_normalized"] = 0.0
            entry["source"] = "vector"
            candidate_map[memory_id] = entry

        for row in bm25_results:
            memory_id = str(row.get("id") or "")
            if not memory_id:
                continue
            if memory_id in candidate_map:
                candidate_map[memory_id]["bm25_score"] = _as_float(row.get("bm25_score"), 0.0)
                candidate_map[memory_id]["source"] = "hybrid"
            else:
                entry = dict(row)
                entry["vector_sim"] = 0.0
                entry["similarity"] = 0.0
                entry["source"] = "bm25"
                candidate_map[memory_id] = entry

        all_candidates = list(candidate_map.values())
        print(f"Hybrid merge: {len(all_candidates)} unique memories before scoring")
        print(f"  IDs: {_short_ids(all_candidates)}")
        if not all_candidates:
            return

        _normalize_bm25_scores(all_candidates)
        print(f"  MIN_FINAL_SCORE={MIN_FINAL_SCORE}")
        print("Hybrid score details:")
        for idx, row in enumerate(all_candidates, start=1):
            vector_sim = _as_float(row.get("vector_sim"), 0.0)
            bm25_normalized = _as_float(row.get("bm25_normalized"), 0.0)
            recency_days = _days_since_accessed(row)
            recency_boost = _recency_score(recency_days)
            source_boost = _source_boost(row)
            access_boost = _access_boost(row)
            confidence = _as_float(row.get("confidence"), 1.0)
            final_score = _hybrid_score(
                vector_sim,
                bm25_normalized,
                recency_boost * source_boost * access_boost,
                confidence,
            )
            print(
                f"  {idx}. id={row['id']} source={row.get('source')} vector_sim={vector_sim:.6f} "
                f"bm25_score={_as_float(row.get('bm25_score'), 0.0):.6f} bm25_norm={bm25_normalized:.6f} "
                f"recency_days={recency_days:.2f} recency_boost={recency_boost:.3f} "
                f"source_boost={source_boost:.3f} access_boost={access_boost:.3f} confidence={confidence:.3f} "
                f"final_score={final_score:.6f} passes={final_score >= MIN_FINAL_SCORE}"
            )
            print(f"     content={_snippet(str(row.get('content') or ''))}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
