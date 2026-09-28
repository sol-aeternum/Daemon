"""Fail-closed account scope, approved inference routes and atomic spend reservations.

Every LiteLLM call must pass through ``guarded_completion``. A stream retains its
reservation until its iterator is closed, including cancellation and exceptions.
Unpriced modalities cannot be estimated by this text-token ledger and are denied.

Selection is capability-first and cheapest-acceptable, not cheapest: the account
scope binds a workload profile from :mod:`orchestrator.model_routing`, that profile
supplies the ordered groups of acceptable models and the comparable output budget
for the job, and the cheapest candidate in the first group with any qualified
candidate is chosen *for this request's own bound*. A model outside a profile's
groups is not cheaper, it is not a candidate. Nothing here qualifies a route: the
independent approval gate in ``config/inference_policy.json`` remains the sole
permission to dispatch, and the account's resolved entitlements and budget remain
the sole source of limits.

For a caller that owns its own redundancy, ``guarded_completion`` also takes an
exact route pin and a per-dispatch timeout. That seam dispatches **one** qualified
route and reports what happened; it deliberately contains no failover loop, so a
retry decision stays with the caller that also owns the second call's budget.
"""

from __future__ import annotations

import json
import math
import inspect
import logging
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Final, cast

import httpx
import litellm
from litellm.exceptions import APIConnectionError, Timeout
from openai import APIError as OpenAIAPIError

from orchestrator import model_routing
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.policy import RoutePolicy, load_inference_policy
from orchestrator.entitlements.errors import (
    AccessDenied,
    AccountError,
    AccountSuspended,
    BudgetExceeded,
    EntitlementsError,
    LimitExceeded,
    PolicyError,
)
from orchestrator.config import get_settings

logger = logging.getLogger(__name__)
STREAM_CLOSE_TIMEOUT_S = 2.0


def _consume_close_result(task: asyncio.Task[None]) -> None:
    """Retrieve errors from abandoned transport cleanup without blocking accounting."""
    if not task.cancelled():
        task.exception()


#: Sanitized failure categories. A closed vocabulary, so a recorded dispatch
#: failure is comparable across runs without carrying provider text.
FAILURE_RATE_LIMITED: Final[str] = "rate_limited"
FAILURE_UPSTREAM_UNAVAILABLE: Final[str] = "upstream_unavailable"
FAILURE_CONNECTION_FAILED: Final[str] = "connection_failed"
FAILURE_TIMEOUT: Final[str] = "timeout"
FAILURE_DEADLINE_EXCEEDED: Final[str] = "deadline_exceeded"
FAILURE_AUTHENTICATION_FAILED: Final[str] = "authentication_failed"
FAILURE_PAYMENT_FAILED: Final[str] = "payment_failed"
FAILURE_INVALID_REQUEST: Final[str] = "invalid_request"
FAILURE_SETTLEMENT_FAILED: Final[str] = "settlement_failed"
FAILURE_UNSPECIFIED: Final[str] = "unspecified"

#: The only upstream statuses approved as a same-model retry trigger.
RETRY_ELIGIBLE_STATUS: Final[frozenset[int]] = frozenset({429, 502, 503, 504})

#: Transport-level timeouts and connection failures, checked before status codes
#: because litellm's own classes carry synthetic default statuses that would
#: otherwise read as ordinary HTTP failures.
_TIMEOUT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    Timeout,
    httpx.TimeoutException,
    asyncio.TimeoutError,
)
_CONNECTION_ERRORS: Final[tuple[type[BaseException], ...]] = (
    APIConnectionError,
    httpx.TransportError,
)


