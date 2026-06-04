"""Council command handler for Daemon."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from orchestrator.council.config import load_role_timeouts, load_roster
from orchestrator.council.engine import (
    generate_agent_ids,
    run_round_1,
    run_round_2,
    run_audit_round,
)
from orchestrator.council.interview import (
    render_interview_message,
    parse_interview_response,
)
from orchestrator.council.models import CouncilConfig, CouncilSession, CouncilRound
from orchestrator.council.output import CouncilOutputRenderer

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def handle_council_command(
    message: str,
    conversation_id: str = "",
    bypass_interview: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Handle /council command.

    Args:
        message: User message (may include /council prefix and prompt)
        conversation_id: Conversation ID for session tracking
        bypass_interview: If True, skip interview and use defaults

    Returns:
        Dict with response type and content
    """
    prompt = message
    if prompt.startswith("/council"):
        prompt = prompt[len("/council") :].strip()
        if prompt.startswith("--default"):
            prompt = prompt[len("--default") :].strip()
            bypass_interview = True
        elif prompt.startswith("--"):
            parts = prompt.split(" ", 1)
            if len(parts) > 1:
                prompt = parts[1].strip()

    if not prompt:
        return {
            "type": "error",
            "content": "Please provide a prompt for the council. Usage: /council {your question}",
        }

    if not bypass_interview:
        config = CouncilConfig()
        config.interview_state = {"prompt": prompt}
        interview_msg = render_interview_message(config)
        return {
            "type": "interview",
            "content": interview_msg,
            "config": config,
        }

    return await run_council(
        prompt,
        conversation_id,
        progress_callback=progress_callback,
    )


