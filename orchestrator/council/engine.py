"""Council engine for multi-perspective deliberation."""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

import litellm

from orchestrator.council.models import (
    CouncilConfig,
    CouncilSession,
    CouncilRound,
    PerspectiveResponse,
    PerspectiveType,
    AuditFinding,
)
from orchestrator.council.config import (
    DEFAULT_ROLE_TIMEOUT_SECONDS,
    get_perspective_config,
)
from orchestrator.council import prompts as council_prompts
from orchestrator.council.tools import council_completion_with_tools
from orchestrator.config import get_settings
from orchestrator.tools.builtin import create_council_readonly_registry
from orchestrator.tools.executor import ToolExecutor


_council_tool_registry = None
_council_tool_executor = None


def _get_council_tools():
    global _council_tool_registry, _council_tool_executor
    if _council_tool_registry is None:
        _council_tool_registry = create_council_readonly_registry(
            brave_api_key=get_settings().brave_api_key,
        )
        _council_tool_executor = ToolExecutor(_council_tool_registry)
    return _council_tool_registry.list_schemas(), _council_tool_executor


def generate_agent_ids(roster: dict[str, str]) -> dict[str, str]:
    """Generate random anonymous agent IDs for a roster."""
    available_ids = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    random.shuffle(available_ids)
    roles = list(roster.keys())
    return {role: f"Agent-{available_ids[i]}" for i, role in enumerate(roles)}


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


def _reasoning_kwargs(model: str) -> dict[str, Any]:
    if not model.startswith("openrouter/"):
        return {}
    return {
        "reasoning_effort": "medium",
        "include_reasoning": True,
    }


def _is_reasoning_param_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "reasoning",
        "include_reasoning",
        "reasoning_effort",
        "unsupported parameter",
    )
    return any(marker in text for marker in markers)


async def _call_model(
    *,
    role: str,
    model: str,
    prompt: str,
    system_prompt: str,
    timeout_s: float,
    tools: list[dict[str, Any]] | None = None,
    tool_executor: ToolExecutor | None = None,
) -> tuple[str, str, str | None, str | None, dict[str, Any], str]:
    # If tools are provided, use council_completion_with_tools
    if tools and tool_executor:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            content, usage = await council_completion_with_tools(
                model=model,
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                timeout=int(timeout_s),
            )
            return (role, content, None, None, dict(usage), model)
        except Exception as exc:
            return (role, "", str(exc), None, {}, model)

    # Otherwise, use existing single-shot path
    params: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "timeout": timeout_s,
    }
    reasoning = _reasoning_kwargs(model)
    params.update(reasoning)

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**params),
            timeout=timeout_s + 5,
        )
    except Exception as exc:
        if reasoning and _is_reasoning_param_error(exc):
            fallback_params = dict(params)
            fallback_params.pop("reasoning_effort", None)
            fallback_params.pop("include_reasoning", None)
            try:
                response = await asyncio.wait_for(
                    litellm.acompletion(**fallback_params),
                    timeout=timeout_s + 5,
                )
            except asyncio.TimeoutError:
                return (
                    role,
                    "",
                    f"Timeout after {timeout_s:.0f}s",
                    None,
                    {},
                    model,
                )
            except Exception as fallback_exc:
                return (role, "", str(fallback_exc), None, {}, model)
        elif isinstance(exc, asyncio.TimeoutError):
            return (role, "", f"Timeout after {timeout_s:.0f}s", None, {}, model)
        else:
            return (role, "", str(exc), None, {}, model)

    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return (role, "", "No choices returned", None, _extract_usage(response), model)

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    if message is None and isinstance(first_choice, dict):
        message = first_choice.get("message")

    content = _get_message_content(message)
    reasoning_text = _get_message_reasoning(message) or _get_message_reasoning(response)
    usage = _extract_usage(response)
    actual_model = _extract_response_model(response, model)
    return (role, content, None, reasoning_text, usage, actual_model)


async def run_round_1(
    prompt: str,
    roster: dict[str, str],
    timeout_by_role: dict[str, float] | None = None,
) -> list[PerspectiveResponse]:
    """Run Round 1 - independent responses from each perspective."""
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
    responses = []
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
            confidence = _parse_confidence(content)
            responses.append(
                PerspectiveResponse(
                    perspective=PerspectiveType(role),
                    content=content,
                    confidence=confidence,
                    reasoning=reasoning,
                    usage=usage,
                    model_id=model_id,
                )
            )
    return responses


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
    roles_to_call = {role: model for role, model in roster.items() if role != "auditor"}

    async def call_role(
        role: str, model: str
    ) -> tuple[str, str, str | None, str | None, dict[str, Any], str]:
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

        timeout_s = per_role_timeouts.get(role, DEFAULT_ROLE_TIMEOUT_SECONDS)
        return await _call_model(
            role=role,
            model=model,
            prompt=review_prompt,
            system_prompt=role_system_prompt,
            timeout_s=timeout_s,
        )

    tasks = [call_role(role, model) for role, model in roles_to_call.items()]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    responses = []
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
            confidence = _parse_confidence(content)
            responses.append(
                PerspectiveResponse(
                    perspective=PerspectiveType(role),
                    content=content,
                    confidence=confidence,
                    reasoning=reasoning,
                    usage=usage,
                    model_id=model_id,
                )
            )
    return responses


async def run_audit_round(
    original_prompt: str,
    roster: dict[str, str],
    final_responses: list[PerspectiveResponse],
    timeout_s: float = DEFAULT_ROLE_TIMEOUT_SECONDS,
) -> tuple[list[AuditFinding], dict[str, Any], str | None]:
    """Run audit round - auditor reviews all final positions."""
    auditor_model = roster.get("auditor")
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

    role, content, error, _reasoning, usage, _model_id = await _call_model(
        role="auditor",
        model=auditor_model,
        prompt=audit_prompt,
        system_prompt="You are an independent auditor.",
        timeout_s=timeout_s,
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
            _model_id,
        )

    findings = _parse_audit_findings(content)
    return findings, usage, _model_id


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
) -> list[tuple[str, str, str | None, str | None, dict[str, Any], str]]:
    # Exclude auditor from fan-out (only used in audit round)
    roles_to_call = {role: model for role, model in roster.items() if role != "auditor"}

    if not roles_to_call:
        return []

    default_system = system_prompt or council_prompts.ROUND_1_SYSTEM

    per_role_timeouts = timeout_by_role or {}
    tasks = [
        _call_model(
            role=role,
            model=model,
            prompt=prompt,
            system_prompt=default_system,
            timeout_s=per_role_timeouts.get(role, DEFAULT_ROLE_TIMEOUT_SECONDS),
            tools=tools,
            tool_executor=tool_executor,
        )
        for role, model in roles_to_call.items()
    ]
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
