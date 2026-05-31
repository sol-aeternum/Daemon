"""Unit tests for skill_manage tool: create, list, view actions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.tools.skill_manage import (
    SkillManageTool,
    _check_modification_allowed,
)


class MockRecord:
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


@pytest.fixture
def mock_db_pool() -> AsyncMock:
    pool = AsyncMock()
    return pool


@pytest.fixture
def tool(mock_db_pool: AsyncMock) -> SkillManageTool:
    return SkillManageTool(db_pool=mock_db_pool)


class TestCheckModificationAllowed:
    def test_direct_user_bypass(self) -> None:
        projection = {"source_type": "system", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=False)
        assert allowed is True
        assert reason == ""

    def test_autonomous_can_modify_autonomous_skill(self) -> None:
        projection = {"source_type": "autonomous", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is True

    def test_autonomous_cannot_modify_system_skill_without_flag(self) -> None:
        projection = {"source_type": "system", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is False
        assert "protected" in reason

    def test_autonomous_can_modify_system_skill_with_flag(self) -> None:
        projection = {"source_type": "system", "allow_autonomous_edit": True}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is True

    def test_autonomous_can_modify_with_override_flag(self) -> None:
        projection = {"source_type": "system", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(
            projection, caller_autonomous=True, allow_autonomous_edit=True
        )
        assert allowed is True

    def test_autonomous_cannot_modify_imported_skill(self) -> None:
        projection = {"source_type": "imported", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is False

    def test_autonomous_cannot_modify_manual_skill(self) -> None:
        projection = {"source_type": "manual", "allow_autonomous_edit": False}
        allowed, reason = _check_modification_allowed(projection, caller_autonomous=True)
        assert allowed is False

    def test_no_projection_fails_closed_for_autonomous(self) -> None:
        allowed, reason = _check_modification_allowed(None, caller_autonomous=True)
        assert allowed is False
        assert "no projection row" in reason


class TestSkillManageToolList:
    @pytest.mark.asyncio
    async def test_list_returns_l0_safe_metadata(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetch.return_value = []

        with patch("orchestrator.tools.skill_manage.list_skills") as mock_list:
            mock_list.return_value = [
                {
                    "id": "test-skill",
                    "name": "Test Skill",
                    "description": "A test skill",
                    "enabled": True,
                    "updated_at": "2024-01-01T00:00:00Z",
                    "source_type": None,
                    "allow_autonomous_edit": None,
                    "repo_version": None,
                    "local_version": None,
                    "use_count": None,
                    "last_used_at": None,
                }
            ]
            result = await tool.execute(action="list")

        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "test-skill"
        assert "content" not in parsed[0]
        assert "Skill content" not in result

    @pytest.mark.asyncio
    async def test_list_enhances_with_projection_data(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetch.return_value = [
            MockRecord(
                skill_id="test-skill",
                name="Test Skill",
                description="A test skill",
                source_file_path=str(tmp_path / "test-skill.md"),
                source_hash="abc123",
                enabled=True,
                source_type="autonomous",
                created_by="system",
                origin_url="",
                embedding=None,
                repo_version="1.0.0",
                local_version="1.0.0",
                pending_update=None,
                allow_autonomous_edit=True,
                trigger_conditions="",
                complexity_origin=0,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
                last_used_at=datetime.now(),
                use_count=5,
            )
        ]

        with patch("orchestrator.tools.skill_manage.list_skills") as mock_list:
            mock_list.return_value = [
                {
                    "id": "test-skill",
                    "name": "Test Skill",
                    "description": "A test skill",
                    "enabled": True,
                    "updated_at": "2024-01-01T00:00:00Z",
                    "source_type": None,
                    "allow_autonomous_edit": None,
                    "repo_version": None,
                    "local_version": None,
                    "use_count": None,
                    "last_used_at": None,
                }
            ]
            result = await tool.execute(action="list")

        parsed = json.loads(result)
        assert parsed[0]["source_type"] == "autonomous"
        assert parsed[0]["allow_autonomous_edit"] is True
        assert parsed[0]["use_count"] == 5


class TestSkillManageToolView:
    @pytest.mark.asyncio
    async def test_view_returns_full_content(self, tool: SkillManageTool, tmp_path: Path) -> None:
        skill_file = tmp_path / "view-test.md"
        skill_file.write_text(
            "---\nname: View Test\ndescription: Testing view\nenabled: true\n---\n# View Test\n\nThis is the content."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(action="view", skill_id="view-test")

        parsed = json.loads(result)
        assert parsed["id"] == "view-test"
        assert "This is the content" in parsed["content"]
        assert "description" in parsed

    @pytest.mark.asyncio
    async def test_view_increments_usage(self, tool: SkillManageTool, tmp_path: Path) -> None:
        skill_file = tmp_path / "usage-test.md"
        skill_file.write_text(
            "---\nname: Usage Test\ndescription: Testing usage\nenabled: true\n---\n# Usage Test\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            await tool.execute(action="view", skill_id="usage-test")

        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args[0]
        assert "use_count" in call_args[0]

    @pytest.mark.asyncio
    async def test_view_nonexistent_returns_error(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(action="view", skill_id="nonexistent")

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_view_requires_skill_id(self, tool: SkillManageTool) -> None:
        result = await tool.execute(action="view")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "skill_id" in parsed["error"]


class TestSkillManageToolCreate:
    @pytest.mark.asyncio
    async def test_create_skill_success(self, tool: SkillManageTool, tmp_path: Path) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="new-skill",
            name="New Skill",
            description="A new skill",
            source_file_path=str(tmp_path / "new-skill.md"),
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
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                result = await tool.execute(
                    action="create",
                    name="New Skill",
                    description="A new skill",
                    content="# New Skill\n\nSkill content.",
                    source_type="autonomous",
                )

        parsed = json.loads(result)
        assert parsed["created"] is True
        assert parsed["skill_id"] == "new-skill"

    @pytest.mark.asyncio
    async def test_create_autonomous_skill_by_autonomous_caller_blocked(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="create",
                name="Protected Skill",
                description="A protected skill",
                content="# Protected Skill\n\nContent.",
                source_type="system",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "autonomous context" in parsed["error"]

    @pytest.mark.asyncio
    async def test_create_requires_name(self, tool: SkillManageTool) -> None:
        result = await tool.execute(action="create")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "name" in parsed["error"]


class TestSkillManageToolPatch:
    @pytest.mark.asyncio
    async def test_patch_success(self, tool: SkillManageTool, tmp_path: Path) -> None:
        skill_file = tmp_path / "patch-test.md"
        skill_file.write_text(
            "---\nname: Patch Test\ndescription: Testing patch\nenabled: true\n---\n# Patch Test\n\nOld content here."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="patch-test",
            name="Patch Test",
            description="Testing patch",
            source_file_path=str(tmp_path / "patch-test.md"),
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
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                result = await tool.execute(
                    action="patch",
                    skill_id="patch-test",
                    old_text="Old content here.",
                    new_text="New content here.",
                )

        parsed = json.loads(result)
        assert parsed["patched"] is True
        assert parsed["replaced"] == "Old content here."
        assert parsed["with"] == "New content here."

        content = skill_file.read_text()
        assert "New content here" in content
        assert "Old content here" not in content

    @pytest.mark.asyncio
    async def test_patch_requires_old_text(self, tool: SkillManageTool, tmp_path: Path) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)

        result = await tool.execute(
            action="patch",
            skill_id="some-skill",
            new_text="new",
        )
        parsed = json.loads(result)
        assert "error" in parsed
        assert "old_text" in parsed["error"]

    @pytest.mark.asyncio
    async def test_patch_old_text_not_found(self, tool: SkillManageTool, tmp_path: Path) -> None:
        skill_file = tmp_path / "notfound-test.md"
        skill_file.write_text(
            "---\nname: NotFound Test\ndescription: Testing\nenabled: true\n---\n# NotFound Test\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="notfound-test",
            name="NotFound Test",
            description="Testing",
            source_file_path=str(tmp_path / "notfound-test.md"),
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
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="patch",
                skill_id="notfound-test",
                old_text="nonexistent text",
                new_text="replacement",
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"]


class TestSkillManageToolDelete:
    @pytest.mark.asyncio
    async def test_delete_success(self, tool: SkillManageTool, tmp_path: Path) -> None:
        skill_file = tmp_path / "delete-test.md"
        skill_file.write_text(
            "---\nname: Delete Test\ndescription: Testing delete\nenabled: true\n---\n# Delete Test\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="delete-test",
            name="Delete Test",
            description="Testing delete",
            source_file_path=str(tmp_path / "delete-test.md"),
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
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )
        mock_db_pool.execute.return_value = "DELETE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="delete",
                skill_id="delete-test",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert parsed["deleted"] is True
        assert not skill_file.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_error(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = None

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="delete",
                skill_id="nonexistent",
            )

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_delete_requires_skill_id(self, tool: SkillManageTool) -> None:
        result = await tool.execute(action="delete")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "skill_id" in parsed["error"]


class TestSkillManageToolNoDbPool:
    @pytest.mark.asyncio
    async def test_tool_usable_without_db_pool(self, tmp_path: Path) -> None:
        tool = SkillManageTool(db_pool=None)
        assert tool._projection_store is None
        assert tool._sync_service is None
        result = await tool.execute(action="list")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_view_works_without_db_pool(self, tmp_path: Path) -> None:
        skill_file = tmp_path / "no-db-skill.md"
        skill_file.write_text(
            "---\nname: No DB Skill\ndescription: Testing without DB\nenabled: true\n---\n# No DB Skill\n\nContent."
        )
        tool = SkillManageTool(db_pool=None)
        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(action="view", skill_id="no-db-skill")
        parsed = json.loads(result)
        assert parsed["id"] == "no-db-skill"
        assert "Content" in parsed["content"]


class TestSkillManageToolRegistration:
    def test_skill_manage_registered_when_db_pool_is_none(self) -> None:
        from orchestrator.tools.builtin import create_default_registry

        registry = create_default_registry(db_pool=None)
        assert "skill_manage" in registry

    def test_skill_manage_registered_when_db_pool_provided(self) -> None:
        from orchestrator.tools.builtin import create_default_registry

        mock_pool = AsyncMock()
        registry = create_default_registry(db_pool=mock_pool)
        assert "skill_manage" in registry


class TestSkillManageToolMissingProjectionProtection:
    @pytest.mark.asyncio
    async def test_autonomous_patch_blocked_when_projection_missing_and_sync_fails(
        self, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "no-projection-skill.md"
        skill_file.write_text(
            "---\nname: No Projection Skill\ndescription: Testing protection\nenabled: true\n---\n# No Projection Skill\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = None

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch.object(
                tool._sync_service, "sync_skill", return_value=MagicMock(success=False)
            ):
                result = await tool.execute(
                    action="patch",
                    skill_id="no-projection-skill",
                    old_text="Content.",
                    new_text="Modified.",
                    caller_autonomous=True,
                )

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_autonomous_delete_blocked_when_projection_missing_and_sync_fails(
        self, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "no-projection-delete.md"
        skill_file.write_text(
            "---\nname: No Projection Delete\ndescription: Testing protection\nenabled: true\n---\n# No Projection Delete\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = None

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch.object(
                tool._sync_service, "sync_skill", return_value=MagicMock(success=False)
            ):
                result = await tool.execute(
                    action="delete",
                    skill_id="no-projection-delete",
                    caller_autonomous=True,
                )

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_autonomous_patch_allowed_when_sync_projection_shows_autonomous(
        self, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "sync-to-autonomous.md"
        skill_file.write_text(
            "---\nname: Sync To Autonomous\ndescription: Testing sync\nenabled: true\n---\n# Sync To Autonomous\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = None

        from orchestrator.skills_sync import SyncResult

        synced_projection = MockRecord(
            skill_id="sync-to-autonomous",
            name="Sync To Autonomous",
            description="Testing sync",
            source_file_path=str(tmp_path / "sync-to-autonomous.md"),
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
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )

        async def mock_sync_skill(skill_id, source_type="manual"):
            return SyncResult(skill_id=skill_id, action="upsert", success=True)

        async def mock_get_projection(skill_id):
            return dict(synced_projection)

        with patch.object(tool._sync_service, "sync_skill", mock_sync_skill):
            with patch.object(tool._projection_store, "get_projection", mock_get_projection):
                with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
                    result = await tool.execute(
                        action="patch",
                        skill_id="sync-to-autonomous",
                        old_text="Content.",
                        new_text="Modified by autonomous.",
                        caller_autonomous=True,
                    )

        parsed = json.loads(result)
        if "error" in parsed:
            print(f"ERROR: {parsed['error']}")
        assert parsed.get("patched") is True, f"Expected patched=True, got: {parsed}"
