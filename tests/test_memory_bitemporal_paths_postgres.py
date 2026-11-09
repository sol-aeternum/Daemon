"""Current writer/caller, deletion, source and execution-boundary qualification."""

from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.db import AppState

from orchestrator.memory import revisions
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.memory.equivalence import EquivalencePlan
from orchestrator.memory.injection import build_memory_context
from orchestrator.memory.lineage import record_sources
from orchestrator.memory.startup import require_revision_schema
from orchestrator.memory.temporal import EventScope, TemporalReadScope, TemporalRefusal
from orchestrator.memory.tools import MemoryReadTool, MemoryWriteTool
from orchestrator.routes import memories as api
from tests.durable_tasks_support import durable_env_fixture

env = durable_env_fixture(initialized=True)


@pytest.mark.asyncio
async def test_transaction_context_cannot_be_inherited_by_async_child(env):
    first = await add(env)
    original = await revisions.capture(env.memory, env.alice)
    async with revisions.mutation(env.memory, original):
        child = asyncio.create_task(
            env.memory.update_memory_content(first["id"], "Inherited write", user_id=env.alice)
        )
        with pytest.raises(TemporalRefusal, match="transaction_task_mismatch"):
            await child
    assert (await env.memory.get_memory(first["id"]))["content"] == first["content"]
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == 1


@pytest.fixture(autouse=True)
def fictional_only(monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-paths-pepper-not-a-credential-601")


async def add(env, text="User likes fictional Amber.", **kwargs):
    return await env.memory.insert_memory(env.alice, text, "fact", "user_created", **kwargs)


def exact(start=None, end=None):
    return EventScope(
        start=start,
        end=end,
        start_kind="exact" if start else "unbounded",
        end_kind="exact" if end else "unbounded",
        precision="exact",
    )


@pytest.mark.asyncio
async def test_semantic_writers_revision_and_maintenance_does_not(env):
    first = await add(env)
    await env.memory.update_memory_tier(first["id"], "l0")
    await env.memory.update_memory_metadata(first["id"], {"assertion": "fictional"})
    await env.memory.confirm_memory(first["id"], confirmed=False)
    await env.memory.confirm_memory(first["id"], confirmed=True)
    await env.memory.supersede_memory(
        first["id"], "User likes fictional Blue.", "preference", "user_created", env.alice
    )
    rows = await env.pool.fetch(
        "SELECT * FROM memory_revisions WHERE memory_id=$1 ORDER BY sequence,ordinal", first["id"]
    )
    assert len(rows) == 6
    assert len({r["id"] for r in rows}) == 6
    await env.memory.touch_memory(first["id"])
    await env.memory.bulk_touch_memories([first["id"]])
    await env.memory.update_memory_embedding(first["id"], [0.1] * 1024, embedding_model="synthetic")
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_revisions WHERE memory_id=$1", first["id"]
        )
        == 6
    )
    assert (await env.memory.get_memory(first["id"]))["id"] == first["id"]


@pytest.mark.asyncio
async def test_bulk_import_cannot_restore_knowledge_dates(env):
    old = datetime(1990, 1, 1, tzinfo=UTC)
    assert (
        await env.memory.import_memories(
            env.alice,
            [
                {
                    "content": "User remembers fictional Violet.",
                    "category": "fact",
                    "created_at": old,
                    "known_at": old,
                }
            ],
        )
        == 1
    )
    lower = await env.pool.fetchval(
        "SELECT min(write_lower_at) FROM memory_mutations WHERE user_id=$1", env.alice
    )
    assert lower > old
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == 1


@pytest.mark.asyncio
async def test_correction_preserves_outside_fragments_and_future_ordinary_l0(env):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2021, 1, 1, tzinfo=UTC)
    first = await add(env, event_scope=exact())
    await env.memory.update_memory_content(
        first["id"],
        "User likes fictional Silver.",
        user_id=env.alice,
        event_scope=exact(start, end),
    )
    for at, text in ((start - timedelta(seconds=1), "Amber"), (start, "Silver"), (end, "Amber")):
        scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope(event_at=at))
        assert (
            text
            in (await revisions.search(env.memory, env.alice, scope, memory_id=first["id"]))[0][
                "content"
            ]
        )
    future = datetime.now(UTC) + timedelta(days=100)
    await env.memory.update_memory_content(
        first["id"],
        "User likes fictional Future.",
        user_id=env.alice,
        event_scope=exact(future),
        relationship="world_change",
    )
    await env.memory.update_memory_tier(first["id"], "l0")
    assert "Amber" in (await env.memory.get_l0_memories(env.alice))[0]["content"]


