"""Offline-only policy, HTTP receipt, account accounting and vector identity checks."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.config import Settings
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements import attestation
from orchestrator.entitlements import policy as policy_module
from orchestrator.entitlements.errors import (
    AccountSuspended,
    BudgetExceeded,
    ConcurrencyExceeded,
    PolicyError,
    RateLimitExceeded,
    ReservationCommitUncertain,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)
from orchestrator.entitlements.models import Reservation
from orchestrator.entitlements.plans import ChargeKind, Plan, ReservationStatus
from orchestrator.entitlements.policy import parse_inference_policy
from orchestrator.memory import embedding as public
from orchestrator.memory import embedding_adapter as adapter

ROOT = Path(__file__).resolve().parents[1]
ROUTE_ID = "azure-openrouter-small-1024"


def document() -> dict[str, Any]:
    return json.loads((ROOT / "config/inference_policy.json").read_text())


def approved_document() -> dict[str, Any]:
    doc = document()
    route = doc["embedding_routes"][0]
    route["approved"] = True
    route["availability"] = "verified"
    route["privacy"]["account_prompt_logging_disabled"] = True
    route["privacy"]["free_model_training_opt_out"] = True
    route["operator_review"] = {
        "reviewer": "offline-test",
        "reviewed_at": "2026-10-01T00:00:00Z",
        "evidence": ["fictional-only test, not qualification"],
    }
    return doc


def receipt(count: int = 1, tokens: int = 2) -> dict[str, Any]:
    return {
        "model": "text-embedding-3-small",
        "provider": "Azure",
        "data": [{"index": index, "embedding": [0.1 + index] * 1024} for index in range(count)],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@pytest.fixture
def installed(monkeypatch):
    policy = parse_inference_policy(approved_document())
    route = policy.embedding_routes[ROUTE_ID]
    previous = attestation.snapshot()
    attestation.reset_local_revocations()
    key = (route.route_id, attestation.baseline_fingerprint(route))
    attestation.set_snapshot(
        attestation.AttestationSnapshot(
            loaded=True,
            attested_at={key: datetime.now(timezone.utc)},
        )
    )
    monkeypatch.setattr(runtime, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(adapter, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(public, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(policy_module, "load_inference_policy", lambda: policy)
    settings = Settings.model_construct(embedding_route_id=ROUTE_ID, openrouter_api_key="fictional")
    monkeypatch.setattr(public, "get_settings", lambda: settings)
    public.reset_embedding_metrics_for_tests()
    service = MagicMock(spec=EntitlementService)
    service.reserve = AsyncMock(side_effect=lambda *a, **kw: SimpleNamespace(id=uuid.uuid4()))
    service.settle = AsyncMock()
    service.recover_reservation = AsyncMock()
    scope = runtime.ComputeScope(uuid.uuid4(), cast(EntitlementService, service))
    token = runtime._scope.set(scope)
    yield SimpleNamespace(
        policy=policy, route=route, service=service, scope=scope, settings=settings, key=key
    )
    runtime._scope.reset(token)
    public.reset_embedding_metrics_for_tests()
    attestation.reset_local_revocations()
    attestation.set_snapshot(previous)


def transport(
    monkeypatch, installed, *, payload=None, status=200, body=None, headers=None, error=None
):
    calls: list[httpx.Request] = []
    client_class = httpx.AsyncClient
    options: list[dict[str, Any]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        assert installed.scope.outstanding, "no provider call before registration"
        assert installed.service.reserve.await_count >= 1
        calls.append(request)
        if error is not None:
            raise error
        if body is not None:
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(
            status, json=payload if payload is not None else receipt(), headers=headers
        )

    def client(**kwargs):
        options.append(kwargs)
        kwargs["transport"] = httpx.MockTransport(handle)
        return client_class(**kwargs)

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    return calls, options


def test_portable_denied_and_qualified_production_still_needs_attestation():
    for filename in ("inference_policy.json", "inference_policy.production.json"):
        policy = parse_inference_policy(json.loads((ROOT / "config" / filename).read_text()))
        route = policy.embedding_route(ROUTE_ID)
        assert route is not None
        assert route.approved is (filename == "inference_policy.production.json")
        assert not route.is_approved(policy.requirements)
        assert ROUTE_ID not in policy.routes
        assert route in policy.monitorable_routes()
        assert (route.review.reviewed_at is not None) is route.approved
        assert route.review.review_expires_at is None
    assert Settings.model_fields["embedding_route_id"].default == ""


def test_embedding_mapping_optional_and_completion_contract_unchanged():
    doc = document()
    del doc["embedding_routes"]
    policy = parse_inference_policy(doc)
    assert policy.embedding_routes == {}
    completion = next(iter(policy.routes.values()))
    assert "model_capabilities_unverified" in completion.rejection_reasons(policy.requirements)
    assert not completion.supports(
        required_capabilities=frozenset(), input_tokens=0, output_tokens=0
    )


@pytest.mark.parametrize("collection", ["routes", "embedding_routes", "tool_services"])
def test_accounting_route_ids_unique_across_collections(collection):
    doc = document()
    if collection == "embedding_routes":
        doc[collection].append(copy.deepcopy(doc[collection][0]))
    elif collection == "routes":
        doc[collection][0]["route_id"] = ROUTE_ID
    else:
        doc[collection][0]["service_id"] = ROUTE_ID
    with pytest.raises(PolicyError, match="duplicate"):
        parse_inference_policy(doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dimensions", True),
        ("max_input_tokens", -1),
        ("max_batch_items", "100"),
    ],
)
def test_embedding_parser_type_strict_capacities(field, value):
    doc = document()
    doc["embedding_routes"][0][field] = value
    with pytest.raises(PolicyError):
        parse_inference_policy(doc)


def test_shared_monitor_covers_embedding_route_and_sticky_revocation(installed):
    route = installed.route
    assert route in attestation._monitored_routes()
    checks = attestation.evaluate(
        [route],
        {"data": [{"model_id": adapter.MODEL, "tag": "azure"}]},
        {"data": [{"slug": "azure", "dataPolicy": {"training": False, "retainsPrompts": False}}]},
    )
    assert checks[0].outcome == "attested"
    assert route.is_approved(installed.policy.requirements)
    attestation.apply_observed([replace(checks[0], outcome="revoked")])
    attestation.set_snapshot(
        attestation.AttestationSnapshot(
            loaded=True, attested_at={installed.key: datetime.now(timezone.utc)}
        )
    )
    assert "zdr_attestation_revoked" in route.rejection_reasons(installed.policy.requirements)


@pytest.mark.parametrize("snapshot", ["unknown", "stale", "revoked"])
@pytest.mark.asyncio
async def test_attestation_denial_prevents_reservation_and_send(monkeypatch, installed, snapshot):
    value = (
        attestation.AttestationSnapshot()
        if snapshot == "unknown"
        else attestation.AttestationSnapshot(
            loaded=True,
            attested_at={installed.key: datetime.now(timezone.utc) - timedelta(hours=73)},
            revoked=frozenset({installed.key}) if snapshot == "revoked" else frozenset(),
        )
    )
    attestation.set_snapshot(value)
    calls, _ = transport(monkeypatch, installed)
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_documents_with_metadata(["fictional"])
    assert calls == []
    installed.service.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_reapproval_after_hold_before_send(monkeypatch, installed):
    async def reserve(*args, **kwargs):
        attestation.apply_observed(
            [attestation.RouteCheck(ROUTE_ID, installed.key[1], "revoked", ("left_zdr_listing",))]
        )
        return SimpleNamespace(id=uuid.uuid4())

    installed.service.reserve.side_effect = reserve
    calls, _ = transport(monkeypatch, installed)
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_documents_with_metadata(["fictional"])
    assert calls == []
    assert installed.service.settle.await_args.args[1] == 0
    assert not installed.scope.outstanding


@pytest.mark.parametrize(
    "change",
    [
        {"model": "openai/text-embedding-3-large"},
        {"endpoint": "https://example.com"},
        {"dimensions": 1536},
        {"max_batch_tokens": 300001},
        {"max_input_tokens": 8193},
    ],
)
def test_exact_adapter_pins(installed, change):
    with pytest.raises(adapter.AdapterUnavailable):
        adapter.validate_adapter_route(replace(installed.route, **change))


@pytest.mark.asyncio
async def test_happy_wire_pin_native_model_order_and_actual_settlement(monkeypatch, installed):
    payload = receipt(count=2, tokens=60)
    payload["data"].reverse()
    payload["usage"]["cost"] = "0.0000012"
    calls, options = transport(monkeypatch, installed, payload=payload)
    result = await public.embed_documents_with_metadata(["a" * 1000, "b" * 1000])
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body == {
        "model": adapter.MODEL,
        "input": ["a" * 1000, "b" * 1000],
        "dimensions": 1024,
        "encoding_format": "float",
        "provider": {
            "only": ["azure"],
            "order": ["azure"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "max_price": {"prompt": 0.02},
        },
    }
    assert "HTTP-Referer" not in calls[0].headers and "X-Title" not in calls[0].headers
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert result.embeddings[0] == [0.1] * 1024
    assert result.storage_model == adapter.STORAGE_MODEL
    assert installed.service.settle.await_args.args[1] == 2
    assert installed.service.settle.await_args.kwargs["usage"] == {
        "input_tokens": 60,
        "output_tokens": 0,
    }
    assert installed.scope.outstanding == {}
    assert public.get_embedding_status()["last_outcome"] == "success"
    assert public.get_embedding_retry_activations() == 0


@pytest.mark.asyncio
async def test_query_document_identity_isolated_from_old_spaces(monkeypatch, installed):
    calls, _ = transport(monkeypatch, installed)
    document_result = await public.embed_documents_with_metadata(["fictional"])
    query = await public.embed_query_with_metadata("fictional")
    results = await public.embed_query_for_configured_storage_models(
        "fictional", primary_result=query
    )
    assert len(calls) == 2 and results == [query]
    assert document_result.storage_model == query.storage_model == adapter.STORAGE_MODEL
    assert isinstance(query.embedding, public.EmbeddingVector)
    assert query.embedding.storage_model == adapter.STORAGE_MODEL
    assert public.get_enabled_embedding_storage_models() == (adapter.STORAGE_MODEL,)
    assert public.get_configured_embedding_fallback_storage_models() == ()
    assert adapter.STORAGE_MODEL not in (
        "text-embedding-3-small",
        "openai:text-embedding-3-small",
        "openrouter:openai/text-embedding-3-small",
    )
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_query("fictional entity")
    assert len(calls) == 2


@pytest.mark.parametrize("model", [adapter.MODEL, "text-embedding-3-small"])
def test_qualified_and_exact_native_model_receipts(installed, model):
    payload = receipt()
    payload["model"] = model
    assert adapter.validate_receipt(payload, installed.route, 1)[1] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "model",
        "provider",
        "count",
        "dimensions",
        "boolean",
        "nonfinite",
        "zero_vector",
        "index",
        "duplicate_index",
        "missing_usage",
        "boolean_usage",
        "string_usage",
        "zero_usage",
        "total_mismatch",
        "output_tokens",
        "negative_cost",
        "excess_cost",
        "nonfinite_cost",
        "boolean_cost",
    ],
)
@pytest.mark.asyncio
async def test_malformed_receipt_charges_hold_once_without_retry(monkeypatch, installed, mutation):
    payload = receipt(count=2)
    if mutation == "model":
        payload["model"] = "text-embedding-3-small-2026"
    elif mutation == "provider":
        payload["provider"] = "OpenAI"
    elif mutation == "count":
        payload["data"].pop()
    elif mutation == "dimensions":
        payload["data"][0]["embedding"].pop()
    elif mutation == "boolean":
        payload["data"][0]["embedding"][0] = True
    elif mutation == "nonfinite":
        payload["data"][0]["embedding"][0] = float("inf")
    elif mutation == "zero_vector":
        payload["data"][0]["embedding"] = [0.0] * 1024
    elif mutation == "index":
        payload["data"][0]["index"] = True
    elif mutation == "duplicate_index":
        payload["data"][1]["index"] = 0
    elif mutation == "missing_usage":
        del payload["usage"]
    elif mutation == "boolean_usage":
        payload["usage"]["prompt_tokens"] = True
    elif mutation == "string_usage":
        payload["usage"]["prompt_tokens"] = "2"
    elif mutation == "zero_usage":
        payload["usage"] = {"prompt_tokens": 0, "total_tokens": 0}
    elif mutation == "total_mismatch":
        payload["usage"]["total_tokens"] = 3
    elif mutation == "output_tokens":
        payload["usage"]["completion_tokens"] = 1
    elif mutation == "negative_cost":
        payload["usage"]["cost"] = -1
    elif mutation == "excess_cost":
        payload["usage"]["cost"] = "0.01"
    elif mutation == "nonfinite_cost":
        payload["usage"]["cost"] = "NaN"
    elif mutation == "boolean_cost":
        payload["usage"]["cost"] = False
    calls, _ = transport(monkeypatch, installed, payload=payload)
    with pytest.raises(public.EmbeddingRequestError):
        await public.embed_documents_with_metadata(["a" * 1000, "b" * 1000])
    assert len(calls) == 1
    assert (
        installed.service.settle.await_args.args[1] == installed.service.reserve.await_args.args[1]
    )
    assert installed.service.settle.await_args.kwargs["usage"] == {"estimated_cost": True}
    assert public.get_embedding_retry_activations() == 0


@pytest.mark.parametrize(
    "body,headers,status",
    [
        (b'{"model":"x","model":"y"}', {}, 200),
        (b"broken", {}, 200),
        (b"x", {"content-encoding": "gzip"}, 200),
        (b"", {"location": "https://example.com"}, 302),
    ],
)
@pytest.mark.asyncio
async def test_untrusted_http_receipts_bounded_no_redirect_retry(
    monkeypatch, installed, body, headers, status
):
    calls, _ = transport(monkeypatch, installed, body=body, headers=headers, status=status)
    with pytest.raises(public.EmbeddingRequestError):
        await public.embed_documents_with_metadata(["fictional"])
    assert len(calls) == 1
    assert (
        installed.service.settle.await_args.args[1] == installed.service.reserve.await_args.args[1]
    )


@pytest.mark.asyncio
async def test_oversized_response_charges_unknown_hold(monkeypatch, installed):
    monkeypatch.setattr(adapter, "MAX_RESPONSE_BYTES", 10)
    calls, _ = transport(monkeypatch, installed, body=b"x" * 11)
    with pytest.raises(public.EmbeddingRequestError):
        await public.embed_documents_with_metadata(["fictional"])
    assert len(calls) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("fictional"), httpx.ConnectError("fictional")])
@pytest.mark.asyncio
async def test_marked_transport_failure_conservative_no_retry(monkeypatch, installed, error):
    calls, _ = transport(monkeypatch, installed, error=error)
    with pytest.raises(public.EmbeddingRequestError):
        await public.embed_documents_with_metadata(["fictional"])
    assert len(calls) == 1
    assert (
        installed.service.settle.await_args.args[1] == installed.service.reserve.await_args.args[1]
    )


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceeded(requested=1, spent=1, reserved=0, ceiling=1),
        AccountSuspended(),
        RateLimitExceeded(requests_in_window=1, ceiling=1),
        ConcurrencyExceeded(open_reservations=1, ceiling=1),
    ],
)
@pytest.mark.asyncio
async def test_account_refusal_never_dispatches_or_settles(monkeypatch, installed, error):
    installed.service.reserve.side_effect = error
    calls, _ = transport(monkeypatch, installed)
    with pytest.raises(public.EmbeddingBudgetError):
        await public.embed_documents_with_metadata(["fictional"])
    assert calls == []
    installed.service.settle.assert_not_awaited()


@pytest.mark.parametrize("locality", [True, None, "false"])
@pytest.mark.asyncio
async def test_local_and_unknown_locality_never_admitted(monkeypatch, installed, locality):
    calls, _ = transport(monkeypatch, installed)
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_documents_with_metadata(["fictional"], local_only=locality)
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_query_with_metadata("fictional", local_only=locality)
    assert calls == []
    installed.service.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_invalid_unicode_and_long_inputs_no_send(monkeypatch, installed):
    calls, _ = transport(monkeypatch, installed)
    result = await public.embed_documents_with_metadata(["", "  "])
    assert result.embeddings == []
    assert public.get_embedding_status()["last_outcome"] == "never_attempted"
    for text in ("漢" * 2700, "x" * 8000, "\ud800"):
        with pytest.raises(public.EmbeddingRequestError):
            await public.embed_documents_with_metadata(["valid", text])
    assert calls == []
    installed.service.reserve.assert_not_awaited()
    assert adapter.input_bound("😊漢") == len("😊漢".encode("utf-8")) + 16


@pytest.mark.asyncio
async def test_batch_caps_split_preserve_all_texts(monkeypatch, installed):
    payload = receipt(count=1)
    route = replace(installed.route, max_batch_items=1)
    policy = replace(installed.policy, embedding_routes={ROUTE_ID: route})
    monkeypatch.setattr(runtime, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(adapter, "load_inference_policy", lambda: policy)
    calls, _ = transport(monkeypatch, installed, payload=payload)
    vectors = await adapter.embed(["first", "second"], route_id=ROUTE_ID, api_key="fictional")
    assert len(vectors) == len(calls) == 2
    assert [json.loads(call.content)["input"] for call in calls] == [["first"], ["second"]]


@pytest.mark.asyncio
async def test_settlement_failure_propagates_and_keeps_hold(monkeypatch, installed):
    installed.service.settle.side_effect = RuntimeError("fictional secret must not leak")
    calls, _ = transport(monkeypatch, installed)
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await public.embed_documents_with_metadata(["fictional"])
    assert caught.value.code == "settlement_failed"
    assert len(calls) == 1 and installed.scope.outstanding
    with pytest.raises(runtime.ComputeUnavailable):
        public.raise_if_embedding_accounting_error(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_confirmed_overage_settles_truthfully_then_stops(monkeypatch, installed):
    calls, _ = transport(monkeypatch, installed, payload=receipt(tokens=1000))
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await public.embed_documents_with_metadata(["fictional"])
    assert caught.value.code == "embedding_price_exceeded"
    assert installed.service.settle.await_args.args[1] == 20
    assert installed.service.reserve.await_args.args[1] == 1
    assert len(calls) == 1 and installed.scope.outstanding == {}


@pytest.mark.asyncio
async def test_cancellation_during_reserve_registers_then_refunds(monkeypatch, installed):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reserve(*args, **kwargs):
        started.set()
        await finish.wait()
        return SimpleNamespace(id=uuid.uuid4())

    installed.service.reserve.side_effect = reserve
    calls, _ = transport(monkeypatch, installed)
    task = asyncio.create_task(public.embed_documents_with_metadata(["fictional"]))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [] and installed.scope.outstanding == {}
    assert installed.service.settle.await_args.args[1] == 0


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.asyncio
async def test_cancel_before_and_after_dispatch_selects_zero_or_hold(installed, marked):
    started = asyncio.Event()

    async def work():
        async with runtime.metered_embedding_call(installed.route, 1000) as charge:
            if marked:
                charge.mark_dispatched()
            started.set()
            await asyncio.Future()

    task = asyncio.create_task(work())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert installed.service.settle.await_args.args[1] == (20 if marked else 0)


@pytest.mark.asyncio
async def test_cancel_during_settlement_keeps_single_task_and_actual(installed):
    started, finish = asyncio.Event(), asyncio.Event()

    async def settle(*args, **kwargs):
        started.set()
        await finish.wait()

    installed.service.settle.side_effect = settle

    async def work():
        async with runtime.metered_embedding_call(installed.route, 1000) as charge:
            charge.mark_dispatched()
            charge.confirm(input_tokens=2, actual_microusd=1)

    task = asyncio.create_task(work())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    hold = next(iter(installed.scope.outstanding.values()))
    assert hold.actual == 1
    finish.set()
    await installed.scope.settle(hold.reservation, hold.actual)
    assert installed.service.settle.await_count == 1 and installed.scope.outstanding == {}


def uncertain_receipt(installed, bound=20) -> ReservationReceipt:
    return ReservationReceipt(
        reservation=Reservation(
            id=uuid.uuid4(),
            user_id=installed.scope.user_id,
            period_key="2026-10",
            plan=Plan.FREE,
            operation=installed.scope.operation,
            charge_kind=ChargeKind.PLAN,
            premium=False,
            extended=installed.scope.extended,
            reserved_microusd=bound,
            status=ReservationStatus.OPEN,
            created_at=datetime.now(timezone.utc),
        ),
        user_id=installed.scope.user_id,
        scope_id=installed.scope.scope_id,
        provider="openrouter",
        model=adapter.MODEL,
        route_id=ROUTE_ID,
        extended_run=False,
        background=installed.scope.background,
    )


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.asyncio
async def test_uncertain_commit_recovers_known_zero_never_dispatches(installed, committed):
    value = uncertain_receipt(installed)
    installed.service.reserve.side_effect = ReservationCommitUncertain(value)
    installed.service.recover_reservation.return_value = value.reservation if committed else None
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_embedding_call(installed.route, 1000):
            pytest.fail("ambiguous acquisition never authorizes send")
    assert caught.value.code == "account_unavailable"
    assert installed.service.reserve.await_count == 1 and installed.scope.outstanding == {}
    if committed:
        assert installed.service.settle.await_args.args[1] == 0
    else:
        installed.service.settle.assert_not_awaited()


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.asyncio
async def test_unresolved_or_mismatched_receipt_propagates_accounting(installed, mismatch):
    value = uncertain_receipt(installed)
    if mismatch:
        value = replace(value, model="another")
    installed.service.reserve.side_effect = ReservationCommitUncertain(value)
    installed.service.recover_reservation.side_effect = ReservationRecoveryUnresolved(value)
    with pytest.raises(runtime.ComputeUnavailable) as caught:
        async with runtime.metered_embedding_call(installed.route, 1000):
            pytest.fail("no send")
    assert caught.value.code == "reservation_outcome_unresolved"
    assert caught.value.category == runtime.FAILURE_SETTLEMENT_FAILED
    assert installed.scope.outstanding[value.id].actual == 0
    if mismatch:
        installed.service.recover_reservation.assert_not_awaited()
        with pytest.raises(runtime.ComputeUnavailable):
            await installed.scope.settle(value, 0)


@pytest.mark.asyncio
async def test_concurrent_calls_preserve_user_scope_flags_and_first_extended_once(installed):
    installed.scope.extended = True
    installed.scope.background = True
    installed.scope.expected_period = "2026-10"

    async def work():
        async with runtime.metered_embedding_call(installed.route, 1000) as charge:
            charge.mark_dispatched()
            await asyncio.sleep(0)
            charge.confirm(input_tokens=2, actual_microusd=1)

    await asyncio.gather(work(), work())
    calls = installed.service.reserve.await_args_list
    assert [call.kwargs["extended_run"] for call in calls] == [True, False]
    for call in calls:
        assert call.args[0] == installed.scope.user_id
        assert call.kwargs["scope_id"] == installed.scope.scope_id
        assert call.kwargs["background"] and call.kwargs["extended"]
        assert call.kwargs["expected_period"] == "2026-10"
        assert call.kwargs["model"] == adapter.MODEL and call.kwargs["route_id"] == ROUTE_ID
    assert len(installed.scope.settled) == 2 and installed.scope.outstanding == {}


def test_status_static_per_adapter_no_probe(monkeypatch, installed):
    def forbidden(**kwargs):
        pytest.fail("status may not probe")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", forbidden)
    status = public.get_embedding_status()
    assert status["configuration"] == "eligible" and status["reason_codes"] == []
    assert status["last_outcome"] == "never_attempted"
    assert status["observation_scope"] == "backend_process"
    assert status["adapters"]["azure-openrouter"]["budget_adapter_available"]
    for provider in ("voyage", "openrouter", "openai"):
        assert status["adapters"][provider]["reason_codes"] == ["budget_adapter_unavailable"]


@pytest.mark.asyncio
async def test_unknown_selector_denies_without_legacy_fallback(monkeypatch, installed):
    installed.settings.embedding_route_id = "unknown"
    calls, _ = transport(monkeypatch, installed)
    legacy = AsyncMock(side_effect=AssertionError("no legacy fallback"))
    monkeypatch.setattr(public, "_embed_with_voyage_retry", legacy)
    with pytest.raises(public.EmbeddingConfigurationError):
        await public.embed_documents_with_metadata(["fictional"])
    assert calls == []
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_scope_denies_without_send(monkeypatch, installed):
    calls, _ = transport(monkeypatch, installed)
    token = runtime._scope.set(None)
    try:
        with pytest.raises(public.EmbeddingBudgetError):
            await public.embed_documents_with_metadata(["fictional"])
    finally:
        runtime._scope.reset(token)
    assert calls == []
    installed.service.reserve.assert_not_awaited()


@pytest.mark.parametrize("pipeline", [None, "unknown"])
@pytest.mark.asyncio
async def test_extraction_and_injection_guard_actual_source_before_cloud(
    monkeypatch, installed, pipeline
):
    from orchestrator.memory import extraction, injection

    store = MagicMock()
    store.get_conversation = AsyncMock(
        return_value={"user_id": installed.scope.user_id, "pipeline": pipeline}
    )
    extractor = AsyncMock(side_effect=AssertionError("no source processing"))
    query = AsyncMock(side_effect=AssertionError("no local query embedding"))
    monkeypatch.setattr(extraction, "extract_facts_from_text", extractor)
    monkeypatch.setattr(injection, "embed_query_with_metadata", query)
    assert await extraction.process_extraction(
        store, installed.scope.user_id, uuid.uuid4(), "fictional"
    ) == (False, [], False)
    assert await injection.build_memory_context(store, uuid.uuid4()) == ""
    extractor.assert_not_awaited()
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedup_guard_actual_source_and_local_fact_no_embedding(monkeypatch, installed):
    from orchestrator.memory import dedup

    store = MagicMock()
    store.get_conversation = AsyncMock(
        return_value={"user_id": installed.scope.user_id, "pipeline": "local"}
    )
    fact = SimpleNamespace(content="fictional", category="fact", local_only=False)
    embedding = AsyncMock(side_effect=AssertionError("no local source embedding"))
    monkeypatch.setattr(dedup, "prepare_memory_embedding", embedding)
    with pytest.raises(public.EmbeddingConfigurationError):
        await dedup.deduplicate_facts(store, installed.scope.user_id, [fact], uuid.uuid4())
    fact.local_only = True
    store._insert_memory_with_outcome = AsyncMock(return_value=({"id": uuid.uuid4()}, True))
    result = await dedup.deduplicate_facts(store, installed.scope.user_id, [fact], None)
    assert len(result.new) == 1
    insert_call = store._insert_memory_with_outcome.await_args
    assert insert_call is not None
    assert insert_call.kwargs["local_only"] is True
    assert insert_call.kwargs["embedding"] is None
    embedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_retrieval_lexical_without_query_embedding(monkeypatch, installed):
    from orchestrator.memory import retrieval

    monkeypatch.setattr(retrieval, "get_settings", lambda: installed.settings)
    query = AsyncMock(side_effect=AssertionError("no local query dispatch"))
    retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr(retrieval, "embed_query_for_configured_storage_models", query)
    monkeypatch.setattr(retrieval, "retrieve_memories", retrieve)
    store = MagicMock()
    store.has_memories_with_embedding_model = AsyncMock(return_value=False)
    assert (
        await retrieval.retrieve_memories_for_text(
            store, "fictional local query", user_id=installed.scope.user_id, include_local=True
        )
        == []
    )
    query.assert_not_awaited()
    retrieval_call = retrieve.await_args
    assert retrieval_call is not None
    assert retrieval_call.kwargs["query_embedding"] == []
    assert retrieval_call.kwargs["embedding_model"] == adapter.STORAGE_MODEL


@pytest.mark.asyncio
async def test_outer_http_scope_reuses_same_user_never_nests(monkeypatch, installed):
    from orchestrator.routes import memories

    def forbidden(*args, **kwargs):
        pytest.fail("HTTP writer nested the active account scope")

    monkeypatch.setattr(memories, "account_compute", forbidden)
    app_state = SimpleNamespace(memory_store=SimpleNamespace(_pool=object()))
    auth = SimpleNamespace(user_id=installed.scope.user_id)
    gen = memories._embedding_account_scope(cast(Any, app_state), cast(Any, auth))
    await anext(gen)
    assert runtime.current_scope() is installed.scope
    await gen.aclose()


@pytest.mark.asyncio
async def test_outer_http_scope_established_for_unscoped_writer(monkeypatch, installed):
    from orchestrator.routes import memories

    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: installed.service)
    app_state = SimpleNamespace(memory_store=SimpleNamespace(_pool=object()))
    auth = SimpleNamespace(user_id=installed.scope.user_id)
    token = runtime._scope.set(None)
    try:
        gen = memories._embedding_account_scope(cast(Any, app_state), cast(Any, auth))
        await anext(gen)
        active = runtime.current_scope()
        assert active.user_id == auth.user_id and not active.background
        async with runtime.metered_embedding_call(installed.route, 1000):
            pass
        assert installed.service.reserve.await_args.kwargs["scope_id"] == active.scope_id
        await gen.aclose()
    finally:
        runtime._scope.reset(token)


@pytest.mark.asyncio
async def test_corrected_memory_cannot_swallow_accounting_failure(monkeypatch, installed):
    from orchestrator.routes import memories

    store = MagicMock()
    store.get_memory = AsyncMock(
        return_value={"user_id": installed.scope.user_id, "content": "old", "local_only": False}
    )
    store.update_memory_content = AsyncMock()
    failure = runtime.ComputeUnavailable(
        "settlement_failed",
        "Reservation settlement failed",
        category=runtime.FAILURE_SETTLEMENT_FAILED,
    )
    monkeypatch.setattr(memories, "embed_documents_with_metadata", AsyncMock(side_effect=failure))
    with pytest.raises(runtime.ComputeUnavailable):
        await memories.update_memory(
            uuid.uuid4(),
            memories.MemoryUpdate(content="new"),
            cast(Any, SimpleNamespace(memory_store=store)),
            cast(Any, SimpleNamespace(user_id=installed.scope.user_id)),
        )
    store.update_memory_content.assert_not_awaited()
