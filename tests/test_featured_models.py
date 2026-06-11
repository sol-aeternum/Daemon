"""Integration tests for featured models in the catalog."""

import uuid

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import AsyncClient, ASGITransport

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings

# Featured models from orchestrator/catalog.py
FEATURED_MODELS = [
    "openrouter/moonshotai/kimi-k2.5",
    "openrouter/anthropic/claude-opus-4.6",
    "openrouter/anthropic/claude-sonnet-4.6",
    "openrouter/google/gemini-2.5-pro",
    "openrouter/google/gemini-3.1-pro-preview",
    "openrouter/openai/gpt-5.2",
    "openrouter/meta-llama/llama-4-scout",
]


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Create async test client."""
    from orchestrator.main import app

    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    async def override_settings():
        return get_settings()

    async def override_auth(_request: Request):
        return AuthenticatedDevice(
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        )

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[require_device_auth] = override_auth
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FEATURED_MODELS)
async def test_featured_model_chat(client: AsyncClient, model: str):
    """Test that each featured model can be used in chat completions."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10,
        },
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200, f"Model {model} failed: {response.text}"
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_featured():
    """Test that /v1/catalog returns featured models."""
    from orchestrator.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/v1/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "featured" in data
    assert len(data["featured"]) == 7
