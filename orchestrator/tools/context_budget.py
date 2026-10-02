"""Fit tool evidence using the same qualification and sizing rules as dispatch.

This is a preflight, never admission authority: guarded_completion still resolves
the account and reserves each actual model call after tool execution.
"""

from dataclasses import dataclass, field
from typing import Any

from orchestrator import compute_runtime
from orchestrator.entitlements.models import ResolvedPolicy
from orchestrator.guardrails import strip_reasoning_fields_from_message


SYNTHESIS_NOTICE = (
    "Tool execution is complete or the remaining context allowance is exhausted. "
    "Do not call any more tools. Provide the final user-facing answer from the "
    "available evidence. If a source was only partly read or a result was omitted, "
    "state that limitation; do not claim complete coverage."
)
SKIPPED_RESULT = (
    '{"error":"context_budget_exhausted",'
    '"message":"This tool was not executed; remaining context is reserved for the answer."}'
)
OMITTED_RESULT = (
    '{"error":"context_budget_exhausted",'
    '"message":"The tool ran, but its output could not fit; source coverage is incomplete."}'
)


def synthesis_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        *(strip_reasoning_fields_from_message(message) for message in messages),
        {"role": "system", "content": SYNTHESIS_NOTICE},
    ]


@dataclass
class ToolContextBudget:
    policy: ResolvedPolicy = field(repr=False)
    params: dict[str, Any] = field(repr=False)
    requested_model: str | None
    extended: bool
    account_allow_premium: bool
    route_id: str | None = None
    output_tokens: int = 0

    def fits(self, messages: list[dict[str, Any]], *, tools: Any = None) -> bool:
        if self.route_id is None:
            return False
        params = dict(self.params, messages=synthesis_messages(messages))
        params.pop("tools", None)
        params.pop("tool_choice", None)
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        params["max_tokens"] = self.output_tokens
        try:
            input_size = compute_runtime._request_bound(params)
            candidates = compute_runtime._priced_candidates(
                self.policy,
                input_size,
                params,
                self.requested_model,
                self.extended,
                account_allow_premium=self.account_allow_premium,
            )
        except compute_runtime.ComputeUnavailable:
            return False
        for _, output, route, premium, _ in candidates:
            account_context = self.policy.limits_for(premium).max_context_tokens
            ceiling = min(route.max_context_tokens, account_context or route.max_context_tokens)
            if input_size.estimate + output <= ceiling and (
                tools or route.route_id == self.route_id
            ):
                return True
        return False


async def tool_context_budget(
    params: dict[str, Any], messages_with_placeholders: list[dict[str, Any]]
) -> ToolContextBudget | None:
    """Reserve answer space before accepting results from a tool-call batch.

    No account scope means no preflight (e.g. a mocked completion in unit tests),
    not permission to make an unguarded provider call.
    """
    scope = compute_runtime._scope.get()
    if scope is None:
        return None
    policy = await scope.service.resolve(scope.user_id)
    requested = params.get("model")
    model = (
        requested
        if isinstance(requested, str) and requested not in {"", "auto"} and not scope.auto_route
        else None
    )
    synthesis_params = dict(params, messages=synthesis_messages(messages_with_placeholders))
    synthesis_params.pop("tools", None)
    synthesis_params.pop("tool_choice", None)
    budget = ToolContextBudget(policy, params, model, scope.extended, scope.account_allow_premium)
    input_size = None
    try:
        input_size = compute_runtime._request_bound(synthesis_params)
        candidates = compute_runtime._priced_candidates(
            policy,
            input_size,
            synthesis_params,
            model,
            scope.extended,
            account_allow_premium=scope.account_allow_premium,
            fit_to_budget=True,
        )
    except compute_runtime.ComputeUnavailable:
        candidates = []
    if candidates and input_size is not None:
        _, output, route, premium, _ = candidates[0]
        account_context = policy.limits_for(premium).max_context_tokens
        ceiling = min(route.max_context_tokens, account_context or route.max_context_tokens)
        budget.output_tokens = min(output, max(0, ceiling - input_size.estimate))
        if budget.output_tokens:
            budget.route_id = route.route_id
    return budget
