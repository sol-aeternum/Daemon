"""Synthetic provider/gate tests: no Docker or paid inference."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from scripts.durable_restart_fixture import (
    ANSWERS,
    fake_completion,
    fake_effect,
    install,
    validate_isolation,
)


def test_fixture_refuses_without_disposable_permit(monkeypatch, tmp_path: Path):
    for key in os.environ:
        if key.endswith("API_KEY") or key == "FAL_KEY":
            monkeypatch.delenv(key)
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="permit"):
        validate_isolation(tmp_path)


def test_fixture_refuses_live_credentials(monkeypatch, tmp_path: Path):
    (tmp_path / "permit").touch()
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-for-refusal-test")
    with pytest.raises(RuntimeError, match="credentials"):
        validate_isolation(tmp_path)


def test_fixture_requires_internal_database_and_explicit_selection(monkeypatch, tmp_path: Path):
    (tmp_path / "permit").touch()
    for key in os.environ:
        if key.endswith("API_KEY") or key == "FAL_KEY":
            monkeypatch.delenv(key)
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    validate_isolation(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic-invalid/external")
    with pytest.raises(RuntimeError, match="externally"):
        validate_isolation(tmp_path)
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("POSTGRES_HOST", "external.invalid")
    with pytest.raises(RuntimeError, match="externally"):
        validate_isolation(tmp_path)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("MOCK_LLM", "false")
    with pytest.raises(RuntimeError, match="explicitly"):
        validate_isolation(tmp_path)


@pytest.mark.asyncio
async def test_first_b_dispatch_stays_gated_after_partial_then_recovery_finishes(tmp_path: Path):
    params = {"stream": True, "messages": [{"role": "user", "content": "drill B"}]}
    stream = await fake_completion(tmp_path, **params)
    first = await anext(stream)
    assert first["choices"][0]["delta"]["content"] == ANSWERS["B"][:8]
    pending = asyncio.create_task(anext(stream))
    # A deterministic scheduler yield verifies it cannot finish without release.
    await asyncio.sleep(0)
    assert not pending.done()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await stream.aclose()
    recovered = await fake_completion(tmp_path, **params)
    chunks = [chunk async for chunk in recovered]
    text = "".join(
        chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk["choices"]
    )
    assert text == ANSWERS["B"]
    assert len((tmp_path / "dispatch-B").read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_material_fixture_records_only_explicit_calls(tmp_path: Path):
    result = json.loads(await fake_effect(tmp_path))
    assert result == {"success": True, "performed": True}
    assert (tmp_path / "effects").read_text().splitlines() == ["performed"]


def test_install_selects_only_synthetic_transport_without_credentials(monkeypatch, tmp_path: Path):
    from orchestrator import compute_runtime
    from orchestrator.config import Settings, get_settings
    from orchestrator.tasks import runner
    from orchestrator.tools.notification import NotificationSendTool

    for key in os.environ:
        if key.endswith("API_KEY") or key == "FAL_KEY":
            monkeypatch.delenv(key)
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("DURABLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "permit").touch()
    # Restore the normal test/application contract after explicit fixture use.
    for target, name in (
        (compute_runtime.litellm, "acompletion"),
        (runner, "_publish"),
        (NotificationSendTool, "execute"),
        (Settings, "get_provider_config"),
        (get_settings(), "mock_llm"),
    ):
        monkeypatch.setattr(target, name, getattr(target, name))
    original = compute_runtime.litellm.acompletion
    install(tmp_path)
    assert compute_runtime.litellm.acompletion is not original
    assert get_settings().mock_llm is False
    provider = get_settings().get_provider_config()
    assert provider.requires_auth is False
    assert not provider.api_key
    assert (tmp_path / "installed").read_text() == "guarded synthetic transport\n"
