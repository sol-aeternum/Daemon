"""Unit tests for skill upgrade: repo manifest tracking and safe upgrade path.

Tests cover:
- Unchanged system skills update silently
- Locally modified skills preserve local content and populate pending_update
- New repo skills are inserted as source_type='system'
- Removed repo skills are deprecated but not deleted

Architecture: snapshot-based with caller-provided repo_contents.
The upgrade service receives repo content as an external input (not read from local filesystem)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.skills_projection import compute_content_hash
from orchestrator.skills_upgrade import (
    MANIFEST_FILENAME,
    SNAPSHOT_DIRNAME,
    SkillManifest,
    SkillManifestEntry,
    SkillUpgradeService,
    _now_iso,
    _save_snapshot,
    _load_snapshot,
    load_manifest,
    save_manifest,
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


def _make_mock_db_pool() -> AsyncMock:
    return AsyncMock()


class TestSkillManifest:
    def test_empty_manifest_to_dict(self) -> None:
        manifest = SkillManifest()
        data = manifest.to_dict()
        assert data["version"] == 1
        assert data["skills"] == {}

    def test_manifest_to_dict_roundtrip(self) -> None:
        manifest = SkillManifest()
        manifest.skills["test-skill"] = SkillManifestEntry(
            repo_hash="abc123",
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        data = manifest.to_dict()
        restored = SkillManifest.from_dict(data)
        assert "test-skill" in restored.skills
        assert restored.skills["test-skill"].repo_hash == "abc123"
        assert restored.skills["test-skill"].repo_version == "1.0.0"


class TestManifestLoadSave:
    def test_load_returns_empty_when_no_manifest(self, tmp_path: Path) -> None:
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            manifest = load_manifest()
        assert manifest.version == 1
        assert manifest.skills == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            manifest = SkillManifest()
            manifest.skills["skill-a"] = SkillManifestEntry(
                repo_hash="hash-a",
                repo_version="1.0.0",
                local_version="1.0.0",
                updated_at=_now_iso(),
            )
            save_manifest(manifest)
            loaded = load_manifest()
        assert "skill-a" in loaded.skills
        assert loaded.skills["skill-a"].repo_hash == "hash-a"

    def test_load_returns_empty_on_invalid_json(self, tmp_path: Path) -> None:
        manifest_file = tmp_path / MANIFEST_FILENAME
        manifest_file.write_text("not valid json", encoding="utf-8")
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            manifest = load_manifest()
        assert manifest.version == 1
        assert manifest.skills == {}


class TestUpgradeServiceNewSkill:
    @pytest.mark.asyncio
    async def test_new_repo_skill_inserted_as_system(self, tmp_path: Path) -> None:
        repo_content = (
            "---\nname: New Repo Skill\ndescription: A new repo skill\n"
            "enabled: true\nrepo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# New Repo Skill\n\nContent here."
        )
        local_content = repo_content
        skill_file = tmp_path / "new-repo-skill.md"
        skill_file.write_text(local_content)
        repo_hash = compute_content_hash(repo_content)
        local_hash = compute_content_hash(local_content)
        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="new-repo-skill",
            name="New Repo Skill",
            description="A new repo skill",
            source_file_path=str(tmp_path / "new-repo-skill.md"),
            source_hash=local_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                result = await service.sync_repo_skills(
                    {"new-repo-skill": repo_content}
                )

        assert result.total_inserts == 1
        insert_action = next(a for a in result.actions if a.action == "insert")
        assert insert_action.skill_id == "new-repo-skill"
        assert insert_action.success is True
        snapshot_path = tmp_path / SNAPSHOT_DIRNAME / "new-repo-skill.md"
        assert snapshot_path.exists()


class TestUpgradeServiceUnchanged:
    @pytest.mark.asyncio
    async def test_unchanged_skill_reports_unchanged(self, tmp_path: Path) -> None:
        content = (
            "---\nname: Stable Skill\ndescription: Stable\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Stable Skill\n\nContent."
        )
        skill_file = tmp_path / "stable-skill.md"
        skill_file.write_text(content)
        current_hash = compute_content_hash(content)

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "stable-skill.md"
        snapshot_file.write_text(content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="stable-skill",
            name="Stable Skill",
            description="Stable",
            source_file_path=str(tmp_path / "stable-skill.md"),
            source_hash=current_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        manifest = SkillManifest()
        manifest.skills["stable-skill"] = SkillManifestEntry(
            repo_hash=current_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            service = SkillUpgradeService(store)
            result = await service.sync_repo_skills({"stable-skill": content})

        assert result.total_unchanged == 1
        unchanged_action = next(a for a in result.actions if a.action == "unchanged")
        assert unchanged_action.skill_id == "stable-skill"
        assert unchanged_action.success is True


class TestUpgradeServiceSilentUpdate:
    @pytest.mark.asyncio
    async def test_repo_update_on_unmodified_skill_silent_update(
        self, tmp_path: Path
    ) -> None:
        old_content = (
            "---\nname: Upgradable Skill\ndescription: Upgradable\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Upgradable Skill\n\nOld content."
        )
        new_content = (
            "---\nname: Upgradable Skill\ndescription: Upgradable\nenabled: true\n"
            "repo_version: 2.0.0\nlocal_version: 2.0.0\n---\n"
            "# Upgradable Skill\n\nNew content from repo."
        )
        old_hash = compute_content_hash(old_content)
        new_hash = compute_content_hash(new_content)

        skill_file = tmp_path / "upgradable-skill.md"
        skill_file.write_text(old_content)

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "upgradable-skill.md"
        snapshot_file.write_text(old_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="upgradable-skill",
            name="Upgradable Skill",
            description="Upgradable",
            source_file_path=str(tmp_path / "upgradable-skill.md"),
            source_hash=old_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["upgradable-skill"] = SkillManifestEntry(
            repo_hash=old_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                result = await service.sync_repo_skills(
                    {"upgradable-skill": new_content}
                )

        assert result.total_silent_updates == 1
        silent_action = next(a for a in result.actions if a.action == "silent_update")
        assert silent_action.skill_id == "upgradable-skill"
        assert silent_action.success is True
        updated_file = skill_file.read_text()
        assert "New content from repo" in updated_file


class TestUpgradeServicePendingUpdate:
    @pytest.mark.asyncio
    async def test_locally_modified_skill_gets_pending_update(
        self, tmp_path: Path
    ) -> None:
        old_content = (
            "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Modified Skill\n\nOriginal content."
        )
        new_repo_content = (
            "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
            "repo_version: 2.0.0\nlocal_version: 1.0.0\n---\n"
            "# Modified Skill\n\nNew content from repo."
        )
        user_modified_content = (
            "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
            "repo_version: 2.0.0\nlocal_version: 1.1.0\n---\n"
            "# Modified Skill\n\nUser modified content."
        )
        old_hash = compute_content_hash(old_content)
        user_hash = compute_content_hash(user_modified_content)

        skill_file = tmp_path / "modified-skill.md"
        skill_file.write_text(user_modified_content)

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "modified-skill.md"
        snapshot_file.write_text(old_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="modified-skill",
            name="Modified Skill",
            description="Modified",
            source_file_path=str(tmp_path / "modified-skill.md"),
            source_hash=user_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.1.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["modified-skill"] = SkillManifestEntry(
            repo_hash=old_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                result = await service.sync_repo_skills(
                    {"modified-skill": new_repo_content}
                )

        assert result.total_pending_updates == 1
        pending_action = next(a for a in result.actions if a.action == "pending_update")
        assert pending_action.skill_id == "modified-skill"
        assert pending_action.success is True
        assert pending_action.details is not None
        assert pending_action.details.get("repo_version") == "2.0.0"
        local_content = skill_file.read_text()
        assert "User modified content" in local_content


class TestUpgradeServiceDeprecate:
    @pytest.mark.asyncio
    async def test_empty_repo_contents_no_ops(self, tmp_path: Path) -> None:
        """When repo provides no content, fail-safe no-op (do NOT deprecate tracked skills)."""
        local_content = (
            "---\nname: Existing Skill\ndescription: Exists\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Existing Skill\n\nThis skill exists."
        )
        skill_file = tmp_path / "existing-skill.md"
        skill_file.write_text(local_content)
        local_hash = compute_content_hash(local_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="existing-skill",
            name="Existing Skill",
            description="Exists",
            source_file_path=str(tmp_path / "existing-skill.md"),
            source_hash=local_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["existing-skill"] = SkillManifestEntry(
            repo_hash=local_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            service = SkillUpgradeService(store)
            result = await service.sync_repo_skills({})

        assert result.total_deprecated == 0
        assert result.actions == []

    @pytest.mark.asyncio
    async def test_removed_repo_skill_deprecated_not_deleted(
        self, tmp_path: Path
    ) -> None:
        """When a specific skill is removed from repo (repo still has other skills), deprecate it."""
        other_skill_content = (
            "---\nname: Other Skill\ndescription: Other\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Other Skill\n\nOther skill content."
        )
        local_content = (
            "---\nname: Removed Skill\ndescription: Removed\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Removed Skill\n\nThis was a repo skill."
        )
        skill_file = tmp_path / "removed-skill.md"
        skill_file.write_text(local_content)
        local_hash = compute_content_hash(local_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="removed-skill",
            name="Removed Skill",
            description="Removed",
            source_file_path=str(tmp_path / "removed-skill.md"),
            source_hash=local_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["removed-skill"] = SkillManifestEntry(
            repo_hash=local_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            service = SkillUpgradeService(store)
            result = await service.sync_repo_skills(
                {"other-skill": other_skill_content}
            )

        assert result.total_deprecated == 1
        deprecate_action = next(a for a in result.actions if a.action == "deprecated")
        assert deprecate_action.skill_id == "removed-skill"
        assert deprecate_action.success is True
        pool.execute.assert_called()
        assert skill_file.exists()
        assert "This was a repo skill" in skill_file.read_text()


class TestApplyPendingUpdate:
    @pytest.mark.asyncio
    async def test_apply_pending_update_overwrites_file(self, tmp_path: Path) -> None:
        original_content = (
            "---\nname: Pending Skill\ndescription: Pending\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Pending Skill\n\nLocal content."
        )
        pending_content = (
            "---\nname: Pending Skill\ndescription: Pending\nenabled: true\n"
            "repo_version: 2.0.0\nlocal_version: 1.0.0\n---\n"
            "# Pending Skill\n\nRepo content."
        )
        skill_file = tmp_path / "pending-skill.md"
        skill_file.write_text(original_content)

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "pending-skill.md"
        snapshot_file.write_text(original_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="pending-skill",
            name="Pending Skill",
            description="Pending",
            source_file_path=str(tmp_path / "pending-skill.md"),
            source_hash=compute_content_hash(original_content),
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.1.0",
            pending_update={
                "repo_hash": compute_content_hash(pending_content),
                "repo_version": "2.0.0",
                "repo_content": pending_content,
                "repo_name": "Pending Skill",
                "repo_description": "Pending",
                "updated_at": _now_iso(),
            },
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["pending-skill"] = SkillManifestEntry(
            repo_hash=compute_content_hash(pending_content),
            repo_version="2.0.0",
            local_version="1.1.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                action = await service.apply_pending_update("pending-skill")

        assert action.action == "applied"
        assert action.success is True
        updated_content = skill_file.read_text(encoding="utf-8")
        assert "Repo content." in updated_content
        assert "2.0.0" in updated_content


class TestDismissPendingUpdate:
    @pytest.mark.asyncio
    async def test_dismiss_pending_update_clears_pending(self, tmp_path: Path) -> None:
        original_content = (
            "---\nname: Dismiss Skill\ndescription: Dismiss\nenabled: true\n"
            "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
            "# Dismiss Skill\n\nLocal content."
        )
        skill_file = tmp_path / "dismiss-skill.md"
        skill_file.write_text(original_content)

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "dismiss-skill.md"
        snapshot_file.write_text(original_content)

        pool = _make_mock_db_pool()
        pool.fetchrow.return_value = MockRecord(
            skill_id="dismiss-skill",
            name="Dismiss Skill",
            description="Dismiss",
            source_file_path=str(tmp_path / "dismiss-skill.md"),
            source_hash=compute_content_hash(original_content),
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update={
                "repo_hash": "new-hash",
                "repo_version": "2.0.0",
                "repo_content": "New content.",
                "repo_name": "Dismiss Skill",
                "repo_description": "Dismiss",
                "updated_at": _now_iso(),
            },
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        pool.fetch.return_value = []

        manifest = SkillManifest()
        manifest.skills["dismiss-skill"] = SkillManifestEntry(
            repo_hash="new-hash",
            repo_version="2.0.0",
            local_version="1.0.0",
            updated_at=_now_iso(),
        )
        manifest_path = tmp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        from orchestrator.skills_projection import SkillProjectionStore

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                action = await service.dismiss_pending_update("dismiss-skill")

        assert action.action == "dismissed"
        assert action.success is True
        local_content = skill_file.read_text(encoding="utf-8")
        assert "Local content." in local_content


class TestLoadRepoContents:
    def test_load_repo_contents_returns_empty_when_dir_missing(
        self, tmp_path: Path
    ) -> None:
        with patch(
            "orchestrator.skills_upgrade.REPO_SKILLS_DIR",
            tmp_path / "nonexistent",
        ):
            from orchestrator.skills_upgrade import load_repo_contents

            result = load_repo_contents()
        assert result == {}

    def test_load_repo_contents_loads_valid_skills(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo_skills"
        repo_dir.mkdir()
        (repo_dir / "test-skill.md").write_text(
            "---\nname: Test Skill\ndescription: desc\nenabled: true\n---\n# Test\nContent."
        )
        (repo_dir / "another-skill.md").write_text(
            "---\nname: Another\ndescription: desc2\nenabled: true\n---\n# Another\nContent."
        )
        with patch(
            "orchestrator.skills_upgrade.REPO_SKILLS_DIR",
            repo_dir,
        ):
            from orchestrator.skills_upgrade import load_repo_contents

            result = load_repo_contents()
        assert "test-skill" in result
        assert "another-skill" in result
        assert result["test-skill"].startswith("---\nname: Test Skill")

    def test_load_repo_contents_skips_files_without_frontmatter(
        self, tmp_path: Path
    ) -> None:
        repo_dir = tmp_path / "repo_skills"
        repo_dir.mkdir()
        (repo_dir / "valid-skill.md").write_text(
            "---\nname: Valid\ndescription: d\nenabled: true\n---\n# Valid\nContent."
        )
        (repo_dir / "invalid-skill.md").write_text("No frontmatter\nContent.")
        with patch(
            "orchestrator.skills_upgrade.REPO_SKILLS_DIR",
            repo_dir,
        ):
            from orchestrator.skills_upgrade import load_repo_contents

            result = load_repo_contents()
        assert "valid-skill" in result
        assert "invalid-skill" not in result
