"""Conversation model-selection contract (#231): synthetic, no services or inference."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.routes.conversations import router


OWNER = uuid.UUID(int=1)
CONVERSATION = uuid.UUID(int=2)
AT = datetime(2026, 10, 29, 1, 2, 3, tzinfo=timezone.utc)
SELECTABLE = "openrouter/test/selectable"


def conversation(metadata: object = None, *, user_id: uuid.UUID = OWNER):
    return {
        "id": CONVERSATION,
        "user_id": user_id,
        "pipeline": "cloud",
        "created_at": AT,
        "updated_at": AT,
        "last_activity_at": AT,
        "pinned": False,
        "metadata": metadata,
    }


@pytest_asyncio.fixture
async def api(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.routes.conversations.selectable_model_ids", lambda: {SELECTABLE}
    )
    app = FastAPI()
    app.include_router(router)
    store = AsyncMock()
    store.get_conversation.return_value = conversation()
    store.update_conversation.return_value = conversation()
    store.get_messages.return_value = []
    store.list_conversations_page.return_value = ([], 0, False)
    state = SimpleNamespace(memory_store=store, db_pool=None)
    app.dependency_overrides[get_app_state] = lambda: state
    app.dependency_overrides[require_device_auth] = lambda: AuthenticatedDevice(
        user_id=OWNER, device_id=uuid.UUID(int=10), session_id=uuid.UUID(int=11)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, store


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [SELECTABLE, "auto"])
async def test_patch_persists_only_the_selected_model_without_touching_activity(api, model):
    client, store = api
    response = await client.patch(f"/conversations/{CONVERSATION}", json={"selected_model": model})
    assert response.status_code == 200
    kwargs = store.update_conversation.call_args.kwargs
    assert kwargs["metadata_patch"] == {"selected_model": model}
    assert kwargs["touch_activity"] is False


@pytest.mark.asyncio
async def test_patch_with_other_fields_still_counts_as_activity(api):
    client, store = api
    response = await client.patch(
        f"/conversations/{CONVERSATION}", json={"title": "Renamed", "selected_model": SELECTABLE}
    )
    assert response.status_code == 200
    kwargs = store.update_conversation.call_args.kwargs
    assert kwargs["metadata_patch"] == {"selected_model": SELECTABLE}
    assert kwargs["touch_activity"] is True

    await client.patch(f"/conversations/{CONVERSATION}", json={"pinned": True})
    kwargs = store.update_conversation.call_args.kwargs
    assert kwargs["metadata_patch"] is None
    assert kwargs["touch_activity"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"selected_model": "openrouter/test/not-approved"},
        {"selected_model": ""},
        {"selected_model": "x" * 201},
        # The old client shape and any arbitrary metadata write fail loudly.
        {"metadata": {"model": SELECTABLE}},
        {"metadata": {"last_summarized_msg_count": 0}},
        {"model": SELECTABLE},
    ],
)
async def test_patch_rejects_unselectable_models_and_unknown_fields(api, body):
    client, store = api
    response = await client.patch(f"/conversations/{CONVERSATION}", json=body)
    assert response.status_code == 422
    store.update_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_unselectable_model_error_is_typed(api):
    client, _store = api
    response = await client.patch(
        f"/conversations/{CONVERSATION}", json={"selected_model": "openrouter/test/nope"}
    )
    assert response.json()["detail"]["code"] == "model_not_selectable"


@pytest.mark.asyncio
async def test_patch_cannot_select_a_model_for_another_users_conversation(api):
    client, store = api
    store.get_conversation.return_value = conversation(user_id=uuid.UUID(int=99))
    response = await client.patch(
        f"/conversations/{CONVERSATION}", json={"selected_model": SELECTABLE}
    )
    assert response.status_code == 404
    store.update_conversation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "auto"),
        ({}, "auto"),
        ({"selected_model": SELECTABLE}, SELECTABLE),
        # asyncpg returns JSONB as text without a codec.
        (json.dumps({"selected_model": SELECTABLE, "last_summarized_msg_count": 4}), SELECTABLE),
        # A stored choice that is no longer selectable is reported truthfully;
        # /chat admission, not this read, decides whether it can run.
        ({"selected_model": "openrouter/test/retired"}, "openrouter/test/retired"),
        ({"selected_model": 7}, "auto"),
        ("not json", "auto"),
    ],
)
async def test_reads_return_the_selected_model_but_never_raw_metadata(api, metadata, expected):
    client, store = api
    store.get_conversation.return_value = conversation(metadata)
    store.list_conversations_page.return_value = ([conversation(metadata)], 1, False)

    detail = (await client.get(f"/conversations/{CONVERSATION}")).json()
    listed = (await client.get("/conversations")).json()["conversations"][0]

    for body in (detail, listed):
        assert body["selected_model"] == expected
        assert "metadata" not in body


@pytest.mark.asyncio
async def test_store_merges_model_patch_and_can_leave_activity_untouched():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    store = MemoryStore(pool, ContentEncryption(Fernet.generate_key().decode()))

    await store.update_conversation(
        CONVERSATION, metadata_patch={"selected_model": SELECTABLE}, touch_activity=False
    )
    query, *args = pool.fetchrow.call_args.args
    # Merge, never replace: server-owned keys (summary checkpoints) survive.
    assert "COALESCE(metadata, '{}'::jsonb) || $8::jsonb" in query
    assert "updated_at       = CASE WHEN $10 THEN NOW() ELSE updated_at END" in query
    assert "last_activity_at = CASE WHEN $10 THEN NOW() ELSE last_activity_at END" in query
    assert json.loads(args[7]) == {"selected_model": SELECTABLE}
    assert args[9] is False

    await store.update_conversation(CONVERSATION, title="t")
    assert pool.fetchrow.call_args.args[10] is True
