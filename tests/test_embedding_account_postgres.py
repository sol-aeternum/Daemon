"""Real ledger, synthetic rows and mocked HTTP; isolated test DB required."""

import json

import httpx
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.memory import embedding_adapter as adapter
from tests.test_account_compute_postgres import account_database as _account_database
from tests.test_embedding_adapter import installed as _installed, receipt

# Re-export the existing fixtures; all setup/cleanup remains in their owners.
account_database = _account_database
installed = _installed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["valid", "malformed", "timeout", "cancel"])
async def test_embedding_reserves_and_settles_real_ledger(
    monkeypatch, installed, account_database, outcome
):
    import asyncio

    pool, owner = account_database
    calls = []
    original_client = httpx.AsyncClient
    payload = receipt()
    if outcome == "malformed":
        payload["data"][0]["embedding"] = [0.0] * 1024

    async def handle(request):
        assert str(request.url) == adapter.ENDPOINT + "/embeddings"
        calls.append(request)
        rows = await pool.fetch("SELECT * FROM entitlement_reservations")
        assert len(rows) == 1 and rows[0]["status"] == "open"
        assert rows[0]["user_id"] == owner
        assert rows[0]["route_id"] == installed.route.route_id
        assert rows[0]["scope_id"] == runtime.current_scope().scope_id
        assert rows[0]["reserved_microusd"] == installed.route.estimate_microusd(
            adapter.input_bound("fictional")
        )
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return httpx.Response(200, json=payload)

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(**kwargs)

    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    async with runtime.account_compute(pool, owner, operation="chat", background=True) as scope:
        if outcome == "valid":
            vectors = await adapter.embed(
                ["fictional"], route_id=installed.route.route_id, api_key="fictional"
            )
            assert len(vectors) == 1 and len(vectors[0]) == 1024
        else:
            expected = (
                asyncio.CancelledError if outcome == "cancel" else adapter.AdapterReceiptError
            )
            with pytest.raises(expected):
                await adapter.embed(
                    ["fictional"], route_id=installed.route.route_id, api_key="fictional"
                )
        assert scope.outstanding == {}
    rows = await pool.fetch("SELECT * FROM entitlement_reservations")
    assert len(calls) == len(rows) == 1
    row = rows[0]
    assert row["status"] == "settled" and row["settled_at"] is not None
    expected_charge = (
        installed.route.estimate_microusd(2) if outcome == "valid" else row["reserved_microusd"]
    )
    assert row["actual_microusd"] == expected_charge
    assert row["background"] is True
    usage = json.loads(row["usage"])
    if outcome == "valid":
        assert usage["input_tokens"] == 2 and usage["output_tokens"] == 0
    else:
        assert usage["estimated_cost"] is True
        assert "input_tokens" not in usage  # Unknown usage is not a fabricated receipt.
    assert "fictional" not in row["usage"]
