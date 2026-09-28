"""Chat admission must qualify the exact workload profile, on both surfaces.

Round-7 review finding: automatic admission accepted *any* approved text route,
so a deployment whose approved routes cannot serve the request's own profile
still answered 200 and only failed later — after persistence, inside the stream.
These tests pin the contract:

* automatic admission is decided by the exact profile the classifier selects,
  and by the council profile for a council run;
* an unrelated approved route buys nothing;
* an exact manual selection is still admitted on its own route;
* admission stays cheap: it reserves no account budget and calls no provider,
  and it never preselects a route for dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
import uuid

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from orchestrator import main as main_module
from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import get_settings
from orchestrator.db import AppState, get_app_state
from orchestrator.main import app
from tests.qualified_compute import install_qualified_compute

# Placements taken from the real workload routing metadata, so these tests
# exercise the shipped profiles rather than a parallel fiction:
#   Luna    -> routine, research, background, council (not reasoning)
#   GLM-5.3 -> routine, research, reasoning, council
#   kimi    -> approved, but in no profile group at all
LUNA = "openrouter/openai/gpt-6-luna"
GLM = "openrouter/z-ai/glm-5.3"
UNROUTED = "openrouter/moonshotai/kimi-k2.5"
EXPLICIT = "openrouter/test/explicit-model"

NATIVE = "/chat"
COMPAT = "/v1/chat/completions"


class _RecordingStore:
    """Records every persisted row so admission ordering is observable."""

    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    @property
    def assistant_rows(self) -> list[dict[str, Any]]:
        return [row for row in self.inserts if row.get("role") == "assistant"]

    async def create_conversation(
        self, *, user_id: uuid.UUID, pipeline: str, title: str
    ) -> dict[str, Any]:
        return {"id": uuid.uuid4()}

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        return None

    async def get_recent_messages(
        self, conversation_id: uuid.UUID, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return []

    async def get_user_settings(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        self.inserts.append(kwargs)
        return {"id": uuid.uuid4()}

    async def update_message(self, **kwargs: Any) -> None:
        return None


class _Harness:
    """One authenticated deployment whose approved routes the test chooses."""

    def __init__(self, client: AsyncClient, store: _RecordingStore) -> None:
        self.client = client
        self.store = store
        self.dispatched: list[dict[str, Any]] = []
        self.account_scopes: list[str | None] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.council_streams: list[str] = []

    async def post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        return await self.client.post(endpoint, json=payload)

    def assert_no_servable_work_started(self) -> None:
        """Nothing persisted, streamed or dispatched may precede the refusal."""
        assert self.store.assistant_rows == []
        assert self.dispatched == []
        assert self.account_scopes == []
        assert self.llm_calls == []


def _auto_payload(endpoint: str, message: str) -> dict[str, Any]:
    if endpoint == NATIVE:
        return {"message": message, "model": "auto"}
    return {"model": "auto", "messages": [{"role": "user", "content": message}], "stream": True}


def _explicit_payload(endpoint: str, message: str, model: str) -> dict[str, Any]:
    if endpoint == NATIVE:
        return {"message": message, "model": model}
    return {"model": model, "messages": [{"role": "user", "content": message}], "stream": True}


@pytest_asyncio.fixture
async def harness_factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """Build an authenticated client for a chosen set of approved routes."""
    clients: list[AsyncClient] = []
    original_app_state = getattr(app.state, "app_state", None)
    original_settings = getattr(app.state, "settings", None)

    async def build(*, models: tuple[str, ...]) -> _Harness:
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("REDIS_URL", "")
        monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
        monkeypatch.setenv("MOCK_LLM", "true")
        get_settings.cache_clear()

        settings = get_settings()
        app_state = AppState(settings=settings)
        app_state.db_pool = object()  # type: ignore[assignment]
        store = _RecordingStore()
        app_state.memory_store = store  # type: ignore[assignment]
        install_qualified_compute(monkeypatch, models=models)

        async def override_settings() -> Any:
            return get_settings()

        async def override_app_state() -> Any:
            return app.state.app_state

        async def override_auth(_request: Request) -> AuthenticatedDevice:
            return AuthenticatedDevice(
                user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                device_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            )

        app.dependency_overrides[get_settings] = override_settings
        app.dependency_overrides[get_app_state] = override_app_state
        app.dependency_overrides[require_device_auth] = override_auth
        app.state.app_state = app_state
        app.state.settings = settings

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        harness = _Harness(client, store)

        real_frames = main_module._account_chat_frames
        real_account_compute = main_module.account_compute
        real_chat = main_module.stream_sse_chat

        async def capture_frames(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
            harness.dispatched.append(
                {
                    "profile": kwargs.get("profile"),
                    "auto_route": kwargs.get("auto_route"),
                    "actual_model": kwargs.get("actual_model"),
                }
            )
            async for frame in real_frames(*args, **kwargs):
                yield frame

        @asynccontextmanager
        async def capture_scope(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
            harness.account_scopes.append(kwargs.get("profile"))
            async with real_account_compute(*args, **kwargs):
                yield

        async def capture_chat(**kwargs: Any) -> AsyncIterator[str]:
            harness.llm_calls.append(kwargs)
            async for frame in real_chat(**kwargs):
                yield frame

        async def capture_council(**kwargs: Any) -> AsyncIterator[str]:
            harness.council_streams.append(str(kwargs.get("user_message", "")))
            yield main_module.sse(
                "council_done",
                {"type": "council_done", "data": {"status": "completed"}},
            )

        monkeypatch.setattr(main_module, "_account_chat_frames", capture_frames)
        monkeypatch.setattr(main_module, "account_compute", capture_scope)
        monkeypatch.setattr(main_module, "stream_sse_chat", capture_chat)
        monkeypatch.setattr(main_module, "stream_council", capture_council)
        monkeypatch.setattr(main_module, "stream_council_interview_response", capture_council)
        return harness

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        app.state.app_state = original_app_state
        app.state.settings = original_settings


# --------------------------------------------------------------------------- #
# The review finding itself
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [NATIVE, COMPAT])
async def test_unqualified_profile_is_refused_before_any_work(
    harness_factory: Any, endpoint: str
) -> None:
    """An approved route no profile may use must not buy a 200."""
    harness = await harness_factory(models=(UNROUTED,))

    response = await harness.post(endpoint, _auto_payload(endpoint, "hello"))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "route_unavailable"
    assert "event:" not in response.text
    assert "text/event-stream" not in response.headers["content-type"]
    harness.assert_no_servable_work_started()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [NATIVE, COMPAT])
async def test_unapproved_routine_model_refuses_routine_and_keeps_reasoning(
    harness_factory: Any, endpoint: str
) -> None:
    """Luna's placement is the whole routine group: losing it is a truth, not a fallback."""
    harness = await harness_factory(models=(GLM, UNROUTED))

    routine = await harness.post(endpoint, _auto_payload(endpoint, "hello"))
    assert routine.status_code == 503
    assert routine.json()["detail"]["code"] == "route_unavailable"
    harness.assert_no_servable_work_started()

    reasoning = await harness.post(
        endpoint, _auto_payload(endpoint, "compare postgres and mysql trade-offs")
    )
    assert reasoning.status_code == 200, reasoning.text
    assert harness.dispatched == [
        {"profile": "reasoning", "auto_route": True, "actual_model": "auto"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [NATIVE, COMPAT])
@pytest.mark.parametrize(
    ("message", "profile"),
    [
        ("hello", "routine"),
        ("search for recent weather reports", "research"),
        ("compare postgres and mysql trade-offs", "reasoning"),
    ],
)
async def test_qualified_profile_is_admitted_without_preselecting_a_route(
    harness_factory: Any, endpoint: str, message: str, profile: str
) -> None:
    harness = await harness_factory(models=(LUNA, GLM))

    response = await harness.post(endpoint, _auto_payload(endpoint, message))

    assert response.status_code == 200, response.text
    # Admission returns "auto": the profile is qualified, not pinned to its
    # cheapest candidate. The dispatch profile is the classified one.
    assert harness.dispatched == [{"profile": profile, "auto_route": True, "actual_model": "auto"}]
    assert harness.account_scopes == [profile]


# --------------------------------------------------------------------------- #
# Council runs on the council profile, not on the classified one
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_council_admission_uses_the_council_profile(harness_factory: Any) -> None:
    """ "/council --default Compare options" classifies as reasoning, but streams as council."""
    harness = await harness_factory(models=(LUNA,))

    response = await harness.post(
        NATIVE, {"message": "/council --default Compare options", "model": "auto"}
    )

    assert response.status_code == 200, response.text
    assert harness.council_streams == ["/council --default Compare options"]
    assert harness.account_scopes == ["council"]


@pytest.mark.asyncio
async def test_council_config_interview_admission_uses_the_council_profile(
    harness_factory: Any,
) -> None:
    harness = await harness_factory(models=(LUNA,))

    response = await harness.post(
        NATIVE, {"message": "/council config: preset=deep", "model": "auto"}
    )

    assert response.status_code == 200, response.text
    assert harness.account_scopes == ["council"]


@pytest.mark.asyncio
async def test_council_is_refused_when_only_routine_is_qualified(harness_factory: Any) -> None:
    """A deployment with no council-qualified route cannot run a council round."""
    harness = await harness_factory(models=(UNROUTED,))

    response = await harness.post(
        NATIVE, {"message": "/council --default Compare options", "model": "auto"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "route_unavailable"
    assert harness.council_streams == []
    harness.assert_no_servable_work_started()


# --------------------------------------------------------------------------- #
# Explicit selection keeps its own contract
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [NATIVE, COMPAT])
async def test_explicit_selection_is_unaffected_by_profile_qualification(
    harness_factory: Any, endpoint: str
) -> None:
    harness = await harness_factory(models=(UNROUTED, EXPLICIT))

    response = await harness.post(endpoint, _explicit_payload(endpoint, "hello", EXPLICIT))

    assert response.status_code == 200, response.text
    assert harness.dispatched == [
        {"profile": "routine", "auto_route": False, "actual_model": EXPLICIT}
    ]


@pytest.mark.asyncio
async def test_explicit_selection_still_refuses_an_unapproved_model(harness_factory: Any) -> None:
    harness = await harness_factory(models=(LUNA,))

    response = await harness.post(
        NATIVE, {"message": "hello", "model": "openrouter/test/not-approved"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "route_unavailable"
    harness.assert_no_servable_work_started()


# --------------------------------------------------------------------------- #
# Admission stays cheap, and a qualified profile is not a selected route
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_qualified_profile_check_answers_availability_not_selection(
    harness_factory: Any,
) -> None:
    """The check returns no route at all, and an unknown workload fails closed."""
    await harness_factory(models=(LUNA,))

    assert main_module._qualified_profile_available("routine") is True
    assert main_module._approved_chat_model(None, profile="routine") == "auto"
    assert main_module._qualified_profile_available("no-such-profile") is False


@pytest.mark.asyncio
async def test_admission_reserves_no_budget_and_calls_no_provider(harness_factory: Any) -> None:
    harness = await harness_factory(models=(UNROUTED,))

    response = await harness.post(NATIVE, _auto_payload(NATIVE, "hello"))

    assert response.status_code == 503
    # No account scope was opened, so nothing was reserved, and no completion
    # was ever attempted.
    assert harness.account_scopes == []
    assert harness.llm_calls == []
    assert harness.store.inserts == []
