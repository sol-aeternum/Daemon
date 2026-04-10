"""Tests for the Daemon orchestrator."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
import orchestrator.daemon as daemon_module
from orchestrator.main import app


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create an async test client."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_providers_endpoint_mock_mode(client, monkeypatch):
    """Test the providers endpoint in mock mode."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    response = await client.get("/providers")
    assert response.status_code == 200
    data = response.json()
    assert "providers" in data
    assert "default" in data
    assert data["default"] == "openrouter"
    assert "openrouter" in data["providers"]


@pytest.mark.asyncio
async def test_chat_stream_emits_done_mock_mode(client, monkeypatch):
    """Test that the chat endpoint emits the done event in mock mode."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    response = await client.post(
        "/chat",
        json={"message": "hello"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    body = response.text
    # Should contain all expected SSE events
    assert "event: token" in body
    assert "event: final" in body
    assert "event: done" in body
    # Should contain mock content (tokens emitted character by character)
    assert '"text":"("' in body or '"text":"("' in body.replace('"', '')
    assert '"text":"m"' in body  # Part of "(mock)"
    assert '"text":"o"' in body  # Part of "(mock)"


@pytest.mark.asyncio
async def test_chat_stream_emits_tool_events_via_completion_pipeline(
    client, monkeypatch
):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(*_args, **_kwargs):
        yield {"type": "thinking", "content": "Planning"}
        yield {"type": "content_delta", "content": "Starting "}
        yield {
            "type": "tool_executing",
            "name": "spawn_agent",
            "arguments": '{"agent_type":"research","prompt":"compare devices"}',
        }
        yield {
            "type": "tool_result",
            "name": "spawn_agent",
            "result": '{"session_id":"ses_123","agent_type":"research"}',
        }
        yield {"type": "content_delta", "content": "Done"}
        yield {"type": "done"}

    monkeypatch.setattr(
        daemon_module, "create_default_registry", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        daemon_module, "completion_with_tools", fake_completion_with_tools
    )

    response = await client.post(
        "/chat",
        json={"message": "spawn research subagent"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "spawn_agent" in body
    assert "event: final" in body


@pytest.mark.asyncio
async def test_chat_stream_handles_tool_pipeline_error_gracefully(client, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(*_args, **_kwargs):
        yield {
            "type": "tool_executing",
            "name": "http_request",
            "arguments": '{"url":"https://example.invalid","method":"GET"}',
        }
        yield {
            "type": "error",
            "error": "Request failed: [Errno -2] Name or service not known",
        }

    monkeypatch.setattr(
        daemon_module, "create_default_registry", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        daemon_module, "completion_with_tools", fake_completion_with_tools
    )

    response = await client.post(
        "/chat",
        json={"message": "compare devices"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "http_request" in body
    assert '"synthetic":true' in body
    assert "I hit a tool error and will continue" in body
    assert "event: final" in body
    assert "event: done" in body
    assert "event: error" not in body


@pytest.mark.asyncio
async def test_chat_stream_resolves_pending_tool_calls_before_done(client, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(*_args, **_kwargs):
        yield {
            "type": "tool_executing",
            "name": "web_search",
            "arguments": '{"query":"galaxy s26 ultra vs oneplus 15"}',
        }
        yield {"type": "done"}

    monkeypatch.setattr(
        daemon_module, "create_default_registry", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        daemon_module, "completion_with_tools", fake_completion_with_tools
    )

    response = await client.post(
        "/chat",
        json={"message": "compare devices"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "web_search" in body
    assert "Tool call did not complete before stream finished" in body


@pytest.mark.asyncio
async def test_chat_stream_provides_fallback_message_when_no_content_after_tools(client, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def fake_completion_with_tools(*_args, **_kwargs):
        yield {
            "type": "tool_executing",
            "name": "http_request",
            "arguments": '{"url":"https://example.invalid","method":"GET"}',
        }
        yield {
            "type": "tool_result",
            "name": "http_request",
            "result": json.dumps({"success": False, "error": "Connection refused"}),
        }
        yield {"type": "done"}

    monkeypatch.setattr(
        daemon_module, "create_default_registry", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        daemon_module, "completion_with_tools", fake_completion_with_tools
    )

    response = await client.post(
        "/chat",
        json={"message": "fetch data from example.invalid"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "http_request" in body
    assert "event: final" in body
    assert "event: done" in body
    assert "event: error" not in body
    # Should include fallback message since no content_delta was emitted
    assert "couldn't complete the request" in body or "I encountered issues" in body


@pytest.mark.asyncio
async def test_openai_models_endpoint_mock_mode(client, monkeypatch):
    """Test the OpenAI-compatible /v1/models endpoint."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter-uncensored")
    get_settings.cache_clear()

    response = await client.get("/v1/models")
    assert response.status_code == 200

    data = response.json()
    assert data["object"] == "list"
    assert "data" in data
    assert len(data["data"]) > 0

    # Check model structure
    model = data["data"][0]
    assert "id" in model
    assert "object" in model
    assert model["object"] == "model"
    assert "owned_by" in model


