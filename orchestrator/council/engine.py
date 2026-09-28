"""Council engine for multi-perspective deliberation."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from orchestrator.compute_runtime import guarded_completion
from orchestrator.model_routing import routing_context

from orchestrator.council.models import (
    MIN_COUNCIL_DEVELOPERS,
    CouncilConfig,
    CouncilDiversityError,
    CouncilSession,
    CouncilRound,
    PerspectiveResponse,
    PerspectiveType,
    AuditFinding,
    read_developer,
    roster_developers,
)
from orchestrator.council.config import (
    DEFAULT_ROLE_TIMEOUT_SECONDS,
    get_perspective_config,
)
from orchestrator.council import prompts as council_prompts
from orchestrator.council.tools import council_completion_with_tools

from orchestrator.tools.builtin import create_advisor_registry

from orchestrator.tools.executor import ToolExecutor


AUDITOR_ROLE = "auditor"

# The tool loop and the command layer both spell a failed seat as "Error: ..."
# text, so that prefix is the engine's failure marker too.
ERROR_PREFIX = "Error:"

# One seat's result: role, content, error, reasoning, usage, and the model that
# actually served it.
SeatResult = tuple[str, str, str | None, str | None, dict[str, Any], str]


class RoutingSelection(Protocol):
    """The part of the routing state a seat is allowed to read.

    The state is per call, so a seat observes only the route chosen for its own
    request even while its neighbours run concurrently in the same account scope.
    """

    selected_model: str | None


def _get_council_tools():

    # A fresh, purely local tool registry for each deliberation. No singleton
    # can retain another account's memory, credentials or stateful tools.
    registry = create_advisor_registry()
    return registry.list_schemas(), ToolExecutor(registry)


def generate_agent_ids(roster: dict[str, str]) -> dict[str, str]:
    """Generate random anonymous agent IDs for a roster."""
    available_ids = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    random.shuffle(available_ids)
    roles = list(roster.keys())
    return {role: f"Agent-{available_ids[i]}" for i, role in enumerate(roles)}


def debate_seats(roster: Mapping[str, str]) -> dict[str, str]:
    """The seats that deliberate; the auditor only runs in the audit round."""
    return {role: model for role, model in roster.items() if role != AUDITOR_ROLE}


def _seat_exclusions(roster: Mapping[str, str], role: str) -> tuple[frozenset[str], frozenset[str]]:
    """Developers and models this seat must not borrow if it has to fall back.

    A seat never excludes its own developer, so its assigned preference stays
    eligible whenever that preference is qualified. Every *other* seat's
    developer is reserved instead, which confines the fallback to a replacement
    model from the seat's own developer or onto a developer no seat was planned
    for. That is what keeps a substitution from quietly collapsing the planned
    diversity; the round result is still checked afterwards, because two seats
    can each land on the same unused developer.
    """
    developers = roster_developers(roster)
    own_developer = developers.get(role, "")
    own_model = roster.get(role, "")
    excluded_developers = frozenset(
        developer
        for other, developer in developers.items()
        if other != role and developer != own_developer
    )
    excluded_models = frozenset(
        model for other, model in roster.items() if other != role and model != own_model
    )
    return excluded_developers, excluded_models


def _served_seats(results: Sequence[SeatResult]) -> list[tuple[str, str]]:
    """(role, model) for every seat that actually produced a response."""
    served: list[tuple[str, str]] = []
    for role, _content, error, _reasoning, _usage, model_id in results:
        if error is None:
            served.append((role, model_id))
    return served


def _require_planned_diversity(
    roster: Mapping[str, str], roles: Iterable[str], *, stage: str
) -> None:
    """Refuse a round whose planned seats cannot field independent developers."""
    developers = roster_developers(roster)
    planned = sorted({developers[role] for role in roles if developers.get(role)})
    if len(planned) >= MIN_COUNCIL_DEVELOPERS:
        return
    raise CouncilDiversityError(
        f"Council {stage} needs at least {MIN_COUNCIL_DEVELOPERS} model developers planned "
        f"across its seats, but the roster plans {len(planned)}: {', '.join(planned) or 'none'}. "
        "Assign seats to models from more developers, or qualify more developer routes."
    )


def _require_served_diversity(results: Sequence[SeatResult], *, stage: str) -> None:
    """Refuse to call a round a council unless enough developers really answered.

    Seats are allowed to fall back, and two independent fallbacks can still
    collide on one developer that no seat was planned for. Consensus drawn from
    one or two vendors is not a council, so this is an error rather than a
    thinner result: the caller surfaces it and nothing downstream reports a
    successful deliberation.
    """
    served = _served_seats(results)
    distinct = sorted(
        {developer for _role, model_id in served if (developer := read_developer(model_id))}
    )
    if len(distinct) >= MIN_COUNCIL_DEVELOPERS:
        return
    detail = ", ".join(f"{role}={model_id}" for role, model_id in served)
    raise CouncilDiversityError(
        f"Council {stage} was served by {len(distinct)} model developers, "
        f"below the required {MIN_COUNCIL_DEVELOPERS}: {', '.join(distinct) or 'none'}. "
        f"Seats that answered: {detail or 'none'}. "
        "Qualify routes from more developers, or run a council whose seats resolve."
    )


def _responses_from_results(results: Sequence[SeatResult]) -> list[PerspectiveResponse]:
    """Turn raw seat results into responses, keeping the served model id."""
    responses: list[PerspectiveResponse] = []
    for role, content, error, reasoning, usage, model_id in results:
        if error:
            responses.append(
                PerspectiveResponse(
                    perspective=PerspectiveType(role),
                    content=f"Error: {error}",
                    confidence=0.0,
                    reasoning=reasoning,
                    usage=usage,
                    model_id=model_id,
                )
            )
        else:
            responses.append(
                PerspectiveResponse(
                    perspective=PerspectiveType(role),
                    content=content,
                    confidence=_parse_confidence(content),
                    reasoning=reasoning,
                    usage=usage,
                    model_id=model_id,
                )
            )
    return responses


def _parse_confidence(content: str) -> float:
    """Parse confidence rating from model response."""
    lines = content.split("\n")
    for line in lines:
        if line.startswith("**Confidence**:") or line.startswith("Confidence:"):
            try:
                num = float(line.split(":")[1].strip().split("/")[0])
                return min(max(num, 0.0), 10.0)
            except (ValueError, IndexError):
                pass
    return 5.0


def _get_message_content(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            text = block.get("text") or block.get("content")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(part for part in parts if part)

    return ""


def _get_message_reasoning(message: Any) -> str | None:
    def _coerce_reasoning(value: Any) -> str | None:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None

        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_text = _coerce_reasoning(item)
                if item_text:
                    parts.append(item_text)
            if parts:
                return "\n".join(parts)
            return None

        if isinstance(value, dict):
            parts: list[str] = []
            for key in (
                "text",
                "content",
                "reasoning",
                "reasoning_content",
                "thinking",
                "summary",
                "output_text",
            ):
                item_text = _coerce_reasoning(value.get(key))
                if item_text:
                    parts.append(item_text)
            if parts:
                return "\n".join(parts)
            return None

        return None

    candidates: list[Any] = []
    if isinstance(message, dict):
        candidates.extend(
            [
                message.get("reasoning_content"),
                message.get("reasoning"),
                message.get("thinking"),
                message.get("reasoning_details"),
            ]
        )
    else:
        candidates.extend(
            [
                getattr(message, "reasoning_content", None),
                getattr(message, "reasoning", None),
                getattr(message, "thinking", None),
                getattr(message, "reasoning_details", None),
            ]
        )

    for candidate in candidates:
        text = _coerce_reasoning(candidate)
        if text:
            return text

    return None


def _extract_response_model(response: Any, requested_model: str) -> str:
    model_name: Any = None
    if isinstance(response, dict):
        model_name = response.get("model")
    else:
        model_name = getattr(response, "model", None)

    if isinstance(model_name, str) and model_name.strip():
        return model_name.strip()

    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        hidden_model = hidden.get("model")
        if isinstance(hidden_model, str) and hidden_model.strip():
            return hidden_model.strip()

    return requested_model


def _extract_usage(response: Any) -> dict[str, Any]:
    usage_payload: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }

    usage = getattr(response, "usage", None)
    if usage is not None:
        if isinstance(usage, dict):
            usage_payload["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
            usage_payload["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
            usage_payload["total_tokens"] = int(usage.get("total_tokens", 0) or 0)
        else:
            usage_payload["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
            usage_payload["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
            usage_payload["total_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)

    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if cost is None:
            headers = hidden.get("additional_headers")
            if isinstance(headers, dict):
                cost = headers.get("llm_provider-x-litellm-response-cost")
        if isinstance(cost, (int, float, str)):
            try:
                usage_payload["cost_usd"] = float(str(cost))
            except (TypeError, ValueError):
                usage_payload["cost_usd"] = 0.0
        else:
            usage_payload["cost_usd"] = 0.0

    return usage_payload


def _routed_model(routing: RoutingSelection, reported: str) -> str:
    """The model that actually served this seat.

    Selection is capability-first, so the model a seat asks for is not
    necessarily the model that answers it, and reporting the request would put a
    model in the council record that never spoke. The per-call routing state is
    the only truthful source, and it is scoped to this seat alone. When nothing
    was dispatched at all (a mocked seat, or a call rejected before routing)
    there is no selection to report, so the caller's own fallback stands.
    """
    selected = routing.selected_model
    if isinstance(selected, str) and selected.strip():
        return selected.strip()
    return reported


async def _call_model(
    *,
    role: str,
    model: str,
    prompt: str,
    system_prompt: str,
    timeout_s: float,
    tools: list[dict[str, Any]] | None = None,
    tool_executor: ToolExecutor | None = None,
    excluded_models: frozenset[str] = frozenset(),
    excluded_developers: frozenset[str] = frozenset(),
) -> SeatResult:
    """Run one seat and report the model that really served it.

    The seat's preference is a request, not a reservation: the runtime selects
    the cheapest qualified route that satisfies it, and the seat reports what it
    got. The excluded sets keep a substitution inside the seat's own developer
    (or onto a developer no other seat is planned for) so the round cannot lose
    its independence without anybody noticing.

    Model-specific parameters are deliberately absent: the central routing
    presets own which reasoning and sampling options a chosen model accepts, and
    council used to guess with a blanket setting plus a retry that quietly
    stripped it. Nothing here may be model-shaped.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    with routing_context(
        "council",
        preferred_model=model,
        excluded_models=excluded_models,
        excluded_developers=excluded_developers,
    ) as routing:
        if tools and tool_executor:
            try:
                content, usage = await council_completion_with_tools(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_executor=tool_executor,
                    timeout=int(timeout_s),
                )
            except Exception as exc:
                return (role, "", str(exc), None, {}, _routed_model(routing, model))
            # The tool loop reports its own failures as "Error: ..." text rather
            # than raising, so a seat that never actually answered must be
            # classified here: a failed seat cannot be counted as a served
            # developer, and its text is already the error.
            if content.startswith(ERROR_PREFIX):
                return (
                    role,
                    "",
                    content[len(ERROR_PREFIX) :].strip(),
                    None,
                    dict(usage),
                    _routed_model(routing, model),
                )
            return (role, content, None, None, dict(usage), _routed_model(routing, model))

        try:
            response = await asyncio.wait_for(
                guarded_completion(model=model, messages=messages, timeout=timeout_s),
                timeout=timeout_s + 5,
            )
        except asyncio.TimeoutError:
            return (
                role,
                "",
                f"Timeout after {timeout_s:.0f}s",
                None,
                {},
                _routed_model(routing, model),
            )
        except Exception as exc:
            return (role, "", str(exc), None, {}, _routed_model(routing, model))

        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            return (
                role,
                "",
                "No choices returned",
                None,
                _extract_usage(response),
                _routed_model(routing, model),
            )

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is None and isinstance(first_choice, dict):
            message = first_choice.get("message")

        content = _get_message_content(message)
        reasoning_text = _get_message_reasoning(message) or _get_message_reasoning(response)
        usage = _extract_usage(response)
        actual_model = _routed_model(routing, _extract_response_model(response, model))
        return (role, content, None, reasoning_text, usage, actual_model)


