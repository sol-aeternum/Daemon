"""Approved controls: legacy identity, independent instructions, attempt reconstruction.

Synthetic acceptance verifies forwarding/HTTP conflict mapping, not SQL isolation.
No real providers, source services, Redis or PostgreSQL are used.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import uuid

from fastapi import HTTPException, Request
import pytest

from orchestrator.answer_controls import append_answer_controls
from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import Settings
from orchestrator.db import AppState
from orchestrator.model_router import ModelDecision
from orchestrator.models import ChatRequest
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import Claim


BASE = {
    "key": "fictional-key",
    "conversation_id": None,
    "message": "hello",
    "attachments": None,
    "model": None,
    "provider": None,
    "metadata": None,
    "disable_memory_write": False,
}
LEGACY = (
    '{"attachments":[],"content_parts":[],"conversation_id":null,'
    '"disable_memory_write":false,"message":"hello","metadata":{},'
    '"model":"auto","provider":null}'
)


@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"answer_detail": None},
        {"research_effort": None},
        {"answer_detail": None, "research_effort": None},
    ],
)
def test_legacy_canonical_bytes_and_digest_are_unchanged(controls):
    actual = chat_request_fingerprint(**BASE, **controls)
    assert actual.canonical == LEGACY
    assert (
        actual.digest
        == hmac.new(
            b"fictional-key", b"daemon.task.request.v1\n" + LEGACY.encode(), hashlib.sha256
        ).hexdigest()
    )
    assert append_answer_controls("unaltered prompt \n", **controls) == "unaltered prompt \n"


@pytest.mark.parametrize("detail", ["brief", "balanced", "detailed"])
@pytest.mark.parametrize("effort", ["auto", "research"])
def test_explicit_controls_are_independent_identity_and_instructions(detail, effort):
    fields = {"answer_detail": detail, "research_effort": effort}
    assert ChatRequest(message="hello", **fields).answer_detail == detail
    actual = chat_request_fingerprint(**BASE, **fields)
    assert json.loads(actual.canonical) == {**json.loads(LEGACY), **fields}
    assert actual.digest != chat_request_fingerprint(**BASE).digest
    assert actual.digest != chat_request_fingerprint(**BASE, answer_detail=detail).digest
    assert actual.digest != chat_request_fingerprint(**BASE, research_effort=effort).digest
    prompt = append_answer_controls("base", **fields)
    assert f"Answer detail: {detail}." in prompt
    assert f"Research effort: {effort}." in prompt
    assert "do not expand budgets, permissions, tools, model access" in prompt


@pytest.mark.asyncio
async def test_durable_acceptance_forwards_controls_and_existing_conflict(monkeypatch):
    import orchestrator.main as main
    from orchestrator.tasks.store import IdempotencyConflict

    seen = []
    accepted = SimpleNamespace(created=False, task_id=uuid.uuid4())

    async def accept(**kwargs):
        if seen and kwargs["request_canonical"] != seen[0]["request_canonical"]:
            raise IdempotencyConflict()
        seen.append(kwargs)
        return accepted

    store = SimpleNamespace(accept=accept)
    monkeypatch.setattr(main, "task_acceptor", lambda _: store)
    monkeypatch.setattr(main, "task_store", lambda _: store)
    monkeypatch.setattr(main, "validate_and_get_pepper", lambda _: "fictional-key")
    monkeypatch.setattr(main, "_observe_task_response", lambda *args: accepted)
    auth = AuthenticatedDevice(
        user_id=uuid.uuid4(), device_id=uuid.uuid4(), session_id=uuid.uuid4()
    )

    async def send(**controls):
        return await main._durable_chat(
            payload=ChatRequest(message="hello", **controls),
            request=Request({"type": "http", "headers": [(b"idempotency-key", b"same-key")]}),
            settings=cast(Any, Settings)(_env_file=None),
            app_state=cast(AppState, SimpleNamespace(memory_store=object(), redis=None)),
            auth=auth,
            request_id="synthetic",
            user_message="hello",
            prepared_user_content="hello",
            pipeline="cloud",
            model_decision=ModelDecision(tier="routine", model="synthetic", reason="fixture"),
            selected_model="auto",
            actual_model="synthetic",
            routing_info={},
            title_generation=None,
        )

    assert await send(answer_detail="brief", research_effort="research") is accepted
    assert await send(answer_detail="brief", research_effort="research") is accepted
    assert seen[0]["task_input"]["answer_detail"] == "brief"
    assert seen[0]["task_input"]["research_effort"] == "research"
    for changed in [
        dict(answer_detail="detailed", research_effort="research"),
        dict(answer_detail="brief", research_effort="auto"),
        {},
    ]:
        with pytest.raises(HTTPException) as error:
            await send(**changed)
        assert error.value.status_code == 409
        assert isinstance(error.value.detail, dict)
        assert cast(dict[str, Any], error.value.detail)["code"] == "idempotency_conflict"
    seen.clear()
    await send(answer_detail=None, research_effort=None)
    assert "answer_detail" not in seen[0]["task_input"]
    assert "research_effort" not in seen[0]["task_input"]
    assert seen[0]["request_canonical"] == LEGACY
    await send()  # omitted and null replay, without historical backfill


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "controls", [{}, {"answer_detail": "brief", "research_effort": "research"}]
)
async def test_worker_reconstructs_controls_from_accepted_input(monkeypatch, controls):
    from orchestrator.tasks.runner import _system_prompt

    monkeypatch.setattr("orchestrator.skills_store.build_skill_index", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "orchestrator.memory.injection.build_memory_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        "orchestrator.memory.injection.assemble_system_prompt", AsyncMock(return_value="base")
    )
    prompt, _ = await _system_prompt(
        SimpleNamespace(get_user_settings=AsyncMock(return_value={})),
        None,
        cast(
            Claim,
            SimpleNamespace(
                user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), task_input=controls
            ),
        ),
    )
    assert prompt == append_answer_controls("base", **controls)
