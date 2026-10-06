"""Durable chat task runner against real PostgreSQL with the mock LLM.

Exercises worker execution end to end: completion, live delta generations,
cancellation, fencing of a stale attempt, refusal classification, the effect
fence around material tools, duplicate delivery and the dispatch sweep
(docs/DURABLE_REQUEST_DESIGN.md §4–§9, §17). No paid inference: the chat
engine runs its deterministic ``mock_llm`` stream.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet

from orchestrator.compute_runtime import ComputeUnavailable
from orchestrator.config import get_settings
from orchestrator.tasks import runner
from orchestrator.tasks.fence import REPEATABLE_TOOLS, FencedTool, guard_registry, is_material
from orchestrator.tasks.states import TaskStatus
from orchestrator.tools.registry import Tool, ToolRegistry
from tests.durable_tasks_support import (
    LEASE_S,
    Env,
    FakeRedis,
    accept_task,
    durable_env_fixture,
    expire_lease,
)

env = durable_env_fixture()


MOCK_TEXT = "Scripted answer from a fake provider."
#: Chunks the fake provider streams, joined they make MOCK_TEXT.
CHUNKS = ["Scripted ", "answer ", "from ", "a ", "fake ", "provider."]


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the provider loop with a scripted, paced token stream.

    The real (non-mock) chat path runs, including incremental persistence,
    so only inference itself is faked; no paid call is made.
    """
    monkeypatch.setenv("MOCK_LLM", "false")
    # Hermetic: no ambient search credential (an earlier test module may load a
    # parent-directory .env into os.environ) may enable a live search tool.
    for key in ("BRAVE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    async def scripted_completion(**_kwargs: Any):
        for chunk in CHUNKS:
            await asyncio.sleep(0.05)
            yield {"type": "content_delta", "content": chunk}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", scripted_completion)


def _ctx(env: Env, redis: FakeRedis) -> dict[str, Any]:
    return {
        "task_store": env.tasks,
        "store": env.memory,
        "db_pool": env.pool,
        "settings": get_settings(),
        "redis": redis,
    }


def _deltas(redis: FakeRedis, task_id: uuid.UUID) -> list[dict[str, Any]]:
    channel = runner.live_channel(task_id)
    return [m for c, m in redis.published if c == channel and m["t"] == "delta"]


async def _status(env: Env, task_id: uuid.UUID) -> str:
    return await env.pool.fetchval("SELECT status FROM tasks WHERE id = $1", task_id)


@pytest.mark.asyncio
async def test_task_completes_and_live_deltas_rebuild_the_result(env: Env, mock_llm: None):
    accepted = await accept_task(env)
    redis = FakeRedis()
    assert await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)) == "completed"

    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None and snapshot.status is TaskStatus.COMPLETED
    assert snapshot.content == MOCK_TEXT
    deltas = _deltas(redis, accepted.task_id)
    assert [d["seq"] for d in deltas] == list(range(1, len(deltas) + 1))
    assert {d["gen"] for d in deltas} == {1}
    assert "".join(d["text"] for d in deltas) == MOCK_TEXT
    assert redis.published[-1][1] == {"t": "terminal", "gen": 1}
    history = await env.memory.get_recent_messages(
        accepted.conversation_id, exclude_status=["streaming", "error", "cancelled"]
    )
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "hello"),
        ("assistant", MOCK_TEXT),
    ]


@pytest.mark.asyncio
async def test_duplicate_delivery_runs_once(env: Env, mock_llm: None):
    accepted = await accept_task(env)
    redis = FakeRedis()
    outcomes = await asyncio.gather(
        runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)),
        runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)),
    )
    assert sorted(outcomes) == ["completed", "skipped"]
    assert (
        await env.pool.fetchval("SELECT attempt_count FROM tasks WHERE id = $1", accepted.task_id)
        == 1
    )


