"""Native HTTP, encrypted durable input and legacy migration; fictional only."""

from __future__ import annotations

import json
import os
from secrets import token_hex
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from orchestrator.memory import revisions
from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.temporal import EventScope, TemporalReadScope, TemporalRefusal
from orchestrator.tasks import runner
from tests.durable_tasks_support import durable_env_fixture, migration_sql_for_schema
from tests.test_task_api_postgres import api as api, _submit

env = durable_env_fixture(initialized=True)


async def prepare(api, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    monkeypatch.setattr(
        "orchestrator.memory.injection.embed_query_with_metadata",
        AsyncMock(side_effect=EmbeddingConfigurationError("disabled synthetic")),
    )
    memory = await api.env.memory.insert_memory(
        api.env.alice,
        "User likes fictional Amber.",
        "fact",
        "manual",
        event_scope=EventScope(start_kind="unbounded", end_kind="unbounded", precision="exact"),
    )
    await api.env.memory.update_memory_tier(memory["id"], "l0")
    scope = await revisions.resolve(api.env.memory, api.env.alice, TemporalReadScope())
    seen = []

    async def model(**kwargs):
        seen.append((kwargs, revisions.current_read_scope()))
        yield {"type": "content_delta", "content": "Fictional response."}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", model)
    return scope, seen


@pytest.mark.asyncio
async def test_native_http_explicit_cutoff_excludes_future_history_and_preferences(
    api, monkeypatch
):
    scope, seen = await prepare(api, monkeypatch)
    conv = await api.env.memory.create_conversation(api.env.alice, title="Synthetic history")
    await api.env.memory.insert_message(
        conv["id"], api.env.alice, "user", "FUTURE-HISTORY-POISON", status="complete"
    )
    await api.env.memory.update_user_settings(
        api.env.alice, {"custom_instructions": "FUTURE-PREFERENCE-POISON"}
    )
    response = await api.client.post(
        "/chat",
        headers={"X-Daemon-Client-Features": ""},
        json={
            "message": "What fictional color?",
            "conversation_id": str(conv["id"]),
            "temporal_scope": scope.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200 and seen
    wire = json.dumps(seen[0][0], default=str)
    assert "FUTURE-HISTORY-POISON" not in wire and "FUTURE-PREFERENCE-POISON" not in wire
    assert "Amber" in wire and "revision=" in wire
    assert seen[0][1].watermark == scope.watermark


@pytest.mark.asyncio
async def test_durable_http_input_replay_and_actual_worker_keep_original_watermark(
    api, monkeypatch
):
    scope, seen = await prepare(api, monkeypatch)
    await _submit(
        api,
        "What fictional color?",
        key="synthetic-memory-cutoff-key",
        temporal_scope=scope.model_dump(mode="json"),
    )
    row = await api.env.pool.fetchrow("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1")
    decoded = api.env.tasks._open(row["input_ciphertext"])
    assert decoded["temporal_scope"] == scope.model_dump(mode="json")
    assert "temporal_scope" in decoded["_request"]
    # Later knowledge exists before the durable worker actually prepares context.
    await api.env.memory.insert_memory(
        api.env.alice, "User knows FUTURE-MEMORY-POISON.", "fact", "manual"
    )
    outcome = await runner.run_chat_task(api.ctx(), str(row["id"]))
    assert outcome == "completed" and seen
    wire = json.dumps(seen[0][0], default=str)
    assert "FUTURE-MEMORY-POISON" not in wire and "Amber" in wire
    assert seen[0][1].watermark == scope.watermark
    latest = await revisions.resolve(api.env.memory, api.env.alice, TemporalReadScope())
    changed = await api.client.post(
        "/chat",
        headers={"Idempotency-Key": "synthetic-memory-cutoff-key"},
        json={"message": "What fictional color?", "temporal_scope": latest.model_dump(mode="json")},
    )
    assert changed.status_code == 409


@pytest.mark.asyncio
async def test_legacy_baseline_is_migration_knowledge_and_reset_inventory(env):
    schema = "tasks_test_" + uuid4().hex
    pool = None
    async with env.pool.acquire() as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        pool = await asyncpg.create_pool(
            os.environ["TASKS_TEST_DATABASE_URL"],
            min_size=1,
            max_size=3,
            server_settings={"search_path": f"{schema},public"},
        )
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        async with pool.acquire() as conn:
            for path in sorted(migrations.glob("*.sql")):
                if path.name < "066_memory_bitemporal.sql":
                    await conn.execute(migration_sql_for_schema(path, schema))
            ciphertext = env.memory.encryption.encrypt("Legacy extant text only.")
            owners = [(uuid4(), 3, 4), (uuid4(), 7, 3), (uuid4(), None, 2)]
            memories = []
            for owner, generation, count in owners:
                await conn.execute(
                    "INSERT INTO users(id,email,name,username) VALUES($1,$2,'Synthetic',$2)",
                    owner,
                    f"{owner}@daemon.test",
                )
                if generation is not None:
                    await conn.execute(
                        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,$2)",
                        owner,
                        generation,
                    )
                for _ in range(count):
                    mid = uuid4()
                    memories.append((owner, mid))
                    await conn.execute(
                        "INSERT INTO memories(id,user_id,content,category,source_type,created_at) VALUES($1,$2,$3,'fact','manual','1990-01-01T00:00:00Z')",
                        mid,
                        owner,
                        ciphertext,
                    )
            before = await conn.fetchval("SELECT clock_timestamp()")
            await conn.execute((migrations / "066_memory_bitemporal.sql").read_text())
        store = MemoryStore(pool, env.memory.encryption)
        for owner, mid in memories:
            retained = await store.get_memory(mid)
            assert retained is not None and retained["content"] == "Legacy extant text only."
            assert retained["user_id"] == owner
        assert await pool.fetchval("SELECT count(*) FROM memory_revisions") == len(memories)
        assert await pool.fetchval("SELECT count(*) FROM memory_revision_segments") == len(memories)
        assert await pool.fetchval("SELECT count(*) FROM memory_knowledge_clock") == len(owners)
        assert await pool.fetchval("SELECT count(*) FROM memory_mutations") == len(owners)
        for owner, generation, count in owners:
            clock = await pool.fetchrow(
                "SELECT * FROM memory_knowledge_clock WHERE user_id=$1", owner
            )
            assert clock["account_generation"] == (generation or 0) and clock["sequence"] == 1
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM memory_revisions r JOIN memory_mutations k "
                    "ON k.user_id=r.user_id AND k.sequence=r.sequence "
                    "WHERE r.user_id=$1 AND k.account_generation=$2 AND r.relationship='baseline'",
                    owner,
                    generation or 0,
                )
                == count
            )
        assert await pool.fetchval("SELECT min(write_lower_at) FROM memory_mutations") >= before
        with pytest.raises(TemporalRefusal, match="before_available_history"):
            await revisions.resolve(store, owners[0][0], TemporalReadScope(known_at=before))
        manifest = json.loads(
            (migrations.parent / "tests/fixtures/account_writer_catalogue.json").read_text()
        )
        for table in (
            "memory_knowledge_clock",
            "memory_mutations",
            "memory_revisions",
            "memory_revision_segments",
            "memory_dependencies",
            "memory_sources",
            "memory_tombstones",
            "memory_erased_sources",
        ):
            assert manifest["tables"][table]["category"] == "content"
            assert "users" in manifest["ownership_paths"][table]
    finally:
        if pool is not None:
            await pool.close()
        async with env.pool.acquire() as admin:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
