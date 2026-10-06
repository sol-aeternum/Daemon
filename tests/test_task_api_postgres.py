"""Durable chat API end to end against real PostgreSQL (no paid inference).

Covers the /chat compatibility adapter, the task endpoints, reconnect and
cross-device reads, idempotency, conversation busy, cancellation, tenant
isolation, and the observer's content-generation rules including a retry
while a client is attached (docs/DURABLE_REQUEST_DESIGN.md §6–§8, §11, §17).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from orchestrator.tasks import observe as observe_module
from orchestrator.tasks import runner
from orchestrator.tasks.observe import observe_task
from tests.durable_tasks_support import (
    LEASE_S,
    Env,
    FakeRedis,
    accept_task,
    durable_env_fixture,
    expire_lease,
)
from tests.qualified_compute import install_qualified_compute

env = durable_env_fixture()


CHUNKS = ["Durable ", "answers ", "survive ", "closed ", "apps."]
ANSWER = "".join(CHUNKS)
FIXTURE_MODELS = (
    "openrouter/openai/gpt-6-luna",
    "openrouter/z-ai/glm-5.3",
    "openrouter/moonshotai/kimi-k2.5",
)


class Api:
    def __init__(self, env: Env, client: AsyncClient, redis: FakeRedis, who: dict) -> None:
        self.env = env
        self.client = client
        self.redis = redis
        self._who = who

    def as_user(self, user_id: uuid.UUID) -> None:
        self._who["user"] = user_id

    def ctx(self) -> dict[str, Any]:
        return {
            "task_store": self.env.tasks,
            "store": self.env.memory,
            "db_pool": self.env.pool,
            "settings": get_settings(),
            "redis": self.redis,
        }

    async def run_worker_once_enqueued(self, timeout: float = 10) -> list[str]:
        """Wait for the API's wake-up, then run it (a worker running alongside)."""
        async with asyncio.timeout(timeout):
            while not self.redis.enqueued:
                await asyncio.sleep(0.01)
        return await self.run_worker()

    async def run_worker(self) -> list[str]:
        """Run every wake-up the API enqueued, like an arq worker would."""
        outcomes = []
        while self.redis.enqueued:
            name, args, _ = self.redis.enqueued.pop(0)
            if name == "run_chat_task":
                outcomes.append(await runner.run_chat_task(self.ctx(), *args))
        return outcomes


@pytest_asyncio.fixture
async def api(env: Env, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Api]:
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("REDIS_URL", "")
    # Hermetic: no ambient search credential (an earlier test module may load a
    # parent-directory .env into os.environ) may enable a live search tool.
    for key in ("BRAVE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    install_qualified_compute(monkeypatch, models=FIXTURE_MODELS)
    monkeypatch.setattr(observe_module, "POLL_S", 0.05)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.05)

    async def no_rate_limit(**_kwargs: Any) -> None:
        return None

    # Rate limiting is covered by its own tests; these exercise durability.
    monkeypatch.setattr("orchestrator.main._enforce_chat_rate_limit", no_rate_limit)
    monkeypatch.setattr("orchestrator.main.enforce_rate_limit", no_rate_limit)
    monkeypatch.setattr("orchestrator.main.get_rate_limiter", lambda _request: None)

    async def scripted_completion(**_kwargs: Any):
        for chunk in CHUNKS:
            await asyncio.sleep(0.05)
            yield {"type": "content_delta", "content": chunk}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", scripted_completion)

    redis = FakeRedis()
    state = AppState(settings=get_settings())
    state.db_pool = env.pool
    state.memory_store = env.memory
    state.redis = redis  # type: ignore[assignment]
    who = {"user": env.alice}

    async def override_auth(_request: Request) -> AuthenticatedDevice:
        return AuthenticatedDevice(
            user_id=who["user"], device_id=uuid.uuid4(), session_id=uuid.uuid4()
        )

    async def override_app_state() -> AppState:
        return state

    async def override_settings():
        return get_settings()

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[require_device_auth] = override_auth
    original = getattr(app.state, "app_state", None)
    app.state.app_state = state
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Api(env, client, redis, who)
    finally:
        app.dependency_overrides.clear()
        app.state.app_state = original


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.split("\n\n"):
        event = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), None)
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), None)
        if event and data:
            events.append((event, json.loads(data)))
    return events