@pytest.mark.asyncio
async def test_cancel_during_stream_keeps_partial_and_ends_cancelled(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.05)
    accepted = await accept_task(env)
    redis = FakeRedis()
    job = asyncio.create_task(runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)))
    while len(_deltas(redis, accepted.task_id)) < 3:
        await asyncio.sleep(0.01)
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert await asyncio.wait_for(job, timeout=10) == "cancelled"
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None and snapshot.status is TaskStatus.CANCELLED
    assert snapshot.content and MOCK_TEXT.startswith(snapshot.content)
    assert snapshot.content != MOCK_TEXT


@pytest.mark.asyncio
async def test_stale_attempt_is_fenced_and_never_publishes(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.05)
    accepted = await accept_task(env)
    redis = FakeRedis()
    stale = asyncio.create_task(runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)))
    while len(_deltas(redis, accepted.task_id)) < 3:
        await asyncio.sleep(0.01)
    # The stale worker stalls past its lease; another worker takes over.
    await expire_lease(env, accepted.task_id)
    takeover = await env.tasks.claim(accepted.task_id, worker_id="other", lease_s=LEASE_S)
    assert takeover is not None and takeover.epoch == 2
    assert await asyncio.wait_for(stale, timeout=10) == "fenced"
    await env.tasks.complete(accepted.task_id, takeover.epoch, content="from attempt 2")
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None and snapshot.content == "from attempt 2"
    assert snapshot.content_generation == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_status", "expected_code"),
    [
        ("budget_exceeded", "failed", "budget_exceeded"),
        ("route_unavailable", "failed", "route_unavailable"),
        ("rate_limited", "queued", None),
    ],
)
async def test_compute_refusals_are_classified(
    env: Env,
    mock_llm: None,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected_status: str,
    expected_code: str | None,
):
    class _Refusing:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> None:
            raise ComputeUnavailable(code, "refused")

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(runner, "account_compute", _Refusing)
    accepted = await accept_task(env)
    outcome = await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id))
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", accepted.task_id)
    assert outcome == expected_status == row["status"]
    assert row["terminal_code"] == expected_code


@pytest.mark.asyncio
async def test_sweep_wakes_lost_work_with_unique_job_ids(env: Env):
    first = await accept_task(env)
    second = await accept_task(env)
    redis = FakeRedis()
    assert await runner.sweep_tasks({"task_store": env.tasks, "redis": redis}) == 2
    names = {name for name, _, _ in redis.enqueued}
    job_ids = {kwargs["_job_id"] for _, _, kwargs in redis.enqueued}
    task_ids = {args[0] for _, args, _ in redis.enqueued}
    assert names == {"run_chat_task"}
    assert task_ids == {str(first.task_id), str(second.task_id)}
    assert all(job_id.startswith("task:") for job_id in job_ids) and len(job_ids) == 2


# --------------------------------------------------------------------------- #
# Effect fence
# --------------------------------------------------------------------------- #


class _CountingTool(Tool):
    name = "notification_send"
    description = "test"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, **_kwargs: Any) -> str:
        self.calls += 1
        return json.dumps({"success": True})


def test_unknown_tools_are_material_by_default():
    assert not is_material("web_search")
    for name in ("notification_send", "http_request", "reminder_set", "spawn_agent", "new_tool"):
        assert is_material(name)
    assert "notification_send" not in REPEATABLE_TOOLS


def test_guard_wraps_only_material_tools():
    registry = ToolRegistry()
    material = _CountingTool()
    registry.register(material)

    class _Read(_CountingTool):
        name = "web_search"

    registry.register(_Read())
    guard_registry(registry, store=None, task_id=uuid.uuid4(), epoch=1)  # type: ignore[arg-type]
    assert isinstance(registry.get("notification_send"), FencedTool)
    assert not isinstance(registry.get("web_search"), FencedTool)


