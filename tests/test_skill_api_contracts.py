"""Tests for skills API contracts including projection metadata and download endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from orchestrator.auth import AdminOrDeviceAuth, AuthenticatedDevice
from orchestrator.config import get_settings
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


class ASGISyncClient:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        async def _request() -> Response:
            transport = ASGITransport(app=self.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url, **kwargs)

        return asyncio_runner(_request())

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        return self.request("DELETE", url, **kwargs)


def asyncio_runner(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)


def _set_db_pool(client: ASGISyncClient, db_pool: AsyncMock | None) -> None:
    """Helper to set db_pool on app state, bypassing type checking."""
    cast(Any, client.app).state.app_state.db_pool = db_pool


@pytest.fixture
def app_with_mock_db(
    mock_db_pool: AsyncMock, fake_authenticated_device: AuthenticatedDevice
) -> FastAPI:
    app = FastAPI()
    app.include_router(skills_router.router)

    mock_app_state = MagicMock()
    mock_app_state.db_pool = mock_db_pool
    mock_app_state.redis = MagicMock()
    mock_app_state.memory_store = MagicMock()
    mock_app_state.video_credits_dal = MagicMock()
    cast(Any, app).state.app_state = mock_app_state

    async def override_device_auth() -> AuthenticatedDevice:
        return fake_authenticated_device

    async def override_admin_or_device_auth() -> AdminOrDeviceAuth:
        return AdminOrDeviceAuth(
            authenticated_device=fake_authenticated_device,
            is_admin=False,
        )

    app.dependency_overrides[skills_router.require_device_auth] = override_device_auth
    app.dependency_overrides[skills_router.require_admin_or_device_auth] = (
        override_admin_or_device_auth
    )

    return app


@pytest.fixture
def client(app_with_mock_db: FastAPI) -> ASGISyncClient:
    """Create test client."""
    return ASGISyncClient(app_with_mock_db)


@pytest.fixture
def mock_db_pool() -> AsyncMock:
    """Create mock database pool with proper async context manager for device auth."""
    from contextlib import asynccontextmanager

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=datetime.now())
    conn.execute = AsyncMock()

    @asynccontextmanager
    async def mock_acquire():
        yield conn

    pool = AsyncMock()
    pool.acquire = MagicMock(side_effect=lambda: mock_acquire())

    return pool


@pytest.fixture
def fake_authenticated_device() -> AuthenticatedDevice:
    return AuthenticatedDevice(
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        device_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        session_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
    )


@pytest.fixture
def sample_skill_content() -> str:
    """Sample valid skill markdown content."""
    return """---
name: Test Skill
description: A test skill for API contract validation
enabled: true
---
# Test Skill

