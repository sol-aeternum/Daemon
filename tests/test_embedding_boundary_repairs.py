"""Offline regressions for the bounded embedding activation review repairs."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.compute_runtime import ComputeUnavailable, FAILURE_SETTLEMENT_FAILED
from orchestrator.memory import dreaming, injection
from orchestrator.memory.embedding import EmbeddingConfigurationError


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["", "azure-openrouter-small-1024", "missing"])
async def test_bootstrap_checks_only_selected_embedding_candidate(monkeypatch, selector):
    from scripts import attest_inference_routes as script
    from orchestrator.entitlements.policy import load_inference_policy

    policy = load_inference_policy()
    settings = SimpleNamespace(database_url="postgresql://fictional", embedding_route_id=selector)
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "apply_resolved_database_url", lambda settings: None)
    monkeypatch.setattr(script, "load_inference_policy", lambda: policy)
    pool = AsyncMock()
    connect = AsyncMock(return_value=pool)
    monkeypatch.setattr(script.asyncpg, "create_pool", connect)
    monkeypatch.setattr(script.attestation, "refresh", AsyncMock())
    check = AsyncMock(return_value={})
    monkeypatch.setattr(script.attestation, "run_check", check)
    selected = []

    def admission(routes, requirements, *, now):
        selected.extend(routes)
        return 1, {}

    monkeypatch.setattr(script.attestation, "bootstrap_status", admission)
    assert await script.main() == 1
    if selector == "missing":
        connect.assert_not_awaited()
        return
    assert [route.route_id for route in selected] == [
        *policy.routes,
        *([selector] if selector else []),
    ]
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("locality", [True, None, "false"])
async def test_direct_dream_cluster_refuses_noncloud_sources(monkeypatch, locality):
    provider = AsyncMock()
    monkeypatch.setattr(dreaming, "guarded_completion", provider)
    with pytest.raises(EmbeddingConfigurationError):
        await dreaming.dream_on_cluster([{"content": "fictional", "local_only": locality}])
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_dreaming_filters_local_and_propagates_accounting_failure(monkeypatch):
    store = AsyncMock()
    owner = uuid.uuid4()
    cloud = {
        "id": uuid.uuid4(),
        "local_only": False,
        "content": "fictional",
        "memory_slot": "work.editor",
    }
    store.get_dream_candidate_memories.return_value = [
        {**cloud, "local_only": True},
        {**cloud, "local_only": None},
        cloud,
    ]
    store.get_dream_runs.return_value = []
    monkeypatch.setattr(
        dreaming,
        "get_settings",
        lambda: SimpleNamespace(dreaming_enabled=True, dream_min_cluster_size=1),
    )
    synthesize = AsyncMock(
        return_value=(
            [{"content": "User likes editors", "source_memory_ids": [str(cloud["id"])]}],
            "fictional",
        )
    )
    monkeypatch.setattr(dreaming, "dream_on_cluster", synthesize)
    error = ComputeUnavailable(
        "settlement_failed", "unresolved", category=FAILURE_SETTLEMENT_FAILED
    )
    embed = AsyncMock(side_effect=error)
    monkeypatch.setattr(dreaming, "embed_documents_with_metadata", embed)
    with pytest.raises(ComputeUnavailable) as caught:
        await dreaming.run_dreaming(owner, store)
    assert caught.value is error
    assert synthesize.await_args is not None
    assert synthesize.await_args.args[0] == [cloud]
    store.insert_memory.assert_not_awaited()
    store.log_dream_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_local_injection_preserves_lexical_context_without_embedding(monkeypatch):
    store = AsyncMock()
    store.get_conversation.return_value = {"user_id": uuid.uuid4(), "pipeline": "local"}
    store.get_l0_memories.return_value = [
        {"content": "User prefers quiet rooms", "category": "fact"}
    ]
    store.get_recent_messages.return_value = [{"role": "user", "content": "What editor?"}]
    store.get_recent_summaries.return_value = []
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "selected")
    embed = AsyncMock()
    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    retrieve = AsyncMock(return_value=[{"content": "User uses a text editor", "category": "fact"}])
    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    context = await injection.build_memory_context(store, uuid.uuid4())
    assert "quiet rooms" in context and "text editor" in context
    embed.assert_not_awaited()
    assert retrieve.await_args is not None
    assert retrieve.await_args.kwargs["include_local"] is True
    assert retrieve.await_args.kwargs["query_embedding"] is None
