from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Awaitable, Callable
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.skill_evaluator_prompts import (
    build_skill_creation_prompt,
    build_skill_refinement_prompt,
)
from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.skills_store import SkillDetail, get_skill
from orchestrator.tools.skill_manage import SkillManageTool

logger = logging.getLogger(__name__)

SKILL_EVALUATION_TOOL_THRESHOLD = 5
SKILL_EVALUATION_MATCH_THRESHOLD = 0.85
TURN_FETCH_LIMIT = 250
_DEFAULT_COMPLETION_TEMPERATURE = 0.1
_CREATION_MAX_TOKENS = 2400
_REFINEMENT_MAX_TOKENS = 1800
_MAX_TOOL_VALUE_CHARS = 500
_MAX_TOOL_TRACE_CHARS = 6000
PROTECTED_SOURCE_TYPES = frozenset({"system", "imported", "manual"})
_REQUIRED_SKILL_SECTIONS = (
    "Purpose",
    "When To Use",
    "Workflow",
    "Verification",
    "Guardrails",
)
_NORMALIZE_SKILL_NAME_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SkillEvaluationRequest:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    assistant_message_id: uuid.UUID
    tool_call_count: int


@dataclass(frozen=True)
class SkillTurnContext:
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    tool_trace: str
    conversation_summary: str | None = None


@dataclass(frozen=True)
class SkillDraft:
    name: str
    description: str
    trigger_conditions: str
    skill_markdown: str


@dataclass(frozen=True)
class SkillRefinementDecision:
    decision: str
    reason: str
    trigger_conditions: str
    old_text: str = ""
    new_text: str = ""


@dataclass(frozen=True)
class SkillEvaluationResult:
    debounce_key: str
    classification: str
    tool_call_count: int
    created_skill_id: str | None = None
    patched_skill_id: str | None = None
    matched_skill_id: str | None = None
    matched_similarity: float | None = None
    matched_source_type: str | None = None
    protected: bool = False
    trigger_conditions: str | None = None
    complexity_origin: int | None = None
    reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


CompletionCallable = Callable[..., Awaitable[Any]]
EmbeddingCallable = Callable[[str], Awaitable[list[float]]]


class ConversationStoreProtocol(Protocol):
    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    async def get_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None: ...


class SkillProjectionProtocol(Protocol):
    async def search_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    async def update_autonomous_metadata(
        self,
        skill_id: str,
        *,
        trigger_conditions: str | None = None,
        complexity_origin: int | None = None,
    ) -> bool: ...


class SkillManageProtocol(Protocol):
    async def execute(self, **kwargs: Any) -> str: ...


def build_skill_evaluation_debounce_key(
    conversation_id: str | uuid.UUID,
    assistant_message_id: str | uuid.UUID,
) -> str:
    return f"skill_eval:{conversation_id}:{assistant_message_id}"


