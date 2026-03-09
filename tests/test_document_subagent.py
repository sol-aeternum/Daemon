"""Unit tests for DocumentSubagent."""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.subagents.document import DocumentSubagent


@pytest.fixture
def document_subagent():
    """Create a DocumentSubagent instance for testing."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    return DocumentSubagent(config)


@pytest.mark.asyncio
async def test_document_subagent_initialization():
    """Test DocumentSubagent initialization with agent_type."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    assert agent.agent_type.value == "document"
    assert agent.api_key == "test-key"
    assert agent.base_url == "https://test.api/v1"
    assert agent.model == "anthropic/claude-sonnet-4.5"


@pytest.mark.asyncio
async def test_document_subagent_initialization_with_env_vars(monkeypatch):
    """Test DocumentSubagent initialization with environment variables."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://env.api/v1")

    agent = DocumentSubagent()

    assert agent.api_key == "env-key"
    assert agent.base_url == "https://env.api/v1"


@pytest.mark.asyncio
async def test_document_subagent_execute_missing_api_key():
    """Test DocumentSubagent execute method with missing API key."""
    agent = DocumentSubagent({})

    result = await agent.execute("Create a document", {"format": "docx"})

    assert result.success is False
    assert result.error is not None
    assert "OPENROUTER_API_KEY not configured" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_unsupported_format():
    """Test DocumentSubagent execute method with unsupported format."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    # Mock load_document_skill to raise ValueError for unsupported format
    with patch(
        "orchestrator.subagents.document.load_document_skill",
        side_effect=ValueError("Unsupported format"),
    ):
        result = await agent.execute("Create a document", {"format": "pptx"})

        assert result.success is False
        assert result.error is not None
        assert "Unsupported format: pptx" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_code_generation_failure(document_subagent):
    """Test DocumentSubagent execute method when code generation fails."""
    # Mock load_document_skill to return some content
    with patch(
        "orchestrator.subagents.document.load_document_skill",
        return_value="Skill content",
    ):
        # Mock _generate_code to return failure
        with patch.object(
            document_subagent,
            "_generate_code",
            return_value={"success": False, "error": "Generation failed"},
        ):
            result = await document_subagent.execute(
                "Create a document", {"format": "docx"}
            )

            assert result.success is False
    assert "Generation failed" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_empty_code(document_subagent):
    """Test DocumentSubagent execute method when generated code is empty."""
    # Mock load_document_skill to return some content
    with patch(
        "orchestrator.subagents.document.load_document_skill",
        return_value="Skill content",
    ):
        # Mock _generate_code to return empty code
        with patch.object(
            document_subagent,
            "_generate_code",
            return_value={"success": True, "code": ""},
        ):
            result = await document_subagent.execute(
                "Create a document", {"format": "docx"}
            )

            assert result.success is False
            assert "LLM returned empty code" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_code_execution_failure(document_subagent):
    """Test DocumentSubagent execute method when code execution fails."""
    # Mock load_document_skill to return some content
    with patch(
        "orchestrator.subagents.document.load_document_skill",
        return_value="Skill content",
    ):
        # Mock _generate_code to return some code
        with patch.object(
            document_subagent,
            "_generate_code",
            return_value={"success": True, "code": "print('Hello')"},
        ):
            # Mock _execute_sandbox to return failure
            with patch.object(
                document_subagent,
                "_execute_sandbox",
                return_value={"success": False, "error": "Execution failed"},
            ):
                result = await document_subagent.execute(
                    "Create a document", {"format": "docx"}
                )

                assert result.success is False
    assert "Execution failed" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_file_not_found(document_subagent):
    """Test DocumentSubagent execute method when generated file is not found."""
    # Mock load_document_skill to return some content
    with patch(
        "orchestrator.subagents.document.load_document_skill",
        return_value="Skill content",
    ):
        # Mock _generate_code to return some code
        with patch.object(
            document_subagent,
            "_generate_code",
            return_value={"success": True, "code": "print('Hello')"},
        ):
            # Mock _execute_sandbox to return success but file not found
            with patch.object(
                document_subagent,
                "_execute_sandbox",
                return_value={"success": True, "file_path": "/tmp/nonexistent.docx"},
            ):
                result = await document_subagent.execute(
                    "Create a document", {"format": "docx"}
                )

                assert result.success is False
                assert "Generated file not found" in result.error