async def handle_council_interview_response(
    response: str,
    config: CouncilConfig,
    conversation_id: str = "",
    prompt_override: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Handle user's response to council interview."""
    config = parse_interview_response(response, config)
    prompt = (prompt_override or config.interview_state.get("prompt", "")).strip()
    if not prompt:
        return {
            "type": "error",
            "content": "Session expired. Please start a new /council command.",
        }
    return await run_council(
        prompt,
        conversation_id,
        config,
        progress_callback=progress_callback,
    )


async def run_council(
    prompt: str,
    conversation_id: str = "",
    config: CouncilConfig | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the full council deliberation."""
    if config is None:
        config = CouncilConfig()

    explicit_fields = getattr(config, "model_fields_set", set())
    has_explicit_roster = isinstance(explicit_fields, set) and "roster" in explicit_fields
    roster = config.roster if has_explicit_roster else load_roster(config.preset_name)
    config.roster = roster
    role_timeouts = load_role_timeouts(config.preset_name)
    agent_ids = generate_agent_ids(roster)
    debate_roles = [role for role in roster if role != "auditor"]
    total_rounds = config.round_count + (1 if config.audit_enabled else 0)

    async def emit_progress(
        *,
        stage: str,
        current_round: int,
        models_complete: int,
        models_total: int,
    ) -> None:
        if progress_callback is None:
            return
        await progress_callback(
            {
                "stage": stage,
                "current_round": current_round,
                "total_rounds": total_rounds,
                "models_complete": models_complete,
                "models_total": models_total,
            }
        )

    session = CouncilSession(
        session_id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        prompt=prompt,
        config=config,
    )
    progress_events: list[dict[str, Any]] = []

    async def capture_progress(
        *,
        stage: str,
        current_round: int,
        models_complete: int,
        models_total: int,
    ) -> None:
        event = {  # noqa: F841
            "stage": stage,
            "current_round": current_round,
            "total_rounds": total_rounds,
            "models_complete": models_complete,
            "models_total": models_total,
        }
        # Emit event immediately instead of buffering
        await emit_progress(
            stage=stage,
            current_round=current_round,
            models_complete=models_complete,
            models_total=models_total,
        )

    await capture_progress(
        stage="round_1",
        current_round=1,
        models_complete=0,
        models_total=len(debate_roles),
    )

    round_1_responses = await run_round_1(
        prompt,
        roster,
        timeout_by_role=role_timeouts,
    )
    session.rounds.append(CouncilRound(round_number=1, prompt=prompt, responses=round_1_responses))
    round_1_success = sum(
        1 for response in round_1_responses if not response.content.startswith("Error:")
    )
    await capture_progress(
        stage="round_1",
        current_round=1,
        models_complete=round_1_success,
        models_total=len(debate_roles),
    )

    previous_round = round_1_responses
    for round_num in range(2, config.round_count + 1):
        await capture_progress(
            stage=f"round_{round_num}",
            current_round=round_num,
            models_complete=0,
            models_total=len(debate_roles),
        )
        round_2_responses = await run_round_2(
            prompt,
            roster,
            previous_round,
            agent_ids,
            config.preset_name,
            timeout_by_role=role_timeouts,
        )
        session.rounds.append(
            CouncilRound(
                round_number=round_num,
                prompt=prompt,
                responses=round_2_responses,
            )
        )
        round_success = sum(
            1 for response in round_2_responses if not response.content.startswith("Error:")
        )
        await capture_progress(
            stage=f"round_{round_num}",
            current_round=round_num,
            models_complete=round_success,
            models_total=len(debate_roles),
        )
        previous_round = round_2_responses

    audit_usage: dict[str, Any] = {}
    audit_model_id: str | None = None
    if config.audit_enabled:
        await capture_progress(
            stage="audit",
            current_round=config.round_count + 1,
            models_complete=0,
            models_total=1,
        )
        audit_findings, audit_usage, audit_model_id = await run_audit_round(
            prompt,
            roster,
            previous_round,
            timeout_s=role_timeouts.get("auditor", 120.0),
        )
        session.audit_findings = audit_findings
        await capture_progress(
            stage="audit",
            current_round=config.round_count + 1,
            models_complete=1,
            models_total=1,
        )

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cost_usd = 0.0
    models_used: set[str] = set()
    usage_by_role: dict[str, dict[str, Any]] = {}

    for round_obj in session.rounds:
        for response in round_obj.responses:
            role = response.perspective.value
            usage = response.usage if isinstance(response.usage, dict) else {}
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            response_total_tokens = int(usage.get("total_tokens", 0) or 0)
            cost_usd = float(usage.get("cost_usd", 0.0) or 0.0)

            total_prompt_tokens += prompt_tokens
            total_completion_tokens += completion_tokens
            total_tokens += response_total_tokens
            total_cost_usd += cost_usd

            if response.model_id:
                models_used.add(response.model_id)

            role_usage = usage_by_role.setdefault(
                role,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            role_usage["prompt_tokens"] += prompt_tokens
            role_usage["completion_tokens"] += completion_tokens
            role_usage["total_tokens"] += response_total_tokens
            role_usage["cost_usd"] += cost_usd

    if isinstance(audit_usage, dict) and audit_usage:
        audit_prompt = int(audit_usage.get("prompt_tokens", 0) or 0)
        audit_completion = int(audit_usage.get("completion_tokens", 0) or 0)
        audit_total = int(audit_usage.get("total_tokens", 0) or 0)
        audit_cost = float(audit_usage.get("cost_usd", 0.0) or 0.0)
        total_prompt_tokens += audit_prompt
        total_completion_tokens += audit_completion
        total_tokens += audit_total
        total_cost_usd += audit_cost
        usage_by_role["auditor"] = {
            "prompt_tokens": audit_prompt,
            "completion_tokens": audit_completion,
            "total_tokens": audit_total,
            "cost_usd": audit_cost,
        }
        auditor_model = audit_model_id or roster.get("auditor")
        if auditor_model:
            models_used.add(auditor_model)

    session.token_costs = {
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost_usd,
        "models_used": sorted(models_used),
        "by_role": usage_by_role,
    }
    session.metadata["progress_events"] = progress_events

    renderer = CouncilOutputRenderer()
    output = renderer.render_session(session)

    return {
        "type": "council_output",
        "content": renderer.render_text(output),
        "session_id": session.session_id,
        "output": output,
    }


async def handle_council_retry(
    session_id: str,
    new_config: CouncilConfig | None = None,
) -> dict[str, Any]:
    """Handle /council:retry command."""
    return {
        "type": "error",
        "content": "Retry functionality not yet implemented.",
    }


async def handle_council_audit(
    session_id: str,
) -> dict[str, Any]:
    """Handle /council:audit command."""
    return {
        "type": "error",
        "content": "Post-hoc audit not yet implemented.",
    }
