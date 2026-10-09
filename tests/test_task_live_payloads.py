"""Connected live payload delivery through the owner-scoped durable cursor."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from orchestrator.tasks import observe, runner
from tests.durable_tasks_support import LEASE_S, Env, FakeRedis, accept_task, durable_env_fixture

env = durable_env_fixture()


def _live(accepted, claim, seq, kind, name, **data):
    return {
        "t": "frame",
        "gen": claim.epoch,
        "seq": seq,
        "frame": observe.sse(
            kind,
            {
                "type": kind,
                "conversation_id": f"conv_{accepted.conversation_id}",
                "request_id": "original-submit",
                "data": {
                    "name": name,
                    "event_seq": seq,
                    "content_generation": claim.epoch,
                    **data,
                },
            },
        ),
    }


async def _connected(env, accepted, redis, *, authorized=None, poll_s=10):
    frames = []
    ready = asyncio.Event()
    changed = asyncio.Condition()

    async def consume():
        async for frame in observe.observe_task(
            env.tasks,
            redis,
            env.alice,
            accepted.task_id,
            request_id="observer",
            poll_s=poll_s,
            authorized=authorized,
        ):
            frames.append(runner._parse_frame(frame))
            async with changed:
                changed.notify_all()
            if frames[-1][1]["data"].get("lifecycle_kind") == "attempt_started":
                ready.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 5)

    async def until(seq):
        async with changed:
            await asyncio.wait_for(
                changed.wait_for(lambda: any(e["data"].get("event_seq") == seq for _, e in frames)),
                5,
            )

    return frames, task, until


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", ["search", "media"])
async def test_connected_observer_preserves_full_payloads_once(env: Env, fixture):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis)
    name = "web_search" if fixture == "search" else "generate_document"
    arguments = {"query": "fictional source"} if fixture == "search" else {"format": "csv"}
    result = (
        {
            "query": "fictional source",
            "results": [{"url": "https://example.test/source", "title": "Fixture"}],
            "total_found": 1,
        }
        if fixture == "search"
        else {"success": True, "file_url": "/generated-files/fixture.csv"}
    )
    channel = runner.live_channel(accepted.task_id)
    try:
        call = await env.tasks.record_event(
            accepted.task_id, claim.epoch, "tool_call", {"name": name, "epoch": claim.epoch}
        )
        await redis.publish(
            channel,
            json.dumps(_live(accepted, claim, call, "tool_call", name, arguments=arguments)),
        )
        await until(call)
        completed = await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": name, "epoch": claim.epoch, "outcome": "succeeded"},
        )
        message = _live(
            accepted, claim, completed, "tool_result", name, result=result, outcome="succeeded"
        )
        await redis.publish(channel, json.dumps(message))
        await until(completed)
        await redis.publish(channel, json.dumps(message))
        await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
        await redis.publish(channel, json.dumps({"t": "terminal", "gen": claim.epoch}))
        await asyncio.wait_for(task, 5)
        tools = [(kind, e) for kind, e in frames if kind in {"tool_call", "tool_result"}]
        assert [kind for kind, _ in tools] == ["tool_call", "tool_result"]
        assert tools[0][1]["data"]["arguments"] == arguments
        assert tools[1][1]["data"]["result"] == result
        assert all(e["request_id"] == "observer" for _, e in tools)
        assert [e["data"]["event_seq"] for _, e in tools] == [call, completed]
        if fixture == "search":
            # This is the actual metadata consumed by messageSources.ts, not an
            # event-count-only assertion: the source URL/title survives SSE.
            assert tools[1][1]["data"]["result"]["results"][0] == result["results"][0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_later_sequence_recovers_gap_without_losing_available_result(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis)
    try:
        call = await env.tasks.record_event(
            accepted.task_id, claim.epoch, "tool_call", {"name": "web_search", "epoch": claim.epoch}
        )
        completed = await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
        )
        result = {"query": "q", "results": [], "total_found": 0}
        message = _live(
            accepted,
            claim,
            completed,
            "tool_result",
            "web_search",
            result=result,
            outcome="succeeded",
        )
        await redis.publish(runner.live_channel(accepted.task_id), json.dumps(message))
        await until(completed)
        await redis.publish(
            runner.live_channel(accepted.task_id),
            json.dumps(
                _live(
                    accepted,
                    claim,
                    call,
                    "tool_call",
                    "web_search",
                    arguments={"query": "too late"},
                )
            ),
        )
        await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
        await redis.publish(
            runner.live_channel(accepted.task_id), json.dumps({"t": "terminal", "gen": claim.epoch})
        )
        await asyncio.wait_for(task, 5)
        tools = [e["data"] for kind, e in frames if kind in {"tool_call", "tool_result"}]
        assert [data["event_seq"] for data in tools] == [call, completed]
        assert tools[0]["arguments"] == {}  # no late enrichment/duplicate
        assert tools[1]["result"] == result
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_payload_attribution_and_authorization_fail_closed(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    seq = await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_result",
        {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
    )
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    original = _live(
        accepted,
        claim,
        seq,
        "tool_result",
        "web_search",
        outcome="succeeded",
        result={"query": "q", "results": [], "total_found": 0},
    )
    candidates = []
    for field, value in (
        ("seq", True),
        ("seq", seq + 1),
        ("gen", True),
        ("gen", claim.epoch + 1),
        ("t", "delta"),
        ("frame", "not SSE"),
    ):
        candidates.append({**original, field: value})
    for field, value in (
        ("name", "other_tool"),
        ("event_seq", True),
        ("event_seq", seq + 1),
        ("content_generation", True),
        ("content_generation", claim.epoch + 1),
        ("outcome", "unknown"),
        ("result", {"error": "contradictory failure"}),
    ):
        candidate = copy.deepcopy(original)
        kind, envelope = runner._parse_frame(candidate["frame"])
        assert kind is not None
        envelope["data"][field] = value
        candidate["frame"] = observe.sse(kind, envelope)
        candidates.append(candidate)
    for field, value in (
        ("type", "tool_call"),
        ("conversation_id", "conv_foreign"),
        ("data", None),
    ):
        candidate = copy.deepcopy(original)
        kind, envelope = runner._parse_frame(candidate["frame"])
        assert kind is not None
        envelope[field] = value
        candidate["frame"] = observe.sse(kind, envelope)
        candidates.append(candidate)

    for candidate in candidates:
        view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
        emitted = [
            runner._parse_frame(frame)
            async for frame in observe._drain_events(
                env.tasks,
                env.alice,
                accepted.task_id,
                view,
                snapshot,
                live_message=candidate,
            )
        ]
        result = next(e["data"] for kind, e in emitted if kind == "tool_result")
        assert result["result"] == {"outcome": "succeeded", "success": True}
        assert result["replayed"] is True
        assert view.event_seq == seq

    async def refused():
        return False

    for user, authorized in ((env.bob, None), (env.alice, refused)):
        view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
        emitted = []
        with pytest.raises(observe._ObserverRevoked):
            async for frame in observe._drain_events(
                env.tasks,
                user,
                accepted.task_id,
                view,
                snapshot,
                authorized=authorized,
                live_message=original,
            ):
                emitted.append(frame)
        assert emitted == [] and view.event_seq == 0


@pytest.mark.asyncio
async def test_initial_replay_never_duplicates_or_enriches_late_payload(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    seq = await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_result",
        {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
    )
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis)
    try:
        await until(seq)  # initial catch-up committed summary already emitted
        await redis.publish(
            runner.live_channel(accepted.task_id),
            json.dumps(
                _live(
                    accepted,
                    claim,
                    seq,
                    "tool_result",
                    "web_search",
                    outcome="succeeded",
                    result={"query": "q", "results": [], "total_found": 0},
                )
            ),
        )
        await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
        await redis.publish(
            runner.live_channel(accepted.task_id), json.dumps({"t": "terminal", "gen": claim.epoch})
        )
        await asyncio.wait_for(task, 5)
        results = [e["data"]["result"] for kind, e in frames if kind == "tool_result"]
        assert results == [{"outcome": "succeeded", "success": True}]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_material_summary_stays_single_without_proven_operation_correlation(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    operation = await env.tasks.begin_operation(
        accepted.task_id,
        claim.epoch,
        tool_name="generate_document",
        target=None,
        min_lease_margin_s=0,
    )
    await env.tasks.finish_operation(operation, outcome="succeeded")
    seq = await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_result",
        {"name": "generate_document", "epoch": claim.epoch, "outcome": "succeeded"},
    )
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
    message = _live(
        accepted,
        claim,
        seq,
        "tool_result",
        "generate_document",
        outcome="succeeded",
        result={"success": True, "file_url": "/generated-files/fixture.csv"},
    )
    emitted = [
        runner._parse_frame(frame)
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            view,
            snapshot,
            live_message=message,
        )
    ]
    results = [e["data"] for kind, e in emitted if kind == "tool_result"]
    assert len(results) == 1
    assert results[0]["operation_id"] == str(operation)
    assert results[0]["result"] == {"outcome": "succeeded", "success": True}
    assert view.event_seq == seq
    assert all(
        "file_url" not in str(e.payload)
        for e in await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["call", "delta", "terminal", "call_with_noise"])
async def test_ready_queued_sibling_payload_is_preserved_before_projection(
    env: Env, monkeypatch, trigger
):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis)
    entered, release = asyncio.Event(), asyncio.Event()
    read_snapshot = env.tasks.snapshot

    async def blocked_snapshot(*args, **kwargs):
        entered.set()
        await release.wait()
        return await read_snapshot(*args, **kwargs)

    monkeypatch.setattr(env.tasks, "snapshot", blocked_snapshot)
    channel = runner.live_channel(accepted.task_id)
    try:
        call = await env.tasks.record_event(
            accepted.task_id, claim.epoch, "tool_call", {"name": "web_search", "epoch": claim.epoch}
        )
        completed = await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
        )
        result = {
            "query": "q",
            "results": [{"url": "https://example.test/queued", "title": "Queued"}],
            "total_found": 1,
        }
        call_message = _live(
            accepted, claim, call, "tool_call", "web_search", arguments={"query": "q"}
        )
        if trigger in {"call", "call_with_noise"}:
            first = call_message
        elif trigger == "delta":
            # A delta gap forces resync while subsequent tool frames are ready.
            first = {"t": "delta", "gen": claim.epoch, "seq": 2, "text": "uncommitted"}
        else:
            await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
            first = {"t": "terminal", "gen": claim.epoch}
        await redis.publish(channel, json.dumps(first))
        await asyncio.wait_for(entered.wait(), 5)
        if trigger not in {"call", "call_with_noise"}:
            await redis.publish(channel, json.dumps(call_message))
        if trigger == "call_with_noise":
            await redis.publish(channel, "[]")
        await redis.publish(
            channel,
            json.dumps(
                _live(
                    accepted,
                    claim,
                    completed,
                    "tool_result",
                    "web_search",
                    result=result,
                    outcome="succeeded",
                )
            ),
        )
        release.set()
        await until(completed)
        if trigger != "terminal":
            await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
            await redis.publish(channel, json.dumps({"t": "terminal", "gen": claim.epoch}))
        await asyncio.wait_for(task, 5)
        tools = [e["data"] for kind, e in frames if kind in {"tool_call", "tool_result"}]
        assert [data["event_seq"] for data in tools] == [call, completed]
        assert tools[0]["arguments"] == {"query": "q"}
        assert tools[1]["result"] == result
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_seq", ["not-a-number", [1], True])
@pytest.mark.parametrize("deadline", [False, True])
async def test_malformed_delta_sequence_does_not_end_observation(env: Env, bad_seq, deadline):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis, poll_s=0 if deadline else 10)
    channel = runner.live_channel(accepted.task_id)
    try:
        await redis.publish(
            channel,
            json.dumps({"t": "delta", "gen": claim.epoch, "seq": bad_seq, "text": "malformed"}),
        )
        seq = await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
        )
        result = {"query": "q", "results": [], "total_found": 0}
        await redis.publish(
            channel,
            json.dumps(
                _live(
                    accepted,
                    claim,
                    seq,
                    "tool_result",
                    "web_search",
                    result=result,
                    outcome="succeeded",
                )
            ),
        )
        await until(seq)
        await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
        await redis.publish(channel, json.dumps({"t": "terminal", "gen": claim.epoch}))
        await asyncio.wait_for(task, 5)
        results = [e["data"]["result"] for kind, e in frames if kind == "tool_result"]
        assert len(results) == 1
        assert results[0] in (result, {"outcome": "succeeded", "success": True})
        assert not any(
            kind == "token" and e["data"].get("text") == "malformed" for kind, e in frames
        )
        assert frames[-1][0] == "done"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        {"type": "message", "data": "not json"},
        {"type": "message", "data": "[]"},
        {"type": "subscribe", "data": 1},
        [],
    ],
)
async def test_consumed_invalid_transport_item_is_not_an_empty_queue(raw):
    class PubSub:
        async def get_message(self, **kwargs):
            return raw

    assert await observe._next_message(PubSub(), 0) == {}


@pytest.mark.asyncio
async def test_malformed_live_generation_does_not_end_observation(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    redis = FakeRedis()
    frames, task, until = await _connected(env, accepted, redis)
    channel = runner.live_channel(accepted.task_id)
    try:
        await redis.publish(
            channel, json.dumps({"t": "frame", "gen": "not-a-number", "frame": "invalid"})
        )
        seq = await env.tasks.record_event(
            accepted.task_id,
            claim.epoch,
            "tool_result",
            {"name": "web_search", "epoch": claim.epoch, "outcome": "succeeded"},
        )
        result = {"query": "q", "results": [], "total_found": 0}
        await redis.publish(
            channel,
            json.dumps(
                _live(
                    accepted,
                    claim,
                    seq,
                    "tool_result",
                    "web_search",
                    result=result,
                    outcome="succeeded",
                )
            ),
        )
        await until(seq)
        await env.tasks.complete(accepted.task_id, claim.epoch, content="final")
        await redis.publish(channel, json.dumps({"t": "terminal", "gen": claim.epoch}))
        await asyncio.wait_for(task, 5)
        assert [e["data"]["result"] for kind, e in frames if kind == "tool_result"] == [result]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