def _displayed(events: list[tuple[str, dict[str, Any]]]) -> str:
    """What a client following the contract shows: tokens, replaced on reset."""
    text = ""
    for event, envelope in events:
        data = envelope.get("data", {})
        if event == "task" and data.get("reset"):
            text = data["content"]
        elif event == "token":
            text += data["text"]
    return text


async def _post(api: Api, message: str = "hello", key: str | None = None, **extra: Any):
    headers = {"Idempotency-Key": key} if key else {}
    return await api.client.post("/chat", json={"message": message, **extra}, headers=headers)


async def _submit(api: Api, message: str = "hello", key: str | None = None, **extra: Any) -> dict:
    """Submit a turn, wait until it is durably accepted, then drop the client.

    Durable /chat streams until the task ends and ASGITransport buffers whole
    bodies, so tests that only need acceptance disconnect like a closed app.
    """
    before = await api.env.pool.fetchval("SELECT count(*) FROM tasks")
    client = asyncio.create_task(_post(api, message, key=key, **extra))
    async with asyncio.timeout(10):
        while await api.env.pool.fetchval("SELECT count(*) FROM tasks") == before:
            await asyncio.sleep(0.01)
    client.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await client
    task_id = await api.env.pool.fetchval("SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")
    return (await api.client.get(f"/tasks/{task_id}")).json()


async def _stream_with_worker(api: Api, **post: Any) -> tuple[Any, list[tuple[str, dict]]]:
    """POST /chat while a worker runs alongside (ASGITransport buffers bodies)."""
    worker = asyncio.create_task(api.run_worker_once_enqueued())
    client = asyncio.create_task(
        _post(api, post.get("message", "hello"), key=post.get("key", uuid.uuid4().hex))
    )
    outcomes = await worker
    if outcomes != ["completed"]:
        # Fail with the evidence instead of waiting on a re-queued task.
        client.cancel()
        rows = await api.env.pool.fetch("SELECT status, terminal_code FROM tasks")
        attempts = await api.env.pool.fetch("SELECT outcome, terminal_code FROM task_attempts")
        pytest.fail(f"worker outcomes {outcomes}; tasks {rows}; attempts {attempts}")
    response = await asyncio.wait_for(client, timeout=10)
    return response, _events(response.text)


# --------------------------------------------------------------------------- #
# /chat adapter
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_is_accepted_durably_and_streams_the_committed_answer(api: Api):
    response, events = await _stream_with_worker(api)
    assert response.status_code == 200
    task_id = uuid.UUID(response.headers["X-Daemon-Task-Id"])
    kinds = [event for event, _ in events]
    assert kinds[0] == "task" and "conversation" in kinds
    assert kinds[-2:] == ["final", "done"]
    assert events[-1][1]["data"]["status"] == "completed"
    assert _displayed(events) == ANSWER
    snapshot = await api.env.tasks.snapshot(api.env.alice, task_id)
    assert snapshot is not None and snapshot.content == ANSWER


