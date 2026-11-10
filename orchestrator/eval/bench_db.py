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

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.lineage import erase
from orchestrator.memory.revisions import capture_for, mutation
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

BENCHMARK_DB_NAME_PREFIX = "daemon_lme_bench"
BENCHMARK_DB_MARKER = "daemon-longmemeval-benchmark-v1"

# Tables whose content determines what evaluation can retrieve and rank.
# retrieval_log is deliberately excluded: evaluate appends diagnostics there and
# nothing in ranking reads it. Its erasure-fenced publication also advances the
# clock and commit observations without changing an assertion. Hash protection
# state and revision-bearing mutations, not that diagnostic bookkeeping.
FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("conversations", "user_id"),
    ("messages", "user_id"),
    ("memories", "user_id"),
    ("memory_extraction_log", "user_id"),
    ("entities", "user_id"),
    ("memory_revisions", "user_id"),
    ("memory_revision_segments", "user_id"),  # Owner is qualified through its revision.
    ("memory_dependencies", "user_id"),
    ("memory_sources", "user_id"),
    ("memory_knowledge_clock", "user_id"),
    ("memory_mutations", "user_id"),
    ("memory_tombstones", "user_id"),
    ("memory_erased_sources", "user_id"),
)
# A synthetic content reset is NOT an account reset or a temporal-history rewind.
RETAINED_PROTECTION_TABLES = frozenset(
    {"memory_knowledge_clock", "memory_mutations", "memory_tombstones", "memory_erased_sources"}
)
_FINGERPRINT_KEYS = {
    "memory_dependencies": "t.user_id,t.parent_memory_id,t.parent_revision_id,t.dependent_kind,t.dependent_id",
    "memory_sources": "t.user_id,t.memory_id,t.source_kind,t.source_id",
    "memory_knowledge_clock": "t.user_id",
    "memory_mutations": "t.user_id,t.sequence",
    "memory_tombstones": "t.user_id,t.memory_id",
    "memory_erased_sources": "t.user_id,t.source_kind,t.source_id,t.memory_id",
}

