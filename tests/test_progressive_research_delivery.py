"""Real backend publication path with synthetic persistence and source transport."""

import asyncio
from collections.abc import AsyncGenerator
import json
from typing import cast

import pytest

from orchestrator.tasks import observe, runner
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from tests.progressive_research_support import (
    Delivery,
    ENDING,
    INCOMPLETE,
    PREFIX,
    PREFIX_PARTS,
    transcript,
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
@pytest.mark.parametrize("failure", ["exception", "refused", "cancel"])
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
        # The existing cleanup may save the prefix again but cannot run the tool.
        outcome = await runner._resolve(cast(TaskStore, delivery.store), delivery.state)
        assert (
            outcome == {"exception": "failed", "refused": "fenced", "cancel": "cancelled"}[failure]
        )
        if failure != "refused":
            frames = [runner._parse_frame(f) for f in await delivery.observation()]
            assert frames[-1][1]["data"]["status"] == (
                "cancelled" if failure == "cancel" else "error"
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
async def test_periodic_failure_is_best_effort_but_dirty_section_is_flushed():
    delivery = Delivery("periodic")
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        assert delivery.store.periodic_failed
        assert delivery.store.writes == [(PREFIX, 2)]
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        assert delivery.state.result is TaskStatus.COMPLETED
    finally:
        delivery.read.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