@pytest.mark.asyncio
async def test_document_subagent_execute_success_docx(document_subagent):
    """Test DocumentSubagent execute method with successful docx generation."""
    # Create a temporary file to simulate generated file
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
        tmp_file.write(b"Fake DOCX content")
        tmp_file_path = tmp_file.name

    try:
        # Mock load_document_skill to return some content
        with patch(
            "orchestrator.subagents.document.load_document_skill",
            return_value="Skill content",
        ):
            # Mock _generate_code to return some code
            with patch.object(
                document_subagent,
                "_generate_code",
                return_value={"success": True, "code": "print('Hello')"},
            ):
                # Mock _execute_sandbox to return success with file path
                with patch.object(
                    document_subagent,
                    "_execute_sandbox",
                    return_value={"success": True, "file_path": tmp_file_path},
                ):
                    # Mock _persist_file to return a predictable path
                    with patch.object(
                        document_subagent,
                        "_persist_file",
                        return_value=Path(tmp_file_path),
                    ):
                        result = await document_subagent.execute(
                            "Create a document", {"format": "docx"}
                        )

                        assert result.success is True
                        assert result.data["format"] == "docx"
                        assert "file_url" in result.data
                        assert "filename" in result.data
                        assert "generation_code" in result.data
    finally:
        # Clean up temporary file
        os.unlink(tmp_file_path)


@pytest.mark.asyncio
async def test_document_subagent_execute_real_sandbox_output_survives_until_persist():
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    generated_code = """
with open('output.docx', 'w', encoding='utf-8') as f:
    f.write('resume body')
"""

    persisted_target = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    persisted_target_path = Path(persisted_target.name)
    persisted_target.close()

    seen_source: dict[str, str] = {}

    def fake_persist(
        source_path: str,
        doc_format: str,
        filename_hint: str = "",
    ) -> Path:
        assert doc_format == "docx"
        assert filename_hint == ""
        seen_source["path"] = source_path
        assert Path(source_path).exists()
        return persisted_target_path

    try:
        with patch(
            "orchestrator.subagents.document.load_document_skill",
            return_value="Skill content",
        ):
            with patch.object(
                agent,
                "_generate_code",
                return_value={"success": True, "code": generated_code},
            ):
                with patch.object(agent, "_persist_file", side_effect=fake_persist):
                    result = await agent.execute("Create a resume", {"format": "docx"})

        assert result.success is True
        assert "path" in seen_source
        assert not Path(seen_source["path"]).exists()
    finally:
        persisted_target_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_document_subagent_execute_success_csv(document_subagent):
    """Test DocumentSubagent execute method with successful csv generation."""
    # Create a temporary file to simulate generated file
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_file:
        tmp_file.write(b"Fake CSV content")
        tmp_file_path = tmp_file.name

    try:
        # Mock load_document_skill to return some content
        with patch(
            "orchestrator.subagents.document.load_document_skill",
            return_value="Skill content",
        ):
            # Mock _generate_code to return some code
            with patch.object(
                document_subagent,
                "_generate_code",
                return_value={"success": True, "code": "print('Hello')"},
            ):
                # Mock _execute_sandbox to return success with file path
                with patch.object(
                    document_subagent,
                    "_execute_sandbox",
                    return_value={"success": True, "file_path": tmp_file_path},
                ):
                    # Mock _persist_file to return a predictable path
                    with patch.object(
                        document_subagent,
                        "_persist_file",
                        return_value=Path(tmp_file_path),
                    ):
                        result = await document_subagent.execute(
                            "Create a document", {"format": "csv"}
                        )

                        assert result.success is True
                        assert result.data["format"] == "csv"
                        assert "file_url" in result.data
                        assert "filename" in result.data
                        assert "generation_code" in result.data
    finally:
        # Clean up temporary file
        os.unlink(tmp_file_path)


