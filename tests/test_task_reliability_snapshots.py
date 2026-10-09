"""Atomic task/snapshot crash boundaries against disposable PostgreSQL only."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
import asyncpg

from orchestrator.config import Settings
from orchestrator.services.fetch.models import EXTRACTION_VERSION_V1
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.web_snapshots import WebSnapshotStore
from orchestrator.tasks import runner
from orchestrator.tasks.store import ExecutionRefused
from orchestrator.tools.registry import ToolRegistry
from orchestrator.tools.web_fetch import WebFetchTool
from tests.durable_tasks_support import (
    LEASE_S,
    Env,
    accept_task,
    durable_env_fixture,
    expire_lease,
)

env = durable_env_fixture()
URL = "https://example.test/source"


def _reader(env, accepted, claim):
    snapshots = WebSnapshotStore(env.pool, env.memory._enc, Settings())
    fetcher = SimpleNamespace(
        fetch=AsyncMock(
            return_value=SimpleNamespace(
                source_url=URL,
                final_url=URL,
                title="test",
                content="immutable content",
                extraction_version=EXTRACTION_VERSION_V1,
            )
        )
    )
    tool = WebFetchTool(snapshots, env.alice, accepted.conversation_id)
    tool._fetch_service = cast(FetchService, fetcher)
    registry = ToolRegistry()
    registry.register(tool)
    runner._guard_tools(registry, env.tasks, runner.AttemptState(claim=claim))
    return tool, snapshots, fetcher


async def _counts(env):
    return (
        await env.pool.fetchval("SELECT count(*) FROM web_snapshots"),
        await env.pool.fetchval("SELECT count(*) FROM task_events WHERE kind = 'page_refreshed'"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
async def test_crash_after_shared_commit_reuses_exact_snapshot(env: Env, monkeypatch, refresh):
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    assert first is not None
    tool, snapshots, fetcher = _reader(env, accepted, first)
    create = snapshots.create

    async def crash_after_commit(*args, **kwargs):
        await create(*args, **kwargs)
        raise RuntimeError("process lost after shared commit")

    monkeypatch.setattr(snapshots, "create", crash_after_commit)
    # Old secondary marker recording must never run.
    old_marker = AsyncMock(side_effect=AssertionError())
    monkeypatch.setattr(tool.refresh_guard, "record", old_marker, raising=False)
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=refresh))
    assert await _counts(env) == (1, 1)
    saved_id = await env.pool.fetchval("SELECT id FROM web_snapshots")
    await expire_lease(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="second", lease_s=LEASE_S)
    assert second is not None
    retry, _, _ = _reader(env, accepted, second)
    retry._fetch_service = cast(FetchService, fetcher)
    result = json.loads(await retry.execute(url=URL, force_refresh=refresh))
    assert result["snapshot_id"] == str(saved_id)
    assert fetcher.fetch.await_count == 1
    assert await _counts(env) == (1, 1)
    old_marker.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
async def test_failure_before_commit_rolls_back_both_records(env: Env, monkeypatch, refresh):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    tool, _, fetcher = _reader(env, accepted, claim)
    append = env.tasks._append_event

    async def crash_before_commit(conn, task_id, kind, payload=None):
        seq = await append(conn, task_id, kind, payload)
        if kind == "page_refreshed":
            raise RuntimeError("lost before commit")
        return seq

    monkeypatch.setattr(env.tasks, "_append_event", crash_before_commit)
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=refresh))
    assert fetcher.fetch.await_count == 1
    assert await _counts(env) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "missing"])
async def test_pinned_unavailable_never_refetches_or_uses_newer_row(env: Env, failure):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    tool, snapshots, fetcher = _reader(env, accepted, claim)
    pinned = json.loads(await tool.execute(url=URL, force_refresh=True))["snapshot_id"]
    await snapshots.create(
        env.alice,
        accepted.conversation_id,
        source_url=URL,
        content="newer",
        extract_mode="article",
        extraction_version=EXTRACTION_VERSION_V1,
    )
    if failure == "expired":
        await env.pool.execute(
            "UPDATE web_snapshots SET retrieved_at = now() - interval '2 days', "
            "expires_at = now() - interval '1 second' "
            "WHERE id = $1::text::uuid",
            pinned,
        )
    else:
        await env.pool.execute("DELETE FROM web_snapshots WHERE id = $1::text::uuid", pinned)
    result = json.loads(await tool.execute(url=URL, force_refresh=True))
    assert result == {"error": "snapshot_expired"}
    assert fetcher.fetch.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["expire", "cancel", "takeover", "delete"])
async def test_publication_rechecks_after_waiting_for_locks(env: Env, monkeypatch, refusal):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    tool, snapshots, _ = _reader(env, accepted, claim)
    waiting = asyncio.Event()
    lock_account = snapshots._lock_account

    async def observed_lock(conn, user):
        waiting.set()
        await lock_account(conn, user)

    monkeypatch.setattr(snapshots, "_lock_account", observed_lock)
    async with env.pool.acquire() as blocker, blocker.transaction():
        # Match the snapshot serialization lock, allowing task-control FK checks.
        await blocker.execute("SELECT id FROM users WHERE id = $1 FOR NO KEY UPDATE", env.alice)
        publication = asyncio.create_task(tool.execute(url=URL, force_refresh=True))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        if refusal in {"expire", "takeover"}:
            await expire_lease(env, accepted.task_id)
            if refusal == "takeover":
                assert await env.tasks.claim(accepted.task_id, worker_id="next", lease_s=LEASE_S)
        elif refusal == "cancel":
            await asyncio.wait_for(env.tasks.request_cancel(env.alice, accepted.task_id), 5)
        else:
            await blocker.execute(
                "DELETE FROM conversations WHERE id = $1", accepted.conversation_id
            )
    assert "error" in json.loads(await asyncio.wait_for(publication, timeout=5))
    assert await _counts(env) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusive_parent", [None, "account", "conversation"])
async def test_cancel_snapshot_fk_lock_interleaving(env: Env, monkeypatch, exclusive_parent):
    """Both old parent locks create a real cycle, not just a blocked test driver.

    Cancellation holds task; publication holds parents; cancellation's second
    UPDATE rechecks FKs while publication enters the task fence. The production
    locks must let cancellation commit and publication roll back. Reinstating
    either old lock is a negative control: PostgreSQL detects the cycle.
    """
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    assert claim is not None
    _, snapshots, _ = _reader(env, accepted, claim)
    cancel_locked = asyncio.Event()
    parents_locked = asyncio.Event()
    append = env.tasks._append_event

    async def gated_append(conn, task_id, kind, payload=None):
        if kind == "cancel_requested":
            cancel_locked.set()
            await asyncio.wait_for(parents_locked.wait(), 5)
        return await append(conn, task_id, kind, payload)

    monkeypatch.setattr(env.tasks, "_append_event", gated_append)
    if exclusive_parent == "account":

        async def old_account(conn, user):
            await conn.fetchval("SELECT id FROM users WHERE id = $1 FOR UPDATE", user)

        monkeypatch.setattr(snapshots, "_lock_account", old_account)
    elif exclusive_parent == "conversation":

        async def old_conversation(conn, user, conversation):
            await conn.fetchval(
                "SELECT id FROM conversations WHERE id = $1 AND user_id = $2 FOR UPDATE",
                conversation,
                user,
            )

        monkeypatch.setattr(snapshots, "_lock_owned_conversation", old_conversation)

    async def publish(conn, snapshot_id):
        parents_locked.set()
        await env.tasks.pin_page(conn, claim, "lock-interleaving", snapshot_id)

    cancel = asyncio.create_task(env.tasks.request_cancel(env.alice, accepted.task_id))
    publication = None
    try:
        await asyncio.wait_for(cancel_locked.wait(), 5)
        publication = asyncio.create_task(
            snapshots.create(
                env.alice,
                accepted.conversation_id,
                source_url=URL,
                content="lock probe",
                extract_mode="article",
                extraction_version=EXTRACTION_VERSION_V1,
                on_created=publish,
            )
        )
        results = await asyncio.wait_for(
            asyncio.gather(cancel, publication, return_exceptions=True), 8
        )
        if exclusive_parent is not None:
            assert any(isinstance(result, asyncpg.DeadlockDetectedError) for result in results)
        else:
            assert not isinstance(results[0], BaseException)
            assert isinstance(results[1], ExecutionRefused)
            assert results[1].reason == "cancel_requested"
            assert await _counts(env) == (0, 0)
    finally:
        tasks = [cancel] + ([publication] if publication is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(cancel, return_exceptions=True)
        if publication is not None:
            await asyncio.gather(publication, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "conversation"])
async def test_quota_and_claim_owner_mismatch_rollback_provenance(env: Env, mismatch):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    tool, snapshots, _ = _reader(env, accepted, claim)
    snapshots._settings = snapshots.settings.model_copy(
        update={"web_snapshot_max_conversation_count": 0}
    )
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=True))
    assert await _counts(env) == (0, 0)
    snapshots._settings = Settings()
    assert claim is not None
    tool.refresh_guard = runner._TaskRefreshGuard(
        env.tasks,
        runner.AttemptState(
            claim=replace(claim, user_id=env.bob)
            if mismatch == "owner"
            else replace(claim, conversation_id=uuid.uuid4())
        ),
    )
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=True))
    assert await _counts(env) == (0, 0)


@pytest.mark.asyncio
async def test_legacy_marker_has_no_invented_snapshot_provenance(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    assert claim is not None
    tool, _, fetcher = _reader(env, accepted, claim)
    await env.tasks.record_event(
        accepted.task_id, claim.epoch, "page_refreshed", {"key": runner._page_key(URL, "article")}
    )
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=True))
    fetcher.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_expiring_during_marker_work_rolls_back_snapshot(env: Env, monkeypatch):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="first", lease_s=LEASE_S)
    tool, _, _ = _reader(env, accepted, claim)
    append = env.tasks._append_event

    async def expire_during_event(conn, task_id, kind, payload=None):
        seq = await append(conn, task_id, kind, payload)
        if kind == "page_refreshed":
            await conn.execute(
                "UPDATE tasks SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE id = $1",
                task_id,
            )
        return seq

    monkeypatch.setattr(env.tasks, "_append_event", expire_during_event)
    assert "error" in json.loads(await tool.execute(url=URL, force_refresh=True))
    assert await _counts(env) == (0, 0)
