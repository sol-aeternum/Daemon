"""Synthetic cursor/HTTP/store checks: no services, inference or model context."""

from datetime import datetime, timedelta, timezone
import base64
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
from orchestrator.history_pagination import (
    HistoryBoundary,
    InvalidHistoryCursor,
    decode_cursor,
    encode_cursor,
)
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.routes.conversations import router


OWNER = uuid.UUID(int=1)
CONVERSATION = uuid.UUID(int=2)
AT = datetime(2026, 10, 28, 1, 2, 3, 123456, tzinfo=timezone.utc)


def conversation(number=2):
    return {
        "id": uuid.UUID(int=number),
        "user_id": OWNER,
        "pipeline": "cloud",
        "created_at": AT,
        "updated_at": AT,
        "last_activity_at": AT,
        "pinned": False,
    }


def message(number=3, *, encrypted=None):
    return {
        "id": uuid.UUID(int=number),
        "conversation_id": CONVERSATION,
        "user_id": OWNER,
        "role": "assistant",
        "content": encrypted or "synthetic history",
        "created_at": AT,
        "status": "cancelled",
    }


@pytest.mark.parametrize("conversation_id", [None, CONVERSATION])
def test_cursor_roundtrip_preserves_microseconds_and_offset(conversation_id):
    boundary = HistoryBoundary(
        AT.astimezone(timezone(timedelta(hours=9, minutes=30))),
        uuid.UUID(int=3),
        False if conversation_id is None else None,
    )
    token = encode_cursor(boundary, user_id=OWNER, conversation_id=conversation_id)
    assert decode_cursor(token, user_id=OWNER, conversation_id=conversation_id) == boundary


@pytest.mark.parametrize(
    "change",
    [
        {"v": 2},
        {"v": True},
        {"owner": str(uuid.UUID(int=100))},
        {"scope": "search"},
        {"at": "2026-10-28T00:00:00"},
        {"id": "not-a-uuid"},
        {"pinned": 0},
        {"extra": 1},
        {"at": []},
    ],
)
def test_cursor_rejects_invalid_payload(change):
    token = encode_cursor(HistoryBoundary(AT, uuid.UUID(int=3), False), user_id=OWNER)
    data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    data.update(change)
    bad = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    with pytest.raises(InvalidHistoryCursor):
        decode_cursor(bad, user_id=OWNER)


@pytest.mark.parametrize("token", ["", "*", "a" * 1025, "λ", "bnVsbA", "W10"])
def test_cursor_rejects_bad_encoding_or_shape(token):
    with pytest.raises(InvalidHistoryCursor):
        decode_cursor(token, user_id=OWNER)


def test_message_cursor_cannot_cross_conversation_or_list():
    token = encode_cursor(
        HistoryBoundary(AT, uuid.UUID(int=3)), user_id=OWNER, conversation_id=CONVERSATION
    )
    for scope in [None, uuid.UUID(int=99)]:
        with pytest.raises(InvalidHistoryCursor):
            decode_cursor(token, user_id=OWNER, conversation_id=scope)


