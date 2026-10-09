"""Durable cursor/lifecycle/outcome recovery with isolated real PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace

import pytest

from orchestrator.tasks import observe, runner
from orchestrator.tasks.fence import FencedTool
from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.tools.registry import Tool
from tests.durable_tasks_support import (
    LEASE_S,
    Env,
    FakeRedis,
    accept_task,
    durable_env_fixture,
    expire_lease,
)

env = durable_env_fixture()


def _frames(frames):
    return [runner._parse_frame(frame) for frame in frames]


async def _observe(env, task_id, redis=None, **kwargs):
    return [
        frame
        async for frame in observe.observe_task(
            env.tasks, redis, env.alice, task_id, request_id="test", poll_s=0.02, **kwargs
        )
    ]


@pytest.mark.asyncio
async def test_paginated_history_flushes_before_done_and_never_regresses_status(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    for i in range(observe.REPLAY_EVENT_LIMIT + 7):
        await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": f"read_{i}", "epoch": claim.epoch},
        )
    await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
    frames = _frames(await _observe(env, accepted.task_id))
    durable = [(event, data["data"]) for event, data in frames if "event_seq" in data["data"]]
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert [data["event_seq"] for _, data in durable] == list(range(1, snapshot.event_seq + 1))
    results = [data for event, data in durable if event == "tool_result"]
    assert len(results) == observe.REPLAY_EVENT_LIMIT + 7
    assert all(
        data["outcome"] == "unknown" and data["result"] == {"outcome": "unknown"}
        for data in results
    )
    lifecycle = [data for event, data in durable if event == "task"]
    assert {data["lifecycle_kind"] for data in lifecycle} >= {
        "accepted",
        "attempt_started",
        "finished",
    }
    assert all(data["status"] == "completed" for data in lifecycle)
    assert frames[-1][0] == "done"


@pytest.mark.asyncio
async def test_redis_gap_out_of_order_and_duplicates_are_only_hints(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames = []
    ready = asyncio.Event()

    async def consume():
        async for frame in observe.observe_task(
            env.tasks, redis, env.alice, accepted.task_id, request_id="test", poll_s=10
        ):
            frames.append(frame)
            if (
                runner._parse_frame(frame)[1].get("data", {}).get("lifecycle_kind")
                == "attempt_started"
            ):
                ready.set()

    observer = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 5)
    first = await env.tasks.record_event(
        accepted.task_id, claim.epoch, "tool_call", {"name": "read", "epoch": claim.epoch}
    )
    second = await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_result",
        {"name": "read", "epoch": claim.epoch, "outcome": "failed"},
    )
    channel = runner.live_channel(accepted.task_id)
    # Later seq arrives first; body is untrusted/incorrect, never replayed.
    for seq in (second, second, first):
        await redis.publish(
            channel,
            json.dumps(
                {"t": "frame", "gen": claim.epoch, "seq": seq, "frame": "not authoritative"}
            ),
        )
    await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
    await redis.publish(channel, json.dumps({"t": "terminal", "gen": claim.epoch}))
    await asyncio.wait_for(observer, 5)
    tools = [
        (event, data["data"])
        for event, data in _frames(frames)
        if event in {"tool_call", "tool_result"}
    ]
    assert [(event, data["event_seq"]) for event, data in tools] == [
        ("tool_call", first),
        ("tool_result", second),
    ]
    assert tools[1][1]["result"] == {"outcome": "failed", "success": False}


@pytest.mark.asyncio
async def test_fixed_deadline_catches_lost_progress_during_continuous_deltas(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    ready, recovered = asyncio.Event(), asyncio.Event()
    frames = []

    async def consume():
        async for frame in observe.observe_task(
            env.tasks, redis, env.alice, accepted.task_id, request_id="test", poll_s=0.02
        ):
            frames.append(frame)
            event, data = runner._parse_frame(frame)
            if data.get("data", {}).get("lifecycle_kind") == "attempt_started":
                ready.set()
            if event == "tool_result":
                recovered.set()

    observer = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 5)
    await env.tasks.record_event(
        accepted.task_id, claim.epoch, "tool_result", {"name": "lost_publish", "epoch": claim.epoch}
    )

    async def live_traffic():
        seq = 0
        while not recovered.is_set():
            seq += 1
            await redis.publish(
                runner.live_channel(accepted.task_id),
                json.dumps({"t": "delta", "gen": claim.epoch, "seq": seq, "text": "."}),
            )
            await asyncio.sleep(0)

    traffic = asyncio.create_task(live_traffic())
    try:
        await asyncio.wait_for(recovered.wait(), 5)
    finally:
        traffic.cancel()
        await asyncio.gather(traffic, return_exceptions=True)
    await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
    await asyncio.wait_for(observer, 5)
    assert all(
        data["data"]["content_generation"] == claim.epoch
        for event, data in _frames(frames)
        if event in {"token", "tool_result"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": 204, "headers": {}, "body": ""}, "succeeded"),
        ({"status": 422, "headers": {}, "body": "rejected"}, "unknown"),
        ({"status": 502, "headers": {}, "body": "upstream"}, "unknown"),
        ({"error": "timeout"}, "unknown"),
        ({"performed": False}, "failed"),
        ({}, "unknown"),
    ],
)
async def test_finished_operation_recovers_without_progress_recording(env: Env, result, expected):
    class Http(Tool):
        name = "http_request"
        description = "synthetic"
        parameters = {}

        async def execute(self, **_kwargs):
            return json.dumps(result)

    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    await FencedTool(Http(), env.tasks, accepted.task_id, claim.epoch).execute()
    # Simulate crash before runner's tool_result progress record. No retry is
    # permitted after an operation starts, regardless of its outcome.
    assert (
        await env.tasks.fail_attempt(
            accepted.task_id,
            claim.epoch,
            cause=RetryCause.RETRYABLE_ERROR,
            error_code="internal_error",
        )
        is TaskStatus.NEEDS_ATTENTION
    )
    evidence = await env.tasks.operations(env.alice, accepted.task_id)
    assert evidence[0]["outcome"] == expected
    frames = _frames(await _observe(env, accepted.task_id))
    results = [data["data"] for event, data in frames if event == "tool_result"]
    assert len(results) == 1 and results[0]["outcome"] == expected
    assert results[0]["operation_id"]
    assert results[0]["lifecycle_kind"] == "operation_finished"
    assert all(
        "headers" not in str(event.payload) and "body" not in str(event.payload)
        for event in await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    )


@pytest.mark.asyncio
async def test_stale_worker_completion_remains_durable_and_idempotent(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    operation = await env.tasks.begin_operation(
        accepted.task_id, claim.epoch, tool_name="http_request", target=None, min_lease_margin_s=0
    )
    await expire_lease(env, accepted.task_id)
    assert await env.tasks.claim(accepted.task_id, worker_id="next", lease_s=LEASE_S) is None
    await env.tasks.finish_operation(operation, outcome="unknown")
    await env.tasks.finish_operation(operation, outcome="succeeded")
    events = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    finished = [event for event in events if event.kind == "operation_finished"]
    assert len(finished) == 1 and finished[0].payload["outcome"] == "unknown"
    frames = _frames(await _observe(env, accepted.task_id))
    assert [data["data"]["outcome"] for event, data in frames if event == "tool_result"] == [
        "unknown"
    ]


@pytest.mark.asyncio
async def test_deferred_and_claim_only_generations_are_not_regenerations(env: Env):
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    assert await env.tasks.defer_execution(
        accepted.task_id, first.epoch, delay_s=0, reason="rate_limited", max_wait_s=900
    )
    second = await env.tasks.claim(accepted.task_id, worker_id="two", lease_s=LEASE_S)
    assert second is not None
    await expire_lease(env, accepted.task_id)
    third = await env.tasks.claim(accepted.task_id, worker_id="three", lease_s=LEASE_S)
    assert third is not None
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert snapshot.regenerated_after_interruption == 0
    await env.tasks.begin_execution(accepted.task_id, third.epoch, uuid.uuid4())
    await expire_lease(env, accepted.task_id)
    fourth = await env.tasks.claim(accepted.task_id, worker_id="four", lease_s=LEASE_S)
    assert fourth is not None
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    assert snapshot.regenerated_after_interruption == 1
    view = observe._Observation("conv", "req")
    view.generation = fourth.epoch
    view.displayed = "uncommitted"
    # Same-generation content correction retains the explicit count: consumers
    # must deduplicate disclosure by count, never equate any reset with a retry.
    corrections = _frames(view.catch_up(replace(snapshot, content="different committed content")))
    assert corrections[0][1]["data"]["regenerated_after_interruption"] == 1
    await env.tasks.complete(accepted.task_id, fourth.epoch, content="final")
    assert await env.tasks.events_since(env.bob, accepted.task_id, after_seq=0) == []
    assert [
        frame
        async for frame in observe.observe_task(
            env.tasks, None, env.bob, accepted.task_id, request_id="foreign"
        )
    ] == []


@pytest.mark.asyncio
async def test_revocation_during_paginated_replay_stops_without_done(env: Env, monkeypatch):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    for _ in range(5):
        await env.tasks.record_event(
            accepted.task_id, claim.epoch, "tool_call", {"name": "read", "epoch": claim.epoch}
        )
    await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
    monkeypatch.setattr(observe, "REPLAY_EVENT_LIMIT", 2)
    checks = 0

    async def authorized():
        nonlocal checks
        checks += 1
        return checks < 2

    frames = _frames(await _observe(env, accepted.task_id, authorized=authorized))
    assert not any(event == "done" for event, _ in frames)
    assert [data["data"]["event_seq"] for _, data in frames if "event_seq" in data["data"]] == [
        1,
        2,
    ]


@pytest.mark.asyncio
async def test_terminal_watermark_refresh_includes_concurrent_late_evidence(env: Env, monkeypatch):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    operation = await env.tasks.begin_operation(
        accepted.task_id, claim.epoch, tool_name="http_request", target=None, min_lease_margin_s=0
    )
    await env.tasks.fail_attempt(
        accepted.task_id, claim.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="internal_error"
    )
    read = env.tasks.events_since
    recorded = False

    async def late_finish(*args, **kwargs):
        nonlocal recorded
        events = await read(*args, **kwargs)
        if not recorded:
            recorded = True
            await env.tasks.finish_operation(operation, outcome="succeeded")
        return events

    monkeypatch.setattr(env.tasks, "events_since", late_finish)
    frames = _frames(await _observe(env, accepted.task_id))
    result_index = next(i for i, (event, _) in enumerate(frames) if event == "tool_result")
    done_index = next(i for i, (event, _) in enumerate(frames) if event == "done")
    assert result_index < done_index
    assert frames[result_index][1]["data"]["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_generation_change_replays_lifecycle_without_relabeling_old_progress(env: Env):
    accepted = await accept_task(env)
    first = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert first is not None
    await env.tasks.begin_execution(accepted.task_id, first.epoch, uuid.uuid4())
    await env.tasks.write_partial(accepted.task_id, first.epoch, content="partial", delta_seq=1)
    redis = FakeRedis()
    ready, regenerated = asyncio.Event(), asyncio.Event()
    frames = []

    async def consume():
        async for frame in observe.observe_task(
            env.tasks, redis, env.alice, accepted.task_id, request_id="test", poll_s=10
        ):
            frames.append(frame)
            event, envelope = runner._parse_frame(frame)
            data = envelope["data"]
            if event == "token" and data.get("text") == "partial":
                ready.set()
            if (
                event == "task"
                and data.get("reset")
                and data.get("regenerated_after_interruption") == 1
            ):
                regenerated.set()

    observer = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 5)
    await env.tasks.fail_attempt(
        accepted.task_id, first.epoch, cause=RetryCause.RETRYABLE_ERROR, error_code="internal_error"
    )
    await env.pool.execute(
        "UPDATE tasks SET next_wakeup_at = now() WHERE id = $1", accepted.task_id
    )
    second = await env.tasks.claim(accepted.task_id, worker_id="two", lease_s=LEASE_S)
    assert second is not None
    await env.tasks.begin_execution(accepted.task_id, second.epoch, uuid.uuid4())
    await redis.publish(
        runner.live_channel(accepted.task_id), json.dumps({"t": "terminal", "gen": second.epoch})
    )
    await asyncio.wait_for(regenerated.wait(), 5)
    await redis.publish(
        runner.live_channel(accepted.task_id),
        json.dumps({"t": "delta", "gen": first.epoch, "seq": 2, "text": "stale text"}),
    )
    await env.tasks.complete(accepted.task_id, second.epoch, content="final")
    await redis.publish(
        runner.live_channel(accepted.task_id), json.dumps({"t": "terminal", "gen": second.epoch})
    )
    await asyncio.wait_for(observer, 5)
    parsed = _frames(frames)
    assert "stale text" not in "".join(frames)
    lifecycle = [
        data["data"]
        for event, data in parsed
        if event == "task" and "lifecycle_kind" in data["data"]
    ]
    failed = next(data for data in lifecycle if data["lifecycle_kind"] == "attempt_failed")
    assert failed["content_generation"] == second.epoch and failed["lifecycle_epoch"] == first.epoch
    assert failed["status"] in {"running", "completed"}


@pytest.mark.asyncio
async def test_operation_lifecycle_does_not_duplicate_call_or_result_indicators(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    await env.tasks.record_event(
        accepted.task_id, claim.epoch, "tool_call", {"name": "http_request", "epoch": claim.epoch}
    )
    operation = await env.tasks.begin_operation(
        accepted.task_id, claim.epoch, tool_name="http_request", target=None, min_lease_margin_s=0
    )
    await env.tasks.finish_operation(operation, outcome="unknown")
    await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_result",
        {"name": "http_request", "epoch": claim.epoch, "outcome": "unknown"},
    )
    await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
    parsed = _frames(await _observe(env, accepted.task_id))
    assert [event for event, _ in parsed if event in {"tool_call", "tool_result"}] == [
        "tool_call",
        "tool_result",
    ]
    assert any(
        event == "task" and data["data"].get("lifecycle_kind") == "operation_started"
        for event, data in parsed
    )
