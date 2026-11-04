"""Enabled gather invariants; all stores, embeddings and provider sends are fakes."""

import asyncio
import ast
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from orchestrator import config, compute_runtime, model_routing
from orchestrator.entitlements import attestation
from orchestrator.entitlements.policy import load_inference_policy
from orchestrator.memory import injection
from orchestrator.memory import multi_query as mq
from orchestrator.memory.retrieval import RECENCY_REFERENCE_TIME
from tests.test_memory_context_parity import USER_ID, CONVERSATION_ID, RecordingStore, FakeTrust
from tests.test_multi_query import _approved
from tests.test_multi_query_profile import legacy_routing_document
from tests.test_production_inference_policy import (
    REVIEWED,
    _attested as _attested,
    production_policy as production_policy,
    mocked_dispatch as mocked_dispatch,
)

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
WAIT_TIMEOUT = 5.0


def _rows(*ids) -> list[dict[str, object]]:
    return [{"id": uuid.UUID(int=i), "content": f"Fictional fact {i}"} for i in ids]


class _Store(RecordingStore):
    def __init__(self):
        super().__init__(
            conversation={"user_id": USER_ID, "pipeline": "cloud"},
            l0=[],
            messages=[{"role": "user", "content": "Cat and dog names?"}],
            summaries=[],
        )
        self.bulk_touch_memories = AsyncMock()


# Recording fake implements just the gathered methods, not a real pool-backed store.
Store = cast(Any, _Store)


class Provider:
    def __init__(self, values=None, error=None):
        self.calls = []
        self.values = ["Cat name", "Dog name"] if values is None else values
        self.error = error

    async def get_subqueries(self, message, **kwargs):
        self.calls.append((message, kwargs))
        if self.error:
            raise self.error
        return self.values


@pytest.fixture
def setup(monkeypatch):
    settings = SimpleNamespace(
        memory_multi_query_enabled=True,
        memory_multi_query_merge="additive",
        memory_decomposition_timeout_ms=1500,
        memory_subquery_quota=3,
        memory_max_subqueries=3,
    )
    baseline = _rows(1, 2, 3, 4, 5)
    calls = []
    arms = [_rows(1, 6, 7, 8), _rows(6, 9, 10, 11)]

    async def retrieve(**kwargs):
        calls.append(kwargs)
        if kwargs.get("query_role", "original") == "original":
            return baseline
        return arms[int(kwargs["query_role"].split(":")[1])]

    embed = AsyncMock(
        return_value=SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")
    )
    batch = AsyncMock(
        return_value=[SimpleNamespace(embedding=[0.2], model="query", storage_model="storage")] * 2
    )
    trust = FakeTrust()
    monkeypatch.setattr(injection, "get_settings", lambda: settings)
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "pinned")
    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    monkeypatch.setattr(injection, "embed_queries_with_metadata", batch)
    monkeypatch.setattr(injection, "retrieve_memories_for_text", retrieve)
    monkeypatch.setattr(injection, "_lazy_import_trust_signals", lambda: trust)
    return SimpleNamespace(
        settings=settings,
        baseline=baseline,
        calls=calls,
        arms=arms,
        embed=embed,
        batch=batch,
        trust=trust,
    )


