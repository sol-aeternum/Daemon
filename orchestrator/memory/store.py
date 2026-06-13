from __future__ import annotations

# pyright: reportMissingImports=false

import json
import logging
import uuid
import hashlib
import hmac
from datetime import datetime
from typing import Any, cast

import asyncpg

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.embedding import embed_query


def is_explicit_memory(memory: dict[str, Any]) -> bool:
    """Check if a memory was created explicitly (user_created) vs extracted."""
    return memory.get("source_type") == "user_created"


logger = logging.getLogger(__name__)


def _default_embedding_model() -> str:
    return get_settings().embedding_document_model


def _normalize_memory_content_for_hash(content: str) -> str:
    return " ".join(content.strip().split())


def compute_memory_content_hash(content: str) -> str:
    pepper = validate_and_get_pepper(get_settings())
    normalized = _normalize_memory_content_for_hash(content)
    return hmac.new(
        pepper.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class MemoryContentConflictError(Exception):
    """Raised when an active memory edit duplicates another active memory."""


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

    def _decrypt_advisor_traces(self, value: Any) -> Any:
        if value is None:
            return None
        try:
            decrypted = self._enc.decrypt(value)
            return json.loads(decrypted)
        except Exception:
            logger.warning("Failed to decrypt advisor_traces", exc_info=True)
            return None

    def _memory_row_to_dict(self, row: Any) -> dict[str, Any]:
        result = cast(dict[str, Any], dict(row))
        result["content"] = self._enc.decrypt(result["content"])
        return result

    async def _get_active_memory_by_content_hash(
        self,
        user_id: uuid.UUID,
        content_hash: str,
        local_only: bool,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT *
            FROM memories
            WHERE user_id = $1
              AND content_hash = $2
              AND local_only = $3
              AND status = 'active'
              AND valid_to IS NULL
            ORDER BY created_at ASC
            LIMIT 1
            """,
            user_id,
            content_hash,
            local_only,
        )
        if row is None:
            return None
        return self._memory_row_to_dict(row)

    async def backfill_memory_content_hashes(self) -> int:
        """Populate content_hash for legacy current memories that predate the column."""
        rows = await self._pool.fetch(
            """
            SELECT id, content
            FROM memories
            WHERE content_hash IS NULL
              AND valid_to IS NULL
            ORDER BY created_at ASC
            """
        )
        backfilled = 0
        for row in rows:
            content = self._enc.decrypt(row["content"])
            content_hash = compute_memory_content_hash(content)
            try:
                result = await self._pool.execute(
                    """
                    UPDATE memories
                    SET content_hash = $2,
                        updated_at = NOW()
                    WHERE id = $1
                      AND content_hash IS NULL
                    """,
                    row["id"],
                    content_hash,
                )
            except asyncpg.UniqueViolationError:
                logger.warning(
                    "Closing duplicate legacy memory after content_hash backfill conflict for memory %s",
                    row["id"],
                    exc_info=True,
                )
                await self._pool.execute(
                    """
                    UPDATE memories
                    SET valid_to = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                      AND content_hash IS NULL
                      AND valid_to IS NULL
                    """,
                    row["id"],
                )
                continue
            if result == "UPDATE 1":
                backfilled += 1
        return backfilled

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

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM conversations WHERE id = $1",
            conversation_id,
        )
        return dict(row) if row else None

    async def get_completed_council_session(
        self,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get the most recent completed council session for a conversation."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, conversation_id, prompt, config, rounds, 
                       audit_findings, token_costs, created_at, updated_at
                FROM council_sessions
                WHERE conversation_id = $1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                conversation_id,
            )
            if row:
                return {
                    "id": str(row["id"]),
                    "conversation_id": str(row["conversation_id"]),
                    "prompt": row["prompt"],
                    "config": row["config"],
                    "rounds": row["rounds"],
                    "audit_findings": row["audit_findings"],
                    "token_costs": row["token_costs"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                }
            return None

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
        last_retrieved_memory_ids: list[uuid.UUID] | None = None,
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
                last_retrieved_memory_ids = COALESCE($9, last_retrieved_memory_ids),
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
            json.dumps([str(m) for m in last_retrieved_memory_ids])
            if last_retrieved_memory_ids
            else None,
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
        advisor_traces: dict[str, Any] | None = None,
        reasoning_text: str | None = None,
        reasoning_duration_secs: int | None = None,
        reasoning_model: str | None = None,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        encrypted_reasoning_text = (
            self._enc.encrypt(reasoning_text) if reasoning_text is not None else None
        )
        encrypted_advisor_traces = (
            self._enc.encrypt(json.dumps(advisor_traces)) if advisor_traces is not None else None
        )
        row = await self._pool.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, user_id, role, content, model,
                 tokens_in, tokens_out, tool_calls, tool_results, status, metadata,
                 reasoning_text, reasoning_duration_secs, reasoning_model, advisor_traces)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11::jsonb, $12, $13, $14, $15)
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
            encrypted_advisor_traces,
        )
        result = cast(dict[str, Any], dict(row))
        result["content"] = self._enc.decrypt(result["content"])
        if result.get("reasoning_text") is not None:
            result["reasoning_text"] = self._enc.decrypt(result["reasoning_text"])
        if result.get("advisor_traces") is not None:
            result["advisor_traces"] = self._decrypt_advisor_traces(result["advisor_traces"])
        return result

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
        created_after: datetime | None = None,
    ) -> list[dict[str, Any]]:
        # Build query with optional created_after filter
        if created_after is not None:
            rows = await self._pool.fetch(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE conversation_id = $1 AND created_at > $4
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                ) sub
                ORDER BY created_at ASC
                """,
                conversation_id,
                limit,
                offset,
                created_after,
            )
        else:
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
            if d.get("advisor_traces") is not None:
                d["advisor_traces"] = self._decrypt_advisor_traces(d["advisor_traces"])
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
        advisor_traces: dict[str, Any] | None = None,
        tool_calls: list[Any] | None = None,
        tool_results: list[Any] | None = None,
        reasoning_text: str | None = None,
        reasoning_duration_secs: int | None = None,
        reasoning_model: str | None = None,
    ) -> dict[str, Any] | None:
        encrypted_content = self._enc.encrypt(content) if content is not None else None
        metadata_json = json.dumps(metadata) if metadata is not None else None
        tool_calls_json = json.dumps(tool_calls) if tool_calls is not None else None
        tool_results_json = json.dumps(tool_results) if tool_results is not None else None
        encrypted_reasoning_text = (
            self._enc.encrypt(reasoning_text) if reasoning_text is not None else None
        )
        encrypted_advisor_traces = (
            self._enc.encrypt(json.dumps(advisor_traces)) if advisor_traces is not None else None
        )
        row = await self._pool.fetchrow(
            """
            UPDATE messages
            SET content    = COALESCE($2, content),
                status     = COALESCE($3, status),
                metadata   = COALESCE($4::jsonb, metadata),
                tool_calls = COALESCE($5::jsonb, tool_calls),
                tool_results = COALESCE($6::jsonb, tool_results),
                reasoning_text = COALESCE($7, reasoning_text),
                reasoning_duration_secs = COALESCE($8, reasoning_duration_secs),
                reasoning_model = COALESCE($9, reasoning_model),
                advisor_traces = COALESCE($10, advisor_traces)
            WHERE id = $1
            RETURNING *
            """,
            message_id,
            encrypted_content,
            status,
            metadata_json,
            tool_calls_json,
            tool_results_json,
            encrypted_reasoning_text,
            reasoning_duration_secs,
            reasoning_model,
            encrypted_advisor_traces,
        )
        if not row:
            return None
        result = dict(row)
        result["content"] = self._enc.decrypt(result["content"])
        if result.get("reasoning_text") is not None:
            result["reasoning_text"] = self._enc.decrypt(result["reasoning_text"])
        if result.get("advisor_traces") is not None:
            result["advisor_traces"] = self._decrypt_advisor_traces(result["advisor_traces"])
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
            if d.get("advisor_traces") is not None:
                d["advisor_traces"] = self._decrypt_advisor_traces(d["advisor_traces"])
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
        embedding_model: str | None = None,
        source_conversation_id: uuid.UUID | None = None,
        local_only: bool = False,
        confidence: float = 1.0,
        status: str = "active",
        memory_slot: str | None = None,
    ) -> dict[str, Any]:
        encrypted_content = self._enc.encrypt(content)
        content_hash = compute_memory_content_hash(content)
        embedding_str = _format_vector(embedding) if embedding else None
        effective_embedding_model = embedding_model or _default_embedding_model()

        async def _insert(
            conversation_id: uuid.UUID | None,
        ) -> asyncpg.Record:
            row = await self._pool.fetchrow(
                """
                INSERT INTO memories
                    (user_id, content, content_hash, content_tsv, embedding, embedding_model,
                     category, source_type, source_conversation_id, local_only, confidence,
                     status, memory_slot)
                VALUES ($1, $2, $3, to_tsvector('english', $13), $4::vector, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING *
                """,
                user_id,
                encrypted_content,
                content_hash,
                embedding_str,
                effective_embedding_model,
                category,
                source_type,
                conversation_id,
                local_only,
                confidence,
                status,
                memory_slot,
                content,  # plaintext for tsvector computation
            )
            if row is None:
                raise RuntimeError("insert_memory: insert returned no row")
            return row

        try:
            try:
                row = await _insert(source_conversation_id)
            except asyncpg.ForeignKeyViolationError as error:
                if source_conversation_id is None:
                    raise
                logger.warning(
                    "insert_memory: source_conversation_id %s missing; retrying without source conversation reference (%s)",
                    source_conversation_id,
                    error,
                )
                row = await _insert(None)
        except asyncpg.UniqueViolationError:
            existing = await self._get_active_memory_by_content_hash(
                user_id,
                content_hash,
                local_only,
            )
            if existing is not None:
                return existing
            raise
        return self._memory_row_to_dict(row)

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
        search: str | None = None,
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

        if search is not None:
            params.append(f"%{search}%")
            conditions.append(f"content ILIKE ${len(params)}")

        if confirmed is True:
            conditions.append("valid_to IS NULL")
        elif confirmed is False:
            pending_statuses = ["pending", "rejected", "inactive"]
            params.append(pending_statuses)
            conditions.append(f"(valid_to IS NOT NULL OR status = ANY(${len(params)}::text[]))")
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
        content_hash = compute_memory_content_hash(content)
        embedding_str = _format_vector(embedding) if embedding else None
        try:
            row = await self._pool.fetchrow(
                """
                UPDATE memories
                SET content    = $2,
                    embedding  = COALESCE($3::vector, embedding),
                    confidence = COALESCE($4, confidence),
                    content_tsv = to_tsvector('english', $5),
                    content_hash = $6,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                memory_id,
                encrypted_content,
                embedding_str,
                confidence,
                content,
                content_hash,
            )
        except asyncpg.UniqueViolationError as exc:
            raise MemoryContentConflictError(
                "Memory content duplicates an existing active memory"
            ) from exc
        if not row:
            return None
        return self._memory_row_to_dict(row)

    async def update_memory_embedding(
        self,
        memory_id: uuid.UUID,
        embedding: list[float],
        *,
        embedding_model: str | None = None,
    ) -> bool:
        embedding_str = _format_vector(embedding)
        effective_embedding_model = embedding_model or _default_embedding_model()
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
            effective_embedding_model,
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
        if status == "active":
            row = await self._pool.fetchrow(
                "SELECT content, content_hash FROM memories WHERE id = $1",
                memory_id,
            )
            if row is None:
                return False
            content_hash = row["content_hash"]
            if content_hash is None:
                content_hash = compute_memory_content_hash(self._enc.decrypt(row["content"]))
            try:
                result = await self._pool.execute(
                    """
                    UPDATE memories
                    SET status = $2,
                        content_hash = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    memory_id,
                    status,
                    content_hash,
                )
            except asyncpg.UniqueViolationError as exc:
                raise MemoryContentConflictError(
                    "Memory content duplicates an existing active memory"
                ) from exc
            return result == "UPDATE 1"

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

    async def update_memory_tier(
        self,
        memory_id: uuid.UUID,
        tier: str,
    ) -> bool:
        """Update the tier of a memory (l0, l1, or l2)."""
        if tier not in ("l0", "l1", "l2"):
            raise ValueError(f"Invalid tier: {tier}. Must be one of: l0, l1, l2")
        result = await self._pool.execute(
            """
            UPDATE memories
            SET tier = $2, updated_at = NOW()
            WHERE id = $1
            """,
            memory_id,
            tier,
        )
        return result == "UPDATE 1"

    async def update_memory_metadata(
        self,
        memory_id: uuid.UUID,
        metadata: dict[str, Any],
    ) -> bool:
        """Update metadata fields on a memory (merged/patched with existing)."""
        result = await self._pool.execute(
            """
            UPDATE memories
            SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
                updated_at = NOW()
            WHERE id = $1
            """,
            memory_id,
            json.dumps(metadata),
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
        embedding_model: str | None = None,
        source_conversation_id: uuid.UUID | None = None,
        confidence: float = 1.0,
        new_status: str = "active",
        memory_slot: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new memory and mark the old one as superseded (transaction)."""
        encrypted_content = self._enc.encrypt(new_content)
        content_hash = compute_memory_content_hash(new_content)
        embedding_str = _format_vector(embedding) if embedding else None
        effective_embedding_model = embedding_model or _default_embedding_model()
        metadata_json = json.dumps(metadata) if metadata is not None else None

        async with self._pool.acquire() as conn:
            async with conn.transaction():

                async def _insert(
                    conversation_id: uuid.UUID | None,
                ) -> asyncpg.Record:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO memories
                            (user_id, content, content_hash, embedding, embedding_model, category,
                             source_type, source_conversation_id, confidence, status, memory_slot,
                             metadata, content_tsv)
                        VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11, $12, to_tsvector('english', $13))
                        RETURNING *
                        """,
                        user_id,
                        encrypted_content,
                        content_hash,
                        embedding_str,
                        effective_embedding_model,
                        new_category,
                        new_source_type,
                        conversation_id,
                        confidence,
                        new_status,
                        memory_slot,
                        metadata_json,
                        new_content,
                    )
                    if row is None:
                        raise RuntimeError("supersede_memory: insert returned no row")
                    return row

                async def _get_active_duplicate() -> asyncpg.Record | None:
                    return await conn.fetchrow(
                        """
                        SELECT *
                        FROM memories
                        WHERE user_id = $1
                          AND content_hash = $2
                          AND local_only = FALSE
                          AND status = 'active'
                          AND valid_to IS NULL
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        user_id,
                        content_hash,
                    )

                try:
                    try:
                        new_row = await _insert(source_conversation_id)
                    except asyncpg.ForeignKeyViolationError as error:
                        if source_conversation_id is None:
                            raise
                        logger.warning(
                            "supersede_memory: source_conversation_id %s missing; retrying without source conversation reference (%s)",
                            source_conversation_id,
                            error,
                        )
                        new_row = await _insert(None)
                except asyncpg.UniqueViolationError:
                    duplicate_row = await _get_active_duplicate()
                    if duplicate_row is None:
                        raise
                    logger.warning(
                        "supersede_memory: recovered existing active memory after content_hash conflict",
                        exc_info=True,
                    )
                    if duplicate_row["id"] == old_memory_id:
                        return self._memory_row_to_dict(duplicate_row)
                    new_row = duplicate_row

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
                    raise RuntimeError("Supersede failed to close source memory in active state")

        return self._memory_row_to_dict(new_row)

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
        include_dream_observations: bool = False,
        source_conversation_ids: list[uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        embedding_str = _format_vector(query_embedding)
        conversation_filter = [str(value) for value in source_conversation_ids or []] or None

        if category:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND tier != 'l0'
                  AND ($4::bool OR valid_to IS NULL)
                  AND ($5::bool OR local_only = FALSE)
                  AND ($9::bool OR source_type != 'dream')
                  AND ($10::uuid[] IS NULL OR source_conversation_id = ANY($10::uuid[]))
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
                include_dream_observations,
                conversation_filter,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND tier != 'l0'
                  AND ($4::bool OR valid_to IS NULL)
                  AND ($5::bool OR local_only = FALSE)
                  AND ($8::bool OR source_type != 'dream')
                  AND ($9::uuid[] IS NULL OR source_conversation_id = ANY($9::uuid[]))
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
                include_dream_observations,
                conversation_filter,
            )

        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

    async def search_memories_bm25(
        self,
        user_id: uuid.UUID,
        query: str,
        *,
        limit: int = 10,
        category: str | None = None,
        include_local: bool = False,
        include_historical: bool = False,
        memory_slot: str | None = None,
        include_dream_observations: bool = False,
        source_conversation_ids: list[uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Search memories using BM25 full-text search.

        Uses PostgreSQL ts_rank on content_tsv with plainto_tsquery for
        natural language query parsing.
        """
        # By design: L0 memories are always injected, but they must remain
        # directly searchable via BM25 for explicit recall queries.
        conversation_filter = [str(value) for value in source_conversation_ids or []] or None
        if category:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       ts_rank(content_tsv, plainto_tsquery('english', $2)) AS bm25_score
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND ($3::bool OR valid_to IS NULL)
                  AND ($4::bool OR local_only = FALSE)
                  AND ($8::bool OR source_type != 'dream')
                  AND ($9::uuid[] IS NULL OR source_conversation_id = ANY($9::uuid[]))
                  AND content_tsv IS NOT NULL
                  AND content_tsv @@ plainto_tsquery('english', $2)
                  AND category = $5
                  AND ($7::text IS NULL OR memory_slot = $7)
                ORDER BY bm25_score DESC
                LIMIT $6
                """,
                user_id,
                query,
                include_historical,
                include_local,
                category,
                limit,
                memory_slot,
                include_dream_observations,
                conversation_filter,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT *,
                       ts_rank(content_tsv, plainto_tsquery('english', $2)) AS bm25_score
                FROM memories
                WHERE user_id = $1
                  AND status != 'deleted'
                  AND ($3::bool OR valid_to IS NULL)
                  AND ($4::bool OR local_only = FALSE)
                  AND ($7::bool OR source_type != 'dream')
                  AND ($8::uuid[] IS NULL OR source_conversation_id = ANY($8::uuid[]))
                  AND content_tsv IS NOT NULL
                  AND content_tsv @@ plainto_tsquery('english', $2)
                  AND ($5::text IS NULL OR memory_slot = $5)
                ORDER BY bm25_score DESC
                LIMIT $6
                """,
                user_id,
                query,
                include_historical,
                include_local,
                memory_slot,
                limit,
                include_dream_observations,
                conversation_filter,
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
        include_dream_observations: bool = False,
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
        embedding = await embed_query(text)
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
            include_dream_observations=include_dream_observations,
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

    async def get_l0_memories(
        self,
        user_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Get all L0 (frozen) memories for a user.

        L0 memories are always injected into every prompt without
        embedding-based retrieval. They bypass the normal memory pipeline.
        """
        rows = await self._pool.fetch(
            """
            SELECT * FROM memories
            WHERE user_id = $1
              AND tier = 'l0'
              AND status = 'active'
              AND valid_to IS NULL
            ORDER BY created_at ASC
            """,
            user_id,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["content"] = self._enc.decrypt(d["content"])
            results.append(d)
        return results

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

    async def get_dream_candidate_memories(
        self,
        user_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM memories
            WHERE user_id = $1
              AND status = 'active'
              AND valid_to IS NULL
              AND tier = 'l1'
              AND source_type != 'dream'
              AND memory_slot IS NOT NULL
            ORDER BY memory_slot ASC, created_at ASC
            """,
            user_id,
        )
        results = []
        for row in rows:
            item = dict(row)
            item["content"] = self._enc.decrypt(item["content"])
            results.append(item)
        return results

    async def get_users_with_dream_candidates(self) -> list[uuid.UUID]:
        rows = await self._pool.fetch(
            """
            SELECT DISTINCT user_id
            FROM memories
            WHERE status = 'active'
              AND valid_to IS NULL
              AND tier = 'l1'
              AND source_type != 'dream'
              AND memory_slot IS NOT NULL
            ORDER BY user_id
            """
        )
        return [row["user_id"] for row in rows]

    async def get_total_conversation_count(self, user_id: uuid.UUID) -> int:
        """Get total conversation count for a user."""
        row = await self._pool.fetchrow(
            """
            SELECT COUNT(*) as count
            FROM conversations
            WHERE user_id = $1
            """,
            user_id,
        )
        return row["count"] if row else 0

    async def get_users_with_skill_candidates(self, conversation_interval: int) -> list[uuid.UUID]:
        """Get users who have enough conversations since last nudge to trigger consolidation."""
        rows = await self._pool.fetch(
            """
            SELECT DISTINCT s.user_id
            FROM skill_nudge_user_state s
            JOIN (
                SELECT user_id, COUNT(*) as total_count
                FROM conversations
                GROUP BY user_id
            ) c ON c.user_id = s.user_id
            WHERE (c.total_count - s.conversations_since_nudge) >= $1
            ORDER BY s.user_id
            """,
            conversation_interval,
        )
        return [row["user_id"] for row in rows]

    async def get_user_conversation_count_since_last_nudge(self, user_id: uuid.UUID) -> int:
        """Get conversations since last nudge by computing delta from total count."""
        total = await self.get_total_conversation_count(user_id)
        row = await self._pool.fetchrow(
            """
            SELECT conversations_since_nudge
            FROM skill_nudge_user_state
            WHERE user_id = $1
            """,
            user_id,
        )
        last_nudge_total = row["conversations_since_nudge"] if row else 0
        return max(0, total - last_nudge_total)

    async def record_consolidation_nudge_run(
        self, user_id: uuid.UUID, conversation_count: int
    ) -> None:
        """Record that a consolidation nudge ran. conversation_count is the total at this moment."""
        await self._pool.execute(
            """
            INSERT INTO skill_nudge_user_state (user_id, conversations_since_nudge, last_nudge_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                conversations_since_nudge = $2,
                last_nudge_at = NOW()
            """,
            user_id,
            conversation_count,
        )

    async def get_autonomous_skill_candidates(self, min_skills: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT
                sp.skill_id,
                sp.name,
                sp.description,
                sp.embedding,
                sp.use_count,
                sp.last_used_at,
                sp.allow_autonomous_edit,
                sp.source_type,
                sp.enabled
            FROM skill_projections sp
            WHERE sp.source_type = 'autonomous'
              AND sp.enabled = TRUE
              AND sp.embedding IS NOT NULL
            ORDER BY sp.use_count DESC
            LIMIT $1
            """,
            min_skills * 3,
        )
        results = []
        for row in rows:
            results.append(
                {
                    "skill_id": row["skill_id"],
                    "name": row["name"],
                    "description": row["description"],
                    "embedding": self._parse_vector(row["embedding"]),
                    "use_count": row["use_count"],
                    "last_used_at": row["last_used_at"],
                    "allow_autonomous_edit": row["allow_autonomous_edit"],
                    "source_type": row["source_type"],
                    "enabled": row["enabled"],
                }
            )
        return results

    async def get_recent_memories_for_user(
        self, user_id: uuid.UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT id, content, status, created_at, tier
            FROM memories
            WHERE user_id = $1
              AND status = 'active'
              AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        results = []
        for row in rows:
            results.append(
                {
                    "id": str(row["id"]),
                    "content": self._enc.decrypt(row["content"]) if row["content"] else "",
                    "status": row["status"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "tier": row["tier"],
                }
            )
        return results

    def _parse_vector(self, value: Any) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return [float(x) for x in value]
        return None

    async def merge_autonomous_skills(
        self,
        kept_skill_id: str,
        absorbed_skill_ids: list[str],
        user_id: uuid.UUID,
    ) -> None:
        from orchestrator.skills_store import delete_skill, get_skill, update_skill
        from orchestrator.skills_projection import SkillProjectionStore

        kept_skill = get_skill(kept_skill_id)
        absorbed_content_parts = [kept_skill.get("content", "")]

        for absorbed_id in absorbed_skill_ids:
            try:
                absorbed_skill = get_skill(absorbed_id)
                absorbed_content_parts.append(
                    f"\n\n-- Merged from {absorbed_skill.get('name', absorbed_id)} --\n"
                    f"{absorbed_skill.get('content', '')}"
                )
                delete_skill(absorbed_id)

                projection_store = SkillProjectionStore(self._pool)
                await projection_store.delete_projection(absorbed_id)
            except Exception:
                pass

        merged_content = "\n".join(absorbed_content_parts)
        update_skill(
            kept_skill_id,
            name=None,
            description=None,
            content=merged_content,
            enabled=None,
        )

        projection_store = SkillProjectionStore(self._pool)
        await projection_store.upsert_projection(
            skill_id=kept_skill_id,
            name=kept_skill.get("name", ""),
            description=kept_skill.get("description", ""),
            source_file_path=kept_skill.get("source_file_path", ""),
            source_hash="",
            enabled=True,
            source_type="autonomous",
        )

        run_id = uuid.uuid4()
        for absorbed_id in absorbed_skill_ids:
            await self._pool.execute(
                """
                INSERT INTO skill_consolidation_log
                    (user_id, run_id, action_type, skill_id, target_skill_id, reason, status)
                VALUES ($1, $2, 'delete', $3, $4, 'merged', 'applied')
                """,
                user_id,
                run_id,
                absorbed_id,
                kept_skill_id,
            )

        await self._pool.execute(
            """
            INSERT INTO skill_consolidation_log
                (user_id, run_id, action_type, skill_id, reason, status, skill_name, skill_description)
            VALUES ($1, $2, 'merge', $3, $4, 'applied', $5, $6)
            """,
            user_id,
            run_id,
            kept_skill_id,
            f"merged {len(absorbed_skill_ids)} skill(s)",
            kept_skill.get("name", ""),
            kept_skill.get("description", ""),
        )

    async def log_consolidation_nudge_action(
        self,
        user_id: uuid.UUID,
        run_id: uuid.UUID,
        action_type: str,
        skill_id: str | None,
        target_skill_id: str | None,
        reason: str,
        similarity: float | None,
        status: str,
        skill_name: str | None = None,
        skill_description: str | None = None,
        skill_use_count: int | None = None,
        skill_last_used_at: Any = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO skill_consolidation_log
                (user_id, run_id, action_type, skill_id, target_skill_id, reason,
                 similarity, status, skill_name, skill_description, skill_use_count, skill_last_used_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            user_id,
            run_id,
            action_type,
            skill_id,
            target_skill_id,
            reason,
            similarity,
            status,
            skill_name,
            skill_description,
            skill_use_count,
            skill_last_used_at,
        )

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

    async def get_last_extraction_time(
        self,
        conversation_id: uuid.UUID,
    ) -> datetime | None:
        """Get the timestamp of the last extraction for a conversation."""
        row = await self._pool.fetchrow(
            """
            SELECT created_at FROM memory_extraction_log 
            WHERE conversation_id = $1 
            ORDER BY created_at DESC LIMIT 1
            """,
            conversation_id,
        )
        return row["created_at"] if row else None

    # ------------------------------------------------------------------
    # Retrieval log operations
    # ------------------------------------------------------------------

    async def log_retrieval(
        self,
        user_id: uuid.UUID,
        query_text: str,
        query_embedding_model: str,
        query_embedding: list[float] | None,
        candidate_memory_ids: list[uuid.UUID],
        candidate_scores: dict[str, Any],
        selected_memory_ids: list[uuid.UUID],
        l0_included: bool,
        latency_ms: int,
        *,
        conversation_id: uuid.UUID | None = None,
        retrieval_context: str | None = None,
        retrieval_triggered_by: str | None = None,
    ) -> dict[str, Any]:
        embedding_str = _format_vector(query_embedding) if query_embedding else None
        row = await self._pool.fetchrow(
            """
            INSERT INTO retrieval_log
                (user_id, conversation_id, query_text, query_embedding_model, query_embedding,
                 candidate_memory_ids, candidate_scores, selected_memory_ids, l0_included,
                 latency_ms, retrieval_context, retrieval_triggered_by)
            VALUES ($1, $2, $3, $4, $5::vector, $6::uuid[], $7::jsonb, $8::uuid[], $9, $10, $11, $12)
            RETURNING *
            """,
            user_id,
            conversation_id,
            query_text,
            query_embedding_model,
            embedding_str,
            [str(m) for m in candidate_memory_ids],
            json.dumps(candidate_scores),
            [str(m) for m in selected_memory_ids],
            l0_included,
            latency_ms,
            retrieval_context,
            retrieval_triggered_by,
        )
        return dict(row)

    async def get_retrieval_logs(
        self,
        user_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if conversation_id is not None:
            rows = await self._pool.fetch(
                """
                SELECT * FROM retrieval_log
                WHERE user_id = $1 AND conversation_id = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_id,
                conversation_id,
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM retrieval_log
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )
        return [dict(r) for r in rows]

    async def get_retrieval_log(
        self,
        log_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM retrieval_log WHERE id = $1",
            log_id,
        )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    async def insert_entity(
        self,
        user_id: uuid.UUID,
        canonical_name: str,
        lookup_key: str,
        *,
        aliases: list[str] | None = None,
        alias_lookup_keys: list[str] | None = None,
        source_memory_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        encrypted_name = self._enc.encrypt(canonical_name)
        encrypted_aliases = (
            json.dumps(self._enc.encrypt(json.dumps(aliases or [])))
            if aliases is not None
            else None
        )
        row = await self._pool.fetchrow(
            """
            INSERT INTO entities
                (user_id, canonical_name, lookup_key, aliases, alias_lookup_keys, source_memory_id)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6)
            ON CONFLICT (user_id, lookup_key) DO UPDATE
                SET aliases = EXCLUDED.aliases::jsonb,
                    alias_lookup_keys = EXCLUDED.alias_lookup_keys,
                    updated_at = NOW()
            RETURNING *
            """,
            user_id,
            encrypted_name,
            lookup_key,
            encrypted_aliases,
            alias_lookup_keys or [],
            source_memory_id,
        )
        result = dict(row)
        result["canonical_name"] = self._enc.decrypt(result["canonical_name"])
        if result.get("aliases") is not None:
            result["aliases"] = json.loads(self._enc.decrypt(json.loads(result["aliases"])))
        return result

    async def get_entity(
        self,
        entity_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM entities WHERE id = $1",
            entity_id,
        )
        if not row:
            return None
        result = dict(row)
        result["canonical_name"] = self._enc.decrypt(result["canonical_name"])
        if result.get("aliases") is not None:
            result["aliases"] = json.loads(self._enc.decrypt(json.loads(result["aliases"])))
        return result

    async def get_entity_by_lookup_key(
        self,
        user_id: uuid.UUID,
        lookup_key: str,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM entities
            WHERE user_id = $1 AND lookup_key = $2
            """,
            user_id,
            lookup_key,
        )
        if not row:
            return None
        result = dict(row)
        result["canonical_name"] = self._enc.decrypt(result["canonical_name"])
        if result.get("aliases") is not None:
            result["aliases"] = json.loads(self._enc.decrypt(json.loads(result["aliases"])))
        return result

    async def get_entities_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM entities
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["canonical_name"] = self._enc.decrypt(d["canonical_name"])
            if d.get("aliases") is not None:
                d["aliases"] = json.loads(self._enc.decrypt(json.loads(d["aliases"])))
            results.append(d)
        return results

    async def update_entity_aliases(
        self,
        entity_id: uuid.UUID,
        aliases: list[str],
        alias_lookup_keys: list[str],
    ) -> dict[str, Any] | None:
        encrypted_aliases = json.dumps(self._enc.encrypt(json.dumps(aliases)))
        row = await self._pool.fetchrow(
            """
            UPDATE entities
            SET aliases = $2::jsonb,
                alias_lookup_keys = $3::text[],
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            entity_id,
            encrypted_aliases,
            alias_lookup_keys,
        )
        if not row:
            return None
        result = dict(row)
        result["canonical_name"] = self._enc.decrypt(result["canonical_name"])
        if result.get("aliases") is not None:
            result["aliases"] = json.loads(self._enc.decrypt(json.loads(result["aliases"])))
        return result

    async def link_entity_to_memory(
        self,
        entity_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE entities
            SET linked_memory_ids = (
                SELECT ARRAY(SELECT DISTINCT elem
                             FROM UNNEST(linked_memory_ids || ARRAY[$2]) AS elem)
            ),
                updated_at = NOW()
            WHERE id = $1
            """,
            entity_id,
            memory_id,
        )
        return result == "UPDATE 1"

    async def get_entities_for_memory(
        self,
        memory_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT e.* FROM entities e
            WHERE $1 = ANY(e.linked_memory_ids)
            ORDER BY e.created_at DESC
            """,
            memory_id,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["canonical_name"] = self._enc.decrypt(d["canonical_name"])
            if d.get("aliases") is not None:
                d["aliases"] = json.loads(self._enc.decrypt(d["aliases"]))
            results.append(d)
        return results

    async def find_entities_by_alias(
        self,
        user_id: uuid.UUID,
        alias_lookup_key: str,
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM entities
            WHERE user_id = $1 AND $2 = ANY(alias_lookup_keys)
            ORDER BY created_at DESC
            """,
            user_id,
            alias_lookup_key,
        )
        results = []
        for r in rows:
            d = dict(r)
            d["canonical_name"] = self._enc.decrypt(d["canonical_name"])
            if d.get("aliases") is not None:
                d["aliases"] = json.loads(self._enc.decrypt(json.loads(d["aliases"])))
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Dream log operations
    # ------------------------------------------------------------------

    async def log_dream_run(
        self,
        user_id: uuid.UUID,
        status: str,
        *,
        eligible_families: list[str] | None = None,
        skipped_families: list[str] | None = None,
        families_processed: int = 0,
        observations_created: int = 0,
        observation_memory_ids: list[uuid.UUID] | None = None,
        error_message: str | None = None,
        run_completed_at: datetime | None = None,
        model_used: str | None = None,
    ) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dream_log
                (user_id, status, eligible_families, skipped_families, families_processed,
                 observations_created, observation_memory_ids, error_message, run_completed_at, model_used)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7::uuid[], $8, $9, $10)
            RETURNING *
            """,
            user_id,
            status,
            json.dumps(eligible_families or []),
            json.dumps(skipped_families or []),
            families_processed,
            observations_created,
            [str(m) for m in (observation_memory_ids or [])],
            error_message,
            run_completed_at,
            model_used,
        )
        return dict(row)

    async def update_dream_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str | None = None,
        observations_created: int | None = None,
        observation_memory_ids: list[uuid.UUID] | None = None,
        skipped_families: list[str] | None = None,
        error_message: str | None = None,
        run_completed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            UPDATE dream_log
            SET status = COALESCE($2, status),
                observations_created = COALESCE($3, observations_created),
                observation_memory_ids = COALESCE($4, observation_memory_ids),
                skipped_families = COALESCE($5, skipped_families),
                error_message = COALESCE($6, error_message),
                run_completed_at = COALESCE($7, run_completed_at)
            WHERE id = $1
            RETURNING *
            """,
            run_id,
            status,
            observations_created,
            [str(m) for m in observation_memory_ids]
            if observation_memory_ids is not None
            else None,
            json.dumps(skipped_families) if skipped_families is not None else None,
            error_message,
            run_completed_at,
        )
        return dict(row) if row else None

    async def get_dream_runs(
        self,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if status is not None:
            rows = await self._pool.fetch(
                """
                SELECT * FROM dream_log
                WHERE user_id = $1 AND status = $2
                ORDER BY run_started_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_id,
                status,
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM dream_log
                WHERE user_id = $1
                ORDER BY run_started_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )
        return [dict(r) for r in rows]

    async def get_latest_dream_run(
        self,
        user_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM dream_log
            WHERE user_id = $1
            ORDER BY run_started_at DESC
            LIMIT 1
            """,
            user_id,
        )
        return dict(row) if row else None

    async def get_dream_run(
        self,
        run_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dream_log WHERE id = $1",
            run_id,
        )
        return dict(row) if row else None

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
            content = mem["content"]
            encrypted_content = self._enc.encrypt(content)
            content_hash = compute_memory_content_hash(content)
            embedding_str = _format_vector(mem["embedding"]) if mem.get("embedding") else None
            embedding_model = mem.get("embedding_model") or _default_embedding_model()
            status = mem.get("status", "active")
            memory_slot = mem.get("memory_slot")
            try:
                await self._pool.execute(
                    """
                    INSERT INTO memories
                        (user_id, content, content_hash, embedding, embedding_model,
                         category, source_type, local_only, confidence, status, memory_slot)
                    VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    user_id,
                    encrypted_content,
                    content_hash,
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
            except asyncpg.UniqueViolationError:
                continue
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

    @staticmethod
    def _normalize_settings(value: Any) -> dict[str, Any]:
        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in users.settings", extra={"value_type": "str"})
                return {}
            return parsed if isinstance(parsed, dict) else {}

        if isinstance(value, (bytes, bytearray)):
            try:
                parsed = json.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning("Invalid bytes JSON in users.settings")
                return {}
            return parsed if isinstance(parsed, dict) else {}

        try:
            return dict(value)
        except (TypeError, ValueError):
            logger.warning(
                "Unsupported users.settings payload shape",
                extra={"value_type": type(value).__name__},
            )
            return {}

    async def get_user_settings(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Get user settings from database.

        Returns empty dict if user has no settings or doesn't exist.
        """
        if not self._pool:
            return {}
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT settings FROM users WHERE id = $1", user_id)
            if row and row["settings"] is not None:
                return self._normalize_settings(row["settings"])
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
            return self._normalize_settings(row["settings"])


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
    if "advisor_traces" in message:
        message["advisor_traces"] = _coerce_json_value(
            message.get("advisor_traces"),
            default=None,
            expected_type=dict,
        )

    return message


def _format_vector(embedding: list[float]) -> str:
    return "[" + ",".join(str(f) for f in embedding) + "]"
