from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

from orchestrator.advisor_budget import check_advisor_budget, increment_advisor_budget
from orchestrator.config import ProviderConfig, Settings, get_settings
from orchestrator.prompts_advisor import get_advisor_prompt
from orchestrator.tools.builtin import create_advisor_registry
from orchestrator.tools.completion import completion_with_tools
from orchestrator.tools.executor import ExecutionContext
from orchestrator.tools.registry import Tool

if TYPE_CHECKING:
    from orchestrator.memory.store import MemoryStore


MAX_ADVISOR_QUESTION_CHARS = 4000
MAX_ADVISOR_CONTEXT_SUMMARY_CHARS = 6000
ADVISOR_ALLOWLIST_SCOPE = "advisor_minimal"


class AdvisorSlotLike(Protocol):
    model: str
    system_prompt_override: str | None
    timeout_s: float | None


class AdvisorRosterLike(Protocol):
    def resolve(self, domain: str, difficulty: str) -> AdvisorSlotLike: ...


class AdvisorSettingsLike(Protocol):
    default_provider: str
    advisor_budget_per_conversation: int

    def get_advisor_roster_config(self) -> AdvisorRosterLike: ...

    def resolve_advisor_model(self, domain: str, difficulty: str) -> str: ...

    def get_provider_config(self, provider_name: str | None = None) -> ProviderConfig: ...


def _truncate_text(value: str, limit: int) -> str:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}\n...[truncated]"


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _build_advisor_user_message(question: str, context_summary: str | None) -> str:
    bounded_question = _truncate_text(question, MAX_ADVISOR_QUESTION_CHARS)
    bounded_context = (
        _truncate_text(context_summary, MAX_ADVISOR_CONTEXT_SUMMARY_CHARS)
        if isinstance(context_summary, str) and context_summary.strip()
        else None
    )

    parts = [
        "The Daemon executor needs focused advisor guidance.",
        "Respond with ONLY valid JSON using this shape:",
        '{"answer":"string","sufficient":true,"escalate":false,"spawn_recommended":null}',
        "Rules:",
        "- Keep `answer` concise, strategic, and actionable.",
        "- Use `sufficient=true` when the executor can proceed with the current approach.",
        "- Use `escalate=true` only when a higher-difficulty advisor or broader escalation is warranted.",
        "- Set `spawn_recommended` to null unless a spawned specialist is clearly the better path.",
        "",
        "Question:",
        bounded_question,
    ]

    if bounded_context:
        parts.extend(["", "Relevant context summary:", bounded_context])

    return "\n".join(parts)


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_advisor_payload(raw_text: str) -> dict[str, Any]:
    candidate = _strip_json_fence(raw_text)
    if not candidate:
        return {}

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    start_index = candidate.find("{")
    while start_index >= 0:
        try:
            parsed, _ = decoder.raw_decode(candidate[start_index:])
        except json.JSONDecodeError:
            start_index = candidate.find("{", start_index + 1)
            continue
        if isinstance(parsed, dict):
            return parsed
        start_index = candidate.find("{", start_index + 1)

    return {}


def _base_result(
    *,
    advisor_id: str,
    answer: str,
    sufficient: bool = False,
    escalate: bool = False,
    spawn_recommended: Any | None = None,
    error: str | None = None,
    status: str | None = None,
    domain: str | None = None,
    difficulty: str | None = None,
    model: str | None = None,
    budget: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "advisor_id": advisor_id,
        "answer": answer,
        "sufficient": sufficient,
        "escalate": escalate,
    }
    if spawn_recommended is not None:
        payload["spawn_recommended"] = spawn_recommended
    if error:
        payload["error"] = error
    if status:
        payload["status"] = status
    if domain:
        payload["domain"] = domain
    if difficulty:
        payload["difficulty"] = difficulty
    if model:
        payload["model"] = model
    if isinstance(budget, dict) and budget:
        payload["budget"] = budget
    return json.dumps(payload)


