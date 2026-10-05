"""Offline wire/budget tests for the opt-in fictional continuation runner."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts import qualify_azure_embeddings as runner
from scripts import embedding_qualification_support as support


@pytest.mark.asyncio
async def test_preparation_uses_production_wire_without_network(monkeypatch):
    before = runner.POLICY.read_bytes()

    def forbidden(**kwargs):
        pytest.fail("Offline preparation may not construct a network transport")

    monkeypatch.setattr(runner, "HTTP_TRANSPORT", forbidden)
    _, route, fixture, docs, requests = await runner.prepare()
    assert len(docs) == 64 and len(fixture["scenarios"]) == 16
    assert set(requests) == {"documents", "queries"}
    assert route.approved  # Isolated evaluation object ONLY.
    original = runner.load_inference_policy(runner.POLICY)
    candidate = original.embedding_route(runner.ROUTE_ID)
    assert candidate is not None and candidate.approved is True
    assert runner.POLICY.read_bytes() == before  # Evaluation never grants persisted approval.
    assert (
        sum(
            runner.adapter.input_bound(text) for body in requests.values() for text in body["input"]
        )
        == 4781
    )
    for body in requests.values():
        assert body["provider"] == runner.EXPECTED_PROVIDER
        assert body["dimensions"] == 1024
        assert "input_type" not in body
    assert requests["queries"]["input"] == [row["query"] for row in fixture["scenarios"]]


def live_transport(tmp_path, monkeypatch, *, tokens=75711, attempts=0):
    body = {
        "model": runner.adapter.MODEL,
        "dimensions": 1024,
        "encoding_format": "float",
        "input": ["fictional"],
        "provider": runner.EXPECTED_PROVIDER,
    }
    frozen = {
        "parent_reservations": [{"input": tokens, "usd": "0.01804121"}]
        + [{"input": 0, "usd": "0"}] * 8,
        "requests": {"documents": body},
    }
    ledger = {
        "frozen": frozen,
        "attempts": [
            {"batch": str(i), "input": 1, "usd": str(support.reservation_cost(1))}
            for i in range(attempts)
        ],
    }
    path = tmp_path / "child.json"
    sent = []

    async def send(request):
        persisted = json.loads(path.read_text())
        assert persisted["attempts"][-1]["outcome"] == "uncertain"
        assert persisted["attempts"][-1]["payload_sha256"] == support.digest(body)
        sent.append(request)
        return httpx.Response(200, json={"fictional": True})

    network = SimpleNamespace(handle_async_request=send, aclose=AsyncMock())
    monkeypatch.setattr(runner, "HTTP_TRANSPORT", lambda **kwargs: network)
    control = runner.ControlledTransport(live=True, ledger=ledger, ledger_path=path, frozen=frozen)
    control.batch = "documents"
    monkeypatch.setattr(control, "evidence", AsyncMock(return_value={"fictional": True}))
    request = httpx.Request("POST", runner.adapter.ENDPOINT + "/embeddings", json=body)
    return control, ledger, request, sent


@pytest.mark.asyncio
async def test_durable_full_hold_precedes_actual_send_and_no_retry(tmp_path, monkeypatch):
    control, ledger, request, sent = live_transport(tmp_path, monkeypatch)
    await control.handle_async_request(request)
    assert len(sent) == 1
    assert ledger["attempts"][0]["input"] == runner.adapter.input_bound("fictional")
    with pytest.raises(ValueError, match="retry"):
        await control.handle_async_request(request)
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disk", "input_cap", "request_cap", "payload"])
async def test_no_send_after_reservation_or_identity_failure(tmp_path, monkeypatch, failure):
    control, ledger, request, sent = live_transport(
        tmp_path,
        monkeypatch,
        tokens=100000 if failure == "input_cap" else 75711,
        attempts=15 if failure == "request_cap" else 0,
    )
    if failure == "disk":

        def fail(*args):
            raise OSError("disk unavailable")

        monkeypatch.setattr(support, "write_ledger", fail)
    if failure == "payload":
        changed = json.loads(request.content)
        changed["input"] = ["different fictional text"]
        request = httpx.Request("POST", request.url, json=changed)
    with pytest.raises((ValueError, OSError)):
        await control.handle_async_request(request)
    assert sent == []


@pytest.mark.asyncio
async def test_unknown_send_stays_reserved_and_cannot_resume(tmp_path, monkeypatch):
    control, ledger, request, sent = live_transport(tmp_path, monkeypatch)

    async def interrupted(request):
        raise httpx.ReadTimeout("fictional timeout")

    assert control.network is not None
    control.network.handle_async_request = interrupted
    with pytest.raises(httpx.ReadTimeout):
        await control.handle_async_request(request)
    assert ledger["attempts"][0]["outcome"] == "uncertain"
    assert control.ledger_path is not None
    with pytest.raises(ValueError, match="cannot resume"):
        support.open_followup(control.ledger_path, ledger["frozen"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "zdr", "training", "price"])
async def test_public_evidence_is_exact_and_credential_free(monkeypatch, change):
    documents = {
        "/api/v1/endpoints/zdr": {"data": [{"model_id": runner.adapter.MODEL, "tag": "azure"}]},
        "/api/frontend/v1/all-providers": {
            "data": [
                {
                    "slug": "azure",
                    "dataPolicy": {
                        key: False
                        for key in (
                            "training",
                            "retainsPrompts",
                            "trainingOpenRouter",
                            "canPublish",
                        )
                    },
                }
            ]
        },
        "/api/v1/models/openai/text-embedding-3-small/endpoints": {
            "data": {
                "endpoints": [{"tag": "azure", "status": 0, "pricing": {"prompt": "0.00000002"}}]
            }
        },
    }
    if change == "zdr":
        documents["/api/v1/endpoints/zdr"]["data"][0]["model_id"] += "-other"
    elif change == "training":
        documents["/api/frontend/v1/all-providers"]["data"][0]["dataPolicy"]["training"] = True
    elif change == "price":
        documents["/api/v1/models/openai/text-embedding-3-small/endpoints"]["data"]["endpoints"][0][
            "pricing"
        ]["prompt"] = "0.00000003"
    calls = []

    async def public(request):
        assert request.method == "GET"
        assert "authorization" not in request.headers
        calls.append(request.url.path)
        return httpx.Response(200, json=documents[request.url.path])

    monkeypatch.setattr(
        runner,
        "HTTP_CLIENT",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(public)),
    )
    control = runner.ControlledTransport()
    if change is None:
        evidence = await control.evidence()
        assert set(evidence["sha256"]) == {"zdr", "providers", "endpoint"}
    else:
        with pytest.raises(ValueError):
            await control.evidence()
    assert len(calls) == 3
