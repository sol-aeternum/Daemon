from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false

import uuid
from typing import Any, final
from unittest.mock import AsyncMock

import pytest

from orchestrator.memory.embedding import EmbeddingConfigurationError
from orchestrator.skill_evaluator import (
    SkillDraft,
    SkillEvaluationRequest,
    SkillEvaluator,
    SkillRefinementDecision,
    SkillTurnContext,
    _build_dedup_query_text,
)


@final
class StubStore:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.get_messages_mock = AsyncMock(return_value=messages)
        self.get_conversation_mock = AsyncMock(return_value={"summary": "Conversation summary"})

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
class StubSkillStore:
    """The account-owned store calls the evaluator makes, recorded."""

    def __init__(self) -> None:
        self.search_by_embedding = AsyncMock(return_value=[])
        self.create = AsyncMock(
            side_effect=lambda user_id, **fields: {
                "id": str(uuid.uuid4()),
                "slug": "debug-workflow",
                "revision": 1,
                "complexity_origin": fields["complexity_origin"],
                **fields,
            }
        )
        self.patch = AsyncMock(
            side_effect=lambda user_id, slug, old_text, new_text, **fields: {
                "id": str(uuid.uuid4()),
                "slug": slug,
                "revision": 2,
                "name": "Debug Workflow",
                "description": "",
                "body": new_text,
                "complexity_origin": fields["complexity_origin"],
            }
        )
        self.store_embedding = AsyncMock(return_value=True)


@pytest.fixture(autouse=True)
def _no_embedding_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.skill_evaluator.embed_skill", AsyncMock(return_value=True))


def _evaluator(
    messages: list[dict[str, Any]],
    skill_store: StubSkillStore | None,
    query_embedder: AsyncMock | None = None,
) -> SkillEvaluator:
    return SkillEvaluator(
        store=StubStore(messages),
        db_pool=None,
        skill_store=skill_store,  # type: ignore[arg-type]
        query_embedder=query_embedder or AsyncMock(return_value=[0.1, 0.2]),
    )


def _draft(
    name: str = "Debug Workflow", description: str = "Reusable debugging steps"
) -> SkillDraft:
    return SkillDraft(
        name=name,
        description=description,
        trigger_conditions="Use when a bug requires repo tracing and verification.",
        skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable steps.",
    )


def _request(tool_call_count: int) -> SkillEvaluationRequest:
    return SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        tool_call_count=tool_call_count,
    )


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
async def test_missing_qualified_reader_never_uses_legacy_summary() -> None:
    assistant_id = uuid.uuid4()
    store = StubStore(_build_messages(assistant_id))
    evaluator = SkillEvaluator(store, None)
    turn = await evaluator._load_turn_context(
        SkillEvaluationRequest(uuid.uuid4(), uuid.uuid4(), assistant_id, 7)
    )
    assert turn is not None
    assert turn.conversation_summary is None
    store.get_conversation_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reader_state", ["missing", "changed", "qualified"])
async def test_skill_generation_omits_even_qualified_summary_context(
    monkeypatch: pytest.MonkeyPatch, reader_state: str
) -> None:
    assistant_id = uuid.uuid4()
    store = StubStore(_build_messages(assistant_id))
    reader = AsyncMock(return_value=(None, "Qualified synthetic summary."))
    if reader_state != "missing":
        monkeypatch.setattr(store, "read_qualified_conversation_summary", reader, raising=False)
    evaluator = SkillEvaluator(store, None)
    loaded = await evaluator._load_turn_context(
        SkillEvaluationRequest(uuid.uuid4(), uuid.uuid4(), assistant_id, 7)
    )
    assert loaded is not None and loaded.conversation_summary is None
    store.get_conversation_mock.assert_not_awaited()
    prompt = AsyncMock(return_value=None)
    monkeypatch.setattr(evaluator, "_run_json_prompt", prompt)
    turn = SkillTurnContext(
        {"content": "Synthetic user request."},
        {"content": "Synthetic assistant body."},
        "",
        "Qualified synthetic summary.",
    )
    assert await evaluator._generate_skill_draft(turn, 7) is None
    reader.assert_not_awaited()
    prompt.assert_awaited_once()
    assert prompt.await_args is not None
    assert "Qualified synthetic summary." not in prompt.await_args.kwargs["prompt"]


