from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.config import Settings
from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import (
    ConsolidationNudgeAction,
    ConsolidationNudgeResults,
    _apply_delete_action,
    _apply_merge_action,
    _build_consolidation_nudge_debounce_key,
    run_consolidation_nudge_job,
)


class TestConsolidationNudgeDebounceKey:
    def test_format(self) -> None:
        user_id = uuid.uuid4()
        key = _build_consolidation_nudge_debounce_key(user_id)
        assert key == f"consolidation_nudge:{user_id}"

    def test_stable(self) -> None:
        user_id = uuid.uuid4()
        key1 = _build_consolidation_nudge_debounce_key(user_id)
        key2 = _build_consolidation_nudge_debounce_key(user_id)
        assert key1 == key2

    def test_unique_per_user(self) -> None:
        user1 = uuid.uuid4()
        user2 = uuid.uuid4()
        key1 = _build_consolidation_nudge_debounce_key(user1)
        key2 = _build_consolidation_nudge_debounce_key(user2)
        assert key1 != key2


class TestConsolidationNudgeJobResultStructure:
    def test_result_has_correct_structure(self) -> None:
        result: ConsolidationNudgeResults = {
            "status": "ok",
            "user_id": str(uuid.uuid4()),
            "actions": [],
            "skills_reviewed": 0,
            "duplicates_found": 0,
            "duplicates_merged": 0,
            "stale_flagged": 0,
            "errors": [],
            "error_count": 0,
        }
        assert "status" in result
        assert "user_id" in result
        assert "actions" in result
        assert "skills_reviewed" in result
        assert "duplicates_found" in result
        assert "duplicates_merged" in result
        assert "stale_flagged" in result
        assert "errors" in result
        assert "error_count" in result


class TestConsolidationNudgeActionStructure:
    def test_action_merge(self) -> None:
        action: ConsolidationNudgeAction = {
            "action_type": "merge",
            "skill_id": "skill-123",
            "target_skill_id": "skill-456",
            "reason": "merged duplicate skills",
            "similarity": 0.92,
            "status": "applied",
        }
        assert action["action_type"] == "merge"
        assert action["skill_id"] == "skill-123"
        assert action["status"] == "applied"

    def test_action_delete(self) -> None:
        action: ConsolidationNudgeAction = {
            "action_type": "delete",
            "skill_id": "skill-456",
            "target_skill_id": None,
            "reason": "merged_into skill-123",
            "similarity": None,
            "status": "applied",
        }
        assert action["action_type"] == "delete"
        assert action["status"] == "applied"

    def test_action_flag_stale(self) -> None:
        action: ConsolidationNudgeAction = {
            "action_type": "flag_stale",
            "skill_id": "skill-789",
            "target_skill_id": None,
            "reason": "not used in 30 days",
            "similarity": None,
            "status": "recorded",
        }
        assert action["action_type"] == "flag_stale"
        assert action["status"] == "recorded"


@pytest.mark.asyncio
async def test_job_handles_store_unavailable() -> None:
    user_id = uuid.uuid4()
    ctx: dict[str, object] = {
        "store": None,
        "db_pool": None,
    }

    result = await run_consolidation_nudge_job(ctx, str(user_id))

    assert result["status"] == "skipped"
    assert "store_unavailable" in result["errors"]
    assert result["error_count"] == 1


@pytest.mark.asyncio
async def test_job_handles_settings_unavailable() -> None:
    user_id = uuid.uuid4()
    mock_store = MagicMock(spec=MemoryStore)
    ctx: dict[str, object] = {
        "store": mock_store,
        "settings": None,
        "db_pool": None,
    }

    result = await run_consolidation_nudge_job(ctx, str(user_id))

    assert result["status"] == "skipped"
    assert "settings_unavailable" in result["errors"]


