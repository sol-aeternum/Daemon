"""Regression tests for issue #67 — every test file in the repo must
be valid Python.

This guards against the failure mode where a developer adds a test
file with a syntax error and then quietly excludes it from ruff and
pytest via ``pyproject.toml``'s ``extend-exclude``. The excluded file
becomes invisible to CI but still breaks ``ast.parse()`` for any
tool that walks the test tree.

The fix for #67 deleted the broken ``tests/test_video_e2e.py`` and
removed the ``extend-exclude`` line. This test enforces that any
future addition to ``tests/`` is itself parseable, regardless of
ruff/pytest configuration.

The walker uses the filesystem, not ``pytest``'s collection, so
``extend-exclude`` cannot hide a broken file from this check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Repository root is the parent of the ``tests/`` directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"


def _iter_test_files() -> list[Path]:
    """Yield every ``*.py`` file under ``tests/`` (recursively).

    Excludes ``__pycache__/`` which contains compiled bytecode, not
    source. The walker is filesystem-based, not pytest-based, so the
    ``pyproject.toml`` ``extend-exclude`` setting cannot hide files
    from this test.
    """
    if not TESTS_DIR.is_dir():
        return []
    return sorted(path for path in TESTS_DIR.rglob("*.py") if "__pycache__" not in path.parts)


def test_every_test_file_parses() -> None:
    """Every ``tests/**/*.py`` must pass ``ast.parse()``.

    A failure here means a test file has a syntax error. The most
    likely cause is an unmatched bracket, a bad string escape, or
    a half-written test. The fix is to repair or delete the file;
    do NOT add it to ``pyproject.toml``'s ``extend-exclude`` (that
    is the failure mode this test guards against).
    """
    failures: list[tuple[Path, SyntaxError]] = []
    for path in _iter_test_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append((path, exc))

    if failures:
        details = "\n".join(
            f"  {path.relative_to(REPO_ROOT)}: line {exc.lineno}: {exc.msg}"
            for path, exc in failures
        )
        pytest.fail(
            f"{len(failures)} test file(s) failed ast.parse(). "
            f"Repair or delete the broken file(s); do NOT exclude "
            f"them from ruff/pytest:\n{details}"
        )


def test_no_ruff_extend_exclude_for_tests() -> None:
    """The repo's ``pyproject.toml`` must not exclude any test file
    from ruff via ``extend-exclude``.

    The original failure mode for #67 was exactly this: the broken
    ``test_video_e2e.py`` was added to ``extend-exclude`` to hide
    its syntax error. If a test file is broken enough that we want
    to exclude it, we should delete it instead. This test fails if
    anyone re-introduces such an exclusion.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("pyproject.toml not found")

    text = pyproject.read_text(encoding="utf-8")
    # Naive parse: look for the line ``extend-exclude = [...]`` and
    # check that none of the entries point inside ``tests/``.
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("extend-exclude"):
            continue
        if "tests/" in line:
            pytest.fail(
                "pyproject.toml contains a tests/ path in extend-exclude. "
                "If a test file is broken, delete it — do not hide it. "
                f"Line: {raw_line!r}"
            )


def test_tests_dir_is_not_empty() -> None:
    """Sanity check: the walker found at least one test file.

    A failure here means ``tests/`` is missing entirely or only
    contains ``__pycache__/`` — both of which indicate a much
    bigger problem than this file can diagnose.
    """
    files = _iter_test_files()
    assert files, f"No test files found under {TESTS_DIR}"
