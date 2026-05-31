from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


@pytest.mark.asyncio
async def test_council_default_stream_emits_sse_flow(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handle = AsyncMock(
        return_value={
            "type": "council_output",
            "session_id": "ses_test",
            "output": {
                "consensus": "Consensus section",
                "perspectives_summary": "Contested section",
                "findings": [],
                "metadata": {
                    "total_tokens": 123,
                    "total_cost": 0.12,
                    "models_used": ["model-a", "model-b"],
                },
            },
        }
    )
    monkeypatch.setattr("orchestrator.council.sse.handle_council_command", mock_handle)

    response = await client.post(
        "/chat",
        json={"message": "/council --default Should I sell?"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: council_progress" in body
    assert "event: council_output" in body
    assert "event: council_done" in body
    assert "event: done" in body
    assert body.index("event: council_progress") < body.index("event: council_done")


@pytest.mark.asyncio
async def test_council_interview_config_response_executes(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_command = AsyncMock(
        return_value={
            "type": "interview",
            "config": {
                "roster": {
                    "analyst": "model-a",
                    "strategist": "model-b",
                    "skeptic": "model-c",
                },
                "audit_enabled": False,
            },
        }
    )
    mock_interview = AsyncMock(
        return_value={
            "type": "council_output",
            "session_id": "ses_after_interview",
            "output": {
                "consensus": "Config applied",
                "perspectives_summary": "Details",
                "findings": [],
                "metadata": {"total_tokens": 50, "total_cost": 0.04, "models_used": []},
            },
        }
    )

    monkeypatch.setattr("orchestrator.council.sse.handle_council_command", mock_command)
    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_interview_response",
        mock_interview,
    )

    interview_response = await client.post(
        "/chat",
        json={"message": "/council evaluate this option"},
        headers={"Content-Type": "application/json"},
    )

    assert interview_response.status_code == 200
    assert "event: council_interview" in interview_response.text

    run_response = await client.post(
        "/chat",
        json={"message": "/council config: preset=lean, rounds=3, audit=true"},
        headers={"Content-Type": "application/json"},
    )

    assert run_response.status_code == 200
    assert "event: council_done" in run_response.text

    assert mock_interview.await_count == 1
    assert mock_interview.await_args is not None
    kwargs = mock_interview.await_args.kwargs
    config = kwargs["config"]
    assert config.preset_name == "lean"
    assert config.round_count == 3
    assert config.audit_enabled is True


@pytest.mark.asyncio
async def test_normal_message_bypasses_council_branch(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handle = AsyncMock(side_effect=AssertionError("council path should not execute"))
    monkeypatch.setattr("orchestrator.council.sse.handle_council_command", mock_handle)

    response = await client.post(
        "/chat",
        json={"message": "hello"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: council_" not in body
    assert "event: done" in body
    assert mock_handle.await_count == 0