@pytest.mark.asyncio
async def test_document_subagent_execute_passes_filename_hint(document_subagent):
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
        tmp_file.write(b"docx")
        tmp_file_path = tmp_file.name

    persisted_path = Path(tmp_file_path)

    try:
        with patch(
            "orchestrator.subagents.document.load_document_skill",
            return_value="Skill content",
        ):
            with patch.object(
                document_subagent,
                "_generate_code",
                return_value={"success": True, "code": "print('ok')"},
            ):
                with patch.object(
                    document_subagent,
                    "_execute_sandbox",
                    return_value={"success": True, "file_path": tmp_file_path},
                ):
                    with patch.object(
                        document_subagent,
                        "_persist_file",
                        return_value=persisted_path,
                    ) as mock_persist:
                        result = await document_subagent.execute(
                            "Create quarterly report",
                            {
                                "format": "docx",
                                "filename": "quarterly-status-report",
                            },
                        )

        assert result.success is True
        assert mock_persist.call_count == 1
        assert (
            mock_persist.call_args.kwargs["filename_hint"] == "quarterly-status-report"
        )
    finally:
        os.unlink(tmp_file_path)


def test_document_subagent_persist_file_uses_slug_filename(document_subagent):
    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = Path(tmp_dir) / "source.docx"
        source_path.write_bytes(b"docx")
        generated_dir = Path(tmp_dir) / "generated_files"

        with patch(
            "orchestrator.subagents.document.GENERATED_FILES_DIR", generated_dir
        ):
            persisted = document_subagent._persist_file(
                str(source_path),
                "docx",
                filename_hint="Quarterly Report 2026!!",
            )

        assert persisted.name == "quarterly-report-2026.docx"
        assert persisted.exists()


def test_document_subagent_persist_file_collision_suffix(document_subagent):
    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = Path(tmp_dir) / "source.docx"
        source_path.write_bytes(b"docx")
        generated_dir = Path(tmp_dir) / "generated_files"

        with patch(
            "orchestrator.subagents.document.GENERATED_FILES_DIR", generated_dir
        ):
            first = document_subagent._persist_file(
                str(source_path),
                "docx",
                filename_hint="Meeting Notes March",
            )
            second = document_subagent._persist_file(
                str(source_path),
                "docx",
                filename_hint="Meeting Notes March",
            )

        assert first.name == "meeting-notes-march.docx"
        assert second.name.startswith("meeting-notes-march-")
        assert second.suffix == ".docx"
        assert second.name != first.name


def test_document_subagent_persist_file_uuid_fallback(document_subagent):
    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = Path(tmp_dir) / "source.docx"
        source_path.write_bytes(b"docx")
        generated_dir = Path(tmp_dir) / "generated_files"

        with patch(
            "orchestrator.subagents.document.GENERATED_FILES_DIR", generated_dir
        ):
            persisted = document_subagent._persist_file(
                str(source_path),
                "docx",
                filename_hint="../../",
            )

        assert persisted.suffix == ".docx"
        _ = uuid.UUID(persisted.stem)


