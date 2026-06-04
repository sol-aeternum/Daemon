"""Unit tests for skill_manage protection enforcement: caller-aware modification rules."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.tools.skill_manage import (
    SkillManageTool,
)
from tests.test_skill_projection_sync import MockRecord


@pytest.fixture
def mock_db_pool() -> AsyncMock:
    pool = AsyncMock()
    return pool


@pytest.fixture
def tool(mock_db_pool: AsyncMock) -> SkillManageTool:
    return SkillManageTool(db_pool=mock_db_pool)


class TestProtectionDirectUserBypass:
    @pytest.mark.asyncio
    async def test_direct_user_can_patch_system_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "system-skill.md"
        skill_file.write_text(
            "---\nname: System Skill\ndescription: A system skill\nenabled: true\n---\n# System Skill\n\nOriginal content."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="system-skill",
            name="System Skill",
            description="A system skill",
            source_file_path=str(tmp_path / "system-skill.md"),
            source_hash="abc123",
            enabled=True,
            source_type="system",
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
                skill_id="system-skill",
                old_text="Original content.",
                new_text="Modified by direct user.",
                caller_autonomous=False,
            )

        parsed = json.loads(result)
        assert parsed["patched"] is True

    @pytest.mark.asyncio
    async def test_direct_user_can_delete_imported_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "imported-skill.md"
        skill_file.write_text(
            "---\nname: Imported Skill\ndescription: An imported skill\nenabled: true\n---\n# Imported Skill\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="imported-skill",
            name="Imported Skill",
            description="An imported skill",
            source_file_path=str(tmp_path / "imported-skill.md"),
            source_hash="abc123",
            enabled=True,
            source_type="imported",
            created_by="system",
            origin_url="https://example.com",
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
                skill_id="imported-skill",
                caller_autonomous=False,
            )

        parsed = json.loads(result)
        assert parsed["deleted"] is True


class TestProtectionAutonomousBlocked:
    @pytest.mark.asyncio
    async def test_autonomous_blocked_from_system_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "protected-system.md"
        skill_file.write_text(
            "---\nname: Protected System\ndescription: A protected system skill\nenabled: true\n---\n# Protected System\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="protected-system",
            name="Protected System",
            description="A protected system skill",
            source_file_path=str(tmp_path / "protected-system.md"),
            source_hash="abc123",
            enabled=True,
            source_type="system",
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
                skill_id="protected-system",
                old_text="Content.",
                new_text="Modified.",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "protected" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_autonomous_blocked_from_imported_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "protected-imported.md"
        skill_file.write_text(
            "---\nname: Protected Imported\ndescription: A protected imported skill\nenabled: true\n---\n# Protected Imported\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="protected-imported",
            name="Protected Imported",
            description="A protected imported skill",
            source_file_path=str(tmp_path / "protected-imported.md"),
            source_hash="abc123",
            enabled=True,
            source_type="imported",
            created_by="system",
            origin_url="https://example.com",
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
                action="delete",
                skill_id="protected-imported",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "protected" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_autonomous_blocked_from_manual_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "protected-manual.md"
        skill_file.write_text(
            "---\nname: Protected Manual\ndescription: A protected manual skill\nenabled: true\n---\n# Protected Manual\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="protected-manual",
            name="Protected Manual",
            description="A protected manual skill",
            source_file_path=str(tmp_path / "protected-manual.md"),
            source_hash="abc123",
            enabled=True,
            source_type="manual",
            created_by="user",
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
                skill_id="protected-manual",
                old_text="Content.",
                new_text="Modified.",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "protected" in parsed["error"].lower()


class TestProtectionAutonomousAllowed:
    @pytest.mark.asyncio
    async def test_autonomous_can_modify_own_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "my-autonomous-skill.md"
        skill_file.write_text(
            "---\nname: My Autonomous Skill\ndescription: My skill\nenabled: true\n---\n# My Autonomous Skill\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="my-autonomous-skill",
            name="My Autonomous Skill",
            description="My skill",
            source_file_path=str(tmp_path / "my-autonomous-skill.md"),
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
                skill_id="my-autonomous-skill",
                old_text="Content.",
                new_text="Modified by autonomous.",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert parsed["patched"] is True

    @pytest.mark.asyncio
    async def test_autonomous_can_modify_protected_with_flag(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "flagged-system.md"
        skill_file.write_text(
            "---\nname: Flagged System\ndescription: A flagged system skill\nenabled: true\n---\n# Flagged System\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="flagged-system",
            name="Flagged System",
            description="A flagged system skill",
            source_file_path=str(tmp_path / "flagged-system.md"),
            source_hash="abc123",
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="0.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
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
                skill_id="flagged-system",
                old_text="Content.",
                new_text="Modified with flag.",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert parsed["patched"] is True

    @pytest.mark.asyncio
    async def test_autonomous_can_modify_with_override_param(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "override-system.md"
        skill_file.write_text(
            "---\nname: Override System\ndescription: An override system skill\nenabled: true\n---\n# Override System\n\nContent."
        )
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="override-system",
            name="Override System",
            description="An override system skill",
            source_file_path=str(tmp_path / "override-system.md"),
            source_hash="abc123",
            enabled=True,
            source_type="system",
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
                skill_id="override-system",
                old_text="Content.",
                new_text="Modified with override.",
                caller_autonomous=True,
                allow_autonomous_edit=True,
            )

        parsed = json.loads(result)
        assert parsed["patched"] is True


class TestProtectionCreate:
    @pytest.mark.asyncio
    async def test_autonomous_cannot_create_system_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            result = await tool.execute(
                action="create",
                name="New System Skill",
                description="A new system skill",
                content="# New System Skill\n\nContent.",
                source_type="system",
                caller_autonomous=True,
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "autonomous context" in parsed["error"]

    @pytest.mark.asyncio
    async def test_autonomous_can_create_autonomous_skill(
        self, tool: SkillManageTool, tmp_path: Path
    ) -> None:
        mock_db_pool = AsyncMock()
        tool = SkillManageTool(db_pool=mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="new-autonomous-skill",
            name="New Autonomous Skill",
            description="A new autonomous skill",
            source_file_path=str(tmp_path / "new-autonomous-skill.md"),
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
                    name="New Autonomous Skill",
                    description="A new autonomous skill",
                    content="# New Autonomous Skill\n\nContent.",
                    source_type="autonomous",
                    caller_autonomous=True,
                )

        parsed = json.loads(result)
        assert parsed["created"] is True
        assert parsed["source_type"] == "autonomous"
