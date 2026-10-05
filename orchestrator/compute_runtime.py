"""Fail-closed account scope, approved inference routes and atomic spend reservations.

Every LiteLLM call must pass through ``guarded_completion``. A stream retains its
reservation until its iterator is closed, including cancellation and exceptions.
Unpriced modalities cannot be estimated by this text-token ledger and are denied.

Selection is capability-first and cheapest-acceptable, not cheapest: the account
scope binds a workload profile from :mod:`orchestrator.model_routing`, that profile
supplies the ordered groups of acceptable models and an output suitability floor
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
import re
import uuid
import asyncio
import time
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

from orchestrator import model_routing, routing_log
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.plans import TOOL_ROUND_SAFETY_CEILING
from orchestrator.entitlements.policy import (
    EmbeddingRoutePolicy,
    RoutePolicy,
    load_inference_policy,
)
from orchestrator.entitlements.errors import (
    AccessDenied,
    AccountError,
    AccountSuspended,
    BudgetExceeded,
    EntitlementsError,
    LimitExceeded,
    PolicyError,
    ReservationCommitUncertain,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)
from orchestrator.config import get_settings

logger = logging.getLogger(__name__)
STREAM_CLOSE_TIMEOUT_S = 2.0


def _label(value: Any) -> str | None:
    """A loggable identifier or enumeration value, or ``None`` for anything else."""
    value = getattr(value, "value", value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value if isinstance(value, str) else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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


class _CandidateRevoked(ComputeUnavailable):
    """A selected route lost approval before reservation (candidate-specific)."""


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
    #: Server-owned operation identity: one rate admission across all internal
    #: calls, plus one concurrency slot while any reservation remains open.
    scope_id: uuid.UUID = field(default_factory=uuid.uuid4)
    extended_started: bool = False
    extended_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    selected_model: str | None = None
    selected_effort: str | None = None
    selected_budget_fitted: bool = False
    outstanding: dict[Any, ReservationHold] = field(default_factory=dict)
    settled: dict[Any, int] = field(default_factory=dict)
    expected_period: str | None = None
    #: Outer account profile bounds automatic premium eligibility in nested contexts.
    account_allow_premium: bool = True
    #: Routing telemetry only (see :mod:`orchestrator.routing_log`); never read by policy.
    request_id: str | None = None
    profile: str | None = None
    completion_seq: int = 0
    attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    first_output_at: float | None = None

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
        self,
        reservation: Any,
        amount: int,
        *,
        usage: dict[str, Any] | None = None,
        path: str = "unspecified",
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
        result = hold.settlement.result() if hold.settlement.done() else None
        routing_log.emit(
            "settlement",
            scope_id=str(self.scope_id),
            reservation_id=_label(key),
            path=path,
            status=_label(getattr(result, "status", None)),
            actual=amount,
            hold_bound=hold.bound,
            estimated=not usage or bool(usage.get("estimated_cost")),
            input_tokens=_count((usage or {}).get("input_tokens")),
            output_tokens=_count((usage or {}).get("output_tokens")),
            overage=_count(getattr(result, "overage_microusd", None)),
        )


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
    request_id: str | None = None,
) -> AsyncIterator[ComputeScope]:
    """Account scope for one operation. ``background`` marks worker jobs: charged
    to the budget, but never counted against rate or concurrency ceilings."""
    if pool is None or not isinstance(user_id, uuid.UUID):
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable")
    try:
        account_profile = model_routing.load_model_routing().profile(profile)
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
    scope.account_allow_premium = account_profile.allow_premium
    scope.request_id = request_id
    scope.profile = profile
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
    routing_log.emit(
        "scope_open",
        request_id=request_id,
        scope_id=str(scope.scope_id),
        operation=operation,
        profile=profile,
        auto_route=auto_route,
        account_allow_premium=scope.account_allow_premium,
        background=background,
        extended=extended,
    )
    exit_status = "normal"
    try:
        with model_routing.routing_context(profile):
            yield scope
    except BaseException as exc:
        exit_status = _exit_label(exc)
        raise
    finally:
        try:

            async def cleanup(hold: ReservationHold) -> None:
                try:
                    await scope.close_stream(hold)
                finally:
                    await scope.settle(
                        hold.reservation,
                        hold.actual if hold.actual is not None else hold.bound,
                        path="scope_cleanup",
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
            _emit_scope_close(scope, exit_status)


def _exit_label(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, GeneratorExit):
        return "closed"
    if isinstance(exc, ComputeUnavailable):
        return f"error:{exc.code}"
    if isinstance(exc, EntitlementsError):
        return "error:entitlements"
    return "error"


def _emit_scope_close(scope: ComputeScope, exit_status: str) -> None:
    now = time.monotonic()
    routing_log.emit(
        "scope_close",
        request_id=scope.request_id,
        scope_id=str(scope.scope_id),
        operation=scope.operation,
        exit=exit_status,
        completions=scope.completion_seq,
        attempts=scope.attempts,
        settled_total=sum(scope.settled.values()),
        first_output_ms=(
            round((scope.first_output_at - scope.started_at) * 1000)
            if scope.first_output_at is not None
            else None
        ),
        duration_ms=round((now - scope.started_at) * 1000),
    )


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


def selected_budget_fitted() -> bool:
    """Whether :func:`selected_model`'s output was fitted to the remaining budget."""
    routed = model_routing.active_routing()
    if routed is not None:
        return routed.selected_budget_fitted
    scope = _scope.get()
    return scope.selected_budget_fitted if scope else False


def selected_effort() -> str | None:
    """The reasoning effort sent with :func:`selected_model`, if any."""
    routed = model_routing.active_routing()
    if routed is not None:
        return routed.selected_effort
    scope = _scope.get()
    return scope.selected_effort if scope else None


#: Tool rounds for a turn outside any account scope. Such a turn cannot
#: dispatch (``guarded_completion`` refuses it), so this only preserves the
#: historic fixed limit for callers that stub the completion.
UNSCOPED_TOOL_ROUNDS = 4


