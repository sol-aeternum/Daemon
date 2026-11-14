"""Actual raw-pool reset helpers on marked, owned synthetic PostgreSQL only."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import pytest

from orchestrator.eval import bench_db, fact_harness
from orchestrator.memory import revisions
from orchestrator.memory.lineage import record_sources
from orchestrator.memory.temporal import EventScope, TemporalReadScope, TemporalRefusal
from orchestrator.services.account_control.guard import WriteGuardRefusal
from tests.benchmark_reset_support import benchmark_env_fixture
from tests.durable_tasks_support import seed_entitlement

env = benchmark_env_fixture()


@pytest.mark.asyncio
async def test_summary_version_payload_and_closure_participate_in_owned_reset(env):
    from orchestrator.memory import summary_provenance as sp
    from orchestrator.services.account_control.guard import CapturedGeneration

    conv, _, _ = await seed(env)
    generation = await env.pool.fetchval(
        "SELECT content_generation FROM entitlement_accounts WHERE user_id=$1", env.alice
    )
    inputs = await sp.prepare(
        env.memory,
        conv["id"],
        CapturedGeneration(env.alice, generation),
        limit=20,
        render_limit=500,
    )
    assert inputs is not None
    assert await sp.publish(
        env.memory, inputs["summary_snapshot"], "Synthetic reset summary.", continuation=False
    )
    before = await fingerprint(env)
    assert before["tables"]["conversation_summary_payloads"]["rows"] == 1
    await wipe(env)
    after = await fingerprint(env)
    for table in (
        "conversation_summary_versions",
        "conversation_summary_sources",
        "conversation_summary_payloads",
    ):
        assert after["tables"][table]["rows"] == 0


async def seed(env, owner=None):
    owner = owner or env.alice
    conv = await env.memory.create_conversation(owner, title="Synthetic source")
    message = await env.memory.insert_message(
        conv["id"], owner, "user", "Original fictional source", status="complete"
    )
    memory = await env.memory.insert_memory(
        owner,
        "User likes fictional Amber.",
        "fact",
        "extracted",
        source_conversation_id=conv["id"],
        event_scope=EventScope(start_kind="unbounded", end_kind="unbounded", precision="exact"),
    )
    async with revisions.mutation(env.memory, await revisions.capture(env.memory, owner)) as conn:
        await record_sources(conn, owner, memory["id"], [message["id"]])
    return conv, message, memory


async def wipe(env, pool=None):
    return await bench_db.wipe_benchmark_user(
        pool or env.pool, env.alice, fact_harness.CANONICAL_RESET_STATEMENTS
    )


async def fingerprint(env, owner=None):
    return await bench_db.fingerprint_benchmark_state(env.pool, owner or env.alice)


@pytest.mark.asyncio
async def test_raw_orphan_helper_erases_lineage_and_derived_copies_but_not_foreign_owner(env):
    conv, message, root = await seed(env)
    other, _, _ = await seed(env, env.bob)
    child = await env.memory.publish_derived_memory(
        env.alice, "Fictional derived summary.", "summary", "consolidation", parents=[root]
    )
    foreign = await fingerprint(env, env.bob)
    result = await bench_db.rollback_orphan_conversations(env.pool, env.alice, {str(conv["id"])})
    assert result["memories"] == 2 and result["conversations"] == 1
    assert await env.memory.get_memory(child["id"]) is None
    assert await env.memory.get_conversation(conv["id"]) is None
    assert await env.memory.get_conversation(other["id"])
    assert await fingerprint(env, env.bob) == foreign
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_erased_sources WHERE user_id=$1 AND source_id=$2",
            env.alice,
            message["id"],
        )
        == 1
    )
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_dependencies WHERE user_id=$1", env.alice
        )
        == 0
    )


@pytest.mark.asyncio
async def test_content_reset_preserves_protections_spend_generation_and_old_cutoff_revocation(env):
    _, message, root = await seed(env)
    await seed(env, env.bob)
    cutoff = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    stale = await revisions.capture(env.memory, env.alice)
    await env.pool.execute(
        "INSERT INTO entitlement_usage_periods(user_id,period_key,spent_microusd,reserved_microusd) "
        "VALUES($1,'synthetic-period',123,7)",
        env.alice,
    )
    await env.pool.execute(
        "INSERT INTO entitlement_reservations(user_id,period_key,plan,operation,charge_kind,"
        "status,reserved_microusd,actual_microusd,settled_at) "
        "VALUES($1,'synthetic-period','power','agent','plan','settled',150,123,now())",
        env.alice,
    )
    usage = await env.pool.fetchval(
        "SELECT t::text FROM entitlement_usage_periods t WHERE user_id=$1", env.alice
    )
    spend = await env.pool.fetchval(
        "SELECT t::text FROM entitlement_reservations t WHERE user_id=$1", env.alice
    )
    account = await env.pool.fetchval(
        "SELECT t::text FROM entitlement_accounts t WHERE user_id=$1", env.alice
    )
    foreign = await fingerprint(env, env.bob)
    # A deliberately uncascaded synthetic edge must not survive the payload wipe.
    await env.pool.execute(
        "INSERT INTO memory_dependencies VALUES($1,$2,$3,'memory',$4)",
        env.alice,
        uuid4(),
        uuid4(),
        uuid4(),
    )
    counts = await wipe(env)
    assert counts["memories"] == 1 and counts["memory_dependencies"] == 1
    state = await fingerprint(env)
    assert all(
        v["rows"] == 0
        for k, v in state["tables"].items()
        if k not in bench_db.RETAINED_PROTECTION_TABLES
    )
    assert state["tables"]["memory_tombstones"]["rows"] == 1
    assert state["tables"]["memory_erased_sources"]["rows"] == 1
    assert (
        await env.pool.fetchval(
            "SELECT t::text FROM entitlement_accounts t WHERE user_id=$1", env.alice
        )
        == account
    )
    assert (
        await env.pool.fetchval(
            "SELECT t::text FROM entitlement_usage_periods t WHERE user_id=$1", env.alice
        )
        == usage
    )
    assert (
        await env.pool.fetchval(
            "SELECT t::text FROM entitlement_reservations t WHERE user_id=$1", env.alice
        )
        == spend
    )
    assert await fingerprint(env, env.bob) == foreign
    assert await revisions.search(env.memory, env.alice, cutoff) == []
    with pytest.raises(TemporalRefusal, match="erasure_changed"):
        async with revisions.mutation(env.memory, stale):
            pytest.fail("old capture cannot publish")
    again = await env.memory.insert_memory(env.alice, root["content"], "fact", "user_created")
    assert again["id"] != root["id"]
    assert await revisions.search(env.memory, env.alice, cutoff) == []
    assert await env.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memory_erased_sources WHERE source_id=$1)", message["id"]
    )
    before = await env.pool.fetchrow(
        "SELECT * FROM memory_knowledge_clock WHERE user_id=$1", env.alice
    )
    await wipe(env)
    await wipe(env)  # Empty payloads still fence a pre-reset capture.
    after = await env.pool.fetchrow(
        "SELECT * FROM memory_knowledge_clock WHERE user_id=$1", env.alice
    )
    assert (
        after["sequence"] > before["sequence"] and after["erasure_epoch"] > before["erasure_epoch"]
    )
    assert after["account_generation"] == before["account_generation"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        "missing",
        "restricted",
        "foreign_user",
        "foreign_capture",
        "foreign_conversation",
        "stale_capture",
    ],
)
async def test_reset_refusals_never_recapture_provision_or_mutate_payload(env, refusal):
    conv, _, _ = await seed(env)
    if refusal == "missing":
        await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    elif refusal == "restricted":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
    elif refusal == "foreign_user":
        await env.pool.execute(
            "UPDATE users SET email='unrelated@daemon.test' WHERE id=$1", env.alice
        )
    before = await fingerprint(env)
    if refusal == "foreign_capture":
        async with revisions.original_capture(env.memory, env.bob):
            with pytest.raises(TemporalRefusal, match="owner_mismatch"):
                await wipe(env)
    elif refusal == "foreign_conversation":
        foreign = await env.memory.create_conversation(env.bob)
        with pytest.raises(bench_db.BenchmarkDatabaseGuardError, match="ownership"):
            await bench_db.rollback_orphan_conversations(
                env.pool, env.alice, {str(conv["id"]), str(foreign["id"])}
            )
    elif refusal == "stale_capture":
        start = asyncio.Event()

        async def reset():
            await start.wait()
            await wipe(env)

        resetter = asyncio.create_task(reset())
        async with revisions.original_capture(env.memory, env.alice):
            start.set()
            await asyncio.wait_for(resetter, 10)
            before = await fingerprint(env)
            with pytest.raises(TemporalRefusal, match="erasure_changed"):
                await wipe(env)
    else:
        with pytest.raises((WriteGuardRefusal, bench_db.BenchmarkDatabaseGuardError)):
            await wipe(env)
    assert await fingerprint(env) == before
    if refusal == "missing":
        assert (
            await env.pool.fetchval(
                "SELECT count(*) FROM entitlement_accounts WHERE user_id=$1", env.alice
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "segment",
        "dependency",
        "source",
        "clock",
        "mutation",
        "tombstone",
        "erased_source",
        "generation",
        "clock_regressed",
        "missing_clock",
        "clock_rewind",
        "permission",
        "account_restricted",
    ],
)
async def test_temporal_only_changes_are_in_owner_qualified_fingerprints(env, change):
    _, _, root = await seed(env)
    before = await fingerprint(env)
    foreign = await fingerprint(env, env.bob)
    if change == "segment":
        await env.pool.execute(
            "UPDATE memory_revision_segments SET content='synthetic altered ciphertext' WHERE revision_id=$1",
            root["head_revision_id"],
        )
    elif change == "dependency":
        await env.pool.execute(
            "INSERT INTO memory_dependencies VALUES($1,$2,$3,'memory',$4)",
            env.alice,
            root["id"],
            root["head_revision_id"],
            uuid4(),
        )
    elif change == "source":
        await env.pool.execute(
            "INSERT INTO memory_sources VALUES($1,$2,'message',$3)", env.alice, root["id"], uuid4()
        )
    elif change == "clock":
        await env.pool.execute(
            "UPDATE memory_knowledge_clock SET erasure_epoch=erasure_epoch+1 WHERE user_id=$1",
            env.alice,
        )
    elif change == "mutation":
        await env.pool.execute(
            "UPDATE memory_mutations SET write_lower_at=write_lower_at-interval '1 second' WHERE user_id=$1",
            env.alice,
        )
    elif change == "generation":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
            env.alice,
        )
    elif change == "clock_regressed":
        await env.pool.execute(
            "UPDATE memory_knowledge_clock SET clock_regressed=TRUE WHERE user_id=$1", env.alice
        )
    elif change == "missing_clock":
        await env.pool.execute("DELETE FROM memory_knowledge_clock WHERE user_id=$1", env.alice)
    elif change == "clock_rewind":
        await env.pool.execute(
            "UPDATE memory_knowledge_clock SET sequence=0 WHERE user_id=$1", env.alice
        )
    elif change == "permission":
        await env.pool.execute(
            "UPDATE conversations SET pipeline='local' WHERE user_id=$1", env.alice
        )
    elif change == "account_restricted":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", env.alice
        )
    elif change == "tombstone":
        await env.pool.execute(
            "INSERT INTO memory_tombstones(user_id,memory_id,erasure_epoch,erased_at,hard) VALUES($1,$2,0,now(),true)",
            env.alice,
            uuid4(),
        )
    else:
        await env.pool.execute(
            "INSERT INTO memory_erased_sources VALUES($1,'message',$2,$3)",
            env.alice,
            uuid4(),
            uuid4(),
        )
    assert (await fingerprint(env))["sha256"] != before["sha256"]
    assert await fingerprint(env, env.bob) == foreign


@pytest.mark.asyncio
async def test_commit_observation_bookkeeping_is_not_evaluation_contamination(env):
    await seed(env)
    before = await fingerprint(env)
    physical = await env.pool.fetchval(
        "SELECT t::text FROM memory_knowledge_clock t WHERE user_id=$1", env.alice
    )
    async with revisions.mutation(env.memory, await revisions.capture(env.memory, env.alice)):
        pass  # Diagnostic-only publication; no assertion/dependency/source changes.
    assert (
        await env.pool.fetchval(
            "SELECT t::text FROM memory_knowledge_clock t WHERE user_id=$1", env.alice
        )
        != physical
    )
    await env.pool.execute(
        "UPDATE memory_mutations SET observed_committed_by=observed_committed_by+interval '1 second' WHERE user_id=$1",
        env.alice,
    )
    assert await fingerprint(env) == before


@pytest.mark.asyncio
async def test_canonical_reset_acknowledges_each_owner_before_checkpoint_removal(env, tmp_path):
    await seed(env)
    await seed(env, env.bob)
    foreign = await fingerprint(env, env.bob)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("retained recovery checkpoint")
    result = await fact_harness.reset_canonical_benchmark(env.pool, checkpoint)
    assert result.success and not checkpoint.exists()
    assert result.owner_outcomes[str(env.alice)] == "completed"
    assert result.tables_cleared["memories"] == 1
    assert await fingerprint(env, env.bob) == foreign
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_tombstones WHERE user_id=$1", env.alice
        )
        == 1
    )


@pytest.mark.asyncio
async def test_canonical_reset_reports_only_acknowledged_owner_counts_on_partial_failure(
    env, tmp_path
):
    await seed(env)
    await seed(env, env.bob)
    await env.pool.execute(
        "UPDATE users SET email=$2 WHERE id=$1", env.bob, f"longmemeval+{env.bob.hex}@daemon.test"
    )
    first, second = sorted((env.alice, env.bob))
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", second
    )
    unchanged = await fingerprint(env, second)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("recovery evidence")
    result = await fact_harness.reset_canonical_benchmark(env.pool, checkpoint)
    assert not result.success and checkpoint.read_text() == "recovery evidence"
    assert result.owner_outcomes[str(first)] == "completed"
    assert result.owner_outcomes[str(second)] == "not_confirmed"
    assert result.tables_cleared["memories"] == 1
    assert await fingerprint(env, second) == unchanged
    assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", first) == 0


@pytest.mark.asyncio
async def test_revision_segment_fingerprint_never_includes_another_owner(env):
    await seed(env)
    _, _, foreign = await seed(env, env.bob)
    before = await fingerprint(env)
    other = await fingerprint(env, env.bob)
    await env.pool.execute(
        "UPDATE memory_revision_segments SET content='foreign synthetic change' WHERE revision_id=$1",
        foreign["head_revision_id"],
    )
    assert await fingerprint(env) == before
    assert await fingerprint(env, env.bob) != other


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["generation", "suspension"])
async def test_helper_rechecks_original_account_capture_without_refresh(env, monkeypatch, control):
    await seed(env)
    before = await fingerprint(env)
    capture = bench_db.capture_for

    async def change_after_capture(store, owner):
        nonlocal before
        original = await capture(store, owner)
        sql = (
            "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1"
            if control == "generation"
            else "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1"
        )
        await env.pool.execute(sql, owner)
        changed = await fingerprint(env)
        assert changed != before  # Current account permissions are protected too.
        before = changed  # The refused reset must not mutate this new control state.
        return original

    monkeypatch.setattr(bench_db, "capture_for", change_after_capture)
    with pytest.raises(WriteGuardRefusal):
        await wipe(env)
    assert await fingerprint(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_sql_or_commit_failure_preserves_checkpoint_and_rolls_back_payload(
    env, tmp_path, deferred
):
    await seed(env)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("recovery evidence")
    before = await fingerprint(env)
    await env.pool.execute(
        "CREATE FUNCTION reset_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic reset failure'; END $$"
    )
    trigger = (
        "CREATE CONSTRAINT TRIGGER reset_failure AFTER DELETE ON conversations DEFERRABLE INITIALLY DEFERRED"
        if deferred
        else "CREATE TRIGGER reset_failure BEFORE DELETE ON conversations"
    )
    await env.pool.execute(trigger + " FOR EACH ROW EXECUTE FUNCTION reset_failure()")
    result = await fact_harness.reset_canonical_benchmark(env.pool, checkpoint)
    assert not result.success and result.owner_outcomes[str(env.alice)] == "not_confirmed"
    assert checkpoint.read_text() == "recovery evidence"
    assert await fingerprint(env) == before


@pytest.mark.asyncio
async def test_reset_cancellation_rolls_back_and_keeps_checkpoint(env, tmp_path, monkeypatch):
    await seed(env)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("recovery evidence")
    before = await fingerprint(env)
    entered = asyncio.Event()
    actual = bench_db._erase_synthetic_lineages

    async def pause(*args):
        result = await actual(*args)
        entered.set()
        await asyncio.Event().wait()
        return result

    monkeypatch.setattr(bench_db, "_erase_synthetic_lineages", pause)
    task = asyncio.create_task(fact_harness.reset_canonical_benchmark(env.pool, checkpoint))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert checkpoint.read_text() == "recovery evidence"
    assert await fingerprint(env) == before


@pytest.mark.asyncio
async def test_acknowledged_owner_purge_is_not_replayed_after_checkpoint_failure(
    env, tmp_path, monkeypatch
):
    await seed(env)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("recovery evidence")

    def failed_unlink(path):
        raise OSError("synthetic checkpoint failure")

    monkeypatch.setattr(fact_harness, "_reset_checkpoint_file", failed_unlink)
    result = await fact_harness.reset_canonical_benchmark(env.pool, checkpoint)
    assert not result.success and result.owner_outcomes[str(env.alice)] == "completed"
    assert checkpoint.read_text() == "recovery evidence"
    assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", env.alice) == 0
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_tombstones WHERE user_id=$1", env.alice
        )
        == 1
    )


@pytest.mark.asyncio
async def test_unknown_commit_outcome_keeps_checkpoint_and_reports_unconfirmed(env, tmp_path):
    await seed(env)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("recovery evidence")

    class LostCommitConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        @asynccontextmanager
        async def transaction(self, **kwargs):
            assert kwargs == {"isolation": "read_committed"}
            async with self.connection.transaction(**kwargs):
                yield
            raise OSError("synthetic lost COMMIT receipt")

    class LostCommitPool:
        def __getattr__(self, name):
            return getattr(env.pool, name)

        @asynccontextmanager
        async def acquire(self):
            async with env.pool.acquire() as conn:
                yield LostCommitConnection(conn)

    result = await fact_harness.reset_canonical_benchmark(cast(Any, LostCommitPool()), checkpoint)
    assert not result.success and result.owner_outcomes[str(env.alice)] == "not_confirmed"
    assert checkpoint.read_text() == "recovery evidence"
    # Observer-side evidence shows SQL committed; the caller must not infer that
    # from the failed receipt, unlink the checkpoint or automatically replay it.
    assert await env.pool.fetchval("SELECT count(*) FROM memories WHERE user_id=$1", env.alice) == 0
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM memory_tombstones WHERE user_id=$1", env.alice
        )
        == 1
    )


@pytest.mark.asyncio
async def test_reset_verifier_separates_zero_payload_from_retained_protections(env, tmp_path):
    from tests.benchmark_harness import reset_verify_helper as verifier

    owner = verifier.TEST_USER_ID
    await env.pool.execute(
        "INSERT INTO users(id,email,name,username) VALUES($1,'longmemeval@daemon.test','synthetic legacy','synthetic-legacy')",
        owner,
    )
    await seed_entitlement(env.pool, owner)
    await seed(env, owner)
    result = await verifier.full_reset_with_verification(
        env.pool, tmp_path / "checkpoint", cleanup_redis=False
    )
    assert result.success and result.all_zero
    assert not any(result.row_counts_after_reset.values())
    assert result.retained_protection["memory_knowledge_clock"] == 1
    assert result.retained_protection["memory_tombstones"] == 1


@pytest.mark.asyncio
async def test_unmarked_database_and_protection_deletion_statements_fail_closed(env, monkeypatch):
    await seed(env)
    before = await fingerprint(env)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/not_a_benchmark")
    from orchestrator.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(bench_db.BenchmarkDatabaseGuardError):
        await wipe(env)
    with pytest.raises(ValueError, match="fixed content-only"):
        await bench_db.wipe_benchmark_user(
            env.pool, env.alice, [("memory_tombstones", "DELETE FROM memory_tombstones")]
        )
    assert await fingerprint(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["uncertain", "leftover"])
async def test_reset_confirmation_never_replays_uncertain_or_unexplained_effects(
    monkeypatch, tmp_path, failure
):
    from unittest.mock import AsyncMock
    from tests.benchmark_harness import reset_verify_helper as verifier

    first = verifier.ExtendedResetResult(
        success=failure != "uncertain", all_zero=False, error="synthetic unconfirmed effect"
    )
    reset = AsyncMock(return_value=first)
    monkeypatch.setattr(verifier, "full_reset_with_verification", reset)
    result = await verifier.double_reset_for_confirmation(cast(Any, None), tmp_path / "checkpoint")
    reset.assert_awaited_once()
    assert not result["confirmed_clean"]
    assert result["second_result"]["error"] == "confirmation_not_attempted"