@pytest.mark.asyncio
async def test_source_detachment_preserves_ordinary_but_revokes_strict_history(env):
    conv = await env.memory.create_conversation(env.alice, title="Fictional source")
    first = await add(env, source_conversation_id=conv["id"], event_scope=exact())
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    assert await revisions.search(env.memory, env.alice, scope)
    assert await env.memory.delete_conversation(conv["id"], user_id=env.alice)
    retained = await env.memory.get_memory(first["id"], user_id=env.alice)
    assert retained and retained["provenance_unknown"]
    assert await revisions.search(env.memory, env.alice, scope) == []
    assert await revisions.history(env.memory, env.alice, first["id"]) == []


@pytest.mark.asyncio
async def test_task_source_delete_uses_registered_clock_before_fk_detach(env):
    conv = await env.memory.create_conversation(env.alice, title="Fictional source")
    memory = await add(env, source_conversation_id=conv["id"])
    removed, running = await env.tasks.delete_conversation(env.alice, conv["id"])
    assert removed and running is None
    assert (await env.memory.get_memory(memory["id"]))["source_conversation_id"] is None


@pytest.mark.asyncio
async def test_current_source_permission_and_owner_filter_before_cipher(env):
    conv = await env.memory.create_conversation(env.alice, title="Fictional source")
    memory = await add(env, source_conversation_id=conv["id"], event_scope=exact())
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", conv["id"])
    # Poison ciphertext is intentionally not decryptable: permission must filter it.
    await env.pool.execute("UPDATE memory_revision_segments SET content='poison'")
    assert await revisions.search(env.memory, env.alice, scope) == []
    assert await env.memory.get_memory(memory["id"], user_id=env.bob) is None


@pytest.mark.asyncio
async def test_derived_erasure_trace_scrub_and_old_source_suppression(env):
    conv = await env.memory.create_conversation(env.alice, title="Original source remains")
    source = await env.memory.insert_message(
        conv["id"], env.alice, "user", "Original fictional Amber statement.", status="complete"
    )
    root = await add(env, source_conversation_id=conv["id"])
    original = await revisions.capture(env.memory, env.alice)
    async with revisions.mutation(env.memory, original) as conn:
        await record_sources(conn, env.alice, root["id"], [source["id"]])
    child = await env.memory.publish_derived_memory(
        env.alice, "Fictional combined summary.", "summary", "consolidation", parents=[root]
    )
    async with revisions.original_capture(env.memory, env.alice):
        answer = await env.memory.insert_message(
            conv["id"],
            env.alice,
            "assistant",
            "Original assistant source remains.",
            status="complete",
            tool_calls=[{"function": {"name": "memory_read", "arguments": "copied memory"}}],
            tool_results=[
                {"name": "memory_read", "result": "copied memory"},
                {"name": "calculate", "result": "2"},
            ],
        )
    stale = await revisions.capture(env.memory, env.alice)
    await env.memory.delete_memory(root["id"], user_id=env.alice)
    assert await env.memory.get_memory(child["id"]) is None
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_erased_sources WHERE source_id=$1", source["id"]
        )
        == 1
    )
    saved = await env.memory.get_owned_message(
        answer["id"], user_id=env.alice, conversation_id=conv["id"]
    )
    assert saved is not None
    assert saved["content"] == "Original assistant source remains."
    assert saved["tool_results"][0]["result"] == "[Memory-derived payload erased]"
    assert saved["tool_results"][1]["result"] == "2"
    assert saved["tool_calls"][0]["function"]["arguments"] == "[Memory-derived payload erased]"
    with pytest.raises(TemporalRefusal, match="erasure_changed"):
        async with revisions.mutation(env.memory, stale):
            await env.memory.publish_derived_memory(
                env.alice, "Stale derived copy", "summary", "consolidation", parents=[root]
            )
    new = await env.memory.insert_message(
        conv["id"],
        env.alice,
        "user",
        "New user statement can reestablish Amber.",
        status="complete",
    )
    assert not await env.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memory_erased_sources WHERE source_id=$1)", new["id"]
    )


