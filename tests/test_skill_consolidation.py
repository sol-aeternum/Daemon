from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.config import Settings
from orchestrator.memory.store import MemoryStore
from orchestrator.redis_account import account_prefix
from orchestrator.services.skills import SkillError
from tests.qualified_compute import install_qualified_compute
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
        assert key == f"{account_prefix(user_id)}:consolidation_nudge"
        assert str(user_id) not in key

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


class FakeSkillStore:
    """The account-owned store calls consolidation makes, recorded."""

    def __init__(self, candidates: list[dict[str, Any]] | None = None) -> None:
        self.candidates = candidates or []
        self.candidate_calls: list[tuple[uuid.UUID, int]] = []
        self.merge = AsyncMock(
            return_value={"id": str(uuid.uuid4()), "revision": 2, "name": "", "description": "",
                          "body": ""}
        )  # fmt: skip
        self.delete = AsyncMock(return_value=None)

    async def list_autonomous_candidates(
        self, user_id: uuid.UUID, *, limit: int
    ) -> list[dict[str, Any]]:
        self.candidate_calls.append((user_id, limit))
        return self.candidates


def _candidate(slug: str, name: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "name": name,
        "description": f"{name} description",
        "body": "Content here",
        "use_count": 5,
        "last_used_at": datetime.now(timezone.utc).isoformat(),
    }


def _use_skill_store(monkeypatch: pytest.MonkeyPatch, skill_store: FakeSkillStore) -> None:
    monkeypatch.setattr(
        "orchestrator.services.skills.create_skill_store", lambda *_, **__: skill_store
    )
    monkeypatch.setattr(
        "orchestrator.services.skill_index.embed_skill", AsyncMock(return_value=True)
    )


@pytest.mark.asyncio
async def test_job_finds_users_above_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    skill_store = FakeSkillStore()
    _use_skill_store(monkeypatch, skill_store)

    class FakeMemoryStore(MemoryStore):
        def __init__(self) -> None:
            pass

        async def get_users_with_skill_candidates(self, interval: int) -> list[uuid.UUID]:
            return [user_id]

        async def get_user_conversation_count_since_last_nudge(self, uid: uuid.UUID) -> int:
            return 20

        async def get_total_conversation_count(self, uid: uuid.UUID) -> int:
            return 35

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
    # Candidates are read for that account only, never across accounts.
    assert skill_store.candidate_calls == [(user_id, 9)]


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
    async def test_merge_runs_through_the_skill_store_as_consolidation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_id = uuid.uuid4()
        skill_store = FakeSkillStore()
        _use_skill_store(monkeypatch, skill_store)

        refusal = await _apply_merge_action("skill-keep", "skill-absorb", skill_store, user_id)

        assert refusal is None
        skill_store.merge.assert_awaited_once_with(
            user_id, "skill-keep", ["skill-absorb"], actor="consolidation"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (SkillError("autonomous_edit_not_granted"), "not_granted"),
            (SkillError("shipped_read_only"), "not_granted"),
            (SkillError("not_found"), "merge_failed"),
            (RuntimeError("database down: secret detail"), "merge_failed"),
        ],
    )
    async def test_merge_failure_is_a_fixed_code(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
    ) -> None:
        skill_store = FakeSkillStore()
        skill_store.merge = AsyncMock(side_effect=error)
        _use_skill_store(monkeypatch, skill_store)

        assert await _apply_merge_action("a", "b", skill_store, uuid.uuid4()) == code


class _AuditStore:
    def __init__(self, audit_id: uuid.UUID, events: list[tuple[str, str]]) -> None:
        self.audit_id = audit_id
        self.events = events
        self.logged_action: dict[str, Any] = {}
        self.status_updates: list[dict[str, object]] = []

    async def log_consolidation_nudge_action(self, **kwargs: Any) -> uuid.UUID:
        self.events.append(("audit", kwargs["status"]))
        self.logged_action = kwargs
        return self.audit_id

    async def update_consolidation_nudge_action_status(
        self,
        action_id: uuid.UUID,
        *,
        status: str,
        reason: str | None = None,
    ) -> None:
        self.events.append(("audit_update", status))
        self.status_updates.append({"action_id": action_id, "status": status, "reason": reason})


