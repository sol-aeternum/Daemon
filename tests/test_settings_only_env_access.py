"""Static convention test for #83: no direct env access outside Settings.

All env-var access in the backend must go through the `Settings` class
(`orchestrator/config.py`). Direct `os.environ.get(...)`, `os.getenv(...)`,
or `os.environ[...]` reads bypass the type validation, defaults, and env-var
prefix convention that Settings provides.

This test scans the backend tree (excluding `config.py` itself, which is
where the canonical field definitions live) and fails the suite if any new
direct env reads are introduced. Detection is AST-based so all three direct
access forms are caught, not just `os.environ.get(...)`.

The allowlist covers:
  * `orchestrator/config.py` — Settings is the canonical binding site.
  * `orchestrator/database_url.py` — accepts `environ` as a parameter
    (dependency injection used by tests).
  * `scripts/backup_db.py`, `scripts/seed.py` — standalone operational CLIs
    that run outside the daemon process; they read DATABASE_URL and
    BACKUP_DIR directly without booting Settings. Migrating them is
    tracked separately.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_DIRS = (
    REPO_ROOT / "orchestrator",
    REPO_ROOT / "providers",
    REPO_ROOT / "scripts",
)
ALLOWLIST = frozenset(
    {
        "orchestrator/config.py",
        "orchestrator/database_url.py",
        # Standalone operational / diagnostic CLIs that run outside the
        # daemon process. They read DATABASE_URL, ENCRYPTION_KEY, and
        # BACKUP_DIR directly without booting Settings. Migrating them to
        # Settings is tracked separately — they intentionally avoid the
        # daemon lifecycle so they can be invoked from cron / ad-hoc.
        "scripts/backup_db.py",
        "scripts/seed.py",
        "scripts/test_retrieval_quality.py",
    }
)


def _scan_for_direct_env_access() -> list[tuple[pathlib.Path, int, str]]:
    """AST-scan for direct env reads that bypass Settings.

    Catches any of:
      * ``os.environ.get("X")``
      * ``os.environ["X"]``
      * ``os.getenv("X")``
    """
    hits: list[tuple[pathlib.Path, int, str]] = []
    for scan_dir in SCAN_DIRS:
        for path in scan_dir.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # Detect three direct access forms:
                #   1) os.environ.get(...)           Call( Attribute(os.environ, get) )
                #   2) os.getenv(...)                Call( Attribute(os, getenv) )
                #   3) os.environ["KEY"]             Subscript( Attribute(os, environ), Constant )
                if isinstance(node, ast.Call):
                    func = node.func
                    is_environ_get = (
                        isinstance(func, ast.Attribute)
                        and func.attr == "get"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "environ"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id == "os"
                    )
                    is_os_getenv = (
                        isinstance(func, ast.Attribute)
                        and func.attr == "getenv"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "os"
                    )
                    if is_environ_get:
                        hits.append((path, node.lineno, "os.environ.get(...)"))
                    elif is_os_getenv:
                        hits.append((path, node.lineno, "os.getenv(...)"))
                    continue
                if isinstance(node, ast.Subscript):
                    target = node.value
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "environ"
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "os"
                    ):
                        hits.append((path, node.lineno, "os.environ[...]"))
    return hits


def test_no_direct_env_access_outside_settings() -> None:
    hits = _scan_for_direct_env_access()
    assert not hits, (
        "Direct `os.environ.get(...)` / `os.getenv(...)` / "
        '`os.environ["KEY"]` reads detected outside '
        "`orchestrator/config.py` (or other allowed files). Read env vars "
        "through the `Settings` class instead so type validation, defaults, "
        "and env-var prefixing apply uniformly.\n\nFound:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {snippet}" for p, ln, snippet in hits)
    )


def test_ast_gate_detects_all_three_direct_env_forms(tmp_path) -> None:
    """Regression for Codex round-2 P2 on PR #264:

    The widened AST walker must catch every direct env-access form
    (os.environ.get, os.getenv, os.environ["KEY"]) — a previous widening
    pass claimed to detect subscript reads but actually only walked
    ast.Call nodes. Drive the scanner against a fixture file that uses
    each form and assert the fixture produces the expected hits.
    """
    fixture_dir = tmp_path / "orchestrator"
    fixture_dir.mkdir()
    fixture = fixture_dir / "fixture_uses_all_forms.py"
    fixture.write_text(
        "import os\n"
        "\n"
        'A = os.environ.get("DATABASE_URL")\n'
        'B = os.getenv("ENCRYPTION_KEY")\n'
        'C = os.environ["BACKUP_DIR"]\n'
    )

    # Point the scanner at the fixture by overriding SCAN_DIRS at the call
    # boundary. _scan_for_direct_env_access takes no args, so we patch
    # SCAN_DIRS via the module-level constant by monkeypatching.
    import tests.test_settings_only_env_access as mod

    original_scan_dirs = mod.SCAN_DIRS
    original_repo_root = mod.REPO_ROOT
    try:
        mod.SCAN_DIRS = (fixture_dir,)
        mod.REPO_ROOT = tmp_path
        hits = mod._scan_for_direct_env_access()
    finally:
        mod.SCAN_DIRS = original_scan_dirs
        mod.REPO_ROOT = original_repo_root

    snippets = sorted({snippet for _, _, snippet in hits})
    assert set(snippets) == {
        "os.environ[...]",
        "os.environ.get(...)",
        "os.getenv(...)",
    }, f"Expected all three forms detected, got {snippets}"
