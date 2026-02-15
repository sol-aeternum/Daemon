from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from orchestrator.memory.encryption import ContentEncryption

logger = logging.getLogger(__name__)


class MemoryStore:
    """Central data-access layer for the Daemon memory system.

    All content fields (messages.content, memories.content,
    extraction_log.input_snippet) are encrypted/decrypted transparently
    through the ``ContentEncryption`` helper.  Embeddings are stored as
    plaintext vectors so pgvector can index and search them.
    """

    def __init__(self, db_pool: asyncpg.Pool, encryption: ContentEncryption) -> None:
        self._pool = db_pool
        self._enc = encryption

    # ------------------------------------------------------------------
    # Conversation operations
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        user_id: uuid.UUID,
        pipeline: str = "cloud",
        title: str | None = None,
    ) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            """
            INSERT INTO conversations (user_id, pipeline, title)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            user_id,
            pipeline,
            title,
        )
        return dict(row)  # type: ignore[arg-type]

    async def get_conversation(
        self, conversation_id: uuid.UUID
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM conversations WHERE id = $1",
            conversation_id,
        )
        return dict(row) if row else None

    async def list_conversations(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM conversations
            WHERE user_id = $1
            ORDER BY last_activity_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]

    async def update_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        title: str | None = None,
        summary: str | None = None,
        message_count_delta: int = 0,
        tokens_delta: int = 0,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            UPDATE conversations
            SET title            = COALESCE($2, title),
                summary          = COALESCE($3, summary),
                message_count    = message_count + $4,
                tokens_total     = tokens_total + $5,
                updated_at       = NOW(),
                last_activity_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            conversation_id,
            title,
            summary,
            message_count_delta,
            tokens_delta,
        )
        return dict(row) if row else None

    async def delete_conversation(self, conversation_id: uuid.UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM conversations WHERE id = $1",
            conversation_id,
        )
        return result == "DELETE 1"

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    async def insert_message(
        self,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        content: str,
        *,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tool_calls: list[Any] | None = None,
        tool_results: list[Any] | None = None,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        row = await self._pool.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, user_id, role, content, model,
                 tokens_in, tokens_out, tool_calls, tool_results)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            RETURNING *
            """,
            conversation_id,
            user_id,
            role,
            encrypted_content,
            model,
            tokens_in,
            tokens_out,
            json.dumps(tool_calls or []),
            json.dumps(tool_results or []),
        )
        result = dict(row)  # type: ignore[arg-type]
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC
            LIMIT $2 OFFSET $3
            """,
            conversation_id,
            limit,
            offset,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def get_recent_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            ) sub
            ORDER BY created_at ASC
            """,
            conversation_id,
            limit,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Memory operations
    # ------------------------------------------------------------------

    async def insert_memory(
        self,
        user_id: uuid.UUID,
        content: str,
        category: str,
        source_type: str,
        *,
        embedding: list[float] | None = None,
        source_conversation_id: uuid.UUID | None = None,
        local_only: bool = False,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        embedding_str = _format_vector(embedding) if embedding else None
        row = await self._pool.fetchrow(
            """
            INSERT INTO memories
                (user_id, content, embedding, category, source_type,
                 source_conversation_id, local_only, confidence)
            VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            user_id,
            encrypted_content,
            embedding_str,
            category,
            source_type,
            source_conversation_id,
            local_only,
            confidence,
        )
        result = dict(row)  # type: ignore[arg-type]
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def get_memory(self, memory_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM memories WHERE id = $1",
            memory_id,
        )
        if not row:
            return None
        result = dict(row)
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def list_memories(
        self,
        user_id: uuid.UUID,
        *,
        category: str | None = None,
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if category:
            rows = await self._pool.fetch(
                """
                SELECT * FROM memories
                WHERE user_id = $1 AND status = $2 AND category = $3
                ORDER BY created_at DESC
                LIMIT $4 OFFSET $5
                """,
                user_id,
                status,
                category,
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM memories
                WHERE user_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_id,
                status,
                limit,
                offset,
            )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def update_memory_content(
        self,
        memory_id: uuid.UUID,
        content: str,
        *,
        embedding: list[float] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any] | None:
        encrypted_content = self._enc.encrypt(content)
        embedding_str = _format_vector(embedding) if embedding else None
        row = await self._pool.fetchrow(
            """
            UPDATE memories
            SET content    = $2,
                embedding  = COALESCE($3::vector, embedding),
                confidence = COALESCE($4, confidence),
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            memory_id,
            encrypted_content,
            embedding_str,
            confidence,
        )
        if not row:
            return None
        result = dict(row)
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def update_memory_status(
        self,
        memory_id: uuid.UUID,
        status: str,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE memories
            SET status = $2, updated_at = NOW()
            WHERE id = $1
            """,
            memory_id,
            status,
        )
        return result == "UPDATE 1"

    async def supersede_memory(
        self,
        old_memory_id: uuid.UUID,
        new_content: str,
        new_category: str,
        new_source_type: str,
        user_id: uuid.UUID,
        *,
        embedding: list[float] | None = None,
        source_conversation_id: uuid.UUID | None = None,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Create a new memory and mark the old one as superseded (transaction)."""
        encrypted_content = self._enc.encrypt(new_content)
        embedding_str = _format_vector(embedding) if embedding else None

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO memories
                        (user_id, content, embedding, category, source_type,
                         source_conversation_id, confidence)
                    VALUES ($1, $2, $3::vector, $4, $5, $6, $7)
                    RETURNING *
                    """,
                    user_id,
                    encrypted_content,
                    embedding_str,
                    new_category,
                    new_source_type,
                    source_conversation_id,
                    confidence,
                )
                new_id = new_row["id"]  # type: ignore[index]

                await conn.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded',
                        superseded_by = $2,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    old_memory_id,
                    new_id,
                )

        result = dict(new_row)  # type: ignore[arg-type]
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def touch_memory(self, memory_id: uuid.UUID) -> None:
        await self._pool.execute(
            """
            UPDATE memories
            SET last_accessed_at = NOW(),
                access_count = access_count + 1
            WHERE id = $1
            """,
            memory_id,
        )

    async def bulk_touch_memories(self, memory_ids: list[uuid.UUID]) -> None:
        if not memory_ids:
            return
        await self._pool.execute(
            """
            UPDATE memories
            SET last_accessed_at = NOW(),
                access_count = access_count + 1
            WHERE id = ANY($1::uuid[])
            """,
            memory_ids,
        )

    async def delete_memory(self, memory_id: uuid.UUID) -> bool:
        result = await self._pool.execute(
            """
            UPDATE memories
            SET status = 'deleted', updated_at = NOW()
            WHERE id = $1
            """,
            memory_id,
        )
        return result == "UPDATE 1"

    async def search_memories(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        *,
        limit: int = 10,
        min_similarity: float = 0.0,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        embedding_str = _format_vector(query_embedding)

        if category:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status = 'active'
                  AND embedding IS NOT NULL
                  AND category = $5
                  AND 1 - (embedding <=> $2::vector) >= $3
                ORDER BY embedding <=> $2::vector
                LIMIT $4
                """,
                user_id,
                embedding_str,
                min_similarity,
                limit,
                category,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status = 'active'
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $2::vector) >= $3
                ORDER BY embedding <=> $2::vector
                LIMIT $4
                """,
                user_id,
                embedding_str,
                min_similarity,
                limit,
            )

        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def delete_memories_by_source(
        self,
        source_conversation_id: uuid.UUID,
    ) -> int:
        result = await self._pool.execute(
            """
            UPDATE memories
            SET status = 'deleted', updated_at = NOW()
            WHERE source_conversation_id = $1 AND status = 'active'
            """,
            source_conversation_id,
        )
        return int(result.split()[-1])

    # ------------------------------------------------------------------
    # Summary operations
    # ------------------------------------------------------------------

    async def get_recent_summaries(
        self,
        user_id: uuid.UUID,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM memories
            WHERE user_id = $1
              AND category = 'summary'
              AND status = 'active'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Extraction log
    # ------------------------------------------------------------------

    async def log_extraction(
        self,
        user_id: uuid.UUID,
        input_snippet: str,
        *,
        conversation_id: uuid.UUID | None = None,
        extracted_facts: list[Any] | None = None,
        dedup_results: dict[str, Any] | None = None,
        model_used: str | None = None,
    ) -> dict[str, Any]:
        encrypted_snippet = self._enc.encrypt(input_snippet)
        row = await self._pool.fetchrow(
            """
            INSERT INTO memory_extraction_log
                (conversation_id, user_id, input_snippet,
                 extracted_facts, dedup_results, model_used)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            RETURNING *
            """,
            conversation_id,
            user_id,
            encrypted_snippet,
            json.dumps(extracted_facts or []),
            json.dumps(dedup_results or {}),
            model_used,
        )
        result = dict(row)  # type: ignore[arg-type]
        result["input_snippet"] = self._enc.decrypt(result["input_snippet"])
        return result

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def export_memories(
        self,
        user_id: uuid.UUID,
        *,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM memories
            WHERE user_id = $1 AND status = $2
            ORDER BY created_at ASC
            """,
            user_id,
            status,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def import_memories(
        self,
        user_id: uuid.UUID,
        memories: list[dict[str, Any]],
    ) -> int:
        """Bulk-import memories. Each dict must have 'content' and 'category'."""
        if not memories:
            return 0

        inserted = 0
        for mem in memories:
            encrypted_content = self._enc.encrypt(mem["content"])
            embedding_str = (
                _format_vector(mem["embedding"]) if mem.get("embedding") else None
            )
            await self._pool.execute(
                """
                INSERT INTO memories
                    (user_id, content, embedding, category, source_type,
                     local_only, confidence)
                VALUES ($1, $2, $3::vector, $4, $5, $6, $7)
                """,
                user_id,
                encrypted_content,
                embedding_str,
                mem["category"],
                mem.get("source_type", "import"),
                mem.get("local_only", False),
                mem.get("confidence", 1.0),
            )
            inserted += 1
        return inserted

    async def count_memories(
        self,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> int:
        if status:
            row = await self._pool.fetchval(
                """
                SELECT COUNT(*) FROM memories
                WHERE user_id = $1 AND status = $2
                """,
                user_id,
                status,
            )
        else:
            row = await self._pool.fetchval(
                "SELECT COUNT(*) FROM memories WHERE user_id = $1",
                user_id,
            )
        return int(row)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _format_vector(embedding: list[float]) -> str:
    return "[" + ",".join(str(f) for f in embedding) + "]"
