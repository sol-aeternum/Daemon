"""Fail-closed account scope, approved inference routes and atomic spend reservations.

Every LiteLLM call must pass through ``guarded_completion``. A stream retains its
reservation until its iterator is closed, including cancellation and exceptions.
Unpriced modalities cannot be estimated by this text-token ledger and are denied.
"""

from __future__ import annotations

import json
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast

import litellm

from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.policy import RoutePolicy, load_inference_policy
from orchestrator.entitlements.errors import BudgetExceeded, PolicyError
from orchestrator.config import get_settings


class ComputeUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class ComputeScope:
    user_id: uuid.UUID
    service: EntitlementService
    operation: str = "chat"
    auto_route: bool = False
    extended: bool = False
    extended_started: bool = False
    extended_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    selected_model: str | None = None
    outstanding: dict[int, ReservationHold] = field(default_factory=dict)
    settled: dict[int, int] = field(default_factory=dict)

    async def settle(
        self, reservation: Any, amount: int, *, usage: dict[str, int] | None = None
    ) -> None:
        key = id(reservation)
        hold = self.outstanding.get(key)
        if hold is None:
            if self.settled.get(key) == amount:
                return
            raise ComputeUnavailable(
                "settlement_conflict", "Reservation already settled differently"
            )
        if hold.settlement is None:
            # One task per reservation prevents racing a stream-finally against
            # the account-scope-finally on client cancellation.
            hold.actual = amount
            if usage:
                hold.settlement = asyncio.create_task(
                    self.service.settle(reservation, amount, usage=usage)
                )
            else:
                hold.settlement = asyncio.create_task(self.service.settle(reservation, amount))
        elif hold.actual != amount:
            raise ComputeUnavailable(
                "settlement_conflict", "Reservation already settling differently"
            )
        await asyncio.shield(hold.settlement)
        self.settled[key] = amount
        self.outstanding.pop(key, None)


@dataclass
class ReservationHold:
    reservation: Any
    bound: int
    settlement: asyncio.Task[Any] | None = None
    actual: int | None = None


_scope: ContextVar[ComputeScope | None] = ContextVar("compute_scope", default=None)


@asynccontextmanager
async def account_compute(
    pool: Any,
    user_id: uuid.UUID,
    *,
    operation: str = "chat",
    auto_route: bool = False,
    extended: bool = False,
) -> AsyncIterator[ComputeScope]:
    if pool is None or not isinstance(user_id, uuid.UUID):
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable")
    scope = ComputeScope(user_id, EntitlementService(pool), operation, auto_route, extended)
    await scope.service.reconcile_expired_reservations(
        user_id,
        before=datetime.now(timezone.utc) - timedelta(seconds=2 * get_settings().request_timeout_s),
    )
    if extended and "extended_agents" not in (await scope.service.resolve(user_id)).capabilities:
        raise ComputeUnavailable(
            "extended_agents_exhausted",
            "Extended agent allowance unavailable; normal chat is still available",
        )
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        try:
            settlements = [
                asyncio.create_task(
                    scope.settle(
                        hold.reservation, hold.actual if hold.actual is not None else hold.bound
                    )
                )
                for hold in tuple(scope.outstanding.values())
            ]
            if settlements:
                await asyncio.gather(*settlements)
        finally:
            _scope.reset(token)


def current_scope() -> ComputeScope:
    scope = _scope.get()
    if scope is None:
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable")
    return scope


def selected_model() -> str | None:
    """The actual approved model most recently sent in this account scope."""
    scope = _scope.get()
    return scope.selected_model if scope else None


