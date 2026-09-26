"""Authenticated account scope and disabled vendor generation cannot be overridden."""

from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from orchestrator import main as orchestrator_main
from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.models import OpenAIChatRequest, OpenAIMessage
from orchestrator.tools.spawn import SpawnAgentTool, SpawnMultipleTool
from tests.qualified_compute import install_qualified_compute


def test_spawn_context_binds_authenticated_user_without_metadata() -> None:
    authenticated = uuid.uuid4()
    context = orchestrator_main._build_trusted_spawn_context(authenticated, None)
    assert context is not None
    assert context == {"video": {"user_id": str(authenticated)}}
    assert "tier" not in context["video"]


def test_spawn_context_cannot_take_identity_or_tier_from_video_metadata() -> None:
    authenticated = uuid.uuid4()
    forged = uuid.uuid4()
    context = orchestrator_main._build_trusted_spawn_context(
        authenticated,
        {"video_generation": {"user_id": str(forged), "tier": "byok", "duration": 7}},
    )
    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)
    assert context["video"]["duration"] == 7
    assert "tier" not in context["video"]


def test_spawn_context_requires_authenticated_identity() -> None:
    parameter = inspect.signature(orchestrator_main._build_trusted_spawn_context).parameters[
        "user_id"
    ]
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", [SpawnAgentTool, SpawnMultipleTool])
@pytest.mark.parametrize("mode", ["video", "image"])
@pytest.mark.parametrize("tier", ["pro", "byok"])
async def test_spawn_tools_deny_forged_vendor_billing_context(tool_type, mode, tier) -> None:
    owner = uuid.uuid4()
    forged = uuid.uuid4()
    tool = tool_type(
        user_id=owner,
        trusted_spawn_context={"video": {"user_id": str(owner)}},
    )
    forged_context = {"mode": mode, "user_id": str(forged), "tier": tier}
    if tool_type is SpawnAgentTool:
        result = await tool.execute(agent_type="image", task="generate", context=forged_context)
    else:
        result = await tool.execute(
            agents=[{"agent_type": "image", "task": "generate", "context": forged_context}]
        )
    assert json.loads(result)["code"] == "capacity_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_openai_chat_completions_threads_authenticated_account_context(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    authenticated = uuid.uuid4()
    auth = AuthenticatedDevice(
        user_id=authenticated,
        device_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )
    install_qualified_compute(monkeypatch)
    captured: list[dict[str, Any]] = []

    async def fake_account_chat_frames(pool, owner, **kwargs):
        assert pool is not None
        assert owner == authenticated
        captured.append(kwargs)
        yield 'event: token\ndata: {"data":{"delta":"ok"}}\n\n'
        yield 'event: final\ndata: {"data":{}}\n\n'

    monkeypatch.setattr(orchestrator_main, "_account_chat_frames", fake_account_chat_frames)
    monkeypatch.setattr(orchestrator_main, "build_skill_index", AsyncMock(return_value=""))
    monkeypatch.setattr(orchestrator_main, "_enforce_chat_rate_limit", AsyncMock())
    monkeypatch.setattr(
        orchestrator_main.app.state,
        "app_state",
        SimpleNamespace(db_pool=object(), memory_store=None),
        raising=False,
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "app": orchestrator_main.app,
            "state": {},
        }
    )
    payload = OpenAIChatRequest(
        model="openrouter/google/gemini-2.5-flash",
        messages=[OpenAIMessage(role="user", content="generate a video")],
        stream=stream,
    )
    response = await orchestrator_main.openai_chat_completions(
        payload, request, get_settings(), auth
    )
    if stream:
        assert isinstance(response, StreamingResponse)
        async for _ in response.body_iterator:
            pass

    assert len(captured) == 1
    assert captured[0]["user_id"] == authenticated
    assert captured[0]["trusted_spawn_context"] == {"video": {"user_id": str(authenticated)}}
