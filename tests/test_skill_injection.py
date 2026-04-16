from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.skills_store import (
    SKILL_INDEX_TOKEN_BUDGET,
    build_skill_index,
)


class TestSkillIndexL0:
    @pytest.mark.asyncio
    async def test_skill_index_contains_names_and_descriptions(
        self, tmp_path: Path
    ) -> None:
        skill_summary = {
            "id": "test-skill",
            "name": "Test Skill",
            "description": "A test skill description",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "Test Skill" in index
        assert "A test skill description" in index

    @pytest.mark.asyncio
    async def test_skill_index_excludes_full_content(self, tmp_path: Path) -> None:
        skill_summary = {
            "id": "test-skill",
            "name": "Test Skill",
            "description": "A test skill",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "imported",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }
        long_content = "x" * 2500

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert long_content not in index
        assert "x" * 100 not in index

    @pytest.mark.asyncio
    async def test_skill_index_provenance_tag_present(self, tmp_path: Path) -> None:
        skill_summary = {
            "id": "system-skill",
            "name": "System Skill",
            "description": "A system skill",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "system",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "[system]" in index

    @pytest.mark.asyncio
    async def test_skill_index_unknown_provenance_when_source_type_none(
        self, tmp_path: Path
    ) -> None:
        skill_summary = {
            "id": "bare-skill",
            "name": "Bare Skill",
            "description": "No provenance",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": None,
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "[unknown]" in index

    @pytest.mark.asyncio
    async def test_skill_index_empty_when_no_skills(self, tmp_path: Path) -> None:
        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch("orchestrator.skills_store.list_skills", return_value=[]):
                index = await build_skill_index(db_pool=None)

        assert index == ""

    @pytest.mark.asyncio
    async def test_skill_index_excludes_disabled_skills(self, tmp_path: Path) -> None:
        skill_summary = {
            "id": "disabled-skill",
            "name": "Disabled Skill",
            "description": "Should not appear",
            "enabled": False,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert index == ""

    @pytest.mark.asyncio
    async def test_skill_index_deterministic_truncation(self, tmp_path: Path) -> None:
        many_skills = []
        for i in range(25):
            many_skills.append(
                {
                    "id": f"skill-{i}",
                    "name": f"Skill {i}",
                    "description": f"Description for skill {i}",
                    "enabled": True,
                    "updated_at": "2026-03-09T00:00:00Z",
                    "source_type": "imported",
                    "allow_autonomous_edit": None,
                    "repo_version": None,
                    "local_version": None,
                    "pending_update": None,
                    "use_count": None,
                    "last_used_at": None,
                }
            )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=many_skills
            ):
                index1 = await build_skill_index(db_pool=None)
                index2 = await build_skill_index(db_pool=None)

        assert index1 == index2

        entries = [line for line in index1.split("\n") if line.startswith("- ")]
        assert len(entries) <= 20

    @pytest.mark.asyncio
    async def test_skill_index_respects_token_budget(self, tmp_path: Path) -> None:
        skill_with_long_description = {
            "id": "long-desc-skill",
            "name": "Long Desc Skill",
            "description": "This is a very long description " * 50,
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills",
                return_value=[skill_with_long_description],
            ):
                index = await build_skill_index(db_pool=None)

        assert len(index) <= SKILL_INDEX_TOKEN_BUDGET * 4

    @pytest.mark.asyncio
    async def test_skill_index_multiple_skills_sorted_deterministically(
        self, tmp_path: Path
    ) -> None:
        skills = [
            {
                "id": "zzz-skill",
                "name": "Zzz Skill",
                "description": "Last alphabetically",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "autonomous",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": None,
                "last_used_at": None,
            },
            {
                "id": "aaa-skill",
                "name": "Aaa Skill",
                "description": "First alphabetically",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "system",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": None,
                "last_used_at": None,
            },
            {
                "id": "mmm-skill",
                "name": "Mmm Skill",
                "description": "Middle alphabetically",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "imported",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": None,
                "last_used_at": None,
            },
        ]

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch("orchestrator.skills_store.list_skills", return_value=skills):
                index1 = await build_skill_index(db_pool=None)
                index2 = await build_skill_index(db_pool=None)

        assert index1 == index2
        assert "Aaa Skill" in index1
        assert "Mmm Skill" in index1
        assert "Zzz Skill" in index1
        assert "[system]" in index1
        assert "[imported]" in index1
        assert "[autonomous]" in index1

    @pytest.mark.asyncio
    async def test_skill_index_no_full_body_in_content(self, tmp_path: Path) -> None:
        skill_summary = {
            "id": "code-skill",
            "name": "Code Skill",
            "description": "A skill with code",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "def execute" not in index
        assert "print" not in index
        assert "hello world" not in index

    @pytest.mark.asyncio
    async def test_skill_index_header_present(self, tmp_path: Path) -> None:
        skill_summary = {
            "id": "header-test",
            "name": "Header Test",
            "description": "Check header",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "system",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "Skill Index (L0):" in index

    @pytest.mark.asyncio
    async def test_skill_index_sorted_by_use_count(self, tmp_path: Path) -> None:
        skills = [
            {
                "id": "low-use",
                "name": "Low Use Skill",
                "description": "Low usage",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "manual",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": 5,
                "last_used_at": None,
            },
            {
                "id": "high-use",
                "name": "High Use Skill",
                "description": "High usage",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "manual",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": 100,
                "last_used_at": None,
            },
            {
                "id": "medium-use",
                "name": "Medium Use Skill",
                "description": "Medium usage",
                "enabled": True,
                "updated_at": "2026-03-09T00:00:00Z",
                "source_type": "manual",
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": 50,
                "last_used_at": None,
            },
        ]

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch("orchestrator.skills_store.list_skills", return_value=skills):
                index = await build_skill_index(db_pool=None)

        high_pos = index.index("High Use Skill")
        med_pos = index.index("Medium Use Skill")
        low_pos = index.index("Low Use Skill")
        assert high_pos < med_pos < low_pos

    @pytest.mark.asyncio
    async def test_skill_index_native_chat_path_uses_l0_index(
        self, tmp_path: Path
    ) -> None:
        skill_summary = {
            "id": "native-chat-skill",
            "name": "Native Chat Skill",
            "description": "Should use L0 index",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "imported",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "Native Chat Skill" in index
        assert "[imported]" in index
        assert "Should use L0 index" in index

    @pytest.mark.asyncio
    async def test_skill_index_no_full_body_in_native_chat_path(
        self, tmp_path: Path
    ) -> None:
        skill_summary = {
            "id": "native-chat-body",
            "name": "Native Chat Body Skill",
            "description": "Has body content",
            "enabled": True,
            "updated_at": "2026-03-09T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": None,
            "last_used_at": None,
        }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_store.list_skills", return_value=[skill_summary]
            ):
                index = await build_skill_index(db_pool=None)

        assert "Native Chat Body Skill" in index
        body_content = "def very_long_body_content_that_should_not_appear" * 10
        assert body_content not in index
