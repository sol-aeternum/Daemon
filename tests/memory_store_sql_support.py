"""Bounded account/revision SQL protocol for MemoryStore business-logic doubles.

The production capture and mutation guards execute unchanged. This scripts their
SQL contract, not PostgreSQL locks/triggers or durable visibility qualification.
Business responses remain separately observable on the supplied executor.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def normalized(sql: str) -> str:
    return " ".join(sql.split())


def validate_write(sql: str, args: tuple) -> None:
    q = normalized(sql)
    if q.startswith("INSERT INTO retrieval_log"):
        assert len(args) in (12, 13) and "RETURNING *" in q, q
        assert "$6::uuid[]" in q and "$7::jsonb" in q and "$8::uuid[]" in q, q
        assert ("query_role" in q) == (len(args) == 13), q
        return
    if q.startswith("INSERT INTO memories"):
        assert len(args) in (12, 18), q
        assert "content_hash" in q and "content_tsv" in q, q
        if len(args) == 18:
            assert "ON CONFLICT DO NOTHING RETURNING *" in q, q
            assert "temporal_scope_hash" in q and "provenance_unknown" in q, q
        else:
            assert "to_tsvector('english', $12)" in q, q
        return
    assert q.startswith("UPDATE memories SET "), q
    if q.startswith("UPDATE memories SET content = $2,"):
        assert len(args) == 18 and "RETURNING *" in q, q
        assert "head_revision_id=$13" in q and "user_id = $10" in q, q
        assert "content_hash IS NOT DISTINCT FROM $12" in q and "status!='deleted'" in q, q
    elif q.startswith("UPDATE memories SET metadata = COALESCE"):
        assert len(args) == 3 and "|| $2::jsonb" in q, q
        assert "head_revision_id=$3" in q and "status!='deleted'" in q, q
    elif q.startswith("UPDATE memories SET status = $2,"):
        assert len(args) == 3 and "content_hash = $3" in q and "WHERE id = $1" in q, q
    elif q.startswith("UPDATE memories SET content_hash = $2,"):
        assert len(args) == 2 and "WHERE id = $1 AND content_hash IS NULL" in q, q
    else:
        assert q.startswith("UPDATE memories SET valid_to = NOW(),"), q
        assert "WHERE id = $1" in q and "valid_to IS NULL" in q, q
        assert len(args) in (1, 2), q


class RegisteredStorePool:
    def __init__(self, executor: Any):
        self.executor = executor
        self.owner = UUID(int=100)
        self.clocks: dict[UUID, dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.xid = 0
        self.events: list[str] = []

    @asynccontextmanager
    async def acquire(self):
        yield RegisteredStoreConnection(self, self.executor)

    def connection(self, executor: Any):
        return RegisteredStoreConnection(self, executor)

    async def fetchrow(self, sql, *args):
        q = normalized(sql)
        if q == "SELECT status, content_generation FROM entitlement_accounts WHERE user_id=$1":
            assert len(args) == 1 and isinstance(args[0], UUID)
            return {"status": "active", "content_generation": 0}
        assert q.startswith(("SELECT ", "WITH ")), q
        return await self.executor.fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        q = normalized(sql)
        if q == "SELECT erasure_epoch FROM memory_knowledge_clock WHERE user_id=$1":
            return self.clocks.get(args[0], {}).get("erasure_epoch", 0)
        if q == "SELECT user_id FROM memories WHERE id=$1":
            assert len(args) == 1 and isinstance(args[0], UUID)
            return self.owner
        raise AssertionError(q)

    async def fetch(self, sql, *args):
        assert normalized(sql).startswith(("SELECT ", "WITH ")), sql
        return await self.executor.fetch(sql, *args)

    async def execute(self, sql, *args):
        # Hash backfill is maintenance, not an assertion. All semantic DML must
        # reach the registered connection, never this pool/autocommit surface.
        q = normalized(sql)
        assert q.startswith("UPDATE memories SET content_hash = $2,"), q
        validate_write(sql, args)
        return await self.executor.execute(sql, *args)


class RegisteredStoreConnection:
    def __init__(self, pool: RegisteredStorePool, executor: Any):
        self.pool, self.executor = pool, executor
        self.active = False
        self.registered = False
        self.xid = 0
        self.mutation_sequence = None
        self.transaction_entries = 0

    def is_in_transaction(self):
        return self.active

    @asynccontextmanager
    async def transaction(self, *, isolation="read_committed"):
        assert isolation == "read_committed" and not self.active
        async with self.pool.lock:
            before = deepcopy(self.pool.clocks)
            self.pool.xid += 1
            self.xid = self.pool.xid
            self.transaction_entries += 1
            self.active = True
            self.pool.events.append("BEGIN")
            try:
                yield self
            except BaseException:
                self.pool.clocks = before
                self.pool.events.append("ROLLBACK")
                raise
            else:
                self.pool.events.append("COMMIT")
            finally:
                self.active = self.registered = False
                self.mutation_sequence = None

    async def fetchrow(self, sql, *args):
        assert self.active
        q = normalized(sql)
        if (
            q
            == "SELECT status, content_generation FROM entitlement_accounts WHERE user_id=$1 FOR SHARE"
        ):
            return await self.pool.fetchrow(q.removesuffix(" FOR SHARE"), *args)
        if q.startswith("SELECT status, content_generation FROM entitlement_accounts"):
            return await self.pool.fetchrow(sql, *args)
        if "FROM account_deletions" in q:
            assert "AS operation" in q and "AS cause" in q and "account_suspension_causes" in q
            return {"operation": False, "cause": False}
        if q in (
            "SELECT * FROM memory_knowledge_clock WHERE user_id=$1 FOR UPDATE NOWAIT",
            "SELECT *,clock_timestamp() AS observed_at FROM memory_knowledge_clock WHERE user_id=$1 FOR UPDATE NOWAIT",
        ):
            row = self.pool.clocks.get(args[0])
            return {**row, "observed_at": datetime.now(UTC)} if row else None
        if q.startswith(("INSERT ", "UPDATE ")):
            assert self.registered
            validate_write(sql, args)
        else:
            assert q.startswith("SELECT ") and "FROM memories" in q, q
        return await self.executor.fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        assert self.active
        q = normalized(sql)
        if q == "SELECT txid_current()":
            return self.xid
        if q == "SHOW transaction_isolation":
            return "read committed"
        if q == "SELECT clock_timestamp()":
            return datetime.now(UTC)
        if q == "SELECT pg_try_advisory_xact_lock(601065,hashtext($1))":
            assert len(args) == 1
            return True
        if (
            q
            == "SELECT sequence FROM memory_mutations WHERE user_id=$1 AND transaction_id=pg_current_xact_id()"
        ):
            return self.mutation_sequence
        if q.startswith("UPDATE memory_knowledge_clock SET sequence=sequence+1,"):
            assert "WHERE user_id=$1 RETURNING sequence" in q and len(args) == 3
            clock = self.pool.clocks[args[0]]
            clock.update(
                sequence=clock["sequence"] + 1, last_clock_at=args[1], clock_regressed=args[2]
            )
            return clock["sequence"]
        assert self.registered and "RETURNING id" in q
        validate_write(sql, args)
        return await self.executor.fetchval(sql, *args)

    async def execute(self, sql, *args):
        assert self.active
        q = normalized(sql)
        if q == "SELECT pg_advisory_xact_lock(601065,hashtext($1))":
            assert len(args) == 1
            return "SELECT 1"
        if q.startswith(
            "INSERT INTO memory_knowledge_clock(user_id,account_generation,last_clock_at)"
        ):
            assert "ON CONFLICT DO NOTHING" in q and len(args) == 3
            self.pool.clocks.setdefault(
                args[0],
                {
                    "account_generation": args[1],
                    "erasure_epoch": 0,
                    "last_clock_at": args[2],
                    "sequence": 0,
                    "clock_regressed": False,
                },
            )
            return "INSERT 0 1"
        if q.startswith("UPDATE memory_mutations SET observed_committed_by=$2 "):
            assert (
                "transaction_id<>pg_current_xact_id()" in q
                and "write_lower_at<=$2" in q
                and len(args) == 2
            )
            return "UPDATE 0"
        if q.startswith(
            "INSERT INTO memory_mutations(user_id,sequence,account_generation,transaction_id,write_lower_at)"
        ):
            assert len(args) == 4 and args[1] == self.pool.clocks[args[0]]["sequence"]
            self.mutation_sequence = args[1]
            self.registered = True
            return "INSERT 0 1"
        if q.startswith(
            "UPDATE memory_knowledge_clock SET last_clock_at=GREATEST(last_clock_at,$2),"
        ):
            assert "WHERE user_id=$1" in q and len(args) == 3
            self.pool.clocks[args[0]].update(last_clock_at=args[1], clock_regressed=args[2])
            return "UPDATE 1"
        assert self.registered
        validate_write(sql, args)
        return await self.executor.execute(sql, *args)
