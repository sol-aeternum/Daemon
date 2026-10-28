"""Benchmark-database safety and state helpers for the LongMemEval fact harness.

The harness deletes rows (``CANONICAL_RESET_STATEMENTS``, orphan rollback), so
it must only ever run against a dedicated benchmark database. The guard is an
allowlist, never an attempt to recognise production: the database must carry
both the benchmark name prefix and an explicit marker comment written when the
benchmark database is initialised (``tests/benchmark_longmemeval/bench_db.sh``).
Production databases never carry that marker, so a misdirected
``DATABASE_URL`` fails closed before any read or write.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

BENCHMARK_DB_NAME_PREFIX = "daemon_lme_bench"
BENCHMARK_DB_MARKER = "daemon-longmemeval-benchmark-v1"

# Tables whose content determines what evaluation can retrieve and rank.
# retrieval_log is deliberately excluded: evaluate appends diagnostics there and
# nothing in ranking reads it.
FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("conversations", "user_id"),
    ("messages", "user_id"),
    ("memories", "user_id"),
    ("memory_extraction_log", "user_id"),
    ("entities", "user_id"),
)
# What one session's extraction can change outside its own conversation (dedup
# merges, supersessions, entity rows). Messages are excluded: a session only
# writes its own, and they cascade with its conversation.
CROSS_SESSION_TABLES: tuple[tuple[str, str], ...] = tuple(
    item for item in FINGERPRINT_TABLES if item[0] != "messages"
)

# Each question's haystack is ingested into its own benchmark user. The id is
# derived from the question id, so a resume or a restored snapshot maps every
# question back to the same user.
BENCHMARK_USER_NAMESPACE = uuid.UUID("ef9c86b2-45be-514c-94af-224bf6b9ef34")


def question_user_id(question_id: str) -> uuid.UUID:
    return uuid.uuid5(BENCHMARK_USER_NAMESPACE, f"longmemeval:{question_id}")


class BenchmarkDatabaseGuardError(RuntimeError):
    """Raised when DATABASE_URL does not point at a marked benchmark database."""


def _database_name_from_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return ""
    if parts.scheme not in ("postgres", "postgresql"):
        return ""
    return unquote(parts.path.lstrip("/"))


def check_benchmark_dsn(dsn: str | None) -> str:
    """Pre-connection check: the DSN must name a benchmark database.

    Returns the database name. Errors never echo the DSN, which can carry
    credentials.
    """
    name = _database_name_from_dsn(dsn or "")
    if not name.startswith(BENCHMARK_DB_NAME_PREFIX):
        raise BenchmarkDatabaseGuardError(
            "Refusing to run: DATABASE_URL does not name a benchmark database "
            f"(expected a database name starting with {BENCHMARK_DB_NAME_PREFIX!r}). "
            "Start the dedicated stack from tests/benchmark_longmemeval/RUNBOOK_LAPTOP.md."
        )
    return name


async def assert_benchmark_database(pool: Any, dsn: str | None) -> str:
    """Refuse unless the connected database is a marked benchmark database."""
    expected_name = check_benchmark_dsn(dsn)
    row = await pool.fetchrow(
        "SELECT current_database() AS name, "
        "shobj_description(d.oid, 'pg_database') AS marker "
        "FROM pg_database d WHERE d.datname = current_database()"
    )
    name = row["name"] if row is not None else None
    marker = row["marker"] if row is not None else None
    if name != expected_name or not str(name or "").startswith(BENCHMARK_DB_NAME_PREFIX):
        raise BenchmarkDatabaseGuardError(
            "Refusing to run: the connected database is not the benchmark database named "
            "in DATABASE_URL."
        )
    if marker != BENCHMARK_DB_MARKER:
        raise BenchmarkDatabaseGuardError(
            "Refusing to run: the connected database has no benchmark marker. Initialise it "
            "with `tests/benchmark_longmemeval/bench_db.sh init`; never mark a database that "
            "holds real data."
        )
    return str(name)


async def ensure_benchmark_account(pool: Any, user_id: uuid.UUID) -> None:
    """Give the benchmark user an active account so account-scoped compute admits it.

    Synthetic initialised state for the benchmark database only, mirroring the
    test fixtures' explicit seeding. Production identities are initialised by
    the identity service and are never touched here. Call after the guard.
    """
    await pool.execute(
        "INSERT INTO entitlement_accounts (user_id, plan, plan_source, status) "
        "VALUES ($1, 'power', 'admin', 'active') ON CONFLICT (user_id) DO NOTHING",
        user_id,
    )


async def ensure_benchmark_user(pool: Any, user_id: uuid.UUID) -> None:
    """Create a per-question benchmark user and its account (idempotent). Call after the guard."""
    await pool.execute(
        "INSERT INTO users (id, email, name, username, preferences, created_at, updated_at) "
        "VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW()) ON CONFLICT (id) DO NOTHING",
        user_id,
        f"longmemeval+{user_id.hex}@daemon.test",
        f"longmemeval-{user_id.hex}",
    )
    await ensure_benchmark_account(pool, user_id)


async def fingerprint_benchmark_state(
    pool: Any,
    user_id: uuid.UUID,
    *,
    tables: tuple[tuple[str, str], ...] = FINGERPRINT_TABLES,
) -> dict[str, Any]:
    """Content hash of every row evaluation can read for the benchmark user.

    Equal fingerprints before and after a phase prove the phase did not change
    retrieval or ranking inputs (access counts, timestamps, trust, status).
    ``tables`` must be drawn from :data:`FINGERPRINT_TABLES`.
    """
    if not set(tables) <= set(FINGERPRINT_TABLES):
        raise ValueError("fingerprint tables must come from FINGERPRINT_TABLES")
    digests: dict[str, dict[str, Any]] = {}
    for table, owner_column in tables:
        row = await pool.fetchrow(
            f"SELECT count(*) AS rows, "  # nosec B608 - fixed identifiers from FINGERPRINT_TABLES
            f"md5(coalesce(string_agg(md5(t::text), '' ORDER BY t.id), '')) AS digest "
            f"FROM {table} t WHERE t.{owner_column} = $1",
            user_id,
        )
        digests[table] = {
            "rows": int(row["rows"]) if row is not None else 0,
            "md5": str(row["digest"]) if row is not None else "",
        }
    combined = hashlib.sha256(json.dumps(digests, sort_keys=True).encode("utf-8")).hexdigest()
    return {"sha256": combined, "tables": digests}


async def fingerprint_benchmark_users(
    pool: Any,
    user_ids: Sequence[uuid.UUID],
    *,
    tables: tuple[tuple[str, str], ...] = FINGERPRINT_TABLES,
) -> dict[str, Any]:
    """One fingerprint over every benchmark user (per-user fingerprints, combined)."""
    per_user: dict[str, dict[str, Any]] = {}
    for user_id in sorted(set(user_ids), key=str):
        per_user[str(user_id)] = await fingerprint_benchmark_state(pool, user_id, tables=tables)
    rows = {
        table: sum(int(fp["tables"][table]["rows"]) for fp in per_user.values())
        for table, _owner in tables
    }
    combined = hashlib.sha256(
        json.dumps({user: fp["sha256"] for user, fp in per_user.items()}, sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    return {"sha256": combined, "users": len(per_user), "rows": rows}


async def list_benchmark_conversation_ids(pool: Any, user_id: uuid.UUID) -> set[str]:
    rows = await pool.fetch("SELECT id FROM conversations WHERE user_id = $1", user_id)
    return {str(row["id"]) for row in rows}


async def rollback_orphan_conversations(
    pool: Any, user_id: uuid.UUID, conversation_ids: set[str]
) -> dict[str, int]:
    """Delete everything an interrupted session ingest wrote.

    Memories and extraction logs reference conversations with ON DELETE SET
    NULL, so they are deleted explicitly before the conversation (messages
    cascade). Dedup merges or supersessions applied to memories of *other*
    sessions are not undone here: callers must verify the result (see
    ``LongMemEvalFactRunner._reconcile_interrupted_session``).
    """
    if not conversation_ids:
        return {"conversations": 0, "memories": 0, "memory_extraction_log": 0}
    ids = [uuid.UUID(value) for value in sorted(conversation_ids)]
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            memories = await conn.execute(
                "DELETE FROM memories WHERE user_id = $1 AND source_conversation_id = ANY($2::uuid[])",
                user_id,
                ids,
            )
            logs = await conn.execute(
                "DELETE FROM memory_extraction_log "
                "WHERE user_id = $1 AND conversation_id = ANY($2::uuid[])",
                user_id,
                ids,
            )
            conversations = await conn.execute(
                "DELETE FROM conversations WHERE user_id = $1 AND id = ANY($2::uuid[])",
                user_id,
                ids,
            )
    for key, status in (
        ("memories", memories),
        ("memory_extraction_log", logs),
        ("conversations", conversations),
    ):
        try:
            counts[key] = int(str(status).split()[-1])
        except (ValueError, IndexError):
            counts[key] = 0
    return counts


async def wipe_benchmark_user(
    pool: Any, user_id: uuid.UUID, statements: Sequence[tuple[str, str]]
) -> dict[str, int]:
    """Delete one benchmark user's memory state in a single transaction.

    ``statements`` are ``(table, "DELETE ... WHERE user_id = $1")`` pairs (the
    harness passes its per-user reset statements). The user row, entitlement
    account and compute ledger stay, so spend remains accounted.
    """
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            for table, statement in statements:
                status = await conn.execute(statement, user_id)
                try:
                    counts[table] = int(str(status).split()[-1])
                except (ValueError, IndexError):
                    counts[table] = 0
    return counts


async def summarize_ingest_routes(pool: Any, user_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    """Which extraction models served the ingest and how memories were embedded."""
    extraction_rows = await pool.fetch(
        "SELECT coalesce(model_used, '<none>') AS model, count(*) AS calls "
        "FROM memory_extraction_log WHERE user_id = ANY($1::uuid[]) GROUP BY 1 ORDER BY 1",
        list(user_ids),
    )
    embedding_rows = await pool.fetch(
        "SELECT coalesce(embedding_model, '<none>') AS model, count(*) AS memories, "
        "count(embedding) AS embedded FROM memories WHERE user_id = ANY($1::uuid[]) "
        "GROUP BY 1 ORDER BY 1",
        list(user_ids),
    )
    return {
        "extraction_models": {str(r["model"]): int(r["calls"]) for r in extraction_rows},
        "memory_embeddings": {
            str(r["model"]): {"memories": int(r["memories"]), "embedded": int(r["embedded"])}
            for r in embedding_rows
        },
    }


async def summarize_compute_spend(
    pool: Any, user_ids: Sequence[uuid.UUID], since: str | datetime | None
) -> dict[str, Any]:
    """Account-ledger spend and token usage for guarded calls since ``since`` (ISO-8601)."""
    if isinstance(since, str):
        since = datetime.fromisoformat(since)
    rows = await pool.fetch(
        "SELECT coalesce(model, '<none>') AS model, operation, count(*) AS calls, "
        "coalesce(sum(actual_microusd), 0) AS actual_microusd, "
        "coalesce(sum((usage->>'input_tokens')::bigint), 0) AS input_tokens, "
        "coalesce(sum((usage->>'output_tokens')::bigint), 0) AS output_tokens, "
        "count(*) FILTER (WHERE status = 'open') AS open_reservations "
        "FROM entitlement_reservations "
        "WHERE user_id = ANY($1::uuid[]) "
        "AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz) "
        "GROUP BY 1, 2 ORDER BY 1, 2",
        list(user_ids),
        since,
    )
    return {
        "by_model": [
            {
                "model": str(r["model"]),
                "operation": str(r["operation"]),
                "calls": int(r["calls"]),
                "actual_microusd": int(r["actual_microusd"]),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "open_reservations": int(r["open_reservations"]),
            }
            for r in rows
        ]
    }