class ComputeUnavailable(Exception):
    """An account or compute denial, plus a sanitized retry classification.

    ``category``, ``status_code``, ``retry_after_seconds`` and ``retryable`` are
    the structured form of the generic message, so a caller that owns a failover
    decision never has to parse prose. They are derived from the exception *type*
    and the HTTP status code only — never from an exception message — and they
    carry no provider body, endpoint, prompt or credential, which makes them safe
    to record in an audit artifact.

    ``retryable`` is ``False`` unless the failure was positively classified as an
    upstream 429/502/503/504, a connection failure, or a timeout raised before the
    operation deadline was spent and before any output was released. Denials,
    cancellation, unsettled charges and unclassified exceptions therefore all stop
    by default, and a settlement failure can never be presented as retryable.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = FAILURE_UNSPECIFIED,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.category = category
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DispatchFailure:
    """One failed provider dispatch, reduced to a comparable, safe summary."""

    category: str
    status_code: int | None = None
    retry_after_seconds: float | None = None
    retryable: bool = False

    def as_unavailable(self, code: str, message: str) -> ComputeUnavailable:
        """The boundary exception callers see, carrying this classification."""
        return ComputeUnavailable(
            code,
            message,
            category=self.category,
            status_code=self.status_code,
            retry_after_seconds=self.retry_after_seconds,
            retryable=self.retryable,
        )


def _http_status(exc: BaseException) -> int | None:
    """The upstream HTTP status, when the exception structurally carries one.

    Only the SDK's transport errors are asked, and only for an in-range integer:
    an arbitrary exception with a lookalike attribute is not evidence of an
    upstream response.
    """
    if not isinstance(exc, OpenAIAPIError):
        return None
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status if 100 <= status <= 599 else None


def _provider_retry_after(exc: BaseException) -> float | None:
    """Validated Retry-After delay; the caller caps its cooldown at period end."""
    getter = getattr(getattr(exc, "response", None), "headers", None)
    get = getattr(getter, "get", None)
    if not callable(get):
        return None
    try:
        raw = get("retry-after")
    except (TypeError, ValueError):
        return None
    try:
        seconds = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        if not isinstance(raw, str):
            return None
        try:
            retry_at = parsedate_to_datetime(raw)
            if retry_at.tzinfo is None:
                return None
            seconds = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError, IndexError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _classify_dispatch_failure(
    exc: BaseException, *, deadline_bound: bool, output_released: bool
) -> DispatchFailure:
    """Reduce a provider failure to a sanitized category and retry decision.

    Classification reads the exception type and the HTTP status code, and nothing
    else: a message that merely *looks* like an upstream 503 is unclassified and
    stops. ``deadline_bound`` says the operation's deadline was exhausted, and
    ``output_released`` says a stream had already delivered output, either of
    which removes the approved retry eligibility without changing the category.
    """
    status = _http_status(exc)
    retryable = False
    if isinstance(exc, _TIMEOUT_ERRORS):
        category = FAILURE_DEADLINE_EXCEEDED if deadline_bound else FAILURE_TIMEOUT
        retryable = not deadline_bound
        status = None
    elif isinstance(exc, _CONNECTION_ERRORS):
        category = FAILURE_CONNECTION_FAILED
        retryable = True
        status = None
    elif status == 429:
        category = FAILURE_RATE_LIMITED
        retryable = True
    elif status in RETRY_ELIGIBLE_STATUS:
        category = FAILURE_UPSTREAM_UNAVAILABLE
        retryable = True
    elif status == 408:
        # An upstream timeout *response* is a timeout to read, but only a
        # transport timeout or 429/502/503/504 is an approved retry trigger.
        category = FAILURE_TIMEOUT
    elif status in {401, 403}:
        category = FAILURE_AUTHENTICATION_FAILED
    elif status == 402:
        category = FAILURE_PAYMENT_FAILED
    elif status is not None and 400 <= status < 500:
        category = FAILURE_INVALID_REQUEST
    else:
        category = FAILURE_UNSPECIFIED
    # Once a token or tool event has been released the work is observable, so
    # the same transport failure stops instead of becoming a second dispatch.
    eligible = retryable and not output_released and not deadline_bound
    return DispatchFailure(
        category=category,
        status_code=status,
        retry_after_seconds=_provider_retry_after(exc) if eligible else None,
        retryable=eligible,
    )


def _dispatch_budget_s(value: float | None, remaining: float) -> float:
    """A caller-supplied per-dispatch timeout, validated then clipped.

    Only a positive, finite number is accepted, and never more than the operation
    has left, so this seam can shorten a dispatch but can never lengthen it.
    """
    if value is None:
        return max(0.0, remaining)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComputeUnavailable("capacity_unavailable", "Invalid dispatch timeout")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ComputeUnavailable("capacity_unavailable", "Invalid dispatch timeout")
    return min(seconds, max(0.0, remaining))


# Stable, user-safe messages for account ceilings. The raw entitlement errors
# carry internal ledger numbers and must never reach a client.
_LIMIT_MESSAGES: dict[str, str] = {
    "budget_exceeded": "Compute budget for this period is used up",
    "extended_budget_exceeded": "Extended agent budget for this period is used up",
    "extended_agents_exceeded": "Extended agent runs for this period are used up",
    "trial_exhausted": "Trial allowance is used up",
    "trial_extended_agents_exhausted": "Trial extended agent allowance is used up",
    "rate_limited": "Too many requests; try again shortly",
    "concurrency_exceeded": "Another request is still running; try again when it finishes",
}


#: Capacity refusals that clear on their own; a client may retry them.
RETRYABLE_COMPUTE_CODES: frozenset[str] = frozenset(
    {"rate_limited", "concurrency_exceeded", "capacity_unavailable"}
)


def compute_error(exc: BaseException) -> ComputeUnavailable | None:
    """Sanitized client-facing capacity error for ``exc``, or None if it is not one."""
    if isinstance(exc, ComputeUnavailable):
        return exc
    if isinstance(exc, AccountSuspended):
        return ComputeUnavailable("account_suspended", "Account compute is suspended")
    if isinstance(exc, AccountError):
        return ComputeUnavailable("account_unavailable", "Account compute unavailable")
    if isinstance(exc, LimitExceeded):
        return ComputeUnavailable(
            exc.code, _LIMIT_MESSAGES.get(exc.code, "Compute capacity unavailable")
        )
    if isinstance(exc, AccessDenied):
        return ComputeUnavailable("capability_unavailable", "Capability unavailable")
    return None


@dataclass
class ComputeScope:
    user_id: uuid.UUID
    service: EntitlementService
    operation: str = "chat"
    auto_route: bool = False
    extended: bool = False
    background: bool = False
    #: Groups this scope's reservations into one concurrency slot.
    scope_id: uuid.UUID = field(default_factory=uuid.uuid4)
    extended_started: bool = False
    extended_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    selected_model: str | None = None
    outstanding: dict[Any, ReservationHold] = field(default_factory=dict)
    settled: dict[Any, int] = field(default_factory=dict)
    expected_period: str | None = None

    async def close_stream(self, hold: ReservationHold) -> None:
        """Close acquired transport even when its iterator was never advanced."""
        if hold.response is None:
            return
        if hold.closing is None:

            async def close() -> None:
                closer = getattr(hold.response, "aclose", None)
                if not callable(closer):
                    closer = getattr(hold.response, "close", None)
                if callable(closer):
                    try:
                        result = closer()
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        logger.warning("Could not close an upstream inference stream")

            async def bounded_close() -> None:
                task = asyncio.create_task(close())
                try:
                    done, _ = await asyncio.wait({task}, timeout=STREAM_CLOSE_TIMEOUT_S)
                    if done:
                        await task
                    else:
                        logger.warning("Upstream inference stream cleanup timed out")
                finally:
                    if not task.done():
                        task.cancel()
                    task.add_done_callback(_consume_close_result)

            hold.closing = asyncio.create_task(bounded_close())
        await asyncio.shield(hold.closing)

    async def settle(
        self, reservation: Any, amount: int, *, usage: dict[str, int] | None = None
    ) -> None:
        key = _hold_key(reservation)
        hold = self.outstanding.get(key)
        if hold is None:
            if self.settled.get(key) == amount:
                return
            raise ComputeUnavailable(
                "settlement_conflict",
                "Reservation already settled differently",
                category=FAILURE_SETTLEMENT_FAILED,
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
                "settlement_conflict",
                "Reservation already settling differently",
                category=FAILURE_SETTLEMENT_FAILED,
            )
        try:
            await asyncio.shield(hold.settlement)
        except Exception:
            raise ComputeUnavailable(
                "settlement_failed",
                "Reservation settlement failed",
                category=FAILURE_SETTLEMENT_FAILED,
            ) from None
        self.settled[key] = amount
        self.outstanding.pop(key, None)


@dataclass
class ReservationHold:
    reservation: Any
    bound: int
    settlement: asyncio.Task[Any] | None = None
    actual: int | None = None
    response: Any = None
    closing: asyncio.Task[None] | None = None


def _hold_key(reservation: Any) -> Any:
    """Stable identity of a reservation (its id; the value itself for bare ids)."""
    return getattr(reservation, "id", reservation)


_scope: ContextVar[ComputeScope | None] = ContextVar("compute_scope", default=None)


@asynccontextmanager
async def account_compute(
    pool: Any,
    user_id: uuid.UUID,
    *,
    operation: str = "chat",
    auto_route: bool = False,
    extended: bool = False,
    background: bool = False,
    profile: str = "routine",
    expected_period: str | None = None,
) -> AsyncIterator[ComputeScope]:
    """Account scope for one operation. ``background`` marks worker jobs: charged
    to the budget, but never counted against rate or concurrency ceilings."""
    if pool is None or not isinstance(user_id, uuid.UUID):
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable")
    try:
        model_routing.load_model_routing().profile(profile)
    except (PolicyError, model_routing.RoutingError) as exc:
        raise ComputeUnavailable("profile_unavailable", "Workload profile unavailable") from exc
    scope = ComputeScope(
        user_id,
        EntitlementService(pool),
        operation,
        auto_route,
        extended,
        background,
    )
    scope.expected_period = expected_period
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
        with model_routing.routing_context(profile):
            yield scope
    finally:
        try:

            async def cleanup(hold: ReservationHold) -> None:
                try:
                    await scope.close_stream(hold)
                finally:
                    await scope.settle(
                        hold.reservation, hold.actual if hold.actual is not None else hold.bound
                    )

            settlements = [
                asyncio.create_task(cleanup(hold)) for hold in tuple(scope.outstanding.values())
            ]
            if settlements:
                results = await asyncio.gather(*settlements, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
        finally:
            _scope.reset(token)


def current_scope() -> ComputeScope:
    scope = _scope.get()
    if scope is None:
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable")
    return scope


def selected_model() -> str | None:
    """The actual approved model most recently sent in this account scope."""
    routed = model_routing.active_routing()
    if routed is not None:
        return routed.selected_model
    scope = _scope.get()
    return scope.selected_model if scope else None


def _profile_shortlist(profile: str) -> dict[str, tuple[int, int]]:
    """``model -> (group index, position)`` for one profile's acceptable models."""
    return {
        candidate.model: (candidate.group_index, candidate.position)
        for candidate in model_routing.profile_candidates(profile) or ()
    }


