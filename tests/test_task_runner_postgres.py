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
    # The attempt records the system prompt version it ran with (§10).
    from orchestrator.prompts import DAEMON_PROMPT_VERSION

    assert await env.pool.fetchval(
        "SELECT prompt_version FROM task_attempts WHERE task_id = $1", accepted.task_id
    ) == str(DAEMON_PROMPT_VERSION)
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
    # Long enough for a beat to open a pool connection, so the cancel is seen.
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.25)

    async def stalls_after_three_chunks(**_kwargs: Any):
        # Deterministic: the stream cannot finish before the cancel is seen.
        for chunk in CHUNKS[:3]:
            yield {"type": "content_delta", "content": chunk}
        await asyncio.Event().wait()
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", stalls_after_three_chunks)
    accepted = await accept_task(env)
    redis = FakeRedis()
    job = asyncio.create_task(runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)))
    while len(_deltas(redis, accepted.task_id)) < 3:
        await asyncio.sleep(0.01)
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    assert await asyncio.wait_for(job, timeout=10) == "cancelled"
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None and snapshot.status is TaskStatus.CANCELLED
    assert snapshot.content == "".join(CHUNKS[:3])  # the partial answer is kept


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
    lost: list[bool] = []
    result = await FencedTool(
        inner, env.tasks, accepted.task_id, stale.epoch, lambda: lost.append(True)
    ).execute()
    assert inner.calls == 0 and json.loads(result)["success"] is False
    assert await env.pool.fetchval("SELECT count(*) FROM task_operations") == 0
    # Review of #466: the attempt is told to stop, not just handed a tool error.
    assert lost == [True]


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
    await env.tasks.begin_execution(accepted.task_id, first.epoch, lost_scope)
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


