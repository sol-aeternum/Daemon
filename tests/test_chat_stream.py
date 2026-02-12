"""Tests for the Daemon orchestrator."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.main import app


@pytest.fixture
async def client():
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_providers_endpoint_mock_mode(client, monkeypatch):
    """Test the providers endpoint in mock mode."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")

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
    # Should contain mock content
    assert "(mock)" in body
    assert "hello" in body
    assert "world" in body


@pytest.mark.asyncio
async def test_openai_models_endpoint_mock_mode(client, monkeypatch):
    """Test the OpenAI-compatible /v1/models endpoint."""
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter-uncensored")

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
async def test_api_key_authentication(client, monkeypatch):
    """Test that API key authentication works when configured."""
    monkeypatch.setenv("DAEMON_API_KEY", "test-secret-key")

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