@pytest.mark.asyncio
async def test_evaluator_skips_below_threshold() -> None:
    request = _request(4)
    skill_store = StubSkillStore()

    result = await _evaluator([], skill_store).evaluate_completed_turn(request)

    assert result.classification == "skipped_below_threshold"
    assert result.debounce_key == (
        f"skill_eval:{request.conversation_id}:{request.assistant_message_id}"
    )
    skill_store.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluator_skips_without_a_skill_store() -> None:
    request = _request(7)
    result = await _evaluator([], None).evaluate_completed_turn(request)
    assert result.classification == "skipped_projection_unavailable"


@pytest.mark.asyncio
async def test_evaluator_creates_novel_autonomous_skill() -> None:
    request = _request(7)
    skill_store = StubSkillStore()
    evaluator = _evaluator(_build_messages(request.assistant_message_id), skill_store)
    draft = _draft()
    evaluator._generate_skill_draft = AsyncMock(return_value=draft)

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    # Created in the account of the evaluated turn, as an autonomous actor;
    # trigger conditions and complexity are part of the same write.
    skill_store.create.assert_awaited_once_with(
        request.user_id,
        name=draft.name,
        description=draft.description,
        body=draft.skill_markdown,
        trigger_conditions=draft.trigger_conditions,
        actor="autonomous",
        complexity_origin=7,
    )
    skill_store.search_by_embedding.assert_awaited_once()
    assert skill_store.search_by_embedding.call_args.args[0] == request.user_id


@pytest.mark.asyncio
async def test_create_refusal_is_reported_by_reason() -> None:
    from orchestrator.services.skills import SkillError

    request = _request(7)
    skill_store = StubSkillStore()
    skill_store.create = AsyncMock(side_effect=SkillError("slug_taken"))
    evaluator = _evaluator(_build_messages(request.assistant_message_id), skill_store)
    evaluator._generate_skill_draft = AsyncMock(return_value=_draft())

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_create_conflict"
    assert result.reason == "slug_taken"