@pytest.mark.asyncio
async def test_soft_gc_deadline_not_extended_by_touch_and_hard_owner_cascade(env):
    first = await add(env)
    await env.memory.delete_memory(first["id"], user_id=env.alice)
    await env.memory.touch_memory(first["id"])
    assert (await env.memory.run_garbage_collect())["deleted"] == 0
    await env.pool.execute(
        "UPDATE memory_tombstones SET purge_after=clock_timestamp()-interval '1 day' WHERE user_id=$1",
        env.alice,
    )
    assert (await env.memory.run_garbage_collect())["deleted"] == 1
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revision_segments") == 0
    await env.pool.execute("DELETE FROM users WHERE id=$1", env.alice)
    for table in (
        "memory_knowledge_clock",
        "memory_mutations",
        "memory_revisions",
        "memory_dependencies",
        "memory_tombstones",
        "memory_erased_sources",
        "memory_sources",
    ):
        assert await env.pool.fetchval("SELECT count(*) FROM " + table) == 0


@pytest.mark.asyncio
async def test_clock_regression_blocks_timestamp_not_logical(env):
    await add(env)
    await env.pool.execute(
        "UPDATE memory_knowledge_clock SET last_clock_at=clock_timestamp()+interval '1 day' WHERE user_id=$1",
        env.alice,
    )
    now = await env.pool.fetchval("SELECT clock_timestamp()")
    with pytest.raises(TemporalRefusal, match="indeterminate"):
        await revisions.resolve(env.memory, env.alice, TemporalReadScope(known_at=now))
    logical = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    assert logical.watermark is not None and logical.watermark.sequence == 1


@pytest.mark.asyncio
async def test_http_history_export_and_hard_after_soft(env):
    first = await add(env, event_scope=exact())
    state = AppState(settings=get_settings(), memory_store=env.memory, db_pool=env.pool)
    auth = AuthenticatedDevice(env.alice, uuid4(), uuid4())
    result = await api.get_memory_history(first["id"], state, auth)
    assert result["revisions"][0]["content"] == first["content"]
    foreign = AuthenticatedDevice(env.bob, uuid4(), uuid4())
    with pytest.raises(HTTPException) as error:
        await api.get_memory_history(first["id"], state, foreign)
    assert error.value.status_code == 404
    export = await api.export_memories(api.MemoryExportRequest(history=True), state, auth)
    assert export["memories"][0]["revisions"]
    await api.delete_memory(first["id"], False, state, auth)
    with pytest.raises(HTTPException):
        await api.get_memory_history(first["id"], state, auth)
    await api.delete_memory(first["id"], True, state, auth)
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == 0


@pytest.mark.asyncio
async def test_owner_history_keeps_local_sources_out_of_cloud_retrieval(env):
    conv = await env.memory.create_conversation(env.alice, title="Fictional source")
    first = await add(env, source_conversation_id=conv["id"], event_scope=exact())
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    assert await revisions.search(env.memory, env.alice, scope)
    await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", conv["id"])

    assert await revisions.search(env.memory, env.alice, scope) == []
    local = await revisions.search(env.memory, env.alice, scope, include_local=True)
    assert [row["id"] for row in local] == [first["id"]]
    state = AppState(settings=get_settings(), memory_store=env.memory, db_pool=env.pool)
    auth = AuthenticatedDevice(env.alice, uuid4(), uuid4())
    history = await api.get_memory_history(first["id"], state, auth)
    assert history["revisions"][0]["content"] == first["content"]
    exported = await api.export_memories(api.MemoryExportRequest(history=True), state, auth)
    assert exported["memories"][0]["revisions"][0]["content"] == first["content"]

    foreign = AuthenticatedDevice(env.bob, uuid4(), uuid4())
    with pytest.raises(HTTPException) as error:
        await api.get_memory_history(first["id"], state, foreign)
    assert error.value.status_code == 404
    assert await api.export_memories(api.MemoryExportRequest(history=True), state, foreign) == {
        "memories": []
    }
    await env.memory.delete_conversation(conv["id"], user_id=env.alice)
    with pytest.raises(HTTPException) as error:
        await api.get_memory_history(first["id"], state, auth)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_tool_write_update_stable_id_and_parent_scope_cannot_widen(env, monkeypatch):
    from orchestrator.memory import tools

    monkeypatch.setattr(
        tools,
        "prepare_memory_embedding",
        AsyncMock(side_effect=EmbeddingConfigurationError("synthetic disabled")),
    )

    async def plan(store, incoming, **kwargs):
        return EquivalencePlan(incoming)

    monkeypatch.setattr(tools, "prepare_memory_plan", plan)
    monkeypatch.setattr(MemoryWriteTool, "_check_and_set_contradiction", AsyncMock())
    writer = MemoryWriteTool(env.memory, env.alice)
    assert "created" in await writer.execute(
        action="create",
        content="User likes fictional Amber.",
        event_scope=exact().model_dump(mode="json"),
    )
    first = (await env.memory.list_memories(env.alice))[0]
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    assert "updated" in await writer.execute(
        action="update",
        memory_id=str(first["id"]),
        content="User likes fictional Blue.",
        slot="fictional.color",
    )
    assert await env.pool.fetchval("SELECT count(*) FROM memories") == 1
    assert (
        await env.pool.fetchval("SELECT memory_slot FROM memories WHERE id=$1", first["id"])
        == "fictional.color"
    )
    conv = await env.memory.create_conversation(env.alice, title="Fictional cloud")
    reader = MemoryReadTool(env.memory, env.alice, conversation_id=conv["id"])
    async with revisions.read_scope(scope):
        assert "Amber" in await reader.execute()
        latest = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
        assert latest.watermark is not None
        assert "cannot_be_widened" in await reader.execute(
            watermark=latest.watermark.model_dump(mode="json")
        )