def _normalize_model_for_provider(model_id: str, provider_name: str) -> str:
    normalized = model_id.strip()
    if not normalized:
        return normalized

    if provider_name == "openrouter":
        if normalized.startswith("openrouter/"):
            return normalized
        if normalized.startswith("opencode/"):
            return f"openrouter/{normalized[len('opencode/') :]}"
        return f"openrouter/{normalized}"

    for prefix in ("openrouter/", "opencode/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _extract_response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        if isinstance(choice0, dict):
            message = choice0.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
        else:
            message = getattr(choice0, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content

    for method_name in ("model_dump", "dict"):
        method = getattr(response, method_name, None)
        if not callable(method):
            continue
        try:
            data = method()
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content

    return ""


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    cleaned = raw_text.strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _stringify(value: Any, *, limit: int = _MAX_TOOL_VALUE_CHARS) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=True, sort_keys=True)
        except TypeError:
            text = str(value)
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _is_failed_tool_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("success") is False:
        return True
    error = result.get("error")
    return isinstance(error, str) and bool(error.strip())


def _summarize_tool_trace(assistant_message: dict[str, Any]) -> str:
    tool_calls = assistant_message.get("tool_calls")
    tool_results = assistant_message.get("tool_results")

    calls = tool_calls if isinstance(tool_calls, list) else []
    results = tool_results if isinstance(tool_results, list) else []
    lines: list[str] = []

    for index, call in enumerate(calls, start=1):
        if isinstance(call, dict):
            name = str(call.get("name") or f"tool_{index}")
            arguments = _stringify(call.get("arguments"))
        else:
            name = f"tool_{index}"
            arguments = _stringify(call)
        lines.append(f"{index}. {name} args={arguments}")

    if results:
        lines.append("Results:")

    for index, result_entry in enumerate(results, start=1):
        if isinstance(result_entry, dict):
            name = str(result_entry.get("name") or f"tool_{index}")
            result = _stringify(result_entry.get("result"))
        else:
            name = f"tool_{index}"
            result = _stringify(result_entry)
        lines.append(f"- {name}: {result}")

    trace = "\n".join(lines).strip()
    if len(trace) <= _MAX_TOOL_TRACE_CHARS:
        return trace or "None"
    return trace[: _MAX_TOOL_TRACE_CHARS - 3] + "..."


def _turn_has_failures(assistant_message: dict[str, Any]) -> bool:
    if str(assistant_message.get("status") or "").lower() != "complete":
        return True

    metadata = assistant_message.get("metadata")
    if isinstance(metadata, dict):
        finish_reason = str(metadata.get("finish_reason") or "").lower()
        if finish_reason in {"error", "cancelled"}:
            return True

    tool_results = assistant_message.get("tool_results")
    if not isinstance(tool_results, list):
        return False
    return any(
        _is_failed_tool_result(entry.get("result") if isinstance(entry, dict) else entry)
        for entry in tool_results
    )


def _ensure_skill_markdown(name: str, markdown: str) -> str:
    cleaned = markdown.strip()
    if cleaned.startswith("---\n"):
        end = cleaned.find("\n---\n", 4)
        if end != -1:
            cleaned = cleaned[end + 5 :].strip()
    if not cleaned.startswith("# "):
        cleaned = f"# {name.strip()}\n\n{cleaned}" if cleaned else f"# {name.strip()}"
    return cleaned


def _extract_section_body(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>[\s\S]*?)(?=^##\s+|\Z)",
        flags=re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    return match.group("body").strip()


def _has_required_skill_structure(markdown: str) -> bool:
    if not markdown.startswith("# "):
        return False

    section_positions: list[int] = []
    for heading in _REQUIRED_SKILL_SECTIONS:
        match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
        if match is None:
            return False
        if not _extract_section_body(markdown, heading):
            return False
        section_positions.append(match.start())

    return section_positions == sorted(section_positions)


def _normalize_skill_name(name: str) -> str:
    lowered = name.strip().lower()
    cleaned = _NORMALIZE_SKILL_NAME_PATTERN.sub(" ", lowered)
    return " ".join(cleaned.split())


def _skill_name_match_rank(draft_name: str, existing_name: str) -> int:
    normalized_draft = _normalize_skill_name(draft_name)
    normalized_existing = _normalize_skill_name(existing_name)
    if not normalized_draft or not normalized_existing:
        return 0
    if normalized_draft == normalized_existing:
        return 2

    if set(normalized_draft.split()) == set(normalized_existing.split()):
        return 1
    return 0


def _build_dedup_query_text(draft: SkillDraft) -> str:
    parts = [
        draft.name.strip(),
        draft.description.strip(),
        draft.trigger_conditions.strip(),
    ]
    return "\n".join(part for part in parts if part)


def _parse_skill_draft(payload: dict[str, Any]) -> SkillDraft | None:
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    skill_markdown = str(payload.get("skill_markdown") or "").strip()
    trigger_conditions = str(payload.get("trigger_conditions") or "").strip()

    if not name or not description or not skill_markdown:
        return None

    normalized_markdown = _ensure_skill_markdown(name, skill_markdown)
    if not _has_required_skill_structure(normalized_markdown):
        return None

    return SkillDraft(
        name=name,
        description=description,
        trigger_conditions=trigger_conditions or description,
        skill_markdown=normalized_markdown,
    )


def _parse_refinement_decision(
    payload: dict[str, Any],
) -> SkillRefinementDecision | None:
    decision = str(payload.get("decision") or "").strip().upper()
    reason = str(payload.get("reason") or "").strip()
    trigger_conditions = str(payload.get("trigger_conditions") or "").strip()
    old_text = str(payload.get("old_text") or "")
    new_text = str(payload.get("new_text") or "")

    if decision not in {"NO_CHANGE", "PATCH"}:
        return None
    if not reason:
        return None
    if decision == "PATCH" and not old_text:
        return None

    return SkillRefinementDecision(
        decision=decision,
        reason=reason,
        trigger_conditions=trigger_conditions,
        old_text=old_text,
        new_text=new_text,
    )


class SkillEvaluator:
    def __init__(
        self,
        store: ConversationStoreProtocol,
        db_pool: Any,
        *,
        projection_store: SkillProjectionProtocol | None = None,
        skill_manage_tool: SkillManageProtocol | None = None,
        completion_callable: CompletionCallable = litellm.acompletion,
        query_embedder: EmbeddingCallable = embed_query,
    ) -> None:
        self._store: ConversationStoreProtocol = store
        self._projection_store: SkillProjectionProtocol | None = projection_store or (
            SkillProjectionStore(db_pool) if db_pool is not None else None
        )
        self._skill_manage_tool: SkillManageProtocol = skill_manage_tool or SkillManageTool(
            db_pool=db_pool
        )
        self._completion_callable: CompletionCallable = completion_callable
        self._query_embedder: EmbeddingCallable = query_embedder

    async def evaluate_completed_turn(
        self,
        request: SkillEvaluationRequest,
    ) -> SkillEvaluationResult:
        debounce_key = build_skill_evaluation_debounce_key(
            request.conversation_id,
            request.assistant_message_id,
        )

        if request.tool_call_count < SKILL_EVALUATION_TOOL_THRESHOLD:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_below_threshold",
                tool_call_count=request.tool_call_count,
                complexity_origin=request.tool_call_count,
                reason="tool_call_count below autonomous skill threshold",
            )

        if self._projection_store is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_projection_unavailable",
                tool_call_count=request.tool_call_count,
                complexity_origin=request.tool_call_count,
                reason="skill projection store unavailable",
            )

        turn = await self._load_turn_context(request)
        if turn is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_missing_turn",
                tool_call_count=request.tool_call_count,
                complexity_origin=request.tool_call_count,
                reason="assistant turn or preceding user turn not found",
            )

        if _turn_has_failures(turn.assistant_message):
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_failed_turn",
                tool_call_count=request.tool_call_count,
                complexity_origin=request.tool_call_count,
                reason="completed turn includes failure signals",
            )

        draft = await self._generate_skill_draft(turn, request.tool_call_count)
        if draft is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_invalid_draft",
                tool_call_count=request.tool_call_count,
                complexity_origin=request.tool_call_count,
                reason="creation prompt did not return a valid reusable skill draft",
            )

        best_match = await self._find_best_match(draft)
        if best_match is None:
            return await self._create_skill(request, draft, debounce_key)

        return await self._refine_or_skip_match(
            request=request,
            debounce_key=debounce_key,
            turn=turn,
            draft=draft,
            match=best_match,
        )

    async def _load_turn_context(
        self,
        request: SkillEvaluationRequest,
    ) -> SkillTurnContext | None:
        messages = await self._store.get_messages(
            request.conversation_id,
            limit=TURN_FETCH_LIMIT,
        )

        assistant_index = next(
            (
                index
                for index, message in enumerate(messages)
                if str(message.get("id") or "") == str(request.assistant_message_id)
            ),
            None,
        )
        if assistant_index is None:
            return None

        assistant_message = messages[assistant_index]
        if str(assistant_message.get("role") or "").lower() != "assistant":
            return None

        user_message = next(
            (
                message
                for message in reversed(messages[:assistant_index])
                if str(message.get("role") or "").lower() == "user"
            ),
            None,
        )
        if user_message is None:
            return None

        conversation = await self._store.get_conversation(request.conversation_id)
        conversation_summary = None
        if isinstance(conversation, dict):
            summary_value = conversation.get("summary")
            if isinstance(summary_value, str) and summary_value.strip():
                conversation_summary = summary_value.strip()

        return SkillTurnContext(
            user_message=user_message,
            assistant_message=assistant_message,
            tool_trace=_summarize_tool_trace(assistant_message),
            conversation_summary=conversation_summary,
        )

    async def _generate_skill_draft(
        self,
        turn: SkillTurnContext,
        tool_call_count: int,
    ) -> SkillDraft | None:
        prompt = build_skill_creation_prompt(
            user_request=str(turn.user_message.get("content") or "").strip(),
            assistant_response=str(turn.assistant_message.get("content") or "").strip(),
            tool_trace=turn.tool_trace,
            tool_call_count=tool_call_count,
            conversation_summary=turn.conversation_summary,
        )
        payload = await self._run_json_prompt(
            prompt=prompt,
            system_message=(
                "You extract portable procedural skills from successful complex turns. "
                "Output valid JSON only."
            ),
            max_tokens=_CREATION_MAX_TOKENS,
        )
        if payload is None:
            return None
        return _parse_skill_draft(payload)

    async def _find_best_match(self, draft: SkillDraft) -> dict[str, Any] | None:
        projection_store = self._projection_store
        if projection_store is None:
            return None
        query_embedding = await self._query_embedder(_build_dedup_query_text(draft))
        matches = await projection_store.search_by_embedding(
            query_embedding,
            limit=5,
            min_similarity=SKILL_EVALUATION_MATCH_THRESHOLD,
        )
        if not matches:
            return None

        named_matches = [
            match
            for match in matches
            if _skill_name_match_rank(
                draft.name,
                str(match.get("name") or match.get("skill_id") or ""),
            )
            > 0
        ]
        if not named_matches:
            return None

        return max(
            named_matches,
            key=lambda match: (
                _skill_name_match_rank(
                    draft.name,
                    str(match.get("name") or match.get("skill_id") or ""),
                ),
                float(match.get("similarity") or 0.0),
            ),
        )

    async def _create_skill(
        self,
        request: SkillEvaluationRequest,
        draft: SkillDraft,
        debounce_key: str,
    ) -> SkillEvaluationResult:
        projection_store = self._projection_store
        if projection_store is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_projection_unavailable",
                tool_call_count=request.tool_call_count,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="skill projection store unavailable",
            )

        response = await self._skill_manage_tool.execute(
            action="create",
            name=draft.name,
            description=draft.description,
            content=draft.skill_markdown,
            source_type="autonomous",
            caller_autonomous=True,
        )
        payload = _extract_json_object(response)
        if payload is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_create_conflict",
                tool_call_count=request.tool_call_count,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="skill_manage returned a non-JSON create response",
            )

        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_create_conflict",
                tool_call_count=request.tool_call_count,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason=error,
            )

        created_skill_id = payload.get("skill_id")
        if not isinstance(created_skill_id, str) or not created_skill_id:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_create_conflict",
                tool_call_count=request.tool_call_count,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="skill_manage create response missing skill_id",
            )

        _ = await projection_store.update_autonomous_metadata(
            created_skill_id,
            trigger_conditions=draft.trigger_conditions,
            complexity_origin=request.tool_call_count,
        )
        return SkillEvaluationResult(
            debounce_key=debounce_key,
            classification="created",
            tool_call_count=request.tool_call_count,
            created_skill_id=created_skill_id,
            trigger_conditions=draft.trigger_conditions,
            complexity_origin=request.tool_call_count,
        )

    async def _refine_or_skip_match(
        self,
        *,
        request: SkillEvaluationRequest,
        debounce_key: str,
        turn: SkillTurnContext,
        draft: SkillDraft,
        match: dict[str, Any],
    ) -> SkillEvaluationResult:
        matched_skill_id = str(match.get("skill_id") or "")
        matched_source_type = str(match.get("source_type") or "") or None
        matched_similarity = float(match.get("similarity") or 0.0)
        allow_autonomous_edit = bool(match.get("allow_autonomous_edit"))

        if matched_source_type in PROTECTED_SOURCE_TYPES and not allow_autonomous_edit:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_protected_match",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                protected=True,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason=(
                    f"matched protected {matched_source_type} skill above similarity threshold"
                ),
            )

        try:
            existing_skill = get_skill(matched_skill_id)
        except FileNotFoundError:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_missing_turn",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="projection match exists but markdown skill file is missing",
            )

        refinement = await self._generate_refinement(
            turn=turn,
            draft=draft,
            existing_skill=existing_skill,
        )
        if refinement is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_no_change",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                trigger_conditions=draft.trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="refinement prompt did not return a valid decision",
            )

        trigger_conditions = refinement.trigger_conditions.strip() or draft.trigger_conditions

        if refinement.decision == "NO_CHANGE":
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_no_change",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                trigger_conditions=trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason=refinement.reason,
            )

        response = await self._skill_manage_tool.execute(
            action="patch",
            skill_id=matched_skill_id,
            old_text=refinement.old_text,
            new_text=refinement.new_text,
            caller_autonomous=True,
        )
        payload = _extract_json_object(response)
        if payload is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_patch_conflict",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                trigger_conditions=trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="skill_manage returned a non-JSON patch response",
            )

        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            classification = "skipped_patch_conflict"
            protected = "protected" in error.lower()
            if protected:
                classification = "skipped_protected_match"
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification=classification,
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                protected=protected,
                trigger_conditions=trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason=error,
            )

        projection_store = self._projection_store
        if projection_store is None:
            return SkillEvaluationResult(
                debounce_key=debounce_key,
                classification="skipped_projection_unavailable",
                tool_call_count=request.tool_call_count,
                matched_skill_id=matched_skill_id or None,
                matched_similarity=matched_similarity,
                matched_source_type=matched_source_type,
                trigger_conditions=trigger_conditions,
                complexity_origin=request.tool_call_count,
                reason="skill projection store unavailable",
            )

        existing_complexity = match.get("complexity_origin")
        if isinstance(existing_complexity, bool):
            existing_complexity = None
        next_complexity = max(
            int(existing_complexity or 0),
            request.tool_call_count,
        )
        _ = await projection_store.update_autonomous_metadata(
            matched_skill_id,
            trigger_conditions=trigger_conditions,
            complexity_origin=next_complexity,
        )
        return SkillEvaluationResult(
            debounce_key=debounce_key,
            classification="patched",
            tool_call_count=request.tool_call_count,
            patched_skill_id=matched_skill_id or None,
            matched_skill_id=matched_skill_id or None,
            matched_similarity=matched_similarity,
            matched_source_type=matched_source_type,
            trigger_conditions=trigger_conditions,
            complexity_origin=next_complexity,
            reason=refinement.reason,
        )

    async def _generate_refinement(
        self,
        *,
        turn: SkillTurnContext,
        draft: SkillDraft,
        existing_skill: SkillDetail,
    ) -> SkillRefinementDecision | None:
        prompt = build_skill_refinement_prompt(
            user_request=str(turn.user_message.get("content") or "").strip(),
            assistant_response=str(turn.assistant_message.get("content") or "").strip(),
            tool_trace=turn.tool_trace,
            existing_skill_name=str(existing_skill.get("name") or "").strip(),
            existing_skill_description=str(existing_skill.get("description") or "").strip(),
            existing_skill_markdown=str(existing_skill.get("content") or "").strip(),
            candidate_name=draft.name,
            candidate_description=draft.description,
            candidate_trigger_conditions=draft.trigger_conditions,
            candidate_skill_markdown=draft.skill_markdown,
        )
        payload = await self._run_json_prompt(
            prompt=prompt,
            system_message=(
                "You decide whether an existing autonomous skill needs a minimal patch. "
                "Output valid JSON only."
            ),
            max_tokens=_REFINEMENT_MAX_TOKENS,
        )
        if payload is None:
            return None
        return _parse_refinement_decision(payload)

    async def _run_json_prompt(
        self,
        *,
        prompt: str,
        system_message: str,
        max_tokens: int,
    ) -> dict[str, Any] | None:
        settings = get_settings()
        provider_config = settings.get_provider_config("openrouter")
        model = _normalize_model_for_provider(
            settings.auto_fast_model,
            provider_config.name or "openrouter",
        )

        call_params: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            "temperature": _DEFAULT_COMPLETION_TEMPERATURE,
            "max_tokens": max_tokens,
            "timeout": provider_config.timeout_s,
            "response_format": {"type": "json_object"},
        }
        if provider_config.base_url:
            call_params["api_base"] = provider_config.base_url
        if provider_config.api_key:
            call_params["api_key"] = provider_config.api_key
        if provider_config.extra_headers:
            call_params["extra_headers"] = provider_config.extra_headers

        try:
            response = await self._completion_callable(**call_params)
        except Exception as exc:
            logger.warning("Skill evaluator prompt failed: %s", exc)
            return None

        return _extract_json_object(_extract_response_content(response))
