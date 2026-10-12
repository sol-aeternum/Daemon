"""Unit tests for bounded identity retention without PostgreSQL.

Validates the ARQ registration/audit envelope of ``cleanup_identity_records``
and the argument-validation contract of ``sweep_identity_records`` against
recording fake connections. Real PostgreSQL scenarios (including end-to-end
retention boundaries and claim lock races) live in
``tests/test_identity_retention_postgres.py``.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from arq.connections import ArqRedis
from arq.constants import result_key_prefix
from arq.jobs import deserialize_result, serialize_result
from arq.worker import func

from orchestrator.worker import audit, jobs
from orchestrator.worker.worker import worker as audited_worker

from orchestrator.services.identity import retention
from orchestrator.services.identity.retention import sweep_identity_records


def _next_now() -> datetime:
    return datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


class _RefusingConn:
    """Connection stand-in that fails any statement the sweeper would issue."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, query: str, *args: object) -> str:
        self.statements.append(query)
        raise AssertionError(f"unexpected statement: {query[:80]!r}")

    async def fetchval(self, query: str, *args: object) -> object:
        self.statements.append(query)
        raise AssertionError(f"unexpected statement: {query[:80]!r}")

    @asynccontextmanager
    async def transaction(self):
        raise AssertionError("no transaction should open during validation")
        yield None  # pragma: no cover - never reached


# ---------------------------------------------------------------------------
# Worker registration and audit envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_identity_records_requires_database_pool() -> None:
    with pytest.raises(RuntimeError, match="identity retention database unavailable"):
        await jobs.cleanup_identity_records({"db_pool": None})


@pytest.mark.asyncio
async def test_whole_job_timeout_cancels_blocked_pool_acquisition(monkeypatch) -> None:
    real_timeout = asyncio.timeout
    requested: list[float] = []
    cancelled = asyncio.Event()

    def accelerated_timeout(duration: float):
        requested.append(duration)
        return real_timeout(0.01)

    class BlockedPool:
        @asynccontextmanager
        async def acquire(self):
            try:
                await asyncio.Future()
                yield None
            finally:
                cancelled.set()

    monkeypatch.setattr(jobs.asyncio, "timeout", accelerated_timeout)
    with pytest.raises(TimeoutError):
        await jobs.cleanup_identity_records(cast(Any, {"db_pool": BlockedPool()}))
    assert requested == [30]
    assert cancelled.is_set()


def test_cleanup_identity_records_registered_with_bounded_profile() -> None:
    registered = audited_worker.functions["cleanup_identity_records"]
    assert registered.max_tries == 1
    assert registered.timeout_s == 30
    # The registration keeps no result itself (None falls back to the worker
    # default); the cron entry carries the explicit 3600 s counts envelope.
    assert registered.keep_result_s is None


def test_cleanup_identity_records_cron_scheduled() -> None:
    entries = [
        cron for cron in audited_worker.cron_jobs if cron.name == "cron:cleanup_identity_records"
    ]
    assert len(entries) == 1
    assert entries[0].minute == 20
    assert entries[0].keep_result_s == 3600


def test_shared_counts_inventory_matches_retention_tables() -> None:
    counted = audit._SHARED_COUNTS["cleanup_identity_records"]
    assert counted == frozenset(table for table, _, _, _ in retention._TABLES)


def test_retention_job_takes_no_clock_or_configuration_inputs() -> None:
    """The ARQ job surface accepts only the worker context; neither a clock
    nor retention tuning reaches the job as a runtime input."""
    parameters = inspect.signature(jobs.cleanup_identity_records).parameters
    assert set(parameters) == {"ctx"}
    sweep_parameters = inspect.signature(sweep_identity_records).parameters
    assert set(sweep_parameters) == {"conn", "now", "limit"}


# ---------------------------------------------------------------------------
# Offline worker envelope (convention mirrors test_worker_failure_audit.py)
# ---------------------------------------------------------------------------


class _OfflinePipeline:
    def __init__(
        self,
        transactions: list[list[tuple[str, tuple[Any, ...], dict[str, Any]]]],
    ) -> None:
        self.commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._transactions = transactions

    async def __aenter__(self) -> _OfflinePipeline:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.commands.append((name, args, kwargs))

    def set(self, *args: object, **kwargs: object) -> None:
        self._record("set", *args, **kwargs)

    def delete(self, *args: object) -> None:
        self._record("delete", *args)

    def zrem(self, *args: object) -> None:
        self._record("zrem", *args)

    def zincrby(self, *args: object) -> None:
        self._record("zincrby", *args)

    def pexpire(self, *args: object) -> None:
        self._record("pexpire", *args)

    def get(self, *args: object) -> None:
        self._record("get", *args)

    def incr(self, *args: object) -> None:
        self._record("incr", *args)

    def expire(self, *args: object) -> None:
        self._record("expire", *args)

    async def execute(self) -> list[object]:
        self._transactions.append(list(self.commands))
        if self.commands and self.commands[0][0] == "get":
            values: list[object] = [b"", 1, True]
            if len(self.commands) == 4:
                values.append(False)
            return values
        return []