@pytest.mark.asyncio
async def test_gather_batches_accepted_components_prefix_metadata_roles_and_clock(setup):
    provider = Provider(["Cat name", " cat NAME ", "Dog name"])
    inputs = await injection.gather_memory_context_inputs(
        Store(),
        user_id=USER_ID,
        pipeline="cloud",
        query_text="Cat and dog names?",
        query_reference_time=NOW,
        record_side_effects=False,
        subquery_provider=provider,
    )
    assert inputs.retrieved[:5] == setup.baseline and len(inputs.retrieved) == 11
    assert inputs.retrieved_limit == 11
    setup.batch.assert_awaited_once_with(["Cat name", "Dog name"])
    assert [c["query_role"] for c in setup.calls] == ["original", "sub:0", "sub:1"]
    assert all(
        c["touch_results"] is False and c["query_reference_time"] == NOW for c in setup.calls
    )
    assert all(
        c["limit"] == 10 and c["record_query_text"] is False and c["allow_query_embedding"] is False
        for c in setup.calls[1:]
    )
    assert provider.calls[0][1]["reference_time"] == NOW
    assert RECENCY_REFERENCE_TIME.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "timeout",
        "exception",
        "parse",
        "sub_failure",
        "embedding_failure",
        "no_added",
        "local",
        "unknown",
        "inexact",
        "flag_off",
    ],
)
async def test_fail_open_bytes_and_unset_limit(setup, monkeypatch, caplog, case):
    expected = injection.render_memory_context(
        injection.MemoryContextInputs(retrieved=setup.baseline)
    )
    provider = Provider()
    pipeline = "cloud"
    if case == "empty":
        provider.values = []
    elif case == "timeout":
        # Fake-clock expiration, without a real timeout sleep.
        clock = [100.0]
        monkeypatch.setattr(injection, "monotonic", lambda: clock[0])

        async def expired(provider, message, reference, deadline):
            clock[0] = deadline
            raise TimeoutError

        monkeypatch.setattr(injection, "_wait_for_components", expired)
    elif case == "exception":
        provider.error = RuntimeError("PRIVATE_COMPONENT_SENTINEL")
    elif case == "parse":
        provider.values = [123]
    elif case == "embedding_failure":
        setup.batch.side_effect = RuntimeError("PRIVATE_COMPONENT_SENTINEL")
    elif case == "sub_failure":

        async def failing(**kwargs):
            if kwargs.get("query_role") == "sub:1":
                raise RuntimeError("PRIVATE_COMPONENT_SENTINEL")
            return setup.baseline

        monkeypatch.setattr(injection, "retrieve_memories_for_text", failing)
    elif case == "no_added":
        setup.arms[:] = [setup.baseline, setup.baseline]
    elif case == "flag_off":
        setup.settings.memory_multi_query_enabled = False
    elif case == "local":
        pipeline = "local"
    elif case == "unknown":
        pipeline = None
    elif case == "inexact":
        pipeline = "cloud "
    caplog.set_level("INFO")
    inputs = await injection.gather_memory_context_inputs(
        Store(),
        user_id=USER_ID,
        pipeline=pipeline,
        query_text="Cat and dog names?",
        record_side_effects=False,
        subquery_provider=provider,
    )
    assert inputs.retrieved_limit is None
    assert injection.render_memory_context(inputs) == expected
    assert "PRIVATE_COMPONENT_SENTINEL" not in caplog.text
    if case in {"local", "unknown", "inexact", "flag_off"}:
        assert provider.calls == []
        setup.batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", [None, "local", "Cloud", " cloud", "cloud ", "unknown", ""])
async def test_exact_cloud_permission_independent_of_embedding_selector(
    setup, monkeypatch, pipeline
):
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "")
    provider = Provider()
    await injection.gather_memory_context_inputs(
        Store(),
        user_id=USER_ID,
        pipeline=pipeline,
        query_text="question",
        record_side_effects=False,
        subquery_provider=provider,
    )
    assert provider.calls == []
    setup.batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_arms_start_concurrently(setup, monkeypatch):
    baseline_started, decomposition_started = asyncio.Event(), asyncio.Event()

    async def embed(text):
        baseline_started.set()
        await asyncio.wait_for(decomposition_started.wait(), WAIT_TIMEOUT)
        return SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")

    class ConcurrentProvider(Provider):
        async def get_subqueries(self, message, **kwargs):
            decomposition_started.set()
            await asyncio.wait_for(baseline_started.wait(), WAIT_TIMEOUT)
            return []

    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    await injection.gather_memory_context_inputs(
        Store(),
        user_id=USER_ID,
        pipeline="cloud",
        query_text="question",
        record_side_effects=False,
        subquery_provider=ConcurrentProvider(),
    )
    assert baseline_started.is_set() and decomposition_started.is_set()


