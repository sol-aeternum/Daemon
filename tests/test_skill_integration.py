"""Integration tests for the full autonomous skill lifecycle.

Tests cover the complete create → retrieve → use → refine → consolidate lifecycle:
1. Create: Qualifying turn enqueues skill evaluation that creates autonomous skill
2. Retrieve: Skill can be retrieved via list (L0) and view (L1)
3. Use: Viewing a skill increments use_count
4. Refine: Subsequent qualifying turn with overlap triggers refinement (patch)
5. Consolidate: Consolidation nudge job merges duplicate autonomous skills

Architecture awareness:
- Skill creation uses SkillEvaluator → skill_manage tool → markdown file + DB projection
- Skill retrieval uses skills_store (markdown) + skills_projection (DB metadata)
- Consolidation uses run_consolidation_nudge_job with model-driven duplicate detection
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.config import Settings
from orchestrator.memory.store import MemoryStore
from orchestrator.skill_evaluator import (
    SkillDraft,
    SkillEvaluationRequest,
    SkillEvaluator,
    SkillRefinementDecision,
)
from orchestrator.skills_projection import compute_content_hash
from orchestrator.worker.jobs import (
    run_consolidation_nudge_job,
    run_skill_evaluation_job,
)


class MockRecord:
    """Mock asyncpg Record for testing."""

    def __init__(self, **kwargs: Any) -> None:
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Any:
        return iter(self._data.keys())

    def keys(self) -> Any:
        return self._data.keys()

    def values(self) -> Any:
        return self._data.values()

    def items(self) -> Any:
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class StubStore:
    """Minimal MemoryStore stub for evaluator integration tests."""

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        conversation: dict[str, Any] | None = None,
    ) -> None:
        self._messages = messages or []
        self._conversation = conversation or {"summary": "Test conversation"}
        self.get_messages_mock = AsyncMock(return_value=self._messages)
        self.get_conversation_mock = AsyncMock(return_value=self._conversation)

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        del conversation_id, limit, offset
        return self._messages

    async def get_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        del conversation_id
        return self._conversation


class StubProjectionStore:
    """Minimal projection store stub for integration tests."""

    def __init__(
        self,
        search_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self._search_results = search_results or []
        self.search_by_embedding = AsyncMock(return_value=self._search_results)
        self.update_autonomous_metadata = AsyncMock(return_value=True)
        self.get_projection = AsyncMock(return_value=None)


class StubSkillManageTool:
    """Minimal skill_manage tool stub for integration tests."""

    def __init__(self, response: str = "{}") -> None:
        self._response = response
        self.call_args: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> str:
        self.call_args.append(kwargs)
        return self._response


def _build_qualifying_messages(
    assistant_message_id: uuid.UUID,
    tool_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a qualifying conversation turn (5+ tool calls)."""
    return [
        {
            "id": uuid.uuid4(),
            "role": "user",
            "content": "Please create a reusable workflow for this debugging process.",
            "status": "complete",
        },
        {
            "id": assistant_message_id,
            "role": "assistant",
            "content": "I'll document this debugging workflow as a reusable skill.",
            "status": "complete",
            "metadata": {"finish_reason": "stop"},
            "tool_calls": [
                {"name": "read", "arguments": {"file": "main.py"}},
                {"name": "grep", "arguments": {"pattern": "error"}},
                {"name": "read", "arguments": {"file": "config.py"}},
                {"name": "grep", "arguments": {"pattern": "debug"}},
                {"name": "read", "arguments": {"file": "utils.py"}},
            ],
            "tool_results": tool_results
            or [
                {"name": "read", "result": {"content": "// main content"}},
                {"name": "grep", "result": {"matches": 3}},
                {"name": "read", "result": {"content": "// config content"}},
                {"name": "grep", "result": {"matches": 5}},
                {"name": "read", "result": {"content": "// utils content"}},
            ],
        },
    ]