def choose_route(model: str | None = None, *, profile: str = "routine") -> RoutePolicy:
    """The route this deployment would dispatch for ``profile``.

    With no ``model`` this is the profile's cheapest acceptable route, honouring the
    profile's ordered groups and whether the profile may auto-consider premium
    routes. An explicit ``model`` bypasses the profile shortlist only; it still has to
    be an approved, priced, openrouter route.
    """
    try:
        policy = load_inference_policy()
        shortlist = _profile_shortlist(profile)
    except (PolicyError, model_routing.RoutingError) as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc
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
        routes = [route for route in routes if route.model in shortlist]
        if not model_routing.profile(profile).allow_premium:
            routes = [route for route in routes if getattr(route, "route_class", None) == "routine"]
    if routes:
        return min(
            routes,
            key=lambda route: (
                shortlist.get(route.model, (0, 0))[0] if not model else 0,
                route.price_ceiling.microusd_per_1m_prompt
                + route.price_ceiling.microusd_per_1m_completion
                if route.price_ceiling
                else float("inf"),
                route.route_id,
            ),
        )
    raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")


def _pinned_route(route_id: str, model: str) -> RoutePolicy:
    """The approved route ``route_id``, when it serves exactly ``model``.

    An exact pin is resolved against the policy itself rather than against a
    ranked candidate list, so an unknown, unapproved, expired or cross-model route
    is refused before any budget is reserved and can never become a silent
    substitution. This is not by itself a dispatch permission: the per-request
    capability, token and budget checks still run in :func:`_priced_candidates`.
    """
    try:
        inference = load_inference_policy()
    except PolicyError as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc
    route = inference.routes.get(route_id)
    if route is None or route.model != model or not route.is_approved(inference.requirements):
        raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
    return route


