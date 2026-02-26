from __future__ import annotations

# pyright: reportMissingImports=false

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.embedding import DEFAULT_MODEL, embed_text


def is_explicit_memory(memory: dict[str, Any]) -> bool:
    """Check if a memory was created explicitly (user_created) vs extracted."""
    return memory.get("source_type") == "user_created"

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
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            rows = await self._pool.fetch(
                """
                SELECT * FROM conversations
                WHERE user_id = $1
                  AND COALESCE(title, '') ILIKE $2
                ORDER BY pinned DESC, updated_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_id,
                pattern,
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM conversations
                WHERE user_id = $1
                ORDER BY pinned DESC, updated_at DESC
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
        pinned: bool | None = None,
        title_locked: bool | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            UPDATE conversations
            SET title            = COALESCE($2, title),
                summary          = COALESCE($3, summary),
                summary_updated_at = CASE
                    WHEN $3 IS NOT NULL THEN NOW()
                    ELSE summary_updated_at
                END,
                message_count    = message_count + $4,
                tokens_total     = tokens_total + $5,
                pinned           = COALESCE($6, pinned),
                title_locked     = COALESCE($7, title_locked),
                metadata         = CASE
                    WHEN $8::jsonb IS NULL THEN metadata
                    ELSE COALESCE(metadata, '{}'::jsonb) || $8::jsonb
                END,
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
            pinned,
            title_locked,
            json.dumps(metadata_patch) if metadata_patch is not None else None,
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
        status: str = "streaming",
        metadata: dict[str, Any] | None = None,
        reasoning_text: str | None = None,
        reasoning_duration_secs: int | None = None,
        reasoning_model: str | None = None,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        encrypted_reasoning_text = (
            self._enc.encrypt(reasoning_text) if reasoning_text is not None else None
        )
        row = await self._pool.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, user_id, role, content, model,
                 tokens_in, tokens_out, tool_calls, tool_results, status, metadata,
                 reasoning_text, reasoning_duration_secs, reasoning_model)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11::jsonb, $12, $13, $14)
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
            status,
            json.dumps(metadata or {}),
            encrypted_reasoning_text,
            reasoning_duration_secs,
            reasoning_model,
        )
        result = dict(row)  # type: ignore[arg-type]
        result["content"] = self._enc.decrypt(result["content"])
        if result.get("reasoning_text") is not None:
            result["reasoning_text"] = self._enc.decrypt(result["reasoning_text"])
        return result

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE conversation_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
            ) sub
            ORDER BY created_at ASC
            """,
            conversation_id,
            limit,
            offset,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            if d.get("reasoning_text") is not None:
                d["reasoning_text"] = self._enc.decrypt(d["reasoning_text"])
            results.append(_normalize_message(d))
        return results

    async def count_messages(self, conversation_id: uuid.UUID) -> int:
        """Count messages in a conversation without loading content."""
        row = await self._pool.fetchrow(
            "SELECT COUNT(*) as count FROM messages WHERE conversation_id = $1",
            conversation_id,
        )
        return row["count"] if row else 0

    async def update_message(
        self,
        message_id: uuid.UUID,
        *,
        content: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        reasoning_text: str | None = None,
        reasoning_duration_secs: int | None = None,
        reasoning_model: str | None = None,
    ) -> dict[str, Any] | None:
        encrypted_content = self._enc.encrypt(content) if content is not None else None
        metadata_json = json.dumps(metadata) if metadata is not None else None
        encrypted_reasoning_text = (
            self._enc.encrypt(reasoning_text) if reasoning_text is not None else None
        )
        row = await self._pool.fetchrow(
            """
            UPDATE messages
            SET content    = COALESCE($2, content),
                status     = COALESCE($3, status),
                metadata   = COALESCE($4::jsonb, metadata),
                reasoning_text = COALESCE($5, reasoning_text),
                reasoning_duration_secs = COALESCE($6, reasoning_duration_secs),
                reasoning_model = COALESCE($7, reasoning_model)
            WHERE id = $1
            RETURNING *
            """,
            message_id,
            encrypted_content,
            status,
            metadata_json,
            encrypted_reasoning_text,
            reasoning_duration_secs,
            reasoning_model,
        )
        if not row:
            return None
        result = dict(row)
        result["content"] = self._enc.decrypt(result["content"])
        if result.get("reasoning_text") is not None:
            result["reasoning_text"] = self._enc.decrypt(result["reasoning_text"])
        return result

    async def get_recent_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 20,
        exclude_status: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE conversation_id = $1
                  AND ($3::text[] IS NULL OR status IS NULL OR status NOT IN (SELECT unnest($3::text[])))
                ORDER BY created_at DESC
                LIMIT $2
            ) sub
            ORDER BY created_at ASC
            """,
            conversation_id,
            limit,
            exclude_status,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            if d.get("reasoning_text") is not None:
                d["reasoning_text"] = self._enc.decrypt(d["reasoning_text"])
            results.append(_normalize_message(d))
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
        embedding_model: str = DEFAULT_MODEL,
        source_conversation_id: uuid.UUID | None = None,
        local_only: bool = False,
        confidence: float = 1.0,
        status: str = "active",
        memory_slot: str | None = None,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        embedding_str = _format_vector(embedding) if embedding else None
        row = await self._pool.fetchrow(
            """
            INSERT INTO memories
                (user_id, content, embedding, embedding_model, category, source_type,
                 source_conversation_id, local_only, confidence, status, memory_slot)
            VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING *
            """,
            user_id,
            encrypted_content,
            embedding_str,
            embedding_model,
            category,
            source_type,
            source_conversation_id,
            local_only,
            confidence,
            status,
            memory_slot,
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
        status: str | list[str] | None = "active",
        confirmed: bool | None = None,
        include_local: bool = True,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = ["user_id = $1", "($2::bool OR local_only = FALSE)"]
        params: list[Any] = [user_id, include_local]

        if category is not None:
            params.append(category)
            conditions.append(f"category = ${len(params)}")
        if created_after is not None:
            params.append(created_after)
            conditions.append(f"created_at >= ${len(params)}::timestamptz")
        if created_before is not None:
            params.append(created_before)
            conditions.append(f"created_at <= ${len(params)}::timestamptz")

        if confirmed is True:
            conditions.append("valid_to IS NULL")
        elif confirmed is False:
            pending_statuses = ["pending", "rejected", "inactive"]
            params.append(pending_statuses)
            conditions.append(
                f"(valid_to IS NOT NULL OR status = ANY(${len(params)}::text[]))"
            )
        else:
            if status is None:
                status_list = None
            elif isinstance(status, str):
                status_list = [status]
            else:
                status_list = status
            if status_list is not None:
                params.append(status_list)
                conditions.append(f"status = ANY(${len(params)}::text[])")

        params.extend([limit, offset])
        query = f"""
            SELECT * FROM memories
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """
        rows = await self._pool.fetch(query, *params)
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

    async def update_memory_embedding(
        self,
        memory_id: uuid.UUID,
        embedding: list[float],
        *,
        embedding_model: str = DEFAULT_MODEL,
    ) -> bool:
        embedding_str = _format_vector(embedding)
        result = await self._pool.execute(
            """
            UPDATE memories
            SET embedding = $2::vector,
                embedding_model = $3,
                updated_at = NOW()
            WHERE id = $1
            """,
            memory_id,
            embedding_str,
            embedding_model,
        )
        return result == "UPDATE 1"

    async def update_memory(
        self,
        memory_id: uuid.UUID,
        *,
        content: str,
        embedding: list[float] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any] | None:
        return await self.update_memory_content(
            memory_id,
            content,
            embedding=embedding,
            confidence=confidence,
        )

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

    async def confirm_memory(self, memory_id: uuid.UUID, *, confirmed: bool) -> bool:
        status = "active" if confirmed else "rejected"
        return await self.update_memory_status(memory_id, status)

    async def supersede_memory(
        self,
        old_memory_id: uuid.UUID,
        new_content: str,
        new_category: str,
        new_source_type: str,
        user_id: uuid.UUID,
        *,
        embedding: list[float] | None = None,
        embedding_model: str = DEFAULT_MODEL,
        source_conversation_id: uuid.UUID | None = None,
        confidence: float = 1.0,
        new_status: str = "active",
        memory_slot: str | None = None,
    ) -> dict[str, Any]:
        """Create a new memory and mark the old one as superseded (transaction)."""
        encrypted_content = self._enc.encrypt(new_content)
        embedding_str = _format_vector(embedding) if embedding else None

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO memories
                        (user_id, content, embedding, embedding_model, category, source_type,
                         source_conversation_id, confidence, status, memory_slot)
                    VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING *
                    """,
                    user_id,
                    encrypted_content,
                    embedding_str,
                    embedding_model,
                    new_category,
                    new_source_type,
                    source_conversation_id,
                    confidence,
                    new_status,
                    memory_slot,
                )

                update_result = await conn.execute(
                    """
                    UPDATE memories
                    SET valid_to = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                      AND user_id = $2
                      AND valid_to IS NULL
                    """,
                    old_memory_id,
                    user_id,
                )
                if update_result != "UPDATE 1":
                    raise RuntimeError(
                        "Supersede failed to close source memory in active state"
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

    async def close_memory(self, memory_id: uuid.UUID) -> bool:
        exists = await self._pool.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM memories
                WHERE id = $1
            )
            """,
            memory_id,
        )
        if not bool(exists):
            return False

        await self._pool.execute(
            """
            UPDATE memories
            SET valid_to = NOW(),
                updated_at = NOW()
            WHERE id = $1
              AND valid_to IS NULL
            """,
            memory_id,
        )
        return True

    async def delete_memory(self, memory_id: uuid.UUID, *, soft: bool = True) -> bool:
        if soft:
            result = await self._pool.execute(
                """
                UPDATE memories
                SET status = 'deleted',
                    valid_to = COALESCE(valid_to, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                """,
                memory_id,
            )
            return result == "UPDATE 1"

        result = await self._pool.execute(
            "DELETE FROM memories WHERE id = $1",
            memory_id,
        )
        return result == "DELETE 1"

    async def search_memories(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        *,
        limit: int = 10,
        min_similarity: float = 0.0,
        category: str | None = None,
        include_local: bool = False,
        include_historical: bool = False,
        memory_slot: str | None = None,
    ) -> list[dict[str, Any]]:
        embedding_str = _format_vector(query_embedding)

        if category:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND ($4::bool OR valid_to IS NULL)
                  AND ($5::bool OR local_only = FALSE)
                  AND embedding IS NOT NULL
                  AND category = $6
                  AND ($8::text IS NULL OR memory_slot = $8)
                  AND 1 - (embedding <=> $2::vector) >= $3
                ORDER BY embedding <=> $2::vector
                LIMIT $7
                """,
                user_id,
                embedding_str,
                min_similarity,
                include_historical,
                include_local,
                category,
                limit,
                memory_slot,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND ($4::bool OR valid_to IS NULL)
                  AND ($5::bool OR local_only = FALSE)
                  AND embedding IS NOT NULL
                  AND ($7::text IS NULL OR memory_slot = $7)
                  AND 1 - (embedding <=> $2::vector) >= $3
                ORDER BY embedding <=> $2::vector
                LIMIT $6
                """,
                user_id,
                embedding_str,
                min_similarity,
                include_historical,
                include_local,
                limit,
                memory_slot,
            )

        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def search_memories_by_source(
        self,
        user_id: uuid.UUID,
        text: str,
        min_similarity: float = 0.0,
        category: str | None = None,
        memory_slot: str | None = None,
        include_historical: bool = False,
        include_local: bool = True,
        limit: int = 100,
        source_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search memories by semantic similarity, filtered by source_type.

        Args:
            user_id: User ID
            text: Query text to embed and search
            min_similarity: Minimum similarity threshold
            category: Optional category filter
            memory_slot: Optional memory slot filter
            include_historical: Include historical (expired) memories
            include_local: Include local-only memories
            limit: Maximum results to return
            source_types: Optional list of source_type values to filter by

        Returns:
            List of memory dicts filtered by source_type
        """
        # Embed the text query
        embedding = await embed_text(text)
        # Call search_memories with the same params
        results = await self.search_memories(
            user_id,
            embedding,
            limit=limit,
            min_similarity=min_similarity,
            category=category,
            include_local=include_local,
            include_historical=include_historical,
            memory_slot=memory_slot,
        )
        # Filter by source_types if provided
        if source_types:
            results = [r for r in results if r.get("source_type") in source_types]
        return results

    async def delete_memories_by_source(
        self,
        source_conversation_id: uuid.UUID,
    ) -> int:
        result = await self._pool.execute(
            """
            UPDATE memories
            SET status = 'deleted',
                valid_to = COALESCE(valid_to, NOW()),
                updated_at = NOW()
            WHERE source_conversation_id = $1
              AND status != 'deleted'
            """,
            source_conversation_id,
        )
        return int(result.split()[-1])

    async def delete_all_memories(
        self,
        user_id: uuid.UUID,
        *,
        hard: bool = False,
    ) -> int:
        """Delete all memories for a user.

        soft (default): sets status='deleted' and relies on GC cleanup
        hard: permanent removal from the DB
        """
        if hard:
            result = await self._pool.execute(
                "DELETE FROM memories WHERE user_id = $1",
                user_id,
            )
            return int(result.split()[-1])

        result = await self._pool.execute(
            """
            UPDATE memories
            SET status = 'deleted',
                valid_to = COALESCE(valid_to, NOW()),
                updated_at = NOW()
            WHERE user_id = $1 AND status != 'deleted'
            """,
            user_id,
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
              AND valid_to IS NULL
              AND status != 'deleted'
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
            embedding_model = mem.get("embedding_model", DEFAULT_MODEL)
            status = mem.get("status", "active")
            memory_slot = mem.get("memory_slot")
            await self._pool.execute(
                """
                INSERT INTO memories
                    (user_id, content, embedding, embedding_model, category, source_type,
                     local_only, confidence, status, memory_slot)
                VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10)
                """,
                user_id,
                encrypted_content,
                embedding_str,
                embedding_model,
                mem.get("category", "fact"),
                mem.get("source_type", "import"),
                mem.get("local_only", False),
                mem.get("confidence", 1.0),
                status,
                memory_slot,
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

    async def get_user_settings(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Get user settings from database.

        Returns empty dict if user has no settings or doesn't exist.
        """
        if not self._pool:
            return {}

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT settings FROM users WHERE id = $1", user_id
            )
            if row and row["settings"]:
                return dict(row["settings"])
            return {}

    async def update_user_settings(
        self,
        user_id: uuid.UUID,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._pool:
            return settings

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET settings = $2::jsonb, updated_at = NOW()
                WHERE id = $1
                RETURNING settings
                """,
                user_id,
                json.dumps(settings),
            )
            if not row:
                return settings
            return dict(row["settings"])


async def load_bootstrap_memories(
    store: MemoryStore,
    bootstrap_file: str = "orchestrator/bootstrap_memories.json",
) -> dict[str, Any]:
    """Load bootstrap memories from JSON file into the memory store.

    Bootstrap memories are pre-defined project context that initializes
    the memory system with essential project information.

    Returns stats dict with count of loaded memories.
    """
    import json
    from pathlib import Path

    stats = {"loaded": 0, "errors": 0}

    try:
        bootstrap_path = Path(bootstrap_file)
        if not bootstrap_path.exists():
            logger.warning(f"Bootstrap file not found: {bootstrap_file}")
            return stats

        with open(bootstrap_path) as f:
            data = json.load(f)

        default_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

        for memory in data.get("project_memories", []):
            try:
                source_type = memory.get("source_type", "bootstrapped")
                if source_type == "bootstrap":
                    source_type = "bootstrapped"

                await store.insert_memory(
                    user_id=default_user_id,
                    content=memory["content"],
                    category=memory.get("category", "project"),
                    source_type=source_type,
                )
                stats["loaded"] += 1
            except Exception as e:
                logger.error(f"Failed to load bootstrap memory: {e}")
                stats["errors"] += 1

        return stats
    except Exception as e:
        logger.error(f"Failed to load bootstrap memories: {e}")
        stats["errors"] += 1
        return stats


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize message fields that may be JSON strings from the database.

    Handles tool_calls, tool_results, and metadata which may be returned as:
    - JSON strings (e.g., "[]", "{}")
    - Proper lists/dicts
    - None

    Returns normalized dict with proper types.
    """

    def _coerce_json_value(
        value: Any,
        *,
        default: Any,
        expected_type: type[Any],
    ) -> Any:
        if value is None:
            return default

        if isinstance(value, expected_type):
            return value

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return default
            return parsed if isinstance(parsed, expected_type) else default

        return default

    message["tool_calls"] = _coerce_json_value(
        message.get("tool_calls"),
        default=[],
        expected_type=list,
    )
    message["tool_results"] = _coerce_json_value(
        message.get("tool_results"),
        default=[],
        expected_type=list,
    )
    message["metadata"] = _coerce_json_value(
        message.get("metadata"),
        default={},
        expected_type=dict,
    )

    return message


def _format_vector(embedding: list[float]) -> str:
    return "[" + ",".join(str(f) for f in embedding) + "]"