async def run_round_1(
    prompt: str,
    roster: dict[str, str],
    timeout_by_role: dict[str, float] | None = None,
) -> list[PerspectiveResponse]:
    """Run Round 1 - independent responses from each perspective."""
    seats = debate_seats(roster)
    if not seats:
        return []
    _require_planned_diversity(roster, seats, stage="round 1")

    # Get tools for Round 1
    tools, tool_executor = _get_council_tools()

    # Prepend tool preamble to system prompt
    from datetime import datetime

    current_date = datetime.now().strftime("%Y-%m-%d")
    system_prompt_with_preamble = f"{council_prompts.COUNCIL_TOOL_PREAMBLE.format(current_date=current_date)}\n\n{council_prompts.ROUND_1_SYSTEM}"

    results = await fan_out(
        prompt,
        roster,
        system_prompt_with_preamble,
        timeout_by_role=timeout_by_role,
        tools=tools,
        tool_executor=tool_executor,
    )
    # Round 1 is the council's own baseline, so its diversity is verified here
    # rather than left for the reader of the transcript to notice.
    _require_served_diversity(results, stage="round 1")
    return _responses_from_results(results)


async def run_round_2(
    prompt: str,
    roster: dict[str, str],
    round_1_responses: list[PerspectiveResponse],
    agent_ids: dict[str, str],
    preset: str = "default",
    timeout_by_role: dict[str, float] | None = None,
) -> list[PerspectiveResponse]:
    """Run Round 2 - adversarial review of other perspectives."""
    per_role_timeouts = timeout_by_role or {}
    roles_to_call = debate_seats(roster)
    if not roles_to_call:
        return []
    _require_planned_diversity(roster, roles_to_call, stage="round 2")

    async def call_role(role: str, model: str) -> SeatResult:
        other_responses = []
        for resp in round_1_responses:
            resp_role = resp.perspective.value
            if resp_role == role:
                continue
            anon_id = agent_ids.get(resp_role, "Unknown")
            other_responses.append(f"[{anon_id}]:\n{resp.content}")

        others_text = "\n\n".join(other_responses)
        role_system_prompt = council_prompts.ROUND_2_SYSTEM
        if preset == "adversarial" and role == "contrarian":
            role_system_prompt = council_prompts.ROUND_2_CONTRARIAN

        review_prompt = f"""Original question: {prompt}

Below are responses from other advisors:
{others_text}
"""

        excluded_developers, excluded_models = _seat_exclusions(roster, role)
        timeout_s = per_role_timeouts.get(role, DEFAULT_ROLE_TIMEOUT_SECONDS)
        return await _call_model(
            role=role,
            model=model,
            prompt=review_prompt,
            system_prompt=role_system_prompt,
            timeout_s=timeout_s,
            excluded_models=excluded_models,
            excluded_developers=excluded_developers,
        )

    tasks = [call_role(role, model) for role, model in roles_to_call.items()]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # The final round decides the council, so it is the last chance to refuse a
    # round that only one or two developers answered.
    _require_served_diversity(results, stage="round 2")
    return _responses_from_results(results)


