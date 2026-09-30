"""Issue #362: title scheduling for pre-created UI drafts.

The frontend creates a conversation titled ``New conversation`` before the
first chat message, so the chat endpoint sees ``conversation_exists=True`` and
previously never enqueued ``generate_title``. These tests pin the approved
behaviour:

* An unlocked, still-empty draft enqueues title generation exactly once when
  its first message is successfully persisted.
* Locked (manually set) titles are preserved — no queue.
* Existing conversations that already contain messages are never backfilled.
* The newly-created-in-chat path keeps its title enqueue.
* A failed insert does not enqueue; an enqueue failure does not break chat;
  unauthorized conversations schedule nothing.

All queues and stores are fakes — no live paid calls.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from orchestrator.services.identity.rate_limiter import RateLimiter
from tests.qualified_compute import install_qualified_compute


class FakeTitleQueue:
    """Records arq enqueue attempts without touching Redis."""

    def __init__(self, *, raise_on_enqueue: Exception | None = None) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.raise_on_enqueue = raise_on_enqueue

    async def enqueue_job(
        self,
        *args: Any,
        _job_id: str | None = None,
        _defer_by: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.attempts.append(
            {"args": args, "job_id": _job_id, "defer_by": _defer_by, "kwargs": kwargs}
        )
        if self.raise_on_enqueue is not None:
            raise self.raise_on_enqueue
        return MagicMock(job_id=_job_id)


@pytest_asyncio.fixture
async def client(monkeypatch):
    """Mirror the test_chat_history client fixture with explicit env isolation."""
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-title-scheduling-pepper")
    get_settings.cache_clear()

    settings = get_settings()
    initial_app_state = AppState(settings=settings)
    # Title queues are intentionally independent of the request-limiter Redis seam.
    monkeypatch.setattr(
        "orchestrator.main.get_rate_limiter",
        lambda request: RateLimiter(None, hmac_secret="test-title-scheduling-pepper"),
    )
    install_qualified_compute(monkeypatch)

    async def override_settings():
        return settings

    async def override_app_state():
        return app.state.app_state

    async def override_auth():
        user_id = getattr(
            app.state,
            "_test_auth_user_id",
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
        )
        return AuthenticatedDevice(
            user_id=user_id,
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        )

    monkeypatch.setattr(
        "orchestrator.main.init_app_state",
        AsyncMock(return_value=initial_app_state),
    )
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[require_device_auth] = override_auth

    try:
        async with app.router.lifespan_context(app):
            initial_app_state.db_pool = object()  # type: ignore[assignment]
            transport = ASGITransport(app=app)
            try:
                async with AsyncClient(transport=transport, base_url="http://localhost") as client:
                    yield client
            finally:
                initial_app_state.db_pool = None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def create_mock_app_state(mock_store: AsyncMock, queue: FakeTitleQueue | None) -> MagicMock:
    mock_store.get_user_settings = AsyncMock(return_value=None)
    app_state = MagicMock(spec=AppState)
    app_state.memory_store = mock_store
    app_state.redis = queue
    app_state.db_pool = object()
    return app_state


def set_app_state(mock_app_state: MagicMock, user_id: uuid.UUID) -> None:
    app.state.app_state = mock_app_state
    app.state._test_auth_user_id = user_id


def mock_stream_sse_chat_factory(captured: list[dict[str, Any] | None]):
    async def mock_stream_sse_chat(*, history_messages=None, **kwargs):
        captured.append(history_messages)
        yield 'event: token\ndata: {"type": "token", "data": {"delta": "OK"}}\n\n'
        yield 'event: final\ndata: {"type": "final", "data": {}}\n\n'
        yield 'event: done\ndata: {"type": "done", "data": {"ok": true}}\n\n'

    return mock_stream_sse_chat


def title_attempts(queue: FakeTitleQueue) -> list[dict[str, Any]]:
    return [
        attempt
        for attempt in queue.attempts
        if attempt["args"] and attempt["args"][0] == "generate_title"
    ]


async def post_chat(client: AsyncClient, payload: dict[str, Any]):
    return await client.post(
        "/chat",
        json=payload,
        headers={"Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_precreated_unlocked_empty_draft_enqueues_title_once(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    assert response.status_code == 200
    mock_store.create_conversation.assert_not_awaited()
    mock_store.insert_message.assert_awaited_once()

    attempts = title_attempts(queue)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["args"] == (
        "generate_title",
        str(conversation_id),
        "Hello",
    )
    assert attempt["job_id"] == f"title:{conversation_id}"
    assert attempt["defer_by"] == 0

    # The draft probe must not rely on the stale conversations.message_count
    # column: count_messages reads the authoritative message rows instead.
    mock_store.count_messages.assert_awaited_once_with(conversation_id)
    # Probe (pre-insert) plus history fetch (post-insert).
    assert mock_store.get_recent_messages.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("title_locked", [True, False])
async def test_precreated_named_draft_does_not_enqueue(client, title_locked) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "My chosen title",
            "title_locked": title_locked,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    assert response.status_code == 200
    mock_store.insert_message.assert_awaited_once()
    assert title_attempts(queue) == []
    # A locked title must not even pay for the emptiness probe.
    mock_store.count_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_precreated_locked_null_treated_as_unlocked(client) -> None:
    """Legacy rows may carry NULL title_locked (column added without NOT NULL)."""

    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": None,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    assert response.status_code == 200
    assert len(title_attempts(queue)) == 1


@pytest.mark.asyncio
async def test_nonempty_existing_conversation_is_not_backfilled(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=3)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Second turn",
                "messages": [],
            },
        )

    assert response.status_code == 200
    mock_store.insert_message.assert_awaited_once()
    # No retrospective bulk backfill for already-content conversations.
    assert title_attempts(queue) == []


@pytest.mark.asyncio
async def test_newly_created_chat_keeps_title_enqueue(client) -> None:
    new_conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.create_conversation = AsyncMock(
        return_value={"id": new_conversation_id, "title": "Hello", "title_locked": False}
    )
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {"message": "Hello", "messages": []},
        )

    assert response.status_code == 200
    mock_store.create_conversation.assert_awaited_once()
    create_kwargs = mock_store.create_conversation.await_args.kwargs
    assert create_kwargs["user_id"] == owner
    assert create_kwargs["title"].startswith("Hello")

    attempts = title_attempts(queue)
    assert len(attempts) == 1
    assert attempts[0]["args"] == (
        "generate_title",
        str(new_conversation_id),
        "Hello",
    )
    assert attempts[0]["job_id"] == f"title:{new_conversation_id}"


@pytest.mark.asyncio
async def test_failed_insert_does_not_enqueue(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(side_effect=RuntimeError("database unavailable"))
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    # Graceful degradation: the chat itself still streams.
    assert response.status_code == 200
    mock_store.insert_message.assert_awaited_once()
    # Failed insert must not schedule a paid background title job.
    assert title_attempts(queue) == []


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_break_chat(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    queue = FakeTitleQueue(raise_on_enqueue=RuntimeError("redis unavailable"))
    set_app_state(create_mock_app_state(mock_store, queue), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    response_text = response.text
    assert response.status_code == 200
    mock_store.insert_message.assert_awaited_once()
    assert "event: done" in response_text
    # The failed enqueue attempted exactly once and was absorbed as a warning.
    assert len(queue.attempts) == 1


@pytest.mark.asyncio
async def test_unauthorized_conversation_schedules_nothing(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "Foreign conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.create_conversation = AsyncMock()
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(mock_store, queue), uuid.uuid4())

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    assert response.status_code == 403
    mock_store.insert_message.assert_not_awaited()
    mock_store.create_conversation.assert_not_awaited()
    mock_store.count_messages.assert_not_awaited()
    assert queue.attempts == []


@pytest.mark.asyncio
async def test_draft_probe_failure_preserves_message_without_title_job(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {
        "id": conversation_id,
        "user_id": owner,
        "title": "New conversation",
        "title_locked": False,
        "pipeline": "cloud",
    }
    store.count_messages.side_effect = RuntimeError("test probe failure")
    store.insert_message.return_value = {"id": uuid.uuid4()}
    store.get_recent_messages.return_value = []
    queue = FakeTitleQueue()
    set_app_state(create_mock_app_state(store, queue), owner)
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory([])):
        response = await post_chat(
            client, {"conversation_id": str(conversation_id), "message": "Hello", "messages": []}
        )
    assert response.status_code == 200
    store.insert_message.assert_awaited_once()
    assert title_attempts(queue) == []


@pytest.mark.asyncio
async def test_missing_redis_never_enqueues(client) -> None:
    conversation_id = uuid.uuid4()
    owner = uuid.uuid4()

    mock_store = AsyncMock()
    mock_store.get_conversation = AsyncMock(
        return_value={
            "id": conversation_id,
            "user_id": owner,
            "pipeline": "cloud",
            "title": "New conversation",
            "title_locked": False,
        }
    )
    mock_store.count_messages = AsyncMock(return_value=0)
    mock_store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_store.get_recent_messages = AsyncMock(return_value=[])
    set_app_state(create_mock_app_state(mock_store, None), owner)

    captured: list[dict[str, Any] | None] = []
    with patch("orchestrator.main.stream_sse_chat", mock_stream_sse_chat_factory(captured)):
        response = await post_chat(
            client,
            {
                "conversation_id": f"conv_{conversation_id.hex}",
                "message": "Hello",
                "messages": [],
            },
        )

    assert response.status_code == 200
    mock_store.insert_message.assert_awaited_once()
    # Probe and enqueue are both skipped without a queue.
    mock_store.count_messages.assert_not_awaited()
