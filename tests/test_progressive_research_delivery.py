"""Real backend publication path with synthetic persistence and source transport."""

import asyncio
from collections.abc import AsyncGenerator
import json
from dataclasses import replace
from typing import Any, cast
import uuid

import pytest

from orchestrator.tasks import observe, runner
from orchestrator.tasks._origin import OriginalTaskOrigin
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks.states import RetryCause, TaskStatus
from orchestrator.tasks.store import TaskStore
from tests.progressive_research_support import (
    Delivery,
    ENDING,
    INCOMPLETE,
    PREFIX,
    PREFIX_PARTS,
    transcript,
    terminal_transcript,
    uncertain_transcript,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_fails", [False, True])
async def test_partial_section_persists_before_research_and_reconnects_once(source_fails):
    result = await transcript(source_fails)
    assert result["prefix"] == PREFIX
    assert result["final"] == PREFIX + (INCOMPLETE if source_fails else ENDING)
    assert result["dispatches"] == 3 and result["reads"] == 2
    early = [runner._parse_frame(frame) for frame in result["early_frames"]]
    assert not any(kind in {"done", "final"} for kind, _ in early)
    final = [runner._parse_frame(frame) for frame in result["reconnect_frames"]]
    assert (
        "".join(data["data"]["text"] for kind, data in final if kind == "token") == result["final"]
    )
    assert final[-1][1]["data"] == {"status": "completed"}


@pytest.mark.asyncio
async def test_one_flush_per_changed_section_and_periodic_sequence_is_consumed():
    delivery = Delivery()
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        assert delivery.store.writes == [(PREFIX_PARTS[0], 1), (PREFIX, 2)]
        assert delivery.state.result is None
        assert not delivery.store.final_fields
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        assert [content for content, _ in delivery.store.writes].count(PREFIX) == 1
        assert len(set(delivery.read.urls)) == 2
        assert delivery.store.current.result_message_id == delivery.claim.result_message_id
        assert delivery.state.result is TaskStatus.COMPLETED
    finally:
        delivery.read.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "ack_lost", "refused", "cancel"])
async def test_barrier_failure_never_forwards_or_executes_pending_tool(failure):
    delivery = Delivery(failure)
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        if failure == "cancel":
            await asyncio.wait_for(delivery.store.barrier.wait(), 5)
            delivery.state.cancel_requested = True
            runner._interrupt(delivery.state, task)
        result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        assert all(isinstance(item, asyncio.CancelledError) or item is None for item in result)
        assert delivery.read.calls == 0
        assert delivery.calls == 2
        assert not any(
            event.kind == "tool_call" and event.payload["name"] == "web_fetch"
            for event in delivery.store.events
        )
        assert delivery.state.result is None
        # The runner may not attempt another mutation after an unknown response.
        attempts = delivery.store.mutations.copy()
        outcome = await runner._resolve(cast(TaskStore, delivery.store), delivery.state)
        assert delivery.store.mutations == attempts
        assert len(delivery.store.partial_attempts) == 2
        assert not delivery.store.final_fields
        assert delivery.store.current.status is TaskStatus.RUNNING
        assert [
            message["text"]
            for _, message in delivery.redis.published
            if message.get("t") == "delta"
        ] == PREFIX_PARTS
        if failure == "refused":
            assert outcome == "fenced" and delivery.state.blocked is None
        else:
            assert outcome == (
                "task_transition_indeterminate"
                if failure == "cancel"
                else "task_observation_indeterminate"
            )
            assert delivery.state.blocked == outcome and delivery.state.phase == "closed"
            with pytest.raises(ObservationBlocked):
                await runner.AttemptSink(
                    cast(TaskStore, delivery.store), delivery.state
                ).update_message(content="forbidden second write")
            assert delivery.store.mutations == attempts
            frames = [
                runner._parse_frame(f) for f in await delivery.observation(snapshot_only=True)
            ]
            assert all(kind not in {"done", "final", "error"} for kind, _ in frames)
            assert frames[0][1]["data"]["status"] == "running"
            assert delivery.store.current.content == (
                PREFIX if failure == "ack_lost" else PREFIX_PARTS[0]
            )
    finally:
        delivery.store.release_barrier.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_bound_path_does_not_add_a_mandatory_flush():
    delivery = Delivery("exception", durable=False)
    task = asyncio.create_task(delivery.run())
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        assert delivery.store.writes == [(PREFIX_PARTS[0], 1)]
        assert not delivery.store.barrier.is_set()
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        assert delivery.read.calls == 2
    finally:
        delivery.read.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_periodic_uncertainty_closes_admission_without_second_write_or_provider():
    delivery = Delivery("periodic")
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        await asyncio.wait_for(task, 5)
        assert delivery.store.periodic_failed
        assert delivery.store.partial_attempts == [(PREFIX_PARTS[0], 1)]
        assert delivery.store.writes == [] and delivery.store.current.content == ""
        assert delivery.read.calls == 0 and delivery.calls == 2
        assert delivery.state.blocked == "task_observation_indeterminate"
        assert delivery.state.phase == "closed" and delivery.state.result is None
        assert [
            message["text"]
            for _, message in delivery.redis.published
            if message.get("t") == "delta"
        ] == [PREFIX_PARTS[0]]
        attempts = delivery.store.mutations.copy()
        publications = delivery.redis.published.copy()
        assert (
            await runner._resolve(cast(TaskStore, delivery.store), delivery.state)
            == delivery.state.blocked
        )
        assert delivery.store.mutations == attempts and delivery.redis.published == publications
        assert not any(
            event.kind == "tool_call" and event.payload["name"] == "web_fetch"
            for event in delivery.store.events
        )
    finally:
        delivery.read.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_acknowledged_terminals_are_distinct_from_unknown_writes(status):
    result = await terminal_transcript(status)
    assert result["status"] == status and result["final"] == PREFIX
    frames = [runner._parse_frame(frame) for frame in result["frames"]]
    assert frames[-1][0] == "done"
    assert frames[-1][1]["data"]["status"] == ("error" if status == "failed" else "cancelled")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "cancel", "periodic"])
