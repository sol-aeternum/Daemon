"""Unit tests for skill projection sync: backfill, sync after file changes, delete sync, and drift detection."""

from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportUnusedCallResult=false

from datetime import datetime
from pathlib import Path
from typing import Any, final
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from orchestrator.skills_projection import (
    SkillProjectionStore,
    compute_content_hash,
)
from orchestrator.skills_sync import SkillSyncService, derive_source_type_for_backfill


@final
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


@pytest_asyncio.fixture
async def mock_db_pool() -> AsyncMock:
    pool = AsyncMock()
    return pool


@pytest.fixture
def mock_embedding() -> MagicMock:
    em = MagicMock()
    em.return_value = [0.1] * 1024
    return em


class TestComputeContentHash:
    def test_hash_is_deterministic(self) -> None:
        content = "# Test Skill\n\nSome content."
        h1 = compute_content_hash(content)
        h2 = compute_content_hash(content)
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        h1 = compute_content_hash("# Skill A\n\nContent A")
        h2 = compute_content_hash("# Skill B\n\nContent B")
        assert h1 != h2


class TestProjectionFromRow:
    def test_projection_from_row_keeps_dict_pending_update(self) -> None:
        from orchestrator.skills_projection import projection_from_row

        row = {"skill_id": "s1", "pending_update": {"repo_version": "2.0.0"}}
        result = projection_from_row(row)
        assert result["pending_update"] == {"repo_version": "2.0.0"}
        assert isinstance(result["pending_update"], dict)

    def test_projection_from_row_parses_legacy_string_pending_update(self) -> None:
        from orchestrator.skills_projection import projection_from_row

        row = {
            "skill_id": "s1",
            "pending_update": '{"repo_version": "2.0.0", "repo_hash": "abc"}',
        }
        result = projection_from_row(row)
        assert isinstance(result["pending_update"], dict)
        assert result["pending_update"]["repo_version"] == "2.0.0"
        assert result["pending_update"]["repo_hash"] == "abc"

    def test_projection_from_row_leaves_invalid_string_pending_update(self) -> None:
        from orchestrator.skills_projection import projection_from_row

        row = {"skill_id": "s1", "pending_update": "not valid json {"}
        result = projection_from_row(row)
        assert result["pending_update"] == "not valid json {"

    def test_projection_from_row_handles_none_pending_update(self) -> None:
        from orchestrator.skills_projection import projection_from_row

        row = {"skill_id": "s1", "pending_update": None}
        result = projection_from_row(row)
        assert result["pending_update"] is None