async def tool_round_limit() -> int:
    """Tool rounds one chat turn may run under the current account's plan.

    A plan that caps tool rounds per turn gets exactly that cap. A plan that
    leaves it uncapped (``None``) gets the global runaway-loop safety ceiling,
    which is not a commercial limit and is the same for every uncapped plan.
    The first round is the turn's own model call, so the limit is never below
    one: a suspended account (zero limits) must still reach
    ``guarded_completion`` and get its typed denial, not an empty reply.
    """
    scope = _scope.get()
    if scope is None:
        return UNSCOPED_TOOL_ROUNDS
    policy = await scope.service.resolve(scope.user_id)
    cap = policy.limits.max_tool_loop_iterations
    return TOOL_ROUND_SAFETY_CEILING if cap is None else max(1, cap)


@dataclass(frozen=True, slots=True)
class ToolServiceApproval:
    """One operator-approved, fixed-price tool service.

    ``ceiling_microusd`` is the policy's approved maximum per unit and sizes the
    hold. ``fixed_microusd`` is the pinned price of the single unit a caller
    dispatches, and is what a confirmed call settles. The ceiling is always at
    least the fixed price. Unexpected provider-reported extra units must still
    settle truthfully, including above the hold under the overage contract.
    """

    service_id: str
    service: str
    provider: str
    unit: str
    ceiling_microusd: int
    fixed_microusd: int


def approved_tool_service(
    *,
    service_id: str,
    service: str,
    provider: str,
    unit: str,
    fixed_microusd: int,
) -> ToolServiceApproval:
    """The approved fixed-price tool service ``service_id``, or a typed refusal.

    Tool services are deny-by-default, so this checks identity as well as
    approval: the policy entry must name exactly the service, provider and unit
    the caller is about to dispatch, so a renamed or repurposed entry cannot
    authorize a different call. The entry's operator ceiling must also cover the
    caller's pinned price, because a hold below its own settlement would break
    the ledger's rule that the quote holds. A refusal is an approval or
    configuration fact, so it reserves nothing, dispatches nothing and charges
    nothing.

    This is not by itself a dispatch permission: budget, rate, concurrency and
    account state are still decided by the reservation in
    :func:`metered_tool_call`.
    """
    if (
        not isinstance(service_id, str)
        or not service_id.strip()
        or isinstance(fixed_microusd, bool)
        or not isinstance(fixed_microusd, int)
        or fixed_microusd <= 0
    ):
        raise ComputeUnavailable("tool_service_unavailable", "Approved tool service unavailable")
    try:
        policy = load_inference_policy()
    except PolicyError as exc:
        raise ComputeUnavailable(
            "tool_service_unavailable", "Approved tool service unavailable"
        ) from exc
    entry = policy.tool_service(service_id)
    if (
        entry is None
        or entry.service != service
        or entry.provider != provider
        or entry.unit != unit
        or entry.ceiling_microusd_per_unit is None
        or entry.ceiling_microusd_per_unit < fixed_microusd
        or not entry.is_approved(policy.requirements)
    ):
        raise ComputeUnavailable("tool_service_unavailable", "Approved tool service unavailable")
    return ToolServiceApproval(
        service_id=entry.service_id,
        service=entry.service,
        provider=entry.provider,
        unit=entry.unit,
        ceiling_microusd=int(entry.ceiling_microusd_per_unit),
        fixed_microusd=fixed_microusd,
    )


class ToolCallCharge:
    """Settlement selector for one metered fixed-price tool call.

    Legacy callers remain conservative unless they explicitly opt into dispatch
    tracking. Opted-in callers must mark immediately before the first send;
    only the initial state establishes a known zero provider outcome.
    """

    __slots__ = ("_dispatch_aware", "_hold", "_state", "_units")

    def __init__(
        self, *, dispatch_aware: bool = False, hold: ReservationHold | None = None
    ) -> None:
        self._dispatch_aware = dispatch_aware
        self._hold = hold
        self._state = "pre_dispatch"
        self._units = 1

    @property
    def confirmed(self) -> bool:
        return self._state == "confirmed"

    @property
    def units(self) -> int:
        return self._units

    @property
    def known_not_dispatched(self) -> bool:
        return self._dispatch_aware and self._state == "pre_dispatch"

    def mark_dispatched(self) -> None:
        """Irreversibly leave known-zero state immediately before network I/O."""
        if self._state != "pre_dispatch":
            raise RuntimeError("Tool dispatch was already marked or confirmed")
        if self._hold is not None:
            if self._hold.settlement is not None:
                raise ComputeUnavailable(
                    "settlement_conflict",
                    "Tool reservation is already settling",
                    category=FAILURE_SETTLEMENT_FAILED,
                )
            # Scope cleanup must not see a stale known-zero outcome after send.
            self._hold.actual = None
        self._state = "dispatched"

    def confirm(self, *, units: int = 1) -> None:
        """Settle at the pinned price: the priced unit was delivered as approved."""
        if type(units) is not int or units < 1:
            raise ValueError("Tool units must be a positive integer")
        if self._dispatch_aware and self._state != "dispatched":
            raise RuntimeError("Tool confirmation requires one marked dispatch")
        self._units = units
        self._state = "confirmed"