def _conversation_uuid_from_context(runtime_context: ExecutionContext) -> uuid.UUID | None:
    raw_value = runtime_context.budget_state.get("conversation_uuid")
    if isinstance(raw_value, str) and raw_value:
        try:
            return uuid.UUID(raw_value)
        except ValueError:
            return None
    return None


def _build_nested_advisor_context(
    runtime_context: ExecutionContext,
    *,
    advisor_id: str,
    domain: str,
    difficulty: str,
    model: str,
) -> ExecutionContext:
    parent_trace_key = runtime_context.trace_key or f"advisor-parent:{advisor_id}"
    event_tags = dict(runtime_context.event_tags)
    event_tags.update(
        {
            "domain": domain,
            "difficulty": difficulty,
            "model": model,
        }
    )

    gating_context = dict(runtime_context.gating_context)
    gating_context.update(
        {
            "advisor_depth": 1,
            "surface": gating_context.get("surface") or "chat",
        }
    )

    registry_context = dict(runtime_context.registry_context)
    registry_context.update(
        {
            "registry_scope": "advisor",
            "allowlist_scope": ADVISOR_ALLOWLIST_SCOPE,
            "advisor_depth": 1,
        }
    )

    return ExecutionContext(
        request_id=runtime_context.request_id,
        conversation_id=runtime_context.conversation_id,
        trace_key=f"{parent_trace_key}:{advisor_id}",
        parent_trace_key=parent_trace_key,
        advisor_id=advisor_id,
        event_scope="advisor",
        text_event_type="advisor_text_delta",
        budget_state=dict(runtime_context.budget_state),
        gating_context=gating_context,
        registry_context=registry_context,
        event_tags=event_tags,
    )


def _advisor_event(
    event_type: str,
    advisor_context: ExecutionContext,
    **payload: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type,
        "advisor_id": advisor_context.advisor_id,
        "trace_key": advisor_context.trace_key,
        "parent_trace_key": advisor_context.parent_trace_key,
        "event_scope": advisor_context.event_scope,
        "event_tags": dict(advisor_context.event_tags),
    }
    if advisor_context.request_id:
        event["request_id"] = advisor_context.request_id
    if advisor_context.conversation_id:
        event["conversation_id"] = advisor_context.conversation_id
    event.update(payload)
    return event


def _provider_config_with_timeout(
    provider_config: ProviderConfig,
    timeout_s: float | None,
) -> ProviderConfig:
    if timeout_s is None:
        return provider_config
    return ProviderConfig(**provider_config.model_dump(), timeout_s=timeout_s)