@pytest.mark.asyncio
async def test_document_subagent_load_document_skill_docx():
    """Test that load_document_skill is called with correct format for docx."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    with patch("orchestrator.subagents.document.load_document_skill") as mock_load:
        mock_load.return_value = "Docx skill content"

        # Mock other dependencies to avoid actual execution
        with patch.object(
            agent,
            "_generate_code",
            return_value={"success": True, "code": "print('Hello')"},
        ):
            with patch.object(
                agent,
                "_execute_sandbox",
                return_value={"success": True, "file_path": "/tmp/test.docx"},
            ):
                with patch.object(
                    agent, "_persist_file", return_value=Path("/tmp/test.docx")
                ):
                    await agent.execute("Create a document", {"format": "docx"})

                    mock_load.assert_called_once_with("docx")


@pytest.mark.asyncio
async def test_document_subagent_load_document_skill_csv():
    """Test that load_document_skill is called with correct format for csv."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    with patch("orchestrator.subagents.document.load_document_skill") as mock_load:
        mock_load.return_value = "Csv skill content"

        # Mock other dependencies to avoid actual execution
        with patch.object(
            agent,
            "_generate_code",
            return_value={"success": True, "code": "print('Hello')"},
        ):
            with patch.object(
                agent,
                "_execute_sandbox",
                return_value={"success": True, "file_path": "/tmp/test.csv"},
            ):
                with patch.object(
                    agent, "_persist_file", return_value=Path("/tmp/test.csv")
                ):
                    await agent.execute("Create a document", {"format": "csv"})

                    mock_load.assert_called_once_with("csv")


@pytest.mark.asyncio
async def test_document_subagent_execute_valid_python_code():
    """Test DocumentSubagent execute method with valid Python code execution."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    # Valid Python code that creates the expected output file
    valid_code = """
with open('output.docx', 'w') as f:
    f.write('Test content')
"""

    result = await agent._execute_sandbox(valid_code, "docx")

    try:
        # Print error for debugging
        if not result["success"]:
            print(f"Execution error: {result.get('error')}")

        assert result["success"] is True
        assert "file_path" in result
        assert Path(result["file_path"]).exists()
        assert result.get("cleanup") is True
    finally:
        if result.get("success") and result.get("file_path"):
            Path(result["file_path"]).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_document_subagent_execute_uses_runtime_interpreter():
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    captured_cmd: list[str] = []

    def fake_run(
        cmd: list[str],
        cwd: str,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> MagicMock:
        _ = capture_output
        _ = text
        _ = timeout
        captured_cmd.extend(cmd)
        output_file = Path(cwd) / "output.docx"
        output_file.write_text("ok", encoding="utf-8")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    with patch("orchestrator.subagents.document.subprocess.run", side_effect=fake_run):
        result = await agent._execute_sandbox("print('ok')", "docx")

    assert result["success"] is True
    assert captured_cmd[0] == sys.executable


@pytest.mark.asyncio
async def test_document_subagent_execute_docx_missing_dependency_message():
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "ModuleNotFoundError: No module named 'docx'"

    with patch(
        "orchestrator.subagents.document.subprocess.run", return_value=mock_result
    ):
        result = await agent._execute_sandbox("from docx import Document", "docx")

    assert result["success"] is False
    assert "python-docx is not available" in result["error"]
    assert sys.executable in result["error"]


@pytest.mark.asyncio
async def test_document_subagent_execute_invalid_python_code():
    """Test DocumentSubagent execute method with invalid Python code."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    # Invalid Python code
    invalid_code = """
invalid syntax here
"""

    result = await agent._execute_sandbox(invalid_code, "docx")

    assert result["success"] is False
    assert "Code execution failed" in result["error"]


@pytest.mark.asyncio
async def test_document_subagent_execute_code_timeout():
    """Test DocumentSubagent execute method when code execution times out."""
    config = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://test.api/v1",
    }
    agent = DocumentSubagent(config)

    # Code that will timeout (infinite loop)
    timeout_code = """
while True:
    pass
"""

    # Temporarily reduce timeout for faster testing
    original_timeout = agent.timeout
    agent.timeout = 1.0

    try:
        result = await agent._execute_sandbox(timeout_code, "docx")

        assert result["success"] is False
        assert "timed out" in result["error"]
    finally:
        # Restore original timeout
        agent.timeout = original_timeout
