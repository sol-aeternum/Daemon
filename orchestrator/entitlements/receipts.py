"""Reconcile conservative full-hold settlements to provider receipts (optional work O3).

When a stream ends without a usage report (client disconnect, provider omitted
usage), settlement charges the full reservation hold, because unknown work is never
assumed free. If the stream carried an OpenRouter generation id, its receipt
(``GET {endpoint}/generation?id=...``) later states the tokens actually billed. This
sweep prices that receipt conservatively and lowers the charge to it:

    receipt charge = max(ceiling price of receipt tokens, receipt's own total cost)
    new charge     = min(settled charge, receipt charge)

so a reconciliation can only refund, never add, and never charges below what the
provider reports. Receipts are fetched from the route's pinned, approved endpoint
with only the generation id; no message content is sent or received. A receipt that
has not appeared within ``RECEIPT_WINDOW`` is marked unavailable and the full
charge stands.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from orchestrator.entitlements.policy import RoutePolicy

logger = logging.getLogger(__name__)

#: How long to keep looking for a receipt before the full charge is final.
RECEIPT_WINDOW = timedelta(hours=24)
#: Reservations examined per sweep.
SWEEP_LIMIT = 50
RECEIPT_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class Receipt:
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_cost_usd: float | None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def parse_receipt(payload: Any) -> Receipt | None:
    """The token counts and cost of an OpenRouter generation receipt, if well-formed."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    prompt = data.get("native_tokens_prompt", data.get("tokens_prompt"))
    completion = data.get("native_tokens_completion", data.get("tokens_completion"))
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    cost = data.get("total_cost")
    return Receipt(
        prompt_tokens=_count(prompt),
        completion_tokens=_count(completion),
        reasoning_tokens=_count(data.get("native_tokens_reasoning")),
        total_cost_usd=float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None,
    )


def receipt_charge(route: RoutePolicy, receipt: Receipt) -> int:
    """Conservative microusd charge for a receipt.

    Reasoning tokens are added to completion tokens even if the provider already
    counts them there: double counting can only overcharge, never undercharge.
    """
    tokens = route.estimate_microusd(
        receipt.prompt_tokens, receipt.completion_tokens + receipt.reasoning_tokens
    )
    reported = (
        math.ceil(receipt.total_cost_usd * 1_000_000) if receipt.total_cost_usd is not None else 0
    )
    return max(tokens, reported)


async def fetch_receipt(
    client: httpx.AsyncClient, *, endpoint: str, api_key: str, generation_id: str
) -> Receipt | None:
    """Fetch a receipt; ``None`` while it is not yet available (404)."""
    response = await client.get(
        f"{endpoint.rstrip('/')}/generation",
        params={"id": generation_id},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=RECEIPT_TIMEOUT_S,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_receipt(response.json())


@dataclass
class SweepResult:
    examined: int = 0
    reconciled: int = 0
    refunded_microusd: int = 0
    pending: int = 0
    unavailable: int = 0
    errors: int = 0


async def reconcile_receipts(
    service: Any,
    *,
    routes: dict[str, RoutePolicy],
    client: httpx.AsyncClient,
    api_key: str,
    now: datetime,
) -> SweepResult:
    """One bounded sweep over estimated settlements awaiting a receipt."""
    result = SweepResult()
    candidates = await service.receipt_candidates(
        settled_after=now - RECEIPT_WINDOW - timedelta(hours=1), limit=SWEEP_LIMIT
    )
    for row in candidates:
        result.examined += 1
        expired = row["settled_at"] <= now - RECEIPT_WINDOW
        route = routes.get(row["route_id"]) if row["route_id"] else None
        receipt: Receipt | None = None
        if route is not None and route.endpoint:
            try:
                receipt = await fetch_receipt(
                    client,
                    endpoint=route.endpoint,
                    api_key=api_key,
                    generation_id=row["generation_id"],
                )
            except (httpx.HTTPError, ValueError):
                # Network or upstream failure: try again on a later sweep.
                logger.warning("Receipt fetch failed; will retry")
                result.errors += 1
                continue
        if receipt is None or route is None:
            if expired:
                if await service.mark_receipt_unavailable(row["id"]):
                    result.unavailable += 1
            else:
                result.pending += 1
            continue
        refund = await service.reconcile_to_receipt(
            row["id"],
            receipt_charge(route, receipt),
            input_tokens=receipt.prompt_tokens,
            output_tokens=receipt.completion_tokens + receipt.reasoning_tokens,
        )
        result.reconciled += 1
        result.refunded_microusd += refund
    return result
