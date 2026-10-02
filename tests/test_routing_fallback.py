"""Disclosed fallback from inferred reasoning to the routine profile (optional work O1)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing, routing_log
from orchestrator.config import get_settings
from orchestrator.entitlements import EntitlementService
from orchestrator.tools import completion as completion_module
from orchestrator.tools.completion import completion_with_tools
from orchestrator.tools.registry import Tool, ToolRegistry

PROVIDER = SimpleNamespace(
    name="openrouter",
    model="openrouter/openai/gpt-6-luna",
    timeout_s=30,
    base_url=None,
    api_key="test-key",
    requires_auth=False,
    extra_headers=None,
)


@pytest.fixture
def records() -> Iterator[list[dict[str, Any]]]:
    collected: list[dict[str, Any]] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if message.startswith(routing_log.PREFIX + " "):
                collected.append(json.loads(message[len(routing_log.PREFIX) + 1 :]))

    handler = Handler()
    logger = logging.getLogger(routing_log.LOGGER_NAME)
    logger.addHandler(handler)
    try:
        yield collected
    finally:
        logger.removeHandler(handler)


def _scope(*, auto_route: bool = True, operation: str = "chat") -> runtime.ComputeScope:
    limits = SimpleNamespace(max_context_tokens=32_000, max_output_tokens=4_096)
    policy = SimpleNamespace(
        capabilities={"chat"},
        limits=limits,
        limits_for=lambda _premium: limits,
        remaining_for=lambda _premium: 1_000_000,
    )
    service = SimpleNamespace(
        resolve=AsyncMock(return_value=policy), reserve=AsyncMock(), settle=AsyncMock()
    )
    return runtime.ComputeScope(
        uuid.uuid4(),
        cast(EntitlementService, service),
        operation=operation,
        auto_route=auto_route,
    )


async def _stream() -> AsyncIterator[dict[str, Any]]:
    yield {"choices": [{"delta": {"content": "answer"}}]}


def _dispatcher(refusals: dict[str, str]) -> tuple[list[str], Any]:
    """Refuse with ``refusals[profile]`` while that profile is active; otherwise stream."""
    profiles: list[str] = []

    async def dispatch(**_params: Any) -> Any:
        profile = model_routing.current_routing().profile
        profiles.append(profile)
        if profile in refusals:
            raise runtime.ComputeUnavailable(refusals[profile], "refused")
        return _stream()

    return profiles, dispatch


async def _run(
    dispatch: Any,
    *,
    profile: str = "reasoning",
    scope: runtime.ComputeScope | None = None,
    reasoning_fallback: bool = True,
) -> list[dict[str, Any]]:
    token = runtime._scope.set(scope or _scope())  # pyright: ignore[reportPrivateUsage]
    try:
        with model_routing.routing_context(profile):
            return [
                event
                async for event in completion_with_tools(
                    settings=get_settings(),
                    provider_config=PROVIDER,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": "compare these"}],
                    registry=ToolRegistry(),
                    completion_dispatch=dispatch,
                    reasoning_fallback=reasoning_fallback,
                )
            ]
    finally:
        runtime._scope.reset(token)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["capability_unavailable", "budget_exceeded"])
async def test_inferred_reasoning_refusal_is_answered_on_routine_with_disclosure(
    cause: str, records: list[dict[str, Any]]
) -> None:
    profiles, dispatch = _dispatcher({"reasoning": cause})
    events = await _run(dispatch)
    assert profiles == ["reasoning", "routine"]
    fallback = next(event for event in events if event["type"] == "routing_fallback")
    assert fallback["cause"] == cause
    assert fallback["from_profile"] == "reasoning"
    assert fallback["to_profile"] == "routine"
    assert any(event.get("content") == "answer" for event in events)
    (record,) = [r for r in records if r["event"] == "profile_fallback"]
    assert record["cause"] == cause


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "kwargs", "refusal"),
    [
        ("not opted in", {"reasoning_fallback": False}, "capability_unavailable"),
        ("explicit selection", {"scope": "explicit"}, "capability_unavailable"),
        ("background operation", {"scope": "background"}, "capability_unavailable"),
        ("routine profile", {"profile": "routine"}, "capability_unavailable"),
        ("provider outage", {}, "capacity_unavailable"),
        ("context too large", {}, "context_limit"),
    ],
)
async def test_other_refusals_stand(label: str, kwargs: dict[str, Any], refusal: str) -> None:
    profile = kwargs.pop("profile", "reasoning")
    scope_kind = kwargs.pop("scope", None)
    scope = (
        _scope(auto_route=False)
        if scope_kind == "explicit"
        else _scope(operation="background_job")
        if scope_kind == "background"
        else None
    )
    profiles, dispatch = _dispatcher({profile: refusal})
    with pytest.raises(runtime.ComputeUnavailable) as raised:
        await _run(dispatch, profile=profile, scope=scope, **kwargs)
    assert raised.value.code == refusal, label
    assert profiles == [profile], label


@pytest.mark.asyncio
async def test_a_refused_routine_fallback_is_reported_once_without_looping() -> None:
    profiles, dispatch = _dispatcher(
        {"reasoning": "capability_unavailable", "routine": "budget_exceeded"}
    )
    with pytest.raises(runtime.ComputeUnavailable) as raised:
        await _run(dispatch)
    assert raised.value.code == "budget_exceeded"
    assert profiles == ["reasoning", "routine"]


class _EchoTool(Tool):
    name = "echo"
    description = "Echo"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        return "echoed"


@pytest.mark.asyncio
async def test_fallback_sticks_for_later_tool_rounds_in_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After falling back, every later completion in the turn (including synthesis) runs
    on routine."""
    profiles: list[str] = []

    async def tool_round() -> AsyncIterator[dict[str, Any]]:
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "echo", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        }

    async def dispatch(**_params: Any) -> Any:
        profile = model_routing.current_routing().profile
        profiles.append(profile)
        if profile == "reasoning":
            raise runtime.ComputeUnavailable("capability_unavailable", "refused")
        return tool_round() if len(profiles) == 2 else _stream()

    # The final synthesis call dispatches through guarded_completion directly.
    monkeypatch.setattr(completion_module, "guarded_completion", dispatch)
    registry = ToolRegistry()
    registry.register(_EchoTool())
    token = runtime._scope.set(_scope())  # pyright: ignore[reportPrivateUsage]
    try:
        with model_routing.routing_context("reasoning"):
            events = [
                event
                async for event in completion_with_tools(
                    settings=get_settings(),
                    provider_config=PROVIDER,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": "compare these"}],
                    registry=registry,
                    completion_dispatch=dispatch,
                    reasoning_fallback=True,
                )
            ]
    finally:
        runtime._scope.reset(token)  # pyright: ignore[reportPrivateUsage]
    assert profiles == ["reasoning", "routine", "routine"]
    assert sum(1 for event in events if event["type"] == "routing_fallback") == 1
