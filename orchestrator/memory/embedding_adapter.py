"""Dedicated, single-send Azure/OpenRouter small-1024 embedding adapter.

The route is OFF until an operator approves it and selects it. No credentials,
private input, vectors, provider bodies or exception messages are logged here.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

import httpx

from orchestrator.compute_runtime import (
    ComputeUnavailable,
    FAILURE_SETTLEMENT_FAILED,
    current_scope,
    metered_embedding_call,
)
from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.policy import EmbeddingRoutePolicy, load_inference_policy

MODEL = "openai/text-embedding-3-small"
ENDPOINT = "https://openrouter.ai/api/v1"
DIMENSIONS = 1024
STORAGE_MODEL = "openrouter:azure:openai/text-embedding-3-small:1024"
# Exact native alias observed in the prior fictional screen; not suffix matching.
# OpenAI's model documentation names this same native id.
NATIVE_RECEIPT_MODELS = {MODEL: "text-embedding-3-small"}
PROVIDER_RECEIPT_NAMES = frozenset({"azure", "Azure"})
MAX_INPUT_TOKENS = 8000
MAX_BATCH_TOKENS = 100_000
MAX_BATCH_ITEMS = 100
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 60.0


class AdapterUnavailable(Exception):
    """A pre-send configuration/account denial; nullable vectors remain allowed."""


class AdapterReceiptError(Exception):
    """An unknown provider outcome, charged at the hold without any retry."""


def validate_adapter_route(route: EmbeddingRoutePolicy) -> None:
    """Exact implementation identity and safety limits, independent of approval."""
    if (
        route.provider != "openrouter"
        or route.endpoint != ENDPOINT
        or route.model != MODEL
        or route.dimensions != DIMENSIONS
        or route.transport.provider_only != ("azure",)
        or route.transport.provider_order != ("azure",)
        or not 0 < route.max_input_tokens <= MAX_INPUT_TOKENS
        or not route.max_input_tokens <= route.max_batch_tokens <= MAX_BATCH_TOKENS
        or not 0 < route.max_batch_items <= MAX_BATCH_ITEMS
        or route.approval_mode != "monitored"
        or route.price_ceiling is None
        or not 0 < route.price_ceiling.microusd_per_1m_prompt <= 20000
        or route.price_ceiling.microusd_per_1m_completion != 0
    ):
        raise AdapterUnavailable("Embedding adapter configuration invalid")


def approved_route(route_id: str) -> EmbeddingRoutePolicy:
    try:
        policy = load_inference_policy()
        route = policy.embedding_route(route_id)
        if route is None:
            raise AdapterUnavailable("Approved embedding route unavailable")
        validate_adapter_route(route)
        if not route.is_approved(policy.requirements):
            raise AdapterUnavailable("Approved embedding route unavailable")
        return route
    except PolicyError:
        raise AdapterUnavailable("Embedding inference policy invalid") from None


def input_bound(text: str) -> int:
    # A UTF-8 byte upper bound is conservative for the model's byte-BPE tokenizer.
    # Small per-item overhead also bounds framing/special-token accounting.
    return len(text.encode("utf-8")) + 16


def input_batches(texts: list[str], route: EmbeddingRoutePolicy) -> list[list[str]]:
    """Validate the whole request before any hold/send, then split without truncation."""
    if len(texts) > 1000:
        raise AdapterReceiptError("Too many embedding inputs")
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise AdapterReceiptError("Embedding inputs must be nonempty text")
        try:
            bound = input_bound(text)
        except UnicodeError:
            raise AdapterReceiptError("Embedding input is not valid Unicode") from None
        if bound > route.max_input_tokens:
            raise AdapterReceiptError("Embedding input exceeds its token bound")
    batches: list[list[str]] = []
    batch: list[str] = []
    tokens = 0
    for text in texts:
        bound = input_bound(text)
        if batch and (
            len(batch) >= route.max_batch_items or tokens + bound > route.max_batch_tokens
        ):
            batches.append(batch)
            batch, tokens = [], 0
        batch.append(text)
        tokens += bound
    if batch:
        batches.append(batch)
    return batches


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterReceiptError("Ambiguous embedding receipt")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise AdapterReceiptError("Nonfinite embedding receipt")


def validate_receipt(
    payload: object, route: EmbeddingRoutePolicy, count: int
) -> tuple[list[list[float]], int, int]:
    """Validate identity/vectors/usage and return vectors, input tokens, micro-USD."""
    if not isinstance(payload, dict):
        raise AdapterReceiptError("Invalid embedding receipt")
    if payload.get("model") not in (route.model, NATIVE_RECEIPT_MODELS[route.model]):
        raise AdapterReceiptError("Embedding model differs from the pin")
    # Azure embedding receipts may omit provider (observed in the old screen).
    # That is not independent provider attestation; the enforced request pin and
    # monitored route remain mandatory. Any contradictory receipt is rejected.
    if "provider" in payload and (
        not isinstance(payload["provider"], str)
        or payload["provider"] not in PROVIDER_RECEIPT_NAMES
    ):
        raise AdapterReceiptError("Embedding provider differs from the pin")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise AdapterReceiptError("Embedding count differs from request")
    ordered: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise AdapterReceiptError("Invalid embedding data")
        index = item.get("index")
        vector = item.get("embedding")
        if type(index) is not int or not 0 <= index < count or index in ordered:
            raise AdapterReceiptError("Invalid embedding index")
        if not isinstance(vector, list) or len(vector) != route.dimensions:
            raise AdapterReceiptError("Invalid embedding dimensions")
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in vector):
            raise AdapterReceiptError("Invalid embedding values")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise AdapterReceiptError("Invalid embedding norm")
        ordered[index] = [float(value) for value in vector]
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise AdapterReceiptError("Missing embedding usage")
    tokens, total = usage.get("prompt_tokens"), usage.get("total_tokens")
    if type(tokens) is not int or tokens <= 0 or type(total) is not int or total != tokens:
        raise AdapterReceiptError("Inconsistent embedding input usage")
    if "completion_tokens" in usage and (
        type(usage["completion_tokens"]) is not int or usage["completion_tokens"] != 0
    ):
        raise AdapterReceiptError("Invalid embedding output usage")
    ceiling = route.estimate_microusd(tokens)
    actual = ceiling
    if "cost" in usage:
        raw = usage["cost"]
        if type(raw) not in (float, int, str):
            raise AdapterReceiptError("Invalid embedding cost")
        try:
            cost = Decimal(str(raw))
        except InvalidOperation:
            raise AdapterReceiptError("Invalid embedding cost") from None
        assert route.price_ceiling is not None
        max_usd = Decimal(tokens) * route.price_ceiling.microusd_per_1m_prompt / 1_000_000_000_000
        if not cost.is_finite() or cost < 0 or cost > max_usd:
            raise AdapterReceiptError("Embedding cost exceeds its pinned rate")
        actual = int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    return [ordered[index] for index in range(count)], tokens, actual


async def _post(
    client: httpx.AsyncClient,
    route: EmbeddingRoutePolicy,
    texts: list[str],
    api_key: str,
    provider_options: dict[str, Any],
) -> object:
    async with client.stream(
        "POST",
        f"{route.endpoint}/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
        json={
            "model": route.model,
            "input": texts,
            "dimensions": route.dimensions,
            "encoding_format": "float",
            "provider": provider_options,
        },
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower().strip() not in (
            "",
            "identity",
        ):
            raise AdapterReceiptError("Encoded embedding receipt refused")
        size = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise AdapterReceiptError("Embedding receipt exceeds size bound")
            chunks.append(chunk)
    return json.loads(
        b"".join(chunks), object_pairs_hook=_strict_object, parse_constant=_reject_constant
    )


async def embed(texts: list[str], *, route_id: str, api_key: str) -> list[list[float]]:
    if not texts:
        return []
    route = approved_route(route_id)
    if not api_key:
        raise AdapterUnavailable("Embedding credentials unavailable")
    batches = input_batches(texts, route)
    try:
        current_scope()
    except ComputeUnavailable:
        raise AdapterUnavailable("Embedding account scope unavailable") from None
    vectors: list[list[float]] = []
    try:
        # No retries, redirects, proxies, fallback route, or identifying headers.
        async with httpx.AsyncClient(
            timeout=TIMEOUT_S,
            follow_redirects=False,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        ) as client:
            for batch in batches:
                bound = sum(input_bound(text) for text in batch)
                async with metered_embedding_call(route, bound) as charge:
                    fresh = approved_route(route_id)  # fresh attestation snapshot after hold
                    if fresh != route:
                        raise AdapterUnavailable("Embedding approval changed before send")
                    policy = load_inference_policy()
                    options = fresh.transport_payload(policy.requirements)["extra_body"]["provider"]
                    charge.mark_dispatched()  # immediately before the one HTTP POST
                    receipt = await _post(client, fresh, batch, api_key, options)
                    batch_vectors, tokens, cost = validate_receipt(receipt, fresh, len(batch))
                    charge.confirm(input_tokens=tokens, actual_microusd=cost)
                vectors.extend(batch_vectors)  # release only after accounting succeeds
    except ComputeUnavailable as exc:
        if exc.category == FAILURE_SETTLEMENT_FAILED or exc.code.startswith("settlement"):
            raise
        raise AdapterUnavailable("Embedding account capacity unavailable") from None
    except (AdapterUnavailable, AdapterReceiptError):
        raise
    except (httpx.HTTPError, ValueError, OverflowError):
        raise AdapterReceiptError("Embedding provider outcome unknown") from None
    return vectors