@asynccontextmanager
async def metered_tool_call(
    approval: ToolServiceApproval,
    *,
    scope: ComputeScope | None = None,
    required_capability: str | None = None,
    dispatch_aware: bool = False,
    work_deadline: float | None = None,
) -> AsyncIterator[ToolCallCharge]:
    """Reserve, then settle, exactly ONE fixed-price tool unit.

    The hold is taken on the caller's existing account scope — never a nested
    one — with that scope's own user, operation, ``extended``/``background``
    flags, expected period and ``scope_id``, so a metered tool call shares the
    turn's budget and its concurrency slot. The hold is registered on the scope
    before any I/O, which makes a cancellation or a killed process settle
    conservatively instead of orphaning the reservation.

    ``required_capability`` denies before any reservation when the account's
    resolved plan does not grant it.

    Approval is re-checked immediately before the yield, because taking a hold
    can span long enough for an operator review to lapse. Policy is cached;
    file-based revocations require restart (or explicit cache invalidation).
    A failed re-check returns the whole hold at zero cost and refuses the
    call. Inside the block the caller performs its single external dispatch and
    calls :meth:`ToolCallCharge.confirm` only once the provider has delivered
    the priced unit. By default, or after a marked dispatch, anything else —
    timeout, cancellation, transport or HTTP failure, unparseable or
    out-of-contract output — settles the reserved
    ceiling, the same conservative unknown-usage contract inference uses, so it
    can charge a call whose provider-side billing is unknown. Confirmation is a
    statement about the priced unit, so it survives a later local failure in the
    caller; a caller that cannot vouch for the outcome must not confirm.

    A settlement failure is raised as :class:`ComputeUnavailable` and is never
    retried or swallowed here: incomplete accounting must stop the operation.

    ``dispatch_aware`` is an explicit opt-in: before ``mark_dispatched`` a local
    failure/cancellation settles zero; after the mark unknown outcomes remain
    conservative. Legacy callers need no mark and keep their existing contract.
    ``work_deadline`` bounds capability resolution, lock acquisition and the
    acquisition wait. A started reservation is shielded through registration;
    if interrupted, recovery waits for its result and settles an acquired hold
    at zero before propagating the interruption. That accounting cleanup may
    finish outside the work deadline and never dispatches provider work.
    """
    active = scope if scope is not None else current_scope()
    if type(dispatch_aware) is not bool:
        raise ValueError("Tool dispatch tracking must be explicitly boolean")
    if work_deadline is not None and (not dispatch_aware or not math.isfinite(work_deadline)):
        raise ValueError("Tool work deadline requires dispatch-aware tracking")

    async def acquire_and_register(first_extended: bool) -> Any:
        if work_deadline is not None and time.monotonic() >= work_deadline:
            raise TimeoutError
        reserve_options = (
            {"expected_period": active.expected_period}
            if active.expected_period is not None
            else {}
        )
        try:
            reservation = await active.service.reserve(
                active.user_id,
                approval.ceiling_microusd,
                operation=active.operation,
                provider=approval.provider,
                route_id=approval.service_id,
                premium=False,
                extended=active.extended,
                extended_run=first_extended,
                background=active.background,
                scope_id=active.scope_id,
                **reserve_options,
            )
        except ReservationCommitUncertain as exc:
            receipt: ReservationReceipt = exc.receipt
            # Receipt identity survives even when its committed outcome cannot
            # currently be established. Scope cleanup revalidates this receipt
            # through the service instead of blindly settling a candidate ID.
            active.outstanding[receipt.id] = ReservationHold(
                receipt,
                approval.ceiling_microusd,
                actual=0,
            )

            async def recover_commit() -> None:
                if (
                    receipt.user_id != active.user_id
                    or receipt.scope_id != active.scope_id
                    or receipt.reservation.operation != active.operation
                    or receipt.reservation.reserved_microusd != approval.ceiling_microusd
                    or receipt.provider != approval.provider
                    or receipt.model is not None
                    or receipt.route_id != approval.service_id
                    or receipt.reservation.premium
                    or receipt.reservation.extended != active.extended
                    or receipt.extended_run != first_extended
                    or receipt.background != active.background
                ):

                    async def reject_binding() -> None:
                        raise ReservationRecoveryUnresolved(receipt)

                    # A receipt from a different acquisition is never authority
                    # to settle that row. Retain a failed known-zero accounting
                    # task so normal scope cleanup cannot bypass this binding.
                    rejected = asyncio.create_task(reject_binding())
                    active.outstanding[receipt.id].settlement = rejected
                    await asyncio.shield(rejected)
                recovered = await active.service.recover_reservation(receipt)
                if recovered is None:
                    active.outstanding.pop(receipt.id)
                else:
                    active.outstanding[receipt.id].reservation = recovered
                    if first_extended:
                        active.extended_started = True
                    await active.settle(recovered, 0, path="tool_not_dispatched")

            recovery = asyncio.create_task(recover_commit())
            interrupted = exc.interrupted
            try:
                # Legacy acquisition also needs cancellation-safe receipt
                # recovery: a second cancellation must not orphan an outcome
                # after the first cancellation exposed an ambiguous commit.
                while True:
                    try:
                        await asyncio.shield(recovery)
                        break
                    except asyncio.CancelledError:
                        interrupted = True
                        if recovery.done():
                            recovery.result()
                            break
            except ReservationRecoveryUnresolved:
                raise ComputeUnavailable(
                    "reservation_outcome_unresolved",
                    "Account reservation outcome is unresolved",
                    category=FAILURE_SETTLEMENT_FAILED,
                ) from None
            if interrupted:
                raise asyncio.CancelledError
            # Recovery establishes accounting, never permission to replay the
            # reservation or continue to provider dispatch after an ambiguous
            # commit. Both legacy and dispatch-aware metered acquisition stop.
            raise ComputeUnavailable(
                "account_unavailable", "Account reservation could not be confirmed"
            ) from None
        # No await between a returned reservation and its scope registration.
        active.outstanding[_hold_key(reservation)] = ReservationHold(
            reservation,
            approval.ceiling_microusd,
            actual=0 if dispatch_aware else None,
        )
        if first_extended:
            active.extended_started = True
        return reservation

    work_timeout = asyncio.timeout(
        None if work_deadline is None else max(0.0, work_deadline - time.monotonic())
    )
    try:
        async with work_timeout:
            if required_capability is not None:
                resolved = await active.service.resolve(active.user_id)
                if required_capability not in resolved.capabilities:
                    raise ComputeUnavailable("capability_unavailable", "Capability unavailable")
            async with active.extended_lock:
                first_extended = active.extended and not active.extended_started
                if dispatch_aware:
                    acquisition = asyncio.create_task(acquire_and_register(first_extended))
                    try:
                        reservation = await asyncio.shield(acquisition)
                    except BaseException:

                        async def recover_acquisition() -> None:
                            try:
                                acquired = await acquisition
                            except ComputeUnavailable as exc:
                                if exc.category == FAILURE_SETTLEMENT_FAILED:
                                    raise
                                return
                            except Exception:
                                # A failed reservation never began provider I/O.
                                return
                            await active.settle(acquired, 0, path="tool_not_dispatched")

                        cleanup = asyncio.create_task(recover_acquisition())
                        # Retain the lock through recovery, including repeated
                        # cancellation, so extended-run accounting cannot race.
                        while True:
                            try:
                                await asyncio.shield(cleanup)
                                break
                            except asyncio.CancelledError:
                                if cleanup.done():
                                    cleanup.result()
                                    break
                        raise
                else:
                    reservation = await acquire_and_register(first_extended)
    except TimeoutError:
        if work_timeout.expired() or (
            work_deadline is not None and time.monotonic() >= work_deadline
        ):
            raise
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable") from None
    except ComputeUnavailable:
        raise
    except EntitlementsError as exc:
        raise compute_error(exc) or ComputeUnavailable(
            "account_unavailable", "Account compute unavailable"
        ) from None
    except Exception:
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable") from None

    charge = ToolCallCharge(
        dispatch_aware=dispatch_aware,
        hold=active.outstanding[_hold_key(reservation)] if dispatch_aware else None,
    )
    try:
        approved_tool_service(
            service_id=approval.service_id,
            service=approval.service,
            provider=approval.provider,
            unit=approval.unit,
            fixed_microusd=approval.fixed_microusd,
        )
        if work_deadline is not None and time.monotonic() >= work_deadline:
            raise TimeoutError
    except BaseException:
        # Dispatch has not begun. Use the scope's shielded, idempotent settlement
        # rather than a separate release path: even failed/cancelled accounting
        # must retain a known zero outcome, not invent a provider charge.
        await active.settle(reservation, 0, path="tool_not_dispatched")
        raise
    try:
        yield charge
    finally:
        await _settle_tool_hold(active, reservation, approval, charge=charge)