class TestSkillLifecycleCreate:
    """Tests for skill creation via qualifying turn evaluation."""

    @pytest.mark.asyncio
    async def test_qualifying_turn_triggers_autonomous_skill_creation(self, tmp_path: Path) -> None:
        """A qualifying turn (5+ tool calls) should result in a new autonomous skill."""
        assistant_message_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        skill_manage_tool = StubSkillManageTool(
            response=json.dumps(
                {
                    "skill_id": "debug-workflow",
                    "name": "Debug Workflow",
                    "description": "A reusable debugging workflow for the codebase.",
                    "source_type": "autonomous",
                    "created": True,
                }
            )
        )

        evaluator = SkillEvaluator(
            store=StubStore(_build_qualifying_messages(assistant_message_id)),
            db_pool=None,
            projection_store=StubProjectionStore(search_results=[]),
            skill_manage_tool=skill_manage_tool,
            query_embedder=AsyncMock(return_value=[0.1] * 1024),
        )
        evaluator._generate_skill_draft = AsyncMock(
            return_value=SkillDraft(
                name="Debug Workflow",
                description="A reusable debugging workflow for the codebase.",
                trigger_conditions="Use when debugging errors in the repository.",
                skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable debugging steps.",
            )
        )

        request = SkillEvaluationRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            tool_call_count=5,
        )

        result = await evaluator.evaluate_completed_turn(request)

        assert result.classification == "created"
        assert result.created_skill_id == "debug-workflow"
        assert len(skill_manage_tool.call_args) == 1
        assert skill_manage_tool.call_args[0]["action"] == "create"
        assert skill_manage_tool.call_args[0]["source_type"] == "autonomous"

    @pytest.mark.asyncio
    async def test_below_threshold_turn_does_not_create_skill(self) -> None:
        """A turn with fewer than 5 tool calls should not create a skill."""
        request = SkillEvaluationRequest(
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            assistant_message_id=uuid.uuid4(),
            tool_call_count=4,
        )

        evaluator = SkillEvaluator(
            store=StubStore([]),
            db_pool=None,
            projection_store=StubProjectionStore(),
            skill_manage_tool=StubSkillManageTool(),
            query_embedder=AsyncMock(return_value=[0.1] * 1024),
        )

        result = await evaluator.evaluate_completed_turn(request)

        assert result.classification == "skipped_below_threshold"
        assert result.created_skill_id is None


class TestSkillLifecycleRetrieve:
    """Tests for skill retrieval via list (L0) and view (L1)."""

    @pytest.mark.asyncio
    async def test_list_returns_l0_safe_summaries(self, tmp_path: Path) -> None:
        """List should return L0-safe summaries without full content."""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(
            "---\nname: Test Skill\ndescription: A test skill\nenabled: true\n---\n"
            "# Test Skill\n\nThis is the full content that should NOT appear in list results."
        )

        mock_db_pool = AsyncMock()
        mock_db_pool.fetch.return_value = []

        from orchestrator.tools.skill_manage import SkillManageTool

        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.tools.skill_manage.list_skills") as mock_list:
            mock_list.return_value = [
                {
                    "id": "test-skill",
                    "name": "Test Skill",
                    "description": "A test skill",
                    "enabled": True,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "source_type": "autonomous",
                    "use_count": 5,
                }
            ]
            result = await tool.execute(action="list")

        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "test-skill"
        assert parsed[0]["name"] == "Test Skill"
        # Content should NOT be in list results (L0 safety)
        assert "full content" not in result
        assert "This is the full content" not in result

    @pytest.mark.asyncio
    async def test_view_returns_full_content_and_increments_usage(self, tmp_path: Path) -> None:
        """View should return full content and increment use_count."""
        skill_file = tmp_path / "usage-skill.md"
        skill_file.write_text(
            "---\nname: Usage Skill\ndescription: Testing usage\nenabled: true\n---\n"
            "# Usage Skill\n\nFull content here."
        )

        mock_db_pool = AsyncMock()
        mock_db_pool.execute.return_value = "UPDATE 1"

        from orchestrator.tools.skill_manage import SkillManageTool

        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(action="view", skill_id="usage-skill")

        parsed = json.loads(result)
        assert parsed["id"] == "usage-skill"
        assert "Full content here" in parsed["content"]
        # Verify use_count was incremented
        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args[0]
        assert "use_count" in call_args[0]