@pytest_asyncio.fixture
async def api():
    app = FastAPI()
    app.include_router(router)
    store = AsyncMock()
    store.list_conversations_page.return_value = ([], 0, False)
    store.get_conversation.return_value = conversation()
    store.get_messages.return_value = []
    store.get_message_history_page.return_value = ([], False)
    state = SimpleNamespace(memory_store=store, db_pool=None)
    app.dependency_overrides[get_app_state] = lambda: state
    app.dependency_overrides[require_device_auth] = lambda: AuthenticatedDevice(
        user_id=OWNER, device_id=uuid.UUID(int=10), session_id=uuid.UUID(int=11)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, store


@pytest.mark.asyncio
async def test_list_cursor_metadata_and_defaults(api):
    client, store = api
    store.list_conversations_page.return_value = ([conversation(9), conversation(8)], 101, True)
    response = await client.get("/conversations?pagination=cursor&limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 101
    assert len(body["conversations"]) == 2
    page = body["page"]
    assert page["has_more"] is True
    boundary = decode_cursor(page["next_cursor"], user_id=OWNER)
    assert boundary == HistoryBoundary(AT, uuid.UUID(int=8), False)
    await client.get(
        "/conversations", params={"pagination": "cursor", "cursor": page["next_cursor"]}
    )
    assert store.list_conversations_page.call_args.kwargs["before"] == boundary
    assert store.list_conversations_page.call_args.kwargs["limit"] == 100


@pytest.mark.asyncio
async def test_legacy_search_offset_and_truthful_empty_total(api):
    client, store = api
    store.list_conversations_page.return_value = ([], 201, False)
    response = await client.get("/conversations?search=Council&offset=500&limit=50")
    assert response.status_code == 200
    assert response.json() == {"conversations": [], "total": 201, "page": None}
    assert store.list_conversations_page.call_args.kwargs == {
        "user_id": OWNER,
        "search": "Council",
        "offset": 500,
        "limit": 50,
        "before": None,
    }
    await client.get("/conversations")
    assert store.list_conversations_page.call_args.kwargs["limit"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [conversation()]])
async def test_terminal_list_page(rows, api):
    client, store = api
    store.list_conversations_page.return_value = (rows, len(rows), False)
    response = await client.get("/conversations?pagination=cursor")
    assert response.status_code == 200
    page = response.json()["page"]
    assert page["has_more"] is False and page["next_cursor"] is None
    assert (page["start_cursor"] is None) == (not rows)
    assert (page["end_cursor"] is None) == (not rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status",
    [
        ("/conversations?pagination=cursor&offset=1", 400),
        ("/conversations?pagination=cursor&search=hi", 400),
        ("/conversations?pagination=cursor&limit=101", 422),
        ("/conversations?pagination=cursor&limit=0", 422),
        ("/conversations?pagination=cursor&cursor=", 400),
        ("/conversations?cursor=bad", 400),
        (f"/conversations/{CONVERSATION}?pagination=cursor&offset=1", 400),
        (f"/conversations/{CONVERSATION}?pagination=cursor&limit=101", 422),
        (f"/conversations/{CONVERSATION}?before=bad", 400),
        (f"/conversations/{CONVERSATION}?pagination=cursor&through=bad", 400),
    ],
)
async def test_invalid_requests_do_not_read_pages(api, path, status):
    client, store = api
    response = await client.get(path)
    assert response.status_code == status
    store.list_conversations_page.assert_not_awaited()
    store.get_messages.assert_not_awaited()
    store.get_message_history_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_messages_metadata_bounds_and_legacy(api):
    client, store = api
    store.get_message_history_page.return_value = ([message(3), message(4)], True)
    path = f"/conversations/{CONVERSATION}"
    response = await client.get(path, params={"pagination": "cursor", "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert [m["id"] for m in body["messages"]] == [str(uuid.UUID(int=n)) for n in [3, 4]]
    assert body["messages"][0]["status"] == "cancelled"
    assert body["active_task"] is None and body["latest_task"] is None
    page = body["message_page"]
    assert page["has_more"] is True
    assert page["next_cursor"] == page["start_cursor"]
    await client.get(
        path,
        params={
            "pagination": "cursor",
            "before": page["next_cursor"],
            "through": page["start_cursor"],
        },
    )
    args = store.get_message_history_page.call_args.kwargs
    assert args["before"] == args["through"] == HistoryBoundary(AT, uuid.UUID(int=3))
    response = await client.get(path, params={"limit": 500, "offset": 7})
    assert response.status_code == 200 and response.json()["message_page"] is None
    store.get_messages.assert_awaited_once_with(CONVERSATION, limit=500, offset=7)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [message()]])
async def test_terminal_message_page(rows, api):
    client, store = api
    store.get_message_history_page.return_value = (rows, False)
    response = await client.get(f"/conversations/{CONVERSATION}?pagination=cursor")
    page = response.json()["message_page"]
    assert page["has_more"] is False and page["next_cursor"] is None
    assert (page["start_cursor"] is None) == (not rows)


@pytest.mark.asyncio
async def test_wrong_owner_and_cross_scope_do_not_read_messages(api):
    client, store = api
    token = encode_cursor(
        HistoryBoundary(AT, uuid.UUID(int=3)), user_id=OWNER, conversation_id=uuid.UUID(int=99)
    )
    response = await client.get(
        f"/conversations/{CONVERSATION}", params={"pagination": "cursor", "before": token}
    )
    assert response.status_code == 400
    store.get_conversation.return_value = {**conversation(), "user_id": uuid.UUID(int=99)}
    response = await client.get(f"/conversations/{CONVERSATION}?pagination=cursor")
    assert response.status_code == 404
    store.get_message_history_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_message_page_trims_before_decrypt_and_reverses():
    enc = ContentEncryption(Fernet.generate_key().decode())
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            message(5, encrypted=enc.encrypt("newer")),
            message(4, encrypted=enc.encrypt("older")),
            message(3, encrypted="lookahead must not be decrypted"),
        ]
    )
    store = MemoryStore(pool, enc)
    boundary = HistoryBoundary(AT, uuid.UUID(int=9))
    rows, more = await store.get_message_history_page(
        CONVERSATION, user_id=OWNER, limit=2, before=boundary, through=boundary
    )
    assert more is True
    assert [r["content"] for r in rows] == ["older", "newer"]
    query, *args = pool.fetch.call_args.args
    assert "m.created_at, m.id) <" in query and "m.created_at, m.id) >=" in query
    assert "c.user_id = m.user_id" in query and "c.user_id = $2" in query
    assert args == [CONVERSATION, OWNER, AT, boundary.id, AT, boundary.id, 3]


@pytest.mark.asyncio
async def test_store_list_page_empty_retains_count():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": None, "matching_total": 100}])
    store = MemoryStore(pool, ContentEncryption(Fernet.generate_key().decode()))
    assert await store.list_conversations_page(OWNER, offset=100) == ([], 100, False)
    pool.fetch.assert_awaited_once()
    query, *args = pool.fetch.call_args.args
    assert "matching LEFT JOIN page_rows ON TRUE" in query
    assert "effective_last_activity_at DESC, c.id DESC" in query
    assert args == [OWNER, None, None, None, None, 101, 100]
