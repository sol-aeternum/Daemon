"""All component log exits use text/vector-free sentinel-safe row shapes."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.memory import retrieval
from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.memory.store import MemoryStore
from orchestrator.eval import diagnostics
from scripts import memory_exposure_sweep as sweep

SENTINEL = "PRIVATE_COMPONENT_SENTINEL_M1"
USER = uuid.UUID(int=1)
MID = uuid.UUID(int=2)


def _store():
    return SimpleNamespace(
        log_retrieval=AsyncMock(return_value={}),
        search_memories=AsyncMock(
            return_value=[{"id": MID, "content": SENTINEL, "similarity": 1.0}]
        ),
        search_memories_bm25=AsyncMock(
            return_value=[{"id": MID, "content": SENTINEL, "bm25_score": 1.0}]
        ),
        get_l0_memories=AsyncMock(
            return_value=[
                {"id": uuid.UUID(int=3), "content": "Frozen synthetic fact", "local_only": False}
            ]
        ),
    )


@pytest.fixture
def logging_fakes(monkeypatch):
    monkeypatch.setattr(
        retrieval, "_available_fallback_storage_models", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(retrieval, "_get_entity_expanded_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_is_retrieval_logging_enabled", bool)
    monkeypatch.setattr(retrieval, "get_selected_embedding_route_id", lambda: "")
    monkeypatch.setattr(
        retrieval,
        "embed_query_for_configured_storage_models",
        AsyncMock(
            return_value=[SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")]
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_l0", [False, True])
@pytest.mark.parametrize("lexical", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("fail_log", [False, True])
async def test_sentinel_combined_l0_and_lexical_log_exits(
    logging_fakes, monkeypatch, caplog, include_l0, lexical, empty, fail_log
):
    store = _store()
    if empty:
        store.search_memories.return_value = []
        store.search_memories_bm25.return_value = []
    if fail_log:
        store.log_retrieval.side_effect = RuntimeError(SENTINEL)
    if lexical:
        monkeypatch.setattr(
            retrieval,
            "embed_query_for_configured_storage_models",
            AsyncMock(side_effect=EmbeddingConfigurationError("denied")),
        )
    caplog.set_level("INFO")
    rows = await retrieval.retrieve_memories_for_text(
        cast(Any, store),
        SENTINEL,
        user_id=USER,
        include_l0=include_l0,
        log_retrieval=True,
        record_query_text=False,
        query_role="sub:0",
        touch_results=False,
    )
    await asyncio.sleep(0)
    assert store.log_retrieval.await_count == 1
    row = store.log_retrieval.await_args.kwargs
    assert row["query_text"] == "" and row["query_embedding"] is None
    assert row["query_role"] == "sub:0"
    assert row["l0_included"] is include_l0
    assert bool(row["selected_memory_ids"]) is not empty
    assert len(rows) == (0 if empty else 1) + int(include_l0)
    assert SENTINEL not in json.dumps(row, default=str)
    assert SENTINEL not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("empty,fail_log", [(True, False), (False, False), (False, True)])
async def test_sentinel_normal_empty_populated_and_failed_log_exits(
    logging_fakes, caplog, empty, fail_log
):
    store = _store()
    if empty:
        store.search_memories.return_value = []
        store.search_memories_bm25.return_value = []
    if fail_log:
        store.log_retrieval.side_effect = RuntimeError(SENTINEL)
    caplog.set_level("INFO")
    await retrieval.retrieve_memories(
        cast(Any, store),
        [0.1],
        SENTINEL,
        user_id=USER,
        log_retrieval=True,
        record_query_text=False,
        query_role="sub:1",
        touch_results=False,
    )
    await asyncio.sleep(0)
    assert store.log_retrieval.await_count == (0 if empty else 1)
    for call in store.log_retrieval.await_args_list:
        assert call.kwargs["query_embedding"] is None
        assert call.kwargs["query_text"] == ""
        assert SENTINEL not in json.dumps(call.kwargs, default=str)
    assert SENTINEL not in caplog.text


@pytest.mark.asyncio
async def test_sentinel_component_exception_sanitized(logging_fakes):
    store = _store()
    store.search_memories.side_effect = RuntimeError(SENTINEL)
    with pytest.raises(RuntimeError, match="Component memory retrieval failed") as caught:
        await retrieval.retrieve_memories(
            cast(Any, store), [0.1], SENTINEL, user_id=USER, record_query_text=False
        )
    assert SENTINEL not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical", [False, True])
async def test_sentinel_role_only_control_is_private(logging_fakes, caplog, canonical):
    store = _store()
    caplog.set_level("DEBUG")
    if canonical:
        await retrieval.retrieve_memories_for_text(
            cast(Any, store),
            SENTINEL,
            user_id=USER,
            query_role="sub:2",
            log_retrieval=True,
            touch_results=False,
        )
    else:
        await retrieval.retrieve_memories(
            cast(Any, store),
            [0.1],
            SENTINEL,
            user_id=USER,
            query_role="sub:2",
            log_retrieval=True,
            touch_results=False,
        )
    await asyncio.sleep(0)
    assert store.log_retrieval.await_count == 1
    row = store.log_retrieval.await_args.kwargs
    assert row["query_text"] == "" and row["query_embedding"] is None
    assert SENTINEL not in json.dumps(row, default=str)
    assert SENTINEL not in caplog.text
    store.search_memories.side_effect = RuntimeError(SENTINEL)
    with pytest.raises(RuntimeError, match="Component memory retrieval failed") as caught:
        if canonical:
            await retrieval.retrieve_memories_for_text(
                cast(Any, store), SENTINEL, user_id=USER, query_role="sub:2"
            )
        else:
            await retrieval.retrieve_memories(
                cast(Any, store), [0.1], SENTINEL, user_id=USER, query_role="sub:2"
            )
    assert SENTINEL not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["empty_scope", "missing_user", "missing_conversation", "invalid_user"]
)
async def test_sentinel_early_returns_do_not_log(logging_fakes, caplog, reason):
    store = _store()
    kwargs: dict[str, Any] = {"user_id": USER}
    if reason == "empty_scope":
        kwargs["allowed_source_conversation_ids"] = []
    elif reason == "missing_user":
        kwargs["user_id"] = None
    else:
        kwargs["conversation_id"] = uuid.UUID(int=4)
        store.get_conversation = AsyncMock(
            return_value=None if reason == "missing_conversation" else {"user_id": "invalid"}
        )
    caplog.set_level("DEBUG")
    assert (
        await retrieval.retrieve_memories(
            cast(Any, store), [0.1], SENTINEL, query_role="sub:0", log_retrieval=True, **kwargs
        )
        == []
    )
    await asyncio.sleep(0)
    store.log_retrieval.assert_not_awaited()
    assert SENTINEL not in caplog.text


@pytest.mark.asyncio
async def test_sentinel_store_role_defense_and_original_row_shape():
    pool = SimpleNamespace(fetchrow=AsyncMock(return_value={}))
    store = MemoryStore(cast(Any, pool), cast(Any, Mock()))
    args: dict[str, Any] = dict(
        user_id=USER,
        query_text=SENTINEL,
        query_embedding_model="m",
        query_embedding=[1.0],
        candidate_memory_ids=[],
        candidate_scores={},
        selected_memory_ids=[],
        l0_included=False,
        latency_ms=1,
    )
    await store.log_retrieval(**args, query_role="sub:0")
    call = pool.fetchrow.await_args.args
    assert call[3] == "" and call[5] is None and call[-1] == "sub:0"
    assert SENTINEL not in str(call)
    await store.log_retrieval(**args)
    call = pool.fetchrow.await_args.args
    assert call[3] == SENTINEL and call[5] == "[1.0]"
    assert "query_role" not in call[0]
    await store.log_retrieval(**args, query_role="original")
    call = pool.fetchrow.await_args.args
    assert call[3] == SENTINEL and call[5] == "[1.0]" and call[-1] == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["sub:-1", "sub:١", "sub:", "other"])
async def test_invalid_query_roles_never_persist(role):
    pool = SimpleNamespace(fetchrow=AsyncMock(return_value={}))
    store = MemoryStore(cast(Any, pool), cast(Any, Mock()))
    with pytest.raises(ValueError, match="Invalid retrieval query role"):
        await store.log_retrieval(USER, SENTINEL, "m", [1.0], [], {}, [], False, 1, query_role=role)
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_readers_filter_roles_before_fuzzy_limit():
    pool = SimpleNamespace(fetch=AsyncMock(return_value=[]))
    store = SimpleNamespace(_pool=pool)
    await diagnostics.fetch_retrieval_log_for_query(cast(Any, store), "question", USER)
    await diagnostics.fetch_retrieval_logs_by_fuzzy_match(cast(Any, store), "question", USER)
    for call in pool.fetch.await_args_list:
        sql = call.args[0]
        assert "query_role IS NULL OR query_role = 'original'" in sql
        assert sql.index("query_role") < sql.index("LIMIT")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "original"])
async def test_original_diagnostic_readers_accept_old_and_new_rows(role):
    row = {
        "id": uuid.UUID(int=4),
        "query_text": "question",
        "candidate_memory_ids": [str(MID)],
        "candidate_scores": {},
        "selected_memory_ids": [str(MID)],
        "l0_included": False,
        "latency_ms": 1,
    }
    if role:
        row["query_role"] = role
    pool = SimpleNamespace(fetch=AsyncMock(return_value=[row]))
    store = SimpleNamespace(_pool=pool)
    exact = await diagnostics.fetch_retrieval_log_for_query(cast(Any, store), "question", USER)
    fuzzy = await diagnostics.fetch_retrieval_logs_by_fuzzy_match(
        cast(Any, store), "question", USER
    )
    assert exact is not None and exact.query_text == "question"
    assert len(fuzzy) == 1 and fuzzy[0] == exact


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("kind", ["cancellation", "accounting", "baseline_error"])
async def test_private_error_boundary_preserves_cancellation_accounting_and_baseline_errors(
    logging_fakes, canonical, kind
):
    if kind == "cancellation":
        error = asyncio.CancelledError()
    elif kind == "accounting":
        error = ComputeUnavailable(
            "settlement_failed", "Accounting incomplete", category=FAILURE_SETTLEMENT_FAILED
        )
    else:
        error = RuntimeError("Original retrieval error")
    store = _store()
    store.search_memories.side_effect = error
    kwargs = {} if kind == "baseline_error" else {"record_query_text": False, "query_role": "sub:0"}
    with pytest.raises(type(error)) as caught:
        if canonical:
            await retrieval.retrieve_memories_for_text(
                cast(Any, store), "question", user_id=USER, **kwargs
            )
        else:
            await retrieval.retrieve_memories(
                cast(Any, store), [0.1], "question", user_id=USER, **kwargs
            )
    assert caught.value is error


@pytest.mark.asyncio
async def test_entity_expansion_receives_query_without_persistence(monkeypatch, caplog):
    extract = Mock(return_value=[])
    monkeypatch.setattr(retrieval, "extract_candidates_baseline", extract)
    store = Mock()
    assert await retrieval._get_entity_expanded_candidates(cast(Any, store), USER, SENTINEL) == []
    assert SENTINEL in str(extract.call_args)
    assert store.mock_calls == []
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize("role", [None, "original"])
def test_original_vector_reader_ignores_component_rows(role):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    row = {
        "log_id": "original",
        "user_id": USER,
        "query_text": "question",
        "query_embedding_model": "model",
        "query_embedding": "[1,2]",
        "created_at": now,
    }
    if role:
        row["query_role"] = role
    vector, ids, stats = sweep.select_logged_vector(
        [
            row,
            {
                **row,
                "log_id": "component",
                "query_text": "",
                "query_role": "sub:0",
                "query_embedding": None,
            },
        ],
        user_id=USER,
        text="question",
        model="model",
        dimensions=2,
        vector_from=now,
        vector_until=now,
    )
    assert vector == [1.0, 2.0] and ids == ["original"]
    assert stats["out_of_window_rows"] == 0


def test_query_role_migration_preserves_original_not_null_contract():
    root = Path(__file__).resolve().parents[1]
    original = (root / "migrations/025_create_retrieval_log.sql").read_text()
    up = (root / "migrations/063_retrieval_query_role.sql").read_text()
    down = (root / "migrations/rollback/063_retrieval_query_role.down.sql").read_text()
    assert "query_text TEXT NOT NULL" in original
    assert "ADD COLUMN query_role TEXT" in up
    assert "query_role IS NULL" in up and "^sub:[0-9]+$" in up
    assert "ALTER COLUMN" not in up and "UPDATE " not in up
    assert "DROP COLUMN query_role" in down
    assert "SET LOCAL lock_timeout = '1s'" in up
    assert "SET LOCAL lock_timeout = '1s'" in down


@pytest.mark.asyncio
@pytest.mark.parametrize("include_l0", [False, True])
@pytest.mark.parametrize("lexical", [False, True])
@pytest.mark.parametrize("empty", [False, True])
async def test_real_retrieval_exits_buffer_only_data_until_owned_flush(
    logging_fakes, include_l0, lexical, empty
):
    store = _store()
    if empty:
        store.search_memories.return_value = []
        store.search_memories_bm25.return_value = []
    buffer = []
    token = retrieval._RETRIEVAL_LOG_BUFFER.set(buffer)
    try:
        await retrieval.retrieve_memories_for_text(
            cast(Any, store),
            SENTINEL,
            user_id=USER,
            query_embedding=[] if lexical else [0.1],
            allow_query_embedding=False,
            include_l0=include_l0,
            log_retrieval=True,
            touch_results=False,
            query_role="sub:0",
        )
        store.log_retrieval.assert_not_awaited()
    finally:
        retrieval._RETRIEVAL_LOG_BUFFER.reset(token)
    assert len(buffer) == 1
    assert buffer[0]["query_text"] == "" and buffer[0]["query_embedding"] is None
    assert SENTINEL not in json.dumps(buffer, default=str)
    assert bool(buffer[0]["candidate_memory_ids"]) is not empty
    await retrieval._flush_retrieval_logs(cast(Any, store), buffer)
    store.log_retrieval.assert_awaited_once_with(**buffer[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "duplicates", "failure", "timeout", "parse"])
async def test_real_no_added_logs_and_touches_match_disabled_column_values(
    logging_fakes, monkeypatch, outcome
):
    from orchestrator.memory import injection
    from tests.test_multi_query_gather import Provider

    settings = SimpleNamespace(
        memory_multi_query_enabled=False,
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
    if outcome == "timeout":

        async def expired(*args):
            raise TimeoutError

        monkeypatch.setattr(injection, "_wait_for_components", expired)
    provider = Provider([] if outcome == "empty" else None)
    if outcome == "parse":
        provider.values = [123]
    elif outcome == "failure":
        provider.error = RuntimeError("synthetic")
    results = []
    for enabled in [False, True]:
        settings.memory_multi_query_enabled = enabled
        store = _store()
        store.get_recent_summaries = AsyncMock(return_value=[])
        touches = []
        logs = []
        touched, logged = asyncio.Event(), asyncio.Event()

        async def touch(ids):
            touches.append(ids)
            touched.set()

        async def log(**row):
            logs.append(row)
            logged.set()

        store.bulk_touch_memories = touch
        store.log_retrieval = log
        inputs = await injection.gather_memory_context_inputs(
            cast(Any, store),
            user_id=USER,
            pipeline="cloud",
            query_text="Original question",
            log_retrieval=True,
            subquery_provider=provider,
        )
        await asyncio.wait_for(asyncio.gather(touched.wait(), logged.wait()), 5)
        assert inputs.retrieved_limit is None and inputs.added_memory_ids == ()
        assert len(logs) == 1 and "query_role" not in logs[0]
        logs[0].pop("latency_ms")  # wall-clock duration is not a stored-content contract
        results.append((injection.render_memory_context(inputs), logs, touches))
    assert results[0] == results[1]
