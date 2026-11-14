"""Shared fail-closed multi-query schema prerequisite; no import-time I/O."""

from __future__ import annotations

from typing import Any

from orchestrator.config import Settings


async def require_revision_schema(pool: Any) -> None:
    """Matching source and applied migration are both mandatory; no auto-DDL."""
    if pool is None:
        return  # Existing database-unavailable behavior is not an enabled memory service.
    present = await pool.fetchval(
        "SELECT to_regclass('memory_mutations') IS NOT NULL AND "
        "to_regclass('memory_revision_segments') IS NOT NULL AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('memories') "
        "AND tgname='memory_revision_capture' AND tgenabled='O') AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('messages') "
        "AND tgname='message_source_revision_guard' AND tgenabled='O' AND tgtype=23 "
        "AND tgfoid=to_regprocedure('memory_message_revision_guard()')) AND "
        "(SELECT count(*)=3 AND bool_and(atttypid='bigint'::regtype "
        "AND attnotnull=(attname='source_revision') "
        "AND pg_get_expr(d.adbin,d.adrelid) IS NOT DISTINCT FROM "
        "CASE WHEN attname='source_revision' THEN '1' END) "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=to_regclass('messages') AND NOT a.attisdropped "
        "AND a.attname IN ('source_revision','correction_origin_generation','correction_source_revision')) AND "
        "to_regclass('conversation_summary_versions') IS NOT NULL AND "
        "to_regclass('conversation_summary_sources') IS NOT NULL AND "
        "to_regclass('conversation_summary_payloads') IS NOT NULL AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('conversations') "
        "AND tgname='conversation_summary_head_guard' AND tgenabled='O' AND tgtype=23 "
        "AND tgfoid=to_regprocedure('conversation_summary_head_guard()')) AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('conversation_summary_versions') "
        "AND tgname='summary_version_guard' AND tgenabled='O' AND tgtype=23 "
        "AND tgfoid=to_regprocedure('summary_version_guard()')) AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('conversation_summary_sources') "
        "AND tgname='summary_source_guard' AND tgenabled='O' AND tgtype=31 "
        "AND tgfoid=to_regprocedure('summary_source_guard()')) AND "
        "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('conversation_summary_payloads') "
        "AND tgname='summary_payload_guard' AND tgenabled='O' AND tgtype=23 "
        "AND tgfoid=to_regprocedure('summary_payload_guard()'))"
    )
    if present is not True:
        raise RuntimeError(
            "Memory revisions require migration 066, admission 071 and summary provenance 072 with matching writers; do not bypass rollback guards"
        )


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