@pytest.mark.asyncio
async def test_denied_embedding_skips_semantic_matching_but_creates_skill() -> None:
    request = _request(7)
    skill_store = StubSkillStore()
    denied_embedder = AsyncMock(
        side_effect=EmbeddingConfigurationError("Approved embedding route unavailable")
    )
    evaluator = _evaluator(
        _build_messages(request.assistant_message_id), skill_store, denied_embedder
    )
    evaluator._generate_skill_draft = AsyncMock(return_value=_draft())

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    denied_embedder.assert_awaited_once()
    skill_store.search_by_embedding.assert_not_awaited()
    skill_store.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_evaluator_skips_protected_overlap_without_duplicate_creation() -> None:
    request = _request(8)
    skill_store = StubSkillStore()
    skill_store.search_by_embedding.return_value = [
        {
            "slug": "manual-debug",
            "name": "Debug Workflow",
            "source_type": "manual",
            "allow_autonomous_edit": False,
            "similarity": 0.91,
            "complexity_origin": 6,
        }
    ]
    evaluator = _evaluator(_build_messages(request.assistant_message_id), skill_store)
    evaluator._generate_skill_draft = AsyncMock(return_value=_draft())
    evaluator._generate_refinement = AsyncMock()

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_protected_match"
    assert result.matched_skill_id == "manual-debug"
    assert result.protected is True
    evaluator._generate_refinement.assert_not_awaited()
    skill_store.create.assert_not_awaited()
    skill_store.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluator_patches_overlapping_autonomous_skill() -> None:
    request = _request(8)
    skill_store = StubSkillStore()
    skill_store.search_by_embedding.return_value = [
        {
            "slug": "debug-workflow",
            "name": "debug workflow",
            "description": "A reusable debugging skill.",
            "body": "# Debug Workflow\n\n## Purpose\n\nOld section",
            "source_type": "autonomous",
            "allow_autonomous_edit": True,
            "similarity": 0.92,
            "complexity_origin": 6,
        }
    ]
    evaluator = _evaluator(_build_messages(request.assistant_message_id), skill_store)
    evaluator._generate_skill_draft = AsyncMock(return_value=_draft("Debug Workflow!!!"))
    evaluator._generate_refinement = AsyncMock(
        return_value=SkillRefinementDecision(
            decision="PATCH",
            reason="Add a missing validation step.",
            trigger_conditions="Use when a repo bug needs tracing and post-fix verification.",
            old_text="## Purpose\n\nOld section",
            new_text="## Purpose\n\nUpdated section",
        )
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "patched"
    assert result.patched_skill_id == "debug-workflow"
    assert result.complexity_origin == 8
    # The existing skill offered to the refinement prompt is the match itself.
    assert (
        evaluator._generate_refinement.call_args.kwargs["existing_skill"]["body"]
        == "# Debug Workflow\n\n## Purpose\n\nOld section"
    )
    skill_store.patch.assert_awaited_once_with(
        request.user_id,
        "debug-workflow",
        "## Purpose\n\nOld section",
        "## Purpose\n\nUpdated section",
        actor="autonomous",
        trigger_conditions="Use when a repo bug needs tracing and post-fix verification.",
        complexity_origin=8,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refusal", "classification", "protected"),
    [
        ("autonomous_edit_not_granted", "skipped_protected_match", True),
        ("shipped_read_only", "skipped_protected_match", True),
        ("patch_text_not_found", "skipped_patch_conflict", False),
    ],
)
async def test_patch_refusals_are_classified(
    refusal: str, classification: str, protected: bool
) -> None:
    from orchestrator.services.skills import SkillError

    request = _request(8)
    skill_store = StubSkillStore()
    skill_store.search_by_embedding.return_value = [
        {"slug": "debug-workflow", "name": "Debug Workflow", "source_type": "autonomous",
         "allow_autonomous_edit": True, "similarity": 0.92, "body": "x"}
    ]  # fmt: skip
    skill_store.patch = AsyncMock(side_effect=SkillError(refusal))
    evaluator = _evaluator(_build_messages(request.assistant_message_id), skill_store)
    evaluator._generate_skill_draft = AsyncMock(return_value=_draft())
    evaluator._generate_refinement = AsyncMock(
        return_value=SkillRefinementDecision("PATCH", "r", "", old_text="x", new_text="y")
    )

    result = await evaluator.evaluate_completed_turn(request)

    assert (result.classification, result.protected, result.reason) == (
        classification,
        protected,
        refusal,
    )


@pytest.mark.asyncio
async def test_evaluator_creates_new_skill_when_semantic_match_has_different_name() -> None:
    request = _request(7)
    skill_store = StubSkillStore()
    skill_store.search_by_embedding.return_value = [
        {
            "slug": "worker-repair",
            "name": "Worker Repair",
            "source_type": "autonomous",
            "allow_autonomous_edit": True,
            "similarity": 0.97,
            "complexity_origin": 6,
        }
    ]
    query_embedder = AsyncMock(return_value=[0.3, 0.4])
    evaluator = _evaluator(
        _build_messages(request.assistant_message_id), skill_store, query_embedder
    )
    draft = _draft(description="Investigate repo bugs using a repeatable workflow.")
    evaluator._generate_skill_draft = AsyncMock(return_value=draft)

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    skill_store.create.assert_awaited_once()
    skill_store.patch.assert_not_awaited()
    query_embedder.assert_awaited_once_with(_build_dedup_query_text(draft))


def test_build_dedup_query_text_includes_name_and_trigger_conditions() -> None:
    draft = _draft(description="Investigate repo bugs using a repeatable workflow.")
    assert _build_dedup_query_text(draft) == (
        "Debug Workflow\n"
        "Investigate repo bugs using a repeatable workflow.\n"
        "Use when a bug requires repo tracing and verification."
    )


@pytest.mark.asyncio
async def test_evaluator_skips_failed_turns() -> None:
    request = _request(6)
    skill_store = StubSkillStore()
    evaluator = _evaluator(
        _build_messages(
            request.assistant_message_id,
            tool_results=[{"name": "read", "result": {"success": False, "error": "boom"}}],
        ),
        skill_store,
    )
    evaluator._generate_skill_draft = AsyncMock()

    result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_failed_turn"
    evaluator._generate_skill_draft.assert_not_awaited()
    skill_store.create.assert_not_awaited()
