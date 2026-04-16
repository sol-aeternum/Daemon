"""Tests for skill UI metadata surfaces supporting SkillsTab frontend display.

This module validates the API contracts for:
- Badge rendering (source_type with icons/colors)
- Protected/autonomous edit state and actions
- Pending update presentation (apply/dismiss actions)
- Search and filter behavior at the settings-page contract level
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.routes import skills as skills_router


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


def _set_db_pool(client: TestClient, db_pool: AsyncMock | None) -> None:
    """Helper to set db_pool on app state, bypassing type checking."""
    cast(Any, client.app).state.app_state.db_pool = db_pool


@pytest.fixture
def app_with_mock_db(mock_db_pool: AsyncMock) -> FastAPI:
    """Create FastAPI app with mocked db pool in app state."""
    app = FastAPI()
    app.include_router(skills_router.router)

    mock_app_state = MagicMock()
    mock_app_state.db_pool = mock_db_pool
    cast(Any, app).state.app_state = mock_app_state

    return app


@pytest.fixture
def client(app_with_mock_db: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app_with_mock_db)


@pytest_asyncio.fixture
async def mock_db_pool() -> AsyncMock:
    """Create mock database pool."""
    return AsyncMock()


class TestSkillUIBadgeRendering:
    """Tests for source_type badge metadata surfaces.

    Validates that the API returns the correct source_type values
    that the UI renders as color-coded badges with icons.
    """

    def test_system_skill_badge_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """System skills return source_type='system' for Lock icon + blue badge."""
        skill_file = tmp_path / "system-skill.md"
        skill_file.write_text("""---
name: System Skill
description: A built-in system skill
enabled: true
---
# System Skill

System content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="system-skill",
            name="System Skill",
            description="A built-in system skill",
            source_file_path=str(skill_file),
            source_hash="sys123",
            enabled=True,
            source_type="system",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            skill = data["skills"][0]
            assert skill["source_type"] == "system"

            response = client.get("/skills/system-skill")
            assert response.status_code == 200
            detail = response.json()
            assert detail["source_type"] == "system"

    def test_imported_skill_badge_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Imported skills return source_type='imported' for Globe icon + purple badge."""
        skill_file = tmp_path / "imported-skill.md"
        skill_file.write_text("""---
name: Imported Skill
description: An imported skill
enabled: true
---
# Imported Skill

Imported content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="imported-skill",
            name="Imported Skill",
            description="An imported skill",
            source_file_path=str(skill_file),
            source_hash="imp123",
            enabled=True,
            source_type="imported",
            created_by="user@example.com",
            origin_url="https://example.com/skill.md",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=5,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()
            skill = data["skills"][0]
            assert skill["source_type"] == "imported"

    def test_manual_skill_badge_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Manual skills return source_type='manual' for User icon + green badge."""
        skill_file = tmp_path / "manual-skill.md"
        skill_file.write_text("""---
name: Manual Skill
description: A manually created skill
enabled: true
---
# Manual Skill

Manual content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="manual-skill",
            name="Manual Skill",
            description="A manually created skill",
            source_file_path=str(skill_file),
            source_hash="man123",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=10,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()
            skill = data["skills"][0]
            assert skill["source_type"] == "manual"

    def test_autonomous_skill_badge_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Autonomous skills return source_type='autonomous' for Bot icon + amber badge."""
        skill_file = tmp_path / "autonomous-skill.md"
        skill_file.write_text("""---
name: Autonomous Skill
description: An auto-created skill
enabled: true
---
# Autonomous Skill

