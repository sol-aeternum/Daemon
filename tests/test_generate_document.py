"""Unit tests for GenerateDocumentTool."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.tools import document as document_module
from orchestrator.tools.document import GenerateDocumentTool


@pytest.fixture
def tool():
    return GenerateDocumentTool()


@pytest.fixture
def temp_gen_dir():
    """Redirect GENERATED_FILES_DIR to a temp dir for testing."""
    with tempfile.TemporaryDirectory() as td:
        temp_path = Path(td)
        # Patch the module-level constant
        original = document_module.GENERATED_FILES_DIR
        document_module.GENERATED_FILES_DIR = temp_path
        yield temp_path
        document_module.GENERATED_FILES_DIR = original


@pytest.mark.asyncio
async def test_csv_generation_with_json_rows(tool, temp_gen_dir):
    content = json.dumps([["Name", "Age", "City"], ["Alice", "30", "Sydney"], ["Bob", "25", "Melbourne"]])
    result_json = await tool.execute(format="csv", content=content)
    result = json.loads(result_json)
    assert result["success"] is True
    assert result["data"]["format"] == "csv"
    assert result["data"]["filename"].endswith(".csv")
    assert result["data"]["file_url"].startswith("/generated-files/")
    assert result["data"]["mime_type"] == "text/csv"
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "Alice" in text
    assert "Bob" in text


@pytest.mark.asyncio
async def test_csv_generation_with_headers_and_rows(tool, temp_gen_dir):
    content = json.dumps({"headers": ["Name", "Score"], "rows": [["Alice", "95"], ["Bob", "88"]]})
    result_json = await tool.execute(format="csv", content=content)
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "Name" in text
    assert "Score" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_csv_generation_with_text_fallback(tool, temp_gen_dir):
    content = "Line one\nLine two\nLine three"
    result_json = await tool.execute(format="csv", content=content)
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "Line one" in text
    assert "Line three" in text


@pytest.mark.asyncio
async def test_csv_with_filename_hint(tool, temp_gen_dir):
    content = json.dumps([["X", "Y"], ["1", "2"]])
    result_json = await tool.execute(format="csv", content=content, filename="my-test-csv")
    result = json.loads(result_json)
    assert result["success"] is True
    assert result["data"]["filename"].startswith("my-test-csv")
    assert (temp_gen_dir / result["data"]["filename"]).exists()


@pytest.mark.asyncio
async def test_docx_generation_simple(tool, temp_gen_dir):
    result_json = await tool.execute(
        format="docx",
        content="Hello world. This is a test document.",
        title="Test Document",
    )
    result = json.loads(result_json)
    assert result["success"] is True
    assert result["data"]["format"] == "docx"
    assert result["data"]["filename"].endswith(".docx")
    assert result["data"]["file_url"].startswith("/generated-files/")
    assert "vnd.openxmlformats" in result["data"]["mime_type"]
    path = temp_gen_dir / result["data"]["filename"]
    assert path.exists()
    assert path.stat().st_size > 0


@pytest.mark.asyncio
async def test_docx_with_sections_and_table(tool, temp_gen_dir):
    result_json = await tool.execute(
        format="docx",
        content="Intro text here.",
        title="Report",
        sections=[
            {"title": "Section One", "body": "Body of section one."},
            {"title": "Section Two", "body": "Body of section two."},
        ],
        table={"headers": ["Name", "Value"], "rows": [["Alpha", "100"], ["Beta", "200"]]},
    )
    result = json.loads(result_json)
    assert result["success"] is True
    assert (temp_gen_dir / result["data"]["filename"]).exists()


@pytest.mark.asyncio
async def test_unsupported_format_returns_error(tool):
    result_json = await tool.execute(format="pdf", content="test")
    result = json.loads(result_json)
    assert result["success"] is False
    assert "Unsupported format" in result["error"]


@pytest.mark.asyncio
async def test_file_url_matches_existing_download_pattern(tool, temp_gen_dir):
    result_json = await tool.execute(format="csv", content=json.dumps([["a", "b"]]))
    result = json.loads(result_json)
    url = result["data"]["file_url"]
    filename = result["data"]["filename"]
    assert url == f"/generated-files/{filename}"
