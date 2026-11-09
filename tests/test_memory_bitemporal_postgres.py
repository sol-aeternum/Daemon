"""Real persistence/locks; no inference, application DSN or production state."""

from datetime import UTC, datetime, timedelta
from secrets import token_hex

import asyncpg
import pytest

from orchestrator.memory.revisions import capture, mutation, resolve, search
from orchestrator.memory.store import compute_memory_content_hash
from orchestrator.memory.temporal import EventScope, TemporalReadScope, TemporalRefusal
from tests.durable_tasks_support import durable_env_fixture

env = durable_env_fixture(initialized=True)


def event(start=None, end=None):
    return EventScope(
        start=start,
        end=end,
        start_kind="exact" if start else "unbounded",
        end_kind="exact" if end else "unbounded",
        precision="exact",
    )


async def add(env, text="User lives in fictional Amber.", **kwargs):
    return await env.memory.insert_memory(env.alice, text, "fact", "user_created", **kwargs)


@pytest.mark.asyncio
async def test_immutable_correction_and_stable_id(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    first = await add(env, event_scope=event())
    cutoff = await resolve(env.memory, env.alice, TemporalReadScope())
    second = await env.memory.update_memory_content(
        first["id"],
        "User lives in fictional Blue.",
        user_id=env.alice,
        expected_revision_id=first["head_revision_id"],
        clear_embedding=True,
    )
    assert second["id"] == first["id"] and second["head_revision_id"] != first["head_revision_id"]
    old = await search(env.memory, env.alice, cutoff)
    assert [r["content"] for r in old] == [first["content"]]
    assert (
        await env.memory.update_memory_content(
            first["id"],
            "Stale edit",
            user_id=env.alice,
            expected_revision_id=first["head_revision_id"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_world_change_keeps_old_world_and_old_knowledge(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    t = datetime(2020, 5, 1, tzinfo=UTC)
    first = await add(env, event_scope=event())
    before = await resolve(env.memory, env.alice, TemporalReadScope(event_at=t + timedelta(days=1)))
    await env.memory.update_memory_content(
        first["id"],
        "User lives in fictional Blue.",
        user_id=env.alice,
        expected_revision_id=first["head_revision_id"],
        event_scope=event(t),
        relationship="world_change",
    )
    old_world = await resolve(
        env.memory, env.alice, TemporalReadScope(event_at=t - timedelta(seconds=1))
    )
    new_world = await resolve(env.memory, env.alice, TemporalReadScope(event_at=t))
    assert (await search(env.memory, env.alice, old_world))[0]["content"] == first["content"]
    assert "Blue" in (await search(env.memory, env.alice, new_world))[0]["content"]
    assert (await search(env.memory, env.alice, before))[0]["content"] == first["content"]


@pytest.mark.asyncio
async def test_raw_writer_fails_closed(env):
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await env.pool.execute(
            "INSERT INTO memories(user_id,content,category,source_type) VALUES($1,'cipher','fact','manual')",
            env.alice,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_elsewhere", [False, True])
async def test_backfill_duplicate_closure_rechecks_null_hash_after_serialized_completion(
    env, monkeypatch, completed_elsewhere
):
    """Real unique violation and triggers; competing writes own the account clock.

    Pause after the failed autocommit hash update, before the closure capture.
    A same-account competitor cannot commit inside a held mutation; it instead
    finishes here, before backfill takes its own fresh capture/serialized clock.
    No trigger, registered transaction, or SQL result is replaced by a fake.
    """
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    canonical = await add(env)
    candidate = await add(env, "Different fictional legacy text.")
    async with mutation(env.memory, await capture(env.memory, env.alice)) as conn:
        await conn.execute(
            "UPDATE memories SET content=$2,content_hash=NULL,content_tsv=to_tsvector('english',$3) "
            "WHERE id=$1",
            candidate["id"],
            env.memory.encryption.encrypt(canonical["content"]),
            canonical["content"],
        )
    before = await env.pool.fetchval(
        "SELECT count(*) FROM memory_revisions WHERE memory_id=$1", candidate["id"]
    )
    pool = env.pool
    conflicts = 0
    closure_captures = 0

    class InterleavedPool:
        def __getattr__(self, name):
            return getattr(pool, name)

        async def execute(self, sql, *args):
            nonlocal conflicts
            try:
                return await pool.execute(sql, *args)
            except asyncpg.UniqueViolationError:
                conflicts += 1
                raise

        async def fetchval(self, sql, *args):
            nonlocal closure_captures
            if sql == "SELECT user_id FROM memories WHERE id=$1":
                assert args == (candidate["id"],) and conflicts == 1
                closure_captures += 1
                if completed_elsewhere:
                    async with mutation(env.memory, await capture(env.memory, env.alice)) as conn:
                        # Retiring the old canonical row frees the real exact-hash
                        # constraint; a different writer completes the candidate.
                        await conn.execute(
                            "UPDATE memories SET valid_to=clock_timestamp() WHERE id=$1",
                            canonical["id"],
                        )
                        assert (
                            await conn.execute(
                                "UPDATE memories SET content_hash=$2 WHERE id=$1 AND content_hash IS NULL",
                                candidate["id"],
                                compute_memory_content_hash(canonical["content"]),
                            )
                            == "UPDATE 1"
                        )
            return await pool.fetchval(sql, *args)

    monkeypatch.setattr(env.memory, "_database_pool", InterleavedPool())
    assert await env.memory.backfill_memory_content_hashes() == 0
    assert conflicts == closure_captures == 1
    row = await pool.fetchrow("SELECT * FROM memories WHERE id=$1", candidate["id"])
    assert row is not None and row["user_id"] == env.alice
    assert (row["valid_to"] is None) is completed_elsewhere
    assert row["content_hash"] == (
        compute_memory_content_hash(canonical["content"]) if completed_elsewhere else None
    )
    assert await pool.fetchval(
        "SELECT count(*) FROM memory_revisions WHERE memory_id=$1", candidate["id"]
    ) == before + int(not completed_elsewhere)
    assert (
        await pool.fetchval("SELECT count(*) FROM memory_mutations WHERE user_id=$1", env.bob) == 0
    )


@pytest.mark.asyncio
async def test_delayed_commit_timestamp_bracket_stays_indeterminate(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    original = await capture(env.memory, env.alice)
    async with env.pool.acquire() as conn, conn.transaction():
        async with mutation(env.memory, original, conn=conn):
            await add(env)
            inside = await conn.fetchval("SELECT clock_timestamp()")
    with pytest.raises(TemporalRefusal, match="indeterminate"):
        await resolve(env.memory, env.alice, TemporalReadScope(known_at=inside))
    assert await env.pool.fetchval(
        "SELECT observed_committed_by IS NOT NULL FROM memory_mutations WHERE user_id=$1", env.alice
    )
    # Sealing observes an upper bound; it does NOT retroactively turn the
    # timestamp inside the original bracket into a determinate cutoff.
    with pytest.raises(TemporalRefusal, match="indeterminate"):
        await resolve(env.memory, env.alice, TemporalReadScope(known_at=inside))


@pytest.mark.asyncio
async def test_owned_write_has_postcommit_receipt_and_resolves_later_cutoff(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    await add(env)
    receipt = await env.pool.fetchrow("SELECT * FROM memory_mutations WHERE user_id=$1", env.alice)
    assert receipt["observed_committed_by"] is not None
    assert receipt["observed_committed_by"] >= receipt["write_lower_at"]
    cutoff = await env.pool.fetchval("SELECT clock_timestamp()")
    resolved = await resolve(env.memory, env.alice, TemporalReadScope(known_at=cutoff))
    assert resolved.watermark is not None
    assert resolved.watermark.sequence == receipt["sequence"]


@pytest.mark.asyncio
async def test_caller_owned_commit_is_unsealed_until_durable_recovery(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    original = await capture(env.memory, env.alice)
    async with env.pool.acquire() as conn, conn.transaction():
        async with mutation(env.memory, original, conn=conn):
            await add(env)
    assert (
        await env.pool.fetchval(
            "SELECT observed_committed_by FROM memory_mutations WHERE user_id=$1", env.alice
        )
        is None
    )
    in_gap = await env.pool.fetchval("SELECT clock_timestamp()")
    with pytest.raises(TemporalRefusal, match="indeterminate"):
        await resolve(env.memory, env.alice, TemporalReadScope(known_at=in_gap))
    upper = await env.pool.fetchval(
        "SELECT observed_committed_by FROM memory_mutations WHERE user_id=$1", env.alice
    )
    assert upper is not None and upper > in_gap
    # Recovery commits the bound but cannot invent visibility within the gap.
    with pytest.raises(TemporalRefusal, match="indeterminate"):
        await resolve(env.memory, env.alice, TemporalReadScope(known_at=in_gap))


@pytest.mark.asyncio
async def test_erasure_revokes_old_watermark_and_clears_indexes(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    first = await add(env, event_scope=event())
    cutoff = await resolve(env.memory, env.alice, TemporalReadScope())
    stale = await capture(env.memory, env.alice)
    assert await env.memory.delete_memory(first["id"], user_id=env.alice)
    assert await search(env.memory, env.alice, cutoff) == []
    assert not await env.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM memory_revision_segments WHERE content_tsv IS NOT NULL OR embedding IS NOT NULL)"
    )
    with pytest.raises(TemporalRefusal, match="erasure_changed"):
        async with mutation(env.memory, stale):
            pass
    assert await env.memory.delete_memory(first["id"], user_id=env.alice, soft=False)
    assert await env.pool.fetchval("SELECT count(*) FROM memory_revision_segments") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM memory_tombstones") == 1


@pytest.mark.asyncio
async def test_unknown_event_is_not_strict_match_and_scope_precedes_limit(env, monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", token_hex(32))
    await add(env)
    exact = await add(env, "User visits fictional Silver.", event_scope=event())
    cutoff = await resolve(
        env.memory, env.alice, TemporalReadScope(event_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    rows = await search(env.memory, env.alice, cutoff, limit=1)
    assert [r["id"] for r in rows] == [exact["id"]]
