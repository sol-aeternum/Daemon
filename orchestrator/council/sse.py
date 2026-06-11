"""Council SSE event emitter - wraps council engine with SSE streaming."""

from dataclasses import is_dataclass
import asyncio
import json
from typing import Any, AsyncGenerator

from orchestrator.commands.council import (
    handle_council_command,
    handle_council_interview_response,
    run_council,
)
from orchestrator.council.config import load_roster
from orchestrator.council.models import CouncilConfig
from orchestrator.daemon import now_rfc3339, sse


def _config_to_dict(config: Any) -> dict[str, Any]:
    if isinstance(config, CouncilConfig):
        return config.model_dump()
    if isinstance(config, dict):
        return config
    return {}


def _build_interview_roster(roster: dict[str, str]) -> dict[str, dict[str, str]]:
    role_descriptions = {
        "analyst": "Data-first position with probabilities and assumptions.",
        "strategist": "Long-term options and sequencing trade-offs.",
        "skeptic": "Failure modes, blind spots, and downside risks.",
        "contrarian": "Alternative framing that challenges consensus.",
        "auditor": "Logic and evidence quality checks.",
    }
    built: dict[str, dict[str, str]] = {}
    for role, model_id in roster.items():
        built[role] = {
            "name": role.replace("_", " ").title(),
            "role": role,
            "description": role_descriptions.get(role, model_id),
        }
    return built


