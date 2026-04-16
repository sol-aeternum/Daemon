from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false

import json
import uuid
from typing import Any, final
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.skill_evaluator import (
    SkillDraft,
    SkillEvaluationRequest,
    SkillEvaluator,
    SkillRefinementDecision,
    _build_dedup_query_text,
)


@final
class StubStore:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.get_messages_mock = AsyncMock(return_value=messages)
        self.get_conversation_mock = AsyncMock(
            return_value={"summary": "Conversation summary"}
        )

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        del conversation_id, limit, offset
        return await self.get_messages_mock()

    async def get_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        del conversation_id
        return await self.get_conversation_mock()


@final
class StubProjectionStore:
    def __init__(self) -> None:
        self.search_by_embedding = AsyncMock(return_value=[])
        self.update_autonomous_metadata = AsyncMock(return_value=True)


@final
class StubSkillManageTool:
    def __init__(self, response: str = "{}") -> None:
        self.execute = AsyncMock(return_value=response)


def _build_messages(
    assistant_message_id: uuid.UUID,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    status: str = "complete",
) -> list[dict[str, Any]]:
    return [
        {
            "id": uuid.uuid4(),
            "role": "user",
            "content": "Please turn this debugging workflow into something reusable.",
            "status": "complete",
        },
        {
            "id": assistant_message_id,
            "role": "assistant",
            "content": "I traced the issue, fixed it, and verified the repair.",
            "status": status,
            "metadata": {"finish_reason": "stop"},
            "tool_calls": [
                {"name": "read", "arguments": {"file": "a.py"}},
                {"name": "grep", "arguments": {"pattern": "bug"}},
            ],
            "tool_results": tool_results or [],
        },
    ]