@pytest.mark.asyncio
async def test_job_skips_when_below_interval() -> None:
    user_id = uuid.uuid4()

    class FakeMemoryStore(MemoryStore):
        def __init__(self) -> None:
            pass

        async def get_user_conversation_count_since_last_nudge(self, uid: uuid.UUID) -> int:
            return 5

        async def get_total_conversation_count(self, uid: uuid.UUID) -> int:
            return 20

        async def log_consolidation_nudge_action(self, **kwargs) -> None:
            pass

    test_settings = Settings(
        consolidation_nudge_enabled=True,
        consolidation_nudge_conversation_interval=15,
        consolidation_nudge_stale_days=30,
        consolidation_nudge_min_skills=3,
    )

    ctx: dict[str, object] = {
        "store": FakeMemoryStore(),
        "settings": test_settings,
        "db_pool": None,
    }

    result = await run_consolidation_nudge_job(ctx, str(user_id))

    assert result["status"] == "ok"
    assert result["skills_reviewed"] == 0


@pytest.mark.asyncio
async def test_job_finds_users_above_interval() -> None:
    user_id = uuid.uuid4()

    class FakeMemoryStore(MemoryStore):
        def __init__(self) -> None:
            pass

        async def get_users_with_skill_candidates(self, interval: int) -> list[uuid.UUID]:
            return [user_id]

        async def get_user_conversation_count_since_last_nudge(self, uid: uuid.UUID) -> int:
            return 20

        async def get_total_conversation_count(self, uid: uuid.UUID) -> int:
            return 35

        async def get_autonomous_skill_candidates(self, min_skills: int) -> list[dict[str, Any]]:
            return []

        async def record_consolidation_nudge_run(self, uid: uuid.UUID, count: int) -> None:
            pass

        async def log_consolidation_nudge_action(self, **kwargs) -> None:
            pass

    test_settings = Settings(
        consolidation_nudge_enabled=True,
        consolidation_nudge_conversation_interval=15,
        consolidation_nudge_stale_days=30,
        consolidation_nudge_min_skills=3,
    )

    ctx: dict[str, object] = {
        "store": FakeMemoryStore(),
        "settings": test_settings,
        "db_pool": None,
    }

    result = await run_consolidation_nudge_job(ctx)

    assert result["status"] == "ok"


class TestConsolidationPromptBuilding:
    def test_prompt_includes_skills(self) -> None:
        from orchestrator.consolidation_nudge_prompts import (
            build_consolidation_nudge_prompt,
        )

        skills = [
            {
                "skill_id": "test-skill",
                "name": "Test Skill",
                "description": "A test skill",
                "content": "Skill content here",
                "use_count": 5,
                "last_used_at": "2026-04-01T00:00:00+00:00",
            }
        ]
        prompt = build_consolidation_nudge_prompt(
            autonomous_skills=skills,
            recent_memories=[],
        )
        assert "Test Skill" in prompt
        assert "test-skill" in prompt
        assert "A test skill" in prompt

    def test_prompt_includes_memories(self) -> None:
        from orchestrator.consolidation_nudge_prompts import (
            build_consolidation_nudge_prompt,
        )

        memories = [
            {
                "id": "mem-1",
                "content": "Test memory content",
                "status": "active",
                "created_at": "2026-04-10T00:00:00+00:00",
            }
        ]
        prompt = build_consolidation_nudge_prompt(
            autonomous_skills=[],
            recent_memories=memories,
        )
        assert "Test memory content" in prompt
        assert "active" in prompt


