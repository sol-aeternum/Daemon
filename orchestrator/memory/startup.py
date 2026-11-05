"""Shared fail-closed multi-query schema prerequisite; no import-time I/O."""

from __future__ import annotations

from typing import Any

from orchestrator.config import Settings


async def require_multi_query_schema(pool: Any, settings: Settings) -> None:
    if not settings.memory_multi_query_enabled:
        return
    message = "Memory multi-query requires migration 063 (retrieval_log.query_role)"
    if pool is None:
        raise RuntimeError(message)
    try:
        present = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid = pg_catalog.to_regclass('public.retrieval_log')
                  AND attname = 'query_role' AND NOT attisdropped
            )
            """
        )
    except Exception:
        raise RuntimeError(message) from None
    if present is not True:
        raise RuntimeError(message)
