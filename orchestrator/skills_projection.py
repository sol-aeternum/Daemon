from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

import asyncpg

from orchestrator.memory.embedding import embed_documents

logger = logging.getLogger(__name__)


def _format_vector(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _parse_vector(val: Any) -> list[float] | None:
    if val is None:
        return None
    if isinstance(val, list):
        return [float(x) for x in val]
    return None


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def projection_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    if "embedding" in data:
        data["embedding"] = _parse_vector(data["embedding"])
    # Defensively parse legacy string-shaped pending_update rows into dicts.
    # DB inspection showed jsonb_typeof(pending_update) = 'string' for bad rows.
    if "pending_update" in data and isinstance(data["pending_update"], str):
        try:
            data["pending_update"] = json.loads(data["pending_update"])
        except (json.JSONDecodeError, TypeError):
            pass  # Leave as-is; will be None-ish or cause visible errors
    return data


class SkillProjectionStore:
    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._pool = db_pool

    async def upsert_projection(
        self,
        skill_id: str,
        name: str,
        description: str,
        source_file_path: str,
        source_hash: str,
        enabled: bool = True,
        source_type: str = "manual",
        created_by: str = "system",
        origin_url: str = "",
        embedding: list[float] | None = None,
        repo_version: str = "0.0.0",
        local_version: str = "0.0.0",
        pending_update: dict[str, Any] | None = None,
        allow_autonomous_edit: bool = False,
        trigger_conditions: str = "",
        complexity_origin: int = 0,
    ) -> dict[str, Any]:
        embedding_str = _format_vector(embedding)
        row = await self._pool.fetchrow(
            """
            INSERT INTO skill_projections
                (skill_id, name, description, source_file_path, source_hash,
                 enabled, source_type, created_by, origin_url, embedding,
                 repo_version, local_version, pending_update,
                 allow_autonomous_edit, trigger_conditions, complexity_origin)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector, $11, $12, $13::jsonb, $14, $15, $16)
            ON CONFLICT (skill_id) DO UPDATE SET
                name              = EXCLUDED.name,
                description       = EXCLUDED.description,
                source_file_path  = EXCLUDED.source_file_path,
                source_hash       = EXCLUDED.source_hash,
                enabled           = EXCLUDED.enabled,
                source_type       = EXCLUDED.source_type,
                created_by        = EXCLUDED.created_by,
                origin_url        = EXCLUDED.origin_url,
                embedding         = COALESCE(EXCLUDED.embedding, skill_projections.embedding),
                repo_version      = EXCLUDED.repo_version,
                local_version     = EXCLUDED.local_version,
                pending_update    = EXCLUDED.pending_update,
                allow_autonomous_edit = EXCLUDED.allow_autonomous_edit,
                trigger_conditions = EXCLUDED.trigger_conditions,
                complexity_origin = EXCLUDED.complexity_origin,
                updated_at        = NOW(),
                last_synced_at    = NOW()
            RETURNING *
            """,
            skill_id,
            name,
            description,
            source_file_path,
            source_hash,
            enabled,
            source_type,
            created_by,
            origin_url,
            embedding_str,
            repo_version,
            local_version,
            pending_update,
            allow_autonomous_edit,
            trigger_conditions,
            complexity_origin,
        )
        return projection_from_row(row)

    async def get_projection(self, skill_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM skill_projections WHERE skill_id = $1",
            skill_id,
        )
        if not row:
            return None
        return projection_from_row(row)

    async def list_projections(
        self,
        source_type: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = ["1=1"]
        params: list[Any] = []
        if source_type is not None:
            params.append(source_type)
            conditions.append(f"source_type = ${len(params)}")
        if enabled is not None:
            params.append(enabled)
            conditions.append(f"enabled = ${len(params)}")
        params.extend([limit, offset])
        # conditions contains only the fixed clauses selected above; values
        # remain positional asyncpg parameters.
        where_clause = " AND ".join(conditions)
        query = " ".join(
            (
                "SELECT * FROM skill_projections",
                f"WHERE {where_clause}",
                "ORDER BY updated_at DESC",
                f"LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            )
        )
        rows = await self._pool.fetch(query, *params)
        return [projection_from_row(r) for r in rows]

    async def delete_projection(self, skill_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM skill_projections WHERE skill_id = $1",
            skill_id,
        )
        return result == "DELETE 1"

    async def touch_usage(self, skill_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE skill_projections
            SET use_count = use_count + 1,
                last_used_at = NOW(),
                updated_at = NOW()
            WHERE skill_id = $1
            """,
            skill_id,
        )

    async def update_embedding(self, skill_id: str, embedding: list[float]) -> bool:
        embedding_str = _format_vector(embedding)
        result = await self._pool.execute(
            """
            UPDATE skill_projections
            SET embedding = $2::vector,
                last_synced_at = NOW(),
                updated_at = NOW()
            WHERE skill_id = $1
            """,
            skill_id,
            embedding_str,
        )
        return result == "UPDATE 1"

    async def set_pending_update(
        self,
        skill_id: str,
        pending_update: dict[str, Any],
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE skill_projections
            SET pending_update = $2::jsonb,
                updated_at = NOW()
            WHERE skill_id = $1
            """,
            skill_id,
            pending_update,
        )
        return result == "UPDATE 1"

    async def clear_pending_update(self, skill_id: str) -> bool:
        result = await self._pool.execute(
            """
            UPDATE skill_projections
            SET pending_update = NULL,
                repo_version = local_version,
                updated_at = NOW()
            WHERE skill_id = $1
            """,
            skill_id,
        )
        return result == "UPDATE 1"

    async def update_autonomous_edit(
        self,
        skill_id: str,
        allow_autonomous_edit: bool,
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE skill_projections
            SET allow_autonomous_edit = $2,
                updated_at = NOW()
            WHERE skill_id = $1
            """,
            skill_id,
            allow_autonomous_edit,
        )
        return result == "UPDATE 1"

    async def update_autonomous_metadata(
        self,
        skill_id: str,
        *,
        trigger_conditions: str | None = None,
        complexity_origin: int | None = None,
    ) -> bool:
        set_clauses = ["updated_at = NOW()"]
        params: list[Any] = []

        if trigger_conditions is not None:
            params.append(trigger_conditions)
            set_clauses.append(f"trigger_conditions = ${len(params)}")
        if complexity_origin is not None:
            params.append(complexity_origin)
            set_clauses.append(f"complexity_origin = ${len(params)}")

        if len(params) == 0:
            return False

        params.append(skill_id)
        # set_clauses contains only the fixed column assignments above.
        set_clause = ", ".join(set_clauses)
        query = " ".join(
            (
                "UPDATE skill_projections",
                f"SET {set_clause}",
                f"WHERE skill_id = ${len(params)}",
            )
        )
        result = await self._pool.execute(query, *params)
        return result == "UPDATE 1"

    async def search_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int = 10,
        min_similarity: float = 0.0,
        source_type: str | None = None,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        embedding_str = _format_vector(query_embedding)
        conditions = [
            "embedding IS NOT NULL",
            "1 - (embedding <=> $1::vector) >= $2",
        ]
        params: list[Any] = [embedding_str, min_similarity]
        if source_type is not None:
            params.append(source_type)
            conditions.append(f"source_type = ${len(params)}")
        if enabled_only:
            conditions.append("enabled = TRUE")
        params.append(limit)
        # conditions contains only the fixed clauses selected above; values
        # remain positional asyncpg parameters.
        where_clause = " AND ".join(conditions)
        query = " ".join(
            (
                "SELECT *, 1 - (embedding <=> $1::vector) AS similarity",
                "FROM skill_projections",
                f"WHERE {where_clause}",
                "ORDER BY embedding <=> $1::vector",
                f"LIMIT ${len(params)}",
            )
        )
        rows = await self._pool.fetch(query, *params)
        return [projection_from_row(r) for r in rows]

    async def projection_exists(self, skill_id: str) -> bool:
        exists = await self._pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM skill_projections WHERE skill_id = $1)",
            skill_id,
        )
        return bool(exists)

    async def get_source_hash(self, skill_id: str) -> str | None:
        return await self._pool.fetchval(
            "SELECT source_hash FROM skill_projections WHERE skill_id = $1",
            skill_id,
        )

    async def get_all_skill_ids(self) -> list[str]:
        rows = await self._pool.fetch("SELECT skill_id FROM skill_projections")
        return [r["skill_id"] for r in rows]

    async def get_version_info(self, skill_id: str) -> tuple[str, str] | None:
        row = await self._pool.fetchrow(
            "SELECT repo_version, local_version FROM skill_projections WHERE skill_id = $1",
            skill_id,
        )
        if not row:
            return None
        return (row["repo_version"], row["local_version"])

    async def update_versions(
        self,
        skill_id: str,
        *,
        repo_version: str | None = None,
        local_version: str | None = None,
    ) -> bool:
        set_clauses = ["updated_at = NOW()"]
        params: list[Any] = []
        if repo_version is not None:
            params.append(repo_version)
            set_clauses.append(f"repo_version = ${len(params)}")
        if local_version is not None:
            params.append(local_version)
            set_clauses.append(f"local_version = ${len(params)}")
        if not params:
            return False
        params.append(skill_id)
        # set_clauses contains only the fixed column assignments above.
        set_clause = ", ".join(set_clauses)
        query = " ".join(
            (
                "UPDATE skill_projections",
                f"SET {set_clause}",
                f"WHERE skill_id = ${len(params)}",
            )
        )
        result = await self._pool.execute(query, *params)
        return result == "UPDATE 1"


async def embed_skill_content(name: str, description: str, content: str) -> list[float]:
    text_for_embedding = f"{name}\n{description}\n{content[:2000]}"
    embeddings = await embed_documents([text_for_embedding])
    return embeddings[0] if embeddings else []