async def run_audit_round(
    original_prompt: str,
    roster: dict[str, str],
    final_responses: list[PerspectiveResponse],
    timeout_s: float = DEFAULT_ROLE_TIMEOUT_SECONDS,
) -> tuple[list[AuditFinding], dict[str, Any], str | None]:
    """Run audit round - auditor reviews all final positions."""
    auditor_model = roster.get(AUDITOR_ROLE)
    if not auditor_model:
        return [], {}, None

    responses_text = []
    for resp in final_responses:
        responses_text.append(f"[{resp.perspective.value.upper()}]:\n{resp.content}")
    responses_block = "\n\n".join(responses_text)

    audit_instructions = council_prompts.AUDIT_ROUND.format(
        num_agents=len(final_responses),
        original_prompt=original_prompt,
    )
    audit_prompt = f"""Review the following final positions from the council:

{responses_block}

{audit_instructions}"""

    # The auditor reviews the seats, so it must not be served by one of them: an
    # audit answered by a model that also argued a case is not independent. Its
    # own preference stays eligible, and if that is unavailable the audit is
    # reported as a failed audit rather than quietly given to a debater.
    excluded_developers, excluded_models = _seat_exclusions(roster, AUDITOR_ROLE)
    # Earlier rounds may have used an unassigned developer on fallback. Audit
    # independence is against the models that actually argued, not just the plan.
    served_models = frozenset(resp.model_id for resp in final_responses if resp.model_id)
    excluded_models |= served_models
    excluded_developers |= frozenset(
        developer for model in served_models if (developer := read_developer(model))
    )
    role, content, error, _reasoning, usage, model_id = await _call_model(
        role=AUDITOR_ROLE,
        model=auditor_model,
        prompt=audit_prompt,
        system_prompt="You are an independent auditor.",
        timeout_s=timeout_s,
        excluded_models=excluded_models,
        excluded_developers=excluded_developers,
    )
    if error:
        return (
            [
                AuditFinding(
                    category="execution",
                    severity="moderate",
                    description=f"Audit round failed: {error}",
                )
            ],
            usage,
            model_id,
        )

    findings = _parse_audit_findings(content)
    return findings, usage, model_id