@pytest.mark.asyncio
async def test_closing_the_client_does_not_cancel_and_another_device_sees_the_result(api: Api):
    # The "phone" submits, then disappears while the answer is still pending.
    phone = asyncio.create_task(_post(api, key="phone-1"))
    async with asyncio.timeout(10):
        while not await api.env.pool.fetchval("SELECT count(*) FROM tasks"):
            await asyncio.sleep(0.01)
    phone.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await phone
    task_id = await api.env.pool.fetchval("SELECT id FROM tasks")
    # The disconnect may land before the post-commit wake-up was enqueued; the
    # dispatch sweep recovers that window. Either way the task runs once.
    await runner.sweep_tasks({"task_store": api.env.tasks, "redis": api.redis})
    assert await api.run_worker() == ["completed"]

    # The "laptop" opens the conversation later.
    task = (await api.client.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "completed" and task["content"] == ANSWER
    conversation = (await api.client.get(f"/conversations/{task['conversation_id']}")).json()
    assert conversation["active_task"] is None
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [
        ("user", "hello"),
        ("assistant", ANSWER),
    ]


@pytest.mark.asyncio
async def test_reattaching_mid_run_reconstructs_the_answer(api: Api):
    release = asyncio.Event()

    async def gated_worker() -> list[str]:
        await release.wait()
        return await api.run_worker_once_enqueued()

    worker = asyncio.create_task(gated_worker())
    first_device = asyncio.create_task(_post(api, key="attach"))
    async with asyncio.timeout(10):
        while not await api.env.pool.fetchval("SELECT count(*) FROM tasks"):
            await asyncio.sleep(0.01)
    task_id = str(await api.env.pool.fetchval("SELECT id FROM tasks"))
    # Accepted but not yet run: the conversation advertises its active task.
    task = (await api.client.get(f"/tasks/{task_id}")).json()
    conversation = (await api.client.get(f"/conversations/{task['conversation_id']}")).json()
    assert conversation["active_task"]["id"] == task_id
    assert conversation["active_task"]["status"] == "queued"

    release.set()
    async with asyncio.timeout(10):
        while not (await api.client.get(f"/tasks/{task_id}")).json()["content"]:
            await asyncio.sleep(0.02)
    # A second device reattaches mid-run (the transport returns once the task ends).
    reattached = await api.client.get(f"/tasks/{task_id}/events")
    assert await worker == ["completed"]
    for response in (reattached, await first_device):
        events = _events(response.text)
        assert _displayed(events) == ANSWER
        assert events[-1][1]["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_idempotent_replay_and_conflict(api: Api):
    first, _ = await _stream_with_worker(api, key="same-key")
    replay = await _post(api, key="same-key")
    assert replay.status_code == 200
    assert replay.headers["X-Daemon-Task-Id"] == first.headers["X-Daemon-Task-Id"]
    assert _displayed(_events(replay.text)) == ANSWER
    conflict = await _post(api, message="something else", key="same-key")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert await api.env.pool.fetchval("SELECT count(*) FROM tasks") == 1
    assert await api.env.pool.fetchval("SELECT count(*) FROM messages") == 2


@pytest.mark.asyncio
async def test_second_turn_while_active_is_busy(api: Api):
    task = await _submit(api, key="k1")
    busy = await _post(api, message="again", key="k2", conversation_id=task["conversation_id"])
    assert busy.status_code == 409
    assert busy.json()["detail"] == {
        "code": "conversation_busy",
        "message": "This conversation is still working on an earlier request",
        "task_id": task["id"],
    }


@pytest.mark.asyncio
async def test_cancel_endpoint_queued_then_finished(api: Api):
    task = await _submit(api, key="cancel-me")
    task_id = task["id"]
    cancelled = await api.client.post(f"/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert await api.run_worker() == ["skipped"]
    again = await api.client.post(f"/tasks/{task_id}/cancel")
    assert again.status_code == 409
    assert again.json()["detail"] == {"code": "task_finished", "status": "cancelled"}
    # Another device still finds the finished task and its truthful state.
    conversation = (await api.client.get(f"/conversations/{task['conversation_id']}")).json()
    assert conversation["active_task"] is None
    assert conversation["latest_task"]["id"] == task_id
    assert conversation["latest_task"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_other_account_gets_not_found_everywhere(api: Api):
    task = await _submit(api, key="alice")
    task_id = task["id"]
    api.as_user(api.env.bob)
    assert (await api.client.get(f"/tasks/{task_id}")).status_code == 404
    assert (await api.client.get(f"/tasks/{task_id}/events")).status_code == 404
    assert (await api.client.post(f"/tasks/{task_id}/cancel")).status_code == 404
    intrusion = await _post(api, message="mine now", conversation_id=task["conversation_id"])
    assert intrusion.status_code == 404
    api.as_user(api.env.alice)
    assert (await api.client.get(f"/tasks/{task_id}")).json()["status"] == "queued"


@pytest.mark.asyncio
async def test_invalid_idempotency_key_is_rejected(api: Api):
    response = await _post(api, key="has spaces in it")
    assert response.status_code == 422
    assert await api.env.pool.fetchval("SELECT count(*) FROM tasks") == 0


@pytest.mark.asyncio
async def test_flag_off_keeps_the_request_bound_path(api: Api, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "false")
    get_settings.cache_clear()
    response = await _post(api, key="legacy")
    assert response.status_code == 200
    assert "X-Daemon-Task-Id" not in response.headers
    assert await api.env.pool.fetchval("SELECT count(*) FROM tasks") == 0


# --------------------------------------------------------------------------- #
# Observer content-generation rules (review of #461)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_retry_while_attached_replaces_text_and_drops_stale_frames(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(observe_module, "POLL_S", 0.05)
    redis = FakeRedis()
    accepted = await accept_task(env)
    channel = runner.live_channel(accepted.task_id)

    async def publish(message: dict[str, Any]) -> None:
        await redis.publish(channel, json.dumps(message))

    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    frames: list[str] = []

    async def consume() -> None:
        async for frame in observe_task(
            env.tasks, redis, env.alice, accepted.task_id, request_id="req_test"
        ):
            frames.append(frame)

    observer = asyncio.create_task(consume())
    while not redis.subscribers:
        await asyncio.sleep(0.01)
    # Attempt 1 streams a long answer, then is lost.
    for seq, text in enumerate(["The first ", "attempt was ", "rather long."], start=1):
        await publish({"t": "delta", "gen": first.epoch, "seq": seq, "text": text})
    await env.tasks.write_partial(
        accepted.task_id, first.epoch, content="The first attempt was rather long.", delta_seq=3
    )
    await asyncio.sleep(0.2)
    await expire_lease(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None
    # Attempt 2 regenerates a shorter, different answer; attempt-1 frames arrive late.
    await env.tasks.write_partial(accepted.task_id, second.epoch, content="Short.", delta_seq=1)
    await publish({"t": "delta", "gen": second.epoch, "seq": 1, "text": "Short."})
    await publish({"t": "delta", "gen": first.epoch, "seq": 4, "text": " STALE"})
    await publish({"t": "frame", "gen": first.epoch, "frame": "event: tool_call\ndata: {}\n\n"})
    await env.tasks.complete(accepted.task_id, second.epoch, content="Short.")
    await publish({"t": "terminal", "gen": second.epoch})
    await asyncio.wait_for(observer, timeout=10)

    events = _events("".join(frames))
    assert _displayed(events) == "Short."
    assert "STALE" not in "".join(frames)
    assert "tool_call" not in [event for event, _ in events]
    assert any(e == "task" and d["data"].get("reset") for e, d in events)
    assert events[-1][1]["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_observer_without_live_updates_still_reaches_the_result(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(observe_module, "POLL_S", 0.05)
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    frames: list[str] = []

    async def consume() -> None:
        async for frame in observe_task(
            env.tasks, None, env.alice, accepted.task_id, request_id="req_test"
        ):
            frames.append(frame)

    observer = asyncio.create_task(consume())
    await env.tasks.write_partial(accepted.task_id, claim.epoch, content="Partial", delta_seq=1)
    await asyncio.sleep(0.2)
    await env.tasks.complete(accepted.task_id, claim.epoch, content="Partial and complete.")
    await asyncio.wait_for(observer, timeout=10)
    events = _events("".join(frames))
    assert _displayed(events) == "Partial and complete."
    assert events[-1][1]["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_needs_attention_is_reported_honestly(env: Env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(observe_module, "POLL_S", 0.05)
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.begin_operation(
        accepted.task_id,
        claim.epoch,
        tool_name="notification_send",
        target=None,
        min_lease_margin_s=1,
    )
    await expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    frames = [
        frame
        async for frame in observe_task(
            env.tasks, None, env.alice, accepted.task_id, request_id="req_test"
        )
    ]
    events = dict(_events("".join(frames)))
    assert events["error"]["data"]["code"] == "uncertain_effect"
    assert events["error"]["data"]["retryable"] is False
    assert "may already have happened" in events["error"]["data"]["message"]
    assert events["done"]["data"] == {"status": "error", "reason": "uncertain_effect"}