class EmbeddingCallCharge:
    """Dispatch state and a validated input-token receipt for one embedding POST."""

    def __init__(self, hold: ReservationHold, route: EmbeddingRoutePolicy) -> None:
        self._hold = hold
        self._route = route
        self._state = "pre_dispatch"
        self.input_tokens: int | None = None
        self.actual: int | None = None

    def mark_dispatched(self) -> None:
        if self._state != "pre_dispatch" or self._hold.settlement is not None:
            raise ComputeUnavailable(
                "settlement_conflict",
                "Embedding reservation is already settling",
                category=FAILURE_SETTLEMENT_FAILED,
            )
        self._hold.actual = None
        self._state = "dispatched"

    def confirm(self, *, input_tokens: int, actual_microusd: int) -> None:
        if self._state != "dispatched":
            raise RuntimeError("Embedding confirmation requires one marked dispatch")
        if type(input_tokens) is not int or input_tokens <= 0:
            raise ValueError("Embedding input usage must be a positive integer")
        if type(actual_microusd) is not int or actual_microusd < 0:
            raise ValueError("Embedding cost must be a non-negative integer")
        if actual_microusd > self._route.estimate_microusd(input_tokens):
            raise ValueError("Embedding cost exceeds its pinned input rate")
        self.input_tokens = input_tokens
        self.actual = actual_microusd
        # Cleanup racing cancellation must preserve validated actual usage.
        self._hold.actual = actual_microusd
        self._state = "confirmed"


