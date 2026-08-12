"""Regression tests for authenticated video billing (issue #232).

Billing identity and tier must come from trusted request state on every video
tool invocation, including invocations chosen by the model without Studio's
optional video metadata.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from orchestrator import main as orchestrator_main
from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.models import OpenAIChatRequest, OpenAIMessage
from orchestrator.subagents.base import SubagentType
from orchestrator.tools.spawn import SpawnAgentTool, SpawnMultipleTool


def _video_metadata(**overrides):
    """Return a minimal, tier-eligible video_generation metadata block."""
    base = {
        "duration": 5,
        "source_mode": "text-to-video",
        "provider": "kling",
        "kling_model": "kling-v3-pro",
    }
    base.update(overrides)
    return {"video_generation": base}


def test_build_trusted_spawn_context_uses_authenticated_user_id():
    """When authenticated_user_id is supplied, the trust context reflects it."""
    settings = get_settings()
    authenticated = uuid.uuid4()

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        _video_metadata(),
        authenticated_user_id=authenticated,
    )

    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)


def test_build_trusted_spawn_context_binds_identity_without_video_metadata():
    """Ordinary chat requests still carry immutable video billing identity.

    The model may decide to invoke video generation without Studio's optional
    ``metadata.video_generation`` block, so authentication cannot be conditional
    on that metadata being present.
    """
    settings = get_settings()
    authenticated = uuid.uuid4()

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        None,
        authenticated_user_id=authenticated,
    )

    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)
    assert context["video"]["tier"] == settings.default_tier.lower().strip()
    assert "mode" not in context["video"]


def test_authenticated_user_id_overrides_default_when_supplied():
    """Authenticated identity strictly wins over any model-supplied identity."""
    settings = get_settings()
    authenticated = uuid.UUID("11111111-2222-3333-4444-555555555555")

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        _video_metadata(duration=7),
        authenticated_user_id=authenticated,
    )

    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)
    assert context["video"]["duration"] == 7


def test_build_trusted_spawn_context_requires_authenticated_user():
    """There is no hosted/default-account fallback for generation billing."""
    parameter = inspect.signature(orchestrator_main._build_trusted_spawn_context).parameters[
        "authenticated_user_id"
    ]

    assert parameter.default is inspect.Parameter.empty


def test_spawn_agent_overrides_untrusted_video_billing_context():
    authenticated = uuid.uuid4()
    trusted = {"video": {"user_id": str(authenticated), "tier": "pro"}}
    tool = SpawnAgentTool(trusted_spawn_context=trusted)

    context = tool._apply_trusted_context(
        SubagentType.IMAGE,
        {
            "mode": "video",
            "user_id": str(uuid.uuid4()),
            "tier": "byok",
            "duration": 5,
        },
    )

    assert context is not None
    assert context["mode"] == "video"
    assert context["user_id"] == str(authenticated)
    assert context["tier"] == "pro"


def test_spawn_multiple_overrides_untrusted_video_billing_context():
    authenticated = uuid.uuid4()
    trusted = {"video": {"user_id": str(authenticated), "tier": "starter"}}
    tool = SpawnMultipleTool(trusted_spawn_context=trusted)

    context = tool._apply_trusted_context(
        SubagentType.IMAGE,
        {"mode": "video", "user_id": str(uuid.uuid4()), "tier": "byok"},
    )

    assert context is not None
    assert context["user_id"] == str(authenticated)
    assert context["tier"] == "starter"


def test_trusted_billing_context_does_not_turn_image_request_into_video():
    trusted = {"video": {"user_id": str(uuid.uuid4()), "tier": "pro"}}
    tool = SpawnAgentTool(trusted_spawn_context=trusted)

    context = tool._apply_trusted_context(SubagentType.IMAGE, {"mode": "image"})

    assert context == {"mode": "image"}


def test_trusted_billing_context_allows_byok_without_charging_another_user():
    authenticated = uuid.uuid4()
    trusted = {"video": {"user_id": str(authenticated), "tier": "byok"}}
    tool = SpawnAgentTool(trusted_spawn_context=trusted)

    context = tool._apply_trusted_context(
        SubagentType.IMAGE,
        {"mode": "video", "user_id": str(uuid.uuid4()), "tier": "pro"},
    )

    assert context is not None
    assert context["user_id"] == str(authenticated)
    assert context["tier"] == "byok"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_openai_chat_completions_threads_authenticated_billing_context(monkeypatch, stream):
    authenticated = uuid.uuid4()
    auth = AuthenticatedDevice(
        user_id=authenticated,
        device_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )
    captured: list[dict[str, Any] | None] = []

    async def fake_stream_sse_chat(**kwargs):
        captured.append(kwargs.get("trusted_spawn_context"))
        yield 'event: token\ndata: {"data":{"delta":"ok"}}\n\n'
        yield 'event: final\ndata: {"data":{}}\n\n'

    monkeypatch.setattr(orchestrator_main, "stream_sse_chat", fake_stream_sse_chat)
    monkeypatch.setattr(orchestrator_main, "build_skill_index", AsyncMock(return_value=""))
    monkeypatch.setattr(orchestrator_main, "_enforce_chat_rate_limit", AsyncMock())

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
        model="default",
        messages=[OpenAIMessage(role="user", content="generate a video")],
        stream=stream,
    )

    response = await orchestrator_main.openai_chat_completions(
        payload,
        request,
        get_settings(),
        auth,
    )
    if stream:
        assert isinstance(response, StreamingResponse)
        async for _ in response.body_iterator:
            pass

    assert len(captured) == 1
    trusted_context = captured[0]
    assert isinstance(trusted_context, dict)
    video_context = trusted_context["video"]
    assert isinstance(video_context, dict)
    assert video_context["user_id"] == str(authenticated)
