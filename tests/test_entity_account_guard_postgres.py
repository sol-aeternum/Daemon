"""Queued entity origin/owner/plan/commit proofs on owned disposable PostgreSQL.

Only compute and provider boundaries are scripted. SQL, encryption, native queue
serialization, locks and commits are real; skips do not qualify this slice.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncpg
import pytest
from arq.jobs import deserialize_job, serialize_job

from orchestrator.memory import entities
from orchestrator.memory.entity_projection import EntityMutation, EntityProjectionStale
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.worker import jobs
from tests.durable_tasks_support import durable_env_fixture
from tests.test_account_write_guard import blocked
from tests.test_title_account_guard_postgres import bound_store

env = durable_env_fixture()


async def source(env, *, owner=None, text="the name Alice is useful", conv=None, local=False):
    memory = await env.memory.insert_memory(
        owner or env.alice,
        text,
        "fact",
        "conversation" if conv else "manual",
        source_conversation_id=conv,
        local_only=local,
    )
    return memory["id"]


async def canonical(env, memory, *, name="Robert", linked=None, owner=None):
    return await env.pool.fetchval(
        "INSERT INTO entities(user_id,canonical_name,lookup_key,aliases,"
        "alias_lookup_keys,source_memory_id,linked_memory_ids) "
        "VALUES($1,$2,$3,$4::jsonb,$5,$6,$7) RETURNING id",
        owner or env.alice,
        env.memory.encryption.encrypt(name),
        name.lower(),
        json.dumps(env.memory.encryption.encrypt(json.dumps(["Bobby"]))),
        ["bobby"],
        memory,
        linked if linked is not None else [memory],
    )


async def prepare(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "synthetic-entity-pepper")
    await env.pool.execute(
        "INSERT INTO entitlement_accounts(user_id,content_generation) VALUES($1,7),($2,7)",
        env.alice,
        env.bob,
    )
    conv = await env.memory.create_conversation(env.alice, title="Entity synthetic")
    mid = await source(env, conv=conv["id"])

    @asynccontextmanager
    async def compute(pool, owner, **kwargs):
        assert pool is env.pool and owner == env.alice
        assert kwargs == {
            "operation": "agent",
            "auto_route": True,
            "background": True,
            "profile": "background",
        }
        await no_locks(env, mid, conv["id"])
        yield

    monkeypatch.setattr(jobs, "account_compute", compute)
    embedding = AsyncMock(return_value=[1.0, 0.0])
    completion = AsyncMock(
        return_value={
            "choices": [
                {"message": {"content": "YES: fictional names match"}, "finish_reason": "stop"}
            ]
        }
    )
    monkeypatch.setattr(entities, "embed_query", embedding)
    monkeypatch.setattr(entities, "guarded_completion", completion)
    return mid, conv["id"], embedding, completion


async def no_locks(env, mid, conv):
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", env.alice
        )
        await conn.fetchrow("SELECT id FROM conversations WHERE id=$1 FOR UPDATE NOWAIT", conv)
        await conn.fetchrow("SELECT id FROM memories WHERE id=$1 FOR UPDATE NOWAIT", mid)


async def run(env, ids, *, owner=None, generation=7, **kwargs):
    return await jobs.resolve_entities_job(
        {"store": env.memory, "db_pool": env.pool},
        str(owner or env.alice),
        json.dumps([str(mid) for mid in ids]),
        origin_account_id=str(owner or env.alice),
        origin_account_generation=generation,
        **kwargs,
    )


async def rows(env):
    return [dict(row) for row in await env.pool.fetch("SELECT * FROM entities ORDER BY id")]


async def inputs(env, ids):
    return await env.memory.read_entity_projection_inputs(
        ids, captured_generation=CapturedGeneration(env.alice, 7)
    )


async def save(env, snapshot, mutations, *, store=None):
    return await (store or env.memory).save_entity_projection(
        snapshot, mutations, captured_generation=CapturedGeneration(env.alice, 7)
    )


@pytest.mark.asyncio
async def test_original_nonzero_origin_actual_worker_inserts_encrypted_aliases_and_links(
    env, monkeypatch
):
    mid, _, embedding, model = await prepare(env, monkeypatch)
    result = await run(env, [mid])
    assert result["status"] == "ok" and result["memories_processed"] == 1
    assert result["entities_created"] == 1
    row = (await rows(env))[0]
    assert row["user_id"] == env.alice and row["source_memory_id"] == mid
    assert row["linked_memory_ids"] == [mid]
    assert env.memory.encryption.decrypt(row["canonical_name"]) == "Alice"
    assert json.loads(env.memory.encryption.decrypt(json.loads(row["aliases"]))) == []
    embedding.assert_not_awaited()
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [None, True, -1, 2**63, "7", 7.5])
async def test_invalid_and_legacy_origin_precedes_optional_input_reads(
    env, monkeypatch, generation
):
    mid, _, embedding, model = await prepare(env, monkeypatch)
    reader = AsyncMock(side_effect=AssertionError("invalid origin must not read"))
    monkeypatch.setattr(env.memory, "read_entity_projection_inputs", reader)
    result = await jobs.resolve_entities_job(
        {"store": env.memory, "db_pool": env.pool},
        str(env.alice),
        json.dumps([str(mid)]),
        origin_account_id=str(env.alice),
        origin_account_generation=generation,
    )
    assert result["status"] == "skipped" and result["error_count"] == 0
    reader.assert_not_awaited()
    embedding.assert_not_awaited()
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "bad", "foreign"])
async def test_invalid_uuid_and_foreign_job_origin_never_reads(env, monkeypatch, origin):
    mid, _, _, _ = await prepare(env, monkeypatch)
    reader = AsyncMock(side_effect=AssertionError("invalid origin must not read"))
    monkeypatch.setattr(env.memory, "read_entity_projection_inputs", reader)
    result = await jobs.resolve_entities_job(
        {"store": env.memory, "db_pool": env.pool},
        str(env.alice),
        json.dumps([str(mid)]),
        origin_account_id=str(env.bob) if origin == "foreign" else origin,
        origin_account_generation=7,
    )
    assert result["status"] == "skipped"
    reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["bad", "{}", "null", "[true]", '["bad"]', "[1]", '"x"'])
async def test_malformed_source_payload_skips_before_reads(env, monkeypatch, payload):
    await prepare(env, monkeypatch)
    reader = AsyncMock(side_effect=AssertionError("malformed job must not read"))
    monkeypatch.setattr(env.memory, "read_entity_projection_inputs", reader)
    result = await jobs.resolve_entities_job(
        {"store": env.memory, "db_pool": env.pool},
        str(env.alice),
        payload,
        origin_account_id=str(env.alice),
        origin_account_generation=7,
    )
    assert result["status"] == "skipped" and result["error_count"] == 0
    reader.assert_not_awaited()


async def restrict(env, kind, tmp_path):
    if kind == "reset":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
        return 7, "stale_generation"
    if kind == "suspended":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
        return 7, "account_restricted"
    if kind == "cause":
        await env.pool.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
        )
        return 7, "inconsistent_state"
    if kind == "operation":
        from tests.test_account_control_persistence import persist, settings
        from tests.deletion_journal_support import request
        from orchestrator.services.deletion_journal.store import initialize
        from orchestrator.services.deletion_journal.barrier import require_recovery_barrier

        directory = (tmp_path / "journal", uuid4())
        initialize(*directory)
        await require_recovery_barrier(env.pool, settings(*directory))
        command = request(env.alice)
        await persist(
            env, directory, replace(command, fields=replace(command.fields, generation=8)), uuid4()
        )
        return 8, "open_operation"
    await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    return 7, "missing_account"


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["reset", "suspended", "cause", "operation", "missing"])
async def test_account_refusal_before_decryption_compute_or_provider(
    env, monkeypatch, tmp_path, restriction
):
    mid, _, embedding, model = await prepare(env, monkeypatch)
    generation, reason = await restrict(env, restriction, tmp_path)
    decrypt = Mock(side_effect=AssertionError("refused work must not decrypt"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    compute = Mock(side_effect=AssertionError("refused work must not compute"))
    monkeypatch.setattr(jobs, "account_compute", compute)
    result = await run(env, [mid], generation=generation)
    assert result["status"] == "skipped" and result["errors"] == [reason]
    assert await rows(env) == []
    decrypt.assert_not_called()
    compute.assert_not_called()
    embedding.assert_not_awaited()
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_source_rows_exclude_foreign_missing_local_and_unknown_before_decryption(
    env, monkeypatch
):
    mid, _, _, _ = await prepare(env, monkeypatch)
    foreign = await source(env, owner=env.bob, text="foreign secret sentinel")
    local = await source(env, text="local secret sentinel", local=True)
    orphan = await source(env, text="unknown secret sentinel")
    await env.pool.execute("UPDATE memories SET source_type='conversation' WHERE id=$1", orphan)
    bad_ciphertexts = await env.pool.fetch(
        "SELECT content FROM memories WHERE id=ANY($1::uuid[])", [foreign, local, orphan]
    )
    original = env.memory.encryption.decrypt

    def decrypt(value):
        assert value not in {row["content"] for row in bad_ciphertexts}
        return original(value)

    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    result = await run(env, [mid, foreign, local, orphan, uuid4()])
    assert result["status"] == "ok" and result["memories_processed"] == 1
    assert all(row["source_memory_id"] == mid for row in await rows(env))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_lineage",
    [
        "null",
        "foreign_primary",
        "local_primary",
        "foreign_link",
        "local_link",
        "missing_link",
        "orphan",
    ],
)
async def test_canonical_lineage_qualifies_before_name_decrypt_or_embedding(
    env, monkeypatch, bad_lineage
):
    mid, _, embedding, _ = await prepare(env, monkeypatch)
    lineage = await source(env, text="lineage without mentions")
    bad = await source(
        env,
        owner=env.bob if "foreign" in bad_lineage else env.alice,
        local="local" in bad_lineage,
        text="restricted lineage",
    )
    if bad_lineage == "orphan":
        await env.pool.execute("UPDATE memories SET source_type='conversation' WHERE id=$1", bad)
    primary = (
        None
        if bad_lineage == "null"
        else (bad if "primary" in bad_lineage or bad_lineage == "orphan" else lineage)
    )
    linked = [uuid4() if bad_lineage == "missing_link" else bad] if "link" in bad_lineage else []
    eid = await canonical(env, primary, linked=linked, name="PrivateName")
    cipher = await env.pool.fetchval("SELECT canonical_name FROM entities WHERE id=$1", eid)
    original = env.memory.encryption.decrypt

    def decrypt(value):
        assert value != cipher
        return original(value)

    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    result = await run(env, [mid])
    assert result["status"] == "ok"
    embedding.assert_not_awaited()
    assert (await env.pool.fetchrow("SELECT * FROM entities WHERE id=$1", eid))[
        "canonical_name"
    ] == cipher


@pytest.mark.asyncio
async def test_foreign_canonical_never_decrypts(env, monkeypatch):
    mid, _, embedding, _ = await prepare(env, monkeypatch)
    foreign_source = await source(env, owner=env.bob, text="foreign lineage")
    eid = await canonical(env, foreign_source, owner=env.bob)
    cipher = await env.pool.fetchval("SELECT canonical_name FROM entities WHERE id=$1", eid)
    original = env.memory.encryption.decrypt
    monkeypatch.setattr(
        env.memory.encryption,
        "decrypt",
        lambda value: original(value) if value != cipher else pytest.fail("foreign decrypt"),
    )
    assert (await run(env, [mid]))["status"] == "ok"
    embedding.assert_not_awaited()


async def change_source(env, mid, conv, change):
    if change == "content":
        await env.pool.execute(
            "UPDATE memories SET content=$2 WHERE id=$1",
            mid,
            env.memory.encryption.encrypt("the name Changed"),
        )
    elif change == "slot":
        await env.pool.execute("UPDATE memories SET memory_slot='person.updated' WHERE id=$1", mid)
    elif change == "local":
        await env.pool.execute("UPDATE memories SET local_only=true WHERE id=$1", mid)
    elif change == "owner":
        await env.pool.execute("UPDATE memories SET user_id=$2 WHERE id=$1", mid, env.bob)
    elif change == "deleted":
        await env.pool.execute("DELETE FROM memories WHERE id=$1", mid)
    elif change == "status":
        await env.pool.execute("UPDATE memories SET status='deleted' WHERE id=$1", mid)
    elif change == "closed":
        await env.pool.execute("UPDATE memories SET valid_to=NOW() WHERE id=$1", mid)
    elif change == "pipeline":
        await env.pool.execute("UPDATE conversations SET pipeline='local' WHERE id=$1", conv)
    elif change == "conversation_owner":
        await env.pool.execute("UPDATE conversations SET user_id=$2 WHERE id=$1", conv, env.bob)
    elif change == "provenance":
        await env.pool.execute("UPDATE memories SET source_conversation_id=NULL WHERE id=$1", mid)
    elif change == "reset":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
        )
    else:
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "content",
        "slot",
        "local",
        "owner",
        "deleted",
        "status",
        "closed",
        "pipeline",
        "conversation_owner",
        "provenance",
        "reset",
        "suspend",
    ],
)
async def test_admission_wait_rechecks_original_snapshot_before_providers(env, monkeypatch, change):
    mid, conv, embedding, model = await prepare(env, monkeypatch)
    await canonical(env, mid)

    @asynccontextmanager
    async def admission(*args, **kwargs):
        await no_locks(env, mid, conv)
        await change_source(env, mid, conv, change)
        yield

    monkeypatch.setattr(jobs, "account_compute", admission)
    before = await rows(env)
    result = await run(env, [mid])
    assert result["status"] == "skipped" and result["entities_created"] == 0
    embedding.assert_not_awaited()
    model.assert_not_awaited()
    # Source deletion legitimately nulls the entity FK, not a projection write.
    if change != "deleted":
        assert await rows(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "content",
        "slot",
        "local",
        "owner",
        "deleted",
        "status",
        "closed",
        "pipeline",
        "conversation_owner",
        "provenance",
        "reset",
        "suspend",
        "canonical",
        "aliases",
        "links",
        "nonwinning",
        "cancel",
    ],
)
async def test_edit_during_provider_planning_rejects_entire_old_plan(env, monkeypatch, change):
    mid, conv, embedding, _ = await prepare(env, monkeypatch)
    eid = await canonical(env, mid)
    other = await canonical(env, mid, name="Unselected") if change == "nonwinning" else None
    entered, resume = asyncio.Event(), asyncio.Event()

    async def paused(text):
        await no_locks(env, mid, conv)
        entered.set()
        await resume.wait()
        return [1.0, 0.0] if text != "Unselected" else [0.0, 1.0]

    embedding.side_effect = paused
    task = asyncio.create_task(run(env, [mid]))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if change == "cancel":
            task.cancel()
        elif change == "canonical" or change == "nonwinning":
            await env.pool.execute(
                "UPDATE entities SET canonical_name=$2 WHERE id=$1",
                other or eid,
                env.memory.encryption.encrypt("Changed canonical"),
            )
        elif change == "aliases":
            await env.pool.execute(
                "UPDATE entities SET aliases=$2::jsonb WHERE id=$1",
                eid,
                json.dumps(env.memory.encryption.encrypt('["Concurrent"]')),
            )
        elif change == "links":
            await env.pool.execute("UPDATE entities SET linked_memory_ids='{}' WHERE id=$1", eid)
        else:
            await change_source(env, mid, conv, change)
        before = await rows(env)
        resume.set()
        if change == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["status"] == "skipped" and result["entities_created"] == 0
        assert await rows(env) == before
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_qualified_ambiguous_confirmation_preserves_aliases_and_first_source_semantics(
    env, monkeypatch
):
    mid, conv, embedding, model = await prepare(env, monkeypatch)
    eid = await canonical(env, mid)

    async def embed(text):
        await no_locks(env, mid, conv)
        return [1.0, 0.0] if text == "Robert" else [0.8, 0.6]

    embedding.side_effect = embed

    async def confirm(**kwargs):
        await no_locks(env, mid, conv)
        return {
            "choices": [
                {"message": {"content": "YES: same fictional name"}, "finish_reason": "stop"}
            ]
        }

    model.side_effect = confirm
    result = await run(env, [mid])
    assert result["status"] == "ok" and result["entities_created"] == 1
    model.assert_awaited_once()
    row = await env.pool.fetchrow("SELECT * FROM entities WHERE id=$1", eid)
    assert json.loads(env.memory.encryption.decrypt(json.loads(row["aliases"]))) == [
        "Bobby",
        "Alice",
    ]
    assert row["linked_memory_ids"] == [mid] and row["alias_lookup_keys"] == ["bobby", "alice"]


@pytest.mark.asyncio
async def test_same_lookup_twice_in_one_batch_retains_both_qualified_links(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    other = await source(env, text="Alice appears again")
    result = await run(env, [mid, other, mid])
    assert result["status"] == "ok" and result["memories_processed"] == 2
    saved = await rows(env)
    assert len(saved) == 1 and set(saved[0]["linked_memory_ids"]) == {mid, other}


@pytest.mark.asyncio
async def test_preflight_preserves_payload_order_for_first_source_grouping(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    other = await source(env, text="Alice appears again")
    requested = sorted([mid, other], reverse=True)
    snapshot = await inputs(env, requested)
    assert snapshot.snapshot.source_ids == tuple(requested)
    assert [item[1] for item in snapshot.memory_contents] == requested


@pytest.mark.asyncio
async def test_concurrent_new_writers_one_commits_other_stales_without_clobber(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    mutations = [EntityMutation(None, "Alice", "alice", mid)]
    results = await asyncio.gather(
        save(env, snapshot, mutations), save(env, snapshot, mutations), return_exceptions=True
    )
    assert sum(isinstance(value, list) for value in results) == 1
    assert sum(isinstance(value, EntityProjectionStale) for value in results) == 1
    saved = await rows(env)
    assert len(saved) == 1 and saved[0]["linked_memory_ids"] == [mid]


@pytest.mark.asyncio
async def test_competing_alias_writers_do_not_lose_committed_alias(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    eid = await canonical(env, mid)
    snapshot = await inputs(env, [mid])
    one = [EntityMutation(eid, "Robert", "robert", mid, "Alice", "alice")]
    two = [EntityMutation(eid, "Robert", "robert", mid, "Caroline", "caroline")]
    results = await asyncio.gather(
        save(env, snapshot, one), save(env, snapshot, two), return_exceptions=True
    )
    assert sum(isinstance(value, list) for value in results) == 1
    assert sum(isinstance(value, EntityProjectionStale) for value in results) == 1
    row = await env.pool.fetchrow("SELECT aliases FROM entities WHERE id=$1", eid)
    aliases = json.loads(env.memory.encryption.decrypt(json.loads(row["aliases"])))
    assert "Bobby" in aliases and len(aliases) == 2


@pytest.mark.asyncio
async def test_unexpected_duplicate_key_rolls_back_earlier_new_entity(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    eid = await canonical(env, mid, name="Zulu")
    before = await rows(env)
    with pytest.raises(EntityProjectionStale):
        await save(
            env,
            snapshot,
            [
                EntityMutation(None, "Alice", "alice", mid),
                EntityMutation(None, "Zulu", "zulu", mid),
            ],
        )
    assert await rows(env) == before and (await rows(env))[0]["id"] == eid


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["link", "commit"])
async def test_real_sql_or_commit_failure_rolls_back_entire_batch_and_reuses_connection(
    env, monkeypatch, fault
):
    mid, _, _, _ = await prepare(env, monkeypatch)
    await env.pool.execute(
        "CREATE FUNCTION entity_fault() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'synthetic entity fault'; END $$; "
        + (
            "CREATE CONSTRAINT TRIGGER entity_fault AFTER INSERT ON entities DEFERRABLE INITIALLY DEFERRED "
            if fault == "commit"
            else "CREATE TRIGGER entity_fault BEFORE UPDATE ON entities "
        )
        + "FOR EACH ROW EXECUTE FUNCTION entity_fault();"
    )
    result = await run(env, [mid])
    assert result["status"] == "error" and result["entities_created"] == 0
    assert await rows(env) == []
    async with env.pool.acquire() as reused, reused.transaction():
        assert await reused.fetchval("SELECT 1") == 1
        await reused.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE NOWAIT", env.alice
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "cause"])
async def test_control_first_actual_wait_observes_subordinate_changes_after_lock(
    env, monkeypatch, change
):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    async with env.pool.acquire() as writer, env.pool.acquire() as fence:
        tx = fence.transaction()
        await tx.start()
        await fence.fetchrow(
            "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
        )
        if change == "generation":
            await fence.execute(
                "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
            )
        else:
            await fence.execute(
                "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", env.alice, uuid4()
            )
        task = asyncio.create_task(
            save(
                env,
                snapshot,
                [EntityMutation(None, "Alice", "alice", mid)],
                store=bound_store(env, writer),
            )
        )
        try:
            await blocked(env.pool, writer.get_server_pid(), fence.get_server_pid())
            await tx.commit()
            with pytest.raises(WriteGuardRefusal):
                await asyncio.wait_for(task, 10)
            assert not writer.is_in_transaction() and await writer.fetchval("SELECT 1") == 1
            assert await rows(env) == []
        finally:
            if fence.is_in_transaction():
                await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_writer_first_account_lock_precedes_source_wait_and_survives_commit(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as editor,
        env.pool.acquire() as fence,
    ):
        tx = editor.transaction()
        await tx.start()
        await editor.fetchrow("SELECT id FROM memories WHERE id=$1 FOR UPDATE", mid)
        task = asyncio.create_task(
            save(
                env,
                snapshot,
                [EntityMutation(None, "Alice", "alice", mid)],
                store=bound_store(env, writer),
            )
        )
        control = None
        try:
            await blocked(env.pool, writer.get_server_pid(), editor.get_server_pid())

            async def fence_after_commit():
                async with fence.transaction():
                    await fence.fetchrow(
                        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                        env.alice,
                    )
                    assert (
                        await fence.fetchval(
                            "SELECT count(*) FROM entities WHERE user_id=$1", env.alice
                        )
                        == 1
                    )
                    await fence.execute(
                        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1",
                        env.alice,
                    )

            control = asyncio.create_task(fence_after_commit())
            await blocked(env.pool, fence.get_server_pid(), writer.get_server_pid())
            await tx.commit()
            assert len(await asyncio.wait_for(task, 10)) == 1
            await asyncio.wait_for(control, 10)
        finally:
            if editor.is_in_transaction():
                await tx.rollback()
            for job in (task, control):
                if job is not None and not job.done():
                    job.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if control is not None:
                await asyncio.gather(control, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "account", "source"])
async def test_cancellation_actual_lock_wait_cleans_up_transaction_and_connection(
    env, monkeypatch, phase
):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    async with env.pool.acquire() as writer, env.pool.acquire() as holder:
        tx = holder.transaction()
        await tx.start()
        if phase == "source":
            await holder.fetchrow("SELECT id FROM memories WHERE id=$1 FOR UPDATE", mid)
        else:
            await holder.fetchrow(
                "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", env.alice
            )
        store = bound_store(env, writer)
        operation = (
            store.read_entity_projection_inputs(
                [mid], captured_generation=CapturedGeneration(env.alice, 7)
            )
            if phase == "preflight"
            else save(env, snapshot, [EntityMutation(None, "Alice", "alice", mid)], store=store)
        )
        task = asyncio.create_task(operation)
        try:
            await blocked(env.pool, writer.get_server_pid(), holder.get_server_pid())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not writer.is_in_transaction() and await writer.fetchval("SELECT 1") == 1
            assert await rows(env) == []
        finally:
            await tx.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_actual_commit_rollback_and_real_pool_reuse(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    await env.pool.execute("""
      CREATE FUNCTION entity_commit_wait() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN PERFORM pg_advisory_xact_lock(91234569); RETURN NEW; END $$;
      CREATE CONSTRAINT TRIGGER entity_commit_wait AFTER INSERT ON entities
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION entity_commit_wait();
    """)
    acquired = asyncio.get_running_loop().create_future()

    class ObservedPool:
        @asynccontextmanager
        async def acquire(self):
            async with env.pool.acquire() as conn:
                acquired.set_result(conn.get_server_pid())
                yield conn

    store = MemoryStore(cast(asyncpg.Pool, ObservedPool()), env.memory.encryption)
    async with env.pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock(91234569)")
        task = asyncio.create_task(
            save(env, snapshot, [EntityMutation(None, "Alice", "alice", mid)], store=store)
        )
        try:
            pid = await acquired
            await blocked(env.pool, pid, holder.get_server_pid())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with env.pool.acquire() as reused:
                assert reused.get_server_pid() == pid
                assert not reused.is_in_transaction() and await reused.fetchval("SELECT 1") == 1
            assert await rows(env) == []
        finally:
            await holder.execute("SELECT pg_advisory_unlock(91234569)")
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_serialized_arq_retry_keeps_original_currency_after_reset(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    payload = {"origin_account_id": str(env.alice), "origin_account_generation": 7}
    first = deserialize_job(
        serialize_job(
            "resolve_entities_job",
            (str(env.alice), json.dumps([str(mid)])),
            payload,
            1,
            1700000000000,
        )
    )
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
    )
    retry = deserialize_job(
        serialize_job(first.function, first.args, first.kwargs, 2, 1700000000000)
    )
    assert retry.kwargs == payload
    decrypt = Mock(side_effect=AssertionError("retry cannot recapture"))
    monkeypatch.setattr(env.memory.encryption, "decrypt", decrypt)
    result = await jobs.resolve_entities_job(
        {"store": env.memory, "db_pool": env.pool}, *retry.args, **retry.kwargs
    )
    assert result["status"] == "skipped" and result["errors"] == ["stale_generation"]
    decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_real_background_scope_reserves_and_settles_scripted_confirmation(env, monkeypatch):
    import litellm
    from orchestrator import compute_runtime
    from orchestrator.entitlements.service import EntitlementService
    from tests.qualified_compute import install_qualified_compute

    mid, conv, embedding, _ = await prepare(env, monkeypatch)
    await canonical(env, mid)
    install_qualified_compute(monkeypatch)
    # Use actual account admission, reservation and settlement SQL. Only route
    # evidence and the outbound provider are synthetic, confined to this fixture.
    monkeypatch.setattr(compute_runtime, "EntitlementService", EntitlementService)
    monkeypatch.setattr(jobs, "account_compute", compute_runtime.account_compute)
    monkeypatch.setattr(entities, "guarded_completion", compute_runtime.guarded_completion)
    embedding.side_effect = lambda text: [1.0, 0.0] if text == "Robert" else [0.8, 0.6]

    async def provider(**kwargs):
        scope = compute_runtime.current_scope()
        assert scope.user_id == env.alice and scope.background and scope.profile == "background"
        await no_locks(env, mid, conv)
        return {
            "choices": [
                {"message": {"content": "YES: same fictional name"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
            "model": kwargs["model"],
        }

    outbound = AsyncMock(side_effect=provider)
    monkeypatch.setattr(litellm, "acompletion", outbound)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kwargs: 0.0)
    result = await run(env, [mid])
    assert result["status"] == "ok" and result["entities_created"] == 1
    outbound.assert_awaited_once()
    reservations = await env.pool.fetch(
        "SELECT * FROM entitlement_reservations WHERE user_id=$1", env.alice
    )
    assert len(reservations) == 1
    assert reservations[0]["background"] and reservations[0]["scope_id"] is not None
    assert reservations[0]["status"] == "settled"
    assert reservations[0]["actual_microusd"] == 0


@pytest.mark.asyncio
async def test_source_lock_remains_through_actual_commit_not_just_validation(env, monkeypatch):
    mid, _, _, _ = await prepare(env, monkeypatch)
    snapshot = await inputs(env, [mid])
    await env.pool.execute("""
      CREATE FUNCTION entity_commit_wait() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN PERFORM pg_advisory_xact_lock(91234570); RETURN NEW; END $$;
      CREATE CONSTRAINT TRIGGER entity_commit_wait AFTER INSERT ON entities
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION entity_commit_wait();
    """)
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as holder,
        env.pool.acquire() as editor,
    ):
        await holder.execute("SELECT pg_advisory_lock(91234570)")
        task = asyncio.create_task(
            save(
                env,
                snapshot,
                [EntityMutation(None, "Alice", "alice", mid)],
                store=bound_store(env, writer),
            )
        )
        edit = None
        try:
            await blocked(env.pool, writer.get_server_pid(), holder.get_server_pid())
            edit = asyncio.create_task(
                editor.execute("UPDATE memories SET memory_slot='late.edit' WHERE id=$1", mid)
            )
            await blocked(env.pool, editor.get_server_pid(), writer.get_server_pid())
            assert await rows(env) == []  # uncommitted insertion is not published
            await holder.execute("SELECT pg_advisory_unlock(91234570)")
            assert len(await asyncio.wait_for(task, 10)) == 1
            await asyncio.wait_for(edit, 10)
            assert (
                await editor.fetchval("SELECT memory_slot FROM memories WHERE id=$1", mid)
                == "late.edit"
            )
            assert len(await rows(env)) == 1
        finally:
            await holder.execute("SELECT pg_advisory_unlock(91234570)")
            for job in (task, edit):
                if job is not None and not job.done():
                    job.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if edit is not None:
                await asyncio.gather(edit, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["origin", "source", "foreign_target"])
async def test_final_writer_rejects_foreign_or_unplanned_targets_atomically(
    env, monkeypatch, corruption
):
    mid, _, _, _ = await prepare(env, monkeypatch)
    foreign = await source(env, owner=env.bob, text="foreign lineage")
    eid = await canonical(env, foreign, owner=env.bob)
    snapshot = await inputs(env, [mid])
    mutation = EntityMutation(None, "Alice", "alice", mid)
    if corruption == "origin":
        snapshot = replace(
            snapshot, snapshot=replace(snapshot.snapshot, origin=CapturedGeneration(env.bob, 7))
        )
    elif corruption == "source":
        mutation = replace(mutation, source_id=foreign)
    else:
        mutation = replace(mutation, canonical_id=eid, canonical_name="Robert", lookup_key="robert")
    before = await rows(env)
    with pytest.raises(EntityProjectionStale):
        await save(env, snapshot, [mutation])
    assert await rows(env) == before


@pytest.mark.asyncio
async def test_missing_guard_schema_cannot_authorize_optional_projection(env, monkeypatch):
    mid, _, embedding, model = await prepare(env, monkeypatch)
    await env.pool.execute("DROP TABLE account_suspension_causes")
    result = await run(env, [mid])
    assert result["status"] == "error" and result["entities_created"] == 0
    assert await rows(env) == []
    embedding.assert_not_awaited()
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_embedding_adapter_stays_refused_for_legacy_entity_consumer(
    env, monkeypatch
):
    from orchestrator.memory import embedding as embedding_module

    mid, _, _, model = await prepare(env, monkeypatch)
    await canonical(env, mid)
    before = await rows(env)
    monkeypatch.setattr(entities, "embed_query", embedding_module.embed_query)
    monkeypatch.setattr(
        embedding_module, "get_selected_embedding_route_id", lambda: "synthetic-qualified-route"
    )
    dispatch = AsyncMock(
        side_effect=AssertionError("legacy consumer cannot dispatch selected route")
    )
    monkeypatch.setattr(embedding_module, "embed_query_with_metadata", dispatch)
    result = await run(env, [mid])
    # Existing route refusal is preserved; no automatic route or adapter change.
    assert result["status"] == "ok" and result["entities_created"] == 1
    saved = await rows(env)
    assert all(row in saved for row in before)
    dispatch.assert_not_awaited()
    model.assert_not_awaited()