@asynccontextmanager
async def metered_embedding_call(
    route: EmbeddingRoutePolicy, input_token_bound: int, *, scope: ComputeScope | None = None
) -> AsyncIterator[EmbeddingCallCharge]:
    """Reserve input tokens on the EXISTING scope before a single embedding send.

    Acquisition/registration and uncertain-commit recovery mirror dispatch-aware
    metered_tool_call. Known pre-send failure costs zero; unknown post-send usage
    costs the ceiling. No fixed-unit quotient or completion receipt is involved.
    The adapter rechecks its exact route/attestation after acquisition, before send.
    """
    active = scope if scope is not None else current_scope()
    if type(input_token_bound) is not int or not 0 < input_token_bound <= route.max_batch_tokens:
        raise ComputeUnavailable("embedding_unavailable", "Invalid embedding input bound")
    policy = load_inference_policy()
    if policy.embedding_route(route.route_id) != route or not route.is_approved(
        policy.requirements
    ):
        raise ComputeUnavailable("embedding_unavailable", "Approved embedding route unavailable")
    bound = route.estimate_microusd(input_token_bound)

    async def acquire_and_register(first_extended: bool) -> Any:
        options = (
            {"expected_period": active.expected_period}
            if active.expected_period is not None
            else {}
        )
        try:
            reservation = await active.service.reserve(
                active.user_id,
                bound,
                operation=active.operation,
                provider=route.provider,
                model=route.model,
                route_id=route.route_id,
                premium=False,
                extended=active.extended,
                extended_run=first_extended,
                background=active.background,
                scope_id=active.scope_id,
                **options,
            )
        except ReservationCommitUncertain as exc:
            receipt = exc.receipt
            active.outstanding[receipt.id] = ReservationHold(receipt, bound, actual=0)

            async def recover_commit() -> None:
                if (
                    receipt.user_id != active.user_id
                    or receipt.scope_id != active.scope_id
                    or receipt.reservation.operation != active.operation
                    or receipt.reservation.reserved_microusd != bound
                    or receipt.provider != route.provider
                    or receipt.model != route.model
                    or (
                        active.expected_period is not None
                        and receipt.reservation.period_key != active.expected_period
                    )
                    or receipt.route_id != route.route_id
                    or receipt.reservation.premium
                    or receipt.reservation.extended != active.extended
                    or receipt.extended_run != first_extended
                    or receipt.background != active.background
                ):

                    async def reject_binding() -> None:
                        raise ReservationRecoveryUnresolved(receipt)

                    rejected = asyncio.create_task(reject_binding())
                    active.outstanding[receipt.id].settlement = rejected
                    await asyncio.shield(rejected)
                recovered = await active.service.recover_reservation(receipt)
                if recovered is None:
                    active.outstanding.pop(receipt.id)
                else:
                    active.outstanding[receipt.id].reservation = recovered
                    if first_extended:
                        active.extended_started = True
                    await active.settle(recovered, 0, path="embedding_not_dispatched")

            recovery = asyncio.create_task(recover_commit())
            interrupted = exc.interrupted
            try:
                while True:
                    try:
                        await asyncio.shield(recovery)
                        break
                    except asyncio.CancelledError:
                        interrupted = True
                        if recovery.done():
                            recovery.result()
                            break
            except ReservationRecoveryUnresolved:
                raise ComputeUnavailable(
                    "reservation_outcome_unresolved",
                    "Account reservation outcome is unresolved",
                    category=FAILURE_SETTLEMENT_FAILED,
                ) from None
            except Exception:
                raise ComputeUnavailable(
                    "reservation_outcome_unresolved",
                    "Account reservation outcome is unresolved",
                    category=FAILURE_SETTLEMENT_FAILED,
                ) from None
            if interrupted:
                raise asyncio.CancelledError
            raise ComputeUnavailable(
                "account_unavailable", "Account reservation could not be confirmed"
            ) from None
        active.outstanding[_hold_key(reservation)] = ReservationHold(reservation, bound, actual=0)
        if first_extended:
            active.extended_started = True
        return reservation

    try:
        async with active.extended_lock:
            first_extended = active.extended and not active.extended_started
            acquisition = asyncio.create_task(acquire_and_register(first_extended))
            try:
                reservation = await asyncio.shield(acquisition)
            except BaseException:

                async def recover_acquisition() -> None:
                    try:
                        acquired = await acquisition
                    except ComputeUnavailable as exc:
                        if exc.category == FAILURE_SETTLEMENT_FAILED:
                            raise
                        return
                    except Exception:
                        return
                    await active.settle(acquired, 0, path="embedding_not_dispatched")

                cleanup = asyncio.create_task(recover_acquisition())
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        if cleanup.done():
                            cleanup.result()
                            break
                raise
    except ComputeUnavailable:
        raise
    except EntitlementsError as exc:
        raise compute_error(exc) or ComputeUnavailable(
            "account_unavailable", "Account compute unavailable"
        ) from None
    except Exception:
        raise ComputeUnavailable("account_unavailable", "Account compute unavailable") from None

    charge = EmbeddingCallCharge(active.outstanding[_hold_key(reservation)], route)
    try:
        yield charge
    finally:
        if charge._state == "pre_dispatch":
            await active.settle(reservation, 0, path="embedding_not_dispatched")
        elif charge.actual is not None:
            await active.settle(
                reservation,
                charge.actual,
                usage={"input_tokens": charge.input_tokens, "output_tokens": 0},
                path="embedding_call",
            )
            if charge.actual > bound:
                raise ComputeUnavailable(
                    "embedding_price_exceeded",
                    "Embedding charge exceeded its reserved price",
                    category=FAILURE_SETTLEMENT_FAILED,
                )
        else:
            await active.settle(
                reservation, bound, usage={"estimated_cost": True}, path="embedding_call"
            )


async def _settle_tool_hold(
    scope: ComputeScope,
    reservation: Any,
    approval: ToolServiceApproval,
    *,
    charge: ToolCallCharge,
) -> None:
    """Settle one tool unit at its pinned price, or at the hold when uncertain.

    ``estimated_cost`` marks the conservative branch, so an audit can tell a
    priced settlement from one that only knows the call was dispatched. Service
    attribution needs no metadata here: the reservation already carries the
    provider and the service id it was taken for.
    """
    if charge.known_not_dispatched:
        await scope.settle(reservation, 0, path="tool_not_dispatched")
    elif charge.confirmed:
        actual = approval.fixed_microusd * charge.units
        usage = {"tool_calls": 1}
        if charge.units != 1:
            usage["provider_units"] = charge.units
        await scope.settle(reservation, actual, usage=usage, path="tool_call")
        if actual > approval.ceiling_microusd:
            # EntitlementService records the overage and suspends the account.
            raise ComputeUnavailable(
                "tool_price_exceeded", "Tool service charge exceeded its reserved price"
            )
    else:
        await scope.settle(
            reservation,
            approval.ceiling_microusd,
            usage={"estimated_cost": True},
            path="tool_call",
        )


def _profile_shortlist(profile: str) -> dict[str, tuple[int, int]]:
    """``model -> (group index, position)`` for one profile's acceptable models."""
    return {
        candidate.model: (candidate.group_index, candidate.position)
        for candidate in model_routing.profile_candidates(profile) or ()
    }


def _model_excluded(model: str, state: model_routing.RoutingState) -> bool:
    """Apply exclusions without leaking parser errors through admission."""
    try:
        developer = model_routing.developer_for_model(model)
    except model_routing.RoutingError:
        return True
    return model in state.excluded_models or developer in state.excluded_developers


def choose_route(model: str | None = None, *, profile: str = "routine") -> RoutePolicy:
    """The route this deployment would dispatch for ``profile``.

    With no ``model`` this is the profile's cheapest acceptable route, honouring the
    profile's ordered groups and whether the profile may auto-consider premium
    routes. An explicit ``model`` bypasses the profile shortlist only; it still has to
    be an approved, priced, openrouter route.
    """
    state = model_routing.current_routing()
    if model and _model_excluded(model, state):
        raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
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


def _largest_approved_context() -> int:
    """The largest context window among routes ``choose_route`` could select."""
    try:
        policy = load_inference_policy()
    except PolicyError:
        return 0
    return max(
        (
            route.max_context_tokens
            for route in policy.routes.values()
            if route.is_approved(policy.requirements)
            and route.provider == "openrouter"
            and route.model.startswith("openrouter/")
            and route.price_ceiling is not None
            and getattr(route, "route_class", None) in {"routine", "premium"}
        ),
        default=0,
    )