# Fixed content-only reset scope; arbitrary caller SQL cannot delete protections
# or the account/compute ledger. Revision bundles/segments/sources cascade.
BENCHMARK_RESET_STATEMENTS: tuple[tuple[str, str], ...] = tuple(
    (table, f"DELETE FROM {table} WHERE user_id = $1")
    for table in (
        "retrieval_log",
        "dream_log",
        "entities",
        "memories",
        "memory_dependencies",
        "memory_extraction_log",
        "messages",
        "conversations",
    )
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
    """Hash evaluation inputs and current temporal protection for one owner.

    Equal fingerprints before and after a phase prove the phase did not change
    retrieval or ranking inputs (access counts, timestamps, trust, status).
    Diagnostic-only mutations, clock sequence/time and post-commit observations
    are not evaluation inputs. Revision-bearing mutation identity/lower bounds,
    account permissions, erasure epochs and clock-regression flags remain covered.
    An absent zero-state clock and its first diagnostic publication are equivalent.
    This is not a byte-for-byte recovery hash or a historical-clock certification.
    ``tables`` must be drawn from :data:`FINGERPRINT_TABLES`.
    """
    if not set(tables) <= set(FINGERPRINT_TABLES):
        raise ValueError("fingerprint tables must come from FINGERPRINT_TABLES")
    digests: dict[str, dict[str, Any]] = {}
    for table, owner_column in tables:
        if table == "memory_knowledge_clock":
            relation = (
                "(SELECT u.id AS user_id,"
                "COALESCE(k.account_generation,a.content_generation,0) AS account_generation,"
                "COALESCE(k.erasure_epoch,0) AS erasure_epoch,"
                "COALESCE(k.clock_regressed,FALSE) AS clock_regressed,"
                "EXISTS(SELECT 1 FROM memory_revisions r WHERE r.user_id=u.id "
                "AND r.sequence>COALESCE(k.sequence,0)) AS revision_clock_invalid,"
                "a.content_generation AS current_generation,a.status AS account_status,"
                "EXISTS(SELECT 1 FROM account_deletions d WHERE d.account_id=u.id "
                "AND d.closed_at IS NULL) AS open_operation,"
                "EXISTS(SELECT 1 FROM account_suspension_causes c WHERE c.account_id=u.id) AS suspended "
                "FROM users u LEFT JOIN entitlement_accounts a ON a.user_id=u.id "
                "LEFT JOIN memory_knowledge_clock k ON k.user_id=u.id WHERE u.id=$1) t"
            )
        elif table == "memory_mutations":
            relation = (
                "(SELECT k.user_id,k.sequence,k.account_generation,k.transaction_id,k.write_lower_at "
                "FROM memory_mutations k WHERE k.user_id=$1 AND EXISTS("
                "SELECT 1 FROM memory_revisions r WHERE r.user_id=k.user_id "
                "AND r.sequence=k.sequence)) t"
            )
        elif table == "memory_revision_segments":
            relation = (
                "memory_revision_segments t JOIN memory_revisions r ON r.id=t.revision_id "
                "WHERE r.user_id = $1"
            )
        else:
            relation = f"{table} t WHERE t.{owner_column} = $1"
        key = _FINGERPRINT_KEYS.get(table, "t.id")
        row = await pool.fetchrow(
            f"SELECT count(*) AS rows, "  # nosec B608 - fixed identifiers from FINGERPRINT_TABLES
            f"md5(coalesce(string_agg(md5(t::text), '' ORDER BY {key}), '')) AS digest "
            f"FROM {relation}",
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


async def _benchmark_owner(conn: Any, user_id: uuid.UUID) -> bool:
    """Existing synthetic users only; never adopt an unrelated identity or provision."""
    email = await conn.fetchval("SELECT email FROM users WHERE id=$1", user_id)
    if email is None:
        return False
    if email != f"longmemeval+{user_id.hex}@daemon.test" and not (
        user_id == uuid.UUID("12345678-1234-5678-1234-567812345678")
        and email == "longmemeval@daemon.test"
    ):
        raise BenchmarkDatabaseGuardError("Refusing to reset an unowned benchmark user")
    return True


async def _reset_store(pool: Any, user_id: uuid.UUID) -> MemoryStore | None:
    settings = get_settings()
    await assert_benchmark_database(pool, settings.database_url)
    if not await _benchmark_owner(pool, user_id):
        return None
    return MemoryStore(pool, ContentEncryption(settings.daemon_encryption_key))


async def _erase_synthetic_lineages(store: MemoryStore, conn: Any, owner: uuid.UUID, roots):
    count = await erase(store, conn, owner, roots, hard=True)
    if count == 0:
        # Even an empty-memory reset removes source inputs. Fence prior captures
        # without inventing a lineage, clearing tombstones or resetting the clock.
        await conn.execute(
            "UPDATE memory_knowledge_clock SET erasure_epoch=erasure_epoch+1 WHERE user_id=$1",
            owner,
        )
    return count


async def rollback_orphan_conversations(
    pool: Any, user_id: uuid.UUID, conversation_ids: set[str]
) -> dict[str, int]:
    """Delete everything an interrupted session ingest wrote.

    Original-capture admission and lineage erasure precede synthetic source
    deletion on the same READ COMMITTED connection. Temporal deletion protection
    is retained, not rolled back. Changes to earlier sessions and conservative
    invalidation can make exact restoration unprovable; the caller must rebuild
    the question rather than accept an inexact baseline.
    """
    if not conversation_ids:
        return {"conversations": 0, "memories": 0, "memory_extraction_log": 0}
    ids = [uuid.UUID(value) for value in sorted(conversation_ids)]
    counts: dict[str, int] = {}
    store = await _reset_store(pool, user_id)
    if store is None:
        raise BenchmarkDatabaseGuardError("Orphan rollback requires an existing benchmark owner")
    original = await capture_for(store, user_id)
    async with mutation(store, original) as conn:
        await _benchmark_owner(conn, user_id)
        owned = await conn.fetch(
            "SELECT id FROM conversations WHERE user_id=$1 AND id=ANY($2::uuid[]) ORDER BY id FOR UPDATE",
            user_id,
            ids,
        )
        if {row["id"] for row in owned} != set(ids):
            raise BenchmarkDatabaseGuardError("Orphan conversation ownership changed or is foreign")
        roots = await conn.fetch(
            "SELECT id FROM memories WHERE user_id=$1 AND source_conversation_id=ANY($2::uuid[])",
            user_id,
            ids,
        )
        before = await conn.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", user_id)
        await _erase_synthetic_lineages(store, conn, user_id, [row["id"] for row in roots])
        counts["memories"] = before - await conn.fetchval(
            "SELECT count(*) FROM memories WHERE user_id=$1", user_id
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

    Only the fixed content statements are accepted. Existing lineage erasure
    removes revision payloads and derived copies, including uncascaded edges.
    Clocks/mutations/tombstones/erased spans, the user, account generation and
    compute ledger stay. Counts are returned only after acknowledged commit.
    """
    counts: dict[str, int] = {}
    if tuple(statements) != BENCHMARK_RESET_STATEMENTS:
        raise ValueError("Benchmark reset requires the fixed content-only statements")
    store = await _reset_store(pool, user_id)
    if store is None:
        return {table: 0 for table, _ in statements}
    original = await capture_for(store, user_id)
    async with mutation(store, original) as conn:
        await _benchmark_owner(conn, user_id)
        # Counts describe rows actually removed by this acknowledged owner txn,
        # including lineage erasure's conservative derived-record invalidation.
        for table, _ in statements:
            counts[table] = await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE user_id=$1",
                user_id,
            )
        roots = await conn.fetch("SELECT id FROM memories WHERE user_id=$1", user_id)
        await _erase_synthetic_lineages(store, conn, user_id, [row["id"] for row in roots])
        for _, statement in statements:
            await conn.execute(statement, user_id)
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