class TestSkillLifecycleRefine:
    """Tests for skill refinement (patching) via evaluator."""

    @pytest.mark.asyncio
    async def test_overlapping_autonomous_skill_triggers_refinement(self, tmp_path: Path) -> None:
        """A qualifying turn overlapping with an existing autonomous skill should patch it."""
        assistant_message_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Existing autonomous skill with high similarity
        existing_skill_projection = StubProjectionStore(
            search_results=[
                {
                    "skill_id": "debug-workflow",
                    "name": "debug workflow",
                    "source_type": "autonomous",
                    "allow_autonomous_edit": False,
                    "similarity": 0.93,
                    "complexity_origin": 5,
                }
            ]
        )

        skill_manage_tool = StubSkillManageTool(
            response=json.dumps(
                {
                    "skill_id": "debug-workflow",
                    "patched": True,
                }
            )
        )

        evaluator = SkillEvaluator(
            store=StubStore(_build_qualifying_messages(assistant_message_id)),
            db_pool=None,
            projection_store=existing_skill_projection,
            skill_manage_tool=skill_manage_tool,
            query_embedder=AsyncMock(return_value=[0.1] * 1024),
        )
        evaluator._generate_skill_draft = AsyncMock(
            return_value=SkillDraft(
                name="Debug Workflow!!!",
                description="Improved debugging workflow.",
                trigger_conditions="Use when debugging errors in the repository.",
                skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable debugging steps.",
            )
        )
        evaluator._generate_refinement = AsyncMock(
            return_value=SkillRefinementDecision(
                decision="PATCH",
                reason="Add additional verification steps.",
                trigger_conditions="Use when debugging errors requiring post-fix verification.",
                old_text="## Purpose\n\nReusable debugging steps.",
                new_text="## Purpose\n\nReusable debugging steps with verification.",
            )
        )

        with patch(
            "orchestrator.skill_evaluator.get_skill",
            return_value={
                "id": "debug-workflow",
                "name": "Debug Workflow",
                "description": "A reusable debugging workflow.",
                "content": "# Debug Workflow\n\n## Purpose\n\nReusable debugging steps.",
            },
        ):
            request = SkillEvaluationRequest(
                user_id=user_id,
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id,
                tool_call_count=6,
            )

            result = await evaluator.evaluate_completed_turn(request)

        assert result.classification == "patched"
        assert result.patched_skill_id == "debug-workflow"
        assert len(skill_manage_tool.call_args) == 1
        assert skill_manage_tool.call_args[0]["action"] == "patch"
        assert skill_manage_tool.call_args[0]["skill_id"] == "debug-workflow"


class TestSkillLifecycleProtectedSkip:
    """Tests for protected skill behavior in the lifecycle."""

    @pytest.mark.asyncio
    async def test_protected_system_skill_overlap_is_skipped(self) -> None:
        """Overlap with a protected system skill should be skipped without creation."""
        assistant_message_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Protected system skill with high similarity
        system_skill_projection = StubProjectionStore(
            search_results=[
                {
                    "skill_id": "system-debug",
                    "name": "Debug Workflow",
                    "source_type": "system",
                    "allow_autonomous_edit": False,
                    "similarity": 0.92,
                    "complexity_origin": 6,
                }
            ]
        )

        skill_manage_tool = StubSkillManageTool()

        evaluator = SkillEvaluator(
            store=StubStore(_build_qualifying_messages(assistant_message_id)),
            db_pool=None,
            projection_store=system_skill_projection,
            skill_manage_tool=skill_manage_tool,
            query_embedder=AsyncMock(return_value=[0.1] * 1024),
        )
        evaluator._generate_skill_draft = AsyncMock(
            return_value=SkillDraft(
                name="Debug Workflow",
                description="A debugging workflow.",
                trigger_conditions="Use when debugging.",
                skill_markdown="# Debug Workflow\n\nContent.",
            )
        )

        request = SkillEvaluationRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            tool_call_count=7,
        )

        result = await evaluator.evaluate_completed_turn(request)

        assert result.classification == "skipped_protected_match"
        assert result.matched_skill_id == "system-debug"
        assert result.protected is True
        # skill_manage tool should NOT have been called
        assert len(skill_manage_tool.call_args) == 0


class TestSkillLifecycleJobIntegration:
    """Integration tests for skill evaluation job wiring."""

    @pytest.mark.asyncio
    async def test_job_executes_and_returns_result(self) -> None:
        """Full job execution should return structured result."""
        user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        assistant_message_id = uuid.uuid4()

        mock_store = MagicMock(spec=MemoryStore)

        ctx: dict[str, object] = {
            "store": mock_store,
            "db_pool": None,
        }

        with patch(
            "orchestrator.worker.jobs.SkillEvaluator",
        ) as MockEvaluator:
            evaluator_instance = MagicMock()
            evaluator_instance.evaluate_completed_turn = AsyncMock(
                return_value=MagicMock(
                    classification="created",
                    created_skill_id="job-created-skill",
                    patched_skill_id=None,
                    matched_skill_id=None,
                    matched_similarity=None,
                    matched_source_type=None,
                    protected=False,
                    trigger_conditions="test",
                    complexity_origin=5,
                    reason="test",
                    tool_call_count=5,
                )
            )
            MockEvaluator.return_value = evaluator_instance

            result = await run_skill_evaluation_job(
                ctx,
                str(user_id),
                str(conversation_id),
                str(assistant_message_id),
                5,
            )

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["classification"] == "created"
        assert result["created_skill_id"] == "job-created-skill"
        assert result["tool_call_count"] == 5


