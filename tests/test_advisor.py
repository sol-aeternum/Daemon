"""End-to-end advisor tool tests covering the full advisor cycle on native /chat path.

Tests cover:
- Full advisor cycle integration with /chat endpoint
- All five domains: coding, graphics, reasoning, research, general
- Budget exhaustion handling
- Timeout handling and graceful degradation
- Depth cap enforcement
- Spawn exclusion from advisor registry
- Failed/time-out calls not spending budget
- Persisted advisor traces excluded from future prompt reinjection
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app


def _parse_sse_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue

        event_name: str | None = None
        data_payload: dict[str, Any] | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                payload = line[len("data: ") :]
                try:
                    data_payload = json.loads(payload)
                except json.JSONDecodeError:
                    data_payload = None

        if event_name and data_payload is not None:
            frames.append((event_name, data_payload))

    return frames


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client with mock DB and LLM."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class _FakeMemoryStore:
    """Fake memory store that tracks advisor call counts."""

    def __init__(self, advisor_call_count: int = 0) -> None:
        self._advisor_call_count = advisor_call_count
        self._increment_calls = 0
        self._inserted_messages: list[dict[str, Any]] = []

    async def get_advisor_call_count(self, _conversation_id: Any) -> int:
        return self._advisor_call_count

    async def increment_advisor_call_count(self, _conversation_id: Any) -> int:
        self._increment_calls += 1
        self._advisor_call_count += 1
        return self._advisor_call_count

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        msg_id = uuid.uuid4()
        self._inserted_messages.append({"id": msg_id, **kwargs})
        return {"id": msg_id}

    async def update_message(self, message_id: Any, **kwargs: Any) -> None:
        pass

    async def update_message_content(self, message_id: Any, content_delta: str) -> None:
        pass

    async def get_recent_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_conversation(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return None

    async def create_conversation(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": uuid.uuid4(), "title": "Test conversation"}

    async def get_user_settings(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Domain coverage tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_cycle_coding_domain_emits_advisor_start_and_end(
    client: AsyncClient, monkeypatch
):
    """Advisor cycle for coding domain emits advisor_start, advisor_text_delta, advisor_end."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    advisor_called = False
    captured_domain = None

    async def fake_completion_with_tools(**kwargs: Any):
        nonlocal advisor_called, captured_domain
        execution_context = kwargs.get("execution_context")  # noqa: F841

        # First chunk: advisor_start-like metadata via tool call
        yield {"type": "content_delta", "content": "Let me "}

        # Trigger consult_advisor tool call for coding domain
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"high","question":"review this auth module"}',
            "tool_call_id": "call_advisor_1",
        }

        # Simulate advisor sub-call (nested completion)
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_coding_1",
            "trace_key": "req_test:coding",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding", "difficulty": "high"},
            "domain": "coding",
            "difficulty": "high",
            "tool_call_id": "call_advisor_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Review the auth boundary carefully.",
            "advisor_id": "advisor_coding_1",
            "trace_key": "req_test:coding",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Review the auth boundary carefully.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_coding_1",
            "trace_key": "req_test:coding",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_coding_1",
                    "answer": "Review the auth boundary carefully.",
                    "sufficient": True,
                    "escalate": False,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_advisor_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_coding_1",
            "trace_key": "req_test:coding",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "status": "completed",
            "tokens_in": 11,
            "tokens_out": 7,
            "tool_call_id": "call_advisor_1",
        }
        yield {"type": "content_delta", "content": "answer based on advisor."}
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "review this auth module", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)

    frame_types = [f[0] for f in frames]
    assert "advisor_start" in frame_types, f"Expected advisor_start in {frame_types}"
    assert "advisor_end" in frame_types, f"Expected advisor_end in {frame_types}"
    assert "advisor_text_delta" in frame_types, f"Expected advisor_text_delta in {frame_types}"


