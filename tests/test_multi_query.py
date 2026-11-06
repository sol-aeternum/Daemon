"""Offline component validation, ID merges and real guarded-path fake dispatch."""

import asyncio
import hashlib
import json
from dataclasses import replace
from time import monotonic
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime, model_routing
from orchestrator.entitlements import attestation
from orchestrator.memory import multi_query as mq
from tests.test_production_inference_policy import (
    REVIEWED,
    _attested as _attested,
    production_policy as production_policy,
    mocked_dispatch as mocked_dispatch,
)


def _approved(monkeypatch, policy):
    original = policy.route("luna-azure-eu")
    assert original is not None
    route = replace(
        original,
        route_id=mq.DECOMPOSITION_ROUTE_ID,
        transport=replace(original.transport, multi_provider=True, sort="latency"),
    )
    policy = replace(policy, routes={**policy.routes, route.route_id: route})
    monkeypatch.setattr(mq, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: policy)
    previous = attestation.snapshot()
    attestation.set_snapshot(
        replace(
            previous,
            attested_at={
                **previous.attested_at,
                (route.route_id, attestation.baseline_fingerprint(route)): REVIEWED,
            },
        )
    )
    return route


def _rows(*ids) -> list[dict[str, object]]:
    return [{"id": str(i), "content": "Same content, distinct IDs allowed"} for i in ids]


def test_prompt_and_schema_hashes():
    assert (
        hashlib.sha256(mq.PROMPT_V2.encode()).hexdigest()
        == "dbc6aecdfbbd2b2dde0d36311d659e5f2db09b55e4e559dce3c251b4950ed54f"
    )
    assert (
        hashlib.sha256(json.dumps(mq.SUBQUERY_SCHEMA, sort_keys=True).encode()).hexdigest()
        == "bcc93a5949de3adaf289aa11348c08228acdcd96cee7c2c77b38588f8f82ea2c"
    )


def test_components_are_deduplicated_and_whole_message_rejected():
    assert mq.parse_subqueries(
        '{"queries":["Whole question", "Cat name", " cat   NAME "]}', "Whole question"
    ) == ["Cat name"]
    assert mq.validate_subqueries(
        ["Cat name", "Dog name", "Bird name"], "Whole", max_subqueries=2
    ) == ["Cat name", "Dog name"]
    assert mq.validate_subqueries(["Cat name"], "Whole", max_subqueries=0) == []


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        "[]",
        '{"queries":null}',
        '{"queries":[1]}',
        '{"queries":[""]}',
        '{"queries":[],"extra":true}',
        json.dumps({"queries": ["x"] * 4}),
        json.dumps({"queries": ["x" * 257]}),
    ],
)
def test_parse_failures_have_safe_messages(text):
    with pytest.raises(ValueError, match="Invalid memory decomposition response"):
        mq.parse_subqueries(text, "Whole")


def test_additive_prefix_cap_quota_dedup_and_determinism():
    baseline = _rows(1, 2, 3, 4, 5)
    arms = [_rows(1, 6, 7, 8, 9), _rows(6, 7, 10, 11, 12), _rows(1, 2, 13, 14, 15)]
    merged = mq.merge_memories(baseline, arms, quota=3)
    assert merged[:5] == baseline
    assert [r["id"] for r in merged] == list(
        map(str, [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15])
    )
    assert len(merged) == 5 + 3 * len(arms)
    assert len({r["id"] for r in merged}) == len(merged)
    assert merged == mq.merge_memories(baseline, arms, quota=3)
    assert baseline == _rows(1, 2, 3, 4, 5)


def test_round_robin_order_and_production_rejection():
    baseline, arms = _rows(1, 2, 3, 4, 5), [_rows(1, 6, 7), _rows(6, 8, 9)]
    with pytest.raises(ValueError, match="evaluation-only"):
        mq.merge_memories(baseline, arms, quota=3, mode="round_robin")
    assert [
        r["id"]
        for r in mq.merge_memories(baseline, arms, quota=3, mode="round_robin", evaluation=True)
    ] == list(map(str, [1, 6, 8, 2, 7, 9, 3, 4, 5]))