class TestSkillProjectionStore:
    @pytest.mark.asyncio
    async def test_upsert_creates_new_projection(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="new-skill",
            name="New Skill",
            description="A new skill",
            source_file_path="/path/to/new-skill.md",
            source_hash="abc123",
            enabled=True,
            source_type="manual",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="abc123",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        result = await store.upsert_projection(
            skill_id="new-skill",
            name="New Skill",
            description="A new skill",
            source_file_path="/path/to/new-skill.md",
            source_hash="abc123",
        )
        assert result["skill_id"] == "new-skill"
        mock_db_pool.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_projection_returns_none_when_missing(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.return_value = None
        result = await store.get_projection("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_projection_returns_true(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "DELETE 1"
        result = await store.delete_projection("skill-to-delete")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_projection_returns_false_when_not_found(
        self, mock_db_pool: AsyncMock
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "DELETE 0"
        result = await store.delete_projection("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_touch_usage_increments_count(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"
        await store.touch_usage("popular-skill")
        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args[0]
        assert "use_count = use_count + 1" in call_args[0]

    @pytest.mark.asyncio
    async def test_projection_exists_returns_true(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchval.return_value = True
        result = await store.projection_exists("existing-skill")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_autonomous_edit_updates_flag(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"

        result = await store.update_autonomous_edit("test-skill", True)

        assert result is True
        call_args = mock_db_pool.execute.call_args[0]
        assert "allow_autonomous_edit = $2" in call_args[0]
        assert call_args[1] == "test-skill"
        assert call_args[2] is True

    @pytest.mark.asyncio
    async def test_update_autonomous_edit_returns_false_when_not_found(
        self, mock_db_pool: AsyncMock
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 0"

        result = await store.update_autonomous_edit("nonexistent-skill", False)

        assert result is False

    @pytest.mark.asyncio
    async def test_update_autonomous_metadata_updates_trigger_and_complexity(
        self, mock_db_pool: AsyncMock
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"

        result = await store.update_autonomous_metadata(
            "existing-skill",
            trigger_conditions="Use when debugging flaky workers.",
            complexity_origin=7,
        )

        assert result is True
        call_args = mock_db_pool.execute.call_args[0]
        assert "trigger_conditions = $1" in call_args[0]
        assert "complexity_origin = $2" in call_args[0]
        assert call_args[1] == "Use when debugging flaky workers."
        assert call_args[2] == 7
        assert call_args[3] == "existing-skill"

    @pytest.mark.asyncio
    async def test_get_all_skill_ids(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetch.return_value = [
            MockRecord(skill_id="skill-a"),
            MockRecord(skill_id="skill-b"),
        ]
        ids = await store.get_all_skill_ids()
        assert ids == ["skill-a", "skill-b"]

    @pytest.mark.asyncio
    async def test_upsert_passes_pending_update_as_dict_not_string(
        self, mock_db_pool: AsyncMock
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        pending_update = {"repo_version": "2.0.0", "repo_hash": "abc"}
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="test-skill",
            name="Test",
            description="",
            source_file_path="/path/test-skill.md",
            source_hash="abc",
            enabled=True,
            source_type="manual",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update=pending_update,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        await store.upsert_projection(
            skill_id="test-skill",
            name="Test",
            description="",
            source_file_path="/path/test-skill.md",
            source_hash="abc",
            pending_update=pending_update,
        )
        args_tuple = mock_db_pool.fetchrow.call_args[0]
        passed_pending_update = args_tuple[13]
        assert passed_pending_update is pending_update
        assert isinstance(passed_pending_update, dict)

    @pytest.mark.asyncio
    async def test_set_pending_update_passes_dict_not_string(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "UPDATE 1"
        pending_update = {"repo_version": "3.0.0", "repo_hash": "def"}
        await store.set_pending_update("test-skill", pending_update)
        args_tuple = mock_db_pool.execute.call_args[0]
        passed_update = args_tuple[2]
        assert passed_update is pending_update
        assert isinstance(passed_update, dict)

    @pytest.mark.asyncio
    async def test_upsert_uses_jsonb_cast_for_pending_update(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        pending_update = {"repo_version": "2.0.0"}
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="test-skill",
            name="Test",
            description="",
            source_file_path="/path/test-skill.md",
            source_hash="abc",
            enabled=True,
            source_type="manual",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update=pending_update,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        await store.upsert_projection(
            skill_id="test-skill",
            name="Test",
            description="",
            source_file_path="/path/test-skill.md",
            source_hash="abc",
            pending_update=pending_update,
        )
        query = mock_db_pool.fetchrow.call_args[0][0]
        # Verify ::jsonb cast is present for the pending_update value position ($13)
        assert "$13::jsonb" in query, "pending_update must use ::jsonb cast in VALUES"


class TestSkillSyncService:
    @pytest.mark.asyncio
    async def test_sync_skill_creates_projection(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "my-test-skill.md"
        skill_file.write_text(
            "---\nname: My Test Skill\ndescription: A test skill\nenabled: true\n---\n# My Test Skill\n\nSkill content."
        )
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="my-test-skill",
            name="My Test Skill",
            description="A test skill",
            source_file_path=str(skill_file),
            source_hash="abc123",
            enabled=True,
            source_type="manual",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="abc123",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with patch(
                "orchestrator.skills_sync.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                result = await service.sync_skill("my-test-skill")
        assert result.success is True
        assert result.action == "upsert"

    @pytest.mark.asyncio
    async def test_sync_skill_fails_when_file_missing(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            result = await service.sync_skill("nonexistent-skill")
        assert result.success is False
        assert result.error is not None and "not found" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("skill_id", ["../escape", "/outside/escape", "unsafe\\name"])
    async def test_sync_skill_rejects_unsafe_ids_before_database_access(
        self,
        mock_db_pool: AsyncMock,
        tmp_path: Path,
        skill_id: str,
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with pytest.raises(ValueError):
                await service.sync_skill(skill_id)

        mock_db_pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_skill_rejects_symlink_escape(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        outside_file = tmp_path.parent / "outside-skill.md"
        outside_file.write_text("outside", encoding="utf-8")
        (tmp_path / "linked-skill.md").symlink_to(outside_file)

        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with pytest.raises(ValueError, match="escapes the skills directory"):
                await service.sync_skill("linked-skill")

        mock_db_pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_skill_projection_removes_row(self, mock_db_pool: AsyncMock) -> None:
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.execute.return_value = "DELETE 1"
        service = SkillSyncService(store)
        result = await service.delete_skill_projection("to-delete")
        assert result.success is True
        assert result.action == "delete"

    @pytest.mark.asyncio
    async def test_detect_drift_returns_true_when_hash_mismatch(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "drifted-skill.md"
        skill_file.write_text("# Drifted Skill\n\nOriginal content.")
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchval.return_value = (
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            drifted = await service.detect_drift("drifted-skill")
        assert drifted is True

    @pytest.mark.asyncio
    async def test_detect_drift_rejects_unsafe_id_before_database_access(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with pytest.raises(ValueError):
                await service.detect_drift("../escape")

        mock_db_pool.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_drift_returns_false_when_hash_matches(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "stable-skill.md"
        content = "# Stable Skill\n\nContent."
        skill_file.write_text(content)
        expected_hash = compute_content_hash(content)
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchval.return_value = expected_hash
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            drifted = await service.detect_drift("stable-skill")
        assert drifted is False

    @pytest.mark.asyncio
    async def test_detect_drift_returns_false_when_projection_missing(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "orphan-skill.md"
        skill_file.write_text("# Orphan Skill\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchval.return_value = None
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            drifted = await service.detect_drift("orphan-skill")
        assert drifted is False

    @pytest.mark.asyncio
    async def test_reconcile_detects_missing_and_orphaned(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        (tmp_path / "file-only.md").write_text("# File Only\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetch.return_value = [MockRecord(skill_id="db-only")]
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with patch.object(service, "detect_drift", AsyncMock(return_value=False)):
                result = await service.reconcile()
        assert result.total_missing == 1
        assert "file-only" in result.missing
        assert result.total_orphaned == 1
        assert "db-only" in result.orphaned

    @pytest.mark.asyncio
    async def test_reconcile_detects_drifted(self, mock_db_pool: AsyncMock, tmp_path: Path) -> None:
        skill_file = tmp_path / "drifted.md"
        skill_file.write_text("# Drifted\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetch.return_value = [MockRecord(skill_id="drifted")]
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with patch.object(service, "detect_drift", AsyncMock(return_value=True)):
                result = await service.reconcile()
        assert result.total_drifted == 1
        assert "drifted" in result.drifted

    @pytest.mark.asyncio
    async def test_resync_drifted_returns_error_when_not_drifted(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "stable.md"
        skill_file.write_text("# Stable\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with patch.object(service, "detect_drift", AsyncMock(return_value=False)):
                result = await service.resync_drifted("stable")
        assert result.success is False
        assert result.error is not None and "No drift detected" in result.error

    @pytest.mark.asyncio
    async def test_resync_all_drifted(self, mock_db_pool: AsyncMock, tmp_path: Path) -> None:
        skill_file = tmp_path / "drifted-a.md"
        skill_file.write_text("# Drifted A\n\nContent.")
        skill_file2 = tmp_path / "drifted-b.md"
        skill_file2.write_text("# Drifted B\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetch.return_value = [
            MockRecord(skill_id="drifted-a"),
            MockRecord(skill_id="drifted-b"),
        ]
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="drifted-a",
            name="Drifted A",
            description="",
            source_file_path=str(skill_file),
            source_hash="abc",
            enabled=True,
            source_type="manual",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="abc",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            service = SkillSyncService(store)
            with patch.object(service, "detect_drift", AsyncMock(return_value=True)):
                with patch(
                    "orchestrator.skills_sync.embed_skill_content",
                    AsyncMock(return_value=[0.1] * 1024),
                ):
                    results = await service.resync_all_drifted()
        assert len(results) == 2
        assert all(r.action == "upsert" for r in results)

    @pytest.mark.asyncio
    async def test_backfill_existing_skills(self, mock_db_pool: AsyncMock, tmp_path: Path) -> None:
        skill_file = tmp_path / "existing-skill.md"
        skill_file.write_text(
            "---\nname: Existing Skill\ndescription: An existing skill\nenabled: true\n---\n# Existing Skill\n\nContent."
        )
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="existing-skill",
            name="Existing Skill",
            description="An existing skill",
            source_file_path=str(skill_file),
            source_hash="abc",
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="0.0.0",
            local_version="abc",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.list_skills",
                return_value=[
                    {
                        "id": "existing-skill",
                        "name": "Existing Skill",
                        "description": "An existing skill",
                        "enabled": True,
                        "updated_at": "2024-01-01",
                    }
                ],
            ):
                service = SkillSyncService(store)
                with patch(
                    "orchestrator.skills_sync.embed_skill_content",
                    AsyncMock(return_value=[0.1] * 1024),
                ):
                    results = await service.backfill_existing_skills()
        assert len(results) == 1
        assert results[0].skill_id == "existing-skill"
        assert results[0].success is True
        assert results[0].action == "backfill"

    @pytest.mark.asyncio
    async def test_backfill_handles_sync_failure(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        (tmp_path / "fail-skill.md").write_text("# Fail Skill\n\nContent.")
        store = SkillProjectionStore(mock_db_pool)
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.list_skills",
                return_value=[
                    {
                        "id": "fail-skill",
                        "name": "Fail Skill",
                        "description": "",
                        "enabled": True,
                        "updated_at": "2024-01-01",
                    }
                ],
            ):
                service = SkillSyncService(store)
                with patch(
                    "orchestrator.skills_sync.embed_skill_content",
                    AsyncMock(side_effect=RuntimeError("Embedding failed")),
                ):
                    mock_db_pool.fetchrow.side_effect = RuntimeError("DB error during upsert")
                    results = await service.backfill_existing_skills()
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None and "DB error" in results[0].error


class TestStartupBackfillIntegration:
    @pytest.mark.asyncio
    async def test_lifespan_calls_backfill_on_startup(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        from contextlib import asynccontextmanager
        from typing import AsyncIterator

        import asyncio
        from fastapi import FastAPI

        backfill_path_entered = False

        @asynccontextmanager
        async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
            from dataclasses import dataclass

            @dataclass
            class MockState:
                db_pool: AsyncMock

            state = MockState(db_pool=mock_db_pool)
            app.state.app_state = state
            if state.db_pool is not None:
                nonlocal backfill_path_entered
                backfill_path_entered = True
                asyncio.create_task(asyncio.sleep(0))
            yield

        app = FastAPI(lifespan=test_lifespan)

        async with app.router.lifespan_context(app):
            pass

        assert backfill_path_entered is True

    @pytest.mark.asyncio
    async def test_backfill_uses_manual_source_type_for_pre_existing_skills(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "pre-existing-skill.md"
        skill_file.write_text(
            "---\nname: Pre-existing Skill\ndescription: Existed before migration\nenabled: true\n---\n# Pre-existing Skill\n\nContent."
        )
        mock_db_pool.fetchrow.return_value = None
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.side_effect = [
            None,
            MockRecord(
                skill_id="pre-existing-skill",
                name="Pre-existing Skill",
                description="Existed before migration",
                source_file_path=str(skill_file),
                source_hash="abc",
                enabled=True,
                source_type="manual",
                created_by="system",
                origin_url="",
                embedding=None,
                repo_version="0.0.0",
                local_version="abc",
                pending_update=None,
                allow_autonomous_edit=False,
                trigger_conditions="",
                complexity_origin=0,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            ),
        ]
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.list_skills",
                return_value=[
                    {
                        "id": "pre-existing-skill",
                        "name": "Pre-existing Skill",
                        "description": "Existed before migration",
                        "enabled": True,
                        "updated_at": "2024-01-01",
                    }
                ],
            ):
                service = SkillSyncService(store)
                with patch(
                    "orchestrator.skills_sync.embed_skill_content",
                    AsyncMock(return_value=[0.1] * 1024),
                ):
                    results = await service.backfill_existing_skills()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action == "backfill"

    @pytest.mark.asyncio
    async def test_backfill_uses_system_for_manifest_tracked_skill(
        self, mock_db_pool: AsyncMock, tmp_path: Path
    ) -> None:
        skill_file = tmp_path / "manifest-skill.md"
        skill_file.write_text(
            "---\nname: Manifest Skill\ndescription: A repo-managed skill\nenabled: true\n---\n# Manifest Skill\n\nContent."
        )
        store = SkillProjectionStore(mock_db_pool)
        mock_db_pool.fetchrow.side_effect = [
            None,
            MockRecord(
                skill_id="manifest-skill",
                name="Manifest Skill",
                description="A repo-managed skill",
                source_file_path=str(skill_file),
                source_hash="abc",
                enabled=True,
                source_type="system",
                created_by="system",
                origin_url="",
                embedding=None,
                repo_version="0.0.0",
                local_version="abc",
                pending_update=None,
                allow_autonomous_edit=False,
                trigger_conditions="",
                complexity_origin=0,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            ),
        ]
        with patch("orchestrator.skills_sync.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_sync.list_skills",
                return_value=[
                    {
                        "id": "manifest-skill",
                        "name": "Manifest Skill",
                        "description": "A repo-managed skill",
                        "enabled": True,
                        "updated_at": "2024-01-01",
                    }
                ],
            ):
                with patch(
                    "orchestrator.skills_sync.derive_source_type_for_backfill",
                    return_value="system",
                ):
                    service = SkillSyncService(store)
                    with patch(
                        "orchestrator.skills_sync.embed_skill_content",
                        AsyncMock(return_value=[0.1] * 1024),
                    ):
                        results = await service.backfill_existing_skills()
        assert len(results) == 1
        assert results[0].skill_id == "manifest-skill"
        assert results[0].success is True


class TestDeriveSourceTypeForBackfill:
    def test_derive_returns_system_for_manifest_skill(self) -> None:
        from orchestrator.skills_upgrade import SkillManifest, SkillManifestEntry

        mock_manifest = SkillManifest()
        mock_manifest.skills["tracked-skill"] = SkillManifestEntry(
            repo_hash="abc",
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at="2024-01-01",
        )
        with patch(
            "orchestrator.skills_sync.load_manifest",
            return_value=mock_manifest,
        ):
            result = derive_source_type_for_backfill("tracked-skill")
        assert result == "system"

    def test_derive_returns_manual_for_non_manifest_skill(self) -> None:
        from orchestrator.skills_upgrade import SkillManifest

        mock_manifest = SkillManifest()
        with patch(
            "orchestrator.skills_sync.load_manifest",
            return_value=mock_manifest,
        ):
            result = derive_source_type_for_backfill("user-created-skill")
        assert result == "manual"

    def test_derive_returns_manual_when_manifest_empty(self) -> None:
        from orchestrator.skills_upgrade import SkillManifest

        mock_manifest = SkillManifest()
        with patch(
            "orchestrator.skills_sync.load_manifest",
            return_value=mock_manifest,
        ):
            result = derive_source_type_for_backfill("some-skill")
        assert result == "manual"