def _budget_fitted_output(
    route: RoutePolicy, input_bound: int, remaining: int, ceiling: int
) -> int:
    """Largest output whose conservative hold fits ``remaining``, capped at ``ceiling``.

    The hold is ``ceil((input * prompt_price + output * completion_price) / 1e6)``, so it
    fits exactly when ``output <= (remaining * 1e6 - input * prompt_price) /
    completion_price``. The request then sends this output as ``max_tokens``, so the
    hold still bounds what the provider can bill and spending stays enforceable.
    """
    price = route.price_ceiling
    if price is None or ceiling <= 0:
        return 0
    spare = remaining * 1_000_000 - input_bound * price.microusd_per_1m_prompt
    if spare < 0:
        return 0
    if price.microusd_per_1m_completion <= 0:
        return ceiling
    return min(ceiling, spare // price.microusd_per_1m_completion)


def _priced_candidates(
    policy: Any,
    input_size: InputSize,
    params: dict[str, Any],
    requested_model: str | None,
    extended: bool = False,
    *,
    check_budget: bool = True,
    account_allow_premium: bool = True,
    assume_premium_capability: bool = False,
    exclusions: dict[str, int] | None = None,
    fit_to_budget: bool = False,
    fitted: set[str] | None = None,
) -> list[tuple[int, int, RoutePolicy, bool, model_routing.RoutedModel | None]]:
    """Qualified routes for this request, in the order they should be attempted.

    Order is group-major then cheapest-within-group, so the first entry is the
    cheapest acceptable candidate for the workload and a provider failure walks
    outward through the profile's remaining groups. Every entry still satisfies the
    active profile, the request's required capabilities, the account's limits and the
    request's budget, so a fallback is never a silent downgrade.

    ``exclusions``, when given, counts why routes the request could have used were
    left out at full size (telemetry only; it never changes the result).

    ``fit_to_budget`` (optional work O2): when no route of a group fits the remaining
    budget at full size and the caller set no output limit, offer the group's routes
    at the largest output the budget covers, never below the profile's output floor.
    ``fitted`` collects the route ids offered that way.
    """

    def excluded(reason: str) -> None:
        if exclusions is not None:
            exclusions[reason] = exclusions.get(reason, 0) + 1

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
    if explicit and _model_excluded(str(requested_model), state):
        return []
    # Resolve one account-scoped target before looking at candidates. Premium
    # trial overlays remain available to extended scopes, while ordinary auto
    # routing cannot enlarge the request just because one route is premium.
    account_limits = policy.limits_for(extended)
    # ``None`` is uncapped (paid plans sell period capacity, not a smaller turn):
    # the automatic target is then whatever each candidate route can return.
    account_output: int | None = account_limits.max_output_tokens
    if account_limits.max_context_tokens is not None:
        context_left = account_limits.max_context_tokens - input_tokens
        account_output = (
            context_left if account_output is None else min(account_output, context_left)
        )
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
    automatic_target = not explicit and requested_output is None
    pending: dict[
        int, list[tuple[RoutePolicy, bool, model_routing.RoutedModel | None, int, int]]
    ] = {}
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
        if (
            premium_route
            and "premium_routing" not in policy.capabilities
            and not assume_premium_capability
        ):
            excluded("capability")
            continue
        if premium_route and not explicit:
            # Bounded automatic escalation: a profile only reaches a premium route
            # when it says it may, and only inside its own ordered groups.
            if not workload.allow_premium or not account_allow_premium:
                excluded("premium_not_allowed")
                continue
        limits = policy.limits_for(premium)
        if limits.max_context_tokens is not None and input_tokens > limits.max_context_tokens:
            excluded("context")
            continue
        available_output = min(
            route.max_output_tokens,
            route.max_context_tokens - input_tokens,
        )
        if limits.max_output_tokens is not None:
            available_output = min(available_output, limits.max_output_tokens)
        if available_output <= 0:
            excluded("context")
            continue
        if requested_effort is not None and not model_routing.supports_reasoning_effort(
            route.model, str(requested_effort)
        ):
            excluded("effort")
            continue
        if not model_routing.supports_sampling_parameters(
            route.model, params, allow_unknown=explicit
        ):
            excluded("sampling")
            continue
        group_index = placement.group_index if placement is not None else 0
        preferred = 0 if route.model == state.preferred_model else 1
        if automatic_target:
            # Capability fit at the route's full capacity; the shared output
            # target for the group is decided once every candidate is known.
            if route.supports(
                required_capabilities=frozenset(required),
                input_tokens=input_tokens,
                output_tokens=available_output,
            ):
                pending.setdefault(group_index, []).append(
                    (route, premium, placement, available_output, preferred)
                )
            else:
                excluded("route_capabilities")
            continue
        # Explicit caller asks stay exact; an explicit model with no ask keeps
        # its route/account's full available output.
        output_tokens = requested_output if requested_output is not None else available_output
        if output_tokens <= 0 or output_tokens > available_output:
            excluded("output")
            continue
        if not route.supports(
            required_capabilities=frozenset(required),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ):
            excluded("route_capabilities")
            continue
        bound = route.estimate_microusd(input_size.bound, output_tokens)
        if check_budget and bound > policy.remaining_for(premium):
            excluded("budget")
            fitted_output = (
                _budget_fitted_output(
                    route, input_size.bound, policy.remaining_for(premium), output_tokens
                )
                if fit_to_budget and requested_output is None
                else 0
            )
            if fitted_output <= 0 or fitted_output < workload.min_output_tokens:
                continue
            output_tokens = fitted_output
            bound = route.estimate_microusd(input_size.bound, output_tokens)
            if fitted is not None:
                fitted.add(route.route_id)
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
    # Automatic candidates in one group compete for one common output target: the
    # account target, capped by the largest eligible route capacity in that group.
    # A smaller-cap route cannot win on price merely by offering a shorter answer,
    # and a group whose routes all fall short of the account target still serves
    # at its best feasible size instead of refusing or escalating to a later group.
    for group_index, entries in pending.items():
        remaining = entries
        group_accepted = False
        while remaining:
            capacity = max(entry[3] for entry in remaining)
            target = capacity if account_output is None else min(account_output, capacity)
            if target <= 0 or target < workload.min_output_tokens:
                for _ in remaining:
                    excluded("output_floor")
                break
            meeting = [entry for entry in remaining if entry[3] >= target]
            accepted = 0
            for route, premium, placement, _available, preferred in meeting:
                bound = route.estimate_microusd(input_size.bound, target)
                if check_budget and bound > policy.remaining_for(premium):
                    excluded("budget")
                    continue
                candidates.append(
                    (
                        group_index,
                        preferred,
                        bound,
                        route.route_id,
                        target,
                        route,
                        premium,
                        placement,
                    )
                )
                accepted += 1
            if accepted:
                group_accepted = True
                break
            # Nothing at this size fits the budget: those routes are not eligible,
            # so the group's feasible target falls to its next-largest capacity.
            remaining = [entry for entry in remaining if entry[3] < target]
        if group_accepted or not (fit_to_budget and check_budget):
            continue
        # No route of this group fits the budget at any capacity size: offer each at
        # the largest output its hold can cover, if that still meets the floor.
        for route, premium, placement, available, preferred in entries:
            ceiling = available if account_output is None else min(account_output, available)
            fit = _budget_fitted_output(
                route, input_size.bound, policy.remaining_for(premium), ceiling
            )
            if fit <= 0 or fit < workload.min_output_tokens:
                continue
            candidates.append(
                (
                    group_index,
                    preferred,
                    route.estimate_microusd(input_size.bound, fit),
                    route.route_id,
                    fit,
                    route,
                    premium,
                    placement,
                )
            )
            if fitted is not None:
                fitted.add(route.route_id)
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

#: Shape of a provider generation id (OpenRouter ``gen-...``), the key of its receipt.
_GENERATION_ID = re.compile(r"gen-[A-Za-z0-9_-]{1,100}")


def _generation_id(chunk: Any) -> str | None:
    value = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
    return value if isinstance(value, str) and _GENERATION_ID.fullmatch(value) else None


def _estimated_usage(generation_id: str | None) -> dict[str, Any]:
    """Usage for a conservative full-hold settlement, keyed for receipt reconciliation."""
    if generation_id is None:
        return dict(_ESTIMATED)
    return {**_ESTIMATED, "generation_id": generation_id}


async def guarded_completion(
    *,
    _route_id: str | None = None,
    _dispatch_timeout_s: float | None = None,
    _exact_model: bool = False,
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

    ``_exact_model`` opts an internal caller with an automatic account scope into
    an exact model selection without altering the account scope or its limits.
    Without it, automatic scopes continue treating the supplied model as a hint.
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
    if type(_exact_model) is not bool or (
        _exact_model and (not isinstance(requested, str) or requested in {"", "auto"})
    ):
        raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
    model = (
        requested
        if isinstance(requested, str)
        and requested not in {"", "auto"}
        and (_exact_model or not scope.auto_route)
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
    exclusions: dict[str, int] = {}
    fitted_routes: set[str] = set()
    candidates = _priced_candidates(
        policy,
        input_size,
        params,
        model,
        scope.extended,
        account_allow_premium=scope.account_allow_premium,
        exclusions=exclusions,
        fit_to_budget=True,
        fitted=fitted_routes,
    )
    scope.completion_seq += 1
    completion_seq = scope.completion_seq
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
    routing_log.emit(
        "candidates",
        scope_id=str(scope.scope_id),
        completion_seq=completion_seq,
        profile=routing.profile,
        plan=_label(getattr(policy, "plan", None)),
        explicit=model is not None,
        pinned=pinned is not None,
        stream=stream,
        route_ids=[entry[2].route_id for entry in candidates],
        candidate_count=len(candidates),
        exclusions=exclusions,
    )
    attempt_reservations: dict[int, str | None] = {}

    def outcome(index: int, result: str, next_action: str, **fields: object) -> None:
        routing_log.emit(
            "attempt_outcome",
            scope_id=str(scope.scope_id),
            completion_seq=completion_seq,
            attempt_index=index,
            reservation_id=attempt_reservations.get(index),
            outcome=result,
            next_action=next_action,
            **fields,
        )

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
            # Diagnosis only, never admission: if the profile's own routes would be
            # eligible with premium routing, the missing capability is what blocks
            # this request. That is durable, so it is refused truthfully (as an
            # explicit premium pick is) rather than as a retryable capacity error,
            # and it takes precedence over budget and context.
            if "premium_routing" not in policy.capabilities and _priced_candidates(
                policy,
                input_size,
                params,
                model,
                scope.extended,
                check_budget=False,
                account_allow_premium=scope.account_allow_premium,
                assume_premium_capability=True,
            ):
                raise ComputeUnavailable("capability_unavailable", "Premium routing unavailable")
        if _priced_candidates(
            policy,
            input_size,
            params,
            model,
            scope.extended,
            check_budget=False,
            account_allow_premium=scope.account_allow_premium,
        ):
            raise ComputeUnavailable(
                "budget_exceeded", "No qualified route fits the account budget"
            )
        ceilings = [policy.limits.max_context_tokens]
        if "premium_routing" in policy.capabilities:
            ceilings.append(policy.limits_for(True).max_context_tokens)
        if None in ceilings:
            # An uncapped plan's context ceiling is the largest approved route
            # window: input no route can hold is a context error, not a budget one.
            if input_size.estimate >= _largest_approved_context():
                raise ComputeUnavailable("context_limit", "Context too large")
        elif input_size.estimate > max(c for c in ceilings if c is not None):
            raise ComputeUnavailable("context_limit", "Context too large")
        raise ComputeUnavailable(
            "capacity_unavailable",
            "No qualified route supports the workload and requested token limits",
        )

    async def dispatch(
        candidate: tuple[int, int, RoutePolicy, bool, model_routing.RoutedModel | None],
        attempt_index: int,
    ) -> tuple[Any, Any]:
        bound, output_tokens, route, premium, placement = candidate
        inference = load_inference_policy()
        current_route = inference.routes.get(route.route_id)
        if current_route != route or not route.is_approved(inference.requirements):
            raise _CandidateRevoked("route_unavailable", "Approved inference route unavailable")
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
                    workload_profile=routing.profile,
                    reasoning_effort=_label(resolved.get("reasoning_effort")),
                    **reserve_options,
                )
                if first_extended:
                    scope.extended_started = True
        except EntitlementsError:
            raise
        except Exception:
            raise ComputeUnavailable("account_unavailable", "Account compute unavailable") from None
        scope.outstanding[_hold_key(reservation)] = ReservationHold(reservation, bound)
        reservation_id = _label(_hold_key(reservation))
        attempt_reservations[attempt_index] = reservation_id
        scope.attempts += 1
        routing_log.emit(
            "attempt",
            scope_id=str(scope.scope_id),
            completion_seq=completion_seq,
            attempt_index=attempt_index,
            route_id=route.route_id,
            model=route.model,
            group=placement.group if placement is not None else None,
            premium=premium,
            explicit=model is not None,
            pinned=pinned is not None,
            requested_effort=_label(params.get("reasoning_effort")),
            preset_effort=_label(
                model_routing.model_parameter_presets(route.model, routing.profile).get(
                    "reasoning_effort"
                )
            ),
            sent_effort=_label(resolved.get("reasoning_effort")),
            include_reasoning=resolved.get("include_reasoning") is True,
            max_tokens=output_tokens,
            hold_bound=bound,
            budget_fitted=route.route_id in fitted_routes,
            reservation_id=reservation_id,
            stream=stream,
        )
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
            await scope.settle(reservation, bound, path="dispatch_failure")
            raise
        if params.get("stream"):
            scope.outstanding[_hold_key(reservation)].response = response
        # Attribution records what actually went out, on both the precise routing
        # state and the coarser account scope, after any fallback has taken effect.
        sent_effort = _label(resolved.get("reasoning_effort"))
        budget_fitted = route.route_id in fitted_routes
        scope.selected_model = route.model
        scope.selected_effort = sent_effort
        scope.selected_budget_fitted = budget_fitted
        routing.record_selection(
            model=route.model,
            route_id=route.route_id,
            group=placement.group if placement is not None else None,
            explicit=model is not None,
            effort=sent_effort,
            budget_fitted=budget_fitted,
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
        revoked: _CandidateRevoked | None = None
        for index in range(start, len(candidates)):
            walk_on = "fallback" if automatic and index + 1 < len(candidates) else "raise"
            try:
                response, reservation = await dispatch(candidates[index], index)
                return index, response, reservation
            except BudgetExceeded as exc:
                denial = compute_error(exc)
                outcome(
                    index,
                    "denied",
                    walk_on if automatic else "raise",
                    failure_category="budget_exceeded",
                )
                if not automatic:
                    raise denial or ComputeUnavailable(
                        "budget_exceeded", _LIMIT_MESSAGES["budget_exceeded"]
                    ) from None
            except EntitlementsError as exc:
                outcome(index, "denied", "raise", failure_category="entitlements")
                raise compute_error(exc) or ComputeUnavailable(
                    "capacity_unavailable", "Compute capacity unavailable"
                ) from None
            except _CandidateRevoked as exc:
                outcome(index, "revoked", walk_on if automatic else "raise")
                if not automatic:
                    raise
                revoked = exc
            except ComputeUnavailable as exc:
                # Route qualification and settlement are not provider failures.
                outcome(index, "refused", "raise", failure_category=exc.category)
                raise
            except Exception as failure:
                last_failure = _classify_dispatch_failure(
                    failure,
                    deadline_bound=asyncio.get_running_loop().time() >= deadline,
                    output_released=False,
                )
                outcome(
                    index,
                    "failed",
                    walk_on if automatic else "raise",
                    failure_category=last_failure.category,
                    status_code=last_failure.status_code,
                    retryable=last_failure.retryable,
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
        if revoked is not None:
            raise revoked
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
        if scope.first_output_at is None:
            scope.first_output_at = time.monotonic()
        outcome(index, "completed", "none", output_released=True)
        await scope.settle(
            reservation,
            settlement[0] if settlement is not None else bound,
            usage=settlement[1] if settlement is not None else _ESTIMATED,
            path="completed",
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
            generation_id: str | None = None
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
                    if generation_id is None:
                        generation_id = _generation_id(chunk)
                    if not emitted and scope.first_output_at is None:
                        scope.first_output_at = time.monotonic()
                    emitted = True
                    yield chunk
                completed = True
                outcome(index, "completed", "none", output_released=emitted)
            except Exception as failure:
                failed = True
                classified = _classify_dispatch_failure(
                    failure,
                    deadline_bound=asyncio.get_running_loop().time() >= deadline,
                    output_released=emitted,
                )
                terminal = emitted or not automatic or index + 1 >= len(candidates)
                outcome(
                    index,
                    "failed",
                    "raise" if terminal else "fallback",
                    failure_category=classified.category,
                    status_code=classified.status_code,
                    retryable=classified.retryable,
                    output_released=emitted,
                )
                if terminal:
                    raise classified.as_unavailable(
                        "capacity_unavailable", "Qualified provider unavailable"
                    ) from None
            finally:
                hold = scope.outstanding.get(_hold_key(reservation))
                if hold is not None:
                    await scope.close_stream(hold)
                await scope.settle(
                    reservation,
                    settlement[0] if completed and settlement is not None else bound,
                    usage=(
                        settlement[1]
                        if completed and settlement is not None
                        else _estimated_usage(generation_id)
                    ),
                    path="stream_end",
                )
            if not failed:
                return
            index, response, reservation = await first_response(index + 1)

    return metered_stream()