def _parse_audit_findings(content: str) -> list[AuditFinding]:
    """Parse audit findings from auditor response."""
    findings = []
    current_severity = "note"
    current_category = "general"

    lines = content.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "**CRITICAL" in line.upper() or "[CRITICAL]" in line.upper():
            current_severity = "critical"
        elif "**MODERATE" in line.upper() or "[MODERATE]" in line.upper():
            current_severity = "moderate"
        elif "**NOTE" in line.upper() or "[NOTE]" in line.upper():
            current_severity = "note"
        elif line.startswith("-") or line.startswith("*"):
            desc = line.lstrip("-* ").strip()
            if desc and len(desc) > 10:
                findings.append(
                    AuditFinding(
                        category=current_category,
                        severity=current_severity,
                        description=desc,
                    )
                )

    return findings


async def fan_out(
    prompt: str,
    roster: dict[str, str],
    system_prompt: str | None = None,
    timeout_by_role: dict[str, float] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_executor: ToolExecutor | None = None,
) -> list[SeatResult]:
    """Ask every debating seat for an independent opinion, concurrently.

    Each seat is planned against the developers the *other* seats hold, so a
    capability-driven fallback inside one seat cannot quietly take over a seat
    planned for a different vendor. This is a fan-out primitive: whether the
    seats that came back are still an independent council is the caller's call.
    """
    # Exclude auditor from fan-out (only used in audit round)
    roles_to_call = debate_seats(roster)

    if not roles_to_call:
        return []

    default_system = system_prompt or council_prompts.ROUND_1_SYSTEM

    per_role_timeouts = timeout_by_role or {}
    tasks = []
    for role, model in roles_to_call.items():
        excluded_developers, excluded_models = _seat_exclusions(roster, role)
        tasks.append(
            _call_model(
                role=role,
                model=model,
                prompt=prompt,
                system_prompt=default_system,
                timeout_s=per_role_timeouts.get(role, DEFAULT_ROLE_TIMEOUT_SECONDS),
                tools=tools,
                tool_executor=tool_executor,
                excluded_models=excluded_models,
                excluded_developers=excluded_developers,
            )
        )
    results = await asyncio.gather(*tasks, return_exceptions=False)

    return results


