"""Reflection must never disclose unknown/local context to cloud inference."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED
from orchestrator.tools import memory_reflect as reflect
from orchestrator.tools.builtin import create_default_registry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    ["local", "unknown", "missing", "wrong-owner", "no-id", "bad-id", "malformed", "CLOUD"],
)
async def test_registry_reflection_denies_before_any_inference(monkeypatch, context):
    owner, cid = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {
        "user_id": uuid.uuid4() if context == "wrong-owner" else owner,
        "pipeline": "cloud" if context == "wrong-owner" else context,
    }
    if context == "missing":
        store.get_conversation.return_value = None
    if context == "malformed":
        store.get_conversation.return_value = []
    calls = [
        AsyncMock(side_effect=AssertionError("denied context must not dispatch")) for _ in range(3)
    ]
    for name, mock in zip(
        ("embed_query_with_metadata", "retrieve_memories_for_text", "guarded_completion"), calls
    ):
        monkeypatch.setattr(reflect, name, mock)
    registry = create_default_registry(
        memory_store=store,
        user_id=owner,
        conversation_id=None
        if context == "no-id"
        else "invalid"
        if context == "bad-id"
        else str(cid),
    )
    tool = registry.get("memory_reflect")
    assert isinstance(tool, reflect.MemoryReflectTool)
    result = await tool.execute(
        topic="private", pipeline="cloud", include_local=True, conversation_id=str(cid)
    )
    assert "only in a verified cloud conversation" in result
    for mock in calls:
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible", [True, False])
async def test_cloud_reflection_filters_sources_before_synthesis(monkeypatch, eligible):
    owner, cid = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {"user_id": owner, "pipeline": "cloud"}
    embed = AsyncMock(
        return_value=SimpleNamespace(embedding=[1.0], storage_model="test", model="test")
    )
    rows = [
        {"content": "PRIVATE_SENTINEL", "local_only": True},
        {"content": "UNCLASSIFIED_SENTINEL"},
    ]
    if eligible:
        rows.append({"content": "PUBLIC_SENTINEL", "local_only": False})
    retrieve = AsyncMock(return_value=rows)
    synth = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="reflection"))]
        )
    )
    settings = MagicMock()
    settings.get_provider_config.return_value.requires_auth = False
    monkeypatch.setattr(reflect, "get_settings", lambda: settings)
    monkeypatch.setattr(reflect, "embed_query_with_metadata", embed)
    monkeypatch.setattr(reflect, "retrieve_memories_for_text", retrieve)
    monkeypatch.setattr(reflect, "guarded_completion", synth)
    tool = create_default_registry(memory_store=store, user_id=owner, conversation_id=str(cid)).get(
        "memory_reflect"
    )
    assert isinstance(tool, reflect.MemoryReflectTool)
    result = await tool.execute(topic="fictional")
    store.get_conversation.assert_awaited_once_with(cid)
    assert retrieve.await_args is not None
    assert retrieve.await_args.kwargs["include_local"] is False
    if eligible:
        assert result == "reflection"
        assert synth.await_args is not None
        text = synth.await_args.kwargs["messages"][1]["content"]
        assert "PUBLIC_SENTINEL" in text
        assert "PRIVATE_SENTINEL" not in text and "UNCLASSIFIED_SENTINEL" not in text
    else:
        assert "No relevant memories" in result
        synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_settlement_failure_stops_reflection(monkeypatch):
    owner, cid = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_conversation.return_value = {"user_id": owner, "pipeline": "cloud"}
    error = ComputeUnavailable("settlement_failed", "synthetic", category=FAILURE_SETTLEMENT_FAILED)
    monkeypatch.setattr(reflect, "embed_query_with_metadata", AsyncMock(side_effect=error))
    retrieve, synth = AsyncMock(), AsyncMock()
    monkeypatch.setattr(reflect, "retrieve_memories_for_text", retrieve)
    monkeypatch.setattr(reflect, "guarded_completion", synth)
    with pytest.raises(ComputeUnavailable) as caught:
        await reflect.MemoryReflectTool(store, owner, conversation_id=cid).execute(
            topic="fictional"
        )
    assert caught.value is error
    retrieve.assert_not_awaited()
    synth.assert_not_awaited()
