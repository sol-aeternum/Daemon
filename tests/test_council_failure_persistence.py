"""Council output must reach the store on every exit path, including aborts.

The council branches stream progress/output frames and then persist them. If the
council stream, the account cleanup, or client cancellation aborts the run, the
events already streamed are still the only record of what the council produced,
so they must be stored with an error status — while the abort itself stays the
terminal signal for the client.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from typing import Any
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from orchestrator import main as main_module
from tests.qualified_compute import install_qualified_compute

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _RecordingStore:
    """Memory-store stand-in that records the messages the endpoint inserts."""

    def __init__(self) -> None:
        self.conversation_id = uuid.uuid4()
        self.inserts: list[dict[str, Any]] = []
        self.assistant_inserted = asyncio.Event()

    @property
    def assistant_inserts(self) -> list[dict[str, Any]]:
        return [insert for insert in self.inserts if insert.get("role") == "assistant"]

    async def create_conversation(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": self.conversation_id, **kwargs}

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        return None

    async def get_user_settings(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        self.inserts.append(kwargs)
        if kwargs.get("role") == "assistant":
            self.assistant_inserted.set()
        return {"id": uuid.uuid4(), **kwargs}


class _FailingAssistantStore(_RecordingStore):
    """Store whose assistant writes fail, to prove persistence never breaks the stream."""

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("role") == "assistant":
            raise RuntimeError("assistant write unavailable")
        return await super().insert_message(**kwargs)


@pytest.mark.asyncio
async def test_cancel_during_successful_final_save_preserves_output(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowStore(_RecordingStore):
        async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
            entered.set()
            await release.wait()
            return await super().insert_message(**kwargs)

    store = SlowStore()
    monkeypatch.setattr(main_module, "account_compute", _pass_through_account_compute)

    async def source():
        yield _council_output_frame()

    async def drive():
        return [
            frame
            async for frame in main_module._stream_council_events(
                db_pool=object(),
                account_user_id=USER_ID,
                source=source,
                store=store,
                conversation_uuid=store.conversation_id,
                user_id=USER_ID,
                conversation_id="conv_test",
                request_id="req_final_save",
                log_message="save failed",
            )
        ]

    task = asyncio.create_task(drive())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.wait_for(store.assistant_inserted.wait(), 5)
    assert len(store.assistant_inserts) == 1
    assert store.assistant_inserts[0]["status"] == "complete"
    assert store.assistant_inserts[0]["content"] == "Council consensus reached."


@pytest.mark.asyncio
async def test_council_error_marks_saved_row_and_terminal_status(client, store, monkeypatch):
    async def source(**kwargs):
        yield main_module.sse(
            "council_error", {"type": "council_error", "data": {"error": "unavailable"}}
        )

    monkeypatch.setattr(main_module, "stream_council", source)
    monkeypatch.setattr(main_module, "account_compute", _pass_through_account_compute)
    response = await client.post("/chat", json={"message": "/council --default Compare options"})
    assert _sse_payloads(response.text, "done")[-1]["data"] == {"status": "error"}
    assert len(store.assistant_inserts) == 1
    assert store.assistant_inserts[0]["status"] == "error"


@pytest_asyncio.fixture
async def store() -> AsyncIterator[_RecordingStore]:
    yield _RecordingStore()


@pytest_asyncio.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, store: _RecordingStore
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    get_settings.cache_clear()

    settings = get_settings()
    app_state = AppState(settings=settings)
    # The lifespan performs real DB work whenever a pool is present, so the pool
    # is attached only after startup; the endpoint itself is exercised with it.
    app_state.db_pool = None  # type: ignore[assignment]
    app_state.memory_store = store  # type: ignore[assignment]
    install_qualified_compute(monkeypatch)

    async def override_settings():
        return settings

    async def override_app_state():
        return app_state

    async def override_auth():
        return AuthenticatedDevice(
            user_id=USER_ID,
            device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        )

    # The chat endpoint reads its state from ``app.state.app_state``, which the
    # lifespan populates via ``init_app_state``; both are wired here so the
    # endpoint sees the same fixture store the assertions inspect.
    monkeypatch.setattr(
        "orchestrator.main.init_app_state",
        AsyncMock(return_value=app_state),
    )
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_app_state] = override_app_state
    app.dependency_overrides[require_device_auth] = override_auth

    try:
        async with app.router.lifespan_context(app):
            app_state.db_pool = object()  # type: ignore[assignment]
            try:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as http_client:
                    yield http_client
            finally:
                app_state.db_pool = None
    finally:
        app.dependency_overrides.clear()


def _council_output_frame() -> str:
    return main_module.sse(
        "council_output",
        {
            "type": "council_output",
            "data": {
                "session_id": "ses_council",
                "section": "consensus",
                "content": "Council consensus reached.",
            },
        },
    )


def _council_progress_frame() -> str:
    return main_module.sse(
        "council_progress",
        {
            "type": "council_progress",
            "data": {"stage": "round_1", "current_round": 1, "total_rounds": 1},
        },
    )


def _sse_payloads(response_text: str, event_name: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for frame in response_text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith(f"event: {event_name}"):
                data = next(
                    (row for row in frame.splitlines() if row.startswith("data: ")),
                    None,
                )
                if data is not None:
                    payloads.append(json.loads(data[len("data: ") :]))
    return payloads


def _event_names(response_text: str) -> list[str]:
    return [
        line[len("event: ") :] for line in response_text.splitlines() if line.startswith("event: ")
    ]


@asynccontextmanager
async def _pass_through_account_compute(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
    yield None


@asynccontextmanager
async def _failing_settlement_account_compute(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
    yield None
    # The council run finished; the account commit on the way out fails.
    raise RuntimeError("account settlement failed")


def _patch_council_source(monkeypatch: pytest.MonkeyPatch, source_attr: str) -> None:
    async def council_source(**_kwargs: Any) -> AsyncIterator[str]:
        yield _council_progress_frame()
        yield _council_output_frame()

    monkeypatch.setattr(main_module, source_attr, council_source)


COUNCIL_PATHS = [
    pytest.param("stream_council", "/council --default Compare options", id="council-command"),
    pytest.param(
        "stream_council_interview_response",
        "/council config:preset=lean, rounds=1",
        id="council-interview-config",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_attr", "message"), COUNCIL_PATHS)
async def test_council_output_is_persisted_when_account_cleanup_fails(
    client: AsyncClient,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
    source_attr: str,
    message: str,
) -> None:
    _patch_council_source(monkeypatch, source_attr)
    monkeypatch.setattr(
        main_module, "account_compute", _failing_settlement_account_compute, raising=True
    )

    response = await client.post("/chat", json={"message": message})

    assert response.status_code == 200
    names = _event_names(response.text)
    # The council frames streamed before the accounting failure still reached the
    # client, and the abort is terminal.
    assert "council_output" in names
    assert names[-1] == "done"
    assert _sse_payloads(response.text, "error")[0]["data"]["code"] == "internal_error"
    assert _sse_payloads(response.text, "done")[-1]["data"] == {"ok": False}
    assert "council_error" not in names

    assistant_inserts = store.assistant_inserts
    assert len(assistant_inserts) == 1
    insert = assistant_inserts[0]
    assert insert["status"] == "error"
    assert insert["content"] == "Council consensus reached."
    assert insert["model"] == "council"
    assert insert["conversation_id"] == store.conversation_id
    assert insert["user_id"] == USER_ID
    persisted_events = insert["metadata"]["council_events"]
    assert [event["type"] for event in persisted_events] == ["council_progress", "council_output"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_attr", "message"), COUNCIL_PATHS)
async def test_council_output_is_persisted_once_when_the_run_succeeds(
    client: AsyncClient,
    store: _RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
    source_attr: str,
    message: str,
) -> None:
    _patch_council_source(monkeypatch, source_attr)
    monkeypatch.setattr(main_module, "account_compute", _pass_through_account_compute)

    response = await client.post("/chat", json={"message": message})

    assert response.status_code == 200
    assert _sse_payloads(response.text, "done")[-1]["data"] == {"status": "completed"}

    assistant_inserts = store.assistant_inserts
    assert len(assistant_inserts) == 1
    insert = assistant_inserts[0]
    assert insert["status"] == "complete"
    assert insert["content"] == "Council consensus reached."
    assert insert["tool_results"][0]["name"] == "council_events"
    assert insert["tool_results"][0]["result"]["events"] == insert["metadata"]["council_events"]


@pytest.mark.asyncio
async def test_council_persistence_failure_does_not_fail_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FailingAssistantStore()
    conversation_uuid = store.conversation_id

    async def council_source(**_kwargs: Any) -> AsyncIterator[str]:
        yield _council_progress_frame()
        yield _council_output_frame()

    monkeypatch.setattr(main_module, "account_compute", _pass_through_account_compute)

    frames = main_module._stream_council_events(
        db_pool=object(),
        account_user_id=USER_ID,
        source=council_source,
        store=store,
        conversation_uuid=conversation_uuid,
        user_id=USER_ID,
        conversation_id=f"conv_{conversation_uuid}",
        request_id="req_store_failure",
        log_message="Failed to persist council command output",
    )

    streamed = [frame async for frame in frames]

    assert len(store.assistant_inserts) == 0
    assert _sse_payloads("".join(streamed), "done")[0]["data"] == {"status": "completed"}


@pytest.mark.asyncio
async def test_streamed_council_events_are_persisted_when_the_request_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    conversation_uuid = store.conversation_id
    source_blocked = asyncio.Event()
    never_resumed = asyncio.Event()

    async def council_source(**_kwargs: Any) -> AsyncIterator[str]:
        yield _council_output_frame()
        source_blocked.set()
        await never_resumed.wait()

    monkeypatch.setattr(main_module, "account_compute", _pass_through_account_compute)

    frames = main_module._stream_council_events(
        db_pool=object(),
        account_user_id=USER_ID,
        source=council_source,
        store=store,
        conversation_uuid=conversation_uuid,
        user_id=USER_ID,
        conversation_id=f"conv_{conversation_uuid}",
        request_id="req_cancelled",
        log_message="Failed to persist council command output",
    )
    streamed: list[str] = []

    async def drive() -> None:
        async for frame in frames:
            streamed.append(frame)

    task = asyncio.create_task(drive())
    await asyncio.wait_for(source_blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(store.assistant_inserted.wait(), timeout=5)
    assert len(streamed) == 1
    assistant_inserts = store.assistant_inserts
    assert len(assistant_inserts) == 1
    assert assistant_inserts[0]["status"] == "error"
    assert assistant_inserts[0]["content"] == "Council consensus reached."
    assert [event["type"] for event in assistant_inserts[0]["metadata"]["council_events"]] == [
        "council_output"
    ]
