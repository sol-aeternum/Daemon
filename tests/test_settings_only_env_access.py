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
# Every backend Python package. `config/`, `db/`, `council/`, and `backend/`
# are production modules imported by the orchestrator (e.g.
# `orchestrator/subagents/image.py` imports `config.video_pricing` and
# `db.video_credits`), so a direct env read added there must fail this gate
# exactly as one added under `orchestrator/` does.
SCAN_DIRS = (
    REPO_ROOT / "orchestrator",
    REPO_ROOT / "providers",
    REPO_ROOT / "scripts",
    REPO_ROOT / "config",
    REPO_ROOT / "db",
    REPO_ROOT / "council",
    REPO_ROOT / "backend",
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


def _resolve_os_aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Collect the local names that alias `os` / `os.environ` / `os.getenv`.

    Handles the equivalent-but-differently-spelled import forms:
      * ``import os``                  -> os_names = {"os"}
      * ``import os as host_os``       -> os_names = {"host_os"}
      * ``from os import getenv``      -> getenv_names = {"getenv"}
      * ``from os import getenv as g`` -> getenv_names = {"g"}
      * ``from os import environ``     -> environ_names = {"environ"}
      * ``from os import environ as e``-> environ_names = {"e"}
    """
    os_names: set[str] = set()
    getenv_names: set[str] = set()
    environ_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "getenv":
                    getenv_names.add(local)
                elif alias.name == "environ":
                    environ_names.add(local)
    return os_names, getenv_names, environ_names


def _scan_for_direct_env_access() -> list[tuple[pathlib.Path, int, str]]:
    """AST-scan for direct env reads that bypass Settings.

    Catches any of:
      * ``os.environ.get("X")`` / ``environ.get("X")``
      * ``os.environ["X"]`` / ``environ["X"]``
      * ``os.getenv("X")`` / ``getenv("X")``

    ...including under any import alias (``import os as host_os``,
    ``from os import getenv``, ``from os import environ as e``), because
    those forms are semantically identical direct reads.
    """
    hits: list[tuple[pathlib.Path, int, str]] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            continue
        for path in scan_dir.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            os_names, getenv_names, environ_names = _resolve_os_aliases(tree)

            def _is_environ_expr(
                node: ast.expr,
                _os_names: set[str] = os_names,
                _environ_names: set[str] = environ_names,
            ) -> bool:
                """True if `node` evaluates to the `os.environ` mapping."""
                # `os.environ` / `host_os.environ`
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "environ"
                    and isinstance(node.value, ast.Name)
                    and node.value.id in _os_names
                ):
                    return True
                # bare `environ` / aliased `e` from `from os import environ`
                return isinstance(node, ast.Name) and node.id in _environ_names

            for node in ast.walk(tree):
                # Detect the direct access forms:
                #   1) <environ>.get(...)   Call( Attribute(<environ expr>, get) )
                #   2) <os>.getenv(...)     Call( Attribute(Name(os-alias), getenv) )
                #   3) getenv(...)          Call( Name(getenv-alias) )
                #   4) <environ>["KEY"]     Subscript( <environ expr>, ... )
                if isinstance(node, ast.Call):
                    func = node.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "get"
                        and _is_environ_expr(func.value)
                    ):
                        hits.append((path, node.lineno, "os.environ.get(...)"))
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr == "getenv"
                        and isinstance(func.value, ast.Name)
                        and func.value.id in os_names
                    ):
                        hits.append((path, node.lineno, "os.getenv(...)"))
                    elif isinstance(func, ast.Name) and func.id in getenv_names:
                        hits.append((path, node.lineno, "os.getenv(...)"))
                    continue
                if isinstance(node, ast.Subscript) and _is_environ_expr(node.value):
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


def test_ast_gate_detects_all_three_direct_env_forms(tmp_path, monkeypatch) -> None:
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

    # Point the scanner at the fixture by overriding the module-level
    # SCAN_DIRS / REPO_ROOT via a MonkeyPatch fixture, then call the
    # scanner. We resolve the module via sys.modules (already loaded by
    # pytest at collection time) rather than a self-import that
    # basedpyright cannot statically resolve.
    import sys

    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "SCAN_DIRS", (fixture_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted({snippet for _, _, snippet in hits})
    assert set(snippets) == {
        "os.environ[...]",
        "os.environ.get(...)",
        "os.getenv(...)",
    }, f"Expected all three forms detected, got {snippets}"


def test_ast_gate_resolves_imported_os_aliases(tmp_path, monkeypatch) -> None:
    """Regression for the round-3 P2 on PR #268:

    `from os import getenv`, `from os import environ`, and `import os as X`
    are semantically identical direct env reads, but a walker that only
    recognizes an `ast.Name` whose identifier is literally `os` misses all
    of them. Each alias form must produce a hit.
    """
    import sys

    mod = sys.modules[__name__]
    fixture_dir = tmp_path / "aliased"
    fixture_dir.mkdir()

    cases = {
        "import_os_as": (
            "import os as host_os\n"
            '\nA = host_os.getenv("K1")\n'
            'B = host_os.environ.get("K2")\n'
            'C = host_os.environ["K3"]\n',
            {"os.getenv(...)", "os.environ.get(...)", "os.environ[...]"},
        ),
        "from_os_import_getenv": (
            'from os import getenv\n\nA = getenv("K1")\n',
            {"os.getenv(...)"},
        ),
        "from_os_import_getenv_as": (
            'from os import getenv as g\n\nA = g("K1")\n',
            {"os.getenv(...)"},
        ),
        "from_os_import_environ": (
            'from os import environ\n\nA = environ.get("K1")\nB = environ["K2"]\n',
            {"os.environ.get(...)", "os.environ[...]"},
        ),
        "from_os_import_environ_as": (
            'from os import environ as e\n\nA = e.get("K1")\nB = e["K2"]\n',
            {"os.environ.get(...)", "os.environ[...]"},
        ),
    }

    for name, (source, expected) in cases.items():
        case_dir = fixture_dir / name
        case_dir.mkdir()
        (case_dir / f"{name}.py").write_text(source)

        monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
        monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
        hits = mod._scan_for_direct_env_access()

        found = {snippet for _, _, snippet in hits}
        assert found == expected, (
            f"alias form `{name}`: gate reported {sorted(found)}, expected {sorted(expected)}"
        )


def test_ast_gate_does_not_flag_unrelated_names(tmp_path, monkeypatch) -> None:
    """The alias resolver must not fire on names that are not bound to `os`.

    A local variable, function parameter, or same-named import from another
    module called `environ` / `getenv` is not a direct env read, and flagging
    it would make the gate unusable (notably
    `orchestrator/database_url.py` takes an injected `environ` mapping).
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "unrelated"
    case_dir.mkdir()
    (case_dir / "unrelated.py").write_text(
        "from mylib import getenv, environ\n"
        "\n"
        "\n"
        "def read(environ):\n"
        '    local = environ.get("K1")\n'
        '    other = environ["K2"]\n'
        '    third = getenv("K3")\n'
        "    return local, other, third\n"
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    assert mod._scan_for_direct_env_access() == [], (
        "gate flagged names that are not bound to the os module — false positive"
    )


def test_ast_gate_scans_every_backend_package() -> None:
    """Regression for the round-3 P2 on PR #268:

    `config/`, `db/`, `council/`, and `backend/` are production packages
    imported by the orchestrator, so the gate must cover them. Guard the
    SCAN_DIRS tuple against silent narrowing.
    """
    scanned = {d.name for d in SCAN_DIRS}
    for required in ("orchestrator", "providers", "scripts", "config", "db", "council", "backend"):
        assert required in scanned, f"backend package `{required}/` is not covered by the gate"

    # Every top-level directory that contains Python and is not test/tooling
    # scaffolding must be in SCAN_DIRS, so a newly added backend package
    # cannot silently escape the gate.
    ignored = {"tests", "migrations", "frontend", "docs", ".venv", "node_modules"}
    for child in REPO_ROOT.iterdir():
        if not child.is_dir() or child.name.startswith(".") or child.name in ignored:
            continue
        if not any(child.rglob("*.py")):
            continue
        assert child.name in scanned, (
            f"top-level package `{child.name}/` contains Python but is not in SCAN_DIRS; "
            "add it to the gate or to the ignored set with a rationale"
        )
