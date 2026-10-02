"""Reconcile full-hold settlements to provider receipts (optional work O3)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
import pytest_asyncio

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from orchestrator.entitlements.receipts import (
    Receipt,
    fetch_receipt,
    parse_receipt,
    receipt_charge,
    reconcile_receipts,
)
from orchestrator.entitlements.service import EntitlementService
from test_compute_runtime import _route
from test_model_routing import FLASH, accepted_single, dispatch_fixture, named_route

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://openrouter.ai/api/v1"


def _payload(**fields: Any) -> dict[str, Any]:
    return {"data": {"native_tokens_prompt": 100, "native_tokens_completion": 50, **fields}}


def test_parse_receipt_reads_native_counts_and_cost() -> None:
    receipt = parse_receipt(_payload(native_tokens_reasoning=20, total_cost=0.0012))
    assert receipt == Receipt(100, 50, 20, 0.0012)
    assert parse_receipt({"data": {"tokens_prompt": 3, "tokens_completion": 4}}) == Receipt(
        3, 4, 0, None
    )
    assert parse_receipt({"data": {"native_tokens_prompt": "x"}}) is None
    assert parse_receipt({"error": "nope"}) is None


def test_receipt_charge_is_the_larger_of_ceiling_tokens_and_reported_cost() -> None:
    route: Any = _route(model=FLASH, input_price=1_000_000, output_price=2_000_000)
    by_tokens = receipt_charge(route, Receipt(100, 50, 20, None))
    assert by_tokens == route.estimate_microusd(100, 70)  # reasoning counted conservatively
    assert receipt_charge(route, Receipt(100, 50, 20, 1.0)) == 1_000_000


@pytest.mark.asyncio
async def test_fetch_receipt_handles_pending_ready_and_failure() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        generation = request.url.params["id"]
        if generation == "gen-pending":
            return httpx.Response(404)
        if generation == "gen-broken":
            return httpx.Response(500)
        return httpx.Response(200, json=_payload(total_cost=0.0001))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await fetch_receipt(client, endpoint=ENDPOINT, api_key="k", generation_id="gen-pending")
            is None
        )
        ready = await fetch_receipt(client, endpoint=ENDPOINT, api_key="k", generation_id="gen-ok")
        assert ready is not None and ready.prompt_tokens == 100
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_receipt(client, endpoint=ENDPOINT, api_key="k", generation_id="gen-broken")
    assert seen[0].url.path == "/api/v1/generation"
    assert seen[0].headers["Authorization"] == "Bearer k"
    assert seen[0].content == b""  # only the generation id in the query


class _MidStreamDisconnect:
    """Yields one chunk carrying a generation id, then fails (no usage reported)."""

    def __init__(self) -> None:
        self.sent = False
        self.aclose = AsyncMock()

    def __aiter__(self) -> _MidStreamDisconnect:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self.sent:
            raise RuntimeError("client disconnected")
        self.sent = True
        return {"id": "gen-1727-AbC", "choices": [{"delta": {"content": "partial"}}]}


@pytest.mark.asyncio
async def test_unfinished_stream_settles_the_full_hold_with_its_generation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(return_value=_MidStreamDisconnect()),
    ) as (service, _provider, _scope):
        with model_routing.routing_context("routine"):
            stream = await runtime.guarded_completion(
                messages=[{"role": "user", "content": "hi"}], stream=True
            )
            with pytest.raises(runtime.ComputeUnavailable):
                async for _ in stream:
                    pass
    args = service.settle.await_args
    assert args.args[1] == service.reserve.await_args.args[1]  # full hold
    assert args.kwargs["usage"] == {"estimated_cost": True, "generation_id": "gen-1727-AbC"}


class _FakeService:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.reconciled: list[tuple[Any, int]] = []
        self.unavailable: list[Any] = []

    async def receipt_candidates(self, *, settled_after: datetime, limit: int) -> list[dict]:
        return [row for row in self.rows if row["settled_at"] > settled_after][:limit]

    async def reconcile_to_receipt(self, rid: Any, charge: int, **_kw: Any) -> int:
        self.reconciled.append((rid, charge))
        return 7

    async def mark_receipt_unavailable(self, rid: Any) -> bool:
        self.unavailable.append(rid)
        return True


@pytest.mark.asyncio
async def test_sweep_reconciles_waits_expires_and_retries() -> None:
    route: Any = _route(model=FLASH)
    route.endpoint = ENDPOINT
    rows = [
        {"id": 1, "route_id": "r", "generation_id": "gen-ok", "settled_at": NOW},
        {"id": 2, "route_id": "r", "generation_id": "gen-pending", "settled_at": NOW},
        {
            "id": 3,
            "route_id": "r",
            "generation_id": "gen-pending",
            "settled_at": NOW - timedelta(hours=24, minutes=5),
        },
        {"id": 4, "route_id": "r", "generation_id": "gen-broken", "settled_at": NOW},
        {"id": 5, "route_id": "gone", "generation_id": "gen-ok", "settled_at": NOW},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        generation = request.url.params["id"]
        if generation == "gen-pending":
            return httpx.Response(404)
        if generation == "gen-broken":
            return httpx.Response(502)
        return httpx.Response(200, json=_payload())

    service = _FakeService(rows)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await reconcile_receipts(
            service, routes={"r": route}, client=client, api_key="k", now=NOW
        )
    assert [rid for rid, _ in service.reconciled] == [1]
    assert service.unavailable == [3]
    assert (result.examined, result.reconciled, result.pending, result.unavailable) == (5, 1, 2, 1)
    assert result.errors == 1
    assert result.refunded_microusd == 7


# --------------------------------------------------------------------------- ledger
@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[asyncpg.Pool, uuid.UUID]]:
    dsn = os.environ.get("ENTITLEMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires isolated ENTITLEMENTS_TEST_DATABASE_URL")
    schema = f"receipts_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
        )
        user_id = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute("INSERT INTO users VALUES ($1)", user_id)
            migrations = Path(__file__).resolve().parents[1] / "migrations"
            for name in ("039_entitlements_commercial.sql", "042_reservation_routing_labels.sql"):
                await conn.execute((migrations / name).read_text())
        yield pool, user_id
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _snapshot(pool: asyncpg.Pool, user_id: uuid.UUID, rid: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT actual_microusd, usage FROM entitlement_reservations WHERE id = $1", rid
        )
        spent = await conn.fetchval(
            "SELECT COALESCE(SUM(spent_microusd), 0) FROM entitlement_usage_periods "
            "WHERE user_id = $1",
            user_id,
        )
        trial = await conn.fetchrow(
            "SELECT trial_consumed_microusd, trial_state FROM entitlement_accounts "
            "WHERE user_id = $1",
            user_id,
        )
    assert row is not None and trial is not None
    return {
        "actual": row["actual_microusd"],
        "usage": json.loads(row["usage"]) if isinstance(row["usage"], str) else row["usage"],
        "spent": spent,
        "trial_consumed": trial["trial_consumed_microusd"],
        "trial_state": trial["trial_state"],
    }


@pytest.mark.asyncio
async def test_plan_charge_is_lowered_once_and_refunded_to_the_period(database) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    hold = await service.reserve(user_id, 1_000, operation="chat")
    await service.settle(hold, 1_000, usage={"estimated_cost": True, "generation_id": "gen-x"})
    before = await _snapshot(pool, user_id, hold.id)
    assert await service.reconcile_to_receipt(hold.id, 300, input_tokens=10, output_tokens=5) == 700
    after = await _snapshot(pool, user_id, hold.id)
    assert after["actual"] == 300
    assert after["spent"] == before["spent"] - 700
    assert after["usage"]["receipt_reconciled"] is True
    assert after["usage"]["output_tokens"] == 5
    # Idempotent: a second receipt never changes it again.
    assert await service.reconcile_to_receipt(hold.id, 1) == 0
    assert (await _snapshot(pool, user_id, hold.id))["actual"] == 300


@pytest.mark.asyncio
async def test_a_receipt_above_the_charge_never_raises_it(database) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    hold = await service.reserve(user_id, 1_000, operation="chat")
    await service.settle(hold, 1_000, usage={"estimated_cost": True, "generation_id": "gen-x"})
    before = await _snapshot(pool, user_id, hold.id)
    assert await service.reconcile_to_receipt(hold.id, 5_000) == 0
    after = await _snapshot(pool, user_id, hold.id)
    assert after["actual"] == 1_000 and after["spent"] == before["spent"]
    assert after["usage"]["receipt_reconciled"] is True


@pytest.mark.asyncio
async def test_trial_charge_is_refunded_to_trial_consumption(database) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    snapshot = await service.public_snapshot(user_id)
    amount = snapshot["trial"]["remaining_microusd"]
    hold = await service.reserve(user_id, amount, operation="chat", premium=True)
    await service.settle(hold, amount, usage={"estimated_cost": True, "generation_id": "gen-t"})
    exhausted = await _snapshot(pool, user_id, hold.id)
    assert exhausted["trial_state"] == "exhausted"
    refund = await service.reconcile_to_receipt(hold.id, amount // 2)
    restored = await _snapshot(pool, user_id, hold.id)
    assert refund == amount - amount // 2
    assert restored["trial_consumed"] == exhausted["trial_consumed"] - refund
    assert restored["trial_state"] == "active"


@pytest.mark.asyncio
async def test_metered_or_unkeyed_settlements_are_never_touched(database) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    metered = await service.reserve(user_id, 1_000, operation="chat")
    await service.settle(metered, 400, usage={"input_tokens": 10, "output_tokens": 5})
    assert await service.reconcile_to_receipt(metered.id, 1) == 0
    unkeyed = await service.reserve(user_id, 1_000, operation="chat")
    await service.settle(unkeyed, 1_000, usage={"estimated_cost": True})
    assert await service.reconcile_to_receipt(unkeyed.id, 1) == 0
    assert (await _snapshot(pool, user_id, unkeyed.id))["actual"] == 1_000


@pytest.mark.asyncio
async def test_candidates_and_unavailable_marking(database) -> None:
    pool, user_id = database
    service = EntitlementService(pool)
    hold = await service.reserve(user_id, 1_000, operation="chat", route_id="r")
    await service.settle(hold, 1_000, usage={"estimated_cost": True, "generation_id": "gen-c"})
    candidates = await service.receipt_candidates(
        settled_after=datetime.now(timezone.utc) - timedelta(hours=1), limit=10
    )
    assert [row["id"] for row in candidates] == [hold.id]
    assert candidates[0]["generation_id"] == "gen-c"
    assert await service.mark_receipt_unavailable(hold.id) is True
    assert await service.mark_receipt_unavailable(hold.id) is False
    assert (
        await service.receipt_candidates(
            settled_after=datetime.now(timezone.utc) - timedelta(hours=1), limit=10
        )
        == []
    )
    assert (await _snapshot(pool, user_id, hold.id))["actual"] == 1_000