def _requested_output(params: dict[str, Any]) -> int | None:
    """The caller's own output ask, validated as a positive integer or ``None``."""
    configured_max = params.get("max_tokens")
    if configured_max is None:
        return None
    if isinstance(configured_max, bool) or not isinstance(configured_max, int):
        raise ComputeUnavailable("capacity_unavailable", "Invalid output token limit")
    if configured_max <= 0:
        raise ComputeUnavailable("capacity_unavailable", "Invalid output token limit")
    return configured_max


def _priced_candidates(
    policy: Any,
    input_size: InputSize,
    params: dict[str, Any],
    requested_model: str | None,
    extended: bool = False,
    *,
    check_budget: bool = True,
) -> list[tuple[int, int, RoutePolicy, bool, model_routing.RoutedModel | None]]:
    """Qualified routes for this request, in the order they should be attempted.

    Order is group-major then cheapest-within-group, so the first entry is the
    cheapest acceptable candidate for the workload and a provider failure walks
    outward through the profile's remaining groups. Every entry still satisfies the
    active profile, the request's required capabilities, the account's limits and the
    request's budget, so a fallback is never a silent downgrade.
    """
    input_tokens = input_size.estimate
    try:
        inference = load_inference_policy()
    except PolicyError as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc
    state = model_routing.current_routing()
    try:
        workload = model_routing.load_model_routing().profile(state.profile)
        shortlist: dict[str, model_routing.RoutedModel] = {
            candidate.model: candidate
            for candidate in model_routing.profile_candidates(
                state.profile,
                excluded_models=state.excluded_models,
                excluded_developers=state.excluded_developers,
            )
            or ()
        }
    except (PolicyError, model_routing.RoutingError) as exc:
        raise ComputeUnavailable(
            "route_unavailable", "Approved inference route unavailable"
        ) from exc

    explicit = bool(requested_model)
    if explicit:
        # An explicit selection bypasses the shortlist, never the requirements.
        wanted = str(requested_model)
        placement = shortlist.get(wanted)
        if placement is not None:
            shortlist = {wanted: placement}
        else:
            shortlist = {}
    requested_output = _requested_output(params)
    requested_effort = params.get("reasoning_effort")
    required = {"text"}
    if params.get("tools"):
        required.add("tools")
    if (
        isinstance(params.get("response_format"), dict)
        and params["response_format"].get("type") == "json_schema"
    ):
        required.add("json_schema")

    candidates: list[
        tuple[int, int, int, str, int, RoutePolicy, bool, model_routing.RoutedModel | None]
    ] = []
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
        if explicit and route.model != requested_model:
            continue
        placement = shortlist.get(route.model)
        if placement is None and not explicit:
            continue
        premium_route = route_class == "premium"
        premium = premium_route or extended
        if premium_route and "premium_routing" not in policy.capabilities:
            continue
        if premium_route and not explicit:
            # Bounded automatic escalation: a profile only reaches a premium route
            # when it says it may, and only inside its own ordered groups.
            if not workload.allow_premium:
                continue
        limits = policy.limits_for(premium)
        if input_tokens > limits.max_context_tokens:
            continue
        # A caller ask is exact. Automatic selection uses the profile's comparable
        # output floor for every candidate; a manual model with no explicit ask
        # retains the qualified route/account's full available output ceiling.
        available_output = min(
            limits.max_output_tokens,
            route.max_output_tokens,
            route.max_context_tokens - input_tokens,
        )
        budget = (
            requested_output
            if requested_output is not None
            else available_output
            if explicit
            else workload.min_output_tokens
        )
        if budget <= 0 or budget > available_output:
            continue
        output_tokens = budget
        if requested_effort is not None and not model_routing.supports_reasoning_effort(
            route.model, str(requested_effort)
        ):
            continue
        if not model_routing.supports_sampling_parameters(
            route.model, params, allow_unknown=explicit
        ):
            continue
        if not route.supports(
            required_capabilities=frozenset(required),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ):
            continue
        bound = route.estimate_microusd(input_size.bound, output_tokens)
        if check_budget and bound > policy.remaining_for(premium):
            continue
        group_index = placement.group_index if placement is not None else 0
        preferred = 0 if route.model == state.preferred_model else 1
        candidates.append(
            (
                group_index,
                preferred,
                bound,
                route.route_id,
                output_tokens,
                route,
                premium,
                placement,
            )
        )
    # Group order first, then a soft preference inside the group, then the cheapest
    # route for *this* request, then a stable tie-break.
    candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2], entry[3]))
    return [
        (bound, output_tokens, route, premium, placement)
        for _group, _preferred, bound, _route_id, output_tokens, route, premium, placement in candidates
    ]


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
    return len(encoded)