@pytest.mark.asyncio
async def test_historical_context_excludes_unversioned_and_labels_l0(env, monkeypatch):
    from orchestrator.memory import injection

    monkeypatch.setattr(
        injection,
        "embed_query_with_metadata",
        AsyncMock(side_effect=EmbeddingConfigurationError("synthetic disabled")),
    )
    conv = await env.memory.create_conversation(env.alice, title="Fictional cloud")
    first = await add(env, event_scope=exact())
    await env.memory.update_memory_tier(first["id"], "l0")
    await env.pool.execute(
        "UPDATE conversations SET summary='FUTURE SUMMARY POISON' WHERE id=$1", conv["id"]
    )
    await env.memory.insert_message(
        conv["id"], env.alice, "user", "FUTURE MESSAGE POISON", status="complete"
    )
    scope = await revisions.resolve(
        env.memory, env.alice, TemporalReadScope(event_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    block = await build_memory_context(env.memory, conv["id"], temporal_scope=scope, query_text="")
    assert "FUTURE" not in block
    assert "Amber" in block and "revision=" in block and "excluded" in block


@pytest.mark.asyncio
async def test_startup_guard_and_storage_growth_no_arbitrary_retention(env):
    await require_revision_schema(env.pool)
    first = await add(env)
    for index in range(12):
        await env.memory.update_memory_content(
            first["id"], f"User likes fictional shade {index}.", user_id=env.alice
        )
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revisions") == 13
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revision_segments") == 13
    assert await env.pool.fetchval("SELECT pg_total_relation_size('memory_revision_segments')") > 0
    await env.pool.execute("ALTER TABLE memories DISABLE TRIGGER memory_revision_capture")
    try:
        with pytest.raises(RuntimeError, match="migration 066"):
            await require_revision_schema(env.pool)
    finally:
        await env.pool.execute("ALTER TABLE memories ENABLE TRIGGER memory_revision_capture")


@pytest.mark.asyncio
async def test_real_explain_historical_filter_and_ordinary_index_retention(env):
    for index in range(16):
        await add(env, f"User remembers fictional color {index}.", event_scope=exact())
    scope = await revisions.resolve(
        env.memory, env.alice, TemporalReadScope(event_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    captured = []

    class Recorder:
        async def fetch(self, sql, *args):
            captured.append((sql, args))
            return await env.pool.fetch(sql, *args)

    proxy = SimpleNamespace(_pool=Recorder(), _memory_row_to_dict=env.memory._memory_row_to_dict)
    assert len(await revisions.search(proxy, env.alice, scope, query="fictional", limit=1)) == 1
    sql, args = captured[0]
    plan = await env.pool.fetchval("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql, *args)
    decoded = json.loads(plan) if isinstance(plan, str) else plan
    assert decoded[0]["Plan"]["Node Type"] == "Limit"
    assert decoded[0]["Plan"]["Actual Rows"] == 1
    indexes = await env.pool.fetch(
        "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename IN ('memories','memory_revision_segments')"
    )
    assert any("hnsw" in row["indexdef"] for row in indexes)
    assert any("ivfflat" in row["indexdef"] for row in indexes)