@pytest.mark.asyncio
async def test_unapproved_pin_shortlist_and_dispatch_never_send(
    monkeypatch, production_policy, mocked_dispatch
):
    service, provider = mocked_dispatch
    monkeypatch.setattr(mq, "load_inference_policy", lambda: production_policy)
    assert (
        await mq.ProductionSubqueryProvider().get_subqueries(
            "question", reference_time=REVIEWED, deadline=monotonic() + 10
        )
        == []
    )
    proposed = production_policy.route(mq.DECOMPOSITION_ROUTE_ID)
    assert proposed is not None
    with pytest.raises(compute_runtime.ComputeUnavailable):
        compute_runtime._pinned_route(proposed.route_id, proposed.model)
    with pytest.raises(compute_runtime.ComputeUnavailable):
        await compute_runtime.guarded_completion(
            _route_id=proposed.route_id,
            model=proposed.model,
            _exact_model=True,
            messages=[{"role": "user", "content": "question"}],
        )
    only_proposal = replace(production_policy, routes={proposed.route_id: proposed})
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: only_proposal)
    with pytest.raises(compute_runtime.ComputeUnavailable):
        compute_runtime.choose_route(profile=mq.DECOMPOSITION_PROFILE)
    provider.assert_not_awaited()
    service.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_outbound_preset_pin_cap_date_and_task_context(
    monkeypatch, production_policy, mocked_dispatch
):
    service, provider = mocked_dispatch
    route = _approved(monkeypatch, production_policy)
    spy = AsyncMock(wraps=compute_runtime.guarded_completion)
    monkeypatch.setattr(mq, "guarded_completion", spy)
    provider.return_value = {
        "choices": [{"message": {"content": '{"queries":["Cat name", "Dog name"]}'}}]
    }
    with model_routing.routing_context("background") as parent:
        task = asyncio.create_task(
            mq.ProductionSubqueryProvider().get_subqueries(
                "x" * 9000, reference_time=REVIEWED, deadline=monotonic() + 10
            )
        )
        assert await task == ["Cat name", "Dog name"]
        assert model_routing.current_routing() is parent and parent.selected_model is None
    sent = provider.await_args.kwargs
    assert sent["extra_body"]["reasoning"]["effort"] == "none"
    assert "reasoning_effort" not in sent
    assert sent["extra_body"]["provider"]["sort"] == "latency"
    assert "order" not in sent["extra_body"]["provider"]
    assert sent["num_retries"] == 0
    assert json.loads(sent["messages"][1]["content"]) == {
        "message": "x" * mq.MAX_INPUT_CHARS,
        "as_of": "2026-10-03",
    }
    assert spy.await_args is not None
    assert spy.await_args.kwargs["_route_id"] == route.route_id
    assert spy.await_args.kwargs["_exact_model"] is True
    assert "reasoning_effort" not in spy.await_args.kwargs
    assert service.reserve.await_args.kwargs["workload_profile"] == "memory_decomposition"
    assert (
        model_routing.model_parameter_presets(route.model, "background")["reasoning_effort"]
        == "medium"
    )
    service.settle.assert_awaited_once()
    assert not compute_runtime.current_scope().outstanding


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "timeout", "revoked"])
async def test_guarded_decomposition_cleanup_settles_once_without_retry(
    monkeypatch, production_policy, mocked_dispatch, failure
):
    service, provider = mocked_dispatch
    route = _approved(monkeypatch, production_policy)
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        if failure == "timeout":
            raise TimeoutError("synthetic timeout")
        await asyncio.Event().wait()

    provider.side_effect = send
    if failure == "revoked":
        old = attestation.snapshot()
        attestation.set_snapshot(
            replace(
                old, revoked=frozenset({(route.route_id, attestation.baseline_fingerprint(route))})
            )
        )
        assert (
            await mq.ProductionSubqueryProvider().get_subqueries(
                "question", reference_time=REVIEWED, deadline=monotonic() + 10
            )
            == []
        )
        provider.assert_not_awaited()
        service.reserve.assert_not_awaited()
        return
    task = asyncio.create_task(
        mq.ProductionSubqueryProvider().get_subqueries(
            "question", reference_time=REVIEWED, deadline=monotonic() + 10
        )
    )
    if failure == "cancel":
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(compute_runtime.ComputeUnavailable):
            await task
    provider.assert_awaited_once()
    service.reserve.assert_awaited_once()
    service.settle.assert_awaited_once()
    assert not compute_runtime.current_scope().outstanding