@pytest.mark.asyncio
async def test_failed_hold_settlement_defers_without_consuming_an_attempt(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: recovery must not be admitted, or burn the attempt, unsettled."""
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    await expire_lease(env, accepted.task_id)

    real_settle = runner.settle_lost_attempt_holds
    failures = iter([True])

    async def settle_fails_once(*args: Any, **kwargs: Any) -> int:
        if next(failures, False):
            raise ConnectionError("database briefly unavailable")
        return await real_settle(*args, **kwargs)

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", settle_fails_once)
    ctx = _ctx(env, FakeRedis())
    assert await runner.run_chat_task(ctx, str(accepted.task_id)) == "deferred"
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", accepted.task_id)
    assert row["status"] == "queued" and row["attempt_count"] == 1
    await env.pool.execute(
        "UPDATE tasks SET next_wakeup_at = now() - interval '1 second' WHERE id = $1",
        accepted.task_id,
    )
    assert await runner.run_chat_task(ctx, str(accepted.task_id)) == "completed"
    outcomes = await env.pool.fetch(
        "SELECT outcome FROM task_attempts WHERE task_id = $1 ORDER BY epoch", accepted.task_id
    )
    assert [r["outcome"] for r in outcomes] == ["lost", "deferred", "completed"]


@pytest.mark.asyncio
async def test_unrenewable_lease_stops_execution(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: a database outage must not let work run past the lease."""
    monkeypatch.setattr(runner, "LEASE_S", 0.5)
    monkeypatch.setattr(runner, "LEASE_SAFETY_S", 0.1)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.05)

    async def long_completion(**_kwargs: Any):
        for _ in range(60):
            await asyncio.sleep(0.05)
            yield {"type": "content_delta", "content": "x"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", long_completion)

    async def heartbeat_unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(env.tasks, "heartbeat", heartbeat_unavailable)
    accepted = await accept_task(env)
    redis = FakeRedis()
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await runner.run_chat_task(_ctx(env, redis), str(accepted.task_id)) == "fenced"
    assert loop.time() - started < 1.5  # stopped near the lease, not after 3s of output
    assert len(_deltas(redis, accepted.task_id)) < 30


@pytest.mark.asyncio
async def test_history_is_cut_at_the_accepted_turn(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: a request-bound turn written after acceptance is not this task's context."""
    seen: list[list[dict[str, Any]]] = []

    async def capturing_completion(**kwargs: Any):
        seen.append(kwargs["messages"])
        yield {"type": "content_delta", "content": "ok"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", capturing_completion)
    accepted = await accept_task(env, message="the accepted question")
    for role, content in (("user", "/council a later turn"), ("assistant", "council reply")):
        await env.memory.insert_message(
            conversation_id=accepted.conversation_id,
            user_id=env.alice,
            role=role,
            content=content,
            status="complete",
        )
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    contents = [m.get("content") for m in seen[0] if m.get("role") != "system"]
    assert contents[-1] == "the accepted question"
    assert "/council a later turn" not in contents


@pytest.mark.asyncio
@pytest.mark.parametrize("renewal", ["fails_late", "hangs"])
async def test_execution_stops_at_the_lease_deadline_whatever_renewal_does(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch, renewal: str
):
    """Agent review of #466: late or hanging renewals must not extend execution."""
    monkeypatch.setattr(runner, "LEASE_S", 0.6)
    monkeypatch.setattr(runner, "LEASE_SAFETY_S", 0.1)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.2)
    output_times: list[float] = []

    async def long_completion(**_kwargs: Any):
        for _ in range(200):
            await asyncio.sleep(0.01)
            output_times.append(asyncio.get_running_loop().time())
            yield {"type": "content_delta", "content": "x"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", long_completion)

    async def bad_renewal(*_args: Any, **_kwargs: Any) -> Any:
        if renewal == "hangs":
            await asyncio.Event().wait()
        await asyncio.sleep(0.18)  # nearly a whole heartbeat, then fail
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(env.tasks, "heartbeat", bad_renewal)
    accepted = await accept_task(env)
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == "fenced"
    deadline = started + 0.6 - 0.1
    assert output_times and max(output_times) <= deadline + 0.15


@pytest.mark.asyncio
async def test_reconciliation_outlasting_the_lease_never_executes(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "LEASE_S", 0.3)
    monkeypatch.setattr(runner, "LEASE_SAFETY_S", 0.05)
    calls: list[str] = []

    async def slow_settle(*_args: Any, **_kwargs: Any) -> int:
        await asyncio.sleep(0.4)
        return 0

    async def never(**_kwargs: Any):
        calls.append("provider")
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", slow_settle)
    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", never)
    accepted = await accept_task(env)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == "fenced"
    assert calls == []
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", accepted.task_id)
    assert row["attempt_count"] == 0


@pytest.mark.asyncio
async def test_outage_during_recovery_does_not_consume_the_retry(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Agent review of #466: settlement and its deferral both fail, then recovery."""
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    await expire_lease(env, accepted.task_id)  # attempt 1 (of 2) executed and was lost

    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("database unavailable")

    with monkeypatch.context() as outage:
        outage.setattr(runner, "settle_lost_attempt_holds", unavailable)
        outage.setattr(env.tasks, "defer_claim", unavailable)
        assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
            "deferred"
        )
    # The database is back; the second claim's lease lapses.
    await expire_lease(env, accepted.task_id)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    row = await env.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", accepted.task_id)
    assert row["attempt_count"] == 2


@pytest.mark.asyncio
async def test_many_later_turns_do_not_evict_the_accepted_context(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Agent review of #466: the window ends at the accepted turn before the limit."""
    monkeypatch.setenv("CHAT_HISTORY_LIMIT", "3")
    get_settings.cache_clear()
    seen: list[list[dict[str, Any]]] = []

    async def capturing_completion(**kwargs: Any):
        seen.append(kwargs["messages"])
        yield {"type": "content_delta", "content": "ok"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", capturing_completion)
    accepted = await accept_task(env, message="the accepted question")
    for index in range(6):  # more later rows than the history limit
        await env.memory.insert_message(
            conversation_id=accepted.conversation_id,
            user_id=env.alice,
            role="user" if index % 2 == 0 else "assistant",
            content=f"later turn {index}",
            status="complete",
        )
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    contents = [m.get("content") for m in seen[0] if m.get("role") != "system"]
    assert contents[-1] == "the accepted question"
    assert not any("later turn" in str(content) for content in contents)


def _refuse_admission(
    monkeypatch: pytest.MonkeyPatch, env: Env, *, refusals: int, reserve_first: bool = False
) -> None:
    """The account refuses the first ``refusals`` provider calls with
    ``concurrency_exceeded``, as a free plan's single slot does while another
    chat runs. ``reserve_first`` grants a hold in the attempt's scope first."""
    from orchestrator.compute_runtime import current_scope
    from orchestrator.entitlements.service import EntitlementService

    calls = 0

    async def completion(**_kwargs: Any):
        nonlocal calls
        calls += 1
        if calls <= refusals:
            if reserve_first:
                await EntitlementService(env.pool).reserve(
                    env.alice, 1000, operation="chat", scope_id=current_scope().scope_id
                )
            raise ComputeUnavailable("concurrency_exceeded", "refused")
        for chunk in CHUNKS:
            yield {"type": "content_delta", "content": chunk}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", completion)


async def _attempts(env: Env, task_id: uuid.UUID) -> tuple[int, list[str]]:
    count = await env.pool.fetchval("SELECT attempt_count FROM tasks WHERE id = $1", task_id)
    rows = await env.pool.fetch(
        "SELECT outcome FROM task_attempts WHERE task_id = $1 ORDER BY epoch", task_id
    )
    return count, [row["outcome"] for row in rows]


@pytest.mark.asyncio
async def test_waiting_for_an_account_slot_consumes_no_attempts(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: a one-slot account's second chat waits for the slot
    instead of exhausting its attempts while the first chat runs."""
    _refuse_admission(monkeypatch, env, refusals=3)
    accepted = await accept_task(env)
    for _ in range(3):  # more refusals than max_attempts allows
        assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
            "queued"
        )
        row = await env.pool.fetchrow(
            "SELECT next_wakeup_at > now() AS later, terminal_code FROM tasks WHERE id = $1",
            accepted.task_id,
        )
        assert row["later"] and row["terminal_code"] is None
        await env.pool.execute(
            "UPDATE tasks SET next_wakeup_at = now() WHERE id = $1", accepted.task_id
        )
    assert await _attempts(env, accepted.task_id) == (0, ["deferred"] * 3)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    assert await _attempts(env, accepted.task_id) == (1, ["deferred"] * 3 + ["completed"])


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["reservation_granted", "waited_too_long"])
async def test_admission_refusal_counts_once_work_began_or_the_wait_is_over(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch, why: str
):
    _refuse_admission(monkeypatch, env, refusals=1, reserve_first=why == "reservation_granted")
    accepted = await accept_task(env)
    if why == "waited_too_long":
        await env.pool.execute(
            "UPDATE tasks SET created_at = now() - make_interval(secs => $2) WHERE id = $1",
            accepted.task_id,
            runner.ADMISSION_WAIT_S + 1,
        )
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == "queued"
    assert await _attempts(env, accepted.task_id) == (1, ["failed_retryable"])


@pytest.mark.asyncio
async def test_cancel_between_claim_and_execution_makes_no_provider_call(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: a Stop that lands after the claim stops before inference."""
    calls = 0

    async def counting_completion(**_kwargs: Any):
        nonlocal calls
        calls += 1
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", counting_completion)
    accepted = await accept_task(env)
    real_settle = runner.settle_lost_attempt_holds

    async def cancel_then_settle(*args: Any, **kwargs: Any) -> int:
        await env.tasks.request_cancel(env.alice, accepted.task_id)
        return await real_settle(*args, **kwargs)

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", cancel_then_settle)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "cancelled"
    )
    assert calls == 0
    assert (
        await env.pool.fetchval("SELECT attempt_count FROM tasks WHERE id = $1", accepted.task_id)
        == 0
    )


@pytest.mark.asyncio
async def test_completion_reports_the_committed_status_to_the_engine(env: Env):
    """Review of #466: when a cancel wins the commit, success-only follow-ups
    (trust signal, extraction, skill evaluation) must not run."""
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    sink = runner.AttemptSink(env.tasks, runner.AttemptState(claim=claim))
    row = await sink.update_message(content="answer", status="complete")
    assert row is not None and row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_holds_are_settled_when_the_claim_ends_the_task(env: Env, mock_llm: None):
    """Review of #466: a lost attempt that recovery terminalizes (here: it had
    started a material operation) must not keep its hold until generic recovery."""
    from orchestrator.entitlements.service import EntitlementService

    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    scope = uuid.uuid4()
    hold = await EntitlementService(env.pool).reserve(
        env.alice, 1000, operation="chat", scope_id=scope
    )
    await env.tasks.begin_execution(accepted.task_id, first.epoch, scope)
    await env.tasks.begin_operation(
        accepted.task_id, first.epoch, tool_name="notify", target=None, min_lease_margin_s=1
    )
    await expire_lease(env, accepted.task_id)  # the worker died mid-operation
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == "skipped"
    assert await _status(env, accepted.task_id) == "needs_attention"
    assert (
        await env.pool.fetchval(
            "SELECT status FROM entitlement_reservations WHERE id = $1", hold.id
        )
        == "settled"
    )


@pytest.mark.asyncio
async def test_a_fenced_write_stops_the_attempt_at_once(env: Env):
    """Review of #466: a partial write that finds the lease gone cancels execution."""
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    state = runner.AttemptState(claim=claim)
    state.execution = asyncio.create_task(asyncio.Event().wait())  # a stalled provider call
    await expire_lease(env, accepted.task_id)
    assert await runner.AttemptSink(env.tasks, state).update_message(content="late") is None
    assert state.fenced
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(2):  # never cancelled would time out instead
            await state.execution


@pytest.mark.asyncio
async def test_progress_persistence_that_finds_the_lease_gone_stops_the_attempt(env: Env):
    """Review of #466: a fenced tool-event write cancels execution too."""
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    state = runner.AttemptState(claim=claim)
    state.execution = asyncio.create_task(asyncio.Event().wait())
    await expire_lease(env, accepted.task_id)
    await runner._record_progress(env.tasks, state, "tool_call", "web_search")
    assert state.fenced
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(2):
            await state.execution


@pytest.mark.asyncio
async def test_a_preparation_failure_consumes_no_attempt(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """Review of #466: transient failures while preparing the prompt or history
    are deferred, not counted (§5), and still end once the wait is over."""
    real_prompt = runner._system_prompt
    failures = 3

    async def flaky_prompt(*args: Any, **kwargs: Any):
        nonlocal failures
        if failures:
            failures -= 1
            raise ConnectionError("message read failed")
        return await real_prompt(*args, **kwargs)

    monkeypatch.setattr(runner, "_system_prompt", flaky_prompt)
    accepted = await accept_task(env)
    for _ in range(3):  # more failures than max_attempts allows
        assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
            "queued"
        )
        await env.pool.execute(
            "UPDATE tasks SET next_wakeup_at = now() WHERE id = $1", accepted.task_id
        )
    assert await _attempts(env, accepted.task_id) == (0, ["deferred"] * 3)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )

    failures = 1
    late = await accept_task(env, message="second")
    await env.pool.execute(
        "UPDATE tasks SET created_at = now() - make_interval(secs => $2) WHERE id = $1",
        late.task_id,
        runner.ADMISSION_WAIT_S + 1,
    )
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(late.task_id)) == "failed"
    assert (
        await env.pool.fetchval("SELECT terminal_code FROM tasks WHERE id = $1", late.task_id)
        == "preparation_failed"
    )


@pytest.mark.asyncio
async def test_a_refused_operation_stops_the_attempt(env: Env):
    """Review of #466: a cancel seen by the effect fence interrupts at once."""
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    reasons: list[str] = []
    inner = _CountingTool()
    await FencedTool(
        inner, env.tasks, accepted.task_id, claim.epoch, None, reasons.append
    ).execute()
    assert inner.calls == 0 and reasons == ["cancel_requested"]

    state = runner.AttemptState(claim=claim)
    state.execution = asyncio.create_task(asyncio.Event().wait())
    runner._operation_refused(state, "cancel_requested")
    assert state.cancel_requested and state.interrupted
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(2):
            await state.execution


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        # A reported error is no proof nothing happened (a write can time out
        # after the server accepted it): the effect may have happened.
        ({"success": False, "error": "Request failed: timeout"}, "unknown"),
        ({"error": "HTTP 502"}, "unknown"),
        # Only an explicit statement that nothing was done is a failure.
        ({"success": False, "performed": False, "error": "invalid"}, "failed"),
        ({"success": True}, "succeeded"),
    ],
)
async def test_tool_results_are_recorded_honestly(
    env: Env, returned: dict[str, Any], expected: str
):
    """#477 and review of #478: retry evidence never claims a definite outcome
    the tool cannot prove."""

    class _ReportingTool(_CountingTool):
        async def execute(self, **_kwargs: Any) -> str:
            self.calls += 1
            return json.dumps(returned)

    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="w", lease_s=LEASE_S)
    assert claim is not None
    await FencedTool(_ReportingTool(), env.tasks, accepted.task_id, claim.epoch).execute()
    assert (
        await env.pool.fetchval(
            "SELECT outcome FROM task_operations WHERE task_id = $1", accepted.task_id
        )
        == expected
    )


