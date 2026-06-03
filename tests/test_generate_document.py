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
async def test_csv_json_list_of_lists_no_header_duplication(tool, temp_gen_dir):
    content = json.dumps([["Name", "Score"], ["Alice", "95"], ["Bob", "88"]])
    result_json = await tool.execute(format="csv", content=content)
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    name_count = text.count("Name")
    alice_count = text.count("Alice")
    assert name_count == 1, f"'Name' appears {name_count} times (expected 1, not duplicated)"
    assert alice_count == 1, f"'Alice' appears {alice_count} times (expected 1)"


@pytest.mark.asyncio
async def test_csv_single_header_row_no_data_duplication(tool, temp_gen_dir):
    content = json.dumps([["Name", "Score"]])
    result_json = await tool.execute(format="csv", content=content)
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    lines = text.strip().splitlines()
    assert len(lines) == 1, f"Expected 1 header line, got {len(lines)}: {lines}"
    assert "Name" in lines[0]
    name_count = text.count("Name")
    assert name_count == 1, f"'Name' appears {name_count} times (expected 1)"


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
async def test_csv_table_parameter_takes_priority_over_content(tool, temp_gen_dir):
    """table={headers,rows} must be used before content when both are present."""
    result_json = await tool.execute(
        format="csv",
        content="this text must be ignored",
        table={"headers": ["Name", "Score"], "rows": [["Alice", "95"], ["Bob", "88"]]},
    )
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "Name" in text
    assert "Score" in text
    assert "Alice" in text
    assert "Bob" in text
    assert "this text must be ignored" not in text


@pytest.mark.asyncio
async def test_csv_table_with_mixed_type_cells(tool, temp_gen_dir):
    """table parameter should coerce non-string cell values to strings."""
    result_json = await tool.execute(
        format="csv",
        content="ignored",
        table={"headers": ["ID", "Active", "Score"], "rows": [[1, True, 3.5], [2, False, 2.1]]},
    )
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "ID" in text
    assert "1" in text
    assert "True" in text or "true" in text.lower()


@pytest.mark.asyncio
async def test_csv_table_missing_headers_or_rows_is_ignored(tool, temp_gen_dir):
    """table without valid headers/rows falls through to content parsing."""
    result_json = await tool.execute(
        format="csv",
        content="fallback1\nfallback2",
        table={"headers": "not-a-list", "rows": []},
    )
    result = json.loads(result_json)
    assert result["success"] is True
    text = (temp_gen_dir / result["data"]["filename"]).read_text()
    assert "fallback1" in text
    assert "fallback2" in text


@pytest.mark.asyncio
async def test_file_url_matches_existing_download_pattern(tool, temp_gen_dir):
    result_json = await tool.execute(format="csv", content=json.dumps([["a", "b"]]))
    result = json.loads(result_json)
    url = result["data"]["file_url"]
    filename = result["data"]["filename"]
    assert url == f"/generated-files/{filename}"