@dataclass(frozen=True)
class InputSize:
    """Two views of a request's prompt size, in tokens.

    ``bound`` prices the hold. Byte-level tokenizers emit at most one token per
    UTF-8 byte, so bytes plus generous per-message framing cannot be exceeded:
    a settlement above the hold suspends the account, so the quote must hold.

    ``estimate`` sizes the context window and output budget. It is realistic
    (~3 bytes per token, below the ~4 typical of English BPE) so ordinary
    conversations are not refused at a fraction of the model's context.
    """

    bound: int
    estimate: int


_BOUND_TOKENS_PER_MESSAGE = 512
_ESTIMATE_BYTES_PER_TOKEN = 3
_ESTIMATE_TOKENS_PER_MESSAGE = 8


def _input_size(size: int, messages: int) -> InputSize:
    return InputSize(
        bound=max(1, size + _BOUND_TOKENS_PER_MESSAGE * messages),
        estimate=max(
            1, -(-size // _ESTIMATE_BYTES_PER_TOKEN) + _ESTIMATE_TOKENS_PER_MESSAGE * messages
        ),
    )


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

_REASONING_EFFORTS = model_routing.KNOWN_REASONING_EFFORTS


def _request_bound(params: dict[str, Any]) -> InputSize:
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
    messages = params.get("messages")
    return _input_size(
        text_bytes + len(serialized), len(messages) if isinstance(messages, list) else 0
    )


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


def _usage_settlement(
    usage: Any, route: RoutePolicy, bound: int
) -> tuple[int, dict[str, int]] | None:
    """Ceiling-price charge from validated provider usage; None if untrusted.

    Deliberately not capped at the hold: a charge above the quote settles
    truthfully and the ledger suspends the account for operator reconciliation.
    """
    counts = _usage_counts(usage)
    if counts is None or route.price_ceiling is None:
        return None
    charge = route.estimate_microusd(counts["input_tokens"], counts["output_tokens"])
    if charge > bound:
        logger.warning(
            "Provider usage exceeded reserved quote (route=%s charge=%s bound=%s)",
            route.route_id,
            charge,
            bound,
        )
    return charge, counts


def _chunk_usage(chunk: Any) -> Any:
    return chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)