class TestApplyDeleteAction:
    @pytest.mark.asyncio
    async def test_delete_writes_pending_audit_before_destructive_actions(self) -> None:
        user_id = uuid.uuid4()
        audit_id = uuid.uuid4()
        events: list[tuple[str, str]] = []
        skill = _candidate("skill-to-delete", "Skill To Delete")
        skill_store = FakeSkillStore()

        async def delete(owner: uuid.UUID, slug: str, *, actor: str) -> None:
            assert (owner, actor) == (user_id, "consolidation")
            events.append(("skill_delete", slug))

        skill_store.delete = AsyncMock(side_effect=delete)
        store = _AuditStore(audit_id, events)

        result = await _apply_delete_action(skill, cast(MemoryStore, store), skill_store, user_id)

        assert result == {"deleted": True, "reason": "model_delete", "audit_id": audit_id}
        assert events == [
            ("audit", "pending"),
            ("skill_delete", "skill-to-delete"),
            ("audit_update", "applied"),
        ]
        # The audit row names the skill by internal id and a reason code only.
        assert store.logged_action["skill_id"] == uuid.UUID(skill["id"])
        assert store.logged_action["reason"] == "model_delete"
        assert "skill_name" not in store.logged_action
        assert "skill_description" not in store.logged_action
        assert store.status_updates == [
            {"action_id": audit_id, "status": "applied", "reason": None}
        ]

    @pytest.mark.asyncio
    async def test_delete_does_not_run_when_pending_audit_insert_fails(self) -> None:
        class FailingAuditStore:
            async def log_consolidation_nudge_action(self, **kwargs: Any) -> uuid.UUID:
                raise RuntimeError("audit insert failed")

        skill_store = FakeSkillStore()
        result = await _apply_delete_action(
            _candidate("skill-to-delete", "Skill To Delete"),
            cast(MemoryStore, FailingAuditStore()),
            skill_store,
            uuid.uuid4(),
        )

        assert result == {"deleted": False, "reason": "audit_failed"}
        skill_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (SkillError("autonomous_edit_not_granted"), "not_granted"),
            (RuntimeError("store unavailable for skill-to-delete"), "delete_failed"),
        ],
    )
    async def test_delete_marks_pending_audit_failed_when_the_delete_fails(
        self, error: Exception, code: str
    ) -> None:
        audit_id = uuid.uuid4()
        skill_store = FakeSkillStore()
        skill_store.delete = AsyncMock(side_effect=error)
        store = _AuditStore(audit_id, [])

        result = await _apply_delete_action(
            _candidate("skill-to-delete", "Skill To Delete"),
            cast(MemoryStore, store),
            skill_store,
            uuid.uuid4(),
        )

        assert result == {"deleted": False, "reason": code, "audit_id": audit_id}
        assert store.logged_action["status"] == "pending"
        # The failure is recorded as a code, never the exception text.
        assert store.status_updates == [{"action_id": audit_id, "status": "failed", "reason": code}]

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
    async def test_model_called_with_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user_id = uuid.uuid4()
        install_qualified_compute(monkeypatch)
        candidate = _candidate("autonomous-1", "Autonomous One")
        _use_skill_store(monkeypatch, FakeSkillStore([candidate]))
        logged: list[dict[str, Any]] = []

        class FakeMemoryStore(MemoryStore):
            def __init__(self) -> None:
                pass

            async def get_user_conversation_count_since_last_nudge(self, uid: uuid.UUID) -> int:
                return 20

            async def get_total_conversation_count(self, uid: uuid.UUID) -> int:
                return 35

            async def get_recent_memories_for_user(
                self, user_id: uuid.UUID, limit: int = 20
            ) -> list[dict[str, Any]]:
                return []

            async def record_consolidation_nudge_run(self, uid: uuid.UUID, count: int) -> None:
                pass

            async def log_consolidation_nudge_action(self, **kwargs) -> None:
                logged.append(kwargs)

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
            "db_pool": object(),
        }

        completion = AsyncMock(return_value=mock_response)
        with patch("orchestrator.worker.jobs.guarded_completion", completion):
            result = await run_consolidation_nudge_job(ctx, str(user_id))

        assert result["status"] == "ok"
        assert result["stale_flagged"] == 1
        prompt = completion.call_args.kwargs["messages"][1]["content"]
        assert "[autonomous-1] Autonomous One" in prompt
        # The model's free-text reason is not stored; the row holds an id and a code.
        assert [(row["skill_id"], row["reason"], row["status"]) for row in logged] == [
            (uuid.UUID(candidate["id"]), "model_stale", "recorded")
        ]
        assert "low usage" not in repr(logged)


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