@pytest.mark.asyncio
async def test_missing_optional_profile_fails_open_before_completion(
    setup, monkeypatch, production_policy, mocked_dispatch
):
    service, send = mocked_dispatch
    _approved(monkeypatch, production_policy)
    routing = model_routing.parse_model_routing(legacy_routing_document())
    monkeypatch.setattr(model_routing, "load_model_routing", lambda: routing)
    context = Mock(wraps=model_routing.routing_context)
    completion = AsyncMock(side_effect=AssertionError("Absent profile must prevent completion"))
    monkeypatch.setattr(mq, "routing_context", context)
    monkeypatch.setattr(mq, "guarded_completion", completion)
    setup.settings.memory_multi_query_enabled = False
    baseline = await injection.build_memory_context(Store(), CONVERSATION_ID)
    setup.settings.memory_multi_query_enabled = True
    with model_routing.routing_context("background") as parent:
        assert await injection.build_memory_context(Store(), CONVERSATION_ID) == baseline
        assert model_routing.current_routing() is parent
        assert parent.selected_model is None
    assert baseline
    context.assert_called_once_with("memory_decomposition")
    completion.assert_not_awaited()
    send.assert_not_awaited()
    service.reserve.assert_not_awaited()
    setup.batch.assert_not_awaited()
    assert all(call.get("query_role") in (None, "original") for call in setup.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "name,semantic_hash",
    [
        (
            "inference_policy.json",
            "04546b167631e9e490551b2bbc394589602c926babf15aea1147f4c30eebaeaf",
        ),
        (
            "inference_policy.production.json",
            "6b52ed0becd1be8c53dcc91cac662f1215f808a92954aeea4a21ac1615028b54",
        ),
    ],
)
async def test_deployment_owned_legacy_policy_needs_no_proposal_or_transport_fields(
    setup, monkeypatch, tmp_path, enabled, name, semantic_hash
):
    raw = json.loads((Path(__file__).resolve().parents[1] / "config" / name).read_text())
    raw["routes"] = [r for r in raw["routes"] if r["route_id"] != mq.DECOMPOSITION_ROUTE_ID]
    # Whole-document semantic identity with origin/main at this rebase.
    assert hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest() == semantic_hash
    assert all("multi_provider" not in r["privacy"]["transport"] for r in raw["routes"])
    path = tmp_path / name
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("DAEMON_INFERENCE_POLICY", str(path))
    settings = cast(Any, config.Settings)(_env_file=None, memory_multi_query_enabled=enabled)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    policy = load_inference_policy()
    assert policy.source_path == str(path)
    assert policy.route(mq.DECOMPOSITION_ROUTE_ID) is None
    assert set(policy.routes) == {r["route_id"] for r in raw["routes"]}
    completion = AsyncMock(side_effect=AssertionError("A missing pin must never be substituted"))
    monkeypatch.setattr(mq, "guarded_completion", completion)
    setup.settings.memory_multi_query_enabled = False
    baseline = await injection.build_memory_context(Store(), CONVERSATION_ID)
    setup.settings.memory_multi_query_enabled = enabled
    assert await injection.build_memory_context(Store(), CONVERSATION_ID) == baseline
    assert baseline
    completion.assert_not_awaited()
    setup.batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "success",
        "empty",
        "no_added",
        "sub_failure",
        "exception",
        "parse",
        "timeout",
        "local",
        "unknown",
    ],
)
async def test_wrapper_effects_once_on_actual_rendered_set(setup, monkeypatch, case, large):
    store = Store()
    provider = Provider([] if case == "empty" else None)
    if case == "no_added":
        setup.arms[:] = [setup.baseline, setup.baseline]
    if case == "sub_failure":
        original_retrieve = injection.retrieve_memories_for_text

        async def failing(**kwargs):
            if kwargs.get("query_role") == "sub:1":
                raise RuntimeError("synthetic retrieval failure")
            return await original_retrieve(**kwargs)

        monkeypatch.setattr(injection, "retrieve_memories_for_text", failing)
    elif case == "exception":
        provider.error = RuntimeError("synthetic provider failure")
    elif case == "parse":
        provider.values = [123]
    elif case == "timeout":

        async def expired(*args):
            raise TimeoutError

        monkeypatch.setattr(injection, "_wait_for_components", expired)
    elif case in {"local", "unknown"}:
        store.conversation["pipeline"] = "local" if case == "local" else None
    monkeypatch.setattr(injection, "ProductionSubqueryProvider", lambda: provider)
    first_three = injection.render_memory_context(
        injection.MemoryContextInputs(retrieved=setup.baseline[:3])
    )
    budget = injection.estimate_tokens(first_three.split("\n", 1)[1].rsplit("\n", 1)[0])
    if large:
        budget = 10**6
    render = Mock(wraps=injection.render_memory_context)
    monkeypatch.setattr(injection, "render_memory_context", render)
    # The wrapper intentionally refuses an unknown pipeline when a cloud
    # embedding route is selected. Test its enabled lexical fallback separately.
    if case == "unknown":
        monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: "")
    block = await injection.build_memory_context(store, CONVERSATION_ID, budget)
    render.assert_called_once()
    inputs = render.call_args.args[0]
    render.assert_called_once_with(inputs, budget)
    expected = (
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        if large and case == "success"
        else ([1, 2, 3, 4, 5] if large else [1, 2, 3])
    )
    ids = [uuid.UUID(int=i) for i in expected]
    assert [r["id"] for r in injection.rendered_memory_rows(inputs, block)] == ids
    if not large:
        assert block == first_three
    store.bulk_touch_memories.assert_awaited_once_with(ids)
    assert len(setup.trust.calls) == 1 and setup.trust.calls[0]["memory_ids"] == ids
    assert all(c["touch_results"] is False for c in setup.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("record_side_effects", [False, True])
async def test_enabled_gather_alone_has_no_effects_for_any_render_budget(
    setup, record_side_effects
):
    store = Store()
    inputs = await injection.gather_memory_context_inputs(
        store,
        user_id=USER_ID,
        pipeline="cloud",
        conversation_id=CONVERSATION_ID,
        query_text="question",
        subquery_provider=Provider(),
        record_side_effects=record_side_effects,
    )
    assert injection.render_memory_context(inputs, 1) == ""
    assert injection.render_memory_context(inputs, 10**6)
    store.bulk_touch_memories.assert_not_awaited()
    assert setup.trust.calls == []


@pytest.mark.asyncio
async def test_harness_gather_and_render_never_record_effects(setup, monkeypatch):
    from tests.longmemeval.evaluate import build_parity_memory_context

    monkeypatch.setattr(injection, "ProductionSubqueryProvider", Provider)
    store = Store()
    block, inputs = await build_parity_memory_context(
        store, user_id=USER_ID, question_text="question", allowed_source_conversation_ids=None
    )
    assert block == injection.render_memory_context(inputs)
    store.bulk_touch_memories.assert_not_awaited()
    assert setup.trust.calls == []


@pytest.mark.parametrize("module", ["injection.py", "multi_query.py"])
def test_runtime_gather_modules_do_not_import_eval(module):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "orchestrator/memory" / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("orchestrator.eval")
            assert not (node.level and (node.module or "").split(".")[0] == "eval")
        elif isinstance(node, ast.Import):
            assert not any(alias.name.startswith("orchestrator.eval") for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not node.value.startswith("orchestrator.eval")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["touch", "trust", "mapping", "cancel", "accounting"])
async def test_post_render_effects_best_effort_preserve_cancellation_and_accounting(
    setup, monkeypatch, failure
):
    store = Store()
    monkeypatch.setattr(injection, "ProductionSubqueryProvider", lambda: Provider([]))
    if failure == "cancel":
        error = asyncio.CancelledError()
    elif failure == "accounting":
        error = compute_runtime.ComputeUnavailable(
            "settlement_failed",
            "Accounting incomplete",
            category=compute_runtime.FAILURE_SETTLEMENT_FAILED,
        )
    else:
        error = RuntimeError("synthetic best-effort failure")
    if failure == "trust":
        monkeypatch.setattr(setup.trust, "record_retrieved_memories", AsyncMock(side_effect=error))
    elif failure == "mapping":
        monkeypatch.setattr(injection, "rendered_memory_rows", Mock(side_effect=error))
    else:
        store.bulk_touch_memories.side_effect = error
    if failure in {"cancel", "accounting"}:
        with pytest.raises(type(error)) as caught:
            await injection.build_memory_context(store, CONVERSATION_ID)
        assert caught.value is error
    else:
        expected = injection.render_memory_context(
            injection.MemoryContextInputs(retrieved=setup.baseline)
        )
        assert await injection.build_memory_context(store, CONVERSATION_ID) == expected


@pytest.mark.asyncio
async def test_wrapper_preserves_signature_and_passes_enabled_custom_budget(setup):
    # Production default is an unapproved route: baseline still gets rendered effects.
    store = Store()
    budget = 1
    assert await injection.build_memory_context(store, CONVERSATION_ID, budget) == ""
    store.bulk_touch_memories.assert_not_awaited()
    assert setup.trust.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["decomposition", "embedding", "sub_retrieval"])