async def test_unknown_transcripts_stop_at_snapshot_without_invented_terminal(failure):
    result = await uncertain_transcript(failure)
    assert result["status"] in {"task_observation_indeterminate", "task_transition_indeterminate"}
    frames = [runner._parse_frame(frame) for frame in result["frames"]]
    assert frames[0][1]["data"]["status"] == "running"
    assert all(kind not in {"done", "final", "error"} for kind, _ in frames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_id", "foreign-worker"),
        ("user_id", uuid.UUID(int=99)),
        ("task_id", uuid.UUID(int=99)),
        ("epoch", 99),
        ("conversation_id", uuid.UUID(int=99)),
        ("user_message_id", uuid.UUID(int=99)),
        ("result_message_id", uuid.UUID(int=99)),
        ("origin", None),
        ("origin", OriginalTaskOrigin(uuid.UUID(int=2), 8)),
        ("origin", OriginalTaskOrigin(uuid.UUID(int=99), 7)),
    ],
)
@pytest.mark.parametrize(
    "method",
    [
        "write_partial",
        "complete",
        "record_event",
        "acknowledge_cancel",
        "fail_attempt",
        "record_compute_scope",
        "begin_execution",
    ],
)
async def test_synthetic_mutations_validate_full_original_binding_before_effects(
    field, value, method
):
    delivery = Delivery()
    foreign = replace(delivery.claim, **{field: value})
    kwargs: dict[str, Any] = {"binding": foreign}
    args = [delivery.claim.task_id, delivery.claim.epoch]
    if method == "write_partial":
        kwargs.update(content=PREFIX, delta_seq=2)
    elif method == "complete":
        kwargs.update(content=PREFIX)
    elif method == "record_event":
        args.append("tool_call")
    elif method in {"record_compute_scope", "begin_execution"}:
        args.append(uuid.UUID(int=6))
        if method == "begin_execution":
            kwargs["prompt_version"] = "synthetic"
    elif method == "fail_attempt":
        kwargs.update(cause=RetryCause.TERMINAL_ERROR, error_code="synthetic")
    before = delivery.store.current
    with pytest.raises(AssertionError):
        await getattr(delivery.store, method)(*args, **kwargs)
    assert delivery.store.mutations == [method]
    assert delivery.store.current == before and delivery.store.events == delivery.store.writes == []


@pytest.mark.asyncio
async def test_missing_durable_writer_stops_before_the_continuing_read():
    delivery = Delivery(missing_writer=True)
    await asyncio.wait_for(delivery.run(), 5)
    assert delivery.calls == 2
    assert delivery.read.calls == 0
    assert not any(event.payload["name"] == "web_fetch" for event in delivery.store.events)
    assert delivery.state.result is None


@pytest.mark.asyncio
async def test_later_provider_error_stops_honestly_without_a_continuation_promise():
    delivery = Delivery("provider")
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        assert delivery.calls == 3
        assert delivery.store.current.content.startswith(PREFIX)
        assert "Further work stopped" in delivery.store.current.content
        assert "may be incomplete" in delivery.store.current.content
        assert "will continue" not in delivery.store.current.content
        # Preserve the existing limited-answer commit semantics, not a new state.
        assert delivery.state.result is TaskStatus.COMPLETED
    finally:
        delivery.read.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_duplicate_prefix_deltas_do_not_duplicate_reopened_content():
    delivery = Delivery()
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    stream = observe.observe_task(
        cast(TaskStore, delivery.store),
        delivery.redis,
        delivery.claim.user_id,
        delivery.claim.task_id,
        request_id="reopened",
        poll_s=0.02,
    )
    frames = []
    follower = None
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        async for frame in stream:
            frames.append(frame)
            if runner._parse_frame(frame)[0] == "token":
                break
        assert runner._parse_frame(frames[-1])[1]["data"]["text"] == PREFIX
        for channel, message in tuple(delivery.redis.published):
            if message.get("t") == "delta":
                await delivery.redis.publish(channel, json.dumps(message))

        async def follow():
            frames.extend([frame async for frame in stream])

        follower = asyncio.create_task(follow())
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        await runner._publish(delivery.redis, delivery.state, {"t": "terminal", "gen": 1})
        await asyncio.wait_for(follower, 5)
        decoded = [runner._parse_frame(frame) for frame in frames]
        assert (
            "".join(data["data"]["text"] for kind, data in decoded if kind == "token")
            == PREFIX + ENDING
        )
        assert [kind for kind, _ in decoded].count("done") == 1
    finally:
        delivery.read.release.set()
        for pending in (task, follower):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(
            *(p for p in (task, follower) if p is not None), return_exceptions=True
        )
        await cast(AsyncGenerator[str, None], stream).aclose()