class CouncilEngine:
    """Engine for running council deliberations."""

    def __init__(self, config: CouncilConfig | None = None):
        """Initialize council engine."""
        self.config = config or CouncilConfig()

    async def run_deliberation(
        self,
        prompt: str,
        conversation_id: str = "",
    ) -> CouncilSession:
        """Run a complete council deliberation."""
        session = CouncilSession(
            session_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            prompt=prompt,
            config=self.config,
        )

        for round_num in range(1, self.config.round_count + 1):
            round_result = await self._run_round(round_num, prompt, session)
            session.rounds.append(round_result)

        session.final_output = await self._synthesize_output(session)
        return session

    async def _run_round(
        self,
        round_num: int,
        prompt: str,
        session: CouncilSession,
    ) -> CouncilRound:
        """Run a single deliberation round."""
        round_obj = CouncilRound(
            round_number=round_num,
            prompt=prompt,
        )

        responses = []
        for perspective_name in self.config.roster.keys():
            if perspective_name == "auditor":
                continue
            perspective = PerspectiveType(perspective_name)
            response = await self._get_perspective_response(perspective, prompt, session)
            responses.append(response)

        round_obj.responses = responses
        round_obj.consensus = await self._compute_consensus(responses)
        return round_obj

    async def _get_perspective_response(
        self,
        perspective: PerspectiveType,
        prompt: str,
        session: CouncilSession,
    ) -> PerspectiveResponse:
        """Get response from a single perspective."""
        config = get_perspective_config(perspective.value)  # noqa: F841
        return PerspectiveResponse(
            perspective=perspective,
            content="",
            confidence=0.0,
        )

    async def _compute_consensus(
        self,
        responses: list[PerspectiveResponse],
    ) -> str | None:
        """Compute consensus from perspective responses."""
        return None

    async def _synthesize_output(
        self,
        session: CouncilSession,
    ) -> str:
        """Synthesize final output from council session."""
        return ""
