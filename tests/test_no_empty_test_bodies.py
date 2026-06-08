"""Regression tests for issue #71 — empty test bodies are forbidden.

A test function whose body is just ``pass`` (or just a docstring, or
a docstring plus a ``pass``) is indistinguishable from a test that
was never written. It "passes" every CI run, asserts nothing, and
gives a false sense of coverage. The audit issue #71 counted ~17
such candidates via ``grep``; the AST walk below confirms that all
17 are MOCK class methods (``FakeMemoryStore.__init__`` etc.), NOT
test functions, so no actual fix is required. These tests enforce
the rule going forward.

Specifically, every test function ``tests/.../test_*.py`` whose
name starts with ``test_`` must:

1. Contain at least one substantive statement (an assertion, a
   call, an ``await``, a ``return`` of a value, etc.).
2. NOT be decorated with ``@pytest.mark.skip(...)`` or
   ``@pytest.mark.xfail(...)`` without a ``reason=`` argument.

The walker uses the filesystem (not pytest collection) so that
``pyproject.toml`` ``extend-exclude`` cannot hide a violating file.
This mirrors the pattern in ``test_test_files_parse.py`` (issue
#67). Unparseable files are reported once but do not abort the
rest of the walk; the syntax-error check is a separate concern.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"


def _iter_test_files() -> list[Path]:
    """Yield every ``*.py`` file under ``tests/`` (recursively)."""
    if not TESTS_DIR.is_dir():
        return []
    return sorted(
        path
        for path in TESTS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts and "benchmark" not in path.parts
    )


def _parseable_test_files() -> tuple[list[Path], list[tuple[Path, SyntaxError]]]:
    """Partition test files into parseable and unparseable buckets.

    Returns ``(parseable, [(unparseable_path, error), ...])``. The
    empty-test-body check operates on the parseable bucket; the
    unparseable bucket is reported but does not abort the walk.
    The companion ``test_test_files_parse.py`` (issue #67) is the
    primary defense against unparseable test files; this is a
    soft secondary check that does not couple the two concerns.
    """
    parseable: list[Path] = []
    unparseable: list[tuple[Path, SyntaxError]] = []
    for path in _iter_test_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parseable.append(path)
        except SyntaxError as exc:
            unparseable.append((path, exc))
    return parseable, unparseable


def _all_test_functions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every top-level or nested function whose name starts
    with ``test_``. Skips MOCK class methods (the audit's grep
    matched those too, but they're not test functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            functions.append(node)
    return functions


def _has_substantive_body(body: list[ast.stmt]) -> bool:
    """Return True if the function body has anything besides a
    docstring, ``pass``, or ``...`` ellipsis statement.

    A function whose entire body is:

    - a single ``pass`` statement
    - a single docstring (string expression)
    - a docstring followed by a single ``pass`` statement

    is considered empty and must be either implemented, marked
    ``@pytest.mark.skip(reason=...)``, or deleted.
    """
    substantive = []
    for stmt in body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            value = stmt.value.value
            if isinstance(value, str):
                # Module-level or function-level docstring.
                continue
            if value is ...:
                # Ellipsis literal — also not substantive.
                continue
        if isinstance(stmt, ast.Pass):
            continue
        substantive.append(stmt)
    return bool(substantive)


def test_no_test_function_is_pass_only() -> None:
    """Every test function must have at least one substantive
    statement. A function body that is only ``pass`` is the
    textbook definition of an empty test.

    Unparseable test files are reported as a soft warning (not a
    fail) so the empty-body check can still run on the rest of
    the suite. The primary defense for unparseable files is the
    syntax-error check in ``test_test_files_parse.py``. Coupling
    the two checks would force this PR to wait on that one.
    """
    parseable, unparseable = _parseable_test_files()
    violations: list[tuple[Path, int, str]] = []
    for path in parseable:
        for func in _all_test_functions(path):
            if not _has_substantive_body(func.body):
                violations.append((path, func.lineno, func.name))

    if violations:
        details = "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{lineno} {name}" for p, lineno, name in violations
        )
        pytest.fail(
            f"{len(violations)} test function(s) have a pass-only / docstring-only body. "
            f"Implement the assertions, mark with @pytest.mark.skip(reason=...), "
            f"or delete the test:\n{details}"
        )

    if unparseable:
        # Soft notice. The companion test_test_files_parse.py is
        # the primary defense; we mention unparseable files here
        # for visibility but do not couple the two checks.
        names = ", ".join(p.relative_to(REPO_ROOT).as_posix() for p, _ in unparseable)
        pytest.skip(
            f"{len(unparseable)} test file(s) are unparseable and were skipped: "
            f"{names}. The syntax-error check in test_test_files_parse.py "
            f"is the primary defense."
        )


def test_every_pytest_mark_skip_has_reason() -> None:
    """``@pytest.mark.skip(...)`` and ``@pytest.mark.xfail(...)``
    decorators MUST include a ``reason=`` argument. An unmarked
    skip is indistinguishable from a forgotten TODO."""
    parseable, _ = _parseable_test_files()
    violations: list[tuple[Path, int, str, str]] = []
    for path in parseable:
        for func in _all_test_functions(path):
            for d in func.decorator_list:
                # Two decorator shapes match: ``@pytest.mark.skip`` (bare
                # Attribute, no parens) and ``@pytest.mark.skip(...)`` (Call).
                # The bare form is a shorthand for ``@pytest.mark.skip()``
                # with no kwargs, so it also lacks a reason.
                if isinstance(d, ast.Attribute):
                    name = d.attr
                    has_reason = False
                elif isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    name = d.func.attr
                    has_reason = any(kw.arg == "reason" for kw in d.keywords)
                else:
                    continue
                if name not in ("skip", "xfail"):
                    continue
                if not has_reason:
                    violations.append((path, func.lineno, func.name, f"@pytest.mark.{name}"))

    if violations:
        details = "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{lineno} {name} uses {decorator} without reason="
            for p, lineno, name, decorator in violations
        )
        pytest.fail(
            f"{len(violations)} test function(s) use skip/xfail without a reason. "
            f"Add a reason= argument explaining WHY the test is skipped:\n{details}"
        )


def test_tests_dir_is_not_empty() -> None:
    """Sanity check: the walker found at least one test file."""
    files = _iter_test_files()
    assert files, f"No test files found under {TESTS_DIR}"