@pytest.mark.asyncio
async def test_a_cancel_before_preparation_makes_no_provider_backed_call(
    env: Env, mock_llm: None, monkeypatch: pytest.MonkeyPatch
):
    """#477: preparation (memory retrieval may call the embedding provider)
    starts only after a fresh cancel and suspension check."""
    prepared = 0

    async def counting_prompt(*_args: Any, **_kwargs: Any):
        nonlocal prepared
        prepared += 1
        return "system", None

    monkeypatch.setattr(runner, "_system_prompt", counting_prompt)
    accepted = await accept_task(env)
    real_settle = runner.settle_lost_attempt_holds

    async def cancel_then_settle(*args: Any, **kwargs: Any) -> int:
        await env.tasks.request_cancel(env.alice, accepted.task_id)
        return await real_settle(*args, **kwargs)

    monkeypatch.setattr(runner, "settle_lost_attempt_holds", cancel_then_settle)
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "cancelled"
    )
    assert prepared == 0


@pytest.mark.asyncio
async def test_a_regenerated_answer_discloses_the_interruption(env: Env, mock_llm: None):
    """#477 (§4): an answer produced after an interrupted attempt says so."""
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    await env.tasks.write_partial(accepted.task_id, first.epoch, content="Half", delta_seq=1)
    await expire_lease(env, accepted.task_id)  # the worker died mid-answer
    assert await runner.run_chat_task(_ctx(env, FakeRedis()), str(accepted.task_id)) == (
        "completed"
    )
    metadata = await env.pool.fetchval(
        "SELECT metadata FROM messages WHERE id = $1", accepted.result_message_id
    )
    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert metadata["regenerated_after_interruption"] == 1


