"""Tests for progressive SSE emission during council execution."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    # Mock specific modules that cause import issues before importing app
    with patch.dict(
        "sys.modules",
        {
            "trafilatura": AsyncMock(),
            "orchestrator.services.fetch.extract": AsyncMock(),
            "orchestrator.services.fetch.strategies.archive": AsyncMock(),
            "orchestrator.services.fetch.service": AsyncMock(),
            "orchestrator.tools.web_fetch": AsyncMock(),
            "orchestrator.tools.builtin": AsyncMock(),
            "orchestrator.council.engine": AsyncMock(),
        },
    ):
        from orchestrator.main import app

        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("REDIS_URL", "")
        monkeypatch.setenv("MOCK_LLM", "true")
        get_settings.cache_clear()

        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as http_client:
                yield http_client


@pytest.mark.asyncio
async def test_progress_events_arrive_during_execution(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 1,
                    "models_total": 3,
                }
            )
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 2,
                    "models_total": 3,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_progress",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 100,
                    "total_cost": 0.05,
                    "models_used": ["model-a", "model-b"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test progressive events"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    assert "event: council_progress" in body
    assert "event: council_output" in body
    assert "event: council_done" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_event_ordering_preserved(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 1,
                    "models_total": 2,
                }
            )
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 2,
                    "models_total": 2,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_ordering",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 100,
                    "total_cost": 0.05,
                    "models_used": ["model-a", "model-b"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test event ordering"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    progress_pos = body.find("event: council_progress")
    output_pos = body.find("event: council_output")
    done_pos = body.find("event: council_done")

    assert progress_pos != -1
    assert output_pos != -1
    assert done_pos != -1

    assert progress_pos < output_pos
    assert output_pos < done_pos


@pytest.mark.asyncio
async def test_done_event_is_last(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 1,
                    "models_complete": 1,
                    "models_total": 1,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_done_last",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 50,
                    "total_cost": 0.02,
                    "models_used": ["model-a"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test done is last"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    done_pos = body.rfind("event: done")
    council_done_pos = body.rfind("event: council_done")

    assert done_pos != -1
    assert council_done_pos != -1

    last_council_progress = body.rfind("event: council_progress")
    last_council_output = body.rfind("event: council_output")
    last_council_done = body.rfind("event: council_done")

    assert last_council_done > last_council_progress
    assert last_council_done > last_council_output


@pytest.mark.asyncio
async def test_no_events_after_council_done(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 1,
                    "models_complete": 1,
                    "models_total": 1,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_no_after_done",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 50,
                    "total_cost": 0.02,
                    "models_used": ["model-a"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test no events after done"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    last_council_done_pos = body.rfind("event: council_done")
    assert last_council_done_pos != -1

    remaining_body = body[last_council_done_pos:]

    assert "event: council_progress" not in remaining_body[20:]
    assert "event: council_output" not in remaining_body[20:]

    assert "event: done" in body


@pytest.mark.asyncio
async def test_progress_events_with_timestamps(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 3,
                    "models_complete": 1,
                    "models_total": 3,
                }
            )
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 3,
                    "models_complete": 2,
                    "models_total": 3,
                }
            )
            await progress_callback(
                {
                    "stage": "round_2",
                    "current_round": 2,
                    "total_rounds": 3,
                    "models_complete": 1,
                    "models_total": 3,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_timestamps",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 150,
                    "total_cost": 0.08,
                    "models_used": ["model-a", "model-b", "model-c"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test timestamps"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    progress_count = body.count("event: council_progress")
    assert progress_count >= 3

    assert "event: council_output" in body
    assert "event: council_done" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_event_ordering_preserved(  # noqa: F811
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 1,
                    "models_total": 2,
                }
            )
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 2,
                    "models_complete": 2,
                    "models_total": 2,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_ordering",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 100,
                    "total_cost": 0.05,
                    "models_used": ["model-a", "model-b"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test event ordering"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    progress_pos = body.find("event: council_progress")
    output_pos = body.find("event: council_output")
    done_pos = body.find("event: council_done")

    assert progress_pos != -1
    assert output_pos != -1
    assert done_pos != -1

    assert progress_pos < output_pos
    assert output_pos < done_pos


@pytest.mark.asyncio
async def test_done_event_is_last(  # noqa: F811
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 1,
                    "models_complete": 1,
                    "models_total": 1,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_done_last",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 50,
                    "total_cost": 0.02,
                    "models_used": ["model-a"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test done is last"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    done_pos = body.rfind("event: done")
    council_done_pos = body.rfind("event: council_done")

    assert done_pos != -1
    assert council_done_pos != -1

    last_council_progress = body.rfind("event: council_progress")
    last_council_output = body.rfind("event: council_output")
    last_council_done = body.rfind("event: council_done")

    assert last_council_done > last_council_progress
    assert last_council_done > last_council_output


@pytest.mark.asyncio
async def test_no_events_after_council_done(  # noqa: F811
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 1,
                    "models_complete": 1,
                    "models_total": 1,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_no_after_done",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 50,
                    "total_cost": 0.02,
                    "models_used": ["model-a"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test no events after done"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    last_council_done_pos = body.rfind("event: council_done")
    assert last_council_done_pos != -1

    remaining_body = body[last_council_done_pos:]

    assert "event: council_progress" not in remaining_body[20:]
    assert "event: council_output" not in remaining_body[20:]

    assert "event: done" in body


@pytest.mark.asyncio
async def test_progress_events_with_timestamps(  # noqa: F811
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_handle_council_command(
        message: str,
        conversation_id: str,
        bypass_interview: bool,
        progress_callback=None,
    ):
        if progress_callback:
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 3,
                    "models_complete": 1,
                    "models_total": 3,
                }
            )
            await progress_callback(
                {
                    "stage": "round_1",
                    "current_round": 1,
                    "total_rounds": 3,
                    "models_complete": 2,
                    "models_total": 3,
                }
            )
            await progress_callback(
                {
                    "stage": "round_2",
                    "current_round": 2,
                    "total_rounds": 3,
                    "models_complete": 1,
                    "models_total": 3,
                }
            )

        return {
            "type": "council_output",
            "session_id": "ses_test_timestamps",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 150,
                    "total_cost": 0.08,
                    "models_used": ["model-a", "model-b", "model-c"],
                },
            },
        }

    monkeypatch.setattr(
        "orchestrator.council.sse.handle_council_command", mock_handle_council_command
    )

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test timestamps"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    progress_count = body.count("event: council_progress")
    assert progress_count >= 3

    assert "event: council_output" in body
    assert "event: council_done" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_progress_events_with_timestamps(  # noqa: F811
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handle = AsyncMock(
        return_value={
            "type": "council_output",
            "session_id": "ses_test_timestamps",
            "output": {
                "consensus": "Test consensus",
                "perspectives_summary": "Test perspectives",
                "findings": [],
                "metadata": {
                    "total_tokens": 150,
                    "total_cost": 0.08,
                    "models_used": ["model-a", "model-b", "model-c"],
                },
            },
        }
    )
    monkeypatch.setattr("orchestrator.council.sse.handle_council_command", mock_handle)

    response = await client.post(
        "/chat",
        json={"message": "/council --default Test timestamps"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.text

    # Verify progress events are present (at least one)
    assert "event: council_progress" in body

    # Verify all expected events are present
    assert "event: council_output" in body
    assert "event: council_done" in body
    assert "event: done" in body

    # Verify the mock was called
    assert mock_handle.await_count == 1
