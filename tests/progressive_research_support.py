"""Synthetic storage/transport around real runner, daemon, tools and observer.

No SQL, Redis server, network or provider admission. This is delivery-flow evidence,
not qualification of fences, accounting, model quality or remote termination.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from typing import Any, cast
import uuid
from unittest.mock import AsyncMock, patch

from orchestrator import daemon
from orchestrator.config import ProviderConfig, Settings
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
from orchestrator.tasks import observe, runner
from orchestrator.tasks.states import TaskStatus, decide_after_attempt
from orchestrator.tasks.store import Claim, LeaseLost, TaskEvent, TaskSnapshot, TaskStore
from orchestrator.tools.registry import Tool, ToolRegistry
from tests.durable_tasks_support import FakeRedis

PREFIX_PARTS = [
    "## Supported finding\nThe witness reports ",
    "a launch in 1842 ([testimony](https://archive.example/witness)).\n\n",
]
PREFIX = "".join(PREFIX_PARTS)
ENDING = (
    "## Further evidence\nThe register gives 1843 "
    "([register](https://archive.example/register)).\n\n"
    "Correction: 1842 was the witness's reported date, not a verified launch date. "
    "The sources conflict; the exact date remains unknown."
)
INCOMPLETE = "## Limits\nThe register could not be read. The date remains unverified."


class DeliveryStore:
    """One accepted synthetic result row; never a substitute for SQL fencing."""

    def __init__(self, claim: Claim, failure: str):
        self.claim = claim
        self.failure = failure
        self.events: list[TaskEvent] = []
        self.writes: list[tuple[str, int]] = []
        self.final_fields: dict[str, Any] = {}
        self.barrier = asyncio.Event()
        self.release_barrier = asyncio.Event()
        self.failed_once = False
        self.periodic_failed = False
        self.current = TaskSnapshot(
            claim.task_id,
            claim.conversation_id,
            claim.result_message_id,
            TaskStatus.RUNNING,
            None,
            claim.attempt_count,
            claim.epoch,
            0,
            "",
            0,
            False,
        )

    async def record_compute_scope(self, *args, **kwargs):
        pass

    async def begin_execution(self, *args, **kwargs):
        pass

    async def write_partial(self, task_id, epoch, *, content, delta_seq):
        assert (task_id, epoch) == (self.claim.task_id, self.claim.epoch)
        if self.failure == "periodic" and content == PREFIX_PARTS[0] and not self.periodic_failed:
            self.periodic_failed = True
            raise RuntimeError("synthetic periodic persistence failure")
        if content == PREFIX and not self.failed_once:
            self.barrier.set()
            self.failed_once = True
            if self.failure == "exception":
                raise RuntimeError("synthetic persistence failure")
            if self.failure == "refused":
                raise LeaseLost("synthetic lease refusal")
            if self.failure == "cancel":
                await self.release_barrier.wait()
        self.writes.append((content, delta_seq))
        self.current = replace(self.current, content=content, content_delta_seq=delta_seq)

    async def complete(self, task_id, epoch, *, content, message_fields):
        assert (task_id, epoch) == (self.claim.task_id, self.claim.epoch)
        self.final_fields = message_fields
        self.current = replace(self.current, status=TaskStatus.COMPLETED, content=content)
        return TaskStatus.COMPLETED

    async def record_event(self, task_id, epoch, kind, payload):
        seq = len(self.events) + 1
        self.events.append(TaskEvent(seq, kind, payload))
        self.current = replace(self.current, event_seq=seq)
        return seq

    async def snapshot(self, user_id, task_id):
        assert (user_id, task_id) == (self.claim.user_id, self.claim.task_id)
        return self.current

    async def events_since(self, user_id, task_id, *, after_seq, limit):
        return [event for event in self.events if event.seq > after_seq][:limit]

    async def fail_attempt(self, task_id, epoch, **kwargs):
        # Reuse the actual pure disposition rules; SQL/financial behavior is not
        # qualified by this synthetic final-attempt fixture.
        result = decide_after_attempt(
            attempt_count=self.claim.attempt_count,
            max_attempts=self.claim.max_attempts,
            material_started=False,
            **kwargs,
        )
        self.current = replace(
            self.current, status=result.status, terminal_code=result.terminal_code
        )
        return result.status

    async def acknowledge_cancel(self, task_id, epoch):
        self.current = replace(self.current, status=TaskStatus.CANCELLED, cancel_requested=True)
        return TaskStatus.CANCELLED


class Read(Tool):
    name = "web_fetch"
    description = "Synthetic gated source reading."
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

    def __init__(self, source_fails: bool):
        self.source_fails = source_fails
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.urls: list[str] = []

    async def execute(self, **kwargs):
        self.calls += 1
        self.urls.append(kwargs["url"])
        self.started.set()
        await self.release.wait()
        return json.dumps(
            {"error": "source_unavailable"}
            if self.source_fails
            else {"url": "https://archive.example/register", "text": "The register gives 1843."}
        )


class Discovery(Tool):
    name = "web_search"
    description = "Synthetic testimony discovery; no network."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return json.dumps(
            {
                "results": [
                    {
                        "url": "https://archive.example/witness",
                        "description": "The witness reports a launch in 1842.",
                    }
                ]
            }
        )


class Delivery:
    def __init__(
        self,
        failure: str = "",
        *,
        source_fails: bool = False,
        durable: bool = True,
        missing_writer: bool = False,
    ):
        self.claim = Claim(
            uuid.UUID(int=1),
            1,
            uuid.UUID(int=2),
            uuid.UUID(int=3),
            uuid.UUID(int=4),
            uuid.UUID(int=5),
            2,
            2,
            {
                "message": "Briefly research the launch date and distinguish testimony from fact.",
                "answer_detail": "brief",
                "research_effort": "research",
            },
        )
        self.store = DeliveryStore(self.claim, failure)
        self.state = runner.AttemptState(self.claim)
        self.redis = FakeRedis()
        self.read = Read(source_fails)
        self.calls = 0
        self.durable = durable
        self.source_fails = source_fails
        self.missing_writer = missing_writer
        self.registry = ToolRegistry()
        self.registry.register(Discovery())
        self.registry.register(self.read)
        self.raw_frames: list[str] = []

    @asynccontextmanager
    async def _scope(self, *args, **kwargs):
        yield

    async def dispatch(self, **kwargs):
        self.calls += 1
        if self.durable:
            assert "Answer detail: brief." in kwargs["messages"][0]["content"]
            assert "Research effort: research." in kwargs["messages"][0]["content"]
        call = self.calls
        if self.store.failure == "provider" and call == 3:
            raise RuntimeError("synthetic later-round provider failure")

        async def chunks():
            if call == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "discover",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            elif call == 2:
                assert "https://archive.example/witness" in kwargs["messages"][-1]["content"]
                for text in PREFIX_PARTS:
                    yield {"choices": [{"delta": {"content": text}}]}
                # Two reads in one batch: unchanged text must not cause two flushes.
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": f"read-{i}",
                                        "function": {
                                            "name": "web_fetch",
                                            "arguments": json.dumps({"url": url}),
                                        },
                                    }
                                    for i, url in enumerate(
                                        (
                                            "https://archive.example/register",
                                            "https://archive.example/context",
                                        )
                                    )
                                ]
                            }
                        }
                    ]
                }
            else:
                assert call == 3
                assert kwargs["messages"][-1]["role"] == "tool"
                yield {
                    "choices": [{"delta": {"content": INCOMPLETE if self.source_fails else ENDING}}]
                }

        return chunks()

    async def run(self):
        config = ProviderConfig(name="mock", model="mock/research", requires_auth=False)
        settings = SimpleNamespace(
            mock_llm=False,
            daemon_default_timezone="UTC",
            brave_api_key=None,
            tavily_api_key=None,
            web_search_provider="brave",
            chat_history_limit=20,
            get_provider_config=lambda _: config,
        )
        with ExitStack() as stack:
            for target, value in {
                "orchestrator.tasks.runner.account_compute": self._scope,
                "orchestrator.tasks.runner.current_scope": lambda: SimpleNamespace(
                    scope_id=uuid.UUID(int=6)
                ),
                "orchestrator.skills_store.build_skill_index": AsyncMock(return_value=""),
                "orchestrator.memory.injection.build_memory_context": AsyncMock(return_value=""),
                "orchestrator.memory.injection.assemble_system_prompt": AsyncMock(
                    return_value=DAEMON_SYSTEM_PROMPT
                ),
                "orchestrator.tasks.runner._history": AsyncMock(return_value=[]),
                "orchestrator.daemon.create_chat_registry": lambda **_: self.registry,
                "orchestrator.daemon.tool_round_limit": AsyncMock(return_value=5),
                "orchestrator.daemon._lazy_import_trust_signals": lambda: None,
                "orchestrator.daemon.enqueue_account_job": AsyncMock(return_value=object()),
                "orchestrator.daemon.clear_account_job_completion": AsyncMock(),
                "orchestrator.daemon.account_job_id": lambda *args, **kwargs: "synthetic-job",
                "orchestrator.tools.completion.guarded_completion": self.dispatch,
            }.items():
                stack.enter_context(patch(target, value))
            # In this serial fixture, patch the shared legacy get_event_loop
            # accessor, not the running loop's clock used by scheduling/timeouts.
            # Both chunks are deterministically <1s apart; the patch is scoped.
            real_loop = asyncio.get_running_loop()
            stack.enter_context(
                patch.object(
                    daemon.asyncio,
                    "get_event_loop",
                    return_value=SimpleNamespace(time=lambda: 100.0),
                )
            )
            assert asyncio.get_running_loop() is real_loop
            if self.durable:
                await runner._execute(
                    {
                        "settings": settings,
                        "store": None
                        if self.missing_writer
                        else SimpleNamespace(get_user_settings=AsyncMock(return_value={})),
                        "db_pool": object(),
                        "redis": self.redis,
                    },
                    cast(TaskStore, self.store),
                    self.state,
                )
            else:
                # Legacy request-bound writes stay best-effort. Use the same
                # synthetic row writer, but deliberately no message_sink.
                writer = runner.AttemptSink(cast(TaskStore, self.store), self.state)
                async for frame in daemon.stream_sse_chat(
                    settings=cast(Settings, settings),
                    provider_config=config,
                    system_prompt=DAEMON_SYSTEM_PROMPT,
                    user_message="Briefly research the launch date",
                    request_id="synthetic",
                    conversation_id=f"conv_{self.claim.conversation_id}",
                    memory_store=writer,
                    user_id=self.claim.user_id,
                    conversation_uuid=self.claim.conversation_id,
                    is_disconnected=AsyncMock(return_value=False),
                ):
                    self.raw_frames.append(frame)
                    if runner._parse_frame(frame)[0] == "token":
                        self.state.delta_seq += 1

    async def observation(self, *, partial: bool = False):
        frames = []
        stream = observe.observe_task(
            cast(TaskStore, self.store),
            None,
            self.claim.user_id,
            self.claim.task_id,
            request_id="synthetic-observer",
        )
        try:
            async for frame in stream:
                frames.append(frame)
                if partial and runner._parse_frame(frame)[0] == "token":
                    break
        finally:
            await cast(AsyncGenerator[str, None], stream).aclose()
        return frames


async def transcript(source_fails: bool = False):
    delivery = Delivery(source_fails=source_fails)
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        await asyncio.wait_for(delivery.read.started.wait(), 5)
        early = await delivery.observation(partial=True)
        prefix = delivery.store.current.content
        seq = delivery.store.current.content_delta_seq
        assert prefix == PREFIX and seq == 2
        assert delivery.store.current.status is TaskStatus.RUNNING
        delivery.read.release.set()
        await asyncio.wait_for(task, 5)
        final = await delivery.observation()
        return {
            "task_id": str(delivery.claim.task_id),
            "prefix": prefix,
            "final": delivery.store.current.content,
            "early_frames": early,
            "reconnect_frames": final,
            "dispatches": delivery.calls,
            "reads": delivery.read.calls,
        }
    finally:
        delivery.read.release.set()
        delivery.store.release_barrier.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def terminal_transcript(failure: str):
    delivery = Delivery(failure)
    task = asyncio.create_task(delivery.run())
    delivery.state.execution = task
    try:
        if failure == "cancel":
            await asyncio.wait_for(delivery.store.barrier.wait(), 5)
            delivery.state.cancel_requested = True
            runner._interrupt(delivery.state, task)
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        outcome = await runner._resolve(cast(TaskStore, delivery.store), delivery.state)
        assert delivery.read.calls == 0
        return {
            "task_id": str(delivery.claim.task_id),
            "final": delivery.store.current.content,
            "frames": await delivery.observation(),
            "status": outcome,
        }
    finally:
        delivery.store.release_barrier.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def client_transcripts():
    # Serial patch scopes: no concurrent fake model contexts or inference.
    return {
        "completed": await transcript(),
        "source_incomplete": await transcript(True),
        "failed": await terminal_transcript("exception"),
        "cancelled": await terminal_transcript("cancel"),
    }


if __name__ == "__main__":
    # Called by Vitest through the task's real Python environment, no server.
    print(json.dumps(asyncio.run(client_transcripts())))
