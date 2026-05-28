from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import get_settings
from orchestrator.main import app


def make_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def authenticated_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()

    async with app.router.lifespan_context(app):
        original_app_state = app.state.app_state
        app_state = MagicMock()
        app_state.db_pool = object()
        app_state.redis = None
        app_state.memory_store = None
        app_state.video_credits_dal = None
        app.state.app_state = app_state
        try:
            yield app_state
        finally:
            app.state.app_state = original_app_state
            get_settings.cache_clear()


def install_auth(monkeypatch, user_id: uuid.UUID, *, token: str = "valid-token") -> None:
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def fake_verify(_pool, presented_token: str):
        if presented_token != token:
            return None
        return AuthenticatedDevice(
            user_id=user_id,
            device_id=device_id,
            session_id=session_id,
        )

    import orchestrator.auth as auth_module

    monkeypatch.setattr(auth_module, "_verify_access_token", fake_verify)


@pytest.mark.asyncio
async def test_conversations_list_uses_authenticated_user(authenticated_app, monkeypatch):
    auth_user_id = uuid.uuid4()
    install_auth(monkeypatch, auth_user_id)

    store = AsyncMock()
    store.list_conversations = AsyncMock(return_value=[])
    authenticated_app.memory_store = store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations", headers=make_auth_headers("valid-token"))

    assert response.status_code == 200
    store.list_conversations.assert_called_once()
    assert store.list_conversations.call_args.kwargs["user_id"] == auth_user_id


@pytest.mark.asyncio
async def test_conversation_get_hides_other_users_record(authenticated_app, monkeypatch):
    auth_user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    install_auth(monkeypatch, auth_user_id)

    conversation_id = uuid.uuid4()
    store = AsyncMock()
    store.get_conversation = AsyncMock(
        return_value={"id": conversation_id, "user_id": other_user_id}
    )
    store.get_messages = AsyncMock(return_value=[])
    authenticated_app.memory_store = store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/conversations/{conversation_id}", headers=make_auth_headers("valid-token")
        )

    assert response.status_code == 404
    store.get_messages.assert_not_called()


@pytest.mark.asyncio
async def test_user_settings_use_authenticated_user(authenticated_app, monkeypatch):
    auth_user_id = uuid.uuid4()
    install_auth(monkeypatch, auth_user_id)

    store = AsyncMock()
    store.get_user_settings = AsyncMock(return_value={"preferences": {}})
    authenticated_app.memory_store = store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/users/me/settings", headers=make_auth_headers("valid-token")
        )

    assert response.status_code == 200
    store.get_user_settings.assert_called_once_with(auth_user_id)


@pytest.mark.asyncio
async def test_video_credits_balance_ignores_caller_user_id(authenticated_app, monkeypatch):
    auth_user_id = uuid.uuid4()
    requested_user_id = uuid.uuid4()
    install_auth(monkeypatch, auth_user_id)

    credits_dal = AsyncMock()
    credits_dal.get_balance = AsyncMock(return_value=17)
    authenticated_app.video_credits_dal = credits_dal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/video-credits/balance?user_id={requested_user_id}",
            headers=make_auth_headers("valid-token"),
        )

    assert response.status_code == 200
    assert response.json()["balance"] == 17
    credits_dal.get_balance.assert_called_once_with(auth_user_id)


@pytest.mark.asyncio
async def test_chat_persistence_ignores_payload_user_id(authenticated_app, monkeypatch):
    auth_user_id = uuid.uuid4()
    payload_user_id = uuid.uuid4()
    install_auth(monkeypatch, auth_user_id)

    conversation_id = uuid.uuid4()
    store = AsyncMock()
    store.create_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": auth_user_id,
            "pipeline": "cloud",
            "title": "Hello",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
    )
    store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    store.get_conversation = AsyncMock(return_value=None)
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_user_settings = AsyncMock(return_value={})
    authenticated_app.memory_store = store

    async def fake_stream_sse_chat(**_kwargs):
        yield 'event: token\ndata: {"type":"token","data":{"delta":"ok"}}\n\n'
        yield 'event: final\ndata: {"type":"final","data":{}}\n\n'
        yield 'event: done\ndata: {"type":"done","data":{"ok":true}}\n\n'

    transport = ASGITransport(app=app)
    with patch("orchestrator.main.stream_sse_chat", fake_stream_sse_chat):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat",
                headers={
                    **make_auth_headers("valid-token"),
                    "Content-Type": "application/json",
                },
                json={
                    "message": "hello",
                    "messages": [],
                    "user_id": str(payload_user_id),
                },
            )

    assert response.status_code == 200
    assert store.create_conversation.call_args.kwargs["user_id"] == auth_user_id
    assert store.insert_message.call_args.kwargs["user_id"] == auth_user_id
