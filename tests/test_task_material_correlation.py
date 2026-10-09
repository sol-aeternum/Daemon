"""Real fenced media execution through completion, daemon, runner and observer."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
import uuid

import pytest
from cryptography.fernet import Fernet

from orchestrator.config import ProviderConfig, Settings, get_settings
from orchestrator.tasks import observe, runner
from orchestrator.tasks.fence import FencedTool
from orchestrator.tools import completion
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import Tool, ToolRegistry
from tests.durable_tasks_support import LEASE_S
from tests.durable_tasks_support import Env, FakeRedis, accept_task, durable_env_fixture

env = durable_env_fixture()


class MediaTool(Tool):
    name = "generate_document"
    description = "Synthetic media success; no inference or file writes"
    parameters = {"type": "object", "properties": {}}

    def __init__(self):
        self.calls = 0
        self.omit = False

    async def execute(self, **kwargs):
        self.calls += 1
        if kwargs.get("raise_error"):
            raise RuntimeError("Synthetic effect error")
        return json.dumps(
            {
                "success": True,
                "file_url": f"/generated-files/{kwargs.get('label', 'fixture')}.csv",
                **({"content": "oversized-material-result"} if self.omit else {}),
                # IDs in a body must never become trusted invocation metadata.
                **({"operation_id": kwargs["forged_id"]} if "forged_id" in kwargs else {}),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["available", "late", "missing", "crash", "omitted", "postdone"])
async def test_real_fenced_media_result_enriches_one_operation(env: Env, monkeypatch, mode):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for key in ("BRAVE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    media = MediaTool()
    media.omit = mode == "omitted"
    original_completion = completion.completion_with_tools
    finish, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def budget(*args):
        return SimpleNamespace(
            output_tokens=128,
            fits=lambda messages, **kwargs: "oversized-material-result" not in json.dumps(messages),
        )

    async def fake_provider(**kwargs):
        nonlocal calls
        calls += 1

        async def stream():
            if calls == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "media-call",
                                        "function": {
                                            "name": media.name,
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            else:
                await release.wait()
                yield {"choices": [{"delta": {"content": "Finished"}}]}

        return stream()

    async def real_completion(**kwargs):
        # The runner has already fenced the real registry. Replace only the
        # inner implementation, not the fence, executor or completion loop.
        kwargs["registry"].get(media.name)._inner = media
        kwargs["provider_config"] = kwargs["provider_config"].model_copy(
            update={"requires_auth": False}
        )
        async for event in original_completion(**kwargs):
            if mode == "crash" and event["type"] == "tool_result":
                finish.set()
                raise RuntimeError("Synthetic crash after effect/finish before progress")
            yield event

    monkeypatch.setattr(completion, "tool_context_budget", budget)
    monkeypatch.setattr(completion, "guarded_completion", fake_provider)
    monkeypatch.setattr("orchestrator.daemon.completion_with_tools", real_completion)
    accepted = await accept_task(env)

    class Redis(FakeRedis):
        withheld = None

        async def publish(self, channel, message):
            parsed = json.loads(message)
            if parsed.get("t") == "frame":
                kind, _ = runner._parse_frame(parsed["frame"])
                if kind == "tool_result":
                    finish.set()
                    if mode in {"late", "missing", "postdone"}:
                        self.withheld = (channel, message)
                        return 1
            return await super().publish(channel, message)

    redis = Redis()
    frames = []
    summary = asyncio.Event()

    async def consume():
        async for frame in observe.observe_task(
            env.tasks,
            redis,
            env.alice,
            accepted.task_id,
            request_id="observer",
            poll_s=0.02,
        ):
            kind, envelope = runner._parse_frame(frame)
            frames.append((kind, envelope))
            if kind == "tool_result":
                summary.set()

    job = asyncio.create_task(
        runner.run_chat_task(
            {
                "task_store": env.tasks,
                "store": env.memory,
                "db_pool": env.pool,
                "settings": get_settings(),
                "redis": redis,
            },
            str(accepted.task_id),
        )
    )
    observer = None
    try:
        await asyncio.wait_for(finish.wait(), 5)
        # Attach after finish/progress, then deliver the original runner frame.
        # This exercises already-consumed progress, not a fabricated fixture.
        if mode == "available":
            snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
            assert snapshot is not None
            original = next(
                m
                for _, m in redis.published
                if m.get("t") == "frame" and runner._parse_frame(m["frame"])[0] == "tool_result"
            )
            view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
            frames.extend(
                [
                    runner._parse_frame(frame)
                    async for frame in observe._drain_events(
                        env.tasks,
                        env.alice,
                        accepted.task_id,
                        view,
                        snapshot,
                        live_message=original,
                    )
                ]
            )
            # Duplicate or consumed progress never adds a second full result.
            duplicate = [
                frame
                async for frame in observe._drain_events(
                    env.tasks,
                    env.alice,
                    accepted.task_id,
                    view,
                    snapshot,
                    live_message=original,
                )
            ]
            assert duplicate == []
        else:
            observer = asyncio.create_task(consume())
            await asyncio.wait_for(summary.wait(), 5)
        if mode == "late":
            assert redis.withheld is not None
            await FakeRedis.publish(redis, *redis.withheld)
        for _ in range(100 if mode == "late" else 0):
            if any(
                e["data"].get("payload_state") == "full"
                for kind, e in frames
                if kind == "tool_result"
            ):
                break
            await asyncio.sleep(0.01)
        release.set()
        assert await asyncio.wait_for(job, 5) == (
            "needs_attention" if mode == "crash" else "completed"
        )
        if observer is not None:
            await asyncio.wait_for(observer, 5)
        if mode == "postdone":
            assert redis.withheld is not None and observer is not None and observer.done()
            before = list(frames)
            await FakeRedis.publish(redis, *redis.withheld)
            assert frames == before and frames[-1][0] == "done"
        results = [e["data"] for kind, e in frames if kind == "tool_result"]
        if mode in {"available", "late"}:
            assert results[-1]["result"]["file_url"] == "/generated-files/fixture.csv"
            assert results[-1]["payload_state"] == "full"
            assert len(results) == (2 if mode == "late" else 1)
        else:
            assert len(results) == 1
            assert results[0]["payload_state"] == "summary"
            assert results[0]["result"] == {"success": True, "outcome": "succeeded"}
        assert len({r["operation_id"] for r in results}) == 1
        assert all(r["task_id"] == str(accepted.task_id) for r in results)
        assert media.calls == 1
        events = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
        if mode != "crash":
            progress = next(e for e in events if e.kind == "tool_result")
            assert progress.payload["operation_id"] == results[-1]["operation_id"]
        # Repeat delivery after completion/uncertain crash cannot repeat effects.
        assert (
            await runner.run_chat_task({"task_store": env.tasks}, str(accepted.task_id))
            == "skipped"
        )
        assert all("file_url" not in str(e.payload) for e in events)
    finally:
        release.set()
        job.cancel()
        if observer is not None:
            observer.cancel()
        await asyncio.gather(job, *([observer] if observer else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_invocation_identity_isolated_for_same_name_nested_and_refused_calls(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    registry = ToolRegistry()
    media = MediaTool()
    registry.register(FencedTool(media, env.tasks, accepted.task_id, claim.epoch))
    executor = ToolExecutor(registry)
    forged = str(uuid.uuid4())
    first, second = await asyncio.gather(
        executor.execute_invocation(media.name, {"label": "one", "forged_id": forged}),
        executor.execute_invocation(media.name, {"label": "two"}),
    )
    assert first.operation is not None and second.operation is not None
    assert first.operation.operation_id != second.operation.operation_id
    assert str(first.operation.operation_id) != forged
    assert json.loads(first.result)["file_url"] == "/generated-files/one.csv"
    assert json.loads(second.result)["file_url"] == "/generated-files/two.csv"

    child = None

    class Advisor(Tool):
        name = "consult_advisor"
        description = "Synthetic nested invocation"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            nonlocal child
            child = await executor.execute_invocation(media.name, {"label": "child"})
            assert child.operation is not None
            return json.dumps({"success": True, "operation_id": str(child.operation.operation_id)})

    registry.register(FencedTool(Advisor(), env.tasks, accepted.task_id, claim.epoch))
    parent = await executor.execute_invocation("consult_advisor", {})
    assert parent.operation is not None
    assert child is not None and child.operation is not None
    assert parent.operation.tool_name == "consult_advisor"
    assert parent.operation.operation_id != child.operation.operation_id
    assert child.operation.tool_name == media.name
    assert (await executor.execute_invocation(media.name, "not json")).operation is None
    assert (await executor.execute_invocation("unknown_tool", {})).operation is None
    failed = await executor.execute_invocation(media.name, {"raise_error": True})
    assert failed.operation is None
    assert "Synthetic effect error" in failed.result
    await env.tasks.request_cancel(env.alice, accepted.task_id)
    refused = await executor.execute_invocation(media.name, {})
    assert refused.operation is None and json.loads(refused.result)["performed"] is False
    assert media.calls == 4  # two siblings, child and exception; no refused call
    events = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    finished = [e for e in events if e.kind == "operation_finished"]
    assert len(finished) == 5
    assert [e.payload["outcome"] for e in finished].count("unknown") == 1


async def _material_progress(env, accepted, claim, *, filler=False, label="fixture"):
    registry = ToolRegistry()
    registry.register(FencedTool(MediaTool(), env.tasks, accepted.task_id, claim.epoch))
    invocation = await ToolExecutor(registry).execute_invocation(
        "generate_document", {"label": label}
    )
    if filler:
        await env.tasks.record_event(accepted.task_id, claim.epoch, "fixture_gap", {})
    data = {
        "name": "generate_document",
        "result": json.loads(invocation.result),
        **invocation.event_metadata(),
        "content_generation": claim.epoch,
    }
    seq = await runner._record_progress(
        env.tasks,
        runner.AttemptState(claim),
        "tool_result",
        data["name"],
        data,
    )
    data["event_seq"] = seq
    message = {
        "t": "frame",
        "gen": claim.epoch,
        "seq": seq,
        "frame": observe.sse(
            "tool_result",
            {
                "type": "tool_result",
                "conversation_id": f"conv_{accepted.conversation_id}",
                "data": data,
            },
        ),
    }
    return message


@pytest.mark.asyncio
async def test_operation_payload_proof_negatives_pages_and_consumed_sequences(
    env: Env, monkeypatch
):
    monkeypatch.setattr(observe, "REPLAY_EVENT_LIMIT", 2)
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    original = await _material_progress(env, accepted, claim)
    other = await accept_task(env)
    other_claim = await env.tasks.claim(other.task_id, worker_id="two", lease_s=LEASE_S)
    assert other_claim is not None
    foreign = await _material_progress(env, other, other_claim)
    _, foreign_envelope = runner._parse_frame(foreign["frame"])
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    candidates = []
    for key, value in (
        ("operation_id", str(uuid.uuid4())),
        ("operation_id", "invalid"),
        ("operation_id", foreign_envelope["data"]["operation_id"]),
        ("task_id", str(other.task_id)),
        ("task_id", None),
        ("lifecycle_epoch", claim.epoch + 1),
        ("lifecycle_epoch", True),
        ("content_generation", claim.epoch + 1),
        ("content_generation", True),
        ("name", "notification_send"),
        ("outcome", "failed"),
        ("payload_state", "summary"),
        ("result", {"success": False}),
        ("event_seq", True),
        ("operation_id", None),
    ):
        candidate = copy.deepcopy(original)
        _, envelope = runner._parse_frame(candidate["frame"])
        envelope["data"][key] = value
        candidate["frame"] = observe.sse("tool_result", envelope)
        candidates.append(candidate)
    candidates += [
        {**original, "seq": original["seq"] + 1},
        {**original, "seq": True},
        {**original, "gen": claim.epoch + 1},
    ]
    candidate = copy.deepcopy(original)
    _, envelope = runner._parse_frame(candidate["frame"])
    envelope["conversation_id"] = "conv_foreign"
    candidate["frame"] = observe.sse("tool_result", envelope)
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
        results = [e["data"] for kind, e in emitted if kind == "tool_result"]
        assert len(results) == 1 and results[0]["payload_state"] == "summary"
        assert view.event_seq == snapshot.event_seq
        # Invalid late candidates also cannot enrich consumed durable progress.
        assert [
            f
            async for f in observe._drain_events(
                env.tasks,
                env.alice,
                accepted.task_id,
                view,
                snapshot,
                live_message=candidate,
            )
        ] == []

    view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
    emitted = [
        runner._parse_frame(frame)
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            view,
            snapshot,
            live_message={"t": "frame", "gen": claim.epoch, "seq": 1, "frame": "invalid"},
            ready_messages=({}, original),
        )
    ]
    results = [e["data"] for kind, e in emitted if kind == "tool_result"]
    assert len(results) == 1 and results[0]["payload_state"] == "full"
    assert [
        f
        async for f in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            view,
            snapshot,
            live_message=original,
        )
    ] == []
    for user in (env.bob, env.alice):

        async def revoked():
            return False

        with pytest.raises(observe._ObserverRevoked):
            _ = [
                f
                async for f in observe._drain_events(
                    env.tasks,
                    user,
                    accepted.task_id,
                    observe._Observation(f"conv_{accepted.conversation_id}", "observer"),
                    snapshot,
                    authorized=revoked if user == env.alice else None,
                    live_message=original,
                )
            ]


@pytest.mark.asyncio
async def test_durable_gap_cannot_authenticate_available_material_payload(env: Env, monkeypatch):
    monkeypatch.setattr(observe, "REPLAY_EVENT_LIMIT", 2)
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    message = await _material_progress(env, accepted, claim, filler=True)
    await env.pool.execute(
        "DELETE FROM task_events WHERE task_id = $1 AND kind = 'fixture_gap'", accepted.task_id
    )
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    emitted = []
    with pytest.raises(observe._ObserverRevoked):
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            observe._Observation(f"conv_{accepted.conversation_id}", "observer"),
            snapshot,
            live_message=message,
        ):
            emitted.append(runner._parse_frame(frame))
    assert not any(e["data"].get("payload_state") == "full" for _, e in emitted)


@pytest.mark.asyncio
async def test_runner_rejects_unproven_identity_and_preserves_suppressed_outcome(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    state = runner.AttemptState(claim)
    registry = ToolRegistry()
    registry.register(FencedTool(MediaTool(), env.tasks, accepted.task_id, claim.epoch))
    invocation = await ToolExecutor(registry).execute_invocation("generate_document", {})
    full = {
        "name": "generate_document",
        "result": json.loads(invocation.result),
        **invocation.event_metadata(),
    }
    for field, value in (
        ("operation_id", str(uuid.uuid4())),
        ("task_id", str(uuid.uuid4())),
        ("lifecycle_epoch", claim.epoch + 1),
        ("outcome", "failed"),
        ("name", "notification_send"),
    ):
        candidate = {**full, field: value}
        evidence = await runner._progress_evidence(env.tasks, state, candidate)
        assert "operation_id" not in evidence and "operation_id" not in candidate
    suppressed = {**full, "result": completion.OMITTED_RESULT, "payload_state": "summary"}
    evidence = await runner._progress_evidence(env.tasks, state, suppressed)
    assert evidence["outcome"] == "succeeded" and evidence["payload_state"] == "summary"
    assert evidence["operation_id"] == full["operation_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["skipped", "auto_spawn"])
async def test_completion_skip_and_auto_spawn_identity_paths(env: Env, monkeypatch, path):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    media = MediaTool()
    if path == "auto_spawn":
        media.name = "spawn_agent"
    registry = ToolRegistry()
    registry.register(FencedTool(media, env.tasks, accepted.task_id, claim.epoch))

    async def budget(*args):
        return SimpleNamespace(
            output_tokens=128,
            fits=lambda messages, **kwargs: (
                path != "skipped" or "This tool was not executed" not in json.dumps(messages)
            ),
        )

    async def provider(**kwargs):
        async def stream():
            if path == "skipped":
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "skipped-call",
                                        "function": {"name": media.name, "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            else:
                yield {"choices": [{"delta": {"content": "Retrying"}}]}

        return stream()

    monkeypatch.setattr(completion, "tool_context_budget", budget)
    monkeypatch.setattr(completion, "guarded_completion", provider)
    messages = (
        [
            {
                "role": "tool",
                "name": "spawn_agent",
                "tool_call_id": "old",
                "content": json.dumps(
                    {"agent_type": "document", "metadata": {"session_id": "old-session"}}
                ),
            },
            {"role": "user", "content": "try again"},
        ]
        if path == "auto_spawn"
        else [{"role": "user", "content": "make a document"}]
    )
    events = [
        event
        async for event in completion.completion_with_tools(
            settings=Settings(),
            provider_config=ProviderConfig(
                name="openrouter", model="openrouter/test-model", requires_auth=False
            ),
            messages=messages,
            registry=registry,
            actual_model="openrouter/test-model",
        )
    ]
    results = [event for event in events if event["type"] == "tool_result"]
    assert len(results) == 1
    evidence = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    operations = [event for event in evidence if event.kind == "operation_finished"]
    if path == "skipped":
        assert results[0]["result"] == completion.SKIPPED_RESULT
        assert "operation_id" not in results[0] and "payload_state" not in results[0]
        assert media.calls == 0 and operations == []
    else:
        assert media.calls == 1 and len(operations) == 1
        assert results[0]["operation_id"] == operations[0].payload["operation_id"]
        assert results[0]["task_id"] == str(accepted.task_id)
        assert results[0]["lifecycle_epoch"] == claim.epoch
        assert results[0]["payload_state"] == "full"


@pytest.mark.asyncio
async def test_finish_recording_failure_cannot_create_hidden_correlated_progress(
    env: Env, monkeypatch
):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None

    async def failed_finish(*args, **kwargs):
        raise RuntimeError("Synthetic outcome transaction failure")

    monkeypatch.setattr(env.tasks, "finish_operation", failed_finish)
    await env.tasks.record_event(
        accepted.task_id,
        claim.epoch,
        "tool_call",
        {"name": "generate_document", "epoch": claim.epoch},
    )
    original = await _material_progress(env, accepted, claim)
    _, envelope = runner._parse_frame(original["frame"])
    assert "operation_id" not in envelope["data"]
    events = await env.tasks.events_since(env.alice, accepted.task_id, after_seq=0)
    assert not any(event.kind == "operation_finished" for event in events)
    progress = next(event for event in events if event.kind == "tool_result")
    assert "operation_id" not in progress.payload
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    frames = [
        runner._parse_frame(frame)
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            observe._Observation(f"conv_{accepted.conversation_id}", "observer"),
            snapshot,
            live_message=original,
        )
    ]
    assert [kind for kind, _ in frames if kind in {"tool_call", "tool_result"}] == [
        "tool_call",
        "tool_result",
    ]
    assert (
        next(e["data"]["result"] for kind, e in frames if kind == "tool_result")["file_url"]
        == "/generated-files/fixture.csv"
    )


@pytest.mark.asyncio
async def test_same_name_operation_payloads_remain_distinct_through_observer(env: Env):
    accepted = await accept_task(env)
    claim = await env.tasks.claim(accepted.task_id, worker_id="one", lease_s=LEASE_S)
    assert claim is not None
    messages = await asyncio.gather(
        _material_progress(env, accepted, claim, label="first"),
        _material_progress(env, accepted, claim, label="second"),
    )
    snapshot = await env.tasks.snapshot(env.alice, accepted.task_id)
    assert snapshot is not None
    view = observe._Observation(f"conv_{accepted.conversation_id}", "observer")
    frames = [
        runner._parse_frame(frame)
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            view,
            snapshot,
            ready_messages=tuple(reversed(messages)),
        )
    ]
    results = [e["data"] for kind, e in frames if kind == "tool_result"]
    assert len(results) == 2 and len({r["operation_id"] for r in results}) == 2
    expected = {}
    for message in messages:
        _, envelope = runner._parse_frame(message["frame"])
        expected[envelope["data"]["operation_id"]] = envelope["data"]["result"]["file_url"]
    assert {r["operation_id"]: r["result"]["file_url"] for r in results} == expected
    assert all(r["payload_state"] == "full" for r in results)
    assert [
        frame
        async for frame in observe._drain_events(
            env.tasks,
            env.alice,
            accepted.task_id,
            view,
            snapshot,
            ready_messages=tuple(messages),
        )
    ] == []