Autonomous content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="autonomous-skill",
            name="Autonomous Skill",
            description="An auto-created skill",
            source_file_path=str(skill_file),
            source_hash="auto123",
            enabled=True,
            source_type="autonomous",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=3,
            use_count=25,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()
            skill = data["skills"][0]
            assert skill["source_type"] == "autonomous"
            assert skill["use_count"] == 25

    def test_mixed_source_types_in_list(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns diverse source_types for badge variety."""
        skills_data = [
            ("sys1", "system"),
            ("imp1", "imported"),
            ("man1", "manual"),
            ("auto1", "autonomous"),
        ]

        for skill_id, source_type in skills_data:
            skill_file = tmp_path / f"{skill_id}.md"
            skill_file.write_text(f"""---
name: {source_type} Skill
description: A {source_type} skill
enabled: true
---
# {source_type} Skill

Content.
""")

        projections = {
            "sys1": {
                "source_type": "system",
                "allow_autonomous_edit": False,
                "use_count": 0,
            },
            "imp1": {
                "source_type": "imported",
                "allow_autonomous_edit": False,
                "use_count": 5,
            },
            "man1": {
                "source_type": "manual",
                "allow_autonomous_edit": False,
                "use_count": 0,
            },
            "auto1": {
                "source_type": "autonomous",
                "allow_autonomous_edit": True,
                "use_count": 5,
            },
        }

        async def mock_get_projection(self, skill_id):
            proj = projections.get(skill_id, {})
            return {
                "skill_id": skill_id,
                "source_type": proj.get("source_type"),
                "allow_autonomous_edit": proj.get("allow_autonomous_edit"),
                "use_count": proj.get("use_count"),
                "pending_update": None,
                "repo_version": "1.0.0",
                "local_version": "1.0.0",
                "last_used_at": None,
            }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_projection.SkillProjectionStore.get_projection",
                mock_get_projection,
            ):
                response = client.get("/skills")
                assert response.status_code == 200
                data = response.json()

                source_types = {s["source_type"] for s in data["skills"]}
                assert source_types == {"system", "imported", "manual", "autonomous"}


class TestSkillUIProtectedAutonomousEdit:
    """Tests for protected skill autonomous edit toggle UI surface.

    Validates that protected skills (system/imported/manual) can toggle
    allow_autonomous_edit, while autonomous skills are always editable.
    """

    def test_protected_skill_shows_autonomous_edit_toggle(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Protected skills (system) return allow_autonomous_edit field for UI toggle."""
        skill_file = tmp_path / "protected-system.md"
        skill_file.write_text("""---
name: Protected System Skill
description: A protected system skill
enabled: true
---
# Protected System Skill

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="protected-system",
            name="Protected System Skill",
            description="A protected system skill",
            source_file_path=str(skill_file),
            source_hash="prot123",
            enabled=True,
            source_type="system",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/protected-system")
            assert response.status_code == 200
            data = response.json()

            assert data["source_type"] == "system"
            assert data["allow_autonomous_edit"] is False

            list_response = client.get("/skills")
            list_data = list_response.json()
            autonomous_count = sum(
                1 for s in list_data["skills"] if s.get("source_type") == "autonomous"
            )
            assert autonomous_count == 0

    def test_imported_skill_autonomous_edit_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Imported skills show allow_autonomous_edit toggle in metadata."""
        skill_file = tmp_path / "imported-protected.md"
        skill_file.write_text("""---
name: Imported Protected
description: An imported skill
enabled: true
---
# Imported Protected

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="imported-protected",
            name="Imported Protected",
            description="An imported skill",
            source_file_path=str(skill_file),
            source_hash="imp_prot",
            enabled=True,
            source_type="imported",
            created_by="user@example.com",
            origin_url="https://example.com/skill.md",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=0,
            use_count=3,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/imported-protected")
            data = response.json()

            assert data["source_type"] == "imported"
            assert data["allow_autonomous_edit"] is True

    def test_manual_skill_autonomous_edit_metadata(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Manual skills show allow_autonomous_edit toggle in metadata."""
        skill_file = tmp_path / "manual-protected.md"
        skill_file.write_text("""---
name: Manual Protected
description: A manual skill
enabled: true
---
# Manual Protected

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="manual-protected",
            name="Manual Protected",
            description="A manual skill",
            source_file_path=str(skill_file),
            source_hash="man_prot",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/manual-protected")
            data = response.json()

            assert data["source_type"] == "manual"
            assert data["allow_autonomous_edit"] is False

    def test_autonomous_skill_shows_no_toggle_but_edit_indicator(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Autonomous skills return source_type='autonomous' to trigger Bot banner, not toggle."""
        skill_file = tmp_path / "auto-skill.md"
        skill_file.write_text("""---
name: Auto Skill
description: An autonomous skill
enabled: true
---
# Auto Skill

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="auto-skill",
            name="Auto Skill",
            description="An autonomous skill",
            source_file_path=str(skill_file),
            source_hash="auto_hash",
            enabled=True,
            source_type="autonomous",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=5,
            use_count=50,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/auto-skill")
            data = response.json()

            assert data["source_type"] == "autonomous"
            assert data["allow_autonomous_edit"] is True

            list_response = client.get("/skills")
            list_data = list_response.json()
            autonomous_skills = [
                s for s in list_data["skills"] if s.get("source_type") == "autonomous"
            ]
            assert len(autonomous_skills) == 1
            assert autonomous_skills[0]["use_count"] == 50


class TestSkillUIPendingUpdatePresentation:
    """Tests for pending update UI surfaces (apply/dismiss actions).

    Validates that pending_update metadata triggers the correct UI presentation:
    - Update available: blue banner with Apply/Dismiss buttons
    - Deprecated: amber banner with warning icon (no actions)
    """

    def test_pending_update_shows_apply_dismiss_actions(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills with pending_update (non-deprecated) trigger blue banner with Apply/Dismiss."""
        skill_file = tmp_path / "pending-skill.md"
        skill_file.write_text("""---
name: Pending Skill
description: A skill with pending update
repo_version: 1.0.0
local_version: 1.0.0
enabled: true
---
# Pending Skill

Old content.
""")

        pending_data = {
            "repo_hash": "new_hash_123",
            "repo_version": "2.0.0",
            "repo_content": "---\nname: Pending Skill\ndescription: Updated skill\nrepo_version: 2.0.0\n---\n\nNew content.",
            "updated_at": datetime.now().isoformat(),
        }

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="pending-skill",
            name="Pending Skill",
            description="A skill with pending update",
            source_file_path=str(skill_file),
            source_hash="old_hash",
            enabled=True,
            source_type="system",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update=pending_data,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=10,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/pending-skill")
            data = response.json()

            assert data["pending_update"] is not None
            assert data["pending_update"].get("deprecated") is None
            assert data["repo_version"] == "2.0.0"
            assert data["local_version"] == "1.0.0"

            # Verify Apply/Dismiss endpoints exist
            assert client.post(
                "/skills/pending-skill/pending-update", json={"action": "apply"}
            ).status_code in (200, 400)
            assert client.post(
                "/skills/pending-skill/pending-update", json={"action": "dismiss"}
            ).status_code in (200, 400)

    def test_deprecated_skill_shows_warning_no_actions(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Deprecated skills (removed from repo) trigger amber banner, no Apply/Dismiss buttons."""
        skill_file = tmp_path / "deprecated-skill.md"
        skill_file.write_text("""---
name: Deprecated Skill
description: A deprecated skill
enabled: true
---
# Deprecated Skill

Content.
""")

        pending_data = {
            "deprecated": True,
            "removed_from_repo": True,
            "previous_hash": "old_hash",
            "previous_repo_version": "1.0.0",
            "updated_at": datetime.now().isoformat(),
        }

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="deprecated-skill",
            name="Deprecated Skill",
            description="A deprecated skill",
            source_file_path=str(skill_file),
            source_hash="hash123",
            enabled=True,
            source_type="system",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=pending_data,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=5,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/deprecated-skill")
            data = response.json()

            assert data["pending_update"] is not None
            assert data["pending_update"].get("deprecated") is True
            assert data["pending_update"].get("removed_from_repo") is True

    def test_pending_update_count_in_stats_header(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List returns skills with pending_update for stats header counter."""
        for i in range(3):
            skill_file = tmp_path / f"skill{i}.md"
            has_pending = i < 2
            pending = {"repo_version": "2.0.0"} if has_pending else None

            skill_file.write_text(f"""---
name: Skill {i}
description: Test skill {i}
enabled: true
---
# Skill {i}

Content.
""")

        call_count = 0

        def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            idx = call_count % 3
            call_count += 1
            has_pending = idx < 2

            return MockRecord(
                skill_id=f"skill{idx}",
                name=f"Skill {idx}",
                description=f"Test skill {idx}",
                source_file_path=str(tmp_path / f"skill{idx}.md"),
                source_hash=f"hash{idx}",
                enabled=True,
                source_type="manual",
                created_by="user",
                origin_url="",
                embedding=None,
                repo_version="2.0.0" if has_pending else "1.0.0",
                local_version="1.0.0",
                pending_update={"repo_version": "2.0.0"} if has_pending else None,
                allow_autonomous_edit=False,
                trigger_conditions="",
                complexity_origin=0,
                use_count=0,
                last_used_at=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            )

        mock_db_pool.fetchrow.side_effect = mock_fetchrow

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            pending_count = sum(
                1
                for s in data["skills"]
                if s.get("pending_update") and len(s["pending_update"]) > 0
            )
            assert pending_count == 2

    def test_no_pending_update_hides_banner(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills with null/empty pending_update hide the update banner entirely."""
        skill_file = tmp_path / "no-pending.md"
        skill_file.write_text("""---
name: No Pending
description: No pending updates
enabled: true
---
# No Pending

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="no-pending",
            name="No Pending",
            description="No pending updates",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/no-pending")
            data = response.json()

            assert data.get("pending_update") is None


class TestSkillUISearchFilterBehavior:
    """Tests for search and filter behavior at the list contract level.

    Validates that the list endpoint returns all skills for client-side filtering,
    with complete metadata for search by name/description/id.
    """

    def test_list_includes_all_fields_for_search(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns name, description, id for client-side search."""
        skill_file = tmp_path / "python-debug.md"
        skill_file.write_text("""---
name: Python Debugging Helper
description: Helps debug Python code with stack traces
enabled: true
---
# Python Debugging Helper

Debug content.
""")

        async def mock_get_projection(self, skill_id):
            return {
                "skill_id": skill_id,
                "source_type": "manual",
                "allow_autonomous_edit": False,
                "use_count": 15,
                "pending_update": None,
                "repo_version": "1.0.0",
                "local_version": "1.0.0",
                "last_used_at": None,
            }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_projection.SkillProjectionStore.get_projection",
                mock_get_projection,
            ):
                response = client.get("/skills")
                data = response.json()

                skill = data["skills"][0]
                assert "name" in skill
                assert "description" in skill
                assert "id" in skill
                assert skill["name"] == "Python Debugging Helper"
                assert (
                    skill["description"] == "Helps debug Python code with stack traces"
                )
                assert skill["id"] == "python-debug"

    def test_list_includes_source_type_for_filtering(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns source_type for filter by type (all/system/imported/etc)."""
        # Create skills with different source types
        for source_type in ["system", "manual", "autonomous"]:
            skill_file = tmp_path / f"{source_type}-skill.md"
            skill_file.write_text(f"""---
name: {source_type} Skill
description: A {source_type} skill
enabled: true
---
# {source_type} Skill

Content.
""")

        call_count = 0

        def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            types = ["system", "manual", "autonomous"]
            actual_type = types[call_count % 3]
            call_count += 1

            return MockRecord(
                skill_id=f"{actual_type}-skill",
                name=f"{actual_type} Skill",
                description=f"A {actual_type} skill",
                source_file_path=str(tmp_path / f"{actual_type}-skill.md"),
                source_hash=f"hash_{actual_type}",
                enabled=True,
                source_type=actual_type,
                created_by="system"
                if actual_type in ("system", "autonomous")
                else "user",
                origin_url="",
                embedding=None,
                repo_version="1.0.0",
                local_version="1.0.0",
                pending_update=None,
                allow_autonomous_edit=actual_type == "autonomous",
                trigger_conditions="",
                complexity_origin=0,
                use_count=0,
                last_used_at=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            )

        mock_db_pool.fetchrow.side_effect = mock_fetchrow

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            for skill in data["skills"]:
                assert "source_type" in skill
                assert skill["source_type"] in ["system", "manual", "autonomous"]

    def test_list_includes_enabled_for_status_indicator(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns enabled field for green/grey status dot."""
        for enabled in [True, False]:
            skill_file = tmp_path / f"skill-enabled-{enabled}.md"
            skill_file.write_text(f"""---
name: Skill {enabled}
description: Skill with enabled={enabled}
enabled: {str(enabled).lower()}
---
# Skill {enabled}

Content.
""")

        call_count = 0

        def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            enabled = call_count % 2 == 0
            call_count += 1

            return MockRecord(
                skill_id=f"skill-enabled-{enabled}",
                name=f"Skill {enabled}",
                description=f"Skill with enabled={enabled}",
                source_file_path=str(tmp_path / f"skill-enabled-{enabled}.md"),
                source_hash="hash",
                enabled=enabled,
                source_type="manual",
                created_by="user",
                origin_url="",
                embedding=None,
                repo_version="1.0.0",
                local_version="1.0.0",
                pending_update=None,
                allow_autonomous_edit=False,
                trigger_conditions="",
                complexity_origin=0,
                use_count=0,
                last_used_at=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            )

        mock_db_pool.fetchrow.side_effect = mock_fetchrow

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            for skill in data["skills"]:
                assert "enabled" in skill
                assert isinstance(skill["enabled"], bool)

    def test_list_includes_pending_update_for_alert_indicator(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns pending_update for AlertTriangle icon display."""
        for i, has_pending in enumerate([True, False]):
            skill_file = tmp_path / f"skill-pending-{i}.md"
            skill_file.write_text(f"""---
name: Skill Pending {i}
description: Test
enabled: true
---
# Skill

Content.
""")

        projections = {
            "skill-pending-0": {"pending_update": {"repo_version": "2.0.0"}},
            "skill-pending-1": {"pending_update": None},
        }

        async def mock_get_projection(self, skill_id):
            proj = projections.get(skill_id, {})
            return {
                "skill_id": skill_id,
                "source_type": "system",
                "allow_autonomous_edit": False,
                "use_count": 0,
                "pending_update": proj.get("pending_update"),
                "repo_version": "1.0.0",
                "local_version": "1.0.0",
                "last_used_at": None,
            }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_projection.SkillProjectionStore.get_projection",
                mock_get_projection,
            ):
                response = client.get("/skills")
                data = response.json()

                skills_with_pending = [
                    s for s in data["skills"] if s.get("pending_update")
                ]
                assert len(skills_with_pending) == 1
                assert "skill-pending-0" in skills_with_pending[0]["id"]

    def test_list_includes_autonomous_edit_for_sparkles_indicator(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns allow_autonomous_edit for Sparkles icon display."""
        for i, allowed in enumerate([True, False]):
            skill_file = tmp_path / f"skill-auto-{i}.md"
            skill_file.write_text(f"""---
name: Skill Auto {i}
description: Test
enabled: true
---
# Skill

Content.
""")

        projections = {
            "skill-auto-0": {"allow_autonomous_edit": True},
            "skill-auto-1": {"allow_autonomous_edit": False},
        }

        async def mock_get_projection(self, skill_id):
            proj = projections.get(skill_id, {})
            return {
                "skill_id": skill_id,
                "source_type": "manual",
                "allow_autonomous_edit": proj.get("allow_autonomous_edit"),
                "use_count": 0,
                "pending_update": None,
                "repo_version": "1.0.0",
                "local_version": "1.0.0",
                "last_used_at": None,
            }

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_projection.SkillProjectionStore.get_projection",
                mock_get_projection,
            ):
                response = client.get("/skills")
                data = response.json()

                skills_with_sparkles = [
                    s for s in data["skills"] if s.get("allow_autonomous_edit")
                ]
                assert len(skills_with_sparkles) == 1
                assert "skill-auto-0" in skills_with_sparkles[0]["id"]

    def test_list_includes_use_count_for_stats_display(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns use_count for 'Used N×' display."""
        skill_file = tmp_path / "frequently-used.md"
        skill_file.write_text("""---
name: Frequently Used
description: A commonly used skill
enabled: true
---
# Frequently Used

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="frequently-used",
            name="Frequently Used",
            description="A commonly used skill",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="autonomous",
            created_by="daemon",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=0,
            use_count=42,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            skill = data["skills"][0]
            assert "use_count" in skill
            assert skill["use_count"] == 42

    def test_list_includes_updated_at_for_date_display(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns updated_at for 'Updated M/D/YYYY' display."""
        skill_file = tmp_path / "recently-updated.md"
        skill_file.write_text("""---
name: Recently Updated
description: Recently modified
enabled: true
---
# Recently Updated

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="recently-updated",
            name="Recently Updated",
            description="Recently modified",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            skill = data["skills"][0]
            assert "updated_at" in skill
            assert isinstance(skill["updated_at"], str)


class TestSkillUIDetailViewMetadata:
    """Tests for detail view metadata supporting full skill editor display."""

    def test_detail_includes_all_canonical_fields(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Detail endpoint returns content and all metadata for editor view."""
        skill_file = tmp_path / "complete-skill.md"
        skill_file.write_text("""---
name: Complete Skill
description: A complete skill for testing
enabled: true
---
# Complete Skill

## Instructions

Full markdown content here.

- Item 1
- Item 2
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="complete-skill",
            name="Complete Skill",
            description="A complete skill for testing",
            source_file_path=str(skill_file),
            source_hash="complete_hash",
            enabled=True,
            source_type="manual",
            created_by="user@example.com",
            origin_url="https://example.com/skill.md",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="debug, python",
            complexity_origin=3,
            use_count=100,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/complete-skill")
            data = response.json()

            assert data["id"] == "complete-skill"
            assert data["name"] == "Complete Skill"
            assert data["description"] == "A complete skill for testing"
            assert (
                data["content"]
                == "# Complete Skill\n\n## Instructions\n\nFull markdown content here.\n\n- Item 1\n- Item 2"
            )
            assert data["enabled"] is True
            assert data["source_type"] == "manual"
            assert data["created_by"] == "user@example.com"
            assert data["origin_url"] == "https://example.com/skill.md"
            assert data["repo_version"] == "1.0.0"
            assert data["local_version"] == "1.0.0"
            assert data["allow_autonomous_edit"] is True
            assert data["use_count"] == 100
            assert "last_used_at" in data
            assert "updated_at" in data

    def test_detail_graceful_degradation_without_projection(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Detail endpoint returns canonical fields when projection unavailable."""
        skill_file = tmp_path / "degraded-detail.md"
        skill_file.write_text("""---
name: Degraded Detail
description: Skill without projection
enabled: true
---
# Degraded Detail

Content.
""")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            _set_db_pool(client, None)

            response = client.get("/skills/degraded-detail")
            data = response.json()

            assert data["id"] == "degraded-detail"
            assert data["name"] == "Degraded Detail"
            assert data["description"] == "Skill without projection"
            assert data["content"] == "# Degraded Detail\n\nContent."
            assert data["enabled"] is True

            assert data.get("source_type") is None
            assert data.get("use_count") is None


class TestSkillUIStatsHeaderContract:
    """Tests for stats header metadata (total, autonomous count, pending count)."""

    def test_stats_metadata_in_list_response(
        self, client: TestClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """List endpoint returns all skills for client-side stats computation."""
        configs = [
            ("sys1", "system", False, None),
            ("auto1", "autonomous", True, None),
            ("auto2", "autonomous", True, {"update": True}),
            ("man1", "manual", False, {"update": True}),
            ("man2", "manual", False, None),
        ]

        for skill_id, source_type, auto_edit, pending in configs:
            skill_file = tmp_path / f"{skill_id}.md"
            skill_file.write_text(f"""---
name: {skill_id}
description: Test
enabled: true
---
# {skill_id}

Content.
""")

        call_count = 0

        def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            skill_id, source_type, auto_edit, pending = configs[call_count % 5]
            call_count += 1

            return MockRecord(
                skill_id=skill_id,
                name=skill_id,
                description="Test",
                source_file_path=str(tmp_path / f"{skill_id}.md"),
                source_hash=f"hash_{skill_id}",
                enabled=True,
                source_type=source_type,
                created_by="system"
                if source_type in ("system", "autonomous")
                else "user",
                origin_url="",
                embedding=None,
                repo_version="1.0.0",
                local_version="1.0.0",
                pending_update=pending,
                allow_autonomous_edit=auto_edit,
                trigger_conditions="",
                complexity_origin=0,
                use_count=10 if source_type == "autonomous" else 0,
                last_used_at=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            )

        mock_db_pool.fetchrow.side_effect = mock_fetchrow

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            data = response.json()

            assert len(data["skills"]) == 5

            autonomous_count = sum(
                1 for s in data["skills"] if s.get("source_type") == "autonomous"
            )
            assert autonomous_count == 2

            pending_count = sum(
                1
                for s in data["skills"]
                if s.get("pending_update") and len(s["pending_update"]) > 0
            )
            assert pending_count == 2