class TestSkillConsolidationIntegration:
    """Integration tests for consolidation nudge with skill lifecycle."""

    def _make_consolidation_store(self) -> tuple[MagicMock, dict[str, Any]]:
        """Create a mock MemoryStore with consolidation methods configured."""
        merge_tracker: dict[str, Any] = {}

        mock_store = MagicMock(spec=MemoryStore)
        mock_store._pool = MagicMock()

        async def mock_get_conversations_since(uid: uuid.UUID) -> int:
            return 20

        async def mock_get_total_conversations(uid: uuid.UUID) -> int:
            return 35

        async def mock_get_candidates(min_skills: int) -> list[dict[str, Any]]:
            return [
                {
                    "skill_id": "dup-skill-1",
                    "name": "Duplicate Skill One",
                    "description": "First duplicate",
                    "content": "Content one",
                    "use_count": 10,
                    "last_used_at": datetime.now(timezone.utc),
                },
                {
                    "skill_id": "dup-skill-2",
                    "name": "Duplicate Skill Two",
                    "description": "Second duplicate",
                    "content": "Content two",
                    "use_count": 5,
                    "last_used_at": datetime.now(timezone.utc),
                },
            ]

        async def mock_get_recent(uid: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
            return []

        async def mock_record_run(uid: uuid.UUID, count: int) -> None:
            pass

        async def mock_log_action(**kwargs: Any) -> None:
            pass

        async def mock_merge(
            kept_skill_id: str,
            absorbed_skill_ids: list[str],
            user_id: uuid.UUID,
        ) -> None:
            merge_tracker["kept_skill_id"] = kept_skill_id
            merge_tracker["absorbed_skill_ids"] = absorbed_skill_ids

        mock_store.get_user_conversation_count_since_last_nudge = AsyncMock(
            side_effect=mock_get_conversations_since
        )
        mock_store.get_total_conversation_count = AsyncMock(
            side_effect=mock_get_total_conversations
        )
        mock_store.get_autonomous_skill_candidates = AsyncMock(side_effect=mock_get_candidates)
        mock_store.get_recent_memories_for_user = AsyncMock(side_effect=mock_get_recent)
        mock_store.record_consolidation_nudge_run = AsyncMock(side_effect=mock_record_run)
        mock_store.log_consolidation_nudge_action = AsyncMock(side_effect=mock_log_action)
        mock_store.merge_autonomous_skills = AsyncMock(side_effect=mock_merge)

        return mock_store, merge_tracker

    @pytest.mark.asyncio
    async def test_consolidation_nudge_merges_duplicate_autonomous_skills(
        self, tmp_path: Path
    ) -> None:
        """Consolidation nudge should merge duplicate autonomous skills."""
        user_id = uuid.uuid4()
        mock_store, merge_tracker = self._make_consolidation_store()

        test_settings = Settings(
            consolidation_nudge_enabled=True,
            consolidation_nudge_conversation_interval=15,
            consolidation_nudge_stale_days=30,
            consolidation_nudge_min_skills=3,
        )

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "actions": [
                                {
                                    "type": "merge",
                                    "skill_id": "dup-skill-2",
                                    "target_skill_id": "dup-skill-1",
                                    "reason": "duplicate skills with high similarity",
                                    "similarity": 0.95,
                                }
                            ]
                        }
                    )
                )
            )
        ]

        ctx: dict[str, object] = {
            "store": mock_store,
            "settings": test_settings,
            "db_pool": None,
        }

        with patch("litellm.acompletion", AsyncMock(return_value=mock_response)):
            result = await run_consolidation_nudge_job(ctx, str(user_id))

        assert result["status"] == "ok"
        assert result["skills_reviewed"] == 2
        assert result["duplicates_found"] == 1
        assert result["duplicates_merged"] == 1
        # Verify merge was called with correct args
        # Model returns: {"type": "merge", "skill_id": "dup-skill-2", "target_skill_id": "dup-skill-1"}
        # Code semantics: skill_id = kept, target_skill_id = absorbed
        assert merge_tracker.get("kept_skill_id") == "dup-skill-2"
        assert merge_tracker.get("absorbed_skill_ids") == ["dup-skill-1"]

    @pytest.mark.asyncio
    async def test_consolidation_skips_protected_skills(self) -> None:
        """Consolidation should not modify protected (non-autonomous) skills."""
        user_id = uuid.uuid4()

        mock_store = MagicMock(spec=MemoryStore)
        mock_store._pool = MagicMock()

        async def mock_get_conversations_since(uid: uuid.UUID) -> int:
            return 20

        async def mock_get_total_conversations(uid: uuid.UUID) -> int:
            return 35

        async def mock_get_candidates(min_skills: int) -> list[dict[str, Any]]:
            return []

        async def mock_get_recent(uid: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
            return []

        async def mock_record_run(uid: uuid.UUID, count: int) -> None:
            pass

        async def mock_log_action(**kwargs: Any) -> None:
            pass

        mock_store.get_user_conversation_count_since_last_nudge = AsyncMock(
            side_effect=mock_get_conversations_since
        )
        mock_store.get_total_conversation_count = AsyncMock(
            side_effect=mock_get_total_conversations
        )
        mock_store.get_autonomous_skill_candidates = AsyncMock(side_effect=mock_get_candidates)
        mock_store.get_recent_memories_for_user = AsyncMock(side_effect=mock_get_recent)
        mock_store.record_consolidation_nudge_run = AsyncMock(side_effect=mock_record_run)
        mock_store.log_consolidation_nudge_action = AsyncMock(side_effect=mock_log_action)

        test_settings = Settings(
            consolidation_nudge_enabled=True,
            consolidation_nudge_conversation_interval=15,
            consolidation_nudge_stale_days=30,
            consolidation_nudge_min_skills=3,
        )

        ctx: dict[str, object] = {
            "store": mock_store,
            "settings": test_settings,
            "db_pool": None,
        }

        result = await run_consolidation_nudge_job(ctx, str(user_id))

        assert result["status"] == "ok"
        assert result["skills_reviewed"] == 0
        assert result["duplicates_found"] == 0
        assert result["duplicates_merged"] == 0


class TestSkillUpgradeIntegration:
    """Integration tests for upgrade behavior with skill lifecycle."""

    @pytest.mark.asyncio
    async def test_upgrade_applies_pending_update(self, tmp_path: Path) -> None:
        """Applying a pending update should overwrite local file with repo content."""
        from orchestrator.skills_upgrade import SkillUpgradeService

        skill_file = tmp_path / "upgrade-skill.md"
        skill_file.write_text(
            "---\nname: Upgrade Skill\ndescription: Old\nrepo_version: 1.0.0\nlocal_version: 1.0.0\n---\n\nLocal content."
        )

        pending_content = "---\nname: Upgrade Skill\ndescription: New\nrepo_version: 2.0.0\nlocal_version: 1.0.0\n---\n\nRepo new content."

        pool = AsyncMock()
        pool.fetchrow.return_value = MockRecord(
            skill_id="upgrade-skill",
            name="Upgrade Skill",
            description="Old",
            source_file_path=str(tmp_path / "upgrade-skill.md"),
            source_hash=compute_content_hash(
                "---\nname: Upgrade Skill\ndescription: Old\nrepo_version: 1.0.0\nlocal_version: 1.0.0\n---\n\nLocal content."
            ),
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update={
                "repo_hash": compute_content_hash(pending_content),
                "repo_version": "2.0.0",
                "repo_content": pending_content,
                "repo_name": "Upgrade Skill",
                "repo_description": "New",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=5,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)

        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                action = await service.apply_pending_update("upgrade-skill")

        assert action.action == "applied"
        assert action.success is True
        updated_content = skill_file.read_text(encoding="utf-8")
        assert "Repo new content" in updated_content
        assert "2.0.0" in updated_content


class TestSkillRegressionManualFlows:
    """Regression tests to ensure manual skill flows still work."""

    @pytest.mark.asyncio
    async def test_manual_skill_create_still_works(self, tmp_path: Path) -> None:
        """Manual skill creation via API should still work."""
        from orchestrator.tools.skill_manage import SkillManageTool

        mock_db_pool = AsyncMock()
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="manual-new-skill",
            name="Manual New Skill",
            description="Created manually",
            source_file_path=str(tmp_path / "manual-new-skill.md"),
            source_hash="abc123",
            enabled=True,
            source_type="manual",
            created_by="test_user",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="0.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
        )

        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                result = await tool.execute(
                    action="create",
                    name="Manual New Skill",
                    description="Created manually",
                    content="# Manual New Skill\n\nContent.",
                    source_type="manual",
                    caller_autonomous=False,
                )

        parsed = json.loads(result)
        assert parsed["created"] is True
        assert parsed["skill_id"] == "manual-new-skill"

    @pytest.mark.asyncio
    async def test_autonomous_skill_patch_requires_caller_context(self, tmp_path: Path) -> None:
        """Autonomous skill modification should require proper caller context."""
        from orchestrator.tools.skill_manage import SkillManageTool

        skill_file = tmp_path / "auto-skill.md"
        skill_file.write_text(
            "---\nname: Auto Skill\ndescription: An autonomous skill\nenabled: true\n---\n\nOld content."
        )

        mock_db_pool = AsyncMock()
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="auto-skill",
            name="Auto Skill",
            description="An autonomous skill",
            source_file_path=str(tmp_path / "auto-skill.md"),
            source_hash="abc123",
            enabled=True,
            source_type="autonomous",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="0.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=5,
            use_count=10,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=datetime.now(),
        )

        tool = SkillManageTool(db_pool=mock_db_pool)

        # Autonomous caller should be able to patch autonomous skill
        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="patch",
                skill_id="auto-skill",
                old_text="Old content.",
                new_text="New content from autonomous.",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert parsed.get("patched") is True