@pytest.mark.asyncio
async def test_evaluator_skips_below_threshold() -> None:
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        tool_call_count=4,
    )
    skill_manage_tool = StubSkillManageTool()
    evaluator = SkillEvaluator(
        store=StubStore([]),
        db_pool=None,
        projection_store=None,
        skill_manage_tool=skill_manage_tool,
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_below_threshold"
    assert result.debounce_key == (
        f"skill_eval:{request.conversation_id}:{request.assistant_message_id}"
    )


@pytest.mark.asyncio
async def test_evaluator_creates_novel_autonomous_skill() -> None:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=7,
    )
    projection_store = StubProjectionStore()
    projection_store.search_by_embedding.return_value = []
    projection_store.update_autonomous_metadata.return_value = True
    skill_manage_tool = StubSkillManageTool(
        response=json.dumps(
            {
                "skill_id": "debug-workflow",
                "name": "Debug Workflow",
                "description": "A reusable debugging skill.",
                "source_type": "autonomous",
                "created": True,
            }
        )
    )

    evaluator = SkillEvaluator(
        store=StubStore(_build_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=AsyncMock(return_value=[0.1, 0.2]),
    )
    evaluator._generate_skill_draft = AsyncMock(
        return_value=SkillDraft(
            name="Debug Workflow",
            description="Turn a successful debugging turn into a reusable flow.",
            trigger_conditions="Use when a bug requires repo tracing and validation.",
            skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
        )
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    skill_manage_tool.execute.assert_awaited_once_with(
        action="create",
        name="Debug Workflow",
        description="Turn a successful debugging turn into a reusable flow.",
        content="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
        source_type="autonomous",
        caller_autonomous=True,
    )
    projection_store.update_autonomous_metadata.assert_awaited_once_with(
        "debug-workflow",
        trigger_conditions="Use when a bug requires repo tracing and validation.",
        complexity_origin=7,
    )


@pytest.mark.asyncio
async def test_evaluator_skips_protected_overlap_without_duplicate_creation() -> None:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=8,
    )
    projection_store = StubProjectionStore()
    projection_store.search_by_embedding.return_value = [
        {
            "skill_id": "system-debug",
            "name": "Debug Workflow",
            "source_type": "system",
            "allow_autonomous_edit": False,
            "similarity": 0.91,
            "complexity_origin": 6,
        }
    ]
    skill_manage_tool = StubSkillManageTool()

    evaluator = SkillEvaluator(
        store=StubStore(_build_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=AsyncMock(return_value=[0.2, 0.3]),
    )
    evaluator._generate_skill_draft = AsyncMock(
        return_value=SkillDraft(
            name="Debug Workflow",
            description="Overlaps with the system debugging skill.",
            trigger_conditions="Use for debugging system failures.",
            skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
        )
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_protected_match"
    assert result.matched_skill_id == "system-debug"
    assert result.protected is True
    skill_manage_tool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluator_patches_overlapping_autonomous_skill() -> None:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=8,
    )
    projection_store = StubProjectionStore()
    projection_store.search_by_embedding.return_value = [
        {
            "skill_id": "debug-workflow",
            "name": "debug workflow",
            "source_type": "autonomous",
            "allow_autonomous_edit": False,
            "similarity": 0.92,
            "complexity_origin": 6,
        }
    ]
    projection_store.update_autonomous_metadata.return_value = True
    skill_manage_tool = StubSkillManageTool(
        response=json.dumps({"skill_id": "debug-workflow", "patched": True})
    )

    evaluator = SkillEvaluator(
        store=StubStore(_build_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=AsyncMock(return_value=[0.2, 0.3]),
    )
    evaluator._generate_skill_draft = AsyncMock(
        return_value=SkillDraft(
            name="Debug Workflow!!!",
            description="Improve the debugging workflow skill.",
            trigger_conditions="Use when a repo bug needs tracing and verification.",
            skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
        )
    )
    evaluator._generate_refinement = AsyncMock(
        return_value=SkillRefinementDecision(
            decision="PATCH",
            reason="Add a missing validation step.",
            trigger_conditions="Use when a repo bug needs tracing and post-fix verification.",
            old_text="## Purpose\n\nOld section",
            new_text="## Purpose\n\nUpdated section",
        )
    )

    with patch(
        "orchestrator.skill_evaluator.get_skill",
        return_value={
            "id": "debug-workflow",
            "name": "Debug Workflow",
            "description": "A reusable debugging skill.",
            "content": "# Debug Workflow\n\n## Purpose\n\nOld section",
        },
    ):
        result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "patched"
    assert result.patched_skill_id == "debug-workflow"
    skill_manage_tool.execute.assert_awaited_once_with(
        action="patch",
        skill_id="debug-workflow",
        old_text="## Purpose\n\nOld section",
        new_text="## Purpose\n\nUpdated section",
        caller_autonomous=True,
    )
    projection_store.update_autonomous_metadata.assert_awaited_once_with(
        "debug-workflow",
        trigger_conditions="Use when a repo bug needs tracing and post-fix verification.",
        complexity_origin=8,
    )


@pytest.mark.asyncio
async def test_evaluator_creates_new_skill_when_semantic_match_has_different_name() -> (
    None
):
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=7,
    )
    projection_store = StubProjectionStore()
    projection_store.search_by_embedding.return_value = [
        {
            "skill_id": "worker-repair",
            "name": "Worker Repair",
            "source_type": "autonomous",
            "allow_autonomous_edit": False,
            "similarity": 0.97,
            "complexity_origin": 6,
        }
    ]
    skill_manage_tool = StubSkillManageTool(
        response=json.dumps(
            {
                "skill_id": "debug-workflow",
                "name": "Debug Workflow",
                "description": "A reusable debugging skill.",
                "source_type": "autonomous",
                "created": True,
            }
        )
    )

    query_embedder = AsyncMock(return_value=[0.3, 0.4])
    evaluator = SkillEvaluator(
        store=StubStore(_build_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=query_embedder,
    )
    evaluator._generate_skill_draft = AsyncMock(
        return_value=SkillDraft(
            name="Debug Workflow",
            description="Investigate repo bugs using a repeatable workflow.",
            trigger_conditions="Use when a bug requires repo tracing and verification.",
            skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
        )
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    skill_manage_tool.execute.assert_awaited_once()
    query_embedder.assert_awaited_once_with(
        "Debug Workflow\nInvestigate repo bugs using a repeatable workflow.\nUse when a bug requires repo tracing and verification."
    )


def test_build_dedup_query_text_includes_name_and_trigger_conditions() -> None:
    draft = SkillDraft(
        name="Debug Workflow",
        description="Investigate repo bugs using a repeatable workflow.",
        trigger_conditions="Use when a bug requires repo tracing and verification.",
        skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
    )

    assert _build_dedup_query_text(draft) == (
        "Debug Workflow\n"
        "Investigate repo bugs using a repeatable workflow.\n"
        "Use when a bug requires repo tracing and verification."
    )


@pytest.mark.asyncio
async def test_evaluator_skips_failed_turns() -> None:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=6,
    )
    projection_store = StubProjectionStore()
    skill_manage_tool = StubSkillManageTool()
    evaluator = SkillEvaluator(
        store=StubStore(
            _build_messages(
                assistant_message_id,
                tool_results=[
                    {"name": "read", "result": {"success": False, "error": "boom"}}
                ],
            )
        ),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
    )
    evaluator._generate_skill_draft = AsyncMock()

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_failed_turn"
    evaluator._generate_skill_draft.assert_not_awaited()
    skill_manage_tool.execute.assert_not_awaited()