@pytest.mark.asyncio
async def test_openai_chat_completions_streaming_mock_mode(client, monkeypatch):
    """Test the OpenAI-compatible streaming chat completions endpoint."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "openrouter-uncensored",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    body = response.text
    # Should contain SSE data lines
    assert "data:" in body
    # Should end with [DONE]
    assert "[DONE]" in body
    # Should have chat.completion.chunk objects
    assert "chat.completion.chunk" in body


@pytest.mark.asyncio
async def test_openai_chat_completions_non_streaming_mock_mode(client, monkeypatch):
    """Test the OpenAI-compatible non-streaming chat completions endpoint."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "openrouter-uncensored",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["object"] == "chat.completion"
    assert "id" in data
    assert "choices" in data
    assert len(data["choices"]) > 0

    choice = data["choices"][0]
    assert "message" in choice
    assert choice["message"]["role"] == "assistant"
    assert "content" in choice["message"]
    assert "finish_reason" in choice

    # Should contain mock content
    assert "(mock)" in choice["message"]["content"]


@pytest.mark.asyncio
async def test_chat_with_provider_selection_mock_mode(client, monkeypatch):
    """Test that provider can be selected per-request."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    # Request with explicit provider
    response = await client.post(
        "/chat",
        json={"message": "test", "provider": "openrouter"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "openrouter" in body  # Should show provider in response


@pytest.mark.asyncio
async def test_chat_per_message_model_override_bypasses_auto_routing(
    client, monkeypatch
):
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    explicit_model = "openrouter/test/explicit-model"

    response = await client.post(
        "/chat",
        json={
            "message": "ignored",
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": "debug architecture deep dive",
                    "model": explicit_model,
                }
            ],
        },
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200

    routing_events = []
    for frame in response.text.split("\n\n"):
        if "event: routing" not in frame:
            continue

        event_type = None
        data_text = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_text += line[5:].strip()

        if event_type == "routing" and data_text:
            routing_events.append(json.loads(data_text))

    assert routing_events
    routing = routing_events[0]["data"]
    assert routing["model"] == explicit_model
    assert routing["tier"] == "explicit"
    assert routing["reason"] == f"user_selected:{explicit_model}"


@pytest.mark.asyncio
async def test_api_key_authentication(client, monkeypatch):
    """Test that API key authentication works when configured."""
    monkeypatch.setenv("DAEMON_API_KEY", "test-secret-key")
    get_settings.cache_clear()

    # Request without key should fail
    response = await client.get("/health")
    assert response.status_code == 200  # Health is public

    response = await client.get("/providers")
    assert response.status_code == 401

    # Request with wrong key should fail
    response = await client.get(
        "/providers", headers={"Authorization": "Bearer wrong-key"}
    )
    assert response.status_code == 401

    # Request with correct key should succeed
    response = await client.get(
        "/providers", headers={"Authorization": "Bearer test-secret-key"}
    )
    assert response.status_code == 200