This is a test skill with instructions.
"""


class TestSkillsListContract:
    """Tests for GET /skills endpoint contract."""

    def test_list_skills_without_projection(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Skills list works when db is unavailable (graceful degradation)."""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("""---
name: Test Skill
description: Test description
enabled: true
---
# Test Skill

Content.
""")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            _set_db_pool(client, None)

            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            assert "skills" in data
            assert len(data["skills"]) == 1

            skill = data["skills"][0]
            assert skill["id"] == "test-skill"
            assert skill["name"] == "Test Skill"
            assert skill["description"] == "Test description"
            assert skill["enabled"] is True
            assert "updated_at" in skill
            assert skill.get("source_type") is None
            assert skill.get("repo_version") is None

    def test_list_skills_with_projection_metadata(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills list includes projection metadata when db available."""
        skill_file = tmp_path / "imported-skill.md"
        skill_file.write_text("""---
name: Imported Skill
description: An imported skill
enabled: true
---
# Imported Skill

Content.
""")

        # Mock projection data
        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="imported-skill",
            name="Imported Skill",
            description="An imported skill",
            source_file_path=str(skill_file),
            source_hash="abc123",
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
            use_count=42,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1

            skill = data["skills"][0]
            assert skill["source_type"] == "imported"
            assert skill["repo_version"] == "1.0.0"
            assert skill["local_version"] == "1.0.0"
            assert skill["use_count"] == 42
            assert skill["allow_autonomous_edit"] is False
            assert "last_used_at" in skill

    def test_list_skills_gracefully_handles_projection_failure(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills list returns canonical data when projection lookup fails."""
        skill_file = tmp_path / "failing-skill.md"
        skill_file.write_text("""---
name: Failing Skill
description: Skill where projection fails
enabled: true
---
# Failing Skill

Content.
""")

        mock_db_pool.fetchrow.side_effect = Exception("Database connection failed")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1
            assert data["skills"][0]["id"] == "failing-skill"
            assert data["skills"][0]["name"] == "Failing Skill"

    def test_list_skills_with_legacy_string_timestamp(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills list handles legacy string timestamps without crashing."""
        skill_file = tmp_path / "legacy-skill.md"
        skill_file.write_text("""---
name: Legacy Skill
description: Legacy skill with string timestamp
enabled: true
---
# Legacy Skill

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="legacy-skill",
            name="Legacy Skill",
            description="Legacy skill with string timestamp",
            source_file_path=str(skill_file),
            source_hash="legacy123",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=0,
            use_count=5,
            last_used_at="2024-01-15T10:30:00",  # String instead of datetime
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1

            skill = data["skills"][0]
            assert skill["source_type"] == "manual"
            assert skill["last_used_at"] == "2024-01-15T10:30:00"

    def test_list_skills_with_null_timestamp(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Skills list handles null last_used_at without crashing."""
        skill_file = tmp_path / "null-ts-skill.md"
        skill_file.write_text("""---
name: Null Timestamp Skill
description: Skill with null timestamp
enabled: true
---
# Null Timestamp Skill

Content.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="null-ts-skill",
            name="Null Timestamp Skill",
            description="Skill with null timestamp",
            source_file_path=str(skill_file),
            source_hash="null123",
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=0,
            last_used_at=None,  # Null timestamp
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills")
            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1

            skill = data["skills"][0]
            assert skill["last_used_at"] is None


class TestSkillsGetContract:
    """Tests for GET /skills/{id} endpoint contract."""

    def test_get_skill_without_projection(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Get skill works without projection layer (legacy mode)."""
        skill_file = tmp_path / "legacy-skill.md"
        skill_file.write_text("""---
name: Legacy Skill
description: Legacy test
enabled: true
---
# Legacy Skill

Legacy content.
""")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            _set_db_pool(client, None)

            response = client.get("/skills/legacy-skill")
            assert response.status_code == 200

            data = response.json()
            assert data["id"] == "legacy-skill"
            assert data["name"] == "Legacy Skill"
            assert data["content"] == "# Legacy Skill\n\nLegacy content."
            assert data.get("source_type") is None
            assert data.get("created_by") is None
            assert data.get("origin_url") is None

    def test_get_skill_with_projection_metadata(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Get skill includes full projection metadata."""
        skill_file = tmp_path / "manual-skill.md"
        skill_file.write_text("""---
name: Manual Skill
description: Manually created
enabled: false
---
# Manual Skill

Manual content here.
""")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="manual-skill",
            name="Manual Skill",
            description="Manually created",
            source_file_path=str(skill_file),
            source_hash="def456",
            enabled=False,
            source_type="manual",
            created_by="test_user",
            origin_url="",
            embedding=None,
            repo_version="0.5.0",
            local_version="0.5.0",
            pending_update=None,
            allow_autonomous_edit=True,
            trigger_conditions="python, coding",
            complexity_origin=5,
            use_count=10,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/manual-skill")
            assert response.status_code == 200

            data = response.json()
            assert data["id"] == "manual-skill"
            assert data["source_type"] == "manual"
            assert data["created_by"] == "test_user"
            assert data["allow_autonomous_edit"] is True
            assert data["use_count"] == 10
            assert data["enabled"] is False

    def test_get_nonexistent_skill(self, client: ASGISyncClient) -> None:
        """Get skill returns 404 for missing skill."""
        response = client.get("/skills/nonexistent-skill-12345")
        assert response.status_code == 404

    def test_get_skill_gracefully_handles_projection_failure(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Get skill returns canonical data when projection lookup fails."""
        skill_file = tmp_path / "degraded-skill.md"
        skill_file.write_text("""---
name: Degraded Skill
description: Skill with projection failure
enabled: true
---
# Degraded Skill

Content.
""")

        mock_db_pool.fetchrow.side_effect = Exception("Database query failed")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/degraded-skill")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "degraded-skill"
            assert data["name"] == "Degraded Skill"
            assert data.get("source_type") is None


class TestSkillsDownloadContract:
    """Tests for GET /skills/{id}/download endpoint."""

    def test_download_skill_success(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Download returns valid markdown with correct content-disposition."""
        skill_content = """---
name: Exportable Skill
description: A skill for export testing
enabled: true
---
# Exportable Skill

Export this content.
"""
        skill_file = tmp_path / "exportable-skill.md"
        skill_file.write_text(skill_content)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/exportable-skill/download")
            assert response.status_code == 200

            assert response.headers["content-type"] == "text/markdown; charset=utf-8"
            assert (
                'attachment; filename="exportable-skill.md"'
                in response.headers["content-disposition"]
            )

            body = response.text
            assert "---" in body
            assert "name: Exportable Skill" in body
            assert "description: A skill for export testing" in body
            assert "# Exportable Skill" in body

    def test_download_skill_can_be_reimported(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Downloaded skill can be re-imported (roundtrip compatibility)."""
        original_content = """---
name: Roundtrip Skill
description: Testing roundtrip compatibility
enabled: true
---
# Roundtrip Skill

Roundtrip content with special chars: <>&"'

## Instructions

1. Step one
2. Step two
"""
        skill_file = tmp_path / "roundtrip-skill.md"
        skill_file.write_text(original_content)

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/roundtrip-skill/download")
            downloaded = response.text

            assert "---" in downloaded
            assert "name:" in downloaded
            assert "description:" in downloaded
            assert "enabled:" in downloaded

            assert "Roundtrip content with special chars" in downloaded
            assert "## Instructions" in downloaded

    def test_download_nonexistent_skill(self, client: ASGISyncClient) -> None:
        """Download returns 404 for missing skill."""
        response = client.get("/skills/missing-skill/download")
        assert response.status_code == 404


class TestSkillsUploadCompatibility:
    """Tests for POST /skills/upload endpoint (legacy compatibility)."""

    def test_upload_standard_markdown_format(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Upload accepts standard markdown format (# Title + ## Purpose)."""
        standard_md = """# Standard Skill

## Purpose

This is the purpose description.

## Instructions

Do something useful.
"""

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.post(
                "/skills/upload",
                files={"file": ("standard.md", standard_md, "text/markdown")},
                data={"overwrite": "false"},
            )
            assert response.status_code == 201

            data = response.json()
            assert data["id"] == "standard-skill"
            assert data["name"] == "Standard Skill"
            assert "enabled" in data

    def test_upload_frontmatter_format(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Upload accepts frontmatter format."""
        frontmatter_md = """---
name: Frontmatter Skill
description: From frontmatter
enabled: false
---

# Frontmatter Skill

Content here.
"""

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.post(
                "/skills/upload",
                files={"file": ("front.md", frontmatter_md, "text/markdown")},
                data={"overwrite": "false"},
            )
            assert response.status_code == 201

            data = response.json()
            assert data["name"] == "Frontmatter Skill"
            assert data["description"] == "From frontmatter"
            assert data["enabled"] is False

    def test_upload_conflict_without_overwrite(
        self, client: ASGISyncClient, tmp_path: Path
    ) -> None:
        """Upload returns 409 if skill exists and overwrite=false."""
        existing = tmp_path / "existing.md"
        existing.write_text("---\nname: Existing\ndescription: Exists\n---\n\nContent")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.post(
                "/skills/upload",
                files={
                    "file": (
                        "existing.md",
                        "---\nname: Existing\ndescription: New\n---\n\nBody content here.",
                        "text/markdown",
                    )
                },
            )
            assert response.status_code == 409


class TestSkillsCreateUpdateContract:
    """Tests for POST /skills and PUT /skills/{id} contracts."""

    def test_create_skill(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Create skill returns detail with correct structure."""
        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.post(
                "/skills",
                json={
                    "name": "New Skill",
                    "description": "A new skill",
                    "content": "# New Skill\n\nInstructions.",
                    "enabled": True,
                },
            )
            assert response.status_code == 201

            data = response.json()
            assert data["id"] == "new-skill"
            assert data["name"] == "New Skill"
            assert data["description"] == "A new skill"
            assert data["content"] == "# New Skill\n\nInstructions."
            assert data["enabled"] is True
            assert "updated_at" in data

    def test_update_skill(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Update skill returns updated detail."""
        # Create first
        skill_file = tmp_path / "updateable.md"
        skill_file.write_text("---\nname: Updateable\ndescription: Original\n---\n\nOriginal")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.put(
                "/skills/updateable",
                json={
                    "name": "Updated Name",
                    "description": "Updated description",
                    "content": "Updated content",
                    "enabled": False,
                },
            )
            assert response.status_code == 200

            data = response.json()
            assert data["name"] == "Updated Name"
            assert data["description"] == "Updated description"
            assert data["content"] == "Updated content"
            assert data["enabled"] is False


class TestSkillMetadataTypes:
    """Tests for metadata field types and constraints."""

    def test_source_type_enum_values(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """source_type returns valid enum values."""
        skill_file = tmp_path / "enum-skill.md"
        skill_file.write_text("---\nname: Enum Skill\ndescription: Test\n---\n\nContent")

        valid_types = ["system", "imported", "manual", "autonomous"]

        for source_type in valid_types:
            mock_db_pool.fetchrow.return_value = MockRecord(
                skill_id="enum-skill",
                name="Enum Skill",
                description="Test",
                source_file_path=str(skill_file),
                source_hash="hash",
                enabled=True,
                source_type=source_type,
                created_by="system",
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
                response = client.get("/skills/enum-skill")
                data = response.json()
                assert data["source_type"] == source_type

    def test_pending_update_structure(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """pending_update returns JSON object when present."""
        skill_file = tmp_path / "pending-skill.md"
        skill_file.write_text("---\nname: Pending Skill\ndescription: Test\n---\n\nContent")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="pending-skill",
            name="Pending Skill",
            description="Test",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="autonomous",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.0.0",
            pending_update={"changes": ["line1", "line2"], "reason": "improvement"},
            allow_autonomous_edit=True,
            trigger_conditions="",
            complexity_origin=3,
            use_count=5,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.get("/skills/pending-skill")
            data = response.json()
            assert data["pending_update"] == {
                "changes": ["line1", "line2"],
                "reason": "improvement",
            }
            assert data["local_version"] == "1.0.0"
            assert data["repo_version"] == "2.0.0"


class TestSkillsDeleteContract:
    """Tests for DELETE /skills/{id} endpoint."""

    def test_delete_existing_skill(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Delete removes skill and returns status."""
        skill_file = tmp_path / "deletable.md"
        skill_file.write_text("---\nname: Deletable\ndescription: To delete\n---\n\nContent")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.delete("/skills/deletable")
            assert response.status_code == 200
            assert response.json() == {"status": "deleted"}

            response = client.get("/skills/deletable")
            assert response.status_code == 404

    def test_delete_nonexistent_skill(self, client: ASGISyncClient) -> None:
        """Delete returns 404 for missing skill."""
        response = client.delete("/skills/nonexistent-skill-xyz")
        assert response.status_code == 404


class TestSkillsAutonomousEditContract:
    """Tests for PATCH /skills/{id}/autonomous-edit endpoint."""

    def test_toggle_autonomous_edit_for_protected_skill(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Toggle autonomous edit for a protected skill (system/imported/manual) to opt-in."""
        skill_file = tmp_path / "system-skill.md"
        skill_file.write_text("---\nname: System Skill\ndescription: Test\n---\n\nContent")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="system-skill",
            name="System Skill",
            description="Test",
            source_file_path=str(skill_file),
            source_hash="hash",
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
            use_count=0,
            last_used_at=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        mock_db_pool.execute.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.patch(
                "/skills/system-skill/autonomous-edit",
                json={"allow_autonomous_edit": True},
            )
            assert response.status_code == 200

            data = response.json()
            assert data["skill_id"] == "system-skill"
            assert data["allow_autonomous_edit"] is True

    def test_toggle_autonomous_edit_for_imported_skill(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Toggle autonomous edit for an imported skill."""
        skill_file = tmp_path / "imported-skill.md"
        skill_file.write_text("---\nname: Imported Skill\ndescription: Test\n---\n\nContent")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="imported-skill",
            name="Imported Skill",
            description="Test",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="imported",
            created_by="user",
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
        mock_db_pool.execute.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.patch(
                "/skills/imported-skill/autonomous-edit",
                json={"allow_autonomous_edit": True},
            )
            assert response.status_code == 200
            assert response.json()["allow_autonomous_edit"] is True

    def test_toggle_autonomous_edit_no_db(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Toggle without db still returns success for existing skill."""
        skill_file = tmp_path / "manual-skill.md"
        skill_file.write_text("---\nname: Manual Skill\ndescription: Test\n---\n\nContent")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            _set_db_pool(client, None)

            response = client.patch(
                "/skills/manual-skill/autonomous-edit",
                json={"allow_autonomous_edit": True},
            )
            assert response.status_code == 200

    def test_toggle_autonomous_edit_missing_skill(self, client: ASGISyncClient) -> None:
        """Toggle returns 404 for missing skill."""
        response = client.patch(
            "/skills/nonexistent-skill/autonomous-edit",
            json={"allow_autonomous_edit": True},
        )
        assert response.status_code == 404


class TestSkillsPendingUpdateContract:
    """Tests for POST /skills/{id}/pending-update endpoint."""

    def test_apply_pending_update_success(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Apply pending update routes through upgrade service to update skill safely."""

        skill_file = tmp_path / "pending-skill.md"
        skill_file.write_text(
            "---\nname: Pending Skill\ndescription: Old\nrepo_version: 1.0.0\nlocal_version: 1.0.0\n---\n\nOld content"
        )

        repo_content = "---\nname: Pending Skill\ndescription: Updated\nrepo_version: 2.0.0\n---\n\nNew content from repo"

        call_count = 0

        def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockRecord(
                skill_id="pending-skill",
                name="Pending Skill",
                description="Old",
                source_file_path=str(skill_file),
                source_hash="hash",
                enabled=True,
                source_type="system",
                created_by="system",
                origin_url="",
                embedding=None,
                repo_version="2.0.0",
                local_version="1.0.0",
                pending_update={
                    "repo_hash": "newhash",
                    "repo_version": "2.0.0",
                    "repo_content": repo_content,
                },
                allow_autonomous_edit=False,
                trigger_conditions="",
                complexity_origin=0,
                use_count=5,
                last_used_at=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                last_synced_at=datetime.now(),
            )

        mock_db_pool.fetchrow.side_effect = mock_fetchrow
        mock_db_pool.fetchval.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with (
                patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path),
                patch(
                    "orchestrator.skills_upgrade.embed_skill_content",
                    AsyncMock(return_value=[0.1, 0.2, 0.3]),
                ),
            ):
                response = client.post(
                    "/skills/pending-skill/pending-update",
                    json={"action": "apply"},
                )
                assert response.status_code == 200
                assert response.json()["action"] == "applied"
                assert "Updated" in skill_file.read_text()

    def test_dismiss_pending_update_success(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Dismiss pending update clears pending_update field."""
        skill_file = tmp_path / "dismiss-skill.md"
        skill_file.write_text("---\nname: Dismiss Skill\ndescription: Local\n---\n\nLocal content")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="dismiss-skill",
            name="Dismiss Skill",
            description="Local",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.5.0",
            pending_update={"repo_version": "2.0.0", "repo_content": "new"},
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            use_count=3,
            last_used_at=datetime.now(),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
        mock_db_pool.fetchval.return_value = "UPDATE 1"

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            response = client.post(
                "/skills/dismiss-skill/pending-update",
                json={"action": "dismiss"},
            )
            assert response.status_code == 200

            data = response.json()
            assert data["skill_id"] == "dismiss-skill"
            assert data["action"] == "dismissed"

            # Original file should be unchanged
            content = skill_file.read_text()
            assert "Local content" in content

    def test_pending_update_no_pending(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Apply/dismiss returns 400 when no pending update exists."""
        skill_file = tmp_path / "no-pending.md"
        skill_file.write_text("---\nname: No Pending\ndescription: Test\n---\n\nContent")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="no-pending",
            name="No Pending",
            description="Test",
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
            response = client.post(
                "/skills/no-pending/pending-update",
                json={"action": "apply"},
            )
            assert response.status_code == 400
            assert "No pending update" in response.json()["detail"]

    def test_pending_update_no_db(self, client: ASGISyncClient, tmp_path: Path) -> None:
        """Pending update returns 503 when database unavailable."""
        skill_file = tmp_path / "no-db-skill.md"
        skill_file.write_text("---\nname: No DB Skill\ndescription: Test\n---\n\nContent")

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            _set_db_pool(client, None)

            response = client.post(
                "/skills/no-db-skill/pending-update",
                json={"action": "apply"},
            )
            assert response.status_code == 503
            assert "Database not available" in response.json()["detail"]

    def test_pending_update_missing_skill(
        self, client: ASGISyncClient, mock_db_pool: AsyncMock
    ) -> None:
        """Pending update returns 404 when skill projection not found."""
        mock_db_pool.fetchrow.return_value = None

        response = client.post(
            "/skills/nonexistent-skill/pending-update",
            json={"action": "apply"},
        )
        assert response.status_code == 404

    def test_pending_update_invalid_action(
        self, client: ASGISyncClient, tmp_path: Path, mock_db_pool: AsyncMock
    ) -> None:
        """Pending update returns 400 for invalid action."""
        skill_file = tmp_path / "invalid-action.md"
        skill_file.write_text("---\nname: Invalid Action\ndescription: Test\n---\n\nContent")

        mock_db_pool.fetchrow.return_value = MockRecord(
            skill_id="invalid-action",
            name="Invalid Action",
            description="Test",
            source_file_path=str(skill_file),
            source_hash="hash",
            enabled=True,
            source_type="manual",
            created_by="user",
            origin_url="",
            embedding=None,
            repo_version="1.0.0",
            local_version="1.0.0",
            pending_update={"repo_content": "new"},
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
            response = client.post(
                "/skills/invalid-action/pending-update",
                json={"action": "invalid"},
            )
            assert response.status_code == 400
            assert "Invalid action" in response.json()["detail"]


class TestAdminConsolidationAuditRoute:
    def test_admin_consolidation_audit_queries_time_range(
        self,
        app_with_mock_db: FastAPI,
        fake_authenticated_device: AuthenticatedDevice,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()

        async def override_admin_auth() -> AdminOrDeviceAuth:
            return AdminOrDeviceAuth(
                authenticated_device=fake_authenticated_device,
                is_admin=True,
            )

        app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth] = (
            override_admin_auth
        )

        user_id = uuid.uuid4()
        row_id = uuid.uuid4()
        memory_store = MagicMock()
        memory_store.list_consolidation_nudge_actions = AsyncMock(
            return_value=[
                {
                    "id": row_id,
                    "user_id": user_id,
                    "action_type": "delete",
                    "status": "failed",
                    "reason": "delete failed: projection unavailable",
                    "run_at": datetime(2026, 1, 3, 4, 5, tzinfo=timezone.utc),
                }
            ]
        )
        cast(Any, app_with_mock_db).state.app_state.memory_store = memory_store

        test_client = ASGISyncClient(app_with_mock_db)
        response = test_client.get(
            "/skills/admin/consolidation-audit",
            params={
                "user_id": str(user_id),
                "action_type": "delete",
                "status": "failed",
                "since": "2026-01-01T00:00:00+00:00",
                "until": "2026-02-01T00:00:00+00:00",
                "limit": "25",
            },
            headers={"Authorization": "Bearer test-secret-key"},
        )

        assert response.status_code == 200
        assert response.json()["actions"] == [
            {
                "id": str(row_id),
                "user_id": str(user_id),
                "action_type": "delete",
                "status": "failed",
                "reason": "delete failed: projection unavailable",
                "run_at": "2026-01-03T04:05:00Z",
            }
        ]
        memory_store.list_consolidation_nudge_actions.assert_awaited_once_with(
            user_id=user_id,
            action_type="delete",
            status="failed",
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 2, 1, tzinfo=timezone.utc),
            limit=25,
        )

    def test_admin_consolidation_audit_rejects_non_admin_device(
        self,
        app_with_mock_db: FastAPI,
        fake_authenticated_device: AuthenticatedDevice,
    ) -> None:
        non_admin_auth = AdminOrDeviceAuth(
            authenticated_device=fake_authenticated_device,
            is_admin=False,
        )

        async def override_non_admin_auth() -> AdminOrDeviceAuth:
            return non_admin_auth

        app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth] = (
            override_non_admin_auth
        )

        test_client = ASGISyncClient(app_with_mock_db)
        response = test_client.get(
            "/skills/admin/consolidation-audit",
            headers={"Authorization": "Bearer valid-device-token"},
        )

        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]


class TestAdminSyncRoute:
    def test_admin_sync_returns_503_when_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Admin sync returns 503 when db pool is not available."""
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")

        app = FastAPI()
        app.include_router(skills_router.router)
        app.state.app_state = MagicMock(spec=[])

        test_client = ASGISyncClient(app)
        response = test_client.post(
            "/skills/admin/sync",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert response.status_code == 503

    def test_admin_sync_calls_run_upgrade_sync(
        self,
        app_with_mock_db: FastAPI,
        mock_db_pool: AsyncMock,
        fake_authenticated_device: AuthenticatedDevice,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Admin sync route invokes run_upgrade_sync with repo contents."""
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()

        async def override_admin_auth() -> AdminOrDeviceAuth:
            return AdminOrDeviceAuth(
                authenticated_device=fake_authenticated_device,
                is_admin=True,
            )

        app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth] = (
            override_admin_auth
        )

        repo_dir = tmp_path / "repo_skills"
        repo_dir.mkdir()
        (repo_dir / "system-skill.md").write_text(
            "---\nname: System Skill\ndescription: A repo skill\nenabled: true\n---\n# System Skill\nRepo content."
        )

        from orchestrator.skills_upgrade import UpgradeResult, UpgradeAction

        mock_result = UpgradeResult(
            actions=[
                UpgradeAction(
                    skill_id="system-skill",
                    action="insert",
                    success=True,
                )
            ],
            total_inserts=1,
        )

        with (
            patch(
                "orchestrator.skills_upgrade.REPO_SKILLS_DIR",
                repo_dir,
            ),
            patch(
                "orchestrator.routes.skills.run_upgrade_sync",
                AsyncMock(return_value=mock_result),
            ),
        ):
            test_client = ASGISyncClient(app_with_mock_db)
            response = test_client.post(
                "/skills/admin/sync",
                headers={"Authorization": "Bearer test-secret-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["repo_skills_found"] == 1
        assert data["success"] is True
        assert data["total_inserts"] == 1

    def test_admin_sync_with_no_repo_skills(
        self,
        app_with_mock_db: FastAPI,
        mock_db_pool: AsyncMock,
        fake_authenticated_device: AuthenticatedDevice,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Admin sync handles missing repo skills directory gracefully."""
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()

        async def override_admin_auth() -> AdminOrDeviceAuth:
            return AdminOrDeviceAuth(
                authenticated_device=fake_authenticated_device,
                is_admin=True,
            )

        app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth] = (
            override_admin_auth
        )

        empty_dir = tmp_path / "empty_repo_skills"
        empty_dir.mkdir()

        test_client = ASGISyncClient(app_with_mock_db)
        with patch(
            "orchestrator.skills_upgrade.REPO_SKILLS_DIR",
            empty_dir,
        ):
            response = test_client.post(
                "/skills/admin/sync",
                headers={"Authorization": "Bearer test-secret-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["repo_skills_found"] == 0
        assert data["total_inserts"] == 0

    def test_admin_sync_rejects_missing_auth(
        self, app_with_mock_db: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin sync returns 401 when no auth header is provided."""
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()
        del app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth]

        test_client = ASGISyncClient(app_with_mock_db)
        response = test_client.post("/skills/admin/sync")

        assert response.status_code == 401
        assert "Missing or invalid authorization header" in response.json()["detail"]

    def test_admin_sync_rejects_invalid_token(
        self, app_with_mock_db: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin sync returns 401 when an invalid token is provided.

        With the new require_admin_or_device_auth dependency, an invalid token
        that doesn't match the admin key falls through to device auth,
        which fails with 401.
        """
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()
        del app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth]

        test_client = ASGISyncClient(app_with_mock_db)
        response = test_client.post(
            "/skills/admin/sync",
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == 401
        assert "Invalid or expired access token" in response.json()["detail"]

    def test_admin_sync_rejects_non_admin_device(
        self,
        app_with_mock_db: FastAPI,
        fake_authenticated_device: AuthenticatedDevice,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Admin sync returns 403 when a non-admin device auth is used.

        Authenticated devices without admin privilege must not trigger repo skill sync.
        """
        monkeypatch.setenv("DAEMON_ADMIN_API_KEY", "test-secret-key")
        get_settings.cache_clear()

        non_admin_auth = AdminOrDeviceAuth(
            authenticated_device=fake_authenticated_device,
            is_admin=False,
        )

        async def override_non_admin_auth() -> AdminOrDeviceAuth:
            return non_admin_auth

        app_with_mock_db.dependency_overrides[skills_router.require_admin_or_device_auth] = (
            override_non_admin_auth
        )

        test_client = ASGISyncClient(app_with_mock_db)
        response = test_client.post(
            "/skills/admin/sync",
            headers={"Authorization": "Bearer valid-device-token"},
        )

        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]
