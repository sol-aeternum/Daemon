"""Added-only trust composition preserves the legacy baseline call and timestamps."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from orchestrator.memory import trust_signals
from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("matched", [True, False, None])
async def test_added_trust_cas_replaces_exact_captured_baseline_not_stale_prior_turn(matched):
    pool = SimpleNamespace(fetchval=AsyncMock(return_value=matched), execute=AsyncMock())
    store = cast(Any, SimpleNamespace(_pool=pool))
    baseline, added = [UUID(int=1), UUID(int=2)], [UUID(int=3)]
    result = await trust_signals._record_added_retrieved_memories(
        UUID(int=4), baseline, added, store, empty_baseline_snapshot="malformed but unused"
    )
    sql, cid, expected, union = pool.fetchval.await_args.args
    assert "last_retrieved_memory_ids = $2::jsonb" in sql
    assert json.loads(expected) == [str(mid) for mid in baseline]
    assert json.loads(union) == [str(mid) for mid in baseline + added]
    assert result is (matched is True)
    if matched is True:
        pool.execute.assert_awaited_once()
        assert pool.execute.await_args.args[1] == added  # never retimestamp baseline
    else:
        pool.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ordinary", "cancel", "accounting"])
async def test_trust_partial_effects_are_not_rolled_back_or_swallowed(failure):
    error = {
        "ordinary": RuntimeError("synthetic"),
        "cancel": asyncio.CancelledError(),
        "accounting": ComputeUnavailable(
            "settlement_failed", "Accounting incomplete", category=FAILURE_SETTLEMENT_FAILED
        ),
    }[failure]
    pool = SimpleNamespace(
        fetchval=AsyncMock(return_value=True), execute=AsyncMock(side_effect=error)
    )
    with pytest.raises(type(error)) as caught:
        await trust_signals._record_added_retrieved_memories(
            UUID(int=4), [UUID(int=1)], [UUID(int=3)], cast(Any, SimpleNamespace(_pool=pool))
        )
    assert caught.value is error
    pool.fetchval.assert_awaited_once()  # union may already have persisted
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("concurrent_replacement", [False, True])
async def test_empty_baseline_snapshot_replaced_by_rendered_additions_without_new_reads(
    monkeypatch, encoded, concurrent_replacement
):
    from orchestrator.memory import injection
    from tests.test_multi_query_gather import Provider, Store, USER_ID, CONVERSATION_ID, _rows

    prior = [str(UUID(int=20))]
    current = [str(UUID(int=99))] if concurrent_replacement else list(prior)
    store = Store()
    store.conversation["last_retrieved_memory_ids"] = json.dumps(prior) if encoded else prior
    pool = SimpleNamespace(fetchval=AsyncMock(), execute=AsyncMock())
    store._pool = pool

    async def cas(sql, cid, expected, replacement):
        nonlocal current
        assert cid == CONVERSATION_ID
        assert json.loads(expected) == [str(UUID(int=20))]
        assert json.loads(replacement) == [str(UUID(int=6)), str(UUID(int=7))]
        if current != json.loads(expected):
            return None
        current = json.loads(replacement)
        return True

    pool.fetchval.side_effect = cas
    settings = SimpleNamespace(
        memory_multi_query_enabled=True,
        memory_multi_query_merge="additive",
        memory_decomposition_timeout_ms=1500,
        memory_subquery_quota=3,
        memory_max_subqueries=3,
    )
    monkeypatch.setattr(injection, "get_settings", lambda: settings)
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "")
    vector = SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")
    monkeypatch.setattr(injection, "embed_query_with_metadata", AsyncMock(return_value=vector))
    monkeypatch.setattr(
        injection, "embed_queries_with_metadata", AsyncMock(return_value=[vector] * 2)
    )
    monkeypatch.setattr(injection, "ProductionSubqueryProvider", Provider)
    monkeypatch.setattr(injection, "_lazy_import_trust_signals", lambda: trust_signals)
    legacy = AsyncMock()
    monkeypatch.setattr(trust_signals, "record_retrieved_memories", legacy)

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        assert kwargs["user_id"] == USER_ID
        if role is None:
            # Prove the snapshot is copied before gather yields, not a mutable
            # alias into the initial conversation read result.
            if not encoded:
                prior[:] = [str(UUID(int=55))]
            return []
        return _rows(6 if role == "sub:0" else 7)

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    block = await injection.build_memory_context(store, CONVERSATION_ID)
    assert "Fictional fact 6" in block and "Fictional fact 7" in block
    legacy.assert_not_awaited()  # empty baseline retains its legacy no-call contract
    store.bulk_touch_memories.assert_awaited_once_with([UUID(int=6), UUID(int=7)])
    assert [call[0] for call in store.calls].count("get_conversation") == 1
    pool.fetchval.assert_awaited_once()
    if concurrent_replacement:
        assert current == [str(UUID(int=99))]
        pool.execute.assert_not_awaited()
    else:
        assert current == [str(UUID(int=6)), str(UUID(int=7))]
        pool.execute.assert_awaited_once()
        assert pool.execute.await_args.args[1] == [UUID(int=6), UUID(int=7)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot", [None, "broken", "null", "{}", {}, [1], ["not-a-uuid"], [None]]
)
async def test_empty_baseline_invalid_or_missing_snapshot_fails_closed(snapshot):
    pool = SimpleNamespace(fetchval=AsyncMock(), execute=AsyncMock())
    assert (
        await trust_signals._record_added_retrieved_memories(
            UUID(int=4),
            [],
            [UUID(int=3)],
            cast(Any, SimpleNamespace(_pool=pool)),
            empty_baseline_snapshot=snapshot,
        )
        is False
    )
    pool.fetchval.assert_not_awaited()
    pool.execute.assert_not_awaited()


class _JsonbColumnPool:
    """Applies the real trust SQL's effects; JSONB array equality is order-sensitive."""

    def __init__(self, stored: list[str]):
        self.stored = stored
        self.timestamped: list[list[UUID]] = []

    async def execute(self, sql, *args):
        if "UPDATE conversations" in sql:
            self.stored = json.loads(args[1])
        elif "UPDATE memories" in sql:
            self.timestamped.append(list(args[0]))
        else:
            raise AssertionError(f"Unexpected trust SQL: {sql}")

    async def fetch(self, sql, memory_ids):  # the legacy timestamp path
        assert "UPDATE memories" in sql
        self.timestamped.append(list(memory_ids))
        return [{"id": mid} for mid in memory_ids]

    async def fetchval(self, sql, _conversation_id, expected, replacement):
        assert "last_retrieved_memory_ids = $2::jsonb" in sql
        if self.stored != json.loads(expected):
            return None
        self.stored = json.loads(replacement)
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("merge_reorders_baseline", [False, True])
async def test_added_trust_composes_with_the_list_the_real_baseline_call_stored(
    monkeypatch, merge_reorders_baseline
):
    """The CAS only matches while the merge keeps baseline rows in their stored order."""
    from orchestrator.memory import injection
    from tests.test_multi_query_gather import Provider, Store, CONVERSATION_ID, _rows

    baseline_ids = [UUID(int=5), UUID(int=3), UUID(int=1)]  # deliberately unsorted
    added_ids = [UUID(int=6), UUID(int=7)]
    store = Store()
    store.conversation["last_retrieved_memory_ids"] = [str(UUID(int=20))]
    pool = _JsonbColumnPool([str(UUID(int=20))])
    store._pool = pool
    settings = SimpleNamespace(
        memory_multi_query_enabled=True,
        memory_multi_query_merge="additive",
        memory_decomposition_timeout_ms=1500,
        memory_subquery_quota=3,
        memory_max_subqueries=3,
    )
    monkeypatch.setattr(injection, "get_settings", lambda: settings)
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "")
    vector = SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")
    monkeypatch.setattr(injection, "embed_query_with_metadata", AsyncMock(return_value=vector))
    monkeypatch.setattr(
        injection, "embed_queries_with_metadata", AsyncMock(return_value=[vector] * 2)
    )
    monkeypatch.setattr(injection, "ProductionSubqueryProvider", Provider)
    monkeypatch.setattr(injection, "_lazy_import_trust_signals", lambda: trust_signals)

    async def retrieve(**kwargs):
        role = kwargs.get("query_role")
        if role is None:
            return _rows(5, 3, 1)
        return _rows(6 if role == "sub:0" else 7)

    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    if merge_reorders_baseline:
        real_merge = injection.merge_memories

        def reordering_merge(baseline, *args, **kwargs):
            return real_merge(list(reversed(baseline)), *args, **kwargs)

        monkeypatch.setattr(injection, "merge_memories", reordering_merge)

    block = await injection.build_memory_context(store, CONVERSATION_ID)

    assert "Fictional fact 6" in block and "Fictional fact 7" in block
    store.bulk_touch_memories.assert_awaited_once_with(added_ids)
    if merge_reorders_baseline:
        # The failure this test guards against: added trust is dropped silently.
        assert pool.stored == [str(mid) for mid in baseline_ids]
        assert pool.timestamped == [baseline_ids]
    else:
        assert pool.stored == [str(mid) for mid in baseline_ids + added_ids]
        assert pool.timestamped == [baseline_ids, added_ids]