def _parse_config_kv(raw: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "preset":
            parsed["preset_name"] = value.lower()
            continue

        if key == "rounds":
            try:
                parsed["round_count"] = int(value)
            except ValueError:
                continue
            continue

        if key == "audit":
            parsed["audit_enabled"] = value.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
    return parsed


def _normalize_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output

    if isinstance(output, type):
        return {}

    if is_dataclass(output):
        output_dict = getattr(output, "__dict__", None)
        return dict(output_dict) if isinstance(output_dict, dict) else {}

    model_dump = getattr(output, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}

    to_dict = getattr(output, "dict", None)
    if callable(to_dict):
        dumped = to_dict()
        return dumped if isinstance(dumped, dict) else {}

    return {}


def _format_perspectives_summary(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        parts = []
        for advisor, perspective in raw.items():
            parts.append(f"### {advisor}\n{perspective}")
        return "\n\n".join(parts)
    return ""


def _format_findings(raw: Any) -> tuple[str, int]:
    if not isinstance(raw, list):
        return "", 0

    lines: list[str] = []
    for finding in raw:
        finding_dict = finding if isinstance(finding, dict) else _normalize_output(finding)
        if not isinstance(finding_dict, dict):
            continue
        category = finding_dict.get("category", "Finding")
        severity = finding_dict.get("severity")
        description = finding_dict.get("description", "")
        recommendation = finding_dict.get("recommendation")

        headline = category if not severity else f"{category} ({severity})"
        body = f"{description}".strip()
        if recommendation:
            body = f"{body}\nRecommendation: {recommendation}".strip()
        lines.append(f"## {headline}\n{body}".strip())

    return "\n\n".join(lines), len(lines)


async def _emit_council_output_events(
    *,
    output: dict[str, Any],
    session_id: str,
    conversation_id: str,
    request_id: str,
) -> AsyncGenerator[str, None]:
    normalized_output = _normalize_output(output)
    metadata = normalized_output.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    progress_events = metadata.get("progress_events", [])
    if isinstance(progress_events, list) and progress_events:
        for progress_event in progress_events:
            if not isinstance(progress_event, dict):
                continue
            yield sse(
                "council_progress",
                make_envelope(
                    "council_progress",
                    {
                        "stage": progress_event.get("stage", "council"),
                        "current_round": int(progress_event.get("current_round", 0) or 0),
                        "total_rounds": int(progress_event.get("total_rounds", 0) or 0),
                        "models_complete": int(progress_event.get("models_complete", 0) or 0),
                        "models_total": int(progress_event.get("models_total", 0) or 0),
                    },
                    conversation_id,
                    request_id,
                ),
            )
    else:
        models_used = metadata.get("models_used", [])
        model_count = len(models_used) if isinstance(models_used, list) else 0
        models_total = int(metadata.get("models_total", 0) or 0)
        if models_total <= 0:
            models_total = model_count
        if models_total <= 0:
            models_total = 1

        models_complete = model_count if model_count > 0 else models_total

        current_round = int(metadata.get("completed_rounds", 0) or 0)
        if current_round <= 0:
            current_round = 1

        total_rounds = int(metadata.get("total_rounds", 0) or 0)
        if total_rounds <= 0:
            total_rounds = max(current_round, 1)
        yield sse(
            "council_progress",
            make_envelope(
                "council_progress",
                {
                    "stage": "completed",
                    "current_round": current_round,
                    "total_rounds": total_rounds,
                    "models_complete": models_complete,
                    "models_total": models_total,
                },
                conversation_id,
                request_id,
            ),
        )

    consensus = normalized_output.get("consensus")
    if isinstance(consensus, str) and consensus.strip():
        yield sse(
            "council_output",
            make_envelope(
                "council_output",
                {
                    "section": "consensus",
                    "content": consensus,
                    "metadata": {},
                },
                conversation_id,
                request_id,
            ),
        )

    perspectives_content = _format_perspectives_summary(
        normalized_output.get("perspectives_summary")
    )
    if perspectives_content:
        yield sse(
            "council_output",
            make_envelope(
                "council_output",
                {
                    "section": "contested",
                    "content": perspectives_content,
                    "metadata": {},
                },
                conversation_id,
                request_id,
            ),
        )

    findings_content, findings_count = _format_findings(normalized_output.get("findings"))
    if findings_content:
        yield sse(
            "council_output",
            make_envelope(
                "council_output",
                {
                    "section": "audit",
                    "content": findings_content,
                    "metadata": {"count": findings_count},
                },
                conversation_id,
                request_id,
            ),
        )

    raw_reasoning = metadata.get("raw_reasoning")
    if isinstance(raw_reasoning, str) and raw_reasoning.strip():
        yield sse(
            "council_output",
            make_envelope(
                "council_output",
                {
                    "section": "raw",
                    "content": raw_reasoning,
                    "metadata": {},
                },
                conversation_id,
                request_id,
            ),
        )

    # Extract token costs from session metadata
    token_costs = metadata.get("token_costs", {})
    if not isinstance(token_costs, dict):
        token_costs = {}
    total_tokens = token_costs.get("total_tokens", metadata.get("total_tokens", 0))
    total_cost_usd = token_costs.get(
        "total_cost_usd",
        metadata.get("total_cost_usd", metadata.get("total_cost", 0.0)),
    )
    models_used = token_costs.get("models_used", metadata.get("models_used", []))

    yield sse(
        "council_done",
        make_envelope(
            "council_done",
            {
                "session_id": session_id,
                "total_tokens": total_tokens,
                "total_cost_usd": total_cost_usd,
                "models_used": models_used,
                "per_model": token_costs.get("by_role", {}),
            },
            conversation_id,
            request_id,
        ),
    )


def make_envelope(
    event_type: str,
    data: dict[str, Any],
    conversation_id: str,
    request_id: str,
    evt_id: str | None = None,
) -> dict[str, Any]:
    if evt_id is None:
        evt_id = f"evt_{event_type}_{now_rfc3339().replace(':', '')}"
    return {
        "type": event_type,
        "id": evt_id,
        "ts": now_rfc3339(),
        "conversation_id": conversation_id,
        "request_id": request_id,
        "data": data,
    }


async def stream_council(
    user_message: str,
    conversation_id: str,
    request_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream council events as SSE."""
    if request_id is None:
        import uuid

        request_id = f"req_{uuid.uuid4().hex[:8]}"

    user_message = user_message.strip()
    if user_message.startswith("/council"):
        user_message = user_message[8:].strip()

    bypass_interview = "--default" in user_message
    if bypass_interview:
        user_message = user_message.replace("--default", "").strip()

    # Queue to hold progress events for yielding
    progress_queue: list[str] = []

    # Create progress callback to capture events
    async def progress_callback(event_data: dict[str, Any]) -> None:
        progress_queue.append(
            sse(
                "council_progress",
                make_envelope(
                    "council_progress",
                    event_data,
                    conversation_id,
                    request_id,
                ),
            )
        )

    # Run council with progress callback
    result_task = asyncio.create_task(
        handle_council_command(
            message=user_message,
            conversation_id=conversation_id,
            bypass_interview=bypass_interview,
            progress_callback=progress_callback,
        )
    )

    # Yield progress events as they arrive
    while not result_task.done():
        while progress_queue:
            yield progress_queue.pop(0)
        await asyncio.sleep(0.1)  # Small delay to prevent busy waiting

    # Get final result
    result = await result_task
    while progress_queue:
        yield progress_queue.pop(0)

    result_type = result.get("type")

    if result_type == "error":
        yield sse(
            "council_error",
            make_envelope(
                "council_error",
                {"error": result.get("content", "Unknown error")},
                conversation_id,
                request_id,
            ),
        )
        return

    if result_type == "interview":
        config = _config_to_dict(result.get("config"))
        roster = config.get("roster", {})
        if not isinstance(roster, dict):
            roster = {}
        presets = ["default", "adversarial", "lean"]
        rounds_options = [1, 2, 3]

        yield sse(
            "council_interview",
            make_envelope(
                "council_interview",
                {
                    "roster": _build_interview_roster(roster),
                    "presets": presets,
                    "rounds_options": rounds_options,
                    "audit_default": config.get("audit_enabled", False),
                },
                conversation_id,
                request_id,
            ),
        )
        return

    if result_type == "council_output":
        output = result.get("output", {})
        session_id = result.get("session_id", "unknown")
        async for frame in _emit_council_output_events(
            output=output,
            session_id=session_id,
            conversation_id=conversation_id,
            request_id=request_id,
        ):
            yield frame


async def stream_council_interview_response(
    user_message: str,
    conversation_id: str,
    request_id: str | None = None,
    stored_config: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Handle user's response to council interview config."""
    if request_id is None:
        import uuid

        request_id = f"req_{uuid.uuid4().hex[:8]}"

    raw_message = user_message.strip()
    user_message = raw_message.lower()

    if stored_config is None:
        stored_config = {}

    bypass = user_message in ("default", "go", "run", "start", "")
    if bypass:
        bypass_interview = True
    elif user_message.startswith("/council config:"):
        config_payload = raw_message.split(":", 1)[1].strip()
        if config_payload.startswith("{"):
            try:
                parsed = json.loads(config_payload)
                if isinstance(parsed, dict):
                    stored_config.update(parsed)
                bypass_interview = True
                user_message = "go"
            except json.JSONDecodeError:
                bypass_interview = False
        else:
            stored_config.update(_parse_config_kv(config_payload))
            bypass_interview = True
            user_message = "go"
    elif user_message in ("lean",):
        stored_config["preset_name"] = "lean"
        bypass_interview = True
    elif user_message in ("adversarial",):
        stored_config["preset_name"] = "adversarial"
        bypass_interview = True
    elif user_message in ("1", "1 round", "one round"):
        stored_config["round_count"] = 1
        bypass_interview = True
    elif user_message in ("2", "2 rounds", "two rounds"):
        stored_config["round_count"] = 2
        bypass_interview = True
    elif user_message in ("3", "3 rounds", "three rounds"):
        stored_config["round_count"] = 3
        bypass_interview = True
    elif user_message in ("audit", "with audit"):
        stored_config["audit_enabled"] = True
        bypass_interview = True
    elif user_message.startswith("{"):
        try:
            parsed = json.loads(user_message)
            if isinstance(parsed, dict):
                stored_config.update(parsed)
                bypass_interview = True
            else:
                bypass_interview = False
        except json.JSONDecodeError:
            bypass_interview = False
    else:
        bypass_interview = True

    if bypass_interview:
        config_source = dict(stored_config)
        prompt_override = config_source.pop("_prompt", None)

        try:
            config_obj = CouncilConfig(**config_source) if config_source else CouncilConfig()
        except Exception:
            config_obj = CouncilConfig()

        if "roster" not in config_source:
            config_obj.roster = load_roster(config_obj.preset_name)

        if isinstance(prompt_override, str) and prompt_override.strip():
            result = await run_council(
                prompt=prompt_override.strip(),
                conversation_id=conversation_id,
                config=config_obj,
            )
        else:
            result = await handle_council_interview_response(
                response=user_message,
                config=config_obj,
                conversation_id=conversation_id,
            )

        if result.get("type") == "council_output":
            output = result.get("output", {})
            session_id = result.get("session_id", "unknown")

            async for frame in _emit_council_output_events(
                output=output,
                session_id=session_id,
                conversation_id=conversation_id,
                request_id=request_id,
            ):
                yield frame
        elif result.get("type") == "error":
            yield sse(
                "council_error",
                make_envelope(
                    "council_error",
                    {"error": result.get("content", "Unknown error")},
                    conversation_id,
                    request_id,
                ),
            )
    else:
        yield sse(
            "council_error",
            make_envelope(
                "council_error",
                {"error": "Could not parse council config. Try 'default' or 'go'."},
                conversation_id,
                request_id,
            ),
        )