def _terminal_counts_payload(
    counts: dict[str, int],
    *,
    polluted: dict[str, Any],
) -> bytes:
    data = serialize_result(
        function="cron:cleanup_identity_records",
        args=(),
        kwargs={},
        job_try=1,
        enqueue_time_ms=1_700_000_000_000,
        success=True,
        result={**counts, **polluted},
        start_ms=1_700_000_000_100,
        finished_ms=1_700_000_000_200,
        ref="cron:cleanup_identity_records",
        queue_name="arq:queue",
        job_id="cron:cleanup_identity_records:1700000000000",
    )
    assert data is not None
    return data


@pytest.mark.asyncio
async def test_identity_retention_result_persists_table_counts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared counts-only envelope publishes the four per-table counts and
    drops every other field without leaking their serialized form."""

    transactions: list[list[tuple[str, tuple[Any, ...], dict[str, Any]]]] = []
    audited: list[bytes | None] = []
    queue = ArqRedis()

    def pipeline(*, transaction: bool) -> _OfflinePipeline:
        assert transaction
        return _OfflinePipeline(transactions)

    async def capture_audit(
        ctx: object, data: bytes | None, *, deserializer: object = None
    ) -> None:
        assert transactions, "audit must follow the persisted result set"
        audited.append(data)

    async def noop(ctx: object, *args: object, **kwargs: object) -> str:
        return "private generated result"

    monkeypatch.setattr(queue, "pipeline", pipeline)
    monkeypatch.setattr(audit, "audit_worker_job_result", capture_audit)

    instance = audit.AuditedWorker(
        functions=[func(noop, name="cleanup_identity_records", keep_result=3600, max_tries=1)],
        redis_pool=queue,
        handle_signals=False,
        allow_abort_jobs=True,
    )

    counts = {table: 7 for table in audit._SHARED_COUNTS["cleanup_identity_records"]}
    original = _terminal_counts_payload(
        counts,
        polluted={
            "errors": ["private error text"],
            "status": "error",
            "user_id": "private account ID",
            "new_unclassified_field": "private content",
        },
    )
    await instance.finish_job(
        "cron:cleanup_identity_records:1700000000000",
        True,
        original,
        3600,
        False,
        None,
        60,
    )

    sets = [
        command
        for command in transactions[-1]
        if command[0] == "set" and str(command[1][0]).startswith(result_key_prefix)
    ]
    assert len(sets) == 1
    stored = sets[0][1][1]
    decoded = deserialize_result(stored)
    assert decoded.result == counts
    assert b"private" not in stored
    assert audited == [original]


# ---------------------------------------------------------------------------
# sweep_identity_records argument validation (no database work)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 1001, 10**6])
async def test_sweep_rejects_out_of_range_batch_limits(limit: int) -> None:
    conn = _RefusingConn()
    with pytest.raises(ValueError, match="identity retention batch"):
        await sweep_identity_records(cast(Any, conn), now=_next_now(), limit=limit)
    assert not conn.statements


@pytest.mark.asyncio
async def test_sweep_rejects_naive_clock_without_touching_the_database() -> None:
    conn = _RefusingConn()
    with pytest.raises(ValueError, match="timezone-aware"):
        await sweep_identity_records(cast(Any, conn), now=datetime(2026, 10, 12, 12, 0, 0))
    assert not conn.statements


@pytest.mark.asyncio
async def test_sweep_rounds_through_one_transaction_per_table() -> None:
    """Nothing deletable still yields one count per retention table in its own
    bounded transaction (statement/lock timeouts present per invocation)."""

    class _CountingConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, query: str, *args: object) -> str:
            self.statements.append(query)
            return "SET"

        async def fetchval(self, query: str, *args: object) -> object:
            self.statements.append(query)
            return 0

        @asynccontextmanager
        async def transaction(self):
            yield self

    conn = _CountingConn()
    counts = await sweep_identity_records(
        cast(Any, conn), now=_next_now(), limit=retention.MAX_BATCH_SIZE
    )

    assert counts == {
        table: 0
        for table in (
            "identity_audit_log",
            "email_challenges",
            "google_nonce_challenges",
            "signup_invites",
        )
    }
    assert sum(1 for s in conn.statements if "DELETE FROM" in s) == 4
    assert conn.statements.count("SET LOCAL statement_timeout = '5s'") == 4
    assert conn.statements.count("SET LOCAL lock_timeout = '1s'") == 4


def test_retention_windows_are_fixed_absolute_deltas() -> None:
    assert retention.TERMINAL_RETENTION == timedelta(days=7)
    assert retention.AUDIT_RETENTION == timedelta(days=90)
    assert retention.MAX_BATCH_SIZE == 1000


def test_asyncio_timeout_is_used_for_the_whole_job_bound() -> None:
    # The bounded whole-job timeout is a source property; registration above
    # pins arq's own timeout. This guard keeps the import contract explicit.
    source = inspect.getsource(jobs.cleanup_identity_records)
    assert "asyncio.timeout(30)" in source
    assert hasattr(asyncio, "timeout")