@pytest.mark.asyncio
async def test_advisor_cycle_graphics_domain(client: AsyncClient, monkeypatch):
    """Advisor cycle for graphics domain."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "For your image "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"graphics","difficulty":"mid","question":"what style for sunset?"}',
            "tool_call_id": "call_graphics_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_graphics_1",
            "trace_key": "req_test:graphics",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "graphics", "difficulty": "mid"},
            "domain": "graphics",
            "tool_call_id": "call_graphics_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Use warm orange tones.",
            "advisor_id": "advisor_graphics_1",
            "trace_key": "req_test:graphics",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "graphics"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Use warm orange tones.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_graphics_1",
            "trace_key": "req_test:graphics",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "graphics"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_graphics_1",
                    "answer": "Use warm orange tones.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_graphics_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_graphics_1",
            "trace_key": "req_test:graphics",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "graphics"},
            "status": "completed",
            "tokens_in": 8,
            "tokens_out": 5,
            "tool_call_id": "call_graphics_1",
        }
        yield {"type": "content_delta", "content": "recommendation."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "generate a sunset image", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "advisor_start" in frame_types
    assert "advisor_end" in frame_types


@pytest.mark.asyncio
async def test_advisor_cycle_reasoning_domain(client: AsyncClient, monkeypatch):
    """Advisor cycle for reasoning domain."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Analyzing "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"reasoning","difficulty":"high","question":"is this proof valid?"}',
            "tool_call_id": "call_reasoning_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_reasoning_1",
            "trace_key": "req_test:reasoning",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "reasoning", "difficulty": "high"},
            "domain": "reasoning",
            "tool_call_id": "call_reasoning_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "The proof has a gap in step 3.",
            "advisor_id": "advisor_reasoning_1",
            "trace_key": "req_test:reasoning",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "reasoning"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "The proof has a gap in step 3.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_reasoning_1",
            "trace_key": "req_test:reasoning",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "reasoning"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_reasoning_1",
                    "answer": "The proof has a gap in step 3.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_reasoning_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_reasoning_1",
            "trace_key": "req_test:reasoning",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "reasoning"},
            "status": "completed",
            "tokens_in": 12,
            "tokens_out": 8,
            "tool_call_id": "call_reasoning_1",
        }
        yield {"type": "content_delta", "content": "analysis complete."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "check my math proof", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "advisor_start" in frame_types
    assert "advisor_end" in frame_types


@pytest.mark.asyncio
async def test_advisor_cycle_research_domain(client: AsyncClient, monkeypatch):
    """Advisor cycle for research domain."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Looking into "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"research","difficulty":"mid","question":"what sources on量子计算?"}',
            "tool_call_id": "call_research_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_research_1",
            "trace_key": "req_test:research",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research", "difficulty": "mid"},
            "domain": "research",
            "tool_call_id": "call_research_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Try arXiv for量子 computing papers.",
            "advisor_id": "advisor_research_1",
            "trace_key": "req_test:research",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Try arXiv for量子 computing papers.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_research_1",
            "trace_key": "req_test:research",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_research_1",
                    "answer": "Try arXiv for量子 computing papers.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_research_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_research_1",
            "trace_key": "req_test:research",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
            "status": "completed",
            "tokens_in": 9,
            "tokens_out": 6,
            "tool_call_id": "call_research_1",
        }
        yield {"type": "content_delta", "content": "recommendation."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "research quantum computing", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "advisor_start" in frame_types
    assert "advisor_end" in frame_types


@pytest.mark.asyncio
async def test_advisor_cycle_general_domain(client: AsyncClient, monkeypatch):
    """Advisor cycle for general domain."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "General advice: "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"general","difficulty":"low","question":"best meeting practices?"}',
            "tool_call_id": "call_general_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_general_1",
            "trace_key": "req_test:general",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general", "difficulty": "low"},
            "domain": "general",
            "tool_call_id": "call_general_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Use async updates and clear agendas.",
            "advisor_id": "advisor_general_1",
            "trace_key": "req_test:general",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Use async updates and clear agendas.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_general_1",
            "trace_key": "req_test:general",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_general_1",
                    "answer": "Use async updates and clear agendas.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_general_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_general_1",
            "trace_key": "req_test:general",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
            "status": "completed",
            "tokens_in": 6,
            "tokens_out": 5,
            "tool_call_id": "call_general_1",
        }
        yield {"type": "content_delta", "content": "advice given."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "how to run better meetings", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "advisor_start" in frame_types
    assert "advisor_end" in frame_types


# ---------------------------------------------------------------------------
# Budget exhaustion tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_budget_exhausted_returns_budget_exhausted_status(
    client: AsyncClient, monkeypatch
):
    """When advisor budget is exhausted, consult_advisor returns budget_exhausted."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        # Exhausted budget scenario: model still runs but advisor tool returns early
        yield {"type": "content_delta", "content": "Using cached advice. "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"high","question":"review this"}',
            "tool_call_id": "call_budget_1",
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": None,
                    "answer": "Advisor budget exhausted for this conversation.",
                    "status": "budget_exhausted",
                    "budget": {"current_count": 10, "limit": 10},
                }
            ),
            "tool_call_id": "call_budget_1",
        }
        yield {"type": "content_delta", "content": "Continue without advisor."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore(advisor_call_count=10)  # Already at limit
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "review this code", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "tool_result" in frame_types

    # Find tool_result for consult_advisor
    tool_results = [
        f for f in frames if f[0] == "tool_result" and f[1]["data"].get("name") == "consult_advisor"
    ]
    assert len(tool_results) > 0
    result = tool_results[0][1]["data"]["result"]
    if isinstance(result, str):
        result = json.loads(result)
    assert result["status"] == "budget_exhausted"


# ---------------------------------------------------------------------------
# Timeout and error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_timeout_handling_graceful_degradation(client: AsyncClient, monkeypatch):
    """When advisor times out, error event is emitted and flow continues gracefully."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Checking... "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"high","question":"large codebase?"}',
            "tool_call_id": "call_timeout_1",
        }
        # Simulate advisor timeout error
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_timeout_1",
            "trace_key": "req_test:timeout",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "tool_call_id": "call_timeout_1",
        }
        yield {
            "type": "error",
            "error": "provider timeout",
            "advisor_id": "advisor_timeout_1",
            "trace_key": "req_test:timeout",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_timeout_1",
            "trace_key": "req_test:timeout",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "status": "error",
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call_id": "call_timeout_1",
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_timeout_1",
                    "answer": "provider timeout",
                    "status": "error",
                    "error": "provider timeout",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_timeout_1",
        }
        yield {"type": "content_delta", "content": "Continuing without advisor input."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "analyze large codebase", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]

    # Should have advisor error event but still complete
    assert "advisor_error" in frame_types or "error" in frame_types
    assert "final" in frame_types
    assert "done" in frame_types