async def test_accounting_failures_escape_fail_open(setup, monkeypatch, phase):
    error = compute_runtime.ComputeUnavailable(
        "settlement_failed",
        "Accounting incomplete",
        category=compute_runtime.FAILURE_SETTLEMENT_FAILED,
    )
    provider = Provider(error=error if phase == "decomposition" else None)
    if phase == "embedding":
        setup.batch.side_effect = error
    elif phase == "sub_retrieval":

        async def failed(**kwargs):
            if kwargs.get("query_role") == "original":
                return setup.baseline
            raise error

        monkeypatch.setattr(injection, "retrieve_memories_for_text", failed)
    store = Store()
    with pytest.raises(compute_runtime.ComputeUnavailable) as caught:
        await injection.gather_memory_context_inputs(
            store,
            user_id=USER_ID,
            pipeline="cloud",
            query_text="question",
            subquery_provider=provider,
        )
    assert caught.value is error
    store.bulk_touch_memories.assert_not_awaited()


@pytest.mark.asyncio
async def test_round_robin_rejected_before_any_work(setup):
    setup.settings.memory_multi_query_merge = "round_robin"
    store = Store()
    provider = Provider()
    with pytest.raises(ValueError, match="evaluation-only"):
        await injection.gather_memory_context_inputs(
            store,
            user_id=USER_ID,
            pipeline="cloud",
            query_text="question",
            subquery_provider=provider,
        )
    assert provider.calls == [] and store.calls == []
    setup.embed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_evaluation_round_robin_overrides_valid_additive_setting(setup, enabled):
    setup.settings.memory_multi_query_enabled = enabled
    inputs = await injection.gather_memory_context_inputs(
        Store(),
        user_id=USER_ID,
        pipeline="cloud",
        query_text="question",
        record_side_effects=False,
        subquery_provider=Provider(),
        evaluation=True,
        evaluation_merge="round_robin",
    )
    assert setup.settings.memory_multi_query_merge == "additive"
    assert [row["id"] for row in inputs.retrieved] == [
        uuid.UUID(int=i) for i in (1, 6, 9, 2, 7, 10, 3, 8, 11, 4, 5)
    ]
    setup.batch.assert_awaited_once_with(["Cat name", "Dog name"])


