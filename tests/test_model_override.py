from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
import uuid

from fastapi import Request
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from orchestrator import main as main_module
from tests.qualified_compute import install_qualified_compute

# The stand-in deployment must qualify the profiles these tests dispatch under,
# so admission is satisfied by the workload the classifier actually selected:
# Luna serves routine/research/council, GLM-5.3 serves reasoning.
OVERRIDE_FIXTURE_MODELS = (
    "openrouter/openai/gpt-6-luna",
    "openrouter/z-ai/glm-5.3",
    "openrouter/moonshotai/kimi-k2.5",
    "openrouter/test/explicit-model",
)


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    settings = get_settings()
    app_state = AppState(settings=settings)
    app_state.db_pool = object()  # type: ignore[assignment]
    install_qualified_compute(monkeypatch, models=OVERRIDE_FIXTURE_MODELS)

    async def override_settings():
        return get_settings()

    async def override_app_state():
        return app.state.app_state

    async def override_auth(_request: Request):
        return AuthenticatedDevice(
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        )

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[require_device_auth] = override_auth

    original_app_state = getattr(app.state, "app_state", None)
    original_settings = getattr(app.state, "settings", None)
    app.state.app_state = app_state
    app.state.settings = settings
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.state.app_state = original_app_state
        app.state.settings = original_settings


class _SSEEnvelope(BaseModel):
    type: str
    data: dict[str, object]


def _extract_sse_event_envelopes(response_text: str, event_name: str) -> list[_SSEEnvelope]:
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

    explicit_model = "openrouter/test/explicit-model"

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


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/chat", "/v1/chat/completions"])
@pytest.mark.parametrize(
    ("message", "profile"),
    [
        ("hello", "routine"),
        ("search for recent weather reports", "research"),
        ("search for and compare database architectures", "reasoning"),
    ],
)
async def test_endpoint_carries_profile_to_account_dispatch(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    message: str,
    profile: str,
) -> None:
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()
    actual_frames = main_module._account_chat_frames
    observed: list[tuple[str, bool, str]] = []

    async def capture(*args, **kwargs):
        observed.append((kwargs["profile"], kwargs["auto_route"], kwargs["actual_model"]))
        async for frame in actual_frames(*args, **kwargs):
            yield frame

    monkeypatch.setattr(main_module, "_account_chat_frames", capture)
    payload = (
        {"message": message, "model": "auto"}
        if endpoint == "/chat"
        else {"model": "auto", "messages": [{"role": "user", "content": message}], "stream": True}
    )
    response = await client.post(endpoint, json=payload)
    assert response.status_code == 200
    assert observed == [(profile, True, "auto")]


@pytest.mark.asyncio
async def test_council_error_finishes_with_error_status(client, monkeypatch) -> None:
    async def unavailable(**kwargs):
        yield main_module.sse(
            "council_error",
            {"type": "council_error", "data": {"message": "Council diversity unavailable"}},
        )

    monkeypatch.setattr(main_module, "stream_council", unavailable)
    response = await client.post("/chat", json={"message": "/council --default Compare options"})
    assert response.status_code == 200
    assert _extract_sse_event_envelopes(response.text, "council_error")
    assert _extract_sse_event_envelopes(response.text, "done")[-1].data["status"] == "error"


@pytest.mark.asyncio
async def test_council_partial_progress_is_saved_as_error_on_abort(monkeypatch) -> None:
    async def interrupted(*args, **kwargs):
        yield main_module.sse(
            "council_output",
            {"type": "council_output", "data": {"content": "Partial result"}},
        )
        raise RuntimeError("account cleanup failed")

    monkeypatch.setattr(main_module, "_account_frames", interrupted)

    class Store:
        insert_message = AsyncMock()

    store = Store()
    user_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="account cleanup failed"):
        async for _ in main_module._stream_council_events(
            db_pool=object(),
            account_user_id=user_id,
            source=lambda: interrupted(),
            store=store,
            conversation_uuid=uuid.uuid4(),
            user_id=user_id,
            conversation_id="conv_test",
            request_id="req_test",
            log_message="Council persistence failed",
        ):
            pass

    store.insert_message.assert_awaited_once()
    saved = store.insert_message.await_args.kwargs
    assert saved["status"] == "error"
    assert saved["content"] == "Partial result"
    assert saved["metadata"]["council_events"][0]["type"] == "council_output"