def choose_route(model: str | None = None) -> RoutePolicy:
    try:
        policy = load_inference_policy()
        routes = [
            route
            for route in policy.routes.values()
            if route.is_approved(policy.requirements)
            and route.provider == "openrouter"
            and route.model.startswith("openrouter/")
            and route.price_ceiling is not None
            and getattr(route, "route_class", None) in {"routine", "premium"}
        ]
        if model:
            routes = [route for route in routes if route.model == model]
        else:
            routes = [route for route in routes if getattr(route, "route_class", None) == "routine"]
        if routes:
            return min(
                routes,
                key=lambda route: (
                    route.price_ceiling.microusd_per_1m_prompt
                    + route.price_ceiling.microusd_per_1m_completion
                    if route.price_ceiling
                    else float("inf"),
                    route.route_id,
                ),
            )
    except PolicyError as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc
    raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")


def _priced_candidates(
    policy: Any,
    input_tokens: int,
    params: dict[str, Any],
    requested_model: str | None,
    extended: bool = False,
) -> list[tuple[int, int, RoutePolicy, bool]]:
    """Choose only funded approved routes, ranked by this request's worst case."""
    try:
        inference = load_inference_policy()
    except PolicyError as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc
    candidates: list[tuple[int, int, RoutePolicy, bool]] = []
    for route in inference.routes.values():
        if (
            not route.is_approved(inference.requirements)
            or route.provider != "openrouter"
            or not route.model.startswith("openrouter/")
            or route.price_ceiling is None
        ):
            continue
        route_class = getattr(route, "route_class", None)
        if route_class not in {"routine", "premium"}:
            continue
        if requested_model and route.model != requested_model:
            continue
        premium_route = route_class == "premium"
        premium = premium_route or extended
        if premium_route and requested_model is None:
            continue
        if premium_route and "premium_routing" not in policy.capabilities:
            continue
        limits = policy.limits_for(premium)
        if input_tokens > limits.max_context_tokens:
            continue
        output_tokens = min(
            limits.max_output_tokens,
            route.max_output_tokens,
            route.max_context_tokens - input_tokens,
        )
        configured_max = params.get("max_tokens")
        if configured_max is not None:
            if isinstance(configured_max, bool) or not isinstance(configured_max, int):
                raise ComputeUnavailable("capacity_unavailable", "Invalid output token limit")
            output_tokens = min(output_tokens, configured_max)
        if output_tokens <= 0:
            continue
        required = {"text"}
        if params.get("tools"):
            required.add("tools")
        if (
            isinstance(params.get("response_format"), dict)
            and params["response_format"].get("type") == "json_schema"
        ):
            required.add("json_schema")
        if not route.supports(
            required_capabilities=frozenset(required),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ):
            continue
        bound = route.estimate_microusd(input_tokens, output_tokens)
        if bound > policy.remaining_for(premium):
            continue
        candidates.append((bound, output_tokens, route, premium))
    candidates.sort(key=lambda candidate: (candidate[0], candidate[2].route_id))
    return candidates


def _input_bound(messages: Any, tools: Any) -> int:
    # Only plain text and explicitly typed text parts are eligible. A short
    # image/audio URL can incur a large provider-side bill unrelated to its
    # byte length, so never infer multimodal cost from serialized JSON size.
    if not isinstance(messages, list) or not messages:
        raise ComputeUnavailable("modality_unavailable", "Text messages required")
    for message in messages:
        if not isinstance(message, dict):
            raise ComputeUnavailable("modality_unavailable", "Text messages required")
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if (
                    not isinstance(part, dict)
                    or part.get("type") != "text"
                    or set(part) != {"type", "text"}
                    or not isinstance(part["text"], str)
                ):
                    raise ComputeUnavailable(
                        "modality_unavailable", "Multimodal compute unavailable"
                    )
        elif content is not None and not isinstance(content, str):
            raise ComputeUnavailable("modality_unavailable", "Multimodal compute unavailable")
        if any(
            key in message
            for key in ("image_url", "images", "audio", "video", "file", "attachments")
        ):
            raise ComputeUnavailable("modality_unavailable", "Multimodal compute unavailable")
    encoded = json.dumps([messages, tools], ensure_ascii=False).encode("utf-8")
    if len(encoded) > 1_000_000:
        raise ComputeUnavailable("context_limit", "Context too large")
    return max(1, len(encoded) + 512 * len(messages))


