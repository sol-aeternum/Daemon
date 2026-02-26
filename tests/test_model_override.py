from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from orchestrator.config import get_settings
from orchestrator.main import app


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class _SSEEnvelope(BaseModel):
    type: str
    data: dict[str, object]


def _extract_sse_event_envelopes(
    response_text: str, event_name: str
) -> list[_SSEEnvelope]:
    envelopes: list[_SSEEnvelope] = []

    for frame in response_text.split("\n\n"):
        if f"event: {event_name}" not in frame:
            continue

        event_type: str | None = None
        data_text = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_text += line[5:].strip()

        if event_type != event_name or not data_text:
            continue

        envelopes.append(_SSEEnvelope.model_validate_json(data_text))

    return envelopes


@pytest.mark.asyncio
async def test_chat_payload_model_override_respected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()

    explicit_model = "claude-3-opus-20240229"

    response = await client.post(
        "/chat",
        json={"message": "hello", "model": explicit_model},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200

    routing_events = _extract_sse_event_envelopes(response.text, "routing")
    assert routing_events
    routing_data = routing_events[0].data
    assert routing_data.get("model") == explicit_model
    assert routing_data.get("tier") == "explicit"
    assert routing_data.get("reason") == f"user_selected:{explicit_model}"

    final_events = _extract_sse_event_envelopes(response.text, "final")
    assert final_events
    final_data = final_events[0].data
    assert final_data.get("model") == explicit_model
