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
   call, an ``await``, a ``return`` of a value, etc.) — unless it is
   deliberately parked with ``@pytest.mark.skip/xfail(reason=...)``.
2. NOT use ``@pytest.mark.skip(...)`` / ``@pytest.mark.xfail(...)``
   without a ``reason=`` argument — whether on the function, on the
   enclosing class, or via a module-level ``pytestmark``.

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
    """Yield every collected test module under ``tests/`` (recursively).

    Only ``test_*.py`` / ``*_test.py`` files are scanned — pytest's default
    ``python_files`` discovery patterns — so helper scripts under ``tests/``
    that happen to define ``test_*`` functions are not policed (they are
    never collected and create no false coverage). ``tests/benchmark`` is
    included: pytest collects it like any other directory.
    """
    if not TESTS_DIR.is_dir():
        return []
    return sorted(
        path
        for path in TESTS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
        and (path.name.startswith("test_") or path.name.endswith("_test.py"))
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


def _mark_name_and_reason(d: ast.expr) -> tuple[str | None, bool]:
    """Return ``(mark_name, has_reason)`` for a pytest mark expression.

    Handles both ``pytest.mark.skip`` (bare Attribute) and
    ``pytest.mark.skip(...)`` (Call). The bare form is shorthand for a
    call with no kwargs, so it also lacks a reason. Returns
    ``(None, False)`` for anything else.
    """
    if isinstance(d, ast.Attribute):
        return d.attr, False
    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
        has_reason = False
        for kw in d.keywords:
            if kw.arg != "reason":
                continue
            if isinstance(kw.value, ast.Constant):
                # reason="" / reason=None are as undocumented as no reason.
                value = kw.value.value
                has_reason = isinstance(value, str) and bool(value.strip())
            else:
                # Dynamic expression — assume it produces a real reason.
                has_reason = True
        return d.func.attr, has_reason
    return None, False


def _has_documented_skip_mark(decorators: list[ast.expr]) -> bool:
    for d in decorators:
        name, has_reason = _mark_name_and_reason(d)
        if name in ("skip", "xfail") and has_reason:
            return True
    return False


def _is_deliberately_parked(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function carries ``@pytest.mark.skip/xfail(reason=...)``.

    Such a placeholder is an explicitly documented TODO — the failure
    message of the empty-body check names this as acceptable
    remediation, so the check must not double-report it. Marks without
    a (non-empty) reason are still violations (of the reason check).
    """
    return _has_documented_skip_mark(func.decorator_list)


def _parked_scopes(tree: ast.Module) -> tuple[bool, set[str]]:
    """Return (module_parked, parked_class_names) for documented skips.

    pytest honors ``pytestmark = pytest.mark.skip(reason=...)`` at module
    level and ``@pytest.mark.skip(reason=...)`` on ``Test*`` classes; tests
    inside those scopes are deliberately parked without repeating the
    decorator on every function, so the empty-body check must exempt them.
    """
    module_parked = False
    parked_classes: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            if _has_documented_skip_mark(_iter_mark_expressions(node.value)):
                module_parked = True
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if _has_documented_skip_mark(node.decorator_list):
                parked_classes.add(node.name)
    return module_parked, parked_classes


def _functions_inside_classes(tree: ast.Module, class_names: set[str]) -> set[int]:
    """Line numbers of test functions defined inside the named classes."""
    members: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in class_names:
            for inner in ast.walk(node):
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(inner.lineno)
    return members


def _iter_mark_expressions(value: ast.expr) -> list[ast.expr]:
    """Flatten a ``pytestmark`` assignment value into mark expressions.

    ``pytestmark`` may be a single mark or a list/tuple of marks.
    """
    if isinstance(value, (ast.List, ast.Tuple)):
        return list(value.elts)
    return [value]


def _module_and_class_mark_violations(path: Path) -> list[tuple[int, str, str]]:
    """Find skip/xfail marks without reason= outside function decorators.

    Covers the two collection-affecting placements the function-decorator
    walk cannot see: module-level ``pytestmark = ...`` assignments and
    decorators on ``Test*`` classes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
                for mark in _iter_mark_expressions(node.value):
                    name, has_reason = _mark_name_and_reason(mark)
                    if name in ("skip", "xfail") and not has_reason:
                        violations.append((node.lineno, "pytestmark", f"pytest.mark.{name}"))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for d in node.decorator_list:
                name, has_reason = _mark_name_and_reason(d)
                if name in ("skip", "xfail") and not has_reason:
                    violations.append((node.lineno, node.name, f"@pytest.mark.{name}"))
    return violations


def _has_substantive_body(body: list[ast.stmt]) -> bool:
    """Return True if the function body has anything besides a
    docstring, ``pass``, ``...`` ellipsis, or bare ``return``.

    A function whose entire body is built from:

    - ``pass`` statements
    - a docstring (string expression)
    - ``...`` ellipsis literals
    - bare ``return`` / ``return None``

    is considered empty and must be either implemented, marked
    ``@pytest.mark.skip(reason=...)``, or deleted. A bare ``return``
    asserts nothing and passes exactly like ``pass``.
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
        if isinstance(stmt, ast.Return) and (
            stmt.value is None
            or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
        ):
            continue
        substantive.append(stmt)
    return bool(substantive)


def test_no_test_function_is_pass_only() -> None:
    """Every test function must have at least one substantive
    statement. A function body that is only ``pass`` is the
    textbook definition of an empty test.

    Functions explicitly parked with ``@pytest.mark.skip/xfail
    (reason=...)`` are exempt — that is the remediation the failure
    message recommends.

    Unparseable test files are reported as a soft warning (not a
    fail) so the empty-body check can still run on the rest of
    the suite. The primary defense for unparseable files is the
    syntax-error check in ``test_test_files_parse.py``. Coupling
    the two checks would force this PR to wait on that one.
    """
    parseable, unparseable = _parseable_test_files()
    violations: list[tuple[Path, int, str]] = []
    for path in parseable:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_parked, parked_classes = _parked_scopes(tree)
        if module_parked:
            continue
        parked_lines = _functions_inside_classes(tree, parked_classes)
        for func in _all_test_functions(path):
            if _is_deliberately_parked(func) or func.lineno in parked_lines:
                continue
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
    MUST include a ``reason=`` argument. An unmarked skip is
    indistinguishable from a forgotten TODO. Checked on function
    decorators, ``Test*`` class decorators, and module-level
    ``pytestmark`` assignments."""
    parseable, _ = _parseable_test_files()
    violations: list[tuple[Path, int, str, str]] = []
    for path in parseable:
        for func in _all_test_functions(path):
            for d in func.decorator_list:
                name, has_reason = _mark_name_and_reason(d)
                if name not in ("skip", "xfail"):
                    continue
                if not has_reason:
                    violations.append((path, func.lineno, func.name, f"@pytest.mark.{name}"))
        for lineno, where, decorator in _module_and_class_mark_violations(path):
            violations.append((path, lineno, where, decorator))

    if violations:
        details = "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{lineno} {name} uses {decorator} without reason="
            for p, lineno, name, decorator in violations
        )
        pytest.fail(
            f"{len(violations)} skip/xfail mark(s) without a reason. "
            f"Add a reason= argument explaining WHY the test is skipped:\n{details}"
        )


def test_tests_dir_is_not_empty() -> None:
    """Sanity check: the walker found at least one test file."""
    files = _iter_test_files()
    assert files, f"No test files found under {TESTS_DIR}"