class TestConsolidationActionParsing:
    def test_parses_valid_merge_action(self) -> None:
        from orchestrator.consolidation_nudge_prompts import parse_consolidation_actions

        response = """{
            "actions": [
                {
                    "type": "merge",
                    "skill_id": "skill-123",
                    "target_skill_id": "skill-456",
                    "reason": "duplicate skills detected",
                    "similarity": 0.95
                }
            ]
        }"""
        actions = parse_consolidation_actions(response)
        assert len(actions) == 1
        assert actions[0]["type"] == "merge"
        assert actions[0]["skill_id"] == "skill-123"
        assert actions[0]["target_skill_id"] == "skill-456"
        assert actions[0]["similarity"] == 0.95

    def test_parses_flag_stale_action(self) -> None:
        from orchestrator.consolidation_nudge_prompts import parse_consolidation_actions

        response = """{
            "actions": [
                {
                    "type": "flag_stale",
                    "skill_id": "skill-789",
                    "reason": "not used in 60 days",
                    "similarity": null
                }
            ]
        }"""
        actions = parse_consolidation_actions(response)
        assert len(actions) == 1
        assert actions[0]["type"] == "flag_stale"
        assert actions[0]["skill_id"] == "skill-789"

    def test_ignores_invalid_action_types(self) -> None:
        from orchestrator.consolidation_nudge_prompts import parse_consolidation_actions

        response = """{
            "actions": [
                {"type": "invalid_type", "skill_id": "skill-123"}
            ]
        }"""
        actions = parse_consolidation_actions(response)
        assert len(actions) == 0

    def test_returns_empty_for_invalid_json(self) -> None:
        from orchestrator.consolidation_nudge_prompts import parse_consolidation_actions

        actions = parse_consolidation_actions("not json at all")
        assert actions == []


class TestProtectedSkillsExclusion:
    @pytest.mark.asyncio
    async def test_only_autonomous_skills_merged(self) -> None:
        from orchestrator.consolidation_nudge_prompts import (
            build_consolidation_nudge_prompt,
        )

        skills = [
            {
                "skill_id": "autonomous-skill",
                "name": "Autonomous Skill",
                "description": "An autonomous skill",
                "content": "Content",
                "use_count": 10,
                "last_used_at": datetime.now(timezone.utc),
            },
            {
                "skill_id": "system-skill",
                "name": "System Skill",
                "description": "A protected system skill",
                "content": "Content",
                "use_count": 100,
                "last_used_at": datetime.now(timezone.utc),
            },
        ]

        prompt = build_consolidation_nudge_prompt(
            autonomous_skills=skills,
            recent_memories=[],
        )

        assert "autonomous-skill" in prompt
        assert "Autonomous Skill" in prompt


class TestApplyMergeAction:
    @pytest.mark.asyncio
    async def test_merge_deletes_absorbed_skill(self) -> None:
        user_id = uuid.uuid4()

        class FakeMemoryStore:
            def __init__(self) -> None:
                self._pool = MagicMock()

            async def merge_autonomous_skills(
                self,
                kept_skill_id: str,
                absorbed_skill_ids: list[str],
                user_id: uuid.UUID,
            ) -> None:
                self.kept_skill_id = kept_skill_id
                self.absorbed_skill_ids = absorbed_skill_ids

        store = FakeMemoryStore()
        result = await _apply_merge_action(
            "skill-keep", "skill-absorb", cast(MemoryStore, store), user_id
        )

        assert result["merged"] is True
        assert store.kept_skill_id == "skill-keep"
        assert store.absorbed_skill_ids == ["skill-absorb"]