@pytest.mark.asyncio
async def test_cancellation_drains_both_arms_and_summary(setup, monkeypatch):
    started = [asyncio.Event() for _ in range(3)]
    stopped = [False] * 3

    async def waiting(index) -> Any:
        started[index].set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), WAIT_TIMEOUT)
        finally:
            stopped[index] = True

    async def embed(text):
        return await waiting(0)

    class WaitingProvider(Provider):
        async def get_subqueries(self, message, **kwargs):
            return await waiting(1)

    store = Store()
    monkeypatch.setattr(store, "get_recent_summaries", lambda *a, **kw: waiting(2))
    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    task = asyncio.create_task(
        injection.gather_memory_context_inputs(
            store,
            user_id=USER_ID,
            pipeline="cloud",
            query_text="question",
            subquery_provider=WaitingProvider(),
        )
    )
    try:
        await asyncio.gather(*(asyncio.wait_for(event.wait(), WAIT_TIMEOUT) for event in started))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, WAIT_TIMEOUT)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), WAIT_TIMEOUT)
    assert stopped == [True, True, True]
    store.bulk_touch_memories.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_concurrent_guarded_ledger_holds_settle_once_and_context_isolated(
    setup, monkeypatch, production_policy, mocked_dispatch, cancel
):
    service, send = mocked_dispatch
    service.reserve.side_effect = ["hold-one", "hold-two"]
    route = production_policy.embedding_route("azure-openrouter-small-1024")
    assert route is not None
    # Confirm both independent fixture denials before repairing the synthetic
    # evidence: the clock is Oct 3, but embedding review is Oct 5, and the
    # completion fixture attests only completion routes, not embedding routes.
    reasons = route.rejection_reasons(production_policy.requirements, now=REVIEWED)
    assert "operator_review_date_invalid" in reasons
    assert "zdr_attestation_stale" in reasons
    assert (
        route.route_id,
        attestation.baseline_fingerprint(route),
    ) not in attestation.snapshot().attested_at
    route = replace(route, review=replace(route.review, reviewed_at=REVIEWED))
    policy = replace(
        production_policy,
        embedding_routes={**production_policy.embedding_routes, route.route_id: route},
    )
    old = attestation.snapshot()
    attestation.set_snapshot(
        replace(
            old,
            attested_at={
                **old.attested_at,
                (route.route_id, attestation.baseline_fingerprint(route)): REVIEWED,
            },
        )
    )
    _approved(monkeypatch, policy)
    assert route.is_approved(policy.requirements, now=REVIEWED)
    embedding_started, completion_started = asyncio.Event(), asyncio.Event()

    async def embed(text):
        async with compute_runtime.metered_embedding_call(route, 10) as charge:
            charge.mark_dispatched()
            embedding_started.set()
            await asyncio.wait_for(completion_started.wait(), WAIT_TIMEOUT)
            if cancel:
                await asyncio.wait_for(asyncio.Event().wait(), WAIT_TIMEOUT)
            charge.confirm(input_tokens=1, actual_microusd=0)
        return SimpleNamespace(embedding=[0.1], model="query", storage_model="storage")

    async def completion(**kwargs):
        completion_started.set()
        await asyncio.wait_for(embedding_started.wait(), WAIT_TIMEOUT)
        if cancel:
            await asyncio.wait_for(asyncio.Event().wait(), WAIT_TIMEOUT)
        return {"choices": [{"message": {"content": '{"queries":[]}'}}]}

    monkeypatch.setattr(injection, "embed_query_with_metadata", embed)
    send.side_effect = completion
    with model_routing.routing_context("background") as parent:
        task = asyncio.create_task(
            injection.gather_memory_context_inputs(
                Store(),
                user_id=USER_ID,
                pipeline="cloud",
                query_text="question",
                record_side_effects=False,
            )
        )
        try:
            if cancel:
                await asyncio.gather(
                    asyncio.wait_for(embedding_started.wait(), WAIT_TIMEOUT),
                    asyncio.wait_for(completion_started.wait(), WAIT_TIMEOUT),
                )
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, WAIT_TIMEOUT)
            else:
                await asyncio.wait_for(task, WAIT_TIMEOUT)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), WAIT_TIMEOUT)
        assert parent.selected_model is None
    assert service.reserve.await_count == 2 and service.settle.await_count == 2
    assert len({call.args[0] for call in service.settle.await_args_list}) == 2
    scope = compute_runtime.current_scope()
    assert not scope.outstanding
    for key, amount in list(scope.settled.items()):
        await scope.settle(key, amount)
    assert service.settle.await_count == 2