@pytest.mark.asyncio
async def test_material_tool_is_recorded_then_retry_stops_at_needs_attention(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    inner = _CountingTool()
    fenced = FencedTool(inner, env.tasks, accepted.task_id, claim.epoch)
    await fenced.execute(topic="alerts", message="secret body")
    assert inner.calls == 1
    op = await env.pool.fetchrow("SELECT * FROM task_operations WHERE task_id = $1", claim.task_id)
    assert op["outcome"] == "succeeded" and op["tool_name"] == "notification_send"
    target = env.tasks._open(op["target_ciphertext"])
    assert target == {"tool": "notification_send", "topic": "alerts"}

    await expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is None
    assert await _status(env, accepted.task_id) == "needs_attention"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_superseded_attempt_never_runs_material_tool(env: Env):
    accepted = await accept_task(env)
    stale = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert stale is not None
    await expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S) is not None
    inner = _CountingTool()
    result = await FencedTool(inner, env.tasks, accepted.task_id, stale.epoch).execute()
    assert inner.calls == 0 and json.loads(result)["success"] is False
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0


@pytest.mark.asyncio
async def test_post_completion_work_is_not_cancelled_by_the_heartbeat(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """After the result commits, a heartbeat must not treat the ended lease as fencing."""
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.01)
    accepted = await accept_task(env)
    redis = FakeRedis()
    slow_enqueues: list[str] = []

    async def slow_enqueue(name: str, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.2)  # several heartbeat intervals after completion
        slow_enqueues.append(name)
        return object()

    redis.enqueue_job = slow_enqueue  # type: ignore[method-assign]
    assert await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)) == "completed"
    assert "extract_memories" in slow_enqueues


@pytest.mark.asyncio
async def test_suspension_during_run_stops_without_publishing(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.02)
    accepted = await accept_task(env)
    redis = FakeRedis()
    job = asyncio.create_task(runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)))
    while len(_deltas(redis, accepted.task_id)) < 2:
        await asyncio.sleep(0.01)
    await env.pool.execute(
        "INSERT INTO entitlement_accounts (user_id, status) VALUES ($1, 'suspended') "
        "ON CONFLICT (user_id) DO UPDATE SET status = 'suspended'",
        env.alice,
    )
    assert await asyncio.wait_for(job, timeout=10) == "failed"
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", accepted.task_id)
    assert row["terminal_code"] == "account_suspended"


@pytest.mark.asyncio
async def test_lost_attempts_hold_is_settled_before_a_one_slot_account_recovers(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #461: a dead attempt's open hold must not starve the recovery attempt."""
    import contextlib as _contextlib

    from orchestrator.entitlements.errors import LimitExceeded
    from orchestrator.entitlements.service import EntitlementService

    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    service = EntitlementService(env.pool)
    lost_scope = uuid.uuid4()
    hold = await service.reserve(env.alice, 1000, operation="chat", scope_id=lost_scope)
    await env.tasks.record_compute_scope(accepted.task_id, first.epoch, lost_scope)
    # The free plan allows one concurrent operation: the dead hold blocks the next.
    with pytest.raises(LimitExceeded) as refused:
        await service.reserve(env.alice, 1000, operation="chat", scope_id=uuid.uuid4())
    assert refused.value.code == "concurrency_exceeded"

    await expire_lease(env, accepted.task_id)  # the worker died holding it
    status_at_admission: list[str] = []
    real_account_compute = runner.account_compute

    @_contextlib.asynccontextmanager
    async def observed_account_compute(*args: Any, **kwargs: Any):
        status_at_admission.append(
            await env.pool.fetchval(
                "SELECT status FROM entitlement_reservations WHERE id = $1", hold.id
            )
        )
        async with real_account_compute(*args, **kwargs) as scope:
            yield scope

    monkeypatch.setattr(runner, "account_compute", observed_account_compute)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    assert status_at_admission == ["settled"]
    settled = await env.pool.fetchrow(
        "SELECT actual_microusd, reserved_microusd FROM entitlement_reservations WHERE id = $1",
        hold.id,
    )
    # Unknown provider work is charged conservatively at the full hold.
    assert settled["actual_microusd"] == settled["reserved_microusd"]
    # The slot is free again for the account's next operation.
    assert await service.reserve(env.alice, 1000, operation="chat", scope_id=uuid.uuid4())
