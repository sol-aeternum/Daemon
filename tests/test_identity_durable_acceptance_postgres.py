"""Merged birth prerequisite -> actual authenticated PR527 acceptance SQL.

Fresh hosted users are never preseeded; only external proof/provider boundaries
are scripted. No missing-row repair, lifecycle control implementation or worker
output qualification is implied by these tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator import compute_runtime, main
from orchestrator.auth import require_device_auth
from orchestrator.tasks.store import TaskStore
from tests.qualified_compute import install_qualified_compute
from tests.test_identity_claim_postgres import _prepare
from tests.test_identity_initialization_postgres import account_row, complete, env as env
from tests.test_task_acceptance_account_guard_postgres import counts, restrict


async def fresh_client(env, monkeypatch, provider):
    app, settings, address, _, _, payload = await _prepare(env, monkeypatch, provider)
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_accounts") == 0
    signed_in = await complete(app, provider, payload)
    assert signed_in.status_code == 200, signed_in.text
    uid = await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address)
    initialized = await account_row(env, uid)
    assert initialized is not None and initialized["content_generation"] == 0
    settings.durable_chat_enabled = True
    app.state.app_state.memory_store = env.memory
    app.dependency_overrides[main.get_settings] = lambda: settings
    app.add_api_route("/chat", main.chat, methods=["POST"])
    assert require_device_auth not in app.dependency_overrides
    real_service = compute_runtime.EntitlementService
    install_qualified_compute(monkeypatch)
    # Routes/providers are fictional; any account resolution remains real SQL.
    monkeypatch.setattr(compute_runtime, "EntitlementService", real_service)

    async def unthrottled(**_kwargs):
        pass

    async def bounded_observer(*_args, **_kwargs):
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(main, "_enforce_chat_rate_limit", unthrottled)
    monkeypatch.setattr(main, "observe_task", bounded_observer)
    monkeypatch.setattr(
        main, "stream_sse_chat", AsyncMock(side_effect=AssertionError("no request-bound fallback"))
    )
    headers = {
        "Authorization": "Bearer " + signed_in.json()["access_token"],
        "X-Daemon-Client-Features": "task-cancel, task-reset",
        "Idempotency-Key": "fictional-newborn",
    }
    return app, uid, headers


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
async def test_real_newborn_first_turn_and_restricted_replay_keep_original_origin(
    env, monkeypatch, provider
):
    app, uid, headers = await fresh_client(env, monkeypatch, provider)
    before = await account_row(env, uid)
    seen = []
    original_capture, original_accept = main.capture_generation, TaskStore.accept

    async def captured(conn, owner):
        value = await original_capture(conn, owner)
        seen.append(value)
        return value

    async def accept(store, **kwargs):
        assert kwargs["user_id"] == uid
        assert kwargs["captured_generation"] is seen[0]
        return await original_accept(store, **kwargs)

    monkeypatch.setattr(main, "capture_generation", captured)
    monkeypatch.setattr(TaskStore, "accept", accept)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        response = await client.post(
            "/chat", headers=headers, json={"message": "fictional newborn"}
        )
        assert response.status_code == 200 and "X-Daemon-Task-Id" in response.headers
        assert await counts(env) == (1, 2, 1, 1)
        row = await env.pool.fetchrow("SELECT * FROM tasks WHERE user_id=$1", uid)
        stored = env.tasks._open(row["input_ciphertext"])
        for prefix in ("origin", "summary_origin"):
            assert stored[prefix + "_account_id"] == str(uid)
            assert stored[prefix + "_account_generation"] == 0
        assert "origin_account" not in stored["_request"]
        assert row["content_generation"] == 0
        assert await account_row(env, uid) == before
        async with env.pool.acquire() as fence, fence.transaction():
            await restrict(fence, uid, "generation")
            await restrict(fence, uid, "suspended")
        replay = await client.post("/chat", headers=headers, json={"message": "fictional newborn"})
        assert replay.status_code == 200
        assert replay.headers["X-Daemon-Task-Id"] == response.headers["X-Daemon-Task-Id"]
        assert await counts(env) == (1, 2, 1, 1)
        assert (
            await env.pool.fetchval("SELECT input_ciphertext FROM tasks") == row["input_ciphertext"]
        )
        assert len(seen) == 1 and seen[0].account_id == uid and seen[0].generation == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
@pytest.mark.parametrize("change", ["generation", "suspended", "cause", "operation", "removed"])
async def test_newborn_lifecycle_change_after_original_http_capture_cannot_accept_or_repair(
    env, monkeypatch, provider, change
):
    app, uid, headers = await fresh_client(env, monkeypatch, provider)
    seen = []
    original = main.capture_generation

    async def captured(conn, owner):
        value = await original(conn, owner)
        seen.append(value)
        async with env.pool.acquire() as fence, fence.transaction():
            if change == "removed":
                await fence.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", owner)
            else:
                await restrict(fence, owner, change)
        return value

    capture_spy = AsyncMock(side_effect=captured)
    enqueue = AsyncMock(side_effect=AssertionError("refusal cannot enqueue task/title"))
    monkeypatch.setattr(main, "capture_generation", capture_spy)
    monkeypatch.setattr(main, "enqueue_account_job", enqueue)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        response = await client.post(
            "/chat", headers=headers, json={"message": "fictional refusal"}
        )
    assert response.status_code == 503 and response.json()["detail"]["code"] == "task_unavailable"
    capture_spy.assert_awaited_once()
    enqueue.assert_not_called()
    assert len(seen) == 1 and seen[0].account_id == uid and seen[0].generation == 0
    assert await counts(env) == (0, 0, 0, 0)
    if change == "removed":
        assert await account_row(env, uid) is None
    else:
        state = await account_row(env, uid)
        assert state is not None
        assert state["content_generation"] == (8 if change == "generation" else 0)
        assert state["status"] == ("suspended" if change == "suspended" else "active")
    # Identity/session state committed before chat remains; refusal cannot reset it.
    assert await env.pool.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid) == 1
