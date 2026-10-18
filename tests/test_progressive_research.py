"""Instruction and existing-loop regressions, not model-quality qualification.

All provider chunks and source bodies are scripted. These tests prove delivery
order and local cancellation only, not claim entailment, durable recovery, remote
producer cessation, production latency or real research accuracy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
from typing import Any, cast

import pytest

from orchestrator.config import ProviderConfig, Settings
from orchestrator.memory.injection import assemble_system_prompt
from orchestrator.prompts import DAEMON_PROMPT_VERSION, DAEMON_SYSTEM_PROMPT
from orchestrator.tools.completion import completion_with_tools
from orchestrator.tools.registry import Tool, ToolRegistry


@pytest.mark.parametrize(
    "instruction",
    [
        "Simple, stable questions normally need one direct answer",
        "A brief answer can require deep checking",
        "a detailed answer must not add unsupported specifics",
        "changing facts (including prices)",
        "Verify central claims before publishing them confidently",
        "bounded, targeted check",
        "primary testimony",
        "independent corroboration, inference, and unknowns",
        "repeated reports copying one source are not independent corroboration",
        "Use only citations from returned evidence",
        "A failed fetch or unsuccessful search is not evidence that something does not exist",
        "precise, candid correction",
        "actual remaining tool calls in this turn",
        "Do not end a turn promising research that has not run",
        "clearly distinguishing completed checking from incomplete work",
        "Do not spawn extra agents or add model calls solely to send an early message",
        "never bypass a refusal or automatically retry a failed search",
    ],
)
@pytest.mark.asyncio
async def test_research_guidance_reaches_production_prompt_assembly(instruction):
    assembled = await assemble_system_prompt("")
    assert instruction in DAEMON_SYSTEM_PROMPT
    assert instruction in assembled
    assert DAEMON_PROMPT_VERSION >= 6


def _chunk(content: str = "", *, tool: str | None = None, call_id: str = "") -> dict[str, Any]:
    delta: dict[str, Any] = {"content": content}
    if tool is not None:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": call_id,
                "function": {"name": tool, "arguments": "{}"},
            }
        ]
    return {"choices": [{"delta": delta}]}


PRIMARY = "https://archive.example/primary"
INDEPENDENT = "https://archive.example/independent"
PREFIX = f"The witness reports a launch in 1842 ([testimony]({PRIMARY})).\n\n"
SUFFIX = (
    f"An independent register also dates it to 1842 ([register]({INDEPENDENT})). "
    "Its cause remains unknown."
)
INCOMPLETE = "The independent register could not be read; the date is not independently verified."


class ScriptedSearch(Tool):
    name = "web_search"
    description = "Synthetic source discovery; no transport or account inference."
    parameters = {"type": "object", "properties": {}}

    def __init__(self):
        self.calls = 0

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return json.dumps({"url": PRIMARY, "snippet": "The witness reports a launch in 1842."})


class GatedRead(Tool):
    name = "web_fetch"
    description = "Synthetic read held at a deterministic gate."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, fail: bool):
        self.fail = fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self.fail:
            return json.dumps({"error": "source_unavailable"})
        return json.dumps({"url": INDEPENDENT, "text": "The register dates the launch to 1842."})


def _research_stream(fail: bool = False):
    """Use the real loop/executor but no real provider, search or fetch adapters."""
    search, read = ScriptedSearch(), GatedRead(fail)
    registry = ToolRegistry()
    registry.register(search)
    registry.register(read)
    dispatches: list[dict[str, Any]] = []

    async def dispatch(**kwargs):
        dispatches.append(kwargs)
        round_index = len(dispatches)

        async def chunks():
            if round_index == 1:
                yield _chunk(tool="web_search", call_id="discovery")
            elif round_index == 2:
                assert kwargs["messages"][-1]["tool_call_id"] == "discovery"
                assert PRIMARY in kwargs["messages"][-1]["content"]
                yield _chunk(PREFIX, tool="web_fetch", call_id="corroboration")
            else:
                assert round_index == 3
                assert kwargs["messages"][-1]["tool_call_id"] == "corroboration"
                assert 'trust="untrusted"' in kwargs["messages"][-1]["content"]
                expected = "source_unavailable" if fail else INDEPENDENT
                assert expected in kwargs["messages"][-1]["content"]
                yield _chunk(INCOMPLETE if fail else SUFFIX)

        return chunks()

    stream = completion_with_tools(
        Settings(),
        ProviderConfig(name="mock", model="mock/research", requires_auth=False),
        [
            {"role": "system", "content": DAEMON_SYSTEM_PROMPT},
            {"role": "user", "content": "Briefly verify this obscure launch's date and cause."},
        ],
        registry,
        completion_dispatch=dispatch,
    )
    return cast(AsyncGenerator[dict[str, Any], None], stream), search, read, dispatches


@pytest.mark.parametrize("source_fails", [False, True])
@pytest.mark.asyncio
async def test_supported_prefix_does_not_end_research_and_sources_are_paired(source_fails):
    stream, search, read, dispatches = _research_stream(source_fails)
    events = []
    pending = None
    try:
        # Consume up to the continuing read, with the first useful text already visible.
        async for event in stream:
            events.append(event)
            if event["type"] == "tool_executing" and event["name"] == "web_fetch":
                break
        assert [e["content"] for e in events if e["type"] == "content_delta"] == [PREFIX]
        assert not any(e["type"] in {"content_done", "done"} for e in events)
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.wait_for(read.started.wait(), timeout=2)
        assert not pending.done()
        assert len(dispatches) == 2
        assert search.calls == read.calls == 1
        read.release.set()
        events.append(await asyncio.wait_for(pending, timeout=2))
        events.extend([event async for event in stream])
    finally:
        read.release.set()
        if pending is not None and not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await stream.aclose()

    expected_end = INCOMPLETE if source_fails else SUFFIX
    text = "".join(e["content"] for e in events if e["type"] == "content_delta")
    assert text == PREFIX + expected_end
    assert len(dispatches) == 3  # Existing sequential tool rounds, no early-message-only call.
    assert [e["type"] for e in events].count("done") == 1
    assert events[-1]["type"] == "done"
    assert search.calls == read.calls == 1  # A failed read is not retried.
    # A normal synthesis after a failed source is NOT a durable partial-failure state.


@pytest.mark.asyncio
async def test_cancel_after_prefix_reaches_cooperative_read_without_further_dispatch():
    stream, search, read, dispatches = _research_stream()
    events = []
    pending = None
    try:
        async for event in stream:
            events.append(event)
            if event["type"] == "tool_executing" and event["name"] == "web_fetch":
                break
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.wait_for(read.started.wait(), timeout=2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert read.cancelled.is_set()
        assert len(dispatches) == 2
        assert search.calls == read.calls == 1
        assert [e["content"] for e in events if e["type"] == "content_delta"] == [PREFIX]
        assert not any(e["type"] == "done" for e in events)
    finally:
        read.release.set()
        if pending is not None and not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await stream.aclose()


@pytest.mark.asyncio
async def test_stable_answer_uses_one_dispatch_without_staging():
    dispatches = []

    async def dispatch(**kwargs):
        dispatches.append(kwargs)

        async def chunks():
            yield _chunk("Four.")

        return chunks()

    search = ScriptedSearch()
    registry = ToolRegistry()
    registry.register(search)
    events = [
        event
        async for event in completion_with_tools(
            Settings(),
            ProviderConfig(name="mock", model="mock/research", requires_auth=False),
            [{"role": "user", "content": "How many sides does a square have?"}],
            registry,
            completion_dispatch=dispatch,
        )
    ]
    assert len(dispatches) == 1
    assert search.calls == 0
    assert [e["type"] for e in events] == ["content_delta", "content_done", "done"]
    assert events[0]["content"] == "Four."