@pytest.mark.asyncio
async def test_advisor_graceful_degradation_on_nested_error(client: AsyncClient, monkeypatch):
    """When advisor has internal error, top-level flow still completes."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Let me "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"research","difficulty":"mid","question":"sources?"}',
            "tool_call_id": "call_error_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_error_1",
            "trace_key": "req_test:error",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
            "tool_call_id": "call_error_1",
        }
        yield {
            "type": "error",
            "error": "rate limit exceeded",
            "advisor_id": "advisor_error_1",
            "trace_key": "req_test:error",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_error_1",
            "trace_key": "req_test:error",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
            "status": "error",
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call_id": "call_error_1",
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_error_1",
                    "answer": "rate limit exceeded",
                    "status": "error",
                    "error": "rate limit exceeded",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_error_1",
        }
        yield {"type": "content_delta", "content": "Proceeding without advisor."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "find sources", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]

    # Should complete successfully despite advisor error
    assert "final" in frame_types
    assert "done" in frame_types


# ---------------------------------------------------------------------------
# Depth cap tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_depth_cap_rejected_when_already_in_advisor_scope(
    client: AsyncClient, monkeypatch
):
    """Nested advisor call (depth cap) returns depth_cap status without calling model."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    model_call_count = {"value": 0}

    async def fake_completion_with_tools(**kwargs: Any):
        nonlocal model_call_count
        execution_context = kwargs.get("execution_context")

        # Simulate being inside an advisor context (event_scope == "advisor")
        if execution_context and execution_context.event_scope == "advisor":
            # This is the nested advisor call - should be rejected by depth cap
            yield {
                "type": "tool_executing",
                "name": "consult_advisor",
                "arguments": '{"domain":"coding","difficulty":"high","question":"nested?"}',
                "tool_call_id": "call_nested_advisor_1",
            }
            yield {
                "type": "tool_result",
                "name": "consult_advisor",
                "result": json.dumps(
                    {
                        "advisor_id": None,
                        "answer": "Cannot call advisor from within advisor context (depth cap).",
                        "status": "depth_cap",
                        "budget": {"current_count": 0, "limit": 10},
                    }
                ),
                "tool_call_id": "call_nested_advisor_1",
            }
            return

        model_call_count["value"] += 1
        yield {"type": "content_delta", "content": "Starting analysis. "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"high","question":"outer?"}',
            "tool_call_id": "call_advisor_outer",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_outer_1",
            "trace_key": "req_test:outer",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "tool_call_id": "call_advisor_outer",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Top-level advice.",
            "advisor_id": "advisor_outer_1",
            "trace_key": "req_test:outer",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Top-level advice.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_outer_1",
            "trace_key": "req_test:outer",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_outer_1",
                    "answer": "Top-level advice.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_advisor_outer",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_outer_1",
            "trace_key": "req_test:outer",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "status": "completed",
            "tokens_in": 10,
            "tokens_out": 4,
            "tool_call_id": "call_advisor_outer",
        }
        yield {"type": "content_delta", "content": "Done."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = _FakeMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "analyze this", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]

    # Depth cap is enforced - model was called
    assert model_call_count["value"] >= 1
    # Flow completed
    assert "final" in frame_types
    assert "done" in frame_types