_COMPLETION_FIELDS = frozenset(
    {
        "model",
        "messages",
        "stream",
        "timeout",
        "tools",
        "tool_choice",
        "api_base",
        "api_key",
        "extra_headers",
        "temperature",
        "max_tokens",
        "response_format",
        "stop",
        "seed",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stream_options",
        "n",
        "reasoning_effort",
        "include_reasoning",
    }
)

_REASONING_EFFORTS = frozenset({"low", "medium", "high"})


def _request_bound(params: dict[str, Any]) -> int:
    unsupported = set(params) - _COMPLETION_FIELDS
    if unsupported:
        raise ComputeUnavailable("capacity_unavailable", "Unsupported completion parameters")
    if "reasoning_effort" in params:
        effort = params["reasoning_effort"]
        if type(effort) is not str or effort not in _REASONING_EFFORTS:
            raise ComputeUnavailable("capacity_unavailable", "Unsupported reasoning effort")
    if "include_reasoning" in params and type(params["include_reasoning"]) is not bool:
        raise ComputeUnavailable("capacity_unavailable", "Invalid reasoning option")
    text_bytes = _input_bound(params.get("messages"), params.get("tools"))
    prompt_params = {
        key: params[key]
        for key in (
            "response_format",
            "stop",
            "tool_choice",
        )
        if key in params
    }
    try:
        serialized = json.dumps(prompt_params, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComputeUnavailable(
            "capacity_unavailable", "Unsupported completion parameters"
        ) from exc
    return text_bytes + len(serialized)


def _usage_counts(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None

    def get(name: str) -> Any:
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

    prompt = get("prompt_tokens")
    completion = get("completion_tokens")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (prompt, completion)):
        return None
    return {"input_tokens": prompt, "output_tokens": completion}


def _usage_charge(usage: Any, route: RoutePolicy, bound: int) -> int | None:
    """Ceiling-price charge from validated provider usage; None if untrusted."""
    counts = _usage_counts(usage)
    if counts is None or route.price_ceiling is None:
        return None
    charge = route.estimate_microusd(counts["input_tokens"], counts["output_tokens"])
    return charge


async def guarded_completion(**params: Any) -> Any:
    scope = current_scope()
    deadline = asyncio.get_running_loop().time() + get_settings().request_timeout_s
    policy = await scope.service.resolve(scope.user_id)
    if "chat" not in policy.capabilities:
        raise ComputeUnavailable("capability_unavailable", "Chat unavailable")
    if (
        scope.extended
        and not scope.extended_started
        and "extended_agents" not in policy.capabilities
    ):
        raise ComputeUnavailable(
            "extended_agents_exhausted",
            "Extended agent allowance unavailable; normal chat is still available",
        )
    if params.get("api_key") is not None and params["api_key"] != get_settings().openrouter_api_key:
        raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
    if type(params.get("n", 1)) is not int or params.get("n", 1) != 1:
        raise ComputeUnavailable("capacity_unavailable", "Compute capacity unavailable")
    if params.get("extra_headers") not in (
        None,
        get_settings().get_provider_config("openrouter").extra_headers,
    ):
        raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
    if any(
        key in params for key in ("images", "audio", "video", "files", "attachments", "input_audio")
    ):
        raise ComputeUnavailable("modality_unavailable", "Multimodal compute unavailable")

    input_tokens = _request_bound(params)
    requested = params.get("model")
    model = requested if isinstance(requested, str) and not scope.auto_route else None
    candidates = _priced_candidates(policy, input_tokens, params, model, scope.extended)
    if not candidates:
        if model:
            # Explicit choices are never silently downgraded.
            route = choose_route(model)
            if route.route_class == "premium" and "premium_routing" not in policy.capabilities:
                raise ComputeUnavailable("capability_unavailable", "Premium routing unavailable")
        elif input_tokens > policy.limits.max_context_tokens:
            raise ComputeUnavailable("context_limit", "Context too large")
        else:
            choose_route()
        raise ComputeUnavailable("budget_exceeded", "No qualified route fits the account budget")

    async def dispatch(candidate: tuple[int, int, RoutePolicy, bool]) -> tuple[Any, Any]:
        bound, output_tokens, route, premium = candidate
        inference = load_inference_policy()
        # Build the central reviewed transport block BEFORE holding budget so
        # an expired review never reserves capacity for a call we cannot send.
        transport = route.transport_payload(inference.requirements)
        if params.get("api_base") not in (None, route.endpoint):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        call = {
            **params,
            "model": route.model,
            "api_base": route.endpoint,
            "max_tokens": output_tokens,
            "num_retries": 0,
            "timeout": max(0, deadline - asyncio.get_running_loop().time()),
            "extra_headers": get_settings().get_provider_config("openrouter").extra_headers,
            **transport,
        }
        try:
            async with scope.extended_lock:
                first_extended = scope.extended and not scope.extended_started
                reservation = await scope.service.reserve(
                    scope.user_id,
                    bound,
                    operation=scope.operation,
                    provider=route.provider,
                    model=route.model,
                    route_id=route.route_id,
                    premium=premium,
                    extended=first_extended,
                )
                if first_extended:
                    scope.extended_started = True
        except BudgetExceeded:
            # A concurrent request may have consumed the observed allowance.
            # The caller may retry the next candidate with a fresh reservation.
            raise
        scope.outstanding[id(reservation)] = ReservationHold(reservation, bound)
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**call),
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
        except BaseException:
            await scope.settle(reservation, bound)
            raise
        scope.selected_model = route.model
        return response, reservation

    async def first_response(start: int) -> tuple[int, Any, Any]:
        for index in range(start, len(candidates)):
            try:
                response, reservation = await dispatch(candidates[index])
                return index, response, reservation
            except BudgetExceeded:
                if not scope.auto_route:
                    raise
            except Exception:
                if not scope.auto_route:
                    raise ComputeUnavailable(
                        "capacity_unavailable", "Qualified provider unavailable"
                    ) from None
        raise ComputeUnavailable("capacity_unavailable", "No qualified route fits capacity")

    index, response, reservation = await first_response(0)
    if not params.get("stream"):
        bound, _, route, _ = candidates[index]
        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        charge = _usage_charge(usage, route, bound)
        await scope.settle(
            reservation,
            charge if charge is not None else bound,
            usage=_usage_counts(usage) if charge is not None else {"estimated_cost": True},
        )
        return response

    async def metered_stream() -> AsyncIterator[Any]:
        nonlocal index, response, reservation
        while True:
            bound, _, route, _ = candidates[index]
            charge: int | None = None
            usage_counts: dict[str, int] | None = None
            completed = False
            emitted = False
            failed = False
            try:
                async with asyncio.timeout_at(deadline):
                    async for chunk in cast(AsyncIterator[Any], response):
                        chunk_usage = (
                            chunk.get("usage")
                            if isinstance(chunk, dict)
                            else getattr(chunk, "usage", None)
                        )
                        parsed = _usage_charge(chunk_usage, route, bound)
                        if parsed is not None:
                            charge = parsed
                            usage_counts = _usage_counts(chunk_usage)
                        emitted = True
                        yield chunk
                completed = True
            except Exception:
                failed = True
                if emitted or not scope.auto_route or index + 1 >= len(candidates):
                    raise ComputeUnavailable(
                        "capacity_unavailable", "Qualified provider unavailable"
                    ) from None
            finally:
                await scope.settle(
                    reservation,
                    charge if completed and charge is not None else bound,
                    usage=usage_counts
                    if completed and charge is not None
                    else {"estimated_cost": True},
                )
            if not failed:
                return
            index, response, reservation = await first_response(index + 1)

    return metered_stream()