class TestSkillDownloadExport:
    """Tests for skill download/export functionality."""

    def test_exported_skill_can_be_reimported(self, tmp_path: Path) -> None:
        """Downloaded skill content should be re-importable."""
        skill_content = (
            "---\nname: Export Reimport Test\ndescription: Test roundtrip\nenabled: true\n---\n\n"
            "# Export Reimport Test\n\nContent for roundtrip testing."
        )
        skill_file = tmp_path / "export-reimport.md"
        skill_file.write_text(skill_content)

        # Verify the content has proper frontmatter
        assert "---" in skill_content
        assert "name:" in skill_content
        assert "description:" in skill_content
        assert "# Export Reimport Test" in skill_content

        # Read back and verify structure
        read_content = skill_file.read_text()
        lines = read_content.split("\n")
        assert lines[0] == "---"
        # Find end of frontmatter
        frontmatter_end = -1
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                frontmatter_end = i
                break
        assert frontmatter_end > 0
        # Body should start after frontmatter
        body_start = frontmatter_end + 1
        assert "# Export Reimport Test" in "\n".join(lines[body_start:])


class TestSkillProtectionIntegration:
    """Integration tests for protection model across lifecycle."""

    @pytest.mark.asyncio
    async def test_system_skill_protection_prevents_autonomous_modification(
        self, tmp_path: Path
    ) -> None:
        """System skill should prevent autonomous modification by default."""
        from orchestrator.tools.skill_manage import (
            _check_modification_allowed,
        )

        projection = {
            "source_type": "system",
            "allow_autonomous_edit": False,
        }
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is False
        assert "protected" in reason

    @pytest.mark.asyncio
    async def test_system_skill_with_opt_in_allows_autonomous_modification(
        self, tmp_path: Path
    ) -> None:
        """System skill with allow_autonomous_edit=True should allow modification."""
        from orchestrator.tools.skill_manage import _check_modification_allowed

        projection = {
            "source_type": "system",
            "allow_autonomous_edit": True,
        }
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_autonomous_skill_allows_autonomous_modification(self) -> None:
        """Autonomous skill should always allow autonomous modification."""
        from orchestrator.tools.skill_manage import _check_modification_allowed

        projection = {
            "source_type": "autonomous",
            "allow_autonomous_edit": False,  # Even if False, autonomous can modify
        }
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_direct_user_bypasses_protection(self) -> None:
        """Direct user calls should bypass protection checks."""
        from orchestrator.tools.skill_manage import _check_modification_allowed

        projection = {
            "source_type": "system",
            "allow_autonomous_edit": False,
        }
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=False)
        assert allowed is True
        assert reason == ""
