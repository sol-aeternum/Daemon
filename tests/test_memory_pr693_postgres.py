"""PR #693 regressions on owned PostgreSQL; providers are never contacted."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.memory import consolidation, injection, retrieval, revisions, tools
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.memory.equivalence import EquivalencePlan
from orchestrator.memory.temporal import EventScope, TemporalReadScope, TemporalRefusal
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked

env = durable_env_fixture(initialized=True)


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    monkeypatch.setattr(retrieval, "_is_retrieval_logging_enabled", bool)


def exact(start=None):
    return EventScope(
        start=start,
        start_kind="exact" if start else "unbounded",
        end_kind="unbounded",
        precision="exact",
    )


async def add(env, text="User likes fictional Amber.", **kwargs):
    return await env.memory.insert_memory(env.alice, text, "fact", "user_created", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True])
async def test_populated_extraction_log_does_not_rollback_erasure(env, hard):
    conv = await env.memory.create_conversation(env.alice)
    source = await env.memory.insert_message(
        conv["id"], env.alice, "user", "Original fictional source", status="complete"
    )
    root = await add(env, source_conversation_id=conv["id"])
    for owner in (env.alice, env.bob):
        await env.pool.execute(
            "INSERT INTO memory_extraction_log(user_id,input_snippet,extracted_facts) "
            "VALUES($1,$2,'[\"fictional derived fact\"]'::jsonb)",
            owner,
            env.memory.encryption.encrypt("Fictional derived snippet"),
        )
    assert await env.memory.delete_memory(root["id"], user_id=env.alice, soft=not hard)
    row = await env.pool.fetchrow(
        "SELECT input_snippet,extracted_facts FROM memory_extraction_log WHERE user_id=$1",
        env.alice,
    )
    assert row["input_snippet"] == "" and row["extracted_facts"] == "[]"
    assert await env.pool.fetchval("SELECT count(*) FROM memory_tombstones") == 1
    assert not await env.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memory_revision_segments WHERE content_tsv IS NOT NULL)"
    )
    assert await env.pool.fetchval(
        "SELECT input_snippet!='' FROM memory_extraction_log WHERE user_id=$1", env.bob
    )
    saved = await env.memory.get_owned_message(
        source["id"], user_id=env.alice, conversation_id=conv["id"]
    )
    assert saved["content"] == "Original fictional source"
    if not hard:
        assert await env.memory.delete_memory(root["id"], user_id=env.alice, soft=False)
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == 0


@pytest.mark.asyncio
async def test_nested_original_capture_preserves_origin_and_refuses_foreign_owner(env):
    async with revisions.original_capture(env.memory, env.alice) as outer:
        async with revisions.original_capture(env.memory, env.alice) as inner:
            assert inner is outer
        with pytest.raises(TemporalRefusal, match="owner_mismatch"):
            async with revisions.original_capture(env.memory, env.bob):
                pytest.fail("foreign owner cannot replace an originating capture")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update"])
async def test_tool_does_not_refresh_a_pre_erasure_chat_capture(env, monkeypatch, action):
    root = await add(env)
    retained = await add(env, "User enjoys fictional Silver.")
    # Create the independent deletion task before installing the chat context.
    erase_now = asyncio.Event()

    async def erase():
        await erase_now.wait()
        await env.memory.delete_memory(root["id"], user_id=env.alice)

    eraser = asyncio.create_task(erase())
    monkeypatch.setattr(
        tools,
        "prepare_memory_embedding",
        AsyncMock(side_effect=EmbeddingConfigurationError("synthetic denied route")),
    )

    async def plan(store, incoming, **kwargs):
        return EquivalencePlan(incoming)

    monkeypatch.setattr(tools, "prepare_memory_plan", plan)
    monkeypatch.setattr(tools.MemoryWriteTool, "_check_and_set_contradiction", AsyncMock())
    async with revisions.original_capture(env.memory, env.alice):
        erase_now.set()
        await asyncio.wait_for(eraser, 10)
        before = await env.pool.fetchval("SELECT count(*) FROM memory_revisions")
        with pytest.raises(TemporalRefusal, match="erasure_changed"):
            await tools.MemoryWriteTool(env.memory, env.alice).execute(
                action=action,
                memory_id=str(retained["id"]),
                content="User enjoys fictional Blue.",
            )
        assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == before
    assert (await env.memory.get_memory(retained["id"]))["content"] == retained["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "combined", "l0", "buffered"])
async def test_delayed_retrieval_logs_cannot_republish_after_erasure(env, monkeypatch, mode):
    root = await add(env)
    if mode == "l0":
        await env.memory.update_memory_tier(root["id"], "l0")
    original = env.memory.log_retrieval
    started, release = asyncio.Event(), asyncio.Event()
    writers = []

    async def delayed(**row):
        writers.append(asyncio.current_task())
        started.set()
        await release.wait()
        return await original(**row)

    monkeypatch.setattr(env.memory, "log_retrieval", delayed)
    buffer = []
    token = retrieval._RETRIEVAL_LOG_BUFFER.set(buffer if mode == "buffered" else None)
    try:
        if mode == "direct":
            await retrieval.retrieve_memories(
                env.memory,
                [],
                "fictional",
                user_id=env.alice,
                log_retrieval=True,
                touch_results=False,
            )
        else:
            await retrieval.retrieve_memories_for_text(
                env.memory,
                "fictional",
                user_id=env.alice,
                query_embedding=[],
                include_l0=mode == "l0",
                log_retrieval=True,
                allow_query_embedding=False,
                touch_results=False,
                record_query_text=mode != "buffered",
                query_role="sub:0" if mode == "buffered" else None,
            )
        if mode == "buffered":
            assert buffer and buffer[0]["query_text"] == ""
            assert buffer[0]["query_embedding"] is None
            writers.append(asyncio.create_task(retrieval._flush_retrieval_logs(env.memory, buffer)))
        await asyncio.wait_for(started.wait(), 10)
        await env.memory.delete_memory(root["id"], user_id=env.alice)
    finally:
        retrieval._RETRIEVAL_LOG_BUFFER.reset(token)
        release.set()
        await asyncio.wait_for(asyncio.gather(*set(writers), return_exceptions=True), 10)
    assert await env.pool.fetchval("SELECT count(*) FROM retrieval_log") == 0


def log_row(owner, capture):
    return dict(
        user_id=owner,
        query_text="fictional query",
        query_embedding_model="synthetic",
        query_embedding=[0.1] * 1024,
        candidate_memory_ids=[],
        candidate_scores={},
        selected_memory_ids=[],
        l0_included=False,
        latency_ms=1,
        originating_capture=capture,
    )


@pytest.mark.asyncio
async def test_retrieval_capture_precedes_candidate_reads(env, monkeypatch):
    root = await add(env)
    search = env.memory.search_memories_bm25

    async def erase_after_read(*args, **kwargs):
        rows = await search(*args, **kwargs)
        await env.memory.delete_memory(root["id"], user_id=env.alice)
        return rows

    monkeypatch.setattr(env.memory, "search_memories_bm25", erase_after_read)
    buffer = []
    token = retrieval._RETRIEVAL_LOG_BUFFER.set(buffer)
    try:
        await retrieval.retrieve_memories(
            env.memory,
            [],
            "fictional",
            user_id=env.alice,
            log_retrieval=True,
            touch_results=False,
        )
    finally:
        retrieval._RETRIEVAL_LOG_BUFFER.reset(token)
    assert buffer and buffer[0]["originating_capture"].erasure_epoch == 0
    await retrieval._flush_retrieval_logs(env.memory, buffer)
    assert await env.pool.fetchval("SELECT count(*) FROM retrieval_log") == 0


@pytest.mark.asyncio
async def test_log_insert_holds_erasure_barrier_through_commit(env, monkeypatch):
    root = await add(env)
    capture = await revisions.capture(env.memory, env.alice)
    entered, release, eraser_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actual_insert = env.memory._insert_retrieval_log
    actual_enter = revisions.enter
    pids = {}

    async def held_insert(*args):
        pids["writer"] = revisions.pool_for(env.memory).conn.get_server_pid()
        entered.set()
        await release.wait()
        return await actual_insert(*args)

    async def observed_enter(conn, value, *, ordered=False):
        if entered.is_set():
            pids["eraser"] = conn.get_server_pid()
            eraser_entered.set()
        return await actual_enter(conn, value, ordered=ordered)

    monkeypatch.setattr(env.memory, "_insert_retrieval_log", held_insert)
    monkeypatch.setattr(revisions, "enter", observed_enter)
    writer = asyncio.create_task(env.memory.log_retrieval(**log_row(env.alice, capture)))
    eraser = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        eraser = asyncio.create_task(env.memory.delete_memory(root["id"], user_id=env.alice))
        await asyncio.wait_for(eraser_entered.wait(), 10)
        await blocked(env.pool, pids["eraser"], pids["writer"])
        release.set()
        await asyncio.wait_for(writer, 10)
        assert await asyncio.wait_for(eraser, 10)
    finally:
        release.set()
        for task in (writer, eraser):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (writer, eraser) if t is not None), return_exceptions=True
        )
    row = await env.pool.fetchrow("SELECT * FROM retrieval_log")
    assert row["query_text"] == "" and row["query_embedding"] is None
    assert row["candidate_memory_ids"] == row["selected_memory_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "foreign", "generation", "erasure"])
async def test_log_publication_requires_original_owner_and_generation(env, invalid):
    root = await add(env)
    if invalid == "erasure":
        await env.memory.delete_memory(root["id"], user_id=env.alice)
        root = await add(env, "User reestablishes fictional Amber.")
    capture = await revisions.capture(env.memory, env.bob if invalid == "foreign" else env.alice)
    if invalid == "generation":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1", env.alice
        )
    elif invalid == "erasure":
        assert capture.erasure_epoch == 1
        await env.memory.delete_memory(root["id"], user_id=env.alice)
    from orchestrator.services.account_control.guard import WriteGuardRefusal

    with pytest.raises((TemporalRefusal, WriteGuardRefusal)):
        await env.memory.log_retrieval(
            **log_row(env.alice, None if invalid == "missing" else capture)
        )
    assert await env.pool.fetchval("SELECT count(*) FROM retrieval_log") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("any_term", [False, True])
async def test_historical_bm25_preserves_any_term_without_current_fallback(env, any_term):
    root = await add(env, "User reads fictional books.", event_scope=exact())
    scope = await revisions.resolve(
        env.memory, env.alice, TemporalReadScope(event_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    await env.memory.update_memory_content(
        root["id"], "User plays fictional guitar.", user_id=env.alice
    )
    async with revisions.read_scope(scope):
        rows = await env.memory.search_memories_bm25(
            env.alice, "Which books are currently reading?", match_any_term=any_term, limit=1
        )
        assert [row["content"] for row in rows] == ([root["content"]] if any_term else [])
        assert await env.memory.search_memories_bm25(env.alice, "guitar", match_any_term=True) == []
        assert (
            await env.memory.search_memories_bm25(env.alice, "the a of", match_any_term=True) == []
        )


@pytest.mark.asyncio
async def test_historical_any_term_qualifies_scope_and_permissions_before_limit(env):
    at = datetime(2020, 1, 1, tzinfo=UTC)
    kept = await add(env, "User reads books.", event_scope=exact())
    await add(env, "User reads many books constantly.", event_scope=exact(at + timedelta(days=1)))
    await add(env, "User reads many books privately.", event_scope=exact(), local_only=True)
    await add(env, "User reads many books without certified dates.")
    await env.memory.insert_memory(
        env.bob,
        "User reads many books belonging to another owner.",
        "fact",
        "manual",
        event_scope=exact(),
    )
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope(event_at=at))
    async with revisions.read_scope(scope):
        rows = await env.memory.search_memories_bm25(
            env.alice,
            "books reading constantly privately",
            match_any_term=True,
            limit=1,
            category="fact",
        )
    assert [row["id"] for row in rows] == [kept["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "revision", "erase"])
async def test_actual_cluster_discovery_carries_parent_revisions(env, monkeypatch, change):
    parents = [
        await add(
            env,
            f"User likes fictional color {i}.",
            memory_slot="fictional.colors",
            embedding=[0.1] * 1024,
        )
        for i in range(3)
    ]
    (cluster,) = await consolidation.find_memory_clusters(env.alice, env.memory)
    if change == "revision":
        await env.memory.update_memory_metadata(parents[0]["id"], {"changed": True})
    elif change == "erase":
        await env.memory.delete_memory(parents[0]["id"], user_id=env.alice)
    provider = SimpleNamespace(timeout_s=5, base_url=None, api_key=None, extra_headers=None)
    monkeypatch.setattr(
        consolidation,
        "get_settings",
        lambda: SimpleNamespace(
            daemon_encryption_key=None,
            get_provider_config=lambda _: provider,
        ),
    )
    monkeypatch.setattr(consolidation, "ContentEncryption", lambda _: env.memory.encryption)
    monkeypatch.setattr(
        consolidation,
        "guarded_completion",
        AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="User likes fictional colors."))
                ]
            )
        ),
    )
    result = await consolidation.consolidate_cluster(cluster, env.memory, env.alice)
    if change != "none":
        assert result == []
        assert await env.pool.fetchval("SELECT count(*) FROM memory_dependencies") == 0
        assert (
            await env.pool.fetchval(
                "SELECT count(*) FROM memories WHERE source_type='consolidation'"
            )
            == 0
        )
    else:
        assert len(result) == 1
        edges = await env.pool.fetch(
            "SELECT parent_memory_id,parent_revision_id FROM memory_dependencies"
        )
        assert {row["parent_memory_id"]: row["parent_revision_id"] for row in edges} == {
            row["id"]: row["head_revision_id"] for row in parents
        }
        assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE tier='l2'") == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("future_only", [False, True])
async def test_recent_summaries_select_present_assertions_not_future_projection(env, future_only):
    future = datetime.now(UTC) + timedelta(days=100)
    root = await env.memory.insert_memory(
        env.alice,
        "Present fictional summary.",
        "summary",
        "user_created",
        event_scope=exact(future if future_only else None),
    )
    if not future_only:
        await env.memory.update_memory_content(
            root["id"],
            "Future fictional summary.",
            user_id=env.alice,
            event_scope=exact(future),
            relationship="world_change",
        )
    rows = await env.memory.get_recent_summaries(env.alice)
    assert [row["content"] for row in rows] == ([] if future_only else [root["content"]])
    assert await env.memory.get_recent_summaries(env.bob) == []
    inputs = await injection.gather_memory_context_inputs(
        env.memory, user_id=env.alice, pipeline="cloud", query_text="", record_side_effects=False
    )
    block = injection.render_memory_context(inputs)
    assert "Future fictional summary" not in block
    assert (root["content"] in block) is not future_only
    async with revisions.read_scope(TemporalReadScope()):
        assert await env.memory.get_recent_summaries(env.alice) == []
