"""Summary store seam under J1c original-generation fencing.

Legacy SQL-shape regressions (streaming holes, terminal rows, snapshot bounds,
atomic summary persistence) are preserved and extended onto the consolidated
guarded input read plus the guarded continuation helpers. The guard seam is
scripted against the real ``guard_content_write`` requirements instead of a
bare pool mock; the disposable-primary PostgreSQL suite proves the same seam
against real databases.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.memory import summary_provenance as sp
from orchestrator.memory.revisions import Capture
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal


class _IdentityEncryption:
    def decrypt(self, value: str) -> str:
        return value


@pytest.mark.asyncio
async def test_summary_batch_excludes_streaming_messages_and_applies_offset() -> None:
    conversation_id = uuid4()
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, _IdentityEncryption())

    assert await store.get_summary_message_batch(conversation_id, offset=12, limit=100) == []

    query, actual_id, limit, offset = pool.fetch.await_args.args
    assert actual_id == conversation_id
    assert limit == 100
    assert offset == 12
    normalized = " ".join(query.split())
    assert "status IS NULL OR status = 'complete'" in normalized
    # Cursor positions count only summary-eligible rows so skipped terminal
    # rows cannot shift the persisted finalized-message offset.
    assert "COUNT(*) FILTER ( WHERE status IS NULL OR status = 'complete' ) OVER (" in normalized
    assert "ORDER BY created_at ASC, id ASC" in normalized
    assert "contiguous_summary_rn" in normalized


@pytest.mark.asyncio
async def test_summary_batch_stops_at_first_mutable_row_but_skips_terminal_rows() -> None:
    """Codex P2 on PR #165, store.py:464 — finalized rows after a
    streaming row must not be returned in the same batch, while terminal
    error/cancelled rows must neither contribute content nor pin later
    finalized rows.

    Regression: raw-row offsets shift when terminal rows are filtered out.
    The ranked CTE must instead compute a finalized-only cursor while still
    retaining every row for the mutable-boundary calculation.
    """
    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, _IdentityEncryption())

    assert (
        await store.get_summary_message_batch(
            conversation_id,
            offset=0,
            limit=100,
            snapshot_at=snapshot,
        )
        == []
    )

    query, _actual_id, _limit, _offset, _snapshot_arg = pool.fetch.await_args.args
    normalized = " ".join(query.split())
    # The cutoff is expressed in finalized-row space. Terminal rows are
    # explicitly non-blocking; streaming and unknown states stay conservative.
    assert "COALESCE" in normalized
    assert "MIN(summary_rn) FILTER" in normalized
    assert "status NOT IN ('complete', 'error', 'cancelled')" in normalized
    # The outer SELECT applies the finalized-space offset and boundary.
    assert "r.summary_rn > $3" in normalized
    assert "r.summary_rn <= (SELECT contiguous_summary_rn FROM cutoff)" in normalized

    # Regression guard for the ranked-CTE bug: the ``ranked`` CTE must
    # inspect ALL conversation rows at-or-before the snapshot (regardless
    # of status), so the ``cutoff`` CTE can find mutable rows
    # that would otherwise be hidden by ``base_filter``. The outer
    # SELECT then filters to finalized rows within the contiguous
    # prefix.
    #
    # The fix is verified by inspecting the CTE structure: the ``ranked``
    # CTE's row source must use only conversation/snapshot bounds; status is
    # used by the windowed finalized counter, not to filter rows out.
    ranked_section, outer_section = _split_ctes(normalized)
    assert "messages" in ranked_section
    assert "FROM messages WHERE conversation_id = $1 AND created_at <= $4" in ranked_section
    # base_filter MUST appear in the outer SELECT (filter finalized-only)
    assert "m.status IS NULL OR m.status = 'complete'" in outer_section, (
        "outer SELECT must filter to finalized-only rows"
    )


def _split_ctes(normalized_query: str) -> tuple[str, str]:
    """Split a normalized SQL query into (ranked_section, after_ranked_section).

    The CTE list is everything between the outermost WITH and its matching
    SELECT. We split between the end of the ``ranked`` CTE definition and the
    next CTE (``cutoff``) or the outer SELECT. This is a deliberately
    permissive parser — we only care that ``ranked`` is a well-bounded
    substring we can search.
    """
    upper = normalized_query.upper()
    with_idx = upper.find("WITH ")
    ranked_idx = upper.find("RANKED AS")
    cutoff_idx = upper.find("CUTOFF AS", ranked_idx)
    if with_idx == -1 or ranked_idx == -1:
        raise AssertionError(f"Could not locate WITH/ranked in query: {normalized_query!r}")
    if cutoff_idx == -1:
        # No cutoff CTE — the entire body after ranked is the outer SELECT.
        return normalized_query[with_idx:ranked_idx], normalized_query[ranked_idx:]
    return normalized_query[with_idx:cutoff_idx], normalized_query[cutoff_idx:]


@pytest.mark.asyncio
async def test_summary_batch_pins_snapshot_upper_bound() -> None:
    """``snapshot_at`` must filter out rows that finalized after the snapshot."""
    from datetime import datetime, timezone

    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, _IdentityEncryption())

    assert (
        await store.get_summary_message_batch(
            conversation_id,
            offset=0,
            limit=20,
            snapshot_at=snapshot,
        )
        == []
    )

    query, actual_id, limit, offset, snapshot_arg = pool.fetch.await_args.args
    assert actual_id == conversation_id
    assert limit == 20
    assert offset == 0
    assert snapshot_arg == snapshot
    assert "created_at <= $4" in " ".join(query.split())


@pytest.mark.asyncio
async def test_count_summary_messages_at_applies_snapshot_bound() -> None:
    from datetime import datetime, timezone

    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"count": 7}
    store = object.__new__(MemoryStore)
    store._pool = pool

    assert await store.count_summary_messages_at(conversation_id, snapshot_at=snapshot) == 7

    query, actual_id, snapshot_arg = pool.fetchrow.await_args.args
    assert actual_id == conversation_id
    assert snapshot_arg == snapshot
    assert "created_at <= $2" in " ".join(query.split())


@pytest.mark.asyncio
async def test_update_conversation_summary_persists_baseline_atomically(monkeypatch) -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    conn = _SeamConn()
    conn.update_row = {"id": conversation_id}
    store = _bound_store(_SeamPool(conn))

    captured = CapturedGeneration(uuid4(), 7)
    frozen = _snapshot(conversation_id, captured, claimed=42)
    publication = AsyncMock(return_value=True)
    monkeypatch.setattr(sp, "publish", publication)
    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_memory_erasure_epoch=0,
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
        captured_generation=captured,
        summary_snapshot=frozen,
    )

    assert updated is True
    publication.assert_awaited_once_with(store, frozen, "summary", continuation=False)
    # Real atomic closure/head/cursor/rollback assertions live in the owned-PG
    # summary provenance and account-guard suites, not this adapter double.


@pytest.mark.asyncio
async def test_update_conversation_summary_persists_snapshot_timestamp(monkeypatch) -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    conn = _SeamConn()
    conn.update_row = {"id": conversation_id}
    store = _bound_store(_SeamPool(conn))
    captured = CapturedGeneration(uuid4(), 7)
    frozen = _snapshot(conversation_id, captured, claimed=42, at=snapshot)
    publication = AsyncMock(return_value=True)
    monkeypatch.setattr(sp, "publish", publication)

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_memory_erasure_epoch=0,
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
        summary_snapshot_at=snapshot,
        captured_generation=captured,
        summary_snapshot=frozen,
    )

    assert updated is True
    assert publication.await_args is not None
    assert publication.await_args.args[1].snapshot_at == snapshot


def _snapshot(conversation, origin, *, claimed=0, at=None):
    return sp.SummarySnapshot(
        Capture(origin, 0),
        sp.SummaryReference(conversation, None, 0, ()),
        None,
        "{}",
        (),
        (),
        0,
        claimed,
        at or datetime.now(timezone.utc),
        None,
    )


@pytest.mark.asyncio
async def test_count_contiguous_prefix_returns_total_when_all_finalized() -> None:
    """Codex P2 on PR #165, store.py:566 — when every row at the snapshot
    is finalized, the prefix must span the whole snapshot rather than
    returning zero.
    """
    conversation_id = uuid4()
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"prefix_count": 12}
    store = object.__new__(MemoryStore)
    store._pool = pool

    assert (
        await store.count_contiguous_finalized_messages_at(conversation_id, snapshot_at=snapshot)
        == 12
    )

    query, _actual_id, snapshot_arg = pool.fetchrow.await_args.args
    assert snapshot_arg == snapshot
    normalized_query = " ".join(query.split())
    # COALESCE fallback to total count is the regression target.
    assert "COALESCE" in normalized_query
    assert "MAX(summary_rn)" in normalized_query
    assert "FILTER" in normalized_query
    assert "status NOT IN ('complete', 'error', 'cancelled')" in normalized_query


# ---------------------------------------------------------------------------
# Consolidated guarded reads and continuation helpers (J1c)
# ---------------------------------------------------------------------------


class _SeamTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _SeamConn:
    """Scripted READ COMMITTED connection accepted by ``guard_content_write``.

    Handlers are selected by normalized-SQL substring; unexpected statements
    fail the test instead of silently returning mock rows.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.calls: dict[str, tuple[str, tuple]] = {}
        self.account = {"status": "active", "content_generation": 7}
        self.control_state = {"operation": False, "cause": False}
        self.isolation = "read committed"
        self.xid = 424242
        self.conversation_row: Any = None
        self.stats_row: Any = None
        self.exists_result: Any = None
        self.pending_flag: Any = None
        self.update_row: Any = None

    def is_in_transaction(self) -> bool:
        self.events.append(("conn", "is_in_transaction"))
        return True

    def transaction(self, isolation: str | None = None) -> _SeamTransaction:
        assert isolation == "read_committed"
        return _SeamTransaction()

    async def fetchval(self, query: str, *args: Any) -> Any:
        upper = " ".join(query.split()).upper()
        if "ERASURE_EPOCH" in upper:
            self._record("fetchval", "erasure_epoch", query, args)
            return 0
        if "CLOCK_TIMESTAMP()" in upper:
            return datetime(2026, 1, 1, tzinfo=timezone.utc)
        if "SELECT SEQUENCE FROM MEMORY_MUTATIONS" in upper:
            return 1
        if "TXID_CURRENT" in upper:
            self._record("fetchval", "txid", query, args)
            return self.xid
        if "TRANSACTION_ISOLATION" in upper:
            self._record("fetchval", "isolation", query, args)
            return self.isolation
        if upper.startswith("SELECT EXISTS"):
            self._record("fetchval", "exists", query, args)
            return self.exists_result
        if "SUMMARY_CONTINUATION_PENDING" in upper:
            self._record("fetchval", "pending_flag", query, args)
            return self.pending_flag
        raise AssertionError(f"unexpected fetchval: {query!r}")

    async def fetchrow(self, query: str, *args: Any) -> Any:
        upper = " ".join(query.split()).upper()
        if "FROM MEMORY_KNOWLEDGE_CLOCK" in upper:
            return {
                "account_generation": 7,
                "erasure_epoch": 0,
                "last_clock_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        if "ENTITLEMENT_ACCOUNTS" in upper:
            self._record("fetchrow", "account_for_share", query, args)
            return dict(self.account)
        if "ACCOUNT_DELETIONS" in upper:
            self._record("fetchrow", "control_state", query, args)
            return dict(self.control_state)
        if upper.startswith("SELECT C.*") or (
            upper.startswith("SELECT ") and "FROM CONVERSATIONS" in upper
        ):
            self._record("fetchrow", "conversation", query, args)
            return (
                {
                    "summary_head_version_id": None,
                    "summary_state_revision": 0,
                    **self.conversation_row,
                }
                if self.conversation_row
                else None
            )
        if upper.startswith("WITH RANKED"):
            self._record("fetchrow", "stats", query, args)
            return self.stats_row
        if upper.startswith("UPDATE CONVERSATIONS"):
            self._record("fetchrow", "update", query, args)
            return self.update_row
        raise AssertionError(f"unexpected fetchrow: {query!r}")

    async def execute(self, query: str, *args: Any) -> str:
        assert any(
            token in query
            for token in (
                "pg_advisory_xact_lock",
                "INSERT INTO memory_knowledge_clock",
                "UPDATE memory_mutations",
            )
        ), query
        self._record("execute", "revision_registration", query, args)
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list:
        if "FROM memory_erased_sources" in query:
            self._record("fetch", "erased_sources", query, args)
            return []
        if "FROM messages" in query:
            self._record("fetch", "sources", query, args)
            result = []
            for message in (self.stats_row or {}).get("messages", []):
                item = {
                    "status": "complete",
                    "source_revision": 1,
                    "user_id": args[0],
                    "conversation_id": args[1],
                    **message,
                }
                item["id"] = UUID(str(item["id"]))
                if isinstance(item["created_at"], str):
                    item["created_at"] = datetime.fromisoformat(item["created_at"])
                result.append(item)
            return result
        raise AssertionError(f"unexpected fetch: {query!r}")

    def _record(self, kind: str, label: str, query: str, args: tuple) -> None:
        event = (kind, label)
        self.events.append(event)
        self.calls[label] = (query, args)


class _SeamPool:
    def __init__(self, conn: _SeamConn) -> None:
        self._conn = conn
        self.events = conn.events

    def acquire(self) -> Any:
        @asynccontextmanager
        async def _lease():
            yield self._conn

        return _lease()


class _SeamEncryption:
    def __init__(self, log: list[tuple[str, str]]) -> None:
        self._log = log

    def decrypt(self, value: str) -> str:
        self._log.append(("crypto", "decrypt"))
        return value

    def encrypt(self, value: str) -> str:
        self._log.append(("crypto", "encrypt"))
        return value


def _bound_store(pool: _SeamPool) -> MemoryStore:
    store = object.__new__(MemoryStore)
    store._pool = cast(Any, pool)
    store._enc = cast(Any, _SeamEncryption(pool.events))
    return store


@pytest.mark.asyncio
async def test_read_conversation_summary_inputs_decrypts_after_the_guard() -> None:
    """The account capture is validated inside the caller's transaction
    before any conversation row is read, and message content is decrypted
    only after the guard granted the snapshot (owner reads before decrypt).
    """
    conn = _SeamConn()
    conversation_id, owner = uuid4(), uuid4()
    capture = CapturedGeneration(owner, 7)
    conn.conversation_row = {
        "id": conversation_id,
        "user_id": owner,
        "summary": "prior",
        "summary_updated_at": None,
        "metadata": '{"last_summarized_msg_count": 2}',
        "current_message_count": 3,
    }
    conn.stats_row = {
        "finalized_count": 3,
        "contiguous_count": 3,
        "has_hole": False,
        "messages": [
            {
                "id": str(uuid4()),
                "role": "user",
                "content": "ciphertext",
                "created_at": "2000-01-01T00:00:00+00:00",
            }
        ],
    }
    store = _bound_store(_SeamPool(conn))

    result = await store.read_conversation_summary_inputs(
        conversation_id, captured_generation=capture, limit=20
    )

    assert result is not None
    assert result["persisted_baseline"] == 0  # Legacy text is not a qualified parent.
    assert result["summary"] is None
    assert result["current_message_count"] == 1
    assert result["finalized_count"] == 1
    assert result["contiguous_count"] == 1
    assert result["has_hole"] is False
    assert result["snapshot_at"].tzinfo is timezone.utc
    assert result["messages"][0]["content"] == "ciphertext"
    assert result["summary_snapshot"].rendered[0].source_revision == 1
    query, args = conn.calls["sources"]
    assert args[:2] == (owner, conversation_id)
    assert "source_revision" in query
    # Guard acquisition precedes both the conversation read and decryption.
    assert conn.events.index(("fetchrow", "account_for_share")) < conn.events.index(
        ("fetchrow", "conversation")
    )
    assert conn.events.index(("fetchrow", "account_for_share")) < conn.events.index(
        ("crypto", "decrypt")
    )


@pytest.mark.asyncio
async def test_read_conversation_summary_inputs_clamps_the_requested_limit() -> None:
    conversation_id, owner = uuid4(), uuid4()
    capture = CapturedGeneration(owner, 7)

    def _prepared(conn: _SeamConn) -> None:
        conn.conversation_row = {
            "id": conversation_id,
            "user_id": owner,
            "summary": None,
            "summary_updated_at": None,
            "metadata": "{}",
            "current_message_count": 0,
        }
        conn.stats_row = {
            "finalized_count": 0,
            "contiguous_count": 0,
            "has_hole": False,
            "messages": [
                {
                    "id": str(uuid4()),
                    "role": "user",
                    "content": "fiction",
                    "created_at": "2000-01-01T00:00:00+00:00",
                }
                for _ in range(101)
            ],
        }

    conn = _SeamConn()
    _prepared(conn)
    store = _bound_store(_SeamPool(conn))
    result = await store.read_conversation_summary_inputs(
        conversation_id, captured_generation=capture, limit=1000
    )
    assert result is not None
    assert len(result["messages"]) == 100

    conn = _SeamConn()
    _prepared(conn)
    store = _bound_store(_SeamPool(conn))
    result = await store.read_conversation_summary_inputs(
        conversation_id, captured_generation=capture, limit=0
    )
    assert result is not None
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_read_conversation_summary_inputs_returns_none_without_touching_inputs() -> None:
    conversation_id, owner = uuid4(), uuid4()
    conn = _SeamConn()
    conn.conversation_row = None
    store = _bound_store(_SeamPool(conn))

    result = await store.read_conversation_summary_inputs(
        conversation_id, captured_generation=CapturedGeneration(owner, 7), limit=20
    )

    assert result is None
    assert ("crypto", "decrypt") not in conn.events
    assert ("fetchrow", "stats") not in conn.events


@pytest.mark.asyncio
async def test_read_conversation_summary_inputs_refuses_stale_capture_before_reads() -> None:
    """A generation advanced between capture and guard acquisition refuses
    before any conversation/message content is read or decrypted."""
    conversation_id, owner = uuid4(), uuid4()
    conn = _SeamConn()
    conn.account = {"status": "active", "content_generation": 8}
    store = _bound_store(_SeamPool(conn))

    with pytest.raises(WriteGuardRefusal) as excinfo:
        await store.read_conversation_summary_inputs(
            conversation_id, captured_generation=CapturedGeneration(owner, 7), limit=20
        )

    assert excinfo.value.code == "stale_generation"
    assert ("fetchrow", "conversation") not in conn.events
    assert ("crypto", "decrypt") not in conn.events


@pytest.mark.asyncio
async def test_check_conversation_summary_generation_pins_owner_and_version(monkeypatch) -> None:
    conversation_id, owner = uuid4(), uuid4()
    expected_time = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    conn = _SeamConn()
    conn.exists_result = True
    store = _bound_store(_SeamPool(conn))
    capture = CapturedGeneration(owner, 7)
    frozen = _snapshot(conversation_id, capture)
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(sp, "check", check)

    valid = await store.check_conversation_summary_generation(
        conversation_id,
        captured_generation=capture,
        expected_summary_updated_at=expected_time,
        summary_snapshot=frozen,
    )

    assert valid is True
    check.assert_awaited_once_with(store, frozen)

    check.return_value = False
    assert (
        await store.check_conversation_summary_generation(
            conversation_id,
            captured_generation=CapturedGeneration(owner, 7),
            expected_summary_updated_at=None,
            summary_snapshot=frozen,
        )
        is False
    )


@pytest.mark.asyncio
async def test_read_summary_continuation_pending_reads_the_flag_without_clearing() -> None:
    conversation_id, owner = uuid4(), uuid4()
    capture = CapturedGeneration(owner, 7)
    conn = _SeamConn()
    conn.pending_flag = True
    store = _bound_store(_SeamPool(conn))

    assert await store.read_summary_continuation_pending(
        conversation_id, captured_generation=capture
    )
    query, args = conn.calls["pending_flag"]
    normalized = " ".join(query.split())
    assert args == (conversation_id, owner)
    assert "metadata->'summary_continuation_pending' = 'true'::jsonb" in normalized
    assert "UPDATE conversations" not in normalized

    conn.pending_flag = False
    assert (
        await store.read_summary_continuation_pending(conversation_id, captured_generation=capture)
        is False
    )


@pytest.mark.asyncio
async def test_clear_finished_summary_continuation_guards_drained_state() -> None:
    """Clearing is version/CAS-bound and requires a fully drained conversation:
    no mutable rows may exist and the finalized count must not exceed the
    persisted baseline.
    """
    conversation_id, owner = uuid4(), uuid4()
    capture = CapturedGeneration(owner, 7)
    expected_time = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    conn = _SeamConn()
    conn.update_row = {"id": conversation_id}
    store = _bound_store(_SeamPool(conn))
    conn.conversation_row = {"id": conversation_id}
    frozen = _snapshot(conversation_id, capture)

    cleared = await store.clear_finished_summary_continuation(
        conversation_id,
        captured_generation=capture,
        expected_summary_updated_at=expected_time,
        expected_baseline=7,
        summary_snapshot=frozen,
    )

    assert cleared is True
    query, args = conn.calls["update"]
    normalized = " ".join(query.split())
    assert args == (conversation_id, owner, expected_time, 7, None, 0)
    assert "summary_state_revision=$6" in normalized
    assert "summary_updated_at IS NOT DISTINCT FROM $3" in normalized
    assert "metadata->'last_summarized_msg_count' = to_jsonb($4::bigint)" in normalized
    assert "metadata->'summary_continuation_pending' = 'true'::jsonb" in normalized
    assert "status IS NOT NULL AND status NOT IN ('complete','error','cancelled')" in normalized
    assert "(SELECT count(*) FROM messages" in normalized
    assert "<= $4" in normalized
    assert "user_id=$2" in normalized or "user_id = $2" in normalized

    conn.update_row = None
    assert (
        await store.clear_finished_summary_continuation(
            conversation_id,
            captured_generation=capture,
            expected_summary_updated_at=expected_time,
            expected_baseline=7,
            summary_snapshot=frozen,
        )
        is False
    )