_ESTIMATED: dict[str, int] = {"estimated_cost": True}


async def guarded_completion(
    *,
    _route_id: str | None = None,
    _dispatch_timeout_s: float | None = None,
    **params: Any,
) -> Any:
    """Dispatch one qualified completion inside the caller's account scope.

    ``_route_id`` and ``_dispatch_timeout_s`` are an internal, keyword-only
    reliability seam, named as parameters so they can never reach the transport as
    request parameters: ``params`` is what is validated, bounded and sent. With
    ``_route_id`` the call is pinned to exactly that approved route for exactly
    the caller's own model — never another route, never another model — and any
    provider failure is raised rather than walked outward. With
    ``_dispatch_timeout_s`` this dispatch is additionally bounded, clipped to what
    remains of the operation deadline so a shorter bound can never lengthen it.

    Both default to ``None``, which is the existing behaviour for every caller.
    Failover itself is not implemented here: a caller that owns two dispatches,
    their order and their overall budget decides between two exact calls, using the
    ``retryable`` classification a failed call raises.
    """
    scope = current_scope()
    started = asyncio.get_running_loop().time()
    deadline = started + get_settings().request_timeout_s
    dispatch_deadline = started + _dispatch_budget_s(_dispatch_timeout_s, deadline - started)
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
    stream = bool(params.get("stream"))
    stream_options = params.get("stream_options")
    if stream_options is not None and not isinstance(stream_options, dict):
        raise ComputeUnavailable("capacity_unavailable", "Unsupported completion parameters")

    input_size = _request_bound(params)
    requested = params.get("model")
    model = (
        requested
        if isinstance(requested, str) and requested not in {"", "auto"} and not scope.auto_route
        else None
    )
    automatic = model is None
    pinned = None
    if _route_id is not None:
        # An exact route still needs an exact model: without one the scope would
        # choose a model the pinned route may not serve, which is a substitution.
        if not isinstance(_route_id, str) or not _route_id.strip() or model is None:
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        pinned = _pinned_route(_route_id, model)
    routing = model_routing.current_routing()
    candidates = _priced_candidates(policy, input_size, params, model, scope.extended)
    if pinned is not None:
        selected = next(
            (entry for entry in candidates if entry[2].route_id == pinned.route_id), None
        )
        if selected is None:
            # Approved, but unable to serve *this* request's capabilities, token
            # limits or budget. The model-level diagnostics below would describe
            # other routes, so the pin is refused on its own terms.
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        # Exactly one dispatch: an exact pin never falls back to another route or
        # another model, so a failure is raised rather than walked outward.
        candidates = [selected]
        automatic = False
    if not candidates:
        if model:
            # Explicit choices are never silently downgraded.
            route = choose_route(model, profile=routing.profile)
            if route.route_class == "premium" and "premium_routing" not in policy.capabilities:
                raise ComputeUnavailable("capability_unavailable", "Premium routing unavailable")
            effort = params.get("reasoning_effort")
            if effort is not None and not model_routing.supports_reasoning_effort(model, effort):
                raise ComputeUnavailable(
                    "capacity_unavailable",
                    "Selected model does not support the requested reasoning effort",
                )
            if not model_routing.supports_sampling_parameters(model, params, allow_unknown=True):
                raise ComputeUnavailable(
                    "capacity_unavailable",
                    "Selected model does not support the requested sampling parameters",
                )
        else:
            choose_route(profile=routing.profile)
        if _priced_candidates(
            policy, input_size, params, model, scope.extended, check_budget=False
        ):
            raise ComputeUnavailable(
                "budget_exceeded", "No qualified route fits the account budget"
            )
        context_limit = policy.limits.max_context_tokens
        if "premium_routing" in policy.capabilities:
            context_limit = max(context_limit, policy.limits_for(True).max_context_tokens)
        if input_size.estimate > context_limit:
            raise ComputeUnavailable("context_limit", "Context too large")
        raise ComputeUnavailable(
            "capacity_unavailable",
            "No qualified route supports the workload and requested token limits",
        )

    async def dispatch(
        candidate: tuple[int, int, RoutePolicy, bool, model_routing.RoutedModel | None],
    ) -> tuple[Any, Any]:
        bound, output_tokens, route, premium, placement = candidate
        inference = load_inference_policy()
        current_route = inference.routes.get(route.route_id)
        if current_route != route or not route.is_approved(inference.requirements):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        if asyncio.get_running_loop().time() >= dispatch_deadline:
            raise ComputeUnavailable(
                "capacity_unavailable",
                "Dispatch deadline exceeded",
                category=FAILURE_DEADLINE_EXCEEDED,
            )
        # Build the central reviewed transport block BEFORE holding budget so
        # an expired review never reserves capacity for a call we cannot send.
        transport = route.transport_payload(inference.requirements)
        if params.get("api_base") not in (None, route.endpoint):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        try:
            # Model-specific reasoning controls are applied AFTER selection, so the
            # control matches the model that actually answered. A value the caller
            # supplied is preserved and only validated, never rewritten.
            resolved = model_routing.apply_model_parameter_presets(
                params, route.model, routing.profile
            )
        except model_routing.RoutingError as exc:
            raise ComputeUnavailable(
                "capacity_unavailable", "Unsupported reasoning effort"
            ) from exc
        call = {
            **resolved,
            "model": route.model,
            "api_base": route.endpoint,
            "api_key": get_settings().openrouter_api_key,
            "max_tokens": output_tokens,
            # The transport never retries on its own: a hidden SDK retry would
            # spend budget this layer did not reserve and re-send a pinned route.
            "num_retries": 0,
            "extra_headers": get_settings().get_provider_config("openrouter").extra_headers,
            **transport,
        }
        if stream:
            call["stream_options"] = {**(stream_options or {}), "include_usage": True}
        try:
            async with scope.extended_lock:
                first_extended = scope.extended and not scope.extended_started
                reserve_options = (
                    {"expected_period": scope.expected_period}
                    if scope.expected_period is not None
                    else {}
                )
                reservation = await scope.service.reserve(
                    scope.user_id,
                    bound,
                    operation=scope.operation,
                    provider=route.provider,
                    model=route.model,
                    route_id=route.route_id,
                    premium=premium,
                    extended=scope.extended,
                    extended_run=first_extended,
                    background=scope.background,
                    scope_id=scope.scope_id,
                    **reserve_options,
                )
                if first_extended:
                    scope.extended_started = True
        except EntitlementsError:
            raise
        except Exception:
            raise ComputeUnavailable("account_unavailable", "Account compute unavailable") from None
        scope.outstanding[_hold_key(reservation)] = ReservationHold(reservation, bound)
        try:
            # Reservation can consume time: the SDK and local wait share the
            # remainder rather than using an earlier, longer SDK allowance.
            remaining = dispatch_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ComputeUnavailable(
                    "capacity_unavailable",
                    "Dispatch deadline exceeded",
                    category=FAILURE_DEADLINE_EXCEEDED,
                )
            call["timeout"] = remaining
            response = await asyncio.wait_for(
                litellm.acompletion(**call),
                timeout=max(0, dispatch_deadline - asyncio.get_running_loop().time()),
            )
        except BaseException:
            # An unknown or cancelled outcome still cost what was reserved, and a
            # failed settlement replaces this exception rather than hiding behind
            # a retryable one, so incomplete accounting stays visible.
            await scope.settle(reservation, bound)
            raise
        if params.get("stream"):
            scope.outstanding[_hold_key(reservation)].response = response
        # Attribution records what actually went out, on both the precise routing
        # state and the coarser account scope, after any fallback has taken effect.
        scope.selected_model = route.model
        routing.record_selection(
            model=route.model,
            route_id=route.route_id,
            group=placement.group if placement is not None else None,
            explicit=model is not None,
        )
        return response, reservation

    async def first_response(start: int) -> tuple[int, Any, Any]:
        denial: ComputeUnavailable | None = None
        # The candidate walk itself is unchanged: a failed candidate hands the
        # request to the next approved candidate. Only the terminal raise is
        # refined: when the list is exhausted by provider failures, the last
        # one is classified and raised, so the surfaced error carries the same
        # typed verdict as an exact (non-automatic) dispatch.
        last_failure: DispatchFailure | None = None
        for index in range(start, len(candidates)):
            try:
                response, reservation = await dispatch(candidates[index])
                return index, response, reservation
            except BudgetExceeded as exc:
                denial = compute_error(exc)
                if not automatic:
                    raise denial or ComputeUnavailable(
                        "budget_exceeded", _LIMIT_MESSAGES["budget_exceeded"]
                    ) from None
            except EntitlementsError as exc:
                raise compute_error(exc) or ComputeUnavailable(
                    "capacity_unavailable", "Compute capacity unavailable"
                ) from None
            except ComputeUnavailable:
                # Route qualification and settlement are not provider failures.
                raise
            except Exception as failure:
                last_failure = _classify_dispatch_failure(
                    failure,
                    deadline_bound=asyncio.get_running_loop().time() >= deadline,
                    output_released=False,
                )
                if not automatic:
                    raise last_failure.as_unavailable(
                        "capacity_unavailable", "Qualified provider unavailable"
                    ) from None
        if denial is not None:
            # A budget denial keeps its precedence over a later provider failure.
            raise denial
        if last_failure is not None:
            raise last_failure.as_unavailable(
                "capacity_unavailable", "Qualified provider unavailable"
            ) from None
        raise ComputeUnavailable("capacity_unavailable", "No qualified route fits capacity")

    index, response, reservation = await first_response(0)
    if not stream:
        bound, _, route, _, _ = candidates[index]
        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        settlement = _usage_settlement(usage, route, bound)
        await scope.settle(
            reservation,
            settlement[0] if settlement is not None else bound,
            usage=settlement[1] if settlement is not None else _ESTIMATED,
        )
        return response

    async def metered_stream() -> AsyncIterator[Any]:
        nonlocal index, response, reservation
        while True:
            bound, _, route, _, _ = candidates[index]
            settlement: tuple[int, dict[str, int]] | None = None
            completed = False
            emitted = False
            failed = False
            try:
                chunks = aiter(cast(AsyncIterator[Any], response))
                while True:
                    # Bound upstream reads, not consumer time suspended at yield.
                    if asyncio.get_running_loop().time() >= dispatch_deadline:
                        raise TimeoutError
                    try:
                        async with asyncio.timeout_at(dispatch_deadline):
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        break
                    parsed = _usage_settlement(_chunk_usage(chunk), route, bound)
                    if parsed is not None:
                        settlement = parsed
                    emitted = True
                    yield chunk
                completed = True
            except Exception as failure:
                failed = True
                if emitted or not automatic or index + 1 >= len(candidates):
                    raise _classify_dispatch_failure(
                        failure,
                        deadline_bound=asyncio.get_running_loop().time() >= deadline,
                        output_released=emitted,
                    ).as_unavailable(
                        "capacity_unavailable", "Qualified provider unavailable"
                    ) from None
            finally:
                hold = scope.outstanding.get(_hold_key(reservation))
                if hold is not None:
                    await scope.close_stream(hold)
                await scope.settle(
                    reservation,
                    settlement[0] if completed and settlement is not None else bound,
                    usage=settlement[1] if completed and settlement is not None else _ESTIMATED,
                )
            if not failed:
                return
            index, response, reservation = await first_response(index + 1)

    return metered_stream()
