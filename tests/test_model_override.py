from __future__ import annotations

from collections.abc import AsyncIterator
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
from orchestrator import model_routing
from tests.qualified_compute import install_qualified_compute

pytestmark = pytest.mark.usefixtures("unpaced_mock_stream")

# The stand-in deployment must qualify the profiles these tests dispatch under,
# so admission is satisfied by the workload the classifier actually selected:
# Luna serves routine/research, GLM-5.3 serves reasoning.
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


def _payload(endpoint: str, message: str, model: str) -> dict[str, object]:
    if endpoint == "/chat":
        return {"message": message, "model": model}
    return {"model": model, "messages": [{"role": "user", "content": message}], "stream": True}


async def _observed_dispatch(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload: dict[str, object],
) -> list[tuple[str, bool]]:
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()
    actual_frames = main_module._account_chat_frames
    observed: list[tuple[str, bool]] = []

    async def capture(*args, **kwargs):
        observed.append((kwargs["profile"], kwargs["auto_route"]))
        async for frame in actual_frames(*args, **kwargs):
            yield frame

    monkeypatch.setattr(main_module, "_account_chat_frames", capture)
    response = await client.post(endpoint, json=payload)
    assert response.status_code == 200
    return observed


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/chat", "/v1/chat/completions"])
@pytest.mark.parametrize(
    "message",
    ["hello", "search for recent weather reports", "research and compare these vendors"],
)
async def test_explicit_model_runs_under_routine_scope_on_both_endpoints(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, endpoint: str, message: str
) -> None:
    """An explicit model is exact and its preset does not depend on wording (D2)."""
    observed = await _observed_dispatch(
        client, monkeypatch, endpoint, _payload(endpoint, message, "openrouter/test/explicit-model")
    )
    assert observed == [("routine", False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/chat",
            {
                "message": "",
                "model": "auto",
                "attachments": [
                    {"kind": "text", "name": "notes.txt", "text_content": "compare the totals"}
                ],
            },
        ),
        ("/v1/chat/completions", _payload("/v1/chat/completions", "", "auto")),
    ],
)
async def test_upload_without_text_is_not_classified_from_default_text(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload: dict[str, object],
) -> None:
    """The server's default upload text is model-facing only, never a workload signal."""
    observed = await _observed_dispatch(client, monkeypatch, endpoint, payload)
    assert observed == [("routine", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/chat", "/v1/chat/completions"])
async def test_upload_with_analytic_instruction_routes_the_same_on_both_endpoints(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    payload = _payload(endpoint, "analyze this contract", "auto")
    if endpoint == "/chat":
        payload["attachments"] = [
            {"kind": "text", "name": "contract.txt", "text_content": "Terms."}
        ]
    observed = await _observed_dispatch(client, monkeypatch, endpoint, payload)
    assert observed == [("reasoning", True)]


def test_explicit_routine_scope_uses_the_models_default_preset() -> None:
    """D2's effect: wording no longer selects a profile preset for an explicit pick."""
    opus = "openrouter/anthropic/claude-opus-5.5"
    assert model_routing.model_parameter_presets(opus, "routine") == {"reasoning_effort": "high"}
    assert model_routing.model_parameter_presets(opus, "research") == {"reasoning_effort": "xhigh"}


# --------------------------------------------------------------------------- O4
from orchestrator import daemon as daemon_module  # noqa: E402
from orchestrator.tools.completion import _apply_caller_controls  # noqa: E402  # pyright: ignore[reportPrivateUsage]


def _capture_tool_loop(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("DEFAULT_PROVIDER", "openrouter")
    get_settings.cache_clear()
    calls: list[dict] = []

    async def fake_completion_with_tools(*_args, **kwargs):
        calls.append(kwargs)
        yield {"type": "content_delta", "content": "ok"}
        yield {"type": "done"}

    monkeypatch.setattr(daemon_module, "create_default_registry", lambda **_kwargs: object())
    monkeypatch.setattr(daemon_module, "completion_with_tools", fake_completion_with_tools)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_compatibility_endpoint_replays_the_conversation_and_caps_output(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    calls = _capture_tool_loop(monkeypatch)
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": stream,
            "max_tokens": 64,
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "My name is Ada."},
                {"role": "assistant", "content": "Hello Ada."},
                {"role": "user", "content": "What is my name?"},
            ],
        },
    )
    assert response.status_code == 200
    (call,) = calls
    turns = [(m["role"], m["content"]) for m in call["messages"] if m["role"] != "system"]
    assert turns[-3:] == [
        ("user", "My name is Ada."),
        ("assistant", "Hello Ada."),
        ("user", "What is my name?"),
    ]
    assert call["max_output_tokens"] == 64
    assert call["call_overrides"] is None


@pytest.mark.asyncio
async def test_compatibility_sampling_is_forwarded_only_for_explicit_models(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_tool_loop(monkeypatch)
    for model in ("auto", "openrouter/test/explicit-model"):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "stream": True,
                "temperature": 0.2,
                "stop": ["END"],
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 200
    automatic, explicit = calls
    assert automatic["call_overrides"] is None
    assert explicit["call_overrides"] == {"temperature": 0.2, "stop": ["END"]}


@pytest.mark.asyncio
async def test_compatibility_defaults_are_not_treated_as_caller_intent(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_tool_loop(monkeypatch)
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "openrouter/test/explicit-model",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert calls[0]["call_overrides"] is None
    assert calls[0]["max_output_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "code"),
    [({"n": 2}, "unsupported_parameter"), ({"max_tokens": 0}, "invalid_parameter")],
)
async def test_compatibility_unsupported_parameters_are_refused_not_ignored(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, extra: dict, code: str
) -> None:
    _capture_tool_loop(monkeypatch)
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}], **extra},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code


def test_caller_output_cap_only_lowers_the_loop_budget() -> None:
    params: dict = {"max_tokens": 4_000}
    _apply_caller_controls(params, 64, None)
    assert params["max_tokens"] == 64
    params = {"max_tokens": 32}
    _apply_caller_controls(params, 64, None)
    assert params["max_tokens"] == 32
    params = {}
    _apply_caller_controls(params, 64, {"temperature": 0.2})
    assert params == {"max_tokens": 64, "temperature": 0.2}