class TestApplyDeleteAction:
    @pytest.mark.asyncio
    async def test_delete_writes_pending_audit_before_destructive_actions(self) -> None:
        user_id = uuid.uuid4()
        audit_id = uuid.uuid4()
        events: list[tuple[str, str]] = []

        class FakeMemoryStore(MemoryStore):
            def __init__(self) -> None:
                self._pool = MagicMock()
                self.deleted_projection: str | None = None

            async def log_consolidation_nudge_action(self, **kwargs) -> uuid.UUID:
                events.append(("audit", kwargs["status"]))
                self.logged_action = kwargs
                return audit_id

            async def update_consolidation_nudge_action_status(
                self,
                action_id: uuid.UUID,
                *,
                status: str,
                reason: str | None = None,
            ) -> None:
                events.append(("audit_update", status))
                self.updated_action = {"action_id": action_id, "status": status, "reason": reason}

        class FakeProjectionStore:
            def __init__(self, pool: object) -> None:
                self.pool = pool

            async def delete_projection(self, skill_id: str) -> bool:
                events.append(("projection_delete", skill_id))
                return True

        def fake_delete_skill(skill_id: str) -> None:
            events.append(("skill_delete", skill_id))

        with patch("orchestrator.skills_store.delete_skill", side_effect=fake_delete_skill):
            with patch("orchestrator.skills_projection.SkillProjectionStore") as mock_proj:
                mock_proj.return_value = FakeProjectionStore(object())

                store = FakeMemoryStore()
                result = await _apply_delete_action(
                    "skill-to-delete", cast(MemoryStore, store), user_id
                )

                assert result == {"deleted": True, "reason": "ok"}
                assert events == [
                    ("audit", "pending"),
                    ("skill_delete", "skill-to-delete"),
                    ("projection_delete", "skill-to-delete"),
                    ("audit_update", "applied"),
                ]
                assert store.logged_action["status"] == "pending"
                assert store.updated_action == {
                    "action_id": audit_id,
                    "status": "applied",
                    "reason": None,
                }

    @pytest.mark.asyncio
    async def test_delete_does_not_run_when_pending_audit_insert_fails(self) -> None:
        user_id = uuid.uuid4()

        class FakeMemoryStore:
            def __init__(self) -> None:
                self._pool = MagicMock()

            async def log_consolidation_nudge_action(self, **kwargs) -> uuid.UUID:
                raise RuntimeError("audit insert failed")

            async def delete_skill_projection(self, skill_id: str) -> bool:
                self.deleted_projection = skill_id
                return True

        with patch("orchestrator.skills_store.delete_skill") as mock_delete:
            with patch("orchestrator.skills_projection.SkillProjectionStore") as mock_proj:
                result = await _apply_delete_action(
                    "skill-to-delete", cast(MemoryStore, FakeMemoryStore()), user_id
                )

        assert result["deleted"] is False
        assert result["reason"] == "audit log failed before delete: audit insert failed"
        mock_delete.assert_not_called()
        mock_proj.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_marks_pending_audit_failed_when_projection_delete_fails(self) -> None:
        user_id = uuid.uuid4()
        audit_id = uuid.uuid4()

        class FakeMemoryStore:
            def __init__(self) -> None:
                self._pool = MagicMock()
                self.status_updates: list[dict[str, object]] = []

            async def log_consolidation_nudge_action(self, **kwargs) -> uuid.UUID:
                self.logged_action = kwargs
                return audit_id

            async def update_consolidation_nudge_action_status(
                self,
                action_id: uuid.UUID,
                *,
                status: str,
                reason: str | None = None,
            ) -> None:
                self.status_updates.append(
                    {"action_id": action_id, "status": status, "reason": reason}
                )

        class FailingProjectionStore:
            def __init__(self, pool: object) -> None:
                self.pool = pool

            async def delete_projection(self, skill_id: str) -> bool:
                raise RuntimeError(f"projection unavailable for {skill_id}")

        with patch("orchestrator.skills_store.delete_skill") as mock_delete:
            with patch(
                "orchestrator.skills_projection.SkillProjectionStore",
                return_value=FailingProjectionStore(object()),
            ):
                store = FakeMemoryStore()
                result = await _apply_delete_action(
                    "skill-to-delete", cast(MemoryStore, store), user_id
                )

        assert result == {
            "deleted": False,
            "reason": "projection unavailable for skill-to-delete",
        }
        mock_delete.assert_called_once_with("skill-to-delete")
        assert store.logged_action["status"] == "pending"
        assert store.status_updates == [
            {
                "action_id": audit_id,
                "status": "failed",
                "reason": "delete failed: projection unavailable for skill-to-delete",
            }
        ]

    @pytest.mark.asyncio
    async def test_consolidation_log_query_filters_destructive_deletes(self) -> None:
        user_id = uuid.uuid4()
        row_id = uuid.uuid4()

        class FakePool:
            async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
                self.query = query
                self.args = args
                return [
                    {
                        "id": row_id,
                        "user_id": user_id,
                        "action_type": "delete",
                        "status": "failed",
                    }
                ]

        pool = FakePool()
        store = MemoryStore(cast(Any, pool), cast(Any, MagicMock()))
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 2, 1, tzinfo=timezone.utc)

        rows = await store.list_consolidation_nudge_actions(
            user_id=user_id,
            action_type="delete",
            status="failed",
            since=since,
            until=until,
            limit=50,
        )

        assert rows == [
            {
                "id": row_id,
                "user_id": user_id,
                "action_type": "delete",
                "status": "failed",
            }
        ]
        assert "FROM skill_consolidation_log" in pool.query
        assert pool.args == (user_id, "delete", "failed", since, until, 50)


