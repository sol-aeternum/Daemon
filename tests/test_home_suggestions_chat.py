"""Mocked authenticated API/new-chat/history acceptance; no live inference."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from tests.qualified_compute import install_qualified_compute
from tests.test_home_suggestions import rig, produce

# Reuse the fictional service fixture, not an external Redis/model integration.
__all__ = ["rig"]


@pytest_asyncio.fixture
async def chat_client(rig, monkeypatch):
    import orchestrator.main as main

    service, store, redis, _, _ = rig
    install_qualified_compute(monkeypatch)
    settings = get_settings()
    state = AppState(settings=settings)
    state.db_pool = object()  # type: ignore[assignment]
    state.redis = redis  # type: ignore[assignment]
    state.memory_store = store  # type: ignore[assignment]
    auth = AuthenticatedDevice(
        user_id=store.user_id, device_id=uuid.uuid4(), session_id=uuid.uuid4()
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_app_state] = lambda: state
    app.dependency_overrides[require_device_auth] = lambda: auth
    monkeypatch.setattr(main, "_enforce_chat_rate_limit", AsyncMock())
    monkeypatch.setattr(main, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(main, "get_rate_limiter", lambda request: None)
    monkeypatch.setattr(main, "build_skill_index", AsyncMock(return_value=""))
    monkeypatch.setattr(main, "HomeSuggestions", lambda *args: service)
    captured: list[dict[str, Any]] = []

    async def frames(pool, owner, **kwargs):
        assert owner == store.user_id
        captured.append(kwargs)
        yield "event: done\ndata: " + json.dumps({"data": {"ok": False}}) + "\n\n"

    monkeypatch.setattr(main, "_account_chat_frames", frames)
    store.get_recent_messages = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    store.get_conversation = AsyncMock()  # type: ignore[attr-defined]
    store.insert_message = AsyncMock()  # type: ignore[attr-defined]
    store.get_home_suggestion_turn = AsyncMock()  # type: ignore[attr-defined]

    async def binding(conv_id, user_id):
        assert user_id == store.user_id
        binding = store.bindings[0]
        return {
            "id": "original-bound-turn",
            "role": "user",
            "content": binding["prompt"],
            "metadata": {"home_suggestion": binding["context"]},
        }

    store.get_home_suggestion_turn.side_effect = binding  # type: ignore[attr-defined]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, captured, state
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"conversation_id": "conv_client_destination"},
        {"messages": [{"role": "user", "content": "unrelated client history"}]},
        {"attachments": [{"kind": "text", "text_content": "unrelated pending file"}]},
        {"metadata": {"local": True}},
        {"user_id": str(uuid.uuid4())},
    ],
)
async def test_suggestion_rejects_client_destination_history_files_and_metadata(
    rig, chat_client, extra
):
    service, store, _, _, _ = rig
    await produce(rig)
    candidate = (await service.list())["suggestions"][0]
    client, captured, _ = chat_client
    response = await client.post(
        "/chat", json={"message": candidate["prompt"], "suggestion_id": candidate["id"], **extra}
    )
    assert response.status_code == 422
    assert not store.bindings and not captured


@pytest.mark.asyncio
async def test_suggestion_is_server_new_destination_ordinary_model_path_and_reload_context(
    rig, chat_client
):
    service, store, redis, _, _ = rig
    await produce(rig)
    candidate = (await service.list())["suggestions"][0]
    client, captured, _ = chat_client
    first = await client.post(
        "/chat",
        json={
            "message": candidate["prompt"],
            "suggestion_id": candidate["id"],
            "model": "openrouter/test/explicit-model",
        },
    )
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    assert len(store.bindings) == len(captured) == 1
    store.insert_message.assert_not_awaited()  # type: ignore[attr-defined]
    request = captured[0]
    assert request["conversation_id"].startswith("conv_")
    assert request["reported_model"] == "openrouter/test/explicit-model"
    assert request["user_message"] == candidate["prompt"]
    assert request["history_messages"][0]["role"] == "user"
    assert "fictional private launch notes" in request["history_messages"][0]["content"]
    assert "fictional private launch notes" not in request["system_prompt"]
    duplicate = await client.post(
        "/chat", json={"message": candidate["prompt"], "suggestion_id": candidate["id"]}
    )
    assert duplicate.status_code == 409 and len(captured) == 1
    # Simulate reload/follow-up after Redis expiry and original-source deletion.
    destination = request["conversation_uuid"]
    redis.now += 3601
    redis.failed = True
    store.sources.clear()
    store.get_conversation.return_value = {
        "id": destination,
        "user_id": store.user_id,
        "metadata": {"home_suggestion": 1},
    }  # type: ignore[attr-defined]
    store.get_recent_messages.return_value = [
        {"id": "new-turn", "role": "user", "content": "Refine the plan."}
    ]  # type: ignore[attr-defined]
    followup = await client.post(
        "/chat", json={"message": "Refine the plan.", "conversation_id": f"conv_{destination}"}
    )
    assert followup.status_code == 200 and len(captured) == 2
    history = captured[-1]["history_messages"]
    assert "fictional private launch notes" in history[0]["content"]
    assert history[-1]["content"] == "Refine the plan."
    assert history[0]["role"] == "user"


@pytest.mark.asyncio
async def test_chat_persistence_failure_has_no_graceful_fallback_or_provider(rig, chat_client):
    service, store, _, _, _ = rig
    await produce(rig)
    candidate = (await service.list())["suggestions"][0]
    store.persistence_failed = True
    client, captured, _ = chat_client
    response = await client.post(
        "/chat", json={"message": candidate["prompt"], "suggestion_id": candidate["id"]}
    )
    assert response.status_code == 503
    assert "fictional private" not in response.text
    assert not store.bindings and not captured
    store.persistence_failed = False
    replay = await client.post(
        "/chat", json={"message": candidate["prompt"], "suggestion_id": candidate["id"]}
    )
    assert replay.status_code == 409 and not captured


@pytest.mark.asyncio
async def test_ordinary_chat_rate_admission_still_precedes_candidate_claim(
    rig, chat_client, monkeypatch
):
    service, store, _, _, _ = rig
    await produce(rig)
    candidate = (await service.list())["suggestions"][0]
    monkeypatch.setattr(
        "orchestrator.main._enforce_chat_rate_limit",
        AsyncMock(side_effect=HTTPException(429, "Rate denied")),
    )
    client, captured, _ = chat_client
    response = await client.post(
        "/chat", json={"message": candidate["prompt"], "suggestion_id": candidate["id"]}
    )
    assert response.status_code == 429 and not captured and not store.bindings
    assert (await service.list())["suggestions"]


@pytest.mark.asyncio
async def test_list_refresh_auth_no_store_and_authoritative_disabled_state(rig, chat_client):
    service, store, redis, model, _ = rig
    client, _, state = chat_client
    store.enabled = False
    state.redis = None
    disabled = await client.get("/home-suggestions")
    assert disabled.json() == {"enabled": False, "status": "disabled", "suggestions": []}
    assert disabled.headers["cache-control"] == "no-store"
    store.enabled = True
    unavailable = await client.get("/home-suggestions")
    assert unavailable.json()["enabled"] is True and unavailable.json()["status"] == "unavailable"
    state.redis = redis  # type: ignore[assignment]
    queued = await client.post("/home-suggestions/refresh")
    assert queued.status_code == 202 and queued.json() == {"status": "queued"}
    assert queued.headers["cache-control"] == "no-store"
    assert (await client.get("/home-suggestions")).json()["status"] == "generating"
    model.assert_not_awaited()  # GET and refresh enqueue only; worker dispatches.
    for _ in range(3):
        await client.post("/home-suggestions/refresh")
    limited = await client.post("/home-suggestions/refresh")
    assert limited.status_code == 429 and limited.headers["cache-control"] == "no-store"
    assert "fictional" not in limited.text
    app.dependency_overrides.pop(require_device_auth)
    assert (await client.get("/home-suggestions")).status_code == 401
    assert (await client.post("/home-suggestions/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_optin_endpoint_rejects_coercions_and_stale_mixed_settings_patch(chat_client):
    client, _, _ = chat_client
    for value in ("true", 1, None):
        response = await client.patch(
            "/users/me/settings", json={"preferences": {"home_suggestions_enabled": value}}
        )
        assert response.status_code == 422
    mixed = await client.patch(
        "/users/me/settings",
        json={"preferences": {"home_suggestions_enabled": True, "personality": "concise"}},
    )
    assert mixed.status_code == 422
