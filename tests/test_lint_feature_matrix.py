"""Tests for scripts/lint_feature_matrix.py via subprocess invocation."""

from __future__ import annotations

import subprocess
import re
import sys
from pathlib import Path
from subprocess import CompletedProcess

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lint_feature_matrix.py"
REAL_MATRIX = REPO_ROOT / "docs" / "FEATURE_MATRIX.md"


def count_feature_rows(matrix_text: str) -> int:
    """Count non-category-separator feature rows in a FEATURE_MATRIX.md table."""
    lines = matrix_text.splitlines()
    in_matrix = False
    count = 0
    pipe_lines_seen = 0
    for line in lines:
        if line == "## Feature Matrix":
            in_matrix = True
            continue
        if in_matrix and line.startswith("## "):
            break
        if not in_matrix:
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip()[1:-1].split("|")]
        if len(cells) != 7:
            continue
        pipe_lines_seen += 1
        if pipe_lines_seen <= 2:
            continue
        feature = cells[0]
        if bool(re.fullmatch(r"\*\*[^*]+\*\*", feature)) and all(
            c == "—" for c in cells[1:]
        ):
            continue
        count += 1
    return count


class TestLintPositive:
    def test_valid_matrix_passes(self):
        """Positive test: real matrix passes and row count is derived dynamically."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Expected pass, got:\n{result.stdout}\n{result.stderr}"
        # Row count must NOT be hardcoded — derive from matrix content
        expected_count = count_feature_rows(REAL_MATRIX.read_text(encoding="utf-8"))
        assert f"OK: {expected_count} feature rows validated" in result.stdout


def _make_minimal_matrix() -> str:
    """Return a minimal valid matrix string for negative test fixtures."""
    return """\
# Feature Matrix

## Purpose

## Legend

- `—` = not applicable on this surface by design;
- `Not started` = no implementation work has happened;
- `Web experimental` = present on web only as a non-promoted experiment;
- `Backend stable` = backend support shipped, no client surface yet;
- `Mobile eligible` = client surfaces designed/architected, not yet implemented;
- `Cross-client stable` = live and stable on every surface where it should exist;
- `Platform-specific permanent` = deliberately scoped to this surface only (e.g., keyboard shortcuts on web, share intent on mobile).

## Update protocol

---

## Feature Matrix

| Feature | Web | Android PWA | Android native | iOS future | Backend dependency | Wedge required? |
|---|---|---|---|---|---|---|
"""


class TestLintNegative:
    def _run_in_temp_repo(self, tmp_path: Path, matrix_content: str) -> CompletedProcess[str]:
        """Copy script and matrix into a temp repo structure, then run the linter."""
        script_dst = tmp_path / "scripts" / "lint_feature_matrix.py"
        matrix_dst = tmp_path / "docs" / "FEATURE_MATRIX.md"
        script_dst.parent.mkdir(parents=True, exist_ok=True)
        matrix_dst.parent.mkdir(parents=True, exist_ok=True)
        _ = script_dst.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        _ = matrix_dst.write_text(matrix_content, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(script_dst)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

    def _assert_failure_contains(self, tmp_path: Path, matrix_content: str, expected_text: str):
        result = self._run_in_temp_repo(tmp_path, matrix_content)
        assert result.returncode == 1
        assert expected_text in result.stdout

    def test_invalid_client_vocabulary_fails(self, tmp_path: Path):
        """Negative: client cell uses a term not in Legend vocabulary."""
        matrix = _make_minimal_matrix()
        matrix += "| Feature A | Notavalid | Not started | Not started | Not started | Some dep | No |\n"
        self._assert_failure_contains(tmp_path, matrix, "must use Legend vocabulary")

    def test_bad_header_fails(self, tmp_path: Path):
        """Negative: header must match the exact required schema."""
        matrix = _make_minimal_matrix().replace(
            "| Feature | Web | Android PWA | Android native | iOS future | Backend dependency | Wedge required? |",
            "| Feature | Web | Android | Android native | iOS future | Backend dependency | Wedge required? |",
        )
        self._assert_failure_contains(tmp_path, matrix, "expected exact header")

    def test_bad_delimiter_fails(self, tmp_path: Path):
        """Negative: delimiter cells must be hyphen groups only."""
        matrix = _make_minimal_matrix().replace(
            "|---|---|---|---|---|---|---|",
            "|---|---|---|oops|---|---|---|",
        )
        self._assert_failure_contains(tmp_path, matrix, "delimiter row must contain only hyphen groups")

    def test_wrong_column_count_fails(self, tmp_path: Path):
        """Negative: row has wrong number of columns (6 instead of 7)."""
        matrix = _make_minimal_matrix()
        matrix += "| Feature A | Not started | Not started | Not started | Some dep | No |\n"
        self._assert_failure_contains(tmp_path, matrix, "expected 7 cells")

    def test_empty_backend_dependency_fails(self, tmp_path: Path):
        """Negative: backend dependency cell cannot be empty."""
        matrix = _make_minimal_matrix()
        for index in range(20):
            matrix += (
                f"| Feature {index} | Not started | Not started | Not started | Not started | Some dep {index} | No |\n"
            )
        matrix = matrix.replace("| Some dep 0 |", "|  |", 1)
        self._assert_failure_contains(tmp_path, matrix, "Backend dependency must be non-empty")

    def test_malformed_category_row_fails(self, tmp_path: Path):
        """Negative: bold category-looking rows must keep six em-dash cells."""
        matrix = _make_minimal_matrix()
        matrix += "| **Category Name** | — | — | — | — | service | No |\n"
        for index in range(20):
            matrix += (
                f"| Feature {index} | Not started | Not started | Not started | Not started | Some dep {index} | No |\n"
            )
        self._assert_failure_contains(tmp_path, matrix, "category separator rows must use a bold Feature cell")

    def test_feature_row_floor_fails(self, tmp_path: Path):
        """Negative: matrices below the minimum populated feature-row floor fail."""
        matrix = _make_minimal_matrix()
        matrix += "| Feature A | Not started | Not started | Not started | Not started | Some dep | No |\n"
        self._assert_failure_contains(tmp_path, matrix, "must contain at least 20 feature rows")

    def test_invalid_wedge_value_fails(self, tmp_path: Path):
        """Negative: Wedge required? cell has invalid value (not Yes/No)."""
        matrix = _make_minimal_matrix()
        for index in range(19):
            matrix += (
                f"| Feature {index} | Not started | Not started | Not started | Not started | Some dep {index} | No |\n"
            )
        matrix += "| Feature 19 | Not started | Not started | Not started | Not started | Some dep 19 | Maybe |\n"
        result = self._run_in_temp_repo(tmp_path, matrix)
        assert result.returncode == 1
        assert "Wedge required?" in result.stdout
        assert "Yes" in result.stdout and "No" in result.stdout