class TestModelDrivenConsolidationFlow:
    @pytest.mark.asyncio
    async def test_model_called_with_prompt(self) -> None:
        user_id = uuid.uuid4()

        class FakeMemoryStore(MemoryStore):
            def __init__(self) -> None:
                pass

            async def get_user_conversation_count_since_last_nudge(self, uid: uuid.UUID) -> int:
                return 20

            async def get_total_conversation_count(self, uid: uuid.UUID) -> int:
                return 35

            async def get_autonomous_skill_candidates(
                self, min_skills: int
            ) -> list[dict[str, Any]]:
                return [
                    {
                        "skill_id": "autonomous-1",
                        "name": "Autonomous One",
                        "description": "First autonomous skill",
                        "content": "Content here",
                        "use_count": 5,
                        "last_used_at": datetime.now(timezone.utc),
                    }
                ]

            async def get_recent_memories_for_user(
                self, user_id: uuid.UUID, limit: int = 20
            ) -> list[dict[str, Any]]:
                return []

            async def record_consolidation_nudge_run(self, uid: uuid.UUID, count: int) -> None:
                pass

            async def log_consolidation_nudge_action(self, **kwargs) -> None:
                pass

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
                    content='{"actions": [{"type": "flag_stale", "skill_id": "autonomous-1", "reason": "low usage", "similarity": null}]}'
                )
            )
        ]

        ctx: dict[str, object] = {
            "store": FakeMemoryStore(),
            "settings": test_settings,
            "db_pool": None,
        }

        with patch("litellm.acompletion", AsyncMock(return_value=mock_response)):
            result = await run_consolidation_nudge_job(ctx, str(user_id))

            assert result["status"] == "ok"


class TestAuditLogRecording:
    def test_consolidation_nudge_results_are_serializable(self) -> None:
        result: ConsolidationNudgeResults = {
            "status": "ok",
            "user_id": str(uuid.uuid4()),
            "actions": [
                {
                    "action_type": "merge",
                    "skill_id": "skill-123",
                    "target_skill_id": "skill-456",
                    "reason": "model-driven merge",
                    "similarity": 0.92,
                    "status": "applied",
                },
                {
                    "action_type": "delete",
                    "skill_id": "skill-456",
                    "target_skill_id": None,
                    "reason": "merged_into skill-123",
                    "similarity": None,
                    "status": "applied",
                },
                {
                    "action_type": "flag_stale",
                    "skill_id": "skill-789",
                    "target_skill_id": None,
                    "reason": "not used in 30 days",
                    "similarity": None,
                    "status": "recorded",
                },
            ],
            "skills_reviewed": 3,
            "duplicates_found": 1,
            "duplicates_merged": 1,
            "stale_flagged": 1,
            "errors": [],
            "error_count": 0,
        }

        import json

        serialized = json.dumps(result)
        deserialized = json.loads(serialized)

        assert deserialized["status"] == "ok"
        assert len(deserialized["actions"]) == 3
        assert deserialized["duplicates_merged"] == 1
        assert deserialized["stale_flagged"] == 1