class ConsultAdvisorTool(Tool):
    name = "consult_advisor"
    description = (
        "Consult an expert advisor for domain-specific guidance. "
        "Select the domain and difficulty level appropriate to your question. "
        "Provide a clear, specific question and optionally include a summary of "
        "relevant context from the current conversation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Expertise domain for the advisor",
                "enum": ["coding", "graphics", "reasoning", "research", "general"],
            },
            "difficulty": {
                "type": "string",
                "description": "Complexity level of the question",
                "enum": ["low", "mid", "high"],
            },
            "question": {
                "type": "string",
                "description": "The specific question to ask the advisor",
            },
            "context_summary": {
                "type": "string",
                "description": "Optional summary of relevant conversation context to provide the advisor",
            },
        },
        "required": ["domain", "difficulty", "question"],
    }

    def __init__(
        self,
        *,
        settings: AdvisorSettingsLike | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self._settings = settings
        self._memory_store = memory_store

    async def execute(self, **kwargs: Any) -> str:
        domain = str(kwargs.get("domain", "")).strip().lower()
        difficulty = str(kwargs.get("difficulty", "")).strip().lower()
        question = str(kwargs.get("question", "")).strip()
        context_summary = kwargs.get("context_summary")
        runtime_context = kwargs.get("_runtime_context")

        advisor_id = f"advisor_{domain or 'general'}_{uuid.uuid4().hex[:12]}"

        if not question:
            return _base_result(
                advisor_id=advisor_id,
                answer="Advisor question is required.",
                error="Advisor question is required.",
                status="invalid_request",
                domain=domain or None,
                difficulty=difficulty or None,
            )

        if not isinstance(runtime_context, ExecutionContext):
            return _base_result(
                advisor_id=advisor_id,
                answer="consult_advisor requires runtime execution context from the native chat path.",
                error="Missing runtime execution context.",
                status="unsupported_surface",
                domain=domain,
                difficulty=difficulty,
            )

        if runtime_context.event_scope == "advisor":
            return _base_result(
                advisor_id=advisor_id,
                answer="Advisor depth cap reached: advisors cannot call consult_advisor recursively.",
                error="Advisor depth cap reached.",
                status="depth_cap",
                domain=domain,
                difficulty=difficulty,
            )

        surface = runtime_context.gating_context.get("surface")
        if surface and surface != "chat":
            return _base_result(
                advisor_id=advisor_id,
                answer="consult_advisor is only supported on the native chat execution surface.",
                error="Unsupported execution surface.",
                status="unsupported_surface",
                domain=domain,
                difficulty=difficulty,
            )

        if runtime_context.gating_context.get("advisor_eligible") is False:
            return _base_result(
                advisor_id=advisor_id,
                answer="Advisor consultation is not enabled for this request.",
                error="Advisor consultation is not enabled for this request.",
                status="advisor_disabled",
                domain=domain,
                difficulty=difficulty,
            )

        settings: AdvisorSettingsLike = self._settings or get_settings()

        try:
            advisor_slot = settings.get_advisor_roster_config().resolve(domain, difficulty)
            advisor_model = settings.resolve_advisor_model(domain, difficulty)
        except Exception as exc:
            return _base_result(
                advisor_id=advisor_id,
                answer=f"Advisor configuration error: {exc}",
                error=str(exc),
                status="config_error",
                domain=domain,
                difficulty=difficulty,
            )

        conversation_uuid = _conversation_uuid_from_context(runtime_context)
        if self._memory_store is None or conversation_uuid is None:
            return _base_result(
                advisor_id=advisor_id,
                answer="Advisor budget context is unavailable outside the native chat runtime.",
                error="Missing advisor budget context.",
                status="missing_budget_context",
                domain=domain,
                difficulty=difficulty,
                model=advisor_model,
            )

        memory_store = cast("MemoryStore", self._memory_store)

        budget_check = await check_advisor_budget(conversation_uuid, memory_store)
        if not budget_check.allowed:
            return _base_result(
                advisor_id=advisor_id,
                answer=budget_check.message,
                error=budget_check.message,
                status="budget_exhausted",
                domain=domain,
                difficulty=difficulty,
                model=advisor_model,
                budget={
                    "current_count": budget_check.current_count,
                    "limit": budget_check.budget,
                },
            )

        provider_name_value = runtime_context.gating_context.get("provider")
        provider_name = (
            str(provider_name_value)
            if isinstance(provider_name_value, str) and provider_name_value
            else None
        )
        provider_config = _provider_config_with_timeout(
            settings.get_provider_config(provider_name),
            advisor_slot.timeout_s,
        )

        advisor_context = _build_nested_advisor_context(
            runtime_context,
            advisor_id=advisor_id,
            domain=domain,
            difficulty=difficulty,
            model=advisor_model,
        )
        emit_event = runtime_context.emit_event

        if emit_event is not None:
            await emit_event(
                _advisor_event(
                    "advisor_start",
                    advisor_context,
                    domain=domain,
                    difficulty=difficulty,
                    model=advisor_model,
                    tool_call_id=runtime_context.tool_call_id,
                )
            )

        advisor_messages = [
            {
                "role": "system",
                "content": advisor_slot.system_prompt_override or get_advisor_prompt(domain),
            },
            {
                "role": "user",
                "content": _build_advisor_user_message(question, context_summary),
            },
        ]

        advisor_registry = create_advisor_registry()
        advisor_text_parts: list[str] = []
        final_advisor_text: str | None = None
        error_message: str | None = None
        budget_count = budget_check.current_count
        budget_incremented = False
        started_at = time.perf_counter()

        async def handle_event(event: dict[str, Any]) -> None:
            nonlocal final_advisor_text, error_message, budget_count, budget_incremented

            event_type = str(event.get("type") or "")
            if not budget_incremented and event_type and event_type != "error":
                budget_count = await increment_advisor_budget(
                    conversation_uuid,
                    memory_store,
                )
                budget_incremented = True

            if emit_event is not None:
                await emit_event(event)

            if event_type == "advisor_text_delta":
                content = event.get("content")
                if isinstance(content, str) and content:
                    advisor_text_parts.append(content)
            elif event_type == "advisor_text_done":
                content = event.get("content")
                if isinstance(content, str) and content:
                    final_advisor_text = content
            elif event_type == "error":
                error_value = event.get("error")
                if isinstance(error_value, str) and error_value:
                    error_message = error_value

        try:
            completion_settings = cast(Settings, settings)
            advisor_stream = completion_with_tools(
                settings=completion_settings,
                provider_config=provider_config,
                messages=advisor_messages,
                registry=advisor_registry,
                actual_model=advisor_model,
                max_tool_rounds=4,
                execution_context=advisor_context,
            )
            stream_iter = advisor_stream.__aiter__()

            try:
                first_event = await anext(stream_iter)
            except StopAsyncIteration:
                first_event = None

            if isinstance(first_event, dict):
                await handle_event(first_event)

            async for event in stream_iter:
                await handle_event(event)
        except Exception as exc:
            error_message = f"Advisor execution failed: {exc}"
            if emit_event is not None:
                await emit_event(
                    _advisor_event(
                        "error",
                        advisor_context,
                        error=error_message,
                    )
                )

        raw_response_text = final_advisor_text or "".join(advisor_text_parts)
        parsed_payload = _parse_advisor_payload(raw_response_text)
        parsed_answer = parsed_payload.get("answer")
        answer = (
            str(parsed_answer).strip()
            if parsed_answer is not None and str(parsed_answer).strip()
            else raw_response_text.strip()
        )

        if not answer and error_message:
            answer = error_message
        if not answer:
            answer = "Advisor returned no answer."
            if error_message is None:
                error_message = answer

        sufficient = _bool_value(parsed_payload.get("sufficient"))
        escalate = _bool_value(parsed_payload.get("escalate"))
        spawn_recommended = parsed_payload.get("spawn_recommended")
        if spawn_recommended in (False, None, "", []):
            spawn_recommended = None

        usage_snapshot = advisor_context.usage_state.snapshot()
        latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        if emit_event is not None:
            await emit_event(
                _advisor_event(
                    "advisor_end",
                    advisor_context,
                    domain=domain,
                    difficulty=difficulty,
                    model=advisor_model,
                    answer=answer,
                    sufficient=sufficient,
                    escalate=escalate,
                    spawn_recommended=spawn_recommended,
                    status="error" if error_message else "completed",
                    error=error_message,
                    usage=usage_snapshot,
                    tool_call_id=runtime_context.tool_call_id,
                    latency_ms=latency_ms,
                    tokens_in=usage_snapshot.get("prompt_tokens", 0),
                    tokens_out=usage_snapshot.get("completion_tokens", 0),
                    budget={
                        "current_count": budget_count,
                        "limit": budget_check.budget,
                    },
                )
            )

        return _base_result(
            advisor_id=advisor_id,
            answer=answer,
            sufficient=sufficient,
            escalate=escalate,
            spawn_recommended=spawn_recommended,
            error=error_message,
            status="error" if error_message else "completed",
            domain=domain,
            difficulty=difficulty,
            model=advisor_model,
            budget={
                "current_count": budget_count,
                "limit": budget_check.budget,
            },
        )
