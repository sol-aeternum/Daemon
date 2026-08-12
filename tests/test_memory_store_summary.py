from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory.store import MemoryStore


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
async def test_update_conversation_summary_persists_baseline_atomically() -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": conversation_id}
    store = object.__new__(MemoryStore)
    store._pool = pool

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
    )

    assert updated is True
    query, actual_id, summary, actual_time, baseline, snapshot, continuation_pending = (
        pool.fetchrow.await_args.args
    )
    normalized_query = " ".join(query.split())
    assert actual_id == conversation_id
    assert summary == "summary"
    assert actual_time == summary_time
    assert baseline == 42
    assert snapshot is None
    assert continuation_pending is False
    assert "last_summarized_msg_count" in normalized_query
    assert "summary_updated_at IS NOT DISTINCT FROM $3" in normalized_query
    assert "updated_at = NOW()" in normalized_query
    assert "last_activity_at = NOW()" in normalized_query


@pytest.mark.asyncio
async def test_update_conversation_summary_persists_snapshot_timestamp() -> None:
    conversation_id = uuid4()
    summary_time = datetime.now(timezone.utc)
    snapshot = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": conversation_id}
    store = object.__new__(MemoryStore)
    store._pool = pool

    updated = await store.update_conversation_summary(
        conversation_id,
        summary="summary",
        expected_summary_updated_at=summary_time,
        summarized_message_count=42,
        summary_snapshot_at=snapshot,
    )

    assert updated is True
    query, _actual_id, _summary, _time, _baseline, snapshot_arg, _continuation_pending = (
        pool.fetchrow.await_args.args
    )
    assert snapshot_arg == "2026-08-08T12:00:00+00:00"
    assert "last_summarized_at_time" in " ".join(query.split())


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