@pytest.mark.asyncio
async def test_interruption_is_disclosed_even_if_the_next_attempt_never_executes(
    env: Env,
):
    """Review of #478: the count comes from the attempts, not attempt_count."""
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    await expire_lease(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    await env.tasks.acknowledge_cancel(accepted.task_id, second.epoch)
    metadata = await env.pool.fetchval(
        "SELECT metadata FROM messages WHERE id = $1", accepted.result_message_id
    )
    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert metadata["regenerated_after_interruption"] == 1


@pytest.mark.asyncio
async def test_web_fetch_refreshes_are_remembered_per_task_across_attempts(env: Env):
    """#475 and review of #478: refreshes are keyed by task identity (no
    cross-host clock comparison) and survive into a regenerated attempt."""
    from orchestrator.tools.web_fetch import WebFetchTool

    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="w1", lease_s=LEASE_S)
    assert first is not None
    registry = ToolRegistry()
    fetch = WebFetchTool()
    registry.register(fetch)
    runner._guard_tools(registry, env.tasks, runner.AttemptState(claim=first))
    guard = fetch.refresh_guard
    assert guard is not None
    assert not await guard.refreshed("https://example.com/a", "article")
    await guard.record("https://example.com/a", "article")

    await expire_lease(env, accepted.task_id)
    second = await env.tasks.claim(accepted.task_id, worker_id="w2", lease_s=LEASE_S)
    assert second is not None
    later = ToolRegistry()
    refetch = WebFetchTool()
    later.register(refetch)
    runner._guard_tools(later, env.tasks, runner.AttemptState(claim=second))
    assert refetch.refresh_guard is not None
    assert await refetch.refresh_guard.refreshed("https://example.com/a", "article")
    assert not await refetch.refresh_guard.refreshed("https://example.com/a", "metadata")
    # Only a digest is stored, never the URL.
    payloads = await env.pool.fetch(
        "SELECT payload_ciphertext FROM task_events WHERE kind = 'page_refreshed'"
    )
    assert payloads and all("example.com" not in str(p) for p in payloads)