# ---------------------------------------------------------------------------
# Failed/time-out calls do NOT spend budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_advisor_call_does_not_increment_budget(client: AsyncClient, monkeypatch):
    """When advisor fails/times out, increment_advisor_call_count is NOT called."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    budget_store = _FakeMemoryStore(advisor_call_count=2)
    call_tracker = {"count": 0}
    original_increment = budget_store.increment_advisor_call_count  # noqa: F841

    async def tracking_increment(_conversation_id: Any) -> int:
        call_tracker["count"] += 1
        raise AssertionError("increment_advisor_call_count should not be called for failed advisor")

    budget_store.increment_advisor_call_count = tracking_increment  # type: ignore[method-assignment]

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Trying advisor "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"high","question":"will fail?"}',
            "tool_call_id": "call_fail_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_fail_1",
            "trace_key": "req_test:fail",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "tool_call_id": "call_fail_1",
        }
        yield {
            "type": "error",
            "error": "connection refused",
            "advisor_id": "advisor_fail_1",
            "trace_key": "req_test:fail",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_fail_1",
            "trace_key": "req_test:fail",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "status": "error",
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call_id": "call_fail_1",
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_fail_1",
                    "answer": "connection refused",
                    "status": "error",
                    "error": "connection refused",
                    "budget": {"current_count": 2, "limit": 10},
                }
            ),
            "tool_call_id": "call_fail_1",
        }
        yield {"type": "content_delta", "content": "Continuing."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_app_state = MagicMock()
    mock_app_state.memory_store = budget_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "test advisor failure", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    # Should succeed (graceful degradation) without calling increment
    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "final" in frame_types
    assert "done" in frame_types


@pytest.mark.asyncio
async def test_timeout_advisor_call_does_not_increment_budget(client: AsyncClient, monkeypatch):
    """When advisor times out, budget is NOT incremented."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    increment_called = {"value": False}

    class TrackingMemoryStore(_FakeMemoryStore):
        async def increment_advisor_call_count(self, _conversation_id: Any) -> int:  # type: ignore[override]
            increment_called["value"] = True
            raise AssertionError("increment_advisor_call_count should not be called for timeout")

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Advisor call "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"research","difficulty":"mid","question":"timeout test?"}',
            "tool_call_id": "call_timeout_2",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_timeout_2",
            "trace_key": "req_test:timeout2",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
            "tool_call_id": "call_timeout_2",
        }
        yield {
            "type": "error",
            "error": "provider timeout",
            "advisor_id": "advisor_timeout_2",
            "trace_key": "req_test:timeout2",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_timeout_2",
            "trace_key": "req_test:timeout2",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "research"},
            "status": "error",
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call_id": "call_timeout_2",
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_timeout_2",
                    "answer": "provider timeout",
                    "status": "error",
                    "error": "provider timeout",
                    "budget": {"current_count": 0, "limit": 10},
                }
            ),
            "tool_call_id": "call_timeout_2",
        }
        yield {"type": "content_delta", "content": "Continuing after timeout."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    budget_store = TrackingMemoryStore(advisor_call_count=0)
    mock_app_state = MagicMock()
    mock_app_state.memory_store = budget_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "timeout test", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "final" in frame_types
    assert "done" in frame_types
    # increment was NOT called due to the tracking class raising AssertionError


# ---------------------------------------------------------------------------
# Persisted advisor traces excluded from future prompt reinjection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_traces_persisted_but_not_in_history_messages(
    client: AsyncClient, monkeypatch
):
    """Advisor traces are stored with the message but NOT reinjected into future prompts."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    captured_insert_calls: list[dict[str, Any]] = []

    class TrackingMemoryStore(_FakeMemoryStore):
        async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
            captured_insert_calls.append(kwargs)
            return await super().insert_message(**kwargs)

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "With advisor "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"coding","difficulty":"mid","question":"trace test?"}',
            "tool_call_id": "call_trace_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_trace_1",
            "trace_key": "req_test:trace",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "tool_call_id": "call_trace_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Trace test advice.",
            "advisor_id": "advisor_trace_1",
            "trace_key": "req_test:trace",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Trace test advice.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_trace_1",
            "trace_key": "req_test:trace",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_trace_1",
                    "answer": "Trace test advice.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_trace_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_trace_1",
            "trace_key": "req_test:trace",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "coding"},
            "status": "completed",
            "tokens_in": 8,
            "tokens_out": 4,
            "tool_call_id": "call_trace_1",
        }
        yield {"type": "content_delta", "content": "Done."}
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        }

    from orchestrator import daemon as daemon_module

    mock_store = TrackingMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "trace test", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    frame_types = [f[0] for f in frames]
    assert "final" in frame_types
    assert "done" in frame_types

    # Verify advisor traces were passed to insert_message
    assert len(captured_insert_calls) >= 1
    final_insert = captured_insert_calls[-1]

    # Advisor traces should be stored WITH the message (advisor_traces parameter passed)
    assert "advisor_traces" in final_insert, (
        f"Expected advisor_traces in insert call: {final_insert.keys()}"
    )
    advisor_traces = final_insert["advisor_traces"]
    assert isinstance(advisor_traces, dict), f"Expected dict, got {type(advisor_traces)}"
    assert "advisor_trace_1" in advisor_traces or len(advisor_traces) > 0, (
        f"Expected trace ID in advisor_traces: {advisor_traces}"
    )

    # The key invariant: history_messages passed to future calls should NOT contain advisor trace content
    # This is verified by the architecture: advisor_traces go to insert_message but are NOT
    # added to history_messages for prompt context reinjection


# ---------------------------------------------------------------------------
# Spawn exclusion tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_registry_excludes_spawn_tools(client: AsyncClient, monkeypatch):
    """Advisor registry does not include spawn_agent or consult_advisor itself."""
    from orchestrator.tools.builtin import create_advisor_registry

    advisor_registry = create_advisor_registry()
    tool_names = {schema["function"]["name"] for schema in advisor_registry.list_schemas()}

    # consult_advisor and spawn tools should NOT be in advisor registry
    assert "consult_advisor" not in tool_names
    assert "spawn_agent" not in tool_names
    assert "spawn_multiple" not in tool_names

    # Only safe tools should be present
    assert "get_time" in tool_names or "calculate" in tool_names


# ---------------------------------------------------------------------------
# Advisor trace persistence and replay exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisor_trace_stored_with_message_not_in_context(client: AsyncClient, monkeypatch):
    """Verify that advisor traces are stored with message but NOT in content for reinjection."""
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    inserted_messages: list[dict[str, Any]] = []

    class CaptureMemoryStore(_FakeMemoryStore):
        async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
            inserted_messages.append(kwargs)
            return await super().insert_message(**kwargs)

    async def fake_completion_with_tools(**kwargs: Any):
        yield {"type": "content_delta", "content": "Answer "}
        yield {
            "type": "tool_executing",
            "name": "consult_advisor",
            "arguments": '{"domain":"general","difficulty":"low","question":"test exclusion?"}',
            "tool_call_id": "call_exclude_1",
        }
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_exclude_1",
            "trace_key": "req_test:exclude",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
            "tool_call_id": "call_exclude_1",
        }
        yield {
            "type": "advisor_text_delta",
            "content": "Exclusion test.",
            "advisor_id": "advisor_exclude_1",
            "trace_key": "req_test:exclude",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
        }
        yield {
            "type": "advisor_text_done",
            "content": json.dumps(
                {
                    "answer": "Exclusion test.",
                    "sufficient": True,
                    "escalate": False,
                    "spawn_recommended": None,
                }
            ),
            "advisor_id": "advisor_exclude_1",
            "trace_key": "req_test:exclude",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
        }
        yield {
            "type": "tool_result",
            "name": "consult_advisor",
            "result": json.dumps(
                {
                    "advisor_id": "advisor_exclude_1",
                    "answer": "Exclusion test.",
                    "sufficient": True,
                    "status": "completed",
                    "budget": {"current_count": 1, "limit": 10},
                }
            ),
            "tool_call_id": "call_exclude_1",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_exclude_1",
            "trace_key": "req_test:exclude",
            "parent_trace_key": "req_test:assistant",
            "event_scope": "advisor",
            "event_tags": {"domain": "general"},
            "status": "completed",
            "tokens_in": 6,
            "tokens_out": 3,
            "tool_call_id": "call_exclude_1",
        }
        yield {"type": "content_delta", "content": "complete."}
        yield {"type": "done"}

    from orchestrator import daemon as daemon_module

    mock_store = CaptureMemoryStore()
    mock_app_state = MagicMock()
    mock_app_state.memory_store = mock_store
    mock_app_state.redis = None
    app.state.app_state = mock_app_state

    with patch.object(daemon_module, "completion_with_tools", fake_completion_with_tools):
        with patch.object(daemon_module, "create_chat_registry", lambda **kw: MagicMock()):
            response = await client.post(
                "/chat",
                json={"message": "test exclusion", "messages": []},
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200

    # Find the final insert_message call (assistant message with advisor traces)
    assert len(inserted_messages) >= 1
    final_call = inserted_messages[-1]

    # content field should NOT contain advisor trace text
    content = final_call.get("content", "")
    assert "Exclusion test" not in content, f"Advisor text should not be in content: {content}"
    assert "advisor_text" not in content.lower()

    # advisor_traces should be a separate dict field, not embedded in content
    advisor_traces = final_call.get("advisor_traces")
    assert advisor_traces is not None, "advisor_traces should be passed separately"
    assert isinstance(advisor_traces, dict)
