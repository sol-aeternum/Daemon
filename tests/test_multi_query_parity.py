"""No-added paths preserve legacy prompt and baseline effect call contracts."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.memory import injection
from tests import test_memory_context_parity as parity
from tests.test_multi_query_gather import Provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["empty", "unapproved", "parse", "timeout", "exception", "sub_failure", "expiry", "duplicates"],
)
async def test_no_added_identity_against_frozen_legacy_matrix(monkeypatch, outcome):
    settings = SimpleNamespace(
        memory_multi_query_enabled=True,
        memory_multi_query_merge="additive",
        memory_decomposition_timeout_ms=1500,
        memory_subquery_quota=3,
        memory_max_subqueries=3,
    )
    monkeypatch.setattr(injection, "get_settings", lambda: settings)
    provider = Provider([] if outcome == "empty" else None)
    if outcome == "parse":
        provider.values = [123]
    elif outcome == "exception":
        provider.error = RuntimeError("synthetic")
    if outcome != "unapproved":
        monkeypatch.setattr(injection, "ProductionSubqueryProvider", lambda: provider)
    if outcome == "timeout":

        async def expired(*args):
            raise TimeoutError

        monkeypatch.setattr(injection, "_wait_for_components", expired)
    if outcome == "expiry":

        async def expired_branch(task, deadline):
            raise TimeoutError

        monkeypatch.setattr(injection, "_before_component_deadline", expired_branch)
    batch = AsyncMock(
        return_value=[
            SimpleNamespace(embedding=[0.2], storage_model="store-model", model="query-model")
        ]
        * 2
    )
    monkeypatch.setattr(injection, "embed_queries_with_metadata", batch)
    reference = parity._frozen_reference()
    checked = 0
    for pipeline, route, embedding, l0, retrieved, summaries, messages, budget in parity.MATRIX:
        outputs = []
        for implementation in (reference, injection.build_memory_context):
            log = parity._install_collaborators(
                monkeypatch,
                route=route,
                embedding=embedding,
                retrieved=parity.RETRIEVED_SETS[retrieved],
            )
            original_retrieve = injection.retrieve_memories_for_text

            async def retrieve(**kwargs):
                if kwargs.get("query_role") is not None:
                    if outcome == "sub_failure":
                        raise RuntimeError("synthetic")
                    return (
                        [] if embedding == "retrieval_error" else parity.RETRIEVED_SETS[retrieved]
                    )
                return await original_retrieve(**kwargs)

            monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
            store = parity.RecordingStore(
                conversation={"user_id": parity.USER_ID, "pipeline": pipeline},
                l0=parity.L0_SETS[l0],
                messages=parity.MESSAGE_SETS[messages],
                summaries=parity.SUMMARY_SETS[summaries],
            )
            block = await implementation(cast(Any, store), parity.CONVERSATION_ID, budget)
            outputs.append(
                (
                    block,
                    parity._without_store(log["retrieve"], store),
                    log["embed"],
                    parity._without_store(log["trust"].calls, store),
                )
            )
        assert outputs[0] == outputs[1], (
            outcome,
            pipeline,
            route,
            embedding,
            l0,
            retrieved,
            summaries,
            messages,
            budget,
        )
        checked += 1
    assert checked == 1944
